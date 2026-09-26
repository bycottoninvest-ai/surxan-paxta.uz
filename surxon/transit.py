"""Trips on the way to the punkt: where they left from, where the punkt is, and an ESTIMATED arrival.

This is not live tracking: the start point is where the trip's weighings were recorded (phone GPS) or where it was
opened, the end point is the punkt's coordinate, the road is taken as straight line × road_factor, and the tractor
drives at tractor_speed_kmh. Arrival ≈ time the waybill was issued + distance / speed.
"""
import math
from datetime import datetime, timedelta

from .db import q
from .settings import get_float
from .utils import now_str


def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def stations():
    return [dict(r) for r in q('SELECT id, name, lat, lon FROM stations WHERE active=1 AND lat IS NOT NULL')]


def on_the_way(station_id=None):
    """[{wb_id, number, trip_no, trailer, field, kg, sent_at, start, end, km, minutes, eta, progress, station}] — trips
    sent and not yet arrived, with an estimate when both ends are known (else eta=None)."""
    st = {s['id']: s for s in stations()}
    only = next(iter(st.values())) if len(st) == 1 else None
    speed = get_float('tractor_speed_kmh', 20) or 20
    factor = get_float('road_factor', 1.3) or 1.3
    now = datetime.strptime(now_str(), '%Y-%m-%d %H:%M:%S')
    sql = '''SELECT wb.id wb_id, wb.number, wb.net_kg kg, wb.created_at sent_at, tl.id lid, tl.trip_no, tl.station_id,
                    t.code trailer, f.code field, tl.open_lat, tl.open_lon,
                    (SELECT AVG(lat) FROM harvests h WHERE h.load_id=tl.id AND h.voided_at IS NULL AND h.lat IS NOT NULL) hlat,
                    (SELECT AVG(lon) FROM harvests h WHERE h.load_id=tl.id AND h.voided_at IS NULL AND h.lat IS NOT NULL) hlon
             FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id JOIN equipment t ON t.id=tl.trailer_id
             LEFT JOIN fields f ON f.id=tl.field_id
             WHERE wb.status='YARATILDI' AND wb.arrived_at IS NULL'''
    params = []
    if station_id:
        sql += ' AND (tl.station_id=? OR tl.station_id IS NULL)'
        params.append(station_id)
    out = []
    for r in q(sql + ' ORDER BY wb.id', params):
        s = st.get(r['station_id']) or only
        start = (r['hlat'], r['hlon']) if r['hlat'] is not None else ((r['open_lat'], r['open_lon']) if r['open_lat'] is not None else None)
        d = {'wb_id': r['wb_id'], 'number': r['number'], 'trip_no': r['trip_no'], 'trailer': r['trailer'], 'field': r['field'],
             'kg': r['kg'], 'sent_at': r['sent_at'], 'lid': r['lid'], 'eta': None, 'minutes': None, 'progress': None,
             'start': list(start) if start else None, 'end': [s['lat'], s['lon']] if s else None, 'station': s['name'] if s else None}
        if start and s:
            km = _km(start, (s['lat'], s['lon'])) * factor
            minutes = max(1, round(km / speed * 60))
            sent = datetime.strptime(r['sent_at'][:19], '%Y-%m-%d %H:%M:%S')
            eta = sent + timedelta(minutes=minutes)
            d.update(km=round(km, 1), minutes=minutes, eta=eta.strftime('%H:%M'),
                     left=max(0, round((eta - now).total_seconds() / 60)),
                     progress=round(min(0.97, max(0.0, (now - sent).total_seconds() / 60 / minutes)), 3))
        out.append(d)
    return out
