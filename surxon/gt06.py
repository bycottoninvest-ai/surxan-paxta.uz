"""GT06 GPS trackers (Concox GT06 / GT06N and their many clones): a small TCP server that the trackers connect to.

Run as its own container: `python -m surxon.gt06` (port GT06_PORT, default 5023). A tracker is set up once by SMS
(`SERVER,0,<server ip>,5023,0#`, APN, `TIMER,30#`) and is known by its IMEI. The admin assigns an IMEI to a machine on
the Texnikalar page; positions of unknown IMEIs are not kept (only “seen at” so the admin can find the number).

Packets: 0x78 0x78 len proto … serial crc 0x0D 0x0A (0x79 0x79 with a 2-byte length for long ones). Login (0x01),
GPS (0x12, 0x22 with the ignition byte), status/heartbeat (0x13: bit 1 = ignition), alarm (0x16, 0x26) and time
request (0x8A) are understood; the rest is acknowledged or ignored. Device time is UTC.
"""
import asyncio
import os
import struct
from datetime import datetime, timezone

LOGIN, GPS, GPS2, STATUS, ALARM, ALARM2, TIME_REQ = 0x01, 0x12, 0x22, 0x13, 0x16, 0x26, 0x8A
ACK = {LOGIN, STATUS, ALARM, ALARM2, 0x15, 0x17, 0x19, 0x27}
MAX_BUF = 4096


def crc_itu(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return ~crc & 0xFFFF


def packet(proto: int, serial: int, body: bytes = b'') -> bytes:
    core = bytes([len(body) + 5, proto]) + body + struct.pack('>H', serial)
    return b'\x78\x78' + core + struct.pack('>H', crc_itu(core)) + b'\r\n'


def split(buf: bytearray):
    """Take complete frames out of buf → list of (proto, content, serial). Junk before a header is dropped."""
    out = []
    while True:
        i = min((x for x in (buf.find(b'\x78\x78'), buf.find(b'\x79\x79')) if x >= 0), default=-1)
        if i < 0:
            buf.clear() if len(buf) > 1 else None
            return out
        if i:
            del buf[:i]
        long_ = buf[0] == 0x79
        head = 4 if long_ else 3
        if len(buf) < head + 1:
            return out
        n = struct.unpack('>H', buf[2:4])[0] if long_ else buf[2]
        total = head + n + 2
        if n < 5 or total > MAX_BUF:
            del buf[:2]
            continue
        if len(buf) < total:
            return out
        frame = bytes(buf[:total])
        del buf[:total]
        proto = frame[head]
        out.append((proto, frame[head + 1:head + n - 4], struct.unpack('>H', frame[head + n - 4:head + n - 2])[0]))


def imei_of(content: bytes) -> str:
    return content[:8].hex().lstrip('0') or '0'


def _gps(c: bytes, off=0):
    """date(6) + gps len/sats(1) + lat(4) + lon(4) + speed(1) + course/status(2) → dict (UTC time)."""
    yy, mo, dd, hh, mi, ss = c[off:off + 6]
    lat, lon = struct.unpack('>II', c[off + 7:off + 15])
    speed = c[off + 15]
    flags = struct.unpack('>H', c[off + 16:off + 18])[0]
    lat, lon = lat / 1800000.0, lon / 1800000.0
    if not flags & (1 << 10):
        lat = -lat
    if flags & (1 << 11):
        lon = -lon
    try:
        at = datetime(2000 + yy, mo, dd, hh, mi, ss, tzinfo=timezone.utc)
    except ValueError:
        at = None
    return {'at': at, 'lat': lat, 'lon': lon, 'speed': float(speed), 'course': flags & 0x3FF,
            'valid': bool(flags & (1 << 12))}


def decode(proto: int, c: bytes):
    """One packet's meaning: {'kind': 'login'|'gps'|'status'|'time'|'other', …}."""
    try:
        if proto == LOGIN:
            return {'kind': 'login', 'imei': imei_of(c)}
        if proto in (GPS, GPS2) and len(c) >= 18:
            d = _gps(c)
            d['kind'] = 'gps'
            if proto == GPS2 and len(c) >= 27:           # gps 18 + lbs 8 → ignition byte
                d['acc'] = 1 if c[26] else 0
            return d
        if proto == STATUS and len(c) >= 3:
            return {'kind': 'status', 'acc': 1 if c[0] & 0b10 else 0, 'power': c[1], 'gsm': c[2]}
        if proto in (ALARM, ALARM2) and len(c) >= 18:
            d = _gps(c)
            d['kind'] = 'gps'
            lbs = c[18] if len(c) > 18 else 0
            k = 19 + max(lbs - 1, 0) if lbs else 18
            if len(c) > k:
                d['acc'] = 1 if c[k] & 0b10 else 0
            return d
        if proto == TIME_REQ:
            return {'kind': 'time'}
    except (IndexError, struct.error):
        pass
    return {'kind': 'other'}


def reply(proto: int, serial: int):
    if proto in ACK:
        return packet(proto, serial)
    if proto == TIME_REQ:
        n = datetime.now(timezone.utc)
        return packet(TIME_REQ, serial, bytes([n.year - 2000, n.month, n.day, n.hour, n.minute, n.second]))
    return None


class Session:
    """One tracker connection. store(imei, event) is called for every decoded packet after login."""

    def __init__(self, store):
        self.store, self.imei, self.buf = store, None, bytearray()

    def feed(self, data: bytes):
        self.buf += data
        if len(self.buf) > MAX_BUF * 4:
            self.buf.clear()
        answers = []
        for proto, content, serial in split(self.buf):
            ev = decode(proto, content)
            if ev['kind'] == 'login':
                self.imei = ev['imei']
            if self.imei and ev['kind'] in ('login', 'gps', 'status'):
                try:
                    self.store(self.imei, ev)
                except Exception as exc:              # a database hiccup must not drop the connection
                    print(f'gt06 store failed {self.imei}: {exc}', flush=True)
            r = reply(proto, serial)
            if r:
                answers.append(r)
        return answers


async def _client(reader, writer, store, idle=900):
    s = Session(store)
    try:
        while True:
            data = await asyncio.wait_for(reader.read(1024), timeout=idle)
            if not data:
                break
            for r in s.feed(data):
                writer.write(r)
            await writer.drain()
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def serve(store, host='0.0.0.0', port=5023, ready=None):
    server = await asyncio.start_server(lambda r, w: _client(r, w, store), host, port)
    if ready:
        ready(server.sockets[0].getsockname()[1])
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from surxon import create_app
    from surxon.fleet import store_event
    app = create_app()
    port = int(os.environ.get('GT06_PORT', '5023'))

    def store(imei, ev):
        with app.app_context():
            store_event(imei, ev)

    print(f'GT06 GPS server on port {port}', flush=True)
    asyncio.run(serve(store, port=port))
