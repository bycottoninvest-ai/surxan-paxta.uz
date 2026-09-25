"""Read-side queries for the dashboard, lists and reports.

Rule used everywhere: the *final* cotton weight is the weighbridge net
(``weighings.net_kg``). Worker kg entered in the field is an internal
allocation and is never added on top of the net.
"""
from datetime import date, timedelta

from .db import q, scalar

LIVE_HARVEST = 'h.voided_at IS NULL'


def day_kpis(year, day, brig=None):
    bfilter = ' AND h.brigadier_id=?' if brig else ''
    bp = (brig,) if brig else ()
    lf = ' AND tl.brigadier_id=?' if brig else ''

    def harvest(d):
        return q(f'''SELECT COALESCE(SUM(CASE WHEN method='hand' THEN kg END),0) hand,
                            COALESCE(SUM(CASE WHEN method='combine' THEN kg END),0) comb,
                            COALESCE(SUM(kg),0) total, COUNT(DISTINCT worker_id) workers
                     FROM harvests h WHERE season_year=? AND work_date=? AND {LIVE_HARVEST}{bfilter}''',
                 (year, d) + bp, one=True)

    def net(d):
        return scalar(f'''SELECT COALESCE(SUM(w.net_kg),0) FROM weighings w JOIN trailer_loads tl ON tl.id=w.load_id
                          WHERE tl.season_year=? AND w.status='YAKUNLANDI' AND substr(w.tare_at,1,10)=?{lf}''',
                      (year, d) + bp)

    def sent(d):
        return scalar(f'''SELECT COALESCE(SUM(wb.net_kg),0) FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                          WHERE wb.season_year=? AND wb.status<>'BEKOR' AND wb.document_date=?{lf}''', (year, d) + bp)

    def accepted(d):
        return q(f'''SELECT COALESCE(SUM(nr.accepted_kg),0) acc, COALESCE(SUM(nr.diff_kg),0) diff,
                            COALESCE(SUM(wb.net_kg),0) shipped
                     FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id
                     JOIN trailer_loads tl ON tl.id=wb.load_id
                     WHERE wb.season_year=? AND nr.received_date=? AND wb.status<>'BEKOR'{lf}''', (year, d) + bp, one=True)

    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    h, hp = harvest(day), harvest(prev)
    a, ap = accepted(day), accepted(prev)
    transit = q(f'''SELECT COUNT(*) n, COALESCE(SUM(wb.net_kg),0) kg FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                    WHERE wb.season_year=? AND wb.status='YARATILDI'{lf}''', (year,) + bp, one=True)
    trailers_total = scalar("SELECT COUNT(*) FROM equipment WHERE kind='telashka' AND active=1")
    trailers_busy = scalar("SELECT COUNT(*) FROM trailer_loads WHERE status IN ('OCHIQ','TOLDI')")
    trailers_full = scalar("SELECT COUNT(*) FROM trailer_loads WHERE status='TOLDI'")

    def trend(cur, old):
        if not old:
            return None
        return round((cur - old) / old * 100, 1)

    n, np_ = net(day), net(prev)
    s, sp = sent(day), sent(prev)
    return {
        'harvest': h['total'], 'hand': h['hand'], 'combine': h['comb'], 'workers': h['workers'],
        'harvest_trend': trend(h['total'], hp['total']),
        'net': n, 'net_trend': trend(n, np_),
        'sent': s, 'sent_trend': trend(s, sp),
        'accepted': a['acc'], 'accepted_trend': trend(a['acc'], ap['acc']),
        'diff': a['diff'], 'diff_pct': round(a['diff'] / a['shipped'] * 100, 2) if a['shipped'] else None,
        'in_transit': transit['n'], 'in_transit_kg': transit['kg'],
        'trailers_total': trailers_total, 'trailers_busy': trailers_busy, 'trailers_full': trailers_full,
    }


def active_trailers():
    """Every active trailer with its current (unfinished) load, if any."""
    return q('''SELECT e.id trailer_id, e.code trailer_code, e.plate,
                       tl.id load_id, tl.status, tl.opened_at, tl.full_at, tl.internal_kg,
                       tr.code tractor_code, f.code field_code, f.name field_name, b.name brigadier_name,
                       (SELECT COALESCE(SUM(kg),0) FROM harvests h WHERE h.load_id=tl.id AND h.voided_at IS NULL) live_kg,
                       (SELECT COUNT(DISTINCT worker_id) FROM harvests h WHERE h.load_id=tl.id AND h.voided_at IS NULL) workers,
                       (SELECT thumb_path FROM photos p WHERE p.load_id=tl.id AND p.voided_at IS NULL
                          ORDER BY p.id DESC LIMIT 1) thumb
                FROM equipment e
                LEFT JOIN trailer_loads tl ON tl.trailer_id=e.id AND tl.status IN ('OCHIQ','TOLDI')
                LEFT JOIN equipment tr ON tr.id=tl.tractor_id
                LEFT JOIN fields f ON f.id=tl.field_id
                LEFT JOIN brigadiers b ON b.id=tl.brigadier_id
                WHERE e.kind='telashka' AND e.active=1 ORDER BY e.code''')


def equipment_live(day):
    tractors = q('''SELECT e.code, e.operator_name, f.name field_name, tl.status
                    FROM equipment e
                    LEFT JOIN trailer_loads tl ON tl.tractor_id=e.id AND tl.status IN ('OCHIQ','TOLDI')
                    LEFT JOIN fields f ON f.id=tl.field_id
                    WHERE e.kind='traktor' AND e.active=1 GROUP BY e.id ORDER BY e.code''')
    combines = q('''SELECT e.code, e.operator_name,
                           (SELECT f.name FROM harvests h JOIN fields f ON f.id=h.field_id
                             WHERE h.combine_id=e.id AND h.work_date=? AND h.voided_at IS NULL ORDER BY h.id DESC LIMIT 1) field_name,
                           (SELECT COALESCE(SUM(kg),0) FROM harvests h WHERE h.combine_id=e.id AND h.work_date=?
                             AND h.voided_at IS NULL) kg
                    FROM equipment e WHERE e.kind='kombayn' AND e.active=1 ORDER BY e.code''', (day, day))
    return tractors, combines


def field_yields(year, brig=None):
    extra = ' AND f.brigadier_id=?' if brig else ''
    return q(f'''SELECT f.id, f.code, f.name, COALESCE(fs.area_ha, f.area_ha) area_ha, f.polygon_json, b.name brigadier_name,
                        COALESCE((SELECT SUM(w.net_kg) FROM weighings w JOIN trailer_loads tl ON tl.id=w.load_id
                                   WHERE tl.field_id=f.id AND tl.season_year=? AND w.status='YAKUNLANDI'),0) net_kg,
                        COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.field_id=f.id AND h.season_year=?
                                   AND h.voided_at IS NULL),0) internal_kg,
                        (SELECT COUNT(*) FROM trailer_loads tl WHERE tl.field_id=f.id AND tl.season_year=?
                                   AND tl.status IN ('OCHIQ','TOLDI')) active_loads
                 FROM fields f LEFT JOIN brigadiers b ON b.id=f.brigadier_id
                 LEFT JOIN field_seasons fs ON fs.field_id=f.id AND fs.year=?
                 WHERE (f.active=1 OR fs.field_id IS NOT NULL){extra} ORDER BY f.code''',
             (year, year, year, year) + ((brig,) if brig else ()))


def brigadier_results(year):
    return q('''SELECT b.id, b.name, b.full_name,
                       COALESCE((SELECT SUM(area_ha) FROM field_seasons fs WHERE fs.brigadier_id=b.id AND fs.year=?),
                                (SELECT SUM(area_ha) FROM fields f WHERE f.brigadier_id=b.id AND f.active=1),0) area_ha,
                       COALESCE((SELECT SUM(w.net_kg) FROM weighings w JOIN trailer_loads tl ON tl.id=w.load_id
                                  WHERE tl.brigadier_id=b.id AND tl.season_year=? AND w.status='YAKUNLANDI'),0) net_kg,
                       COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.brigadier_id=b.id AND h.season_year=?
                                  AND h.voided_at IS NULL),0) internal_kg,
                       COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.brigadier_id=b.id AND h.season_year=?
                                  AND h.method='hand' AND h.voided_at IS NULL),0) hand_kg,
                       COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.brigadier_id=b.id AND h.season_year=?
                                  AND h.method='combine' AND h.voided_at IS NULL),0) combine_kg
                FROM brigadiers b WHERE b.active=1 ORDER BY net_kg DESC, b.name''', (year,) * 5)


def harvest_series(year, mode='daily', end_day=None, brig=None):
    """Chart data: hand/combine (field kg) + net (weighbridge) per bucket."""
    bf = ' AND brigadier_id=?' if brig else ''
    bp = (brig,) if brig else ()
    lf = ' AND tl.brigadier_id=?' if brig else ''
    if mode == 'hourly':
        rows = q(f'''SELECT substr(created_at,12,2) k, SUM(CASE WHEN method='hand' THEN kg ELSE 0 END) hand,
                            SUM(CASE WHEN method='combine' THEN kg ELSE 0 END) comb
                     FROM harvests WHERE season_year=? AND work_date=? AND voided_at IS NULL{bf} GROUP BY k ORDER BY k''',
                 (year, end_day) + bp)
        by = {r['k']: r for r in rows}
        labels = [f'{h:02d}' for h in range(6, 21)]
        hand, comb, cum_h, cum_c = [], [], 0, 0
        for h in labels:
            r = by.get(h)
            cum_h += (r['hand'] if r else 0)
            cum_c += (r['comb'] if r else 0)
            hand.append(round(cum_h))
            comb.append(round(cum_c))
        return {'labels': [f'{h}:00' for h in labels], 'hand': hand, 'combine': comb, 'net': []}
    if mode == 'weekly' or mode == 'season':
        key = "strftime('%Y-W%W', work_date)"
        nkey = "strftime('%Y-W%W', substr(w.tare_at,1,10))"
    else:
        key, nkey = 'work_date', 'substr(w.tare_at,1,10)'
    rows = q(f'''SELECT {key} k, SUM(CASE WHEN method='hand' THEN kg ELSE 0 END) hand,
                        SUM(CASE WHEN method='combine' THEN kg ELSE 0 END) comb
                 FROM harvests WHERE season_year=? AND voided_at IS NULL{bf} GROUP BY k ORDER BY k''', (year,) + bp)
    nets = {r['k']: r['net'] for r in q(f'''SELECT {nkey} k, SUM(w.net_kg) net FROM weighings w
                                            JOIN trailer_loads tl ON tl.id=w.load_id
                                            WHERE tl.season_year=? AND w.status='YAKUNLANDI'{lf} GROUP BY k''', (year,) + bp)}
    data = {r['k']: r for r in rows}
    if mode == 'daily':
        end = date.fromisoformat(end_day)
        keys = [(end - timedelta(days=i)).isoformat() for i in range(7, -1, -1)]
        labels = [k[8:10] + '.' + k[5:7] for k in keys]
    else:
        keys = sorted(set(data) | set(nets))
        if mode == 'weekly':
            keys = keys[-8:]
        labels = [k.split('-W')[1] + '-hafta' for k in keys]
    return {
        'labels': labels,
        'hand': [round(data[k]['hand']) if k in data else 0 for k in keys],
        'combine': [round(data[k]['comb']) if k in data else 0 for k in keys],
        'net': [round(nets.get(k) or 0) for k in keys],
    }


def top_workers(year, day, limit=10, brig=None):
    bf = ' AND h.brigadier_id=?' if brig else ''
    return q(f'''SELECT w.id, w.full_name, b.name brigadier_name, f.code field_code, f.name field_name,
                        SUM(h.kg) kg, COUNT(*) n
                 FROM harvests h JOIN workers w ON w.id=h.worker_id
                 LEFT JOIN brigadiers b ON b.id=h.brigadier_id LEFT JOIN fields f ON f.id=h.field_id
                 WHERE h.season_year=? AND h.work_date=? AND h.method='hand' AND h.voided_at IS NULL{bf}
                 GROUP BY w.id ORDER BY kg DESC LIMIT ?''', (year, day) + ((brig,) if brig else ()) + (limit,))


WAYBILL_SELECT = '''SELECT wb.*, tl.trip_no, tl.station_id, st.name station_name, tl.full_at sent_at,
                           tl.load_date, tl.vehicle_plate, tl.driver_name, tl.internal_kg, tl.hand_kg,
                           tl.combine_kg, tl.field_id, tl.brigadier_id, t.code trailer_code, tr.code tractor_code,
                           f.code field_code, f.name field_name, b.name brigadier_name,
                           w.gross_kg, w.tare_kg, w.gross_at, w.tare_at, w.basis, w.diff_kg weigh_diff_kg, w.diff_reason weigh_diff_reason,
                           w.scale_no, nr.id receipt_id, nr.accepted_kg, nr.diff_kg nayman_diff_kg, nr.diff_reason nayman_diff_reason,
                           nr.received_date, nr.amount receipt_amount,
                           (SELECT COALESCE(SUM(amount),0) FROM payments p WHERE p.waybill_id=wb.id AND p.voided_at IS NULL) paid,
                           nr.created_at received_at, nr.receiver_name,
                           (SELECT COUNT(DISTINCT h.worker_id) FROM harvests h WHERE h.load_id=tl.id AND h.voided_at IS NULL
                                   AND h.method='hand') workers_count,
                           u.full_name created_name
                    FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                    LEFT JOIN stations st ON st.id=tl.station_id
                    JOIN equipment t ON t.id=tl.trailer_id
                    LEFT JOIN equipment tr ON tr.id=tl.tractor_id
                    LEFT JOIN fields f ON f.id=tl.field_id
                    LEFT JOIN brigadiers b ON b.id=tl.brigadier_id
                    LEFT JOIN weighings w ON w.load_id=tl.id
                    LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                    LEFT JOIN users u ON u.id=wb.created_by'''


def waybills(year, *, day=None, since=None, search='', status='', brig=None, limit=500, offset=0):
    where, params = ['wb.season_year=?'], [year]
    if day:
        where.append('wb.document_date=?'); params.append(day)
    if since:
        where.append('wb.document_date>=?'); params.append(since)
    if status == 'pending':
        where.append("wb.status='YARATILDI'")
    elif status in ('YARATILDI', 'QABUL', 'BEKOR'):
        where.append('wb.status=?'); params.append(status)
    if brig:
        where.append('tl.brigadier_id=?'); params.append(brig)
    if search:
        where.append('(wb.number LIKE ? OR t.code LIKE ? OR tr.code LIKE ? OR f.name LIKE ? OR b.name LIKE ? '
                     'OR tl.vehicle_plate LIKE ? OR tl.trip_no LIKE ?)')
        params += [f'%{search}%'] * 7
    return q(WAYBILL_SELECT + ' WHERE ' + ' AND '.join(where) + ' ORDER BY wb.seq DESC LIMIT ? OFFSET ?', params + [limit, offset])


def waybill(wid):
    return q(WAYBILL_SELECT + ' WHERE wb.id=?', (wid,), one=True)


def trip_state(wb):
    """Display status of a sent trip: YOLDA → KELDI → QABUL (or BEKOR)."""
    if wb['status'] == 'BEKOR':
        return 'BEKOR'
    if wb['status'] == 'QABUL':
        return 'QABUL'
    return 'KELDI' if wb['arrived_at'] else 'YOLDA'


def station_trips(station_id=None, *, state='open', day=None, search='', limit=200):
    """Trips for the punkt screen. state: open (on the way + arrived), received (QABUL), all."""
    where, params = ["wb.status<>'BEKOR'"], []
    if station_id:
        where.append('tl.station_id=?'); params.append(station_id)
    if state == 'open':
        where.append("wb.status='YARATILDI'")
    elif state == 'received':
        where.append("wb.status='QABUL'")
    if day:
        where.append('substr(nr.created_at,1,10)=?'); params.append(day)
    if search:
        where.append('(tl.trip_no LIKE ? OR wb.number LIKE ?)'); params += [f'%{search}%'] * 2
    order = 'nr.created_at DESC' if state == 'received' else 'COALESCE(wb.arrived_at, tl.full_at) DESC'
    return q(WAYBILL_SELECT + ' WHERE ' + ' AND '.join(where) + f' ORDER BY {order} LIMIT ?', params + [limit])


def station_counts(station_id, day):
    sf, params = ('AND tl.station_id=?', [station_id]) if station_id else ('', [])
    row = q(f'''SELECT SUM(CASE WHEN wb.status='YARATILDI' AND wb.arrived_at IS NULL THEN 1 ELSE 0 END) yolda,
                       SUM(CASE WHEN wb.status='YARATILDI' AND wb.arrived_at IS NOT NULL THEN 1 ELSE 0 END) keldi,
                       SUM(CASE WHEN wb.status='QABUL' AND substr(nr.created_at,1,10)=? THEN 1 ELSE 0 END) qabul,
                       COALESCE(SUM(CASE WHEN wb.status='QABUL' AND substr(nr.created_at,1,10)=? THEN nr.accepted_kg END),0) qabul_kg,
                       COALESCE(SUM(CASE WHEN wb.status='QABUL' AND substr(nr.created_at,1,10)=? THEN wb.net_kg END),0) dala_kg
                FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                WHERE wb.status<>'BEKOR' {sf}''', [day, day, day] + params, one=True)
    return {k: (row[k] or 0) for k in row.keys()}


def find_trip(text, station_id=None, year=None):
    """QR payload / full trip number / just the digits → waybill id (the latest match)."""
    import re
    text = (text or '').strip().upper()
    m = re.search(r'TL-(\d{4})-(\d{1,6})', text)
    if m:
        trip = f'TL-{m.group(1)}-{int(m.group(2)):06d}'
    elif text.isdigit() and year:
        trip = f'TL-{year}-{int(text):06d}'
    else:
        m = re.search(r'PA-\d{6}', text)
        if not m:
            return None
        row = q("SELECT wb.id FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id WHERE wb.number=?"
                + (' AND tl.station_id=?' if station_id else ''), [m.group(0)] + ([station_id] if station_id else []), one=True)
        return row['id'] if row else None
    row = q("SELECT wb.id FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id WHERE tl.trip_no=? AND wb.status<>'BEKOR'"
            + (' AND tl.station_id=?' if station_id else '') + ' ORDER BY wb.id DESC',
            [trip] + ([station_id] if station_id else []), one=True)
    return row['id'] if row else None


LOAD_SELECT = '''SELECT tl.*, t.code trailer_code, tr.code tractor_code, f.code field_code, f.name field_name,
                        b.name brigadier_name, w.gross_kg, w.tare_kg, w.net_kg, w.basis, w.status weigh_status, w.gross_at, w.tare_at,
                        w.diff_kg, w.diff_reason, wb.id waybill_id, wb.number waybill_number, wb.status waybill_status,
                        uo.full_name opened_name, uf.full_name full_name_by, st.name station_name,
                        wb.arrived_at, nr.accepted_kg station_kg, nr.diff_kg station_diff_kg, nr.diff_reason station_diff_reason,
                        nr.created_at received_at,
                        (SELECT COALESCE(SUM(kg),0) FROM harvests h WHERE h.load_id=tl.id AND h.voided_at IS NULL) live_kg,
                        (SELECT COUNT(*) FROM photos p WHERE p.load_id=tl.id AND p.voided_at IS NULL) photo_count
                 FROM trailer_loads tl JOIN equipment t ON t.id=tl.trailer_id
                 LEFT JOIN equipment tr ON tr.id=tl.tractor_id
                 LEFT JOIN fields f ON f.id=tl.field_id
                 LEFT JOIN brigadiers b ON b.id=tl.brigadier_id
                 LEFT JOIN weighings w ON w.load_id=tl.id
                 LEFT JOIN waybills wb ON wb.load_id=tl.id
                 LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                 LEFT JOIN stations st ON st.id=tl.station_id
                 LEFT JOIN users uo ON uo.id=tl.opened_by
                 LEFT JOIN users uf ON uf.id=tl.full_by'''


def loads(year, *, statuses=None, brig=None, day=None, limit=300, offset=0):
    where, params = ['tl.season_year=?'], [year]
    if statuses:
        where.append('tl.status IN (%s)' % ','.join('?' * len(statuses))); params += list(statuses)
    if brig:
        where.append('tl.brigadier_id=?'); params.append(brig)
    if day:
        where.append('tl.load_date=?'); params.append(day)
    return q(LOAD_SELECT + ' WHERE ' + ' AND '.join(where) + ' ORDER BY tl.id DESC LIMIT ? OFFSET ?', params + [limit, offset])


def load(load_id):
    return q(LOAD_SELECT + ' WHERE tl.id=?', (load_id,), one=True)


def load_harvests(load_id, include_void=False):
    extra = '' if include_void else ' AND h.voided_at IS NULL'
    return q(f'''SELECT h.*, w.full_name worker_name, c.code combine_code, u.full_name entered_name
                 FROM harvests h LEFT JOIN workers w ON w.id=h.worker_id
                 LEFT JOIN equipment c ON c.id=h.combine_id LEFT JOIN users u ON u.id=h.entered_by
                 WHERE h.load_id=?{extra} ORDER BY h.id DESC''', (load_id,))


def photos_for(*, load_id=None, entity_type=None, entity_id=None, waybill_id=None):
    if load_id:
        return q('SELECT * FROM photos WHERE load_id=? AND voided_at IS NULL ORDER BY id', (load_id,))
    if waybill_id:
        return q('SELECT * FROM photos WHERE waybill_id=? AND voided_at IS NULL ORDER BY id', (waybill_id,))
    return q('SELECT * FROM photos WHERE entity_type=? AND entity_id=? AND voided_at IS NULL ORDER BY id',
             (entity_type, entity_id))


def history(entity_type, entity_id):
    return q('''SELECT a.*, u.full_name FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id
                WHERE a.entity_type=? AND a.entity_id=? ORDER BY a.id''', (entity_type, str(entity_id)))


def load_timeline(load_id):
    """Audit events for the load, its weighing, waybill and Nayman receipt (the full chain)."""
    ids = q('''SELECT w.id wid, wb.id wbid, nr.id nrid FROM trailer_loads tl LEFT JOIN weighings w ON w.load_id=tl.id
               LEFT JOIN waybills wb ON wb.load_id=tl.id LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
               WHERE tl.id=?''', (load_id,), one=True)
    conds, params = ["(a.entity_type='trailer_load' AND a.entity_id=?)"], [str(load_id)]
    for et, key in (('weighing', 'wid'), ('waybill', 'wbid'), ('nayman_receipt', 'nrid')):
        if ids and ids[key]:
            conds.append('(a.entity_type=? AND a.entity_id=?)'); params += [et, str(ids[key])]
    return q(f'''SELECT a.*, u.full_name FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id
                 WHERE {' OR '.join(conds)} ORDER BY a.id''', params)


EVENT_TEXT = {
    ('TOLDI', 'trailer_load'): 'to‘ldi',
    ('OPEN', 'trailer_load'): 'yangi yuk ochildi',
    ('GROSS', 'weighing'): 'brutto tortildi',
    ('TARE', 'weighing'): 'tara tortildi',
    ('CREATE', 'waybill'): 'nakladnoy yaratildi',
    ('CREATE', 'nayman_receipt'): 'Nayman qabul qildi',
    ('FIELD_SUM', 'weighing'): 'tugatildi — punktga yo‘lda',
    ('ARRIVED', 'waybill'): 'punktga keldi',
    ('RECEIVE', 'waybill'): 'punktda qabul qilindi',
    ('CREATE', 'payment'): 'to‘lov kiritildi',
    ('CREATE', 'expense'): 'xarajat kiritildi',
}


def recent_events(limit=8):
    rows = q('''SELECT a.*, u.full_name FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id
                WHERE (a.action, a.entity_type) IN (VALUES ('TOLDI','trailer_load'),('OPEN','trailer_load'),
                      ('TARE','weighing'),('GROSS','weighing'),('CREATE','waybill'),('CREATE','nayman_receipt'),
                      ('CREATE','payment'))
                ORDER BY a.id DESC LIMIT ?''', (limit,))
    import json
    out = []
    for r in rows:
        new = json.loads(r['new_json'] or '{}')
        title, sub, icon, link = '', '', 'truck', None
        if r['entity_type'] == 'trailer_load':
            ld = load(int(r['entity_id']))
            if not ld:
                continue
            title = f"{ld['trailer_code']} {EVENT_TEXT.get((r['action'], r['entity_type']), '')}"
            kg = new.get('internal_kg')
            sub = ' · '.join(x for x in [f"{kg:,.0f} kg".replace(',', ' ') if kg else '', ld['field_name'],
                                          ld['tractor_code'] or ''] if x)
            icon = 'trailer' if r['action'] == 'TOLDI' else 'plus'
            link = ('ops.load_detail', {'load_id': ld['id']})
        elif r['entity_type'] == 'weighing':
            w = q('SELECT w.*, t.code trailer_code, tl.id lid, f.name field_name FROM weighings w '
                  'JOIN trailer_loads tl ON tl.id=w.load_id JOIN equipment t ON t.id=tl.trailer_id '
                  'LEFT JOIN fields f ON f.id=tl.field_id WHERE w.id=?', (int(r['entity_id']),), one=True)
            if not w:
                continue
            title = f"{w['trailer_code']} {EVENT_TEXT[(r['action'], 'weighing')]}"
            kg = new.get('net_kg') or new.get('gross_kg')
            sub = f"{kg:,.0f} kg".replace(',', ' ') + (f" · {w['field_name']}" if w['field_name'] else '')
            icon, link = 'scale', ('ops.load_detail', {'load_id': w['lid']})
        elif r['entity_type'] == 'waybill':
            title = f"{new.get('number')} {EVENT_TEXT[('CREATE', 'waybill')]}"
            sub = f"{new.get('net_kg', 0):,.0f} kg".replace(',', ' ')
            icon, link = 'doc', ('ops.waybill_detail', {'waybill_id': int(r['entity_id'])})
        elif r['entity_type'] == 'nayman_receipt':
            title = EVENT_TEXT[('CREATE', 'nayman_receipt')]
            sub = f"{new.get('accepted_kg', 0):,.0f} kg · {new.get('waybill', '')}".replace(',', ' ')
            icon = 'factory'
        elif r['entity_type'] == 'payment':
            title = EVENT_TEXT[('CREATE', 'payment')]
            sub = f"{new.get('amount', 0):,} so‘m".replace(',', ' ')
            icon = 'wallet'
        out.append({'title': title, 'sub': sub, 'time': r['created_at'][11:16], 'date': r['created_at'][:10],
                    'icon': icon, 'link': link, 'who': r['full_name']})
    return out


def season_comparison():
    return q('''SELECT s.year, s.status,
                       COALESCE((SELECT SUM(area_ha) FROM field_seasons fs WHERE fs.year=s.year),
                                (SELECT SUM(area_ha) FROM fields WHERE active=1),0) area_now,
                       COALESCE((SELECT SUM(w.net_kg) FROM weighings w JOIN trailer_loads tl ON tl.id=w.load_id
                                  WHERE tl.season_year=s.year AND w.status='YAKUNLANDI'),0) net_kg,
                       COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.season_year=s.year AND h.method='hand' AND h.voided_at IS NULL),0) hand_kg,
                       COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.season_year=s.year AND h.method='combine' AND h.voided_at IS NULL),0) combine_kg,
                       COALESCE((SELECT SUM(net_kg) FROM waybills wb WHERE wb.season_year=s.year AND wb.status<>'BEKOR'),0) shipped_kg,
                       COALESCE((SELECT SUM(nr.accepted_kg) FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id
                                  WHERE wb.season_year=s.year AND wb.status<>'BEKOR'),0) accepted_kg,
                       COALESCE((SELECT SUM(amount) FROM expenses e WHERE e.season_year=s.year AND e.voided_at IS NULL),0) expenses,
                       COALESCE((SELECT SUM(amount) FROM payments p WHERE p.season_year=s.year AND p.voided_at IS NULL),0) received,
                       (SELECT COUNT(*) FROM waybills wb WHERE wb.season_year=s.year AND wb.status<>'BEKOR') waybill_count,
                       (SELECT COUNT(*) FROM photos p WHERE p.season_year=s.year AND p.voided_at IS NULL) photo_count
                FROM seasons s ORDER BY s.year DESC''')


def notifications(user, year):
    """Items that need someone's attention, for the bell icon."""
    from .security import has_perm
    items = []
    if has_perm(user, 'weigh.write'):
        n = scalar("SELECT COUNT(*) FROM trailer_loads WHERE status='TOLDI'")
        if n:
            items.append({'text': f'{n} ta telashka tarozini kutmoqda', 'endpoint': 'ops.scale_queue'})
        n = scalar("SELECT COUNT(*) FROM weighings WHERE status='BRUTTO'")
        if n:
            items.append({'text': f'{n} ta tortishda tara kiritilmagan', 'endpoint': 'ops.scale_queue'})
    if user['role'] == 'station':
        n = scalar("SELECT COUNT(*) FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id "
                   "WHERE wb.status='YARATILDI' AND tl.station_id=?", (user['station_id'],))
        if n:
            items.append({'text': f'{n} ta yuk punktga yo‘lda / keldi', 'endpoint': 'punkt.home'})
        return items
    if has_perm(user, 'nayman.write'):
        n = scalar("SELECT COUNT(*) FROM waybills WHERE status='YARATILDI' AND season_year=?", (year,))
        if n:
            items.append({'text': f'{n} ta nakladnoy Nayman qabulini kutmoqda', 'endpoint': 'ops.nayman_list'})
    return items


def finance_summary(year):
    shipped = q('''SELECT COALESCE(SUM(wb.net_kg),0) kg, COUNT(*) n FROM waybills wb
                   WHERE wb.season_year=? AND wb.status<>'BEKOR' ''', (year,), one=True)
    acc = q('''SELECT COALESCE(SUM(nr.accepted_kg),0) kg, COALESCE(SUM(nr.amount),0) amount,
                      SUM(CASE WHEN nr.amount IS NULL THEN 1 ELSE 0 END) unpriced
               FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id
               WHERE wb.season_year=? AND wb.status<>'BEKOR' ''', (year,), one=True)
    received = scalar('SELECT COALESCE(SUM(amount),0) FROM payments WHERE season_year=? AND voided_at IS NULL', (year,))
    expenses = scalar('SELECT COALESCE(SUM(amount),0) FROM expenses WHERE season_year=? AND voided_at IS NULL', (year,))
    cash = q('''SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount END),0) inflow,
                       COALESCE(SUM(CASE WHEN direction='OUT' THEN amount END),0) outflow,
                       COALESCE(SUM(CASE WHEN category='opening' THEN amount END),0) opening
                FROM cash_entries WHERE season_year=? AND voided_at IS NULL''', (year,), one=True)
    return {
        'shipped_kg': shipped['kg'], 'waybills': shipped['n'], 'accepted_kg': acc['kg'],
        'receivable': acc['amount'], 'unpriced': acc['unpriced'] or 0, 'received': received,
        'debt': (acc['amount'] or 0) - received if acc['amount'] else None,
        'expenses': expenses, 'cash_in': cash['inflow'], 'cash_out': cash['outflow'], 'cash_opening': cash['opening'],
        'cash_balance': cash['inflow'] - cash['outflow'],
    }


def worker_settlements(year, rate):
    return q('''SELECT w.id, w.full_name, w.phone, b.name brigadier_name,
                       COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.worker_id=w.id AND h.season_year=? AND h.method='hand'
                                  AND h.voided_at IS NULL),0) kg,
                       COALESCE((SELECT COUNT(DISTINCT work_date) FROM harvests h WHERE h.worker_id=w.id AND h.season_year=?
                                  AND h.voided_at IS NULL),0) days,
                       COALESCE((SELECT SUM(amount) FROM cash_entries c WHERE c.worker_id=w.id AND c.season_year=?
                                  AND c.category='worker_pay' AND c.voided_at IS NULL),0) paid,
                       COALESCE((SELECT SUM(amount) FROM cash_entries c WHERE c.worker_id=w.id AND c.season_year=?
                                  AND c.category='advance' AND c.voided_at IS NULL),0) advances
                FROM workers w LEFT JOIN brigadiers b ON b.id=w.brigadier_id
                WHERE EXISTS (SELECT 1 FROM harvests h WHERE h.worker_id=w.id AND h.season_year=?)
                   OR EXISTS (SELECT 1 FROM cash_entries c WHERE c.worker_id=w.id AND c.season_year=?)
                ORDER BY kg DESC''', (year,) * 6)


def search(term, limit=8):
    like = f'%{term}%'
    out = []
    for r in q('SELECT id, number, net_kg FROM waybills WHERE number LIKE ? ORDER BY seq DESC LIMIT ?', (like, limit)):
        out.append({'type': 'Nakladnoy', 'label': r['number'], 'sub': f"{r['net_kg']:,.0f} kg".replace(',', ' '),
                    'endpoint': 'ops.waybill_detail', 'args': {'waybill_id': r['id']}})
    for r in q('''SELECT tl.id, t.code, tl.status, tl.load_date FROM trailer_loads tl JOIN equipment t ON t.id=tl.trailer_id
                  WHERE t.code LIKE ? OR tl.vehicle_plate LIKE ? ORDER BY tl.id DESC LIMIT ?''', (like, like, limit)):
        out.append({'type': 'Telashka', 'label': f"{r['code']} · yuk №{r['id']}", 'sub': f"{r['status']} · {r['load_date']}",
                    'endpoint': 'ops.load_detail', 'args': {'load_id': r['id']}})
    for r in q('SELECT id, full_name, phone FROM workers WHERE full_name LIKE ? ORDER BY full_name LIMIT ?', (like, limit)):
        out.append({'type': 'Ishchi', 'label': r['full_name'], 'sub': r['phone'] or '',
                    'endpoint': 'people.worker_detail', 'args': {'worker_id': r['id']}})
    for r in q('SELECT id, code, name, area_ha FROM fields WHERE code LIKE ? OR name LIKE ? LIMIT ?', (like, like, limit)):
        out.append({'type': 'Dala', 'label': f"{r['code']} · {r['name']}", 'sub': f"{r['area_ha']:g} ga",
                    'endpoint': 'admin.field_detail', 'args': {'field_id': r['id']}})
    for r in q("SELECT id, code, kind FROM equipment WHERE code LIKE ? OR plate LIKE ? LIMIT ?", (like, like, limit)):
        out.append({'type': 'Texnika', 'label': r['code'], 'sub': r['kind'], 'endpoint': 'admin.equipment', 'args': {}})
    return out[:20]
