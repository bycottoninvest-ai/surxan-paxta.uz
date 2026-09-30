"""Punkt blanks: numbered paper forms printed in advance (a whole season at once) and kept at the punkt, so nothing has to
be printed in the field. The field closes the trip electronically as before; at the punkt the receiver fills one blank by
hand (top: our trip number, sent kg, hand / combine — copied from the phone; bottom: the punkt's brutto / tara / accepted
kg, name, signature, stamp), scans its QR, photographs it and attaches it to that trip. One blank = one trip, never
reused; the office list shows which blank went to which trip, which are still blank and which numbers are missing.
"""
import secrets

from .db import q, tx
from .security import audit
from .utils import UserError, now_str

MAX_BATCH = 1000


def _need(actor, perm):
    if not actor or not actor.can(perm):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')


def create_batch(actor, count):
    """New blanks numbered after the last one (PB-0001 …). Returns (batch, first number, last number)."""
    _need(actor, 'masterdata.write')
    try:
        count = int(str(count).replace(' ', ''))
    except ValueError:
        raise UserError('Nechta blank kerakligini raqam bilan yozing (masalan 600).')
    if not 1 <= count <= MAX_BATCH:
        raise UserError(f'Bir martada 1 dan {MAX_BATCH} tagacha blank chiqarish mumkin.')
    with tx() as db:
        last = db.execute('SELECT MAX(id) m, MAX(batch) b FROM punkt_blanks').fetchone()
        start, batch = (last['m'] or 0) + 1, (last['b'] or 0) + 1
        now = now_str()
        for n in range(start, start + count):
            db.execute('INSERT INTO punkt_blanks(number, token, batch, created_by, created_at) VALUES (?,?,?,?,?)',
                       (f'PB-{n:04d}', secrets.token_urlsafe(9), batch, actor.user_id, now))
        audit(db, actor, 'CREATE', 'punkt_blanks', batch, new={'count': count, 'from': f'PB-{start:04d}',
                                                               'to': f'PB-{start + count - 1:04d}'})
    return batch, f'PB-{start:04d}', f'PB-{start + count - 1:04d}'


def find(code):
    """A blank by the token in its QR, by its number (PB-0012 / 12) or by a whole QR link."""
    code = (code or '').strip()
    if '/blanka/' in code:
        code = code.rsplit('/blanka/', 1)[1].split('?')[0].strip('/')
    b = q('SELECT * FROM punkt_blanks WHERE token=?', (code,), one=True)
    if b:
        return b
    digits = ''.join(ch for ch in code if ch.isdigit())
    return q('SELECT * FROM punkt_blanks WHERE number=?', (f'PB-{int(digits):04d}',), one=True) if digits else None


def required():
    """Once blanks are printed (and unless switched off: punkt_blank_required = 0) a trip can be received at the punkt
    only with its filled blank attached — number and photo — so every receipt has its paper for the check."""
    from .settings import get_bool
    return get_bool('punkt_blank_required') and bool(q('SELECT 1 FROM punkt_blanks LIMIT 1', one=True))


def of_waybill(waybill_id):
    return q('SELECT * FROM punkt_blanks WHERE waybill_id=?', (waybill_id,), one=True)


def check_free(code, waybill_id=None):
    """The blank for this code, if it exists, is not spoiled and is not used by another trip — else a clear error."""
    b = find(code)
    if not b:
        raise UserError('Bunday blank topilmadi — QR ni qayta skanerlang yoki raqamini to‘g‘ri yozing (masalan PB-0012).')
    if b['spoiled_reason']:
        raise UserError(f'{b["number"]} buzilgan deb belgilangan — boshqa blank oling.')
    if b['waybill_id'] and b['waybill_id'] != waybill_id:
        other = q('SELECT tl.trip_no FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id WHERE wb.id=?',
                  (b['waybill_id'],), one=True)
        raise UserError(f'{b["number"]} allaqachon {other["trip_no"] if other else "boshqa reys"}ga ishlatilgan — '
                        'bitta blank faqat bitta reysga.')
    return b


def attach(actor, waybill_id, code, photo=None, field=False, photo_later=False):
    """Tie a filled blank to a trip (with its photo). Idempotent for the same pair; a blank already used for another
    trip, or a trip that already has a blank, is refused. In the field (the clerk scans the blank when closing the
    trailer) no photo is needed yet — the punkt photographs the filled paper."""
    _need(actor, 'load.full' if field else 'station.receive')
    b = check_free(code, waybill_id)
    with tx() as db:
        wb = db.execute("""SELECT wb.*, tl.trip_no, tl.station_id, tl.season_year sy FROM waybills wb
                           JOIN trailer_loads tl ON tl.id=wb.load_id WHERE wb.id=?""", (waybill_id,)).fetchone()
        if not wb or wb['status'] == 'BEKOR':
            raise UserError('Reys topilmadi yoki bekor qilingan.')
        if not field and actor.role == 'station' and wb['station_id'] != actor.station_id:
            raise UserError('Bu reys boshqa punktga jo‘natilgan.')
        cur = db.execute('SELECT * FROM punkt_blanks WHERE id=?', (b['id'],)).fetchone()
        if cur['waybill_id'] and cur['waybill_id'] != waybill_id:
            other = db.execute('SELECT tl.trip_no FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id WHERE wb.id=?',
                               (cur['waybill_id'],)).fetchone()
            raise UserError(f'{cur["number"]} allaqachon {other["trip_no"] if other else "boshqa reys"}ga ishlatilgan — '
                            'bitta blank faqat bitta reysga.')
        has = db.execute('SELECT number FROM punkt_blanks WHERE waybill_id=? AND id<>?', (waybill_id, b['id'])).fetchone()
        if has:
            raise UserError(f'{wb["trip_no"]} ga {has["number"]} blank allaqachon biriktirilgan.')
        pid = cur['photo_id']
        if photo:
            from .photos import store_photo
            pid = store_photo(db, actor, photo, category='nayman', entity_type='punkt_blank', entity_id=b['id'],
                              caption=f'Punkt blanki {cur["number"]} · {wb["trip_no"]}',
                              links={'waybill_id': waybill_id, 'load_id': wb['load_id'], 'season_year': wb['sy']})
        if not pid and not field and not photo_later:     # umumiy yuk: one photo of the UY paper covers it
            raise UserError('To‘ldirilgan blankni rasmga oling.')
        db.execute('UPDATE punkt_blanks SET waybill_id=?, used_by=COALESCE(used_by, ?), used_at=COALESCE(used_at, ?), photo_id=? '
                   'WHERE id=?', (waybill_id, actor.user_id, now_str(), pid, b['id']))
        audit(db, actor, 'ATTACH', 'punkt_blank', b['id'], new={'waybill_id': waybill_id, 'trip_no': wb['trip_no'], 'photo_id': pid,
                                                              'where': 'dala' if field else 'punkt'})
    return cur['number']


def spoil(actor, blank_id, reason):
    _need(actor, 'masterdata.write')
    reason = (reason or '').strip()[:120]
    if len(reason) < 3:
        raise UserError('Sababini yozing (masalan: yirtilgan, noto‘g‘ri yozilgan).')
    with tx() as db:
        b = db.execute('SELECT * FROM punkt_blanks WHERE id=?', (blank_id,)).fetchone()
        if not b:
            raise UserError('Blank topilmadi.')
        if b['waybill_id']:
            raise UserError('Reysga biriktirilgan blankni buzilgan deb bo‘lmaydi.')
        db.execute('UPDATE punkt_blanks SET spoiled_reason=? WHERE id=?', (reason, blank_id))
        audit(db, actor, 'UPDATE', 'punkt_blank', blank_id, new={'spoiled_reason': reason})


def candidates(station_id, limit=12):
    """Trips at (or on the way to) this punkt that have no blank yet — newest arrival first — to pick for a scanned blank."""
    where = ["wb.status<>'BEKOR'", 'NOT EXISTS (SELECT 1 FROM punkt_blanks pb WHERE pb.waybill_id=wb.id)']
    params = []
    if station_id:
        where.append('tl.station_id=?')
        params.append(station_id)
    return q(f"""SELECT wb.id, wb.number, wb.status, wb.net_kg, wb.arrived_at, tl.trip_no, tl.method, e.code trailer,
                        f.code field_code, nr.accepted_kg
                 FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id LEFT JOIN equipment e ON e.id=tl.trailer_id
                 LEFT JOIN fields f ON f.id=tl.field_id LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                 WHERE {' AND '.join(where)}
                 ORDER BY (wb.arrived_at IS NOT NULL) DESC, COALESCE(wb.arrived_at, wb.created_at) DESC LIMIT ?""",
             params + [limit])


def overview(batch=None):
    """The office check: every blank with its trip; counts; numbers skipped (a later blank used, an earlier one not)."""
    rows = q("""SELECT pb.*, tl.trip_no, wb.number wb_number, wb.net_kg, nr.accepted_kg, u.full_name used_name, s.name station
                FROM punkt_blanks pb LEFT JOIN waybills wb ON wb.id=pb.waybill_id LEFT JOIN trailer_loads tl ON tl.id=wb.load_id
                LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id LEFT JOIN users u ON u.id=pb.used_by
                LEFT JOIN stations s ON s.id=tl.station_id""" + (' WHERE pb.batch=?' if batch else '') + ' ORDER BY pb.id',
             (batch,) if batch else ())
    last_used = max((r['id'] for r in rows if r['waybill_id']), default=0)
    skipped = [r for r in rows if not r['waybill_id'] and not r['spoiled_reason'] and r['id'] < last_used]
    return {'rows': rows, 'total': len(rows), 'used': sum(1 for r in rows if r['waybill_id']),
            'spoiled': sum(1 for r in rows if r['spoiled_reason']),
            'free': sum(1 for r in rows if not r['waybill_id'] and not r['spoiled_reason']),
            'skipped': skipped, 'batches': q('SELECT batch, COUNT(*) n, MIN(number) a, MAX(number) b, MIN(created_at) at '
                                             'FROM punkt_blanks GROUP BY batch ORDER BY batch')}
