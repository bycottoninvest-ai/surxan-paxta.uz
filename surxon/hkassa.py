"""Hisobchi kassasi: the field clerk pays the TEZ-PUL talons himself.

Thousands of pickers come every day and one cashier cannot keep up, so in the morning the cashier hands each field
clerk a sum (20–30 mln). The money leaves the cashier's box at once (TRF, “Hisobchiga berildi”) and lands in the
clerk's own box only when the clerk taps “OLDIM” on his phone — until then it is on the way, and the cashier can take
it back. During the day the clerk scans talons and pays from his box (tezpul.pay → his box; no money, no payment).
In the evening he brings back what is left: the cashier counts it and taps “QABUL QILDIM” with the counted sum. What
the system says he should have and what was counted are kept side by side — a difference is a “Farq” the rahbar sees,
it stays on the clerk's box until it is settled. Nothing is deleted: every move is a cash entry with its document.
"""
from . import accounting as A
from .db import q, tx
from .security import audit, pays_cash
from .settings import get_setting
from .utils import UserError, clean_text, now_str, today_str

XFER = ('xfer_out', 'xfer_in')
NO_XFER = "category NOT IN ('xfer_out','xfer_in')"     # company totals: money moved between our boxes is not income


def _need(actor, perm):
    if not actor or not actor.can(perm):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')


def _amount(value):
    try:
        n = int(float(str(value or '').replace(' ', '').replace(',', '.').replace(' ', '')))
    except ValueError:
        raise UserError('Summani raqam bilan yozing.')
    if n <= 0:
        raise UserError('Summa 0 dan katta bo‘lsin.')
    if n > 1_000_000_000:
        raise UserError('Summa juda katta — tekshiring.')
    return n


def _tally(db, uid):
    u = db.execute("SELECT * FROM users WHERE id=? AND role='tally'", (uid,)).fetchone()
    if not u:
        raise UserError('Terim hisobchisi topilmadi.')
    return u


def _ready(db, uid):
    u = _tally(db, uid)
    if not u['pays_cash'] or not u['cashbox_id']:
        raise UserError(f'{u["full_name"]}: pul berish yoqilmagan (Admin yoqadi).')
    return u


def enable(actor, user_id, on=True):
    """Admin: this field clerk pays talons himself — he gets his own cash box (“Hisobchi: Ism”)."""
    if not actor or actor.role != 'admin':
        raise UserError('Buni faqat Admin yoqadi.')
    with tx() as db:
        u = _tally(db, user_id)
        box = u['cashbox_id']
        if on:
            if not box:
                name = clean_text(f'Hisobchi: {u["full_name"] or u["username"]}', 60)
                row = db.execute('SELECT id FROM cashboxes WHERE name=?', (name,)).fetchone()
                box = row['id'] if row else db.execute('INSERT INTO cashboxes(name, active, created_at) VALUES (?,1,?)',
                                                       (name, now_str())).lastrowid
            db.execute('UPDATE cashboxes SET active=1 WHERE id=?', (box,))
            db.execute('UPDATE users SET pays_cash=1, cashbox_id=? WHERE id=?', (box, user_id))
        else:
            hand = A.box_balance(db, box) if box else 0
            if hand:
                raise UserError(f'{u["full_name"]} qo‘lida {A.fmt_som(hand)} bor — avval kassirga topshirsin.')
            if db.execute("SELECT 1 FROM tally_floats WHERE tally_id=? AND status='KUTILMOQDA'", (user_id,)).fetchone():
                raise UserError('Tasdiqlanmagan pul bor — avval kassir uni qaytarib olsin.')
            db.execute('UPDATE users SET pays_cash=0 WHERE id=?', (user_id,))
        audit(db, actor, 'UPDATE', 'user', user_id, new={'pays_cash': bool(on), 'cashbox_id': box})
    return box


def give(actor, tally_id, amount, note='', client_uuid=None):
    """Cashier → clerk, morning or a top-up. The money leaves the cashier's box now; the clerk confirms “OLDIM”."""
    _need(actor, 'hkassa.give')
    amount = _amount(amount)
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT id FROM tally_floats WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return dup['id']
        u = _ready(db, tally_id)
        src = A._cashbox(db, actor, None)
        if src == u['cashbox_id']:
            raise UserError('Hisobchi o‘z kassasidan o‘ziga bera olmaydi.')
        out_id, _ = A.book_cash(db, actor, direction='OUT', category='xfer_out', amount=amount, cashbox_id=src,
                                counterparty=u['full_name'], note=clean_text(f'Hisobchiga berildi · {note}'.rstrip(' ·'), 200),
                                feed=False)
        fid = db.execute('''INSERT INTO tally_floats(tally_id, kind, status, day, amount, from_box, to_box, out_entry_id, note,
                                client_uuid, created_by, created_at) VALUES (?, 'BERISH', 'KUTILMOQDA', ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                         (tally_id, today_str(), amount, src, u['cashbox_id'], out_id, clean_text(note, 200), client_uuid,
                          actor.user_id, now_str())).lastrowid
        audit(db, actor, 'CREATE', 'tally_float', fid, new={'kind': 'BERISH', 'to': u['full_name'], 'amount': amount})
        from .reporting import feed
        feed(db, f'hkassa:{fid}', f'💵 HISOBCHIGA BERILDI {A.fmt_som(amount)} · {u["full_name"]}\n{actor.name} · tasdiq kutilmoqda')
        return fid


def accept(actor, float_id):
    """The clerk: “OLDIM” — the money is in his box now. A second tap does nothing."""
    with tx() as db:
        f = db.execute('SELECT * FROM tally_floats WHERE id=?', (float_id,)).fetchone()
        if not f or f['kind'] != 'BERISH':
            raise UserError('Bunday pul topilmadi.')
        if not actor or f['tally_id'] != actor.user_id:
            raise UserError('Bu pulni faqat olgan hisobchining o‘zi tasdiqlaydi.')
        if f['status'] == 'QABUL':
            return {'already': True, 'amount': f['amount']}
        if f['status'] != 'KUTILMOQDA':
            raise UserError('Bu pul bekor qilingan (kassir qaytarib olgan).')
        ts = now_str()
        n = db.execute("UPDATE tally_floats SET status='QABUL', confirmed_by=?, confirmed_at=? WHERE id=? AND status='KUTILMOQDA'",
                       (actor.user_id, ts, float_id)).rowcount
        if n != 1:
            return {'already': True, 'amount': f['amount']}
        giver = db.execute('SELECT full_name FROM users WHERE id=?', (f['created_by'],)).fetchone()
        in_id, _ = A.book_cash(db, actor, direction='IN', category='xfer_in', amount=f['amount'], cashbox_id=f['to_box'],
                               counterparty=giver['full_name'] if giver else 'Kassir', note='Kassirdan olindi', feed=False)
        db.execute('UPDATE tally_floats SET in_entry_id=? WHERE id=?', (in_id, float_id))
        audit(db, actor, 'CONFIRM', 'tally_float', float_id, old={'status': 'KUTILMOQDA'}, new={'status': 'QABUL'})
        return {'already': False, 'amount': f['amount']}


def cancel(actor, float_id, reason):
    """Cashier: money given but not yet confirmed comes back to the cashier's box (the clerk never took it)."""
    _need(actor, 'hkassa.give')
    reason = clean_text(reason, 200)
    if len(reason or '') < 3:
        raise UserError('Sababini yozing.')
    with tx() as db:
        f = db.execute('SELECT * FROM tally_floats WHERE id=?', (float_id,)).fetchone()
        if not f or f['kind'] != 'BERISH' or f['status'] != 'KUTILMOQDA':
            raise UserError('Faqat hisobchi hali “OLDIM” demagan pulni qaytarib olish mumkin.')
        n = db.execute("""UPDATE tally_floats SET status='BEKOR', void_reason=?, voided_by=?, voided_at=?
                          WHERE id=? AND status='KUTILMOQDA'""", (reason, actor.user_id, now_str(), float_id)).rowcount
        if n != 1:
            raise UserError('Hisobchi pulni hozirgina tasdiqladi — endi kechqurun topshiradi.')
        u = _tally(db, f['tally_id'])
        in_id, _ = A.book_cash(db, actor, direction='IN', category='xfer_in', amount=f['amount'], cashbox_id=f['from_box'],
                               counterparty=u['full_name'], note=f'Hisobchiga berilgan pul qaytdi: {reason}', feed=False)
        db.execute('UPDATE tally_floats SET in_entry_id=? WHERE id=?', (in_id, float_id))
        audit(db, actor, 'VOID', 'tally_float', float_id, old={'status': 'KUTILMOQDA'}, new={'status': 'BEKOR'}, reason=reason)


def receive(actor, tally_id, counted, note='', client_uuid=None):
    """Evening: the clerk hands back what is left; the cashier counts it. Expected vs counted → Farq (stays on his box)."""
    _need(actor, 'hkassa.give')
    counted = _amount(counted)
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT * FROM tally_floats WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return {'expected': dup['expected'], 'counted': dup['amount'], 'diff': (dup['expected'] or 0) - dup['amount']}
        u = _tally(db, tally_id)
        if not u['cashbox_id']:
            raise UserError(f'{u["full_name"]} da kassa yo‘q.')
        expected = A.box_balance(db, u['cashbox_id'])
        if expected <= 0:
            raise UserError(f'{u["full_name"]} qo‘lida tizim bo‘yicha pul yo‘q.')
        if counted > expected:
            raise UserError(f'Tizim bo‘yicha {u["full_name"]} qo‘lida {A.fmt_som(expected)} bo‘lishi kerak — '
                            f'{A.fmt_som(counted)} ko‘p. Qayta sanang: talonni skanerlamay pul bergan bo‘lishi mumkin emasmi?')
        dst = A._cashbox(db, actor, None)
        if dst == u['cashbox_id']:
            raise UserError('Pulni kassirning kassasiga qabul qiling.')
        out_id, _ = A.book_cash(db, actor, direction='OUT', category='xfer_out', amount=counted, cashbox_id=u['cashbox_id'],
                                counterparty='Kassir', note='Kassirga topshirildi', feed=False)
        in_id, _ = A.book_cash(db, actor, direction='IN', category='xfer_in', amount=counted, cashbox_id=dst,
                               counterparty=u['full_name'], note=clean_text(f'Hisobchi topshirdi · {note}'.rstrip(' ·'), 200),
                               feed=False)
        ts = now_str()
        fid = db.execute('''INSERT INTO tally_floats(tally_id, kind, status, day, amount, expected, from_box, to_box, out_entry_id,
                                in_entry_id, note, client_uuid, created_by, created_at, confirmed_by, confirmed_at)
                            VALUES (?, 'TOPSHIRISH', 'QABUL', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                         (tally_id, today_str(), counted, expected, u['cashbox_id'], dst, out_id, in_id, clean_text(note, 200),
                          client_uuid, actor.user_id, ts, actor.user_id, ts)).lastrowid
        diff = expected - counted
        audit(db, actor, 'CREATE', 'tally_float', fid,
              new={'kind': 'TOPSHIRISH', 'from': u['full_name'], 'expected': expected, 'counted': counted, 'farq': diff})
        from .reporting import feed
        feed(db, f'hkassa:{fid}', f'💵 HISOBCHI TOPSHIRDI {A.fmt_som(counted)} · {u["full_name"]}'
             + (f'\n⚠ Farq {A.fmt_som(diff)} (tizimda {A.fmt_som(expected)})' if diff else '\n✓ Farqsiz') + f'\n{actor.name}')
        return {'expected': expected, 'counted': counted, 'diff': diff}


def _row(db, u, day):
    box = u['cashbox_id']
    hand = A.box_balance(db, box) if box else 0
    opening = A.box_balance(db, box, before_day=day) if box else 0
    fl = db.execute('SELECT * FROM tally_floats WHERE tally_id=? AND day=? ORDER BY id', (u['id'], day)).fetchall()
    given = sum(f['amount'] for f in fl if f['kind'] == 'BERISH' and f['status'] == 'QABUL')
    pending = [dict(f) for f in fl if f['kind'] == 'BERISH' and f['status'] == 'KUTILMOQDA']
    back = [f for f in fl if f['kind'] == 'TOPSHIRISH']
    returned = sum(f['amount'] for f in back)
    farq = sum((f['expected'] or 0) - f['amount'] for f in back)
    paid = db.execute('''SELECT COALESCE(SUM(CASE WHEN category='worker_pay' THEN amount
                                               WHEN category='refund_worker_pay' THEN -amount END),0) s,
                                COUNT(CASE WHEN category='worker_pay' THEN 1 END) n
                         FROM cash_entries WHERE cashbox_id=? AND entry_date=? AND voided_at IS NULL''', (box, day)).fetchone() \
        if box else {'s': 0, 'n': 0}
    talons = db.execute("SELECT COUNT(*) n FROM tezpul_talons WHERE paid_by=? AND pay_date=? AND status='TOLANDI'",
                        (u['id'], day)).fetchone()['n']
    if pending:
        state = 'kutmoqda'
    elif hand > 0 and back:
        state = 'farq'
    elif hand > 0:
        state = 'qolida'
    elif back or given:
        state = 'topshirdi'
    else:
        state = 'boshlanmagan'
    return {'id': u['id'], 'name': u['full_name'] or u['username'], 'on': bool(u['pays_cash']), 'box': box,
            'opening': opening, 'given': given, 'pending': pending, 'pending_sum': sum(p['amount'] for p in pending),
            'paid': paid['s'], 'paid_n': talons or paid['n'], 'returned': returned, 'farq': farq, 'hand': hand,
            'state': state, 'moves': [dict(f) for f in fl]}


STATES = {'boshlanmagan': ('Bugun pul olmagan', 'muted'), 'kutmoqda': ('“OLDIM” kutilmoqda', 'amber'),
          'qolida': ('Pul qo‘lida — ishlayapti', 'blue'), 'topshirdi': ('Topshirdi ✓', 'green'),
          'farq': ('Topshirdi, lekin farq bor', 'red')}


def overview(day=None):
    """Every clerk who pays (or held money that day): given, paid with talons, handed back, in hand, Farq."""
    from .db import get_db
    db = get_db()
    day = day or today_str()
    users = db.execute("""SELECT * FROM users WHERE role='tally' AND (pays_cash=1 OR id IN
                          (SELECT tally_id FROM tally_floats WHERE day=?)) ORDER BY full_name""", (day,)).fetchall()
    rows = [_row(db, u, day) for u in users]
    tot = {k: sum(r[k] for r in rows) for k in ('given', 'pending_sum', 'paid', 'paid_n', 'returned', 'farq', 'hand')}
    return {'day': day, 'rows': rows, 'tot': tot}


def mine(actor, day=None):
    """The clerk's own card: what he got, paid, has in hand; money waiting for his “OLDIM”."""
    from .db import get_db
    db = get_db()
    day = day or today_str()
    u = db.execute('SELECT * FROM users WHERE id=?', (actor.user_id,)).fetchone()
    if not u or not pays_cash(u):
        return None
    r = _row(db, u, day)
    r['last'] = [dict(x) for x in db.execute('''SELECT t.number, t.amount, t.kg, t.paid_at FROM tezpul_talons t
                                                 WHERE t.paid_by=? AND t.pay_date=? AND t.status='TOLANDI'
                                                 ORDER BY t.paid_at DESC LIMIT 8''', (u['id'], day))]
    return r


def candidates():
    """Field clerks the admin may switch on."""
    return q("SELECT id, full_name, username, pays_cash, cashbox_id FROM users WHERE role='tally' AND active=1 ORDER BY full_name")


def tick():
    """From the outbox worker: after hkassa_remind_time (21:00) once a day — clerks who still hold money go to the
    rahbar and the cashier (money must come back every evening)."""
    from .db import get_db
    from .yordamchi import _send
    at = (get_setting('hkassa_remind_time') or '21:00').strip()
    day = today_str()
    if now_str()[11:16] < at or (get_setting('hkassa_reminded') or '') == day:
        return False
    db = get_db()
    held = [r for r in overview(day)['rows'] if r['hand'] > 0 or r['pending']]
    with tx(db):
        if held:
            lines = [f'• {r["name"]}: qo‘lida {A.fmt_som(r["hand"])}' + (f' · tasdiqlanmagan {A.fmt_som(r["pending_sum"])}'
                                                                          if r['pending'] else '') for r in held]
            _send(db, 'hkassa_remind', f'hkassa-remind:{day}', '💵 Hisobchilar pulni hali topshirmagan:\n' + '\n'.join(lines)
                  + '\n\nKechqurun qolgan pul kassirga topshirilishi kerak.', ('admin', 'manager', 'accountant', 'cashier'))
        db.execute("INSERT INTO settings(key, value, updated_at) VALUES ('hkassa_reminded', ?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (day, now_str()))
    return bool(held)
