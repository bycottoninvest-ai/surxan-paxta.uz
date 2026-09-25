"""Office TV wallboard — one read-only snapshot of the farm, built only from saved operations.

No money is ever put on the TV (cash, wages, debts, prices). Worker names are shortened (“Abdurahmon Q.”) and can be
switched off in the settings (tv_show_workers = 0). Empty data is shown as “—”, never as an invented 0.
"""
from datetime import date, timedelta

from . import director as D
from .db import q, scalar
from .settings import get_float, get_setting
from .utils import now_str, today_str

WEEKDAYS = ['Dushanba', 'Seshanba', 'Chorshanba', 'Payshanba', 'Juma', 'Shanba', 'Yakshanba']


def _short(name):
    parts = (name or '').split()
    if not parts:
        return ''
    return parts[0] if len(parts) == 1 else f'{parts[0]} {parts[1][0]}.'


def _trailer_state(t):
    """Current state of each trailer from its latest trip."""
    if t['status'] == 'OCHIQ':
        return 'Dalada', 'field'
    if t['status'] == 'TOLDI':
        return 'To‘ldi', 'full'
    if t['wb_status'] == 'YARATILDI':
        return ('Navbatda', 'queue') if t['arrived_at'] else ('Yo‘lda', 'road')
    if t['wb_status'] == 'QABUL' and (t['received_at'] or '')[:10] == today_str():
        return 'Qabul', 'done'
    return 'Kutmoqda', 'idle'


def trailers():
    rows = q('''SELECT e.code, tl.status, wb.status wb_status, wb.arrived_at, nr.created_at received_at
                FROM equipment e
                LEFT JOIN trailer_loads tl ON tl.id=(SELECT id FROM trailer_loads x WHERE x.trailer_id=e.id AND x.status<>'BEKOR'
                                                      ORDER BY x.id DESC LIMIT 1)
                LEFT JOIN waybills wb ON wb.load_id=tl.id AND wb.status<>'BEKOR'
                LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                WHERE e.kind='telashka' AND e.active=1 ORDER BY e.code''')
    out = []
    for r in rows:
        label, key = _trailer_state(r)
        out.append({'code': r['code'], 'state': label, 'key': key})
    return out


def combines(day):
    return [dict(r) for r in q('''SELECT e.code, e.operator_name,
                                         COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.combine_id=e.id AND h.voided_at IS NULL
                                                   AND h.work_date=?),0) kg,
                                         (SELECT MAX(created_at) FROM harvests h WHERE h.combine_id=e.id AND h.voided_at IS NULL) last_at
                                  FROM equipment e WHERE e.kind='kombayn' AND e.active=1 ORDER BY e.code''', (day,))]


def fields_map(year):
    import json
    out = []
    for r in q('''SELECT f.id, f.code, f.name, COALESCE(fs.area_ha, f.area_ha) area_ha, f.polygon_json,
                         COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.field_id=f.id AND h.season_year=? AND h.voided_at IS NULL),0) kg
                  FROM fields f LEFT JOIN field_seasons fs ON fs.field_id=f.id AND fs.year=?
                  WHERE f.active=1 OR fs.field_id IS NOT NULL ORDER BY f.code''', (year, year)):
        try:
            poly = json.loads(r['polygon_json']) if r['polygon_json'] else None
        except ValueError:
            poly = None
        out.append({'code': r['code'], 'name': r['name'], 'ha': r['area_ha'] or 0, 'kg': r['kg'],
                    'kg_ha': round(r['kg'] / r['area_ha']) if r['area_ha'] and r['kg'] else None, 'poly': poly})
    return out


def brigades(day):
    rows = q('''SELECT b.name, SUM(h.kg) kg, COUNT(DISTINCT h.worker_id) people FROM harvests h
                JOIN brigadiers b ON b.id=h.brigadier_id
                WHERE h.voided_at IS NULL AND h.work_date=? AND h.method='hand' GROUP BY b.id ORDER BY kg DESC LIMIT 6''', (day,))
    return [{'name': r['name'], 'kg': r['kg'], 'people': r['people'],
             'per': round(r['kg'] / r['people']) if r['people'] else None} for r in rows]


def hourly(day):
    y = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    out = {h: [0, 0] for h in range(6, 21)}
    for i, d in enumerate((day, y)):
        for r in q('''SELECT CAST(substr(created_at,12,2) AS INTEGER) h, SUM(kg) kg FROM harvests
                      WHERE voided_at IS NULL AND work_date=? GROUP BY h''', (d,)):
            if r['h'] in out:
                out[r['h']][i] = r['kg']
    return [{'h': f'{h:02d}:00', 'today': v[0], 'yesterday': v[1]} for h, v in out.items()]


def top_workers(day, limit=5):
    rows = q('''SELECT w.full_name, SUM(h.kg) kg FROM harvests h JOIN workers w ON w.id=h.worker_id
                WHERE h.voided_at IS NULL AND h.work_date=? AND h.method='hand' GROUP BY w.id ORDER BY kg DESC LIMIT ?''',
             (day, limit))
    return [{'name': _short(r['full_name']), 'kg': r['kg']} for r in rows]


def photos(limit=3):
    return [{'id': r['id'], 'at': r['created_at'][11:16], 'code': r['code'] or ''} for r in
            q('''SELECT p.id, p.uploaded_at created_at, t.code FROM photos p LEFT JOIN trailer_loads tl ON tl.id=p.load_id
                 LEFT JOIN equipment t ON t.id=tl.trailer_id
                 WHERE p.voided_at IS NULL AND p.category IN ('trailer','field') ORDER BY p.id DESC LIMIT ?''', (limit,))]


def feed(limit=10):
    """The director feed without anything about money."""
    return [{'time': x['time'], 'text': x['text'], 'icon': x['icon']} for x in D.feed(limit * 2)
            if x['icon'] not in ('cash', 'edit')][:limit]


def build(year):
    day = today_str()
    d = date.fromisoformat(day)
    c_day = D.cotton(year, day, day)
    season_kg = scalar('SELECT COALESCE(SUM(kg),0) FROM harvests WHERE season_year=? AND voided_at IS NULL', (year,))
    received_today = q('''SELECT COUNT(*) n, COALESCE(SUM(nr.accepted_kg),0) kg FROM nayman_receipts nr
                          JOIN waybills wb ON wb.id=nr.waybill_id WHERE wb.status='QABUL' AND substr(nr.created_at,1,10)=?''',
                       (day,), one=True)
    target = get_float('season_target_kg', 0) or 0
    now = D.trips_now()
    fuel = D.fuel(day, day)
    fmap = fields_map(year)
    with_area = [f for f in fmap if f['ha']]
    total_ha = sum(f['ha'] for f in with_area)
    kg_on_area = sum(f['kg'] for f in with_area)
    show_workers = (get_setting('tv_show_workers') or '1') != '0'
    return {
        'generated_at': now_str(), 'date': day, 'weekday': WEEKDAYS[d.weekday()], 'season': year,
        'company': get_setting('company_name') or 'SURXON TAXIATOSH TEXTILE',
        'kpi': {'today': c_day['field_kg'], 'hand': c_day['hand_kg'], 'combine': c_day['combine_kg'],
                'season': season_kg, 'target': target or None,
                'target_pct': round(season_kg / target * 100, 1) if target else None,
                'received': received_today['kg'], 'received_n': received_today['n'],
                'diff_pct': c_day['diff_pct'], 'diff_kg': c_day['diff_kg'] if c_day['received_n'] else None,
                # kept for older screens / integrations
                'net': c_day['shipped_kg']},
        'flow': {'dalada': now['dalada'] + now['ochiq'],
                 'dalada_kg': now['dalada_kg'] + scalar('''SELECT COALESCE(SUM(h.kg),0) FROM harvests h JOIN trailer_loads tl
                                                           ON tl.id=h.load_id WHERE tl.status='OCHIQ' AND h.voided_at IS NULL'''),
                 'yolda': now['yolda'], 'yolda_kg': now['yolda_kg'], 'navbat': now['navbat'], 'navbat_kg': now['navbat_kg'],
                 'qabul': received_today['n'], 'qabul_kg': received_today['kg']},
        'trailers': trailers(), 'combines': combines(day),
        'fields': fmap, 'fields_stat': {'n': len(fmap), 'ha': round(total_ha, 1),
                                        'avg': round(kg_on_area / total_ha) if total_ha and kg_on_area else None},
        'map_center': get_setting('map_center') or '42.3167,59.6000',
        'brigades': brigades(day), 'hourly': hourly(day),
        'fuel': None if fuel is None else {'taken': fuel['taken_l'], 'given': fuel['given_l'], 'flagged': fuel['flagged']},
        'workers': top_workers(day) if show_workers else None,
        'photos': photos(), 'feed': feed(),
        'weather': {'lat': get_setting('weather_lat'), 'lon': get_setting('weather_lon'), 'place': get_setting('weather_place')},
    }
