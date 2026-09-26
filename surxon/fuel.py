"""Solyarka (diesel): take it at a station against a prepaid ticket, give it to a tractor / combine / car.

Two balances that are never mixed:
- a ticket's money (so‘m) = funds put on it − liters taken × the price in force when taken (frozen on the row);
- a fuel keeper's diesel (liters) = liters taken − liters given out.
Giving fuel to a machine moves only liters: the ticket is never charged twice, and nothing here creates a cash-book
expense except the ticket's funding (one expense per funding, from the cash box or the bank).

QR is mandatory: the phone sends the scanned code, the server resolves it and issues a one-use scan row; an
operation must present a fresh scan of the right kind made by the same person. Times are the server's.
A scan is not GPS: it proves the code was read, not where the phone was.
Liters given to a machine are “berilgan”, not a measured consumption. Unusual amounts get a check flag and a reason,
never a verdict.
"""
import secrets
from datetime import datetime, timedelta

from .db import doc_number, get_db, q, scalar, tx
from .photos import store_photo
from .security import audit
from .settings import get_float, get_setting
from .utils import UserError, clean_text, now_str, today_str


def _now():
    """Server wall-clock in the same zone the rows are written in (now_str), for time differences."""
    return datetime.fromisoformat(now_str())

FUEL_TYPES = {'solyarka': 'Solyarka', 'benzin': 'Benzin', 'gaz': 'Gaz'}
REASONS = ['Og‘ir ish', 'Uzoq ishladi', 'Oldingi bak to‘lmagan', 'Boshqa']
QR_PREFIX = 'SPX-YQ'


def _need(actor, perm):
    if not actor.can(perm):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')


def _season(db):
    from .services import current_season
    return current_season(db)


def _liters(v):
    try:
        v = round(float(str(v).replace(',', '.').replace(' ', '')), 1)
    except (TypeError, ValueError):
        raise UserError('Litrni son bilan kiriting.')
    if v <= 0:
        raise UserError('Litr 0 dan katta bo‘lishi kerak.')
    if v > 5000:
        raise UserError('Bunday ko‘p litr bo‘lmaydi — raqamni tekshiring.')
    return v


def fmt_l(v):
    v = round(v or 0, 1)
    return (f'{v:,.0f}' if v == int(v) else f'{v:,.1f}').replace(',', ' ')


def token():
    return secrets.token_urlsafe(9)


def qr_text(kind, tok):
    return f'{QR_PREFIX}:{kind}:{tok}'


# ------------------------------------------------------------------ stations, machines (QR codes)

def save_station(actor, sid, *, code, name, address='', approved=False, active=True):
    _need(actor, 'fuel.manage')
    code, name = clean_text(code, 20).upper(), clean_text(name, 80)
    if not code or not name:
        raise UserError('Zapravka kodi va nomi majburiy.')
    import sqlite3
    with tx() as db:
        try:
            if sid:
                old = db.execute('SELECT * FROM fuel_stations WHERE id=?', (sid,)).fetchone()
                db.execute('UPDATE fuel_stations SET code=?, name=?, address=?, approved=?, active=? WHERE id=?',
                           (code, name, clean_text(address, 120), int(bool(approved)), int(bool(active)), sid))
                audit(db, actor, 'UPDATE', 'fuel_station', sid, old=dict(old),
                      new={'code': code, 'name': name, 'approved': bool(approved), 'active': bool(active)})
                return sid
            sid = db.execute('''INSERT INTO fuel_stations(code, name, address, qr_token, approved, active, created_at)
                                VALUES (?,?,?,?,?,1,?)''', (code, name, clean_text(address, 120), token(),
                                                            int(bool(approved)), now_str())).lastrowid
            audit(db, actor, 'CREATE', 'fuel_station', sid, new={'code': code, 'name': name, 'approved': bool(approved)})
            return sid
        except sqlite3.IntegrityError:
            raise UserError('Bu zapravka kodi allaqachon bor.')


def equipment_token(db, eid):
    row = db.execute('SELECT qr_token FROM equipment WHERE id=?', (eid,)).fetchone()
    if row and row['qr_token']:
        return row['qr_token']
    tok = token()
    db.execute('UPDATE equipment SET qr_token=? WHERE id=? AND qr_token IS NULL', (tok, eid))
    return db.execute('SELECT qr_token FROM equipment WHERE id=?', (eid,)).fetchone()['qr_token']


def set_equipment_fuel(actor, eid, *, fuel_type, carrier):
    _need(actor, 'masterdata.write')
    if fuel_type and fuel_type not in FUEL_TYPES:
        raise UserError('Yoqilg‘i turi noto‘g‘ri.')
    with tx() as db:
        old = db.execute('SELECT fuel_type, fuel_carrier FROM equipment WHERE id=?', (eid,)).fetchone()
        if not old:
            raise UserError('Texnika topilmadi.')
        db.execute('UPDATE equipment SET fuel_type=?, fuel_carrier=? WHERE id=?', (fuel_type or None, int(bool(carrier)), eid))
        equipment_token(db, eid)
        audit(db, actor, 'UPDATE', 'equipment', eid, old=dict(old), new={'fuel_type': fuel_type or None, 'fuel_carrier': bool(carrier)})


# ------------------------------------------------------------------ tickets (accountant)

def ticket_balance(db, ticket_id):
    funds = db.execute('SELECT COALESCE(SUM(amount),0) FROM fuel_ticket_funds WHERE ticket_id=? AND voided_at IS NULL',
                       (ticket_id,)).fetchone()[0]
    used = db.execute("SELECT COALESCE(SUM(amount),0) FROM fuel_ops WHERE ticket_id=? AND kind='OLISH' AND voided_at IS NULL",
                      (ticket_id,)).fetchone()[0]
    return funds - used


def create_ticket(actor, *, station_id, price_per_l, amount, paid_from, cashbox_id=None, note='', client_uuid=None):
    _need(actor, 'fuel.manage')
    if not price_per_l or price_per_l <= 0:
        raise UserError('1 litr narxini kiriting.')
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT ticket_id FROM fuel_ticket_funds WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return dup['ticket_id']
        st = db.execute('SELECT * FROM fuel_stations WHERE id=? AND active=1', (station_id,)).fetchone()
        if not st:
            raise UserError('Zapravka topilmadi.')
        no = doc_number(db, 'TK', _season(db))
        tid = db.execute('''INSERT INTO fuel_tickets(doc_no, station_id, price_per_l, note, opened_by, opened_at)
                            VALUES (?,?,?,?,?,?)''', (no, station_id, price_per_l, clean_text(note, 200), actor.user_id,
                                                      now_str())).lastrowid
        db.execute('INSERT INTO fuel_prices(ticket_id, price_per_l, set_by, set_at) VALUES (?,?,?,?)',
                   (tid, price_per_l, actor.user_id, now_str()))
        audit(db, actor, 'CREATE', 'fuel_ticket', tid, new={'doc_no': no, 'station': st['code'], 'price_per_l': price_per_l})
        add_funds(actor, tid, amount=amount, paid_from=paid_from, cashbox_id=cashbox_id, client_uuid=client_uuid, note=note)
        return tid


def add_funds(actor, ticket_id, *, amount, paid_from, cashbox_id=None, client_uuid=None, note=''):
    """Money put on a ticket: one expense in the books (from the cash box, or paid by bank) — never again later."""
    _need(actor, 'fuel.manage')
    if paid_from not in ('kassa', 'bank'):
        raise UserError('Tiket puli qayerdan to‘langanini tanlang (kassa yoki bank).')
    if not amount or amount <= 0:
        raise UserError('Tiket summasini kiriting.')
    from .services import add_expense
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT id FROM fuel_ticket_funds WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return dup['id']
        t = db.execute('SELECT t.*, s.name station_name FROM fuel_tickets t JOIN fuel_stations s ON s.id=t.station_id '
                       'WHERE t.id=?', (ticket_id,)).fetchone()
        if not t or t['status'] != 'AKTIV':
            raise UserError('Tiket topilmadi yoki yopilgan.')
        eid = add_expense(actor, amount=amount, expense_date=today_str(), category='Yoqilg‘i', payer=t['station_name'],
                          note=f'Solyarka tiketi {t["doc_no"]}' + (f' — {note}' if note else ''), from_cash=paid_from == 'kassa',
                          cashbox_id=cashbox_id)
        fid = db.execute('''INSERT INTO fuel_ticket_funds(ticket_id, amount, paid_from, expense_id, note, client_uuid,
                                created_by, created_at) VALUES (?,?,?,?,?,?,?,?)''',
                         (ticket_id, amount, paid_from, eid, clean_text(note, 200), client_uuid, actor.user_id,
                          now_str())).lastrowid
        audit(db, actor, 'FUND', 'fuel_ticket', ticket_id, new={'amount': amount, 'paid_from': paid_from, 'expense_id': eid})
        return fid


def set_price(actor, ticket_id, price_per_l):
    """New price for the next takes only — rows already written keep the price they were taken at."""
    _need(actor, 'fuel.manage')
    if not price_per_l or price_per_l <= 0:
        raise UserError('Narx 0 dan katta bo‘lishi kerak.')
    with tx() as db:
        t = db.execute('SELECT * FROM fuel_tickets WHERE id=?', (ticket_id,)).fetchone()
        if not t or t['status'] != 'AKTIV':
            raise UserError('Tiket topilmadi yoki yopilgan.')
        db.execute('UPDATE fuel_tickets SET price_per_l=? WHERE id=?', (price_per_l, ticket_id))
        db.execute('INSERT INTO fuel_prices(ticket_id, price_per_l, set_by, set_at) VALUES (?,?,?,?)',
                   (ticket_id, price_per_l, actor.user_id, now_str()))
        audit(db, actor, 'PRICE', 'fuel_ticket', ticket_id, old={'price_per_l': t['price_per_l']}, new={'price_per_l': price_per_l})


def close_ticket(actor, ticket_id, *, station_balance=None, note=''):
    """Reconcile and close. Liters the keeper already holds are not touched (they are a separate balance)."""
    _need(actor, 'fuel.manage')
    with tx() as db:
        t = db.execute('SELECT * FROM fuel_tickets WHERE id=?', (ticket_id,)).fetchone()
        if not t or t['status'] != 'AKTIV':
            raise UserError('Tiket topilmadi yoki allaqachon yopilgan.')
        bal = ticket_balance(db, ticket_id)
        db.execute("""UPDATE fuel_tickets SET status='YOPILGAN', closed_by=?, closed_at=?, close_note=?, station_balance=?
                      WHERE id=? AND status='AKTIV'""", (actor.user_id, now_str(), clean_text(note, 300), station_balance, ticket_id))
        audit(db, actor, 'CLOSE', 'fuel_ticket', ticket_id, new={'system_balance': bal, 'station_balance': station_balance,
                                                                 'diff': None if station_balance is None else station_balance - bal},
              reason=clean_text(note, 300) or None)
        return bal


def active_tickets(db, station_id):
    rows = db.execute("""SELECT * FROM fuel_tickets WHERE station_id=? AND status='AKTIV' ORDER BY id""", (station_id,)).fetchall()
    return [dict(r, balance=ticket_balance(db, r['id'])) for r in rows]


# ------------------------------------------------------------------ keeper balance, machine info

def keeper_liters(db, keeper_id):
    r = db.execute('''SELECT COALESCE(SUM(CASE WHEN kind='OLISH' THEN liters END),0) - COALESCE(SUM(CASE WHEN kind='BERISH'
                      THEN liters END),0) FROM fuel_ops WHERE keeper_id=? AND voided_at IS NULL''', (keeper_id,)).fetchone()[0]
    return round(r, 1)


def machine_info(db, eid):
    today = today_str()
    r = db.execute('''SELECT COALESCE(SUM(liters),0) l, COUNT(*) n FROM fuel_ops WHERE equipment_id=? AND kind='BERISH'
                      AND voided_at IS NULL AND substr(created_at,1,10)=?''', (eid, today)).fetchone()
    last = db.execute('''SELECT created_at, liters FROM fuel_ops WHERE equipment_id=? AND kind='BERISH' AND voided_at IS NULL
                         ORDER BY id DESC LIMIT 1''', (eid,)).fetchone()
    return {'today_l': round(r['l'], 1), 'today_n': r['n'], 'last_at': last['created_at'] if last else None,
            'last_l': last['liters'] if last else None}


# ------------------------------------------------------------------ scan

def scan(actor, payload, want):
    """Resolve a scanned QR (station or machine). Returns the target and a one-use scan id issued by the server."""
    _need(actor, 'fuel.operate')
    payload = (payload or '').strip()
    parts = payload.split(':')
    if len(parts) != 3 or parts[0] != QR_PREFIX or parts[1] not in ('Z', 'T'):
        raise UserError('Bu QR tizimniki emas. Zapravka yoki texnikaning SURXON QR kodini skanerlang.')
    kind = 'station' if parts[1] == 'Z' else 'equipment'
    if kind != want:
        raise UserError('Bu zapravka QR kodi — texnika kodini skanerlang.' if kind == 'station'
                        else 'Bu texnika QR kodi — zapravka kodini skanerlang.')
    with tx() as db:
        if kind == 'station':
            t = db.execute('SELECT * FROM fuel_stations WHERE qr_token=?', (parts[2],)).fetchone()
            if not t or not t['active']:
                raise UserError('Zapravka topilmadi yoki faol emas.')
            if not t['approved']:
                raise UserError(f'{t["code"]} QR kodi hali buxgalter tomonidan tasdiqlanmagan.')
        else:
            t = db.execute('SELECT * FROM equipment WHERE qr_token=?', (parts[2],)).fetchone()
            if not t or not t['active']:
                raise UserError('Texnika topilmadi yoki faol emas.')
            if t['fuel_type'] != 'solyarka':
                raise UserError(f'{t["code"]}: bu texnika solyarka ishlatmaydi'
                                + (f' ({FUEL_TYPES[t["fuel_type"]].lower()})' if t['fuel_type'] else ' (yoqilg‘i turi belgilanmagan)')
                                + ' — solyarka berib bo‘lmaydi.')
        sid = db.execute('INSERT INTO fuel_scans(user_id, target, target_id, created_at) VALUES (?,?,?,?)',
                         (actor.user_id, kind, t['id'], now_str())).lastrowid
        return sid, dict(t)


def _take_scan(db, actor, scan_id, target):
    s = db.execute('SELECT * FROM fuel_scans WHERE id=?', (scan_id,)).fetchone()
    if not s or s['user_id'] != actor.user_id or s['target'] != target:
        raise UserError('QR skan topilmadi — qayta skanerlang.')
    valid = get_float('fuel_scan_valid_min', 30, db) or 30
    if _now() - datetime.fromisoformat(s['created_at']) > timedelta(minutes=valid):
        raise UserError(f'QR skanerlanganiga {valid:g} daqiqadan ko‘p bo‘ldi — qayta skanerlang.')
    return s


def scan_state(db, scan_id, actor):
    s = db.execute('SELECT * FROM fuel_scans WHERE id=? AND user_id=?', (scan_id, actor.user_id)).fetchone()
    return s


# ------------------------------------------------------------------ take (OLISH)

def take(actor, *, scan_id, liters, ticket_id=None, client_uuid=None):
    _need(actor, 'fuel.operate')
    liters = _liters(liters)
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT id FROM fuel_ops WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return dup['id'], True
        s = _take_scan(db, actor, scan_id, 'station')
        if s['used_at']:
            raise UserError('Bu QR skan allaqachon ishlatilgan — qayta skanerlang.')
        tickets = active_tickets(db, s['target_id'])
        if not tickets:
            raise UserError('Bu zapravkada faol tiket yo‘q — buxgalterga ayting.')
        if ticket_id:
            t = next((x for x in tickets if x['id'] == int(ticket_id)), None)
            if not t:
                raise UserError('Bu tiket faol emas yoki boshqa zapravkaniki.')
        elif len(tickets) == 1:
            t = tickets[0]
        else:
            raise UserError('Bu zapravkada bir nechta faol tiket bor — tiketni tanlang.')
        amount = int(round(liters * t['price_per_l']))
        if amount > t['balance']:
            raise UserError(f'Tiketda pul yetmaydi: qolgan {t["balance"]:,} so‘m, kerak {amount:,} so‘m '
                            f'({fmt_l(liters)} L × {t["price_per_l"]:,}).'.replace(',', ' '))
        ts = now_str()
        no = doc_number(db, 'YQ', _season(db))
        oid = db.execute('''INSERT INTO fuel_ops(doc_no, kind, keeper_id, station_id, ticket_id, liters, price_per_l, amount,
                                scan_id, scan_at, client_uuid, created_at) VALUES (?,'OLISH',?,?,?,?,?,?,?,?,?,?)''',
                         (no, actor.user_id, s['target_id'], t['id'], liters, t['price_per_l'], amount, scan_id,
                          s['created_at'], client_uuid, ts)).lastrowid
        db.execute('UPDATE fuel_scans SET used_at=? WHERE id=?', (ts, scan_id))
        audit(db, actor, 'OLISH', 'fuel_op', oid, new={'doc_no': no, 'liters': liters, 'ticket': t['doc_no'],
                                                        'price_per_l': t['price_per_l'], 'amount': amount})
        from .reporting import feed
        st = db.execute('SELECT code FROM fuel_stations WHERE id=?', (s['target_id'],)).fetchone()
        feed(db, f'fuel:{oid}', f'⛽ Solyarka olindi: +{fmt_l(liters)} L · {st["code"]} · {t["doc_no"]}\n{no} · {actor.name or ""}')
        return oid, False


# ------------------------------------------------------------------ give (BERISH)

def flags_for(db, eid, liters):
    """Reasons to take a second look (not a verdict): a big single amount, a big day, or again too soon."""
    out = []
    one = get_float('fuel_max_issue_l', 200, db) or 200
    day = get_float('fuel_max_day_l', 400, db) or 400
    gap = get_float('fuel_min_gap_min', 60, db) or 60
    info = machine_info(db, eid)
    if liters > one:
        out.append(f'bir berishda {fmt_l(liters)} L (odatiy chegara {one:g} L)')
    if info['today_l'] + liters > day:
        out.append(f'bugun jami {fmt_l(info["today_l"] + liters)} L (kunlik chegara {day:g} L)')
    if info['last_at'] and _now() - datetime.fromisoformat(info['last_at']) < timedelta(minutes=gap):
        mins = int((_now() - datetime.fromisoformat(info['last_at'])).total_seconds() // 60)
        out.append(f'oldingi berishdan {mins} daqiqa o‘tgan (odatiy {gap:g} daqiqa)')
    return out, info


def give_check(actor, *, scan_id, liters):
    _need(actor, 'fuel.operate')
    liters = _liters(liters)
    db = get_db()
    s = _take_scan(db, actor, scan_id, 'equipment')
    have = keeper_liters(db, actor.user_id)
    if liters > have:
        raise UserError(f'Sizda {fmt_l(have)} L bor — {fmt_l(liters)} L berib bo‘lmaydi.')
    flags, info = flags_for(db, s['target_id'], liters)
    return {'liters': liters, 'have': have, 'after': round(have - liters, 1), 'flags': flags, 'info': info}


def give(actor, *, scan_id, liters, photo, reason='', reason_note='', client_uuid=None, purpose=''):
    _need(actor, 'fuel.operate')
    liters = _liters(liters)
    if not photo:
        raise UserError('Yoqilg‘i quyilayotgan joyni kamera bilan rasmga oling.')
    import hashlib
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT id FROM fuel_ops WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return dup['id'], True
        s = _take_scan(db, actor, scan_id, 'equipment')
        if s['used_at']:
            raise UserError('Bu QR skan allaqachon ishlatilgan — qayta skanerlang.')
        have = keeper_liters(db, actor.user_id)
        if liters > have:
            raise UserError(f'Sizda {fmt_l(have)} L bor — {fmt_l(liters)} L berib bo‘lmaydi.')
        sha = hashlib.sha256(photo).hexdigest()
        if db.execute("SELECT 1 FROM photos WHERE sha256=? AND entity_type='fuel_op' AND voided_at IS NULL", (sha,)).fetchone():
            raise UserError('Bu rasm oldingi berishda ishlatilgan — yangi rasm oling.')
        flags, _ = flags_for(db, s['target_id'], liters)
        reason, reason_note = clean_text(reason, 40), clean_text(reason_note, 200)
        if flags:
            if reason not in REASONS:
                raise UserError('Odatdagidan ko‘p yoki tez-tez — sababini tanlang.')
            if reason == 'Boshqa' and len(reason_note) < 3:
                raise UserError('“Boshqa” tanlansa, sababni qisqa yozing.')
        else:
            reason, reason_note = '', ''
        purpose = clean_text(purpose, 40) or None
        if purpose:
            from .fleet import work_types
            if purpose not in [n for n, _ in work_types()]:
                raise UserError('Ish turi ro‘yxatda yo‘q — qaytadan tanlang.')
        ts = now_str()
        no = doc_number(db, 'YQ', _season(db))
        oid = db.execute('''INSERT INTO fuel_ops(doc_no, kind, keeper_id, equipment_id, liters, scan_id, scan_at, flag, reason,
                                reason_note, client_uuid, created_at, purpose) VALUES (?,'BERISH',?,?,?,?,?,?,?,?,?,?,?)''',
                         (no, actor.user_id, s['target_id'], liters, scan_id, s['created_at'], '; '.join(flags) or None,
                          reason or None, reason_note or None, client_uuid, ts, purpose)).lastrowid
        eq = db.execute('SELECT code FROM equipment WHERE id=?', (s['target_id'],)).fetchone()
        pid = store_photo(db, actor, photo, category='fuel', entity_type='fuel_op', entity_id=oid,
                          caption=f'{no} · {eq["code"]} · {fmt_l(liters)} L')
        db.execute('UPDATE fuel_ops SET photo_id=? WHERE id=?', (pid, oid))
        db.execute('UPDATE fuel_scans SET used_at=? WHERE id=?', (ts, scan_id))
        audit(db, actor, 'BERISH', 'fuel_op', oid, new={'doc_no': no, 'equipment': eq['code'], 'liters': liters,
                                                         'flag': flags or None, 'reason': reason or None, 'purpose': purpose})
        from .reporting import feed
        feed(db, f'fuel:{oid}', f'🚜 Solyarka berildi: {eq["code"]} −{fmt_l(liters)} L' + (f' · {purpose}' if purpose else '')
             + f'\n{no} · {actor.name or ""}')
        if flags:
            text = (f'⚠️ Solyarka — tekshirish kerak: {eq["code"]} {fmt_l(liters)} L\n' + '; '.join(flags)
                    + f'\nSabab: {reason}' + (f' — {reason_note}' if reason_note else '') + f'\n{no} · {actor.name or ""}')
            from .reporting import enqueue_alert
            enqueue_alert(db, f'fuelflag:{oid}', text)
        return oid, False


# ------------------------------------------------------------------ correction (accountant): void with a reason

def void_op(actor, op_id, reason):
    _need(actor, 'fuel.manage')
    reason = clean_text(reason, 300)
    if len(reason) < 3:
        raise UserError('Bekor qilish sababini yozing.')
    with tx() as db:
        o = db.execute('SELECT * FROM fuel_ops WHERE id=?', (op_id,)).fetchone()
        if not o or o['voided_at']:
            raise UserError('Amal topilmadi yoki allaqachon bekor qilingan.')
        if o['kind'] == 'OLISH' and keeper_liters(db, o['keeper_id']) - o['liters'] < 0:
            raise UserError('Bu olingan solyarka allaqachon berilgan — avval tegishli berishni tuzating.')
        db.execute('UPDATE fuel_ops SET voided_at=?, voided_by=?, void_reason=? WHERE id=?', (now_str(), actor.user_id, reason, op_id))
        audit(db, actor, 'VOID', 'fuel_op', op_id, old=dict(o), reason=reason)


# ------------------------------------------------------------------ FIFO: which takes each give came from (for cost)

def fifo(db, keeper_id):
    """Replays the keeper's takes and gives in order: each give is matched to the oldest liters still held.
    Returns ({give_id: [(take_id, liters, price)]}, [open lots]). Derived every time, so a correction stays consistent."""
    lots, used = [], {}
    for o in db.execute('SELECT * FROM fuel_ops WHERE keeper_id=? AND voided_at IS NULL ORDER BY id', (keeper_id,)):
        if o['kind'] == 'OLISH':
            lots.append([o['id'], o['liters'], o['price_per_l']])
            continue
        need, parts = o['liters'], []
        while need > 1e-9 and lots:
            take_l = min(need, lots[0][1])
            parts.append((lots[0][0], round(take_l, 1), lots[0][2]))
            lots[0][1] -= take_l
            need -= take_l
            if lots[0][1] <= 1e-9:
                lots.pop(0)
        used[o['id']] = parts
    return used, [{'take_id': x[0], 'liters': round(x[1], 1), 'price': x[2]} for x in lots]


def give_cost(db, op):
    parts = fifo(db, op['keeper_id'])[0].get(op['id'], [])
    return int(round(sum(l * p for _, l, p in parts)))


# ------------------------------------------------------------------ lists

OP_SELECT = '''SELECT o.*, u.full_name keeper_name, s.code station_code, s.name station_name, t.doc_no ticket_no,
                      e.code equipment_code, e.kind equipment_kind, e.plate equipment_plate, p.thumb_path, p.path photo_path,
                      vu.full_name voided_name
               FROM fuel_ops o JOIN users u ON u.id=o.keeper_id LEFT JOIN fuel_stations s ON s.id=o.station_id
               LEFT JOIN fuel_tickets t ON t.id=o.ticket_id LEFT JOIN equipment e ON e.id=o.equipment_id
               LEFT JOIN photos p ON p.id=o.photo_id LEFT JOIN users vu ON vu.id=o.voided_by'''


def my_ops(keeper_id, day=None):
    return q(f'{OP_SELECT} WHERE o.keeper_id=? AND substr(o.created_at,1,10)=? ORDER BY o.id DESC', (keeper_id, day or today_str()))


def ops(date_from, date_to, limit=300):
    return q(f'{OP_SELECT} WHERE substr(o.created_at,1,10) BETWEEN ? AND ? ORDER BY o.id DESC LIMIT ?', (date_from, date_to, limit))


def op(op_id):
    return q(f'{OP_SELECT} WHERE o.id=?', (op_id,), one=True)


def tickets_overview():
    db = get_db()
    rows = q('''SELECT t.*, s.code station_code, s.name station_name FROM fuel_tickets t JOIN fuel_stations s ON s.id=t.station_id
                ORDER BY t.status, t.id DESC''')
    out = []
    for t in rows:
        funds = scalar('SELECT COALESCE(SUM(amount),0) FROM fuel_ticket_funds WHERE ticket_id=? AND voided_at IS NULL', (t['id'],))
        taken = q("""SELECT COALESCE(SUM(liters),0) l, COALESCE(SUM(amount),0) a FROM fuel_ops WHERE ticket_id=?
                     AND kind='OLISH' AND voided_at IS NULL""", (t['id'],), one=True)
        out.append(dict(t, funds=funds, taken_l=taken['l'], taken_amount=taken['a'], balance=ticket_balance(db, t['id'])))
    return out


def keepers():
    db = get_db()
    return [dict(u, liters=keeper_liters(db, u['id'])) for u in
            q("SELECT id, full_name, active FROM users WHERE role='fuel' OR id IN (SELECT keeper_id FROM fuel_ops) ORDER BY full_name")]


def totals(date_from, date_to):
    r = q('''SELECT COALESCE(SUM(CASE WHEN kind='OLISH' THEN liters END),0) taken,
                    COALESCE(SUM(CASE WHEN kind='BERISH' THEN liters END),0) given,
                    COALESCE(SUM(CASE WHEN kind='OLISH' THEN amount END),0) taken_amount,
                    SUM(CASE WHEN kind='BERISH' AND flag IS NOT NULL THEN 1 ELSE 0 END) flagged
             FROM fuel_ops WHERE voided_at IS NULL AND substr(created_at,1,10) BETWEEN ? AND ?''', (date_from, date_to), one=True)
    db = get_db()
    return {'taken_l': round(r['taken'], 1), 'given_l': round(r['given'], 1), 'taken_amount': r['taken_amount'],
            'flagged': r['flagged'] or 0,
            'held_l': round(sum(keeper_liters(db, k['id']) for k in q('SELECT DISTINCT keeper_id id FROM fuel_ops')), 1),
            'tickets_balance': sum(ticket_balance(db, t['id']) for t in q("SELECT id FROM fuel_tickets WHERE status='AKTIV'")),
            'active_tickets': scalar("SELECT COUNT(*) FROM fuel_tickets WHERE status='AKTIV'")}


def by_machine(date_from, date_to):
    return q('''SELECT e.id, e.code, e.kind, e.plate, COUNT(*) n, SUM(o.liters) liters,
                       SUM(CASE WHEN o.flag IS NOT NULL THEN 1 ELSE 0 END) flagged, MAX(o.created_at) last_at
                FROM fuel_ops o JOIN equipment e ON e.id=o.equipment_id
                WHERE o.kind='BERISH' AND o.voided_at IS NULL AND substr(o.created_at,1,10) BETWEEN ? AND ?
                GROUP BY e.id ORDER BY liters DESC''', (date_from, date_to))
