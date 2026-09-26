"""GT06 GPS trackers: protocol, storing positions, what a machine did (field / road / idle), expected vs given diesel,
the job chosen at the pump, done work per field, and the director's machine map."""
import asyncio
import json
import socket
import struct
import threading
from datetime import datetime, timedelta, timezone

from surxon import fleet, gt06
from surxon.db import get_db, q, scalar
from conftest import uuid4
from test_fuel import give, setup, take

POLY = [[42.300, 59.600], [42.309, 59.600], [42.309, 59.612], [42.300, 59.612]]   # ~1 km × 1 km field


def login_pkt(imei='0359339075012345', serial=1):
    return gt06.packet(gt06.LOGIN, serial, bytes.fromhex(imei.zfill(16)))


def gps_pkt(lat, lon, speed, at, acc=None, serial=2):
    flags = (1 << 12) | (1 << 10) | 90                         # positioned, north, east, course 90
    body = bytes([at.year - 2000, at.month, at.day, at.hour, at.minute, at.second, 0xC9])
    body += struct.pack('>IIBH', round(lat * 1800000), round(lon * 1800000), speed, flags)
    body += bytes(8)                                            # LBS
    if acc is not None:
        body += bytes([acc, 0, 1])
        return gt06.packet(gt06.GPS2, serial, body)
    return gt06.packet(gt06.GPS, serial, body)


def status_pkt(acc, serial=3):
    return gt06.packet(gt06.STATUS, serial, bytes([0b01000000 | (0b10 if acc else 0), 6, 4, 0, 2]))


def test_protocol_crc_login_reply_and_decoding():
    # the example from the GT06 protocol document
    assert gt06.crc_itu(bytes.fromhex('0D01012345678901234500 01'.replace(' ', ''))) == 0x8CDD
    assert gt06.packet(gt06.LOGIN, 1) == bytes.fromhex('787805010001D9DC0D0A')
    s = gt06.Session(lambda imei, ev: seen.append((imei, ev)))
    seen = []
    at = datetime(2026, 9, 26, 5, 30, 0, tzinfo=timezone.utc)
    stream = b'junk' + login_pkt() + gps_pkt(42.3045, 59.6061, 7, at, acc=1) + status_pkt(0)
    answers = s.feed(stream[:15]) + s.feed(stream[15:])        # split across TCP reads
    assert answers[0] == gt06.packet(gt06.LOGIN, 1) and len(answers) == 2          # login + status acknowledged
    assert seen[0] == ('359339075012345', {'kind': 'login', 'imei': '359339075012345'})
    ev = seen[1][1]
    assert abs(ev['lat'] - 42.3045) < 1e-6 and abs(ev['lon'] - 59.6061) < 1e-6 and ev['speed'] == 7 and ev['acc'] == 1
    assert ev['at'] == at and ev['course'] == 90
    assert seen[2][1] == {'kind': 'status', 'acc': 0, 'power': 6, 'gsm': 4}


def test_tcp_server_end_to_end(app, world):
    with app.app_context():
        get_db().execute('UPDATE fields SET polygon_json=? WHERE code=?', (json.dumps(POLY), 'D-04'))
    got, port = [], []
    ready = threading.Event()
    loop = asyncio.new_event_loop()

    def run():
        asyncio.set_event_loop(loop)
        loop.create_task(gt06.serve(lambda i, e: got.append((i, e)), host='127.0.0.1', port=0,
                                    ready=lambda p: (port.append(p), ready.set())))
        loop.run_forever()
    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(5)
    with socket.create_connection(('127.0.0.1', port[0]), timeout=5) as c:
        c.sendall(login_pkt())
        assert c.recv(64) == gt06.packet(gt06.LOGIN, 1)
        c.sendall(gps_pkt(42.305, 59.605, 5, datetime.now(timezone.utc)))
        c.sendall(status_pkt(1))
        assert c.recv(64) == gt06.packet(gt06.STATUS, 3)
    import time
    time.sleep(0.2)                                             # let the server see the disconnect
    loop.call_soon_threadsafe(loop.stop)
    assert [e['kind'] for _, e in got] == ['login', 'gps', 'status']


def feed(app, imei, events):
    with app.app_context():
        for ev in events:
            fleet.store_event(imei, ev)


def test_unknown_tracker_then_assigned_and_day_analysis(app, world):
    admin, rahbar = world['admin'], world['rahbar']
    with app.app_context():
        get_db().execute('UPDATE fields SET polygon_json=? WHERE code=?', (json.dumps(POLY), 'D-04'))
    imei = '359339075012345'
    feed(app, imei, [{'kind': 'login', 'imei': imei}])
    page = admin.get('/admin/texnikalar').get_data(as_text=True)
    assert imei in page and 'Yangi GPS trekerlar' in page                     # the admin sees the new tracker
    tid = world['eq']['T-01']
    r = admin.post('/admin/texnikalar', {'id': tid, 'kind': 'traktor', 'code': 'T-01', 'active': '1', 'imei': imei,
                                         'norm_road_lpkm': '0,4', 'work_hours': '07:00-19:00'}).get_json()
    assert r['ok'], r
    bad = admin.post('/admin/texnikalar', {'id': world['eq']['K-01'], 'kind': 'kombayn', 'code': 'K-01', 'active': '1',
                                           'imei': imei}).get_json()
    assert not bad['ok'] and 'T-01' in bad['error']                          # one tracker, one machine
    # yesterday (local): drive 07:00–07:10 on the road, plough in D-04 07:10–09:10, stand with the engine on 30 min
    with app.app_context():
        from surxon.utils import now
        day = (now() - timedelta(days=1)).strftime('%Y-%m-%d')
        tz = now().tzinfo
    t0 = datetime.fromisoformat(day + 'T07:00:00').replace(tzinfo=tz).astimezone(timezone.utc)
    evs = []
    for i in range(21):                                                      # 30 s steps, from 3 km south to the field
        evs.append({'kind': 'gps', 'lat': 42.273 + i * 0.00133, 'lon': 59.601, 'speed': 20.0, 'course': 0, 'valid': True,
                    'acc': 1, 'at': t0 + timedelta(seconds=30 * i)})
    t1 = t0 + timedelta(minutes=10)
    for i in range(241):                                                     # 2 h back and forth across the field, 8 km/h
        row, step = divmod(i, 20)
        lon = 59.6015 + (step if row % 2 == 0 else 19 - step) * 0.00055
        evs.append({'kind': 'gps', 'lat': 42.3005 + row * 0.0007, 'lon': lon, 'speed': 8.0, 'course': 90, 'valid': True,
                    'acc': 1, 'at': t1 + timedelta(seconds=30 * i)})
    t2 = t1 + timedelta(seconds=30 * 240)
    last = evs[-1]
    for i in range(1, 7):                                                    # standing, engine on, heartbeat every 5 min
        evs.append(dict(last, speed=0.0, at=t2 + timedelta(minutes=5 * i)))
    feed(app, imei, evs)
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM vehicle_positions') > 200
        eq = q('SELECT * FROM equipment WHERE id=?', (tid,), one=True)
        st = fleet.day_stats(eq, day)
        assert 1.9 <= st['field_h'] <= 2.1 and 0.4 <= st['idle_h'] <= 0.6 and 2 <= st['road_km'] <= 4
        (fid, job), w = next(iter(st['works'].items()))
        assert job == fleet.NO_JOB and len(w['cells']) > 20
        assert scalar('SELECT COUNT(*) FROM vehicle_days') == 1                # finished day stored once
        # expected: 2 h × 9 l/h (no job chosen → traktor default) + ~3 km × 0.4 + 0.5 h × 2
        exp = fleet.expected_l(eq, st)
        assert 19 <= exp <= 22
    # fuel given with the job “Shudgor” before the work → the job and its norm (14 l/h) apply; given 60 L → too much
    yq, zp, qr = setup(app, world)
    assert take(yq, zp, 400)['ok']
    r = give(yq, qr['T-01'], 60, purpose='Shudgor')
    assert r['ok'], r
    assert not give(yq, qr['T-01'], 5, purpose='Uchish')['ok']              # only the listed jobs
    with app.app_context():
        get_db().execute("UPDATE fuel_ops SET created_at=? WHERE kind='BERISH'", (day + ' 06:50:00',))
        get_db().execute('DELETE FROM vehicle_days')
        get_db().execute('DELETE FROM vehicle_works')
        use = {u['code']: u for u in fleet.usage(day, day)}['T-01']
        assert use['given'] == 60 and 29 <= use['expected'] <= 33 and use['flag'] and use['diff'] > 20
        wk = fleet.works(day, day)
        assert wk[0]['code'] == 'D-04' and wk[0]['job'] == 'Shudgor' and wk[0]['machines'] == ['T-01'] and 5 < wk[0]['pct'] <= 100
    page = rahbar.get(f'/rahbar/texnika?period=hafta').get_data(as_text=True)
    assert 'Texnika xaritada' in page and 'me’yordan ko‘p' in page and 'D-04 · Shudgor' in page
    live = rahbar.get('/rahbar/texnika.json').get_json()['machines']
    assert live[0]['code'] == 'T-01' and live[0]['state'] == 'offline'       # nothing today → no connection shown
    assert len(rahbar.get(f'/rahbar/texnika.json?iz={tid}').get_json()['track']) == 0
    assert world['juma'].get('/rahbar/texnika').status_code == 302


def test_live_state_and_standing_alert(app, world):
    imei = '359339075099999'
    with app.app_context():
        get_db().execute('UPDATE fields SET polygon_json=? WHERE code=?', (json.dumps(POLY), 'D-04'))
        get_db().execute("INSERT INTO settings(key, value, updated_at) VALUES ('fleet_work_hours','00:00-23:59','x')")
        fleet.store_event(imei, {'kind': 'login', 'imei': imei})
    world['admin'].post('/admin/texnikalar', {'id': world['eq']['T-01'], 'kind': 'traktor', 'code': 'T-01', 'active': '1',
                                              'imei': imei})
    now = datetime.now(timezone.utc)
    evs = [{'kind': 'gps', 'lat': 42.305, 'lon': 59.605, 'speed': 0.0, 'course': 0, 'valid': True, 'acc': 1,
            'at': now - timedelta(minutes=m)} for m in (50, 40, 30, 20, 10, 0)]
    feed(app, imei, evs)
    with app.app_context():
        m = fleet.live()[0]
        assert m['state'] == 'idle' and m['field'] == 'D-04' and m['still_min'] >= 49 and 'bir joyda' in m['alert']
    home = world['rahbar'].get('/rahbar').get_data(as_text=True)
    assert 'Texnika xaritada' in home and '1 ta e’tibor' in home
    feed(app, imei, [dict(evs[-1], lat=42.3052, speed=9.0, at=datetime.now(timezone.utc))])
    with app.app_context():
        m = fleet.live()[0]
        assert m['state'] == 'field' and not m['alert']


def test_fuel_give_offers_jobs(app, world):
    yq, zp, qr = setup(app, world)
    page = yq.get('/yoqilgi/berish').get_data(as_text=True)
    assert 'Nima ish uchun?' in page and 'Shudgor' in page and 'Lazer (tekislash)' in page
    assert take(yq, zp, 100)['ok'] and give(yq, qr['T-01'], 20, purpose='Lazer (tekislash)')['ok']
    s = yq.post('/yoqilgi/skan', {'payload': qr['T-01'], 'want': 'equipment'}).get_json()
    assert s['machine']['job'] == 'Lazer (tekislash)'                       # the last job is preselected next time
