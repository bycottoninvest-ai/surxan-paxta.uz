"""Machines with GPS trackers: where they are, what they are doing, how much diesel the work should have taken.

Positions come from surxon.gt06 (one row per ~30 s while moving, every 5 min while standing). A day is cut into
segments between consecutive positions and each segment is one of:
  field — engine on, moving, inside a field contour (the machine is working that field),
  road  — engine on, moving, outside fields or fast (driving),
  idle  — engine on, not moving (diesel burns, no work),
  off   — engine off / no data (gaps longer than GAP_CAP are not counted).
The job (Paxta terish, Shudgor, Lazer …) is the one chosen when the machine last got fuel. Expected litres =
field hours × job norm + road km × l/km + idle hours × idle l/h; it is compared with the fuel actually given in the
Yoqilg‘i section. This is an estimate to look at, never a verdict: norms are editable (Sozlamalar, Texnikalar).
Finished days are stored in vehicle_days / vehicle_works so the season view stays fast.
"""
import json
import math
from datetime import date, datetime, timedelta, timezone

from .db import q, tx
from .settings import get_float, get_setting
from .utils import now, now_str, today_str

KIND_NORMS = {'traktor': (9.0, 0.30, 2.0), 'kombayn': (20.0, 0.50, 3.0), 'mashina': (0.0, 0.25, 1.5)}
TRACKED = ('traktor', 'kombayn', 'mashina')
OFFLINE_MIN = 15
GAP_CAP = 600                 # s: a longer silence is not counted as work or idling
KEEP_M, KEEP_SEC = 15, 300    # store a position after moving 15 m, or every 5 min, or when the engine switches
MOVE_KMH = 2.0
FIELD_MAX_KMH = 18.0
NO_JOB = 'Belgilanmagan'


def _dt(s):
    return datetime.strptime(s, '%Y-%m-%d %H:%M:%S')


def _m(a_lat, a_lon, b_lat, b_lon):
    k = math.cos(math.radians(a_lat))
    return math.hypot((a_lat - b_lat) * 110540, (a_lon - b_lon) * 111320 * k)


# ------------------------------------------------------------------ jobs and norms

def work_types():
    """[(name, l/h)] from the setting “Paxta terish=9, Shudgor=14, …”."""
    out = []
    for part in (get_setting('fleet_work_types') or '').split(','):
        name, _, val = part.partition('=')
        name = name.strip()
        if not name:
            continue
        try:
            lph = float(val.strip().replace(',', '.')) if val.strip() else None
        except ValueError:
            lph = None
        out.append((name[:40], lph))
    return out


def norms(eq, job=None):
    """(field l/h, road l/km, idle l/h) for a machine row and a job name."""
    f, r, i = KIND_NORMS.get(eq['kind'], KIND_NORMS['traktor'])
    if eq['kind'] == 'traktor' and job:
        f = dict(work_types()).get(job) or f
    return (eq['norm_field_lph'] or f, eq['norm_road_lpkm'] or r, eq['norm_idle_lph'] or i)


def work_hours(eq):
    raw = (eq['work_hours'] if eq and eq['work_hours'] else None) or get_setting('fleet_work_hours') or '07:00-19:00'
    try:
        a, b = [x.strip() for x in raw.split('-')]
        return a[:5].zfill(5), b[:5].zfill(5)
    except ValueError:
        return '07:00', '19:00'


def in_hours(eq, hhmm):
    a, b = work_hours(eq)
    return a <= hhmm <= b if a <= b else (hhmm >= a or hhmm <= b)


# ------------------------------------------------------------------ fields

_fields = {}


def fields():
    """[(id, code, bbox, contour, locator, cell_ha)] of fields with a contour (cached until fields change)."""
    from . import geo
    st = q('SELECT COUNT(*) n, MAX(id) m, SUM(LENGTH(polygon_json)) l FROM fields WHERE polygon_json IS NOT NULL', one=True)
    key = (st['n'], st['m'], st['l'])
    if _fields.get('key') != key:
        rows = []
        for r in q('SELECT id, code, area_ha, polygon_json FROM fields WHERE polygon_json IS NOT NULL'):
            try:
                poly = json.loads(r['polygon_json'])
            except ValueError:
                continue
            if len(poly) < 3:
                continue
            cells = geo.field_grid(poly)[0]
            ha = r['area_ha'] or geo.poly_area_ha(poly) or 0
            bbox = (min(p[0] for p in poly), max(p[0] for p in poly), min(p[1] for p in poly), max(p[1] for p in poly))
            rows.append((r['id'], r['code'], bbox, poly, geo.grid_locator(poly), ha / len(cells) if cells else 0))
        _fields.update(key=key, rows=rows)
    return _fields['rows']


def field_at(lat, lon):
    for f in fields():
        b = f[2]
        if b[0] <= lat <= b[1] and b[2] <= lon <= b[3]:
            pts, c = f[3], False
            for i in range(len(pts)):
                (yi, xi), (yj, xj) = pts[i], pts[i - 1]
                if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                    c = not c
            if c:
                return f
    return None


# ------------------------------------------------------------------ storing tracker data

def _local(at_utc):
    if not at_utc:
        return None
    from .utils import tz
    return at_utc.astimezone(tz()).strftime('%Y-%m-%d %H:%M:%S')


def store_event(imei, ev):
    """Called by the GT06 server for login / gps / status packets."""
    ts = now_str()
    with tx() as db:
        t = db.execute('SELECT * FROM trackers WHERE imei=?', (imei,)).fetchone()
        if not t:
            db.execute('INSERT INTO trackers(imei, first_seen, last_seen) VALUES (?,?,?)', (imei, ts, ts))
            t = db.execute('SELECT * FROM trackers WHERE imei=?', (imei,)).fetchone()
        else:
            db.execute('UPDATE trackers SET last_seen=? WHERE imei=?', (ts, imei))
        eid = t['equipment_id']
        if ev['kind'] == 'status':
            db.execute('UPDATE trackers SET last_acc=?, power=?, gsm=? WHERE imei=?', (ev['acc'], ev.get('power'), ev.get('gsm'), imei))
            if eid and t['last_lat'] is not None:
                _keep(db, eid, t['last_lat'], t['last_lon'], 0.0, t['last_course'], ev['acc'], ts, force=ev['acc'] != t['last_acc'])
            return
        if ev['kind'] != 'gps' or not ev.get('valid', True) or (abs(ev['lat']) < 1e-6 and abs(ev['lon']) < 1e-6):
            return
        at = _local(ev.get('at')) or ts
        if at > (_dt(ts) + timedelta(minutes=2)).strftime('%Y-%m-%d %H:%M:%S') or at < (_dt(ts) - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S'):
            at = ts                                     # clock not set yet: use the time it arrived
        acc = ev['acc'] if ev.get('acc') is not None else t['last_acc']
        if not t['last_fix_at'] or at >= t['last_fix_at']:
            db.execute('''UPDATE trackers SET last_lat=?, last_lon=?, last_speed=?, last_course=?, last_fix_at=?,
                          last_acc=COALESCE(?, last_acc) WHERE imei=?''',
                       (ev['lat'], ev['lon'], ev['speed'], ev['course'], at, ev.get('acc'), imei))
        if eid:
            _keep(db, eid, ev['lat'], ev['lon'], ev['speed'], ev['course'], acc, at, force=acc != t['last_acc'])


def _keep(db, eid, lat, lon, speed, course, acc, at, force=False):
    last = db.execute('SELECT * FROM vehicle_positions WHERE equipment_id=? ORDER BY at DESC, id DESC LIMIT 1', (eid,)).fetchone()
    if last and not force and at >= last['at']:
        if (_dt(at) - _dt(last['at'])).total_seconds() < KEEP_SEC and _m(lat, lon, last['lat'], last['lon']) < KEEP_M:
            return False
    db.execute('INSERT INTO vehicle_positions(equipment_id, lat, lon, speed, course, acc, at) VALUES (?,?,?,?,?,?,?)',
               (eid, round(lat, 7), round(lon, 7), speed, course, acc, at))
    if at[:10] < ts_today():
        db.execute('DELETE FROM vehicle_days WHERE equipment_id=? AND day=?', (eid, at[:10]))   # late (buffered) data
        db.execute('DELETE FROM vehicle_works WHERE equipment_id=? AND day=?', (eid, at[:10]))
    return True


def ts_today():
    return today_str()


# ------------------------------------------------------------------ analysing a day

def jobs_timeline(eid, until):
    """[(time, job)] from fuel given to the machine (the job chosen at the pump), oldest first."""
    return [(r['created_at'], r['purpose']) for r in
            q('''SELECT created_at, purpose FROM fuel_ops WHERE equipment_id=? AND kind='BERISH' AND voided_at IS NULL
                 AND purpose IS NOT NULL AND created_at<=? ORDER BY created_at''', (eid, until))]


def _job_at(timeline, at, default):
    job = default
    for t, j in timeline:
        if t <= at:
            job = j
        else:
            break
    return job


def segments(eq, day):
    """Classified segments of one machine's day: [(a, b, kind, dt_s, km, field_id, job)] with a/b = point rows."""
    pts = q('''SELECT lat, lon, speed, acc, at FROM vehicle_positions WHERE equipment_id=? AND at BETWEEN ? AND ?
               ORDER BY at, id''', (eq['id'], day + ' 00:00:00', day + ' 23:59:59'))
    timeline = jobs_timeline(eq['id'], day + ' 23:59:59')
    default = 'Paxta terish' if eq['kind'] == 'kombayn' else NO_JOB
    out = []
    for a, b in zip(pts, pts[1:]):
        dt = (_dt(b['at']) - _dt(a['at'])).total_seconds()
        if dt <= 0:
            continue
        dist = _m(a['lat'], a['lon'], b['lat'], b['lon'])
        kmh = dist / dt * 3.6
        if dt > GAP_CAP:
            out.append((a, b, 'off', dt, 0.0, None, None))
            continue
        engine = a['acc'] == 1 if a['acc'] is not None else ((a['speed'] or 0) > 3 or kmh > 3)
        moving = kmh >= MOVE_KMH or (a['speed'] or 0) >= 3
        if not engine and not moving:
            out.append((a, b, 'off', dt, 0.0, None, None))
        elif not moving:
            out.append((a, b, 'idle', dt, 0.0, None, None))
        else:
            f = field_at((a['lat'] + b['lat']) / 2, (a['lon'] + b['lon']) / 2)
            if f and kmh <= FIELD_MAX_KMH and eq['kind'] != 'mashina':
                out.append((a, b, 'field', dt, dist / 1000, f[0], _job_at(timeline, a['at'], default)))
            else:
                out.append((a, b, 'road', dt, dist / 1000, None, None))
    return out


def analyse(eq, day):
    """{'engine_h','field_h','road_km','idle_h','points', 'works': {(field_id, job): {'h','km','cells'}}}."""
    segs = segments(eq, day)
    res = {'engine_h': 0.0, 'field_h': 0.0, 'road_km': 0.0, 'idle_h': 0.0, 'points': len(segs) + (1 if segs else 0), 'works': {}}
    locs = {f[0]: f[4] for f in fields()}
    for a, b, kind, dt, km, fid, job in segs:
        h = dt / 3600
        if kind in ('field', 'road', 'idle'):
            res['engine_h'] += h
        if kind == 'idle':
            res['idle_h'] += h
        elif kind == 'road':
            res['road_km'] += km
        elif kind == 'field':
            res['field_h'] += h
            w = res['works'].setdefault((fid, job), {'h': 0.0, 'km': 0.0, 'cells': set()})
            w['h'] += h
            w['km'] += km
            loc = locs.get(fid)
            n = max(1, int(km * 1000 / 10))
            for k in range(n + 1):                       # every ~10 m along the pass
                c = loc(a['lat'] + (b['lat'] - a['lat']) * k / n, a['lon'] + (b['lon'] - a['lon']) * k / n) if loc else None
                if c:
                    w['cells'].add(c)
    return res


def day_stats(eq, day):
    """Cached for finished days; today is always computed fresh."""
    if day < today_str():
        row = q('SELECT * FROM vehicle_days WHERE equipment_id=? AND day=?', (eq['id'], day), one=True)
        if row:
            works = {(w['field_id'], w['purpose']): {'h': w['hours'], 'km': w['km'], 'cells': set(json.loads(w['cells'] or '[]'))}
                     for w in q('SELECT * FROM vehicle_works WHERE equipment_id=? AND day=?', (eq['id'], day))}
            return {'engine_h': row['engine_h'], 'field_h': row['field_h'], 'road_km': row['road_km'], 'idle_h': row['idle_h'],
                    'points': row['points'], 'works': works}
    res = analyse(eq, day)
    if day < today_str() and res['points']:
        with tx() as db:
            db.execute('''INSERT OR REPLACE INTO vehicle_days(equipment_id, day, engine_h, field_h, road_km, idle_h, points)
                          VALUES (?,?,?,?,?,?,?)''', (eq['id'], day, res['engine_h'], res['field_h'], res['road_km'],
                                                     res['idle_h'], res['points']))
            db.execute('DELETE FROM vehicle_works WHERE equipment_id=? AND day=?', (eq['id'], day))
            for (fid, job), w in res['works'].items():
                db.execute('''INSERT INTO vehicle_works(equipment_id, day, field_id, purpose, hours, km, cells)
                              VALUES (?,?,?,?,?,?,?)''', (eq['id'], day, fid, job, w['h'], w['km'], json.dumps(sorted(w['cells']))))
    return res


def expected_l(eq, st):
    total = 0.0
    for (fid, job), w in st['works'].items():
        total += w['h'] * norms(eq, job)[0]
    _, road, idle = norms(eq)
    return total + st['road_km'] * road + st['idle_h'] * idle


def data_days(eid, a, b):
    """Days in [a, b] on which the machine sent positions (index-only scan)."""
    return [r['d'] for r in q('''SELECT DISTINCT substr(at,1,10) d FROM vehicle_positions WHERE equipment_id=? AND at BETWEEN ? AND ?
                                 ORDER BY d''', (eid, a + ' 00:00:00', min(b, today_str()) + ' 23:59:59'))]


def _days(a, b):
    d, e = date.fromisoformat(a), date.fromisoformat(min(b, today_str()))
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)


# ------------------------------------------------------------------ what the screens show

def machines():
    return q(f'''SELECT e.*, t.imei, t.last_seen, t.last_lat, t.last_lon, t.last_speed, t.last_course, t.last_acc,
                        t.last_fix_at, t.power, t.gsm
                 FROM equipment e JOIN trackers t ON t.equipment_id=e.id WHERE e.active=1 ORDER BY e.kind, e.code''')


def _since_still(eid, lat, lon):
    """When the machine last moved more than 50 m away from where it is now (looking back 12 hours, across midnight)."""
    since12 = (now().replace(tzinfo=None) - timedelta(hours=12)).strftime('%Y-%m-%d %H:%M:%S')
    rows = q('''SELECT lat, lon, at FROM vehicle_positions WHERE equipment_id=? AND at>=? ORDER BY at DESC LIMIT 400''',
             (eid, since12))
    since = None
    for r in rows:
        if _m(lat, lon, r['lat'], r['lon']) > 50:
            break
        since = r['at']
    return since


def live():
    """Every tracked machine now: position, state, how long it has been standing, flags for the dashboard."""
    n = now().replace(tzinfo=None)
    alert_min = get_float('fleet_idle_alert_min', 30) or 30
    out = []
    for e in machines():
        d = {'id': e['id'], 'code': e['code'], 'kind': e['kind'], 'operator': e['operator_name'] or '',
             'lat': e['last_lat'], 'lon': e['last_lon'], 'speed': round(e['last_speed'] or 0), 'course': e['last_course'] or 0,
             'acc': e['last_acc'], 'at': (e['last_fix_at'] or e['last_seen'] or '')[11:16], 'field': None,
             'state': 'none', 'label': 'Hali joylashuv kelmagan', 'still_min': 0, 'alert': None, 'since': None}
        if e['last_lat'] is None:
            out.append(d)
            continue
        f = field_at(e['last_lat'], e['last_lon'])
        d['field'] = f[1] if f else None
        seen = _dt(e['last_seen']) if e['last_seen'] else None
        age = (n - seen).total_seconds() / 60 if seen else 1e9
        hhmm = n.strftime('%H:%M')
        fix_age = (n - _dt(e['last_fix_at'])).total_seconds() / 60 if e['last_fix_at'] else 1e9
        if age > OFFLINE_MIN:
            d.update(state='offline', label=f'Aloqa yo‘q ({_when(e["last_seen"], n)} dan beri)')
        elif fix_age > OFFLINE_MIN and e['last_acc'] != 0:
            d.update(state='offline', label=f'GPS signal yo‘q · oxirgi joy {_when(e["last_fix_at"], n)}')
        elif (e['last_speed'] or 0) >= 3:
            if f and e['last_speed'] <= FIELD_MAX_KMH and e['kind'] != 'mashina':
                d.update(state='field', label=f'Dalada ishlayapti · {f[1]}')
            else:
                d.update(state='road', label=f'Yo‘lda · {round(e["last_speed"])} km/soat')
            if not in_hours(e, hhmm):
                d['alert'] = 'Ish vaqtidan tashqari yuryapti'
        else:
            since = _since_still(e['id'], e['last_lat'], e['last_lon'])
            mins = int((n - _dt(since)).total_seconds() // 60) if since else 0
            d['still_min'] = mins
            d['since'] = since
            where = f' · {f[1]}' if f else ''
            if e['last_acc'] == 1:
                d.update(state='idle', label=f'Motor yoniq turibdi{where}' + (f' · {_dur(mins)}' if mins >= 5 else ''))
            else:
                d.update(state='parked', label=f'Turibdi{where}' + (f' · {_dur(mins)}' if mins >= 5 else ''))
            if mins >= alert_min and in_hours(e, hhmm):
                d['alert'] = f'{_dur(mins)} dan beri bir joyda' + (' (motor yoniq)' if e['last_acc'] == 1 else '')
        out.append(d)
    order = {'field': 0, 'road': 1, 'idle': 2, 'parked': 3, 'offline': 4, 'none': 5}
    return sorted(out, key=lambda x: (not x['alert'], order[x['state']], x['code']))


def _when(ts, n):
    if not ts:
        return '—'
    return ts[11:16] if ts[:10] == n.strftime('%Y-%m-%d') else f'{ts[8:10]}.{ts[5:7]} {ts[11:16]}'


def _dur(mins):
    return f'{mins // 60} soat {mins % 60} daq' if mins >= 60 else f'{mins} daq'


def track(eid, day=None):
    """Today's path as runs of the same kind: [{'kind', 'pts': [[lat, lon], …], 'from', 'to'}]."""
    eq = q('SELECT * FROM equipment WHERE id=?', (eid,), one=True)
    if not eq:
        return []
    runs = []
    for a, b, kind, *_ in segments(eq, day or today_str()):
        if kind == 'off' and (_m(a['lat'], a['lon'], b['lat'], b['lon']) < 30):
            continue
        if runs and runs[-1]['kind'] == kind and runs[-1]['pts'][-1] == [a['lat'], a['lon']]:
            runs[-1]['pts'].append([b['lat'], b['lon']])
            runs[-1]['to'] = b['at'][11:16]
        else:
            runs.append({'kind': kind, 'pts': [[a['lat'], a['lon']], [b['lat'], b['lon']]], 'from': a['at'][11:16], 'to': b['at'][11:16]})
    return runs


def usage(date_from, date_to):
    """Per tracked machine over the period: hours, km, expected vs given diesel."""
    over = get_float('fleet_over_pct', 15) or 15
    out = []
    for e in machines():
        tot = {'engine_h': 0.0, 'field_h': 0.0, 'road_km': 0.0, 'idle_h': 0.0, 'exp': 0.0}
        for day in data_days(e['id'], date_from, date_to):
            st = day_stats(e, day)
            for k in ('engine_h', 'field_h', 'road_km', 'idle_h'):
                tot[k] += st[k]
            tot['exp'] += expected_l(e, st)
        given = q('''SELECT COALESCE(SUM(liters),0) l FROM fuel_ops WHERE equipment_id=? AND kind='BERISH' AND voided_at IS NULL
                     AND substr(created_at,1,10) BETWEEN ? AND ?''', (e['id'], date_from, date_to), one=True)['l']
        exp = round(tot['exp'])
        diff = round(given - exp) if exp else None
        flag = bool(exp and given > exp * (1 + over / 100) and given - exp >= 10)
        out.append({'id': e['id'], 'code': e['code'], 'kind': e['kind'], 'operator': e['operator_name'] or '',
                    'engine_h': round(tot['engine_h'], 1), 'field_h': round(tot['field_h'], 1), 'road_km': round(tot['road_km']),
                    'idle_h': round(tot['idle_h'], 1), 'expected': exp, 'given': round(given), 'diff': diff, 'flag': flag,
                    'pct': round((given - exp) / exp * 100) if exp else None,
                    'lph': round(given / tot['engine_h'], 1) if tot['engine_h'] >= 1 and given else None})
    return sorted(out, key=lambda x: (not x['flag'], -(x['diff'] or 0)))


def works(date_from, date_to, field_id=None):
    """Done work per field and job: hours, machines, covered part of the field (grid cells → ha, %)."""
    cell_ha = {f[0]: f[5] for f in fields()}
    ncells = {}
    agg = {}
    for e in machines():
        for day in data_days(e['id'], date_from, date_to):
            for (fid, job), w in day_stats(e, day)['works'].items():
                if field_id and fid != field_id:
                    continue
                a = agg.setdefault((fid, job), {'h': 0.0, 'cells': set(), 'machines': set(), 'first': day, 'last': day})
                a['h'] += w['h']
                a['cells'] |= set(w['cells'])
                a['machines'].add(e['code'])
                a['first'], a['last'] = min(a['first'], day), max(a['last'], day)
    if not agg:
        return []
    names = {r['id']: r for r in q('SELECT id, code, name, area_ha, polygon_json FROM fields')}
    out = []
    for (fid, job), a in agg.items():
        if a['h'] < 0.2:                               # a few minutes crossing a field edge is not work
            continue
        f = names.get(fid)
        if fid not in ncells and f and f['polygon_json']:
            from . import geo
            ncells[fid] = len(geo.field_grid(json.loads(f['polygon_json']))[0]) or 1
        ha = len(a['cells']) * cell_ha.get(fid, 0)
        out.append({'field_id': fid, 'code': f['code'] if f else '?', 'name': f['name'] if f else '', 'job': job,
                    'hours': round(a['h'], 1), 'ha': round(ha, 1), 'field_ha': round((f['area_ha'] or 0), 1) if f else 0,
                    'pct': min(100, round(len(a['cells']) / ncells.get(fid, 1) * 100)), 'machines': sorted(a['machines']),
                    'first': a['first'], 'last': a['last'], 'cells': sorted(a['cells'])})
    return sorted(out, key=lambda x: (x['last'], x['code']), reverse=True)


def unknown_trackers():
    return q('SELECT * FROM trackers WHERE equipment_id IS NULL ORDER BY last_seen DESC LIMIT 20')


def assign_tracker(actor, eid, imei):
    """Link an IMEI to a machine (blank = unlink). Audited."""
    from .security import audit
    from .utils import UserError
    imei = ''.join(ch for ch in (imei or '') if ch.isdigit())
    if imei and not 10 <= len(imei) <= 16:
        raise UserError('IMEI 15 ta raqamdan iborat bo‘ladi (trekerning yorlig‘ida yoki SMS: IMEI#).')
    with tx() as db:
        old = db.execute('SELECT imei FROM trackers WHERE equipment_id=?', (eid,)).fetchone()
        if old and old['imei'] == imei:
            return
        if imei:
            other = db.execute('''SELECT e.code FROM trackers t JOIN equipment e ON e.id=t.equipment_id
                                  WHERE t.imei=? AND t.equipment_id<>?''', (imei, eid)).fetchone()
            if other:
                raise UserError(f'Bu treker {other["code"]} ga ulangan — avval o‘sha texnikadan olib tashlang.')
        db.execute('UPDATE trackers SET equipment_id=NULL WHERE equipment_id=?', (eid,))
        if imei:
            db.execute('INSERT OR IGNORE INTO trackers(imei, first_seen) VALUES (?,?)', (imei, now_str()))
            db.execute('UPDATE trackers SET equipment_id=? WHERE imei=?', (eid, imei))
        audit(db, actor, 'UPDATE', 'equipment', eid, old={'tracker': old['imei'] if old else None}, new={'tracker': imei or None})


def save_machine_gps(actor, eid, *, imei, norm_field='', norm_road='', norm_idle='', hours=''):
    """Tracker IMEI, fuel norms (blank = default for the kind / job) and working hours of one machine."""
    from .security import audit
    from .utils import UserError

    def num(v, label, hi):
        v = (v or '').strip().replace(',', '.')
        if not v:
            return None
        try:
            x = float(v)
        except ValueError:
            raise UserError(f'{label}: raqam kiriting.')
        if not 0 < x <= hi:
            raise UserError(f'{label}: 0 dan katta va {hi:g} dan kichik bo‘lsin.')
        return x
    nf, nr, ni = num(norm_field, 'Dalada l/soat', 100), num(norm_road, 'Yo‘lda l/km', 5), num(norm_idle, 'Turganda l/soat', 30)
    hours = (hours or '').strip().replace(' ', '')
    if hours:
        import re
        if not re.fullmatch(r'\d{1,2}:\d{2}-\d{1,2}:\d{2}', hours):
            raise UserError('Ish vaqti shunday yozilsin: 07:00-19:00')
    assign_tracker(actor, eid, imei)
    with tx() as db:
        old = db.execute('SELECT norm_field_lph, norm_road_lpkm, norm_idle_lph, work_hours FROM equipment WHERE id=?', (eid,)).fetchone()
        new = (nf, nr, ni, hours or None)
        if tuple(old) != new:
            db.execute('UPDATE equipment SET norm_field_lph=?, norm_road_lpkm=?, norm_idle_lph=?, work_hours=? WHERE id=?', new + (eid,))
            audit(db, actor, 'UPDATE', 'equipment', eid, old=dict(old), new=dict(zip(old.keys(), new)))



# ------------------------------------------------------------------ one fuel issue: where was it used

def range_stats(eq, start, end):
    """Like analyse() but for any time window (may span days); also the path runs for a map."""
    res = {'engine_h': 0.0, 'field_h': 0.0, 'road_km': 0.0, 'idle_h': 0.0, 'works': {}, 'runs': []}
    locs = {f[0]: f[4] for f in fields()}
    for day in data_days(eq['id'], start[:10], end[:10]):
        for a, b, kind, dt, km, fid, job in segments(eq, day):
            if not (start <= a['at'] < end):
                continue
            h = dt / 3600
            if kind in ('field', 'road', 'idle'):
                res['engine_h'] += h
            if kind == 'idle':
                res['idle_h'] += h
            elif kind == 'road':
                res['road_km'] += km
            elif kind == 'field':
                res['field_h'] += h
                w = res['works'].setdefault((fid, job), {'h': 0.0, 'km': 0.0, 'cells': set()})
                w['h'] += h
                w['km'] += km
                loc, n = locs.get(fid), max(1, int(km * 1000 / 10))
                for k in range(n + 1):
                    c = loc(a['lat'] + (b['lat'] - a['lat']) * k / n, a['lon'] + (b['lon'] - a['lon']) * k / n) if loc else None
                    if c:
                        w['cells'].add(c)
            if kind == 'off':
                continue
            r = res['runs']
            if r and r[-1]['kind'] == kind and r[-1]['pts'][-1] == [a['lat'], a['lon']]:
                r[-1]['pts'].append([b['lat'], b['lon']])
            else:
                r.append({'kind': kind, 'pts': [[a['lat'], a['lon']], [b['lat'], b['lon']]]})
    return res


def fuel_use(op):
    """For a BERISH: from this fill-up until the machine's next one — where it worked, and the expected litres."""
    if not op or op['kind'] != 'BERISH' or not op['equipment_id']:
        return None
    eq = q('SELECT e.*, t.imei FROM equipment e LEFT JOIN trackers t ON t.equipment_id=e.id WHERE e.id=?', (op['equipment_id'],), one=True)
    if not eq or not eq['imei']:
        return None
    nxt = q('''SELECT created_at FROM fuel_ops WHERE equipment_id=? AND kind='BERISH' AND voided_at IS NULL AND created_at>?
               ORDER BY created_at LIMIT 1''', (eq['id'], op['created_at']), one=True)
    end = nxt['created_at'] if nxt else now_str()
    st = range_stats(eq, op['created_at'], end)
    exp = expected_l(eq, st)
    names = {r['id']: r['code'] for r in q('SELECT id, code FROM fields')}
    from . import geo
    polys = {r['id']: json.loads(r['polygon_json']) for r in q('SELECT id, polygon_json FROM fields WHERE polygon_json IS NOT NULL')}
    cell_ha = {f[0]: f[5] for f in fields()}
    works_ = []
    for (fid, job), w in sorted(st['works'].items(), key=lambda x: -x[1]['h']):
        if w['h'] < 0.1:
            continue
        cells = [c['poly'] for c in geo.field_grid(polys.get(fid))[0] if c['id'] in w['cells']] if polys.get(fid) else []
        works_.append({'code': names.get(fid, '?'), 'job': job, 'hours': round(w['h'], 1),
                       'ha': round(len(w['cells']) * cell_ha.get(fid, 0), 1), 'polys': cells,
                       'litres': round(w['h'] * norms(eq, job)[0])})
    return {'code': eq['code'], 'from': op['created_at'], 'to': end, 'open': not nxt, 'engine_h': round(st['engine_h'], 1),
            'field_h': round(st['field_h'], 1), 'road_km': round(st['road_km'], 1), 'idle_h': round(st['idle_h'], 1),
            'expected': round(exp), 'given': op['liters'], 'works': works_, 'runs': st['runs'],
            'road_l': round(st['road_km'] * norms(eq)[1]), 'idle_l': round(st['idle_h'] * norms(eq)[2])}


# ------------------------------------------------------------------ one field: everything done on it this season

def field_history(fid, year):
    """Harvest rounds (from trips and the marked picked area) and machine work (GPS + job at the pump)."""
    from . import geo, picking
    f = picking.field_row(fid, year)
    if not f:
        return None
    cells, cell_ha = picking.grid(f)
    by_id = {c['id']: c['poly'] for c in cells}
    rounds = []
    rep = next((r for r in picking.season_report(year) if r['id'] == fid), None)
    for rnd in picking.ROUNDS:
        t = q('''SELECT COUNT(DISTINCT tl.id) n, MIN(tl.load_date) a, MAX(tl.load_date) b FROM trailer_loads tl
                 WHERE tl.season_year=? AND tl.status<>'BEKOR' AND COALESCE(tl.harvest_round,1)=? AND
                 (tl.field_id=? OR tl.picked_split LIKE ?)''', (year, rnd, fid, f'%"{fid}"%'), one=True)
        marked = picking._marked(year, rnd).get(fid, set())
        if not t['n'] and not marked:
            continue
        rr = next((x for x in (rep or {}).get('rounds', []) if x['round'] == rnd), {})
        rounds.append({'round': rnd, 'label': f'{rnd}-terim', 'trips': t['n'], 'first': t['a'], 'last': t['b'],
                       'kg': rr.get('kg', 0), 'ha': rr.get('ha'), 'pct': rr.get('cover'), 'cha': rr.get('cha'),
                       'polys': [by_id[c] for c in marked if c in by_id]})
    works_ = works(f'{year}-01-01', f'{year}-12-31', fid)
    locator = {c['id']: c['poly'] for c in cells}
    for w in works_:
        w['polys'] = [locator[c] for c in w.pop('cells') if c in locator]
    return {'id': f['id'], 'code': f['code'], 'name': f['name'], 'area_ha': f['area_ha'], 'rounds': rounds, 'works': works_,
            'poly': json.loads(f['polygon_json']) if f['polygon_json'] else None}


def fields_overview(year):
    """Every field with a contour and its latest activity (for the map colours)."""
    import json as _j
    from . import picking
    last = {}
    for r in q('''SELECT field_id, MAX(load_date) d, MAX(COALESCE(harvest_round,1)) rnd FROM trailer_loads
                  WHERE season_year=? AND status<>'BEKOR' GROUP BY field_id''', (year,)):
        last[r['field_id']] = (r['d'], f'{r["rnd"]}-terim', 'pick')
    for w in works(f'{year}-01-01', f'{year}-12-31'):
        if w['last'] >= last.get(w['field_id'], ('',))[0]:
            last[w['field_id']] = (w['last'], w['job'], 'job')
    out = []
    for r in q('''SELECT f.id, f.code, f.name, f.polygon_json, COALESCE(fs.area_ha, f.area_ha) ha FROM fields f
                  LEFT JOIN field_seasons fs ON fs.field_id=f.id AND fs.year=? WHERE f.active=1 AND f.polygon_json IS NOT NULL
                  ORDER BY f.code''', (year,)):
        d = last.get(r['id'])
        out.append({'id': r['id'], 'code': r['code'], 'name': r['name'], 'ha': r['ha'], 'poly': _j.loads(r['polygon_json']),
                    'last': d[0] if d else None, 'what': d[1] if d else None, 'kind': d[2] if d else None})
    return sorted(out, key=lambda x: (x['last'] or '', ), reverse=True)
