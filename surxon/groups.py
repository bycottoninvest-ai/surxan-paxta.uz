"""Umumiy yuk (UY): one tractor tows several trailers — each with its own field blank (PB) — and the punkt weighs them
ONCE on one paper. Our man at the punkt scans the UY paper's QR, then every trailer's blank: the list and the total
field kg build themselves. He writes only the count and the total on the UY paper, it goes in; after the weighing he
scans the UY again and types the punkt's brutto / tara (and the scale's load number). The punkt netto is then shared out
over the trailers by their field kg, so every field, combine (ours or a hired one) and hand brigade keeps its own
account — and one PQ-17 from the cluster ties to the whole group.
"""
import secrets

from .db import q, tx
from .security import audit
from .utils import UserError, clean_text, now_str, today_str

MAX_BATCH = 1000


def _need(actor, perm):
    if not actor or not actor.can(perm):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')


FIRST_NUMBER = 8          # UY-0001 … UY-0007 were already used on paper before the system — never reuse them


def next_number():
    """The number the next UY paper gets: after the highest one printed, never below FIRST_NUMBER."""
    top = q("SELECT MAX(CAST(substr(number, 4) AS INTEGER)) m FROM load_groups", one=True)['m']
    return max((top or 0) + 1, FIRST_NUMBER)


def create_batch(actor, count, start=None):
    """Pre-printed UY papers numbered from `start` (default: after the last one, from UY-0008). Returns (batch, first, last)."""
    _need(actor, 'masterdata.write')
    try:
        count = int(str(count).replace(' ', ''))
    except ValueError:
        raise UserError('Nechta varaq kerakligini raqam bilan yozing (masalan 200).')
    if not 1 <= count <= MAX_BATCH:
        raise UserError(f'Bir martada 1 dan {MAX_BATCH} tagacha varaq chiqarish mumkin.')
    nxt = next_number()
    if start in (None, ''):
        start = nxt
    else:
        try:
            start = int(''.join(ch for ch in str(start) if ch.isdigit()))
        except ValueError:
            raise UserError('Birinchi raqamni son bilan yozing (masalan 8).')
        if start < 1 or start + count - 1 > 9999:
            raise UserError('Raqam 1 dan 9999 gacha bo‘lishi kerak.')
    with tx() as db:
        busy = db.execute("SELECT number FROM load_groups WHERE CAST(substr(number, 4) AS INTEGER) BETWEEN ? AND ? ORDER BY number LIMIT 1",
                          (start, start + count - 1)).fetchone()
        if busy:
            raise UserError(f'{busy["number"]} allaqachon bor — boshqa raqamdan boshlang (keyingi bo‘sh: UY-{nxt:04d}).')
        batch, now = (db.execute('SELECT MAX(batch) b FROM load_groups').fetchone()['b'] or 0) + 1, now_str()
        for n in range(start, start + count):
            db.execute('INSERT INTO load_groups(number, token, batch, created_by, created_at) VALUES (?,?,?,?,?)',
                       (f'UY-{n:04d}', secrets.token_urlsafe(9), batch, actor.user_id, now))
        audit(db, actor, 'CREATE', 'load_groups', batch, new={'count': count, 'from': f'UY-{start:04d}', 'to': f'UY-{start + count - 1:04d}'})
    return batch, f'UY-{start:04d}', f'UY-{start + count - 1:04d}'


def find(code):
    """A UY paper by the token in its QR, a whole QR link, or its number (UY-0012 / 12)."""
    code = (code or '').strip()
    if '/uy/' in code:
        code = code.rsplit('/uy/', 1)[1].split('?')[0].strip('/')
    g = q('SELECT * FROM load_groups WHERE token=?', (code,), one=True)
    if g:
        return g
    digits = ''.join(ch for ch in code if ch.isdigit())
    if digits and (code.upper().startswith('UY') or code.isdigit()):
        return q('SELECT * FROM load_groups WHERE number=?', (f'UY-{int(digits):04d}',), one=True)
    return None


def get(gid):
    g = q('SELECT * FROM load_groups WHERE id=?', (gid,), one=True)
    if not g:
        raise UserError('Umumiy yuk topilmadi.')
    return g


def _check_station(actor, g):
    if actor.role == 'station' and g['station_id'] and g['station_id'] != actor.station_id:
        raise UserError(f'{g["number"]} boshqa punktda ochilgan.')


def open_group(actor, code):
    """The UY paper's first scan opens it at this punkt; later scans just return it."""
    _need(actor, 'station.receive')
    g = find(code)
    if not g:
        raise UserError('Bunday umumiy nakladnoy (UY) topilmadi — QR ni qayta skanerlang.')
    if g['spoiled_reason'] or g['status'] == 'BEKOR':
        raise UserError(f'{g["number"]} bekor qilingan — boshqa varaq oling.')
    _check_station(actor, g)
    if g['status'] == 'BOSH':
        with tx() as db:
            db.execute("UPDATE load_groups SET status='OCHIQ', station_id=?, opened_by=?, opened_at=? WHERE id=? AND status='BOSH'",
                       (actor.station_id if actor.role == 'station' else None, actor.user_id, now_str(), g['id']))
            audit(db, actor, 'OPEN', 'load_group', g['id'], new={'number': g['number']})
        g = get(g['id'])
    return g


def _trip(db, waybill_id):
    return db.execute('''SELECT wb.*, tl.station_id, tl.trip_no, tl.field_id, tl.brigadier_id, tl.season_year sy, tl.method,
                                nr.id receipt_id, i.group_id
                         FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                         LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id LEFT JOIN load_group_items i ON i.waybill_id=wb.id
                         WHERE wb.id=?''', (waybill_id,)).fetchone()


def _add(db, actor, g, waybill_id, blank=None):
    wb = _trip(db, waybill_id)
    if not wb or wb['status'] == 'BEKOR':
        raise UserError('Reys topilmadi yoki bekor qilingan.')
    who = f'{blank} ({wb["trip_no"]})' if blank else wb['trip_no']
    if wb['group_id'] == g['id']:
        raise UserError(f'{who} allaqachon shu ro‘yxatda — ikki marta qo‘shilmaydi.')
    if wb['group_id']:
        other = db.execute('SELECT number FROM load_groups WHERE id=?', (wb['group_id'],)).fetchone()
        raise UserError(f'{who} boshqa umumiy yukda ({other["number"]}) — bitta telashka faqat bitta yukka.')
    if wb['receipt_id'] or wb['status'] == 'QABUL':
        raise UserError(f'{wb["trip_no"]} punktda allaqachon qabul qilingan.')
    if actor.role == 'station' and wb['station_id'] != actor.station_id:
        raise UserError(f'{wb["trip_no"]} boshqa punktga jo‘natilgan telashka.')
    if g['station_id'] and wb['station_id'] != g['station_id']:
        raise UserError(f'{wb["trip_no"]} boshqa punktga jo‘natilgan telashka.')
    db.execute('INSERT INTO load_group_items(group_id, waybill_id, added_by, added_at) VALUES (?,?,?,?)',
               (g['id'], waybill_id, actor.user_id, now_str()))
    audit(db, actor, 'ADD', 'load_group', g['id'], new={'waybill_id': waybill_id, 'trip_no': wb['trip_no']})
    return wb


def add_blank(actor, gid, code, waybill_id=None):
    """A trailer's field blank scanned into the list. A blank the clerk did not scan in the field needs its trailer
    picked once (waybill_id) — it is tied to it here. Returns the trip number."""
    _need(actor, 'station.receive')
    from . import blanks as BL
    g = get(gid)
    _check_station(actor, g)
    if g['status'] != 'OCHIQ':
        raise UserError(f'{g["number"]} ro‘yxati yopilgan — qo‘shib bo‘lmaydi.')
    b = BL.find(code)
    if not b:
        raise UserError('Bunday blank topilmadi — QR ni qayta skanerlang.')
    if b['spoiled_reason']:
        raise UserError(f'{b["number"]} buzilgan deb belgilangan.')
    wid = b['waybill_id']
    if not wid:
        if not waybill_id:
            raise UserError(f'{b["number"]} dalada telashkaga biriktirilmagan — pastdagi ro‘yxatdan telashkasini tanlang.')
        BL.attach(actor, waybill_id, b['number'], photo_later=True)
        wid = waybill_id
    with tx() as db:
        return _add(db, actor, g, wid, b['number'])['trip_no']


def add_trip(actor, gid, waybill_id):
    """A trailer without a blank, picked from the list of trips on the way."""
    _need(actor, 'station.receive')
    g = get(gid)
    _check_station(actor, g)
    if g['status'] != 'OCHIQ':
        raise UserError(f'{g["number"]} ro‘yxati yopilgan — qo‘shib bo‘lmaydi.')
    with tx() as db:
        return _add(db, actor, g, waybill_id)['trip_no']


def remove(actor, gid, waybill_id):
    _need(actor, 'station.receive')
    g = get(gid)
    _check_station(actor, g)
    if g['status'] != 'OCHIQ':
        raise UserError('Ro‘yxat yopilgan — avval “Qayta ochish”.')
    with tx() as db:
        db.execute('DELETE FROM load_group_items WHERE group_id=? AND waybill_id=?', (gid, waybill_id))
        audit(db, actor, 'REMOVE', 'load_group', gid, new={'waybill_id': waybill_id})


def close(actor, gid):
    """“Hammasi shu”: the list is final, the paper gets its count and total, the load goes in."""
    _need(actor, 'station.receive')
    g = get(gid)
    _check_station(actor, g)
    items = detail(gid)['items']
    if not items:
        raise UserError('Ro‘yxat bo‘sh — kamida bitta telashkani skanerlang.')
    if g['status'] != 'OCHIQ':
        return g
    total = round(sum(i['net_kg'] for i in items), 1)
    with tx() as db:
        db.execute("UPDATE load_groups SET status='YUBORILDI', sent_at=?, sent_kg=? WHERE id=? AND status='OCHIQ'", (now_str(), total, gid))
        audit(db, actor, 'CLOSE', 'load_group', gid, new={'trailers': len(items), 'sent_kg': total})
    return get(gid)


def reopen(actor, gid):
    _need(actor, 'station.receive')
    g = get(gid)
    _check_station(actor, g)
    if g['status'] != 'YUBORILDI':
        raise UserError('Faqat hali qabul qilinmagan ro‘yxatni qayta ochish mumkin.')
    with tx() as db:
        db.execute("UPDATE load_groups SET status='OCHIQ' WHERE id=?", (gid,))
        audit(db, actor, 'REOPEN', 'load_group', gid)


def split(total, weights):
    """The punkt netto shared by the field kg: 0.1 kg steps, the last trailer takes the rounding rest (sum is exact)."""
    s = sum(weights)
    parts = [round(total * w / s, 1) if s else 0 for w in weights[:-1]]
    return parts + [round(total - sum(parts), 1)]


def receive(actor, gid, *, gross_kg=None, tare_kg=None, station_kg=None, load_no='', reason='', note='', photo=None):
    """The punkt's one weighing of the whole load → every trailer QABUL QILINDI with its share, the difference reason
    and the load number on each; the UY paper's photo (stamped) is kept on the group. A second tap changes nothing."""
    _need(actor, 'station.receive')
    from .services import OTHER_REASON, STATION_DIFF_REASONS, _sheet_waybill, diff_level
    from .settings import get_float
    g = get(gid)
    _check_station(actor, g)
    if g['status'] == 'QABUL':
        return {'already': True, 'group': g}
    if g['status'] != 'YUBORILDI':
        raise UserError('Avval ro‘yxatni yoping (“Hammasi shu”).')
    if gross_kg is not None or tare_kg is not None:
        if gross_kg is None or tare_kg is None:
            raise UserError('Brutto va tarani ikkalasini ham kiriting (yoki tayyor nettoni).')
        if gross_kg <= tare_kg:
            raise UserError(f'Brutto ({gross_kg:g} kg) taradan ({tare_kg:g} kg) katta bo‘lishi kerak.')
        station_kg = round(gross_kg - tare_kg, 1)
    items = detail(gid)['items']
    sent = round(sum(i['net_kg'] for i in items), 1)
    if not station_kg or station_kg <= 0:
        raise UserError('Punkt netto kg ni kiriting.')
    if station_kg > get_float('max_gross_kg', 40000) or station_kg > sent * 1.5:
        raise UserError(f'Punkt vazni ({station_kg:g} kg) jo‘natilgandan ({sent:g} kg) juda katta. Tekshiring.')
    if not photo:
        raise UserError('Muhrlangan umumiy nakladnoyni (UY) rasmga oling — rasmsiz qabul yopilmaydi.')
    diff, pct, level = diff_level(sent, station_kg)
    reason, note = clean_text(reason, 60), clean_text(note, 300)
    if level != 'ok' and reason not in STATION_DIFF_REASONS:
        raise UserError(f'Farq {diff:+g} kg ({pct:+.2f}%). Farq sababini tanlang.')
    if reason == OTHER_REASON and len(note) < 3:
        raise UserError('“Boshqa sabab” tanlansa, sababni qisqa yozing.')
    full_reason = (f'{reason}: {note}' if reason and note else reason or note) or None
    load_no = ''.join(ch for ch in (load_no or '') if ch.isdigit())[:12] or None
    shares = split(station_kg, [i['net_kg'] for i in items])
    ts = now_str()
    with tx() as db:
        if db.execute('SELECT status FROM load_groups WHERE id=?', (gid,)).fetchone()['status'] == 'QABUL':
            return {'already': True, 'group': g}
        station = db.execute('SELECT name FROM stations WHERE id=?', (items[0]['station_id'],)).fetchone()
        from .photos import store_photo
        pid = store_photo(db, actor, photo, category='nayman', entity_type='load_group', entity_id=gid,
                          caption=f'Umumiy nakladnoy {g["number"]}: {station_kg:g} kg', links={'season_year': items[0]['sy']})
        for it, kg in zip(items, shares):
            if db.execute('SELECT 1 FROM nayman_receipts WHERE waybill_id=?', (it['waybill_id'],)).fetchone():
                raise UserError(f'{it["trip_no"]} allaqachon alohida qabul qilingan — uni ro‘yxatdan olib tashlang.')
            d = round(kg - it['net_kg'], 1)
            db.execute('''INSERT INTO nayman_receipts(accepted_kg, diff_kg, diff_reason, received_date, receiver_name, note, waybill_id,
                          created_by, created_at, load_no) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                       (kg, d, f'Umumiy yuk {g["number"]}' + (f' · {full_reason}' if full_reason else ''), today_str(),
                        clean_text(f'{actor.name} ({station["name"]})' if station else actor.name, 80),
                        note or None, it['waybill_id'], actor.user_id, ts, load_no))
            db.execute("UPDATE waybills SET status='QABUL', arrived_at=COALESCE(arrived_at, ?), updated_at=? WHERE id=?",
                       (ts, ts, it['waybill_id']))
            audit(db, actor, 'RECEIVE', 'waybill', it['waybill_id'],
                  new={'trip_no': it['trip_no'], 'field_kg': it['net_kg'], 'station_kg': kg, 'diff_kg': d, 'group': g['number'],
                       'status': 'QABUL QILINDI'})
            _sheet_waybill(db, it['waybill_id'], f'punktda qabul qilindi ({g["number"]})')
        db.execute('''UPDATE load_groups SET status='QABUL', gross_kg=?, tare_kg=?, accepted_kg=?, load_no=?, diff_kg=?, diff_reason=?,
                      photo_id=?, received_by=?, received_at=? WHERE id=?''',
                   (gross_kg, tare_kg, station_kg, load_no, diff, full_reason, pid, actor.user_id, ts, gid))
        audit(db, actor, 'RECEIVE', 'load_group', gid, new={'trailers': len(items), 'sent_kg': sent, 'station_kg': station_kg,
                                                            'diff_kg': diff, 'load_no': load_no, 'reason': full_reason})
        from .reporting import feed
        feed(db, f'grp:{gid}', f'🏭 Umumiy yuk {g["number"]}: {len(items)} ta telashka · dala {sent:,.0f} kg → punkt '
                               f'{station_kg:,.0f} kg · farq {diff:+,.0f} kg ({pct:+.2f}%)'.replace(',', ' '))
        if level == 'alert':
            from .reporting import enqueue_alert
            enqueue_alert(db, f'grp:{gid}', f'🔴 Katta kg farqi: umumiy yuk {g["number"]} · dala {sent:g} kg, punkt {station_kg:g} kg, '
                                           f'farq {diff:+g} kg ({pct:+.2f}%) · {full_reason}')
    try:
        from .pq17 import rematch
        rematch()
    except Exception:
        pass
    return {'already': False, 'group': get(gid), 'diff_kg': diff, 'diff_pct': pct, 'level': level, 'shares': shares}


def _owner(it):
    if it['method'] == 'combine' or it['combines']:
        return it['combines'] or 'Kombayn'
    return f'Qo‘l terimi · {it["brigadier_name"]}' if it['brigadier_name'] else 'Qo‘l terimi'


def detail(gid):
    """The group with its trailers: field, hand / combine and whose, blank, field kg and (after the weighing) the share."""
    from .accounting import combine_owner
    g = get(gid)
    rows = q('''SELECT i.waybill_id, wb.number, wb.net_kg, wb.status, tl.trip_no, tl.method, tl.station_id, tl.season_year sy,
                       e.code trailer, f.code field_code, f.name field_name, b.name brigadier_name, pb.number blank,
                       nr.accepted_kg, nr.diff_kg
                FROM load_group_items i JOIN waybills wb ON wb.id=i.waybill_id JOIN trailer_loads tl ON tl.id=wb.load_id
                LEFT JOIN equipment e ON e.id=tl.trailer_id LEFT JOIN fields f ON f.id=tl.field_id
                LEFT JOIN brigadiers b ON b.id=tl.brigadier_id LEFT JOIN punkt_blanks pb ON pb.waybill_id=wb.id
                LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                WHERE i.group_id=? ORDER BY i.added_at, i.waybill_id''', (gid,))
    items = []
    for r in rows:
        d = dict(r)
        cs = q('''SELECT DISTINCT e.code, e.operator_name, e.ownership FROM harvests h JOIN equipment e ON e.id=h.combine_id
                  JOIN waybills wb ON wb.load_id=h.load_id WHERE wb.id=? AND h.voided_at IS NULL''', (r['waybill_id'],))
        d['combines'] = ', '.join(f'{c["code"]} · {combine_owner(c)}' for c in cs)
        d['owner'] = _owner(d)
        items.append(d)
    return {'group': g, 'items': items, 'sent_kg': round(sum(i['net_kg'] for i in items), 1)}


def candidates(station_id):
    """Trips on the way to this punkt that are in no umumiy yuk and not received — to pick a trailer without a blank."""
    where, params = ["wb.status='YARATILDI'", 'NOT EXISTS (SELECT 1 FROM load_group_items i WHERE i.waybill_id=wb.id)'], []
    if station_id:
        where.append('tl.station_id=?')
        params.append(station_id)
    return q(f'''SELECT wb.id, wb.number, wb.net_kg, tl.trip_no, e.code trailer, f.code field_code, pb.number blank
                 FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id LEFT JOIN equipment e ON e.id=tl.trailer_id
                 LEFT JOIN fields f ON f.id=tl.field_id LEFT JOIN punkt_blanks pb ON pb.waybill_id=wb.id
                 WHERE {' AND '.join(where)} ORDER BY wb.created_at DESC LIMIT 30''', params)


def who(waybill_ids):
    """{waybill_id: {'label': 'K-02 · Farhod aka (xizmat)' / 'Qo‘l terimi', 'combine': bool, 'uy': 'UY-0001'|None}} —
    for the punkt's trip cards, so Yunus sees whose cotton each trailer carries."""
    from .accounting import combine_owner
    ids = [int(i) for i in waybill_ids]
    if not ids:
        return {}
    marks = ','.join('?' * len(ids))
    out = {r['id']: {'label': 'Qo‘l terimi', 'combine': r['method'] == 'combine', 'uy': r['uy']}
           for r in q(f'''SELECT wb.id, tl.method, g.number uy FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                        LEFT JOIN load_group_items i ON i.waybill_id=wb.id LEFT JOIN load_groups g ON g.id=i.group_id
                        WHERE wb.id IN ({marks})''', ids)}
    cs = {}
    for r in q(f'''SELECT DISTINCT wb.id wid, e.code, e.operator_name, e.ownership FROM waybills wb
                    JOIN harvests h ON h.load_id=wb.load_id AND h.voided_at IS NULL JOIN equipment e ON e.id=h.combine_id
                    WHERE wb.id IN ({marks}) ORDER BY e.code''', ids):
        cs.setdefault(r['wid'], []).append(f'{r["code"]} · {combine_owner(r)}')
    for wid, names in cs.items():
        out[wid].update(label=', '.join(names), combine=True)
    for d in out.values():
        if d['combine'] and d['label'] == 'Qo‘l terimi':
            d['label'] = 'Kombayn'
    return out


def of_waybill(waybill_id):
    return q('''SELECT g.* FROM load_groups g JOIN load_group_items i ON i.group_id=g.id WHERE i.waybill_id=?''',
             (waybill_id,), one=True)


def active(station_id):
    """Open / sent groups at this punkt (to continue), newest first."""
    return q(f'''SELECT g.*, (SELECT COUNT(*) FROM load_group_items i WHERE i.group_id=g.id) n,
                        (SELECT COALESCE(SUM(wb.net_kg),0) FROM load_group_items i JOIN waybills wb ON wb.id=i.waybill_id
                         WHERE i.group_id=g.id) kg
                 FROM load_groups g WHERE g.status IN ('OCHIQ','YUBORILDI')''' + (' AND g.station_id=?' if station_id else '') +
             ' ORDER BY g.opened_at DESC', (station_id,) if station_id else ())


def overview():
    return {'next': next_number(), 'batches': q('SELECT batch, COUNT(*) n, MIN(number) a, MAX(number) b FROM load_groups GROUP BY batch ORDER BY batch'),
            'received': q('''SELECT g.*, (SELECT COUNT(*) FROM load_group_items i WHERE i.group_id=g.id) n FROM load_groups g
                             WHERE g.status<>'BOSH' ORDER BY COALESCE(g.received_at, g.opened_at) DESC LIMIT 200''')}
