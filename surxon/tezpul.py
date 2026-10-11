"""SURXON TEZ-PUL: hand picking paid in cash by numbered paper talons.

1. The notebook: “talon № — name” (names never go into the phone).
2. Field (terim hisobchisi, two steps): scan the talon → type its kg → SAQLASH. The kg is written as an ordinary
   hand-picking weighing (Qo‘l terimi) of the open trailer trip, priced at the trip's rate, so the punkt check, the
   trip documents and the reports see it like any other weighing. The date is the day it was scanned — talons are
   printed once, without a date. Each talon is its own card (“Talon 000274”, hidden from the name lists);
   “👥 Starshi” makes it a group talon (a whole team's kg, up to the group limit), paid to the starshi.
3. Kassa (two steps): scan → the amount owed on that talon appears → PUL BERILDI. The money leaves the existing kassa
   as an ordinary worker payment (payment order + PAY- cash entry), so the worker balances stay exact.
One talon is paid once, ever: the status flips in one guarded UPDATE inside an IMMEDIATE transaction (a second scan,
a photocopy or two cashiers at the same second pay once). The QR holds only the number and a random code stored with
the talon (no kg, no money): a home-made QR with a real number but a wrong code is refused.
"""
import secrets

from .db import get_db, q, tx
from .security import audit
from .utils import UserError, name_key, now_str, today_str

MAX_BATCH = 2000
PREFIX = 'SPX-T'
STATUS = {
    'BOSH': ('Kg yozilmagan', 'b-gray'),
    'KUTILMOQDA': ('Eski tartib — bekor qiling', 'b-orange'),
    'TAYYOR': ('To‘lovga tayyor', 'b-blue'),
    'TOLANDI': ('To‘landi', 'b-green'),
    'BEKOR': ('Bekor qilingan', 'b-red'),
}


class Refused(UserError):
    """A scan that must show a big coloured screen (already paid, fake, cancelled, no kg yet) — carries what to show."""
    def __init__(self, kind, message, talon=None):
        super().__init__(message)
        self.kind, self.talon = kind, talon


def _need(actor, perm):
    if not actor or not actor.can(perm):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')


def number_text(n):
    return f'{int(n):06d}'


def qr_text(t):
    return f'{PREFIX}:{number_text(t["number"])}:{t["code"]}'


def in_use():
    return bool(q('SELECT 1 FROM tezpul_talons LIMIT 1', one=True))


# ------------------------------------------------------------------ issuing (admin)

def create_batch(actor, count, brigadier_id=None):
    """New talons numbered after the last one (000001 …). Returns (batch, first, last)."""
    _need(actor, 'tezpul.manage')
    try:
        count = int(str(count).replace(' ', ''))
    except ValueError:
        raise UserError('Nechta talon kerakligini raqam bilan yozing (masalan 1000).')
    if not 1 <= count <= MAX_BATCH:
        raise UserError(f'Bir martada 1 dan {MAX_BATCH} tagacha talon chiqarish mumkin.')
    with tx() as db:
        if brigadier_id and not db.execute('SELECT 1 FROM brigadiers WHERE id=?', (brigadier_id,)).fetchone():
            raise UserError('Brigada topilmadi.')
        last = db.execute('SELECT MAX(number) m, MAX(batch) b FROM tezpul_talons').fetchone()
        start, batch, now = (last['m'] or 0) + 1, (last['b'] or 0) + 1, now_str()
        db.executemany('INSERT INTO tezpul_talons(number, code, batch, brigadier_id, created_by, created_at) VALUES (?,?,?,?,?,?)',
                       [(n, secrets.token_hex(4).upper(), batch, brigadier_id or None, actor.user_id, now)
                        for n in range(start, start + count)])
        audit(db, actor, 'CREATE', 'tezpul_talons', batch, new={'count': count, 'from': number_text(start),
                                                                 'to': number_text(start + count - 1), 'brigadier_id': brigadier_id})
    return batch, number_text(start), number_text(start + count - 1)


def void(actor, number, reason):
    """Lost / torn / stolen blank talon: never usable again."""
    _need(actor, 'tezpul.manage')
    reason = (reason or '').strip()
    if not reason:
        raise UserError('Bekor qilish sababini yozing.')
    with tx() as db:
        t = db.execute('SELECT * FROM tezpul_talons WHERE number=?', (_num(number),)).fetchone()
        if not t:
            raise UserError('Bunday talon yo‘q.')
        if t['status'] == 'TOLANDI':
            raise UserError(f'№{number_text(t["number"])} allaqachon to‘langan — bekor qilib bo‘lmaydi.')
        if t['status'] == 'TAYYOR' and _live_harvest(db, t):
            raise UserError(f'№{number_text(t["number"])} ga kg yozilgan, puli hali berilmagan. Talon yo‘qolgan bo‘lsa — '
                            f'pulni daftar bo‘yicha “Mening kassam → Ishchiga pul berish” da “Talon {number_text(t["number"])}” '
                            'ni topib bering; kg xato yozilgan bo‘lsa — avval o‘sha tortishni bekor qiling.')
        db.execute("UPDATE tezpul_talons SET status='BEKOR', void_reason=?, voided_by=?, voided_at=? WHERE id=?",
                   (reason[:200], actor.user_id, now_str(), t['id']))
        audit(db, actor, 'VOID', 'tezpul_talon', t['id'], old={'status': t['status']}, new={'reason': reason[:200]})


# ------------------------------------------------------------------ scanning

def _num(text):
    digits = ''.join(ch for ch in str(text or '') if ch.isdigit())
    return int(digits) if digits and len(digits) <= 9 else None


def parse(code, manual_code=''):
    """(number, code) from a scanned QR “SPX-T:000274:7C41E0B9” or from the number + code typed by hand."""
    text = (code or '').strip().upper()
    if text.startswith(PREFIX + ':'):
        parts = text.split(':')
        if len(parts) == 3:
            return _num(parts[1]), parts[2].strip()
        return None, None
    return _num(text), (manual_code or '').strip().upper().replace(' ', '').replace('-', '')


def _log(db, actor, kind, code, talon_id=None, note=''):
    db.execute('INSERT INTO tezpul_events(created_at, user_id, code, kind, talon_id, note) VALUES (?,?,?,?,?,?)',
               (now_str(), actor.user_id if actor else None, (code or '')[:60], kind, talon_id, note[:200]))


def _live_harvest(db, t):
    if not t['harvest_id']:
        return None
    h = db.execute('SELECT * FROM harvests WHERE id=?', (t['harvest_id'],)).fetchone()
    return h if h and not h['voided_at'] else None


def _payable(db, t):
    """What the kassa gives for this talon now: what its card is still owed (0 once paid in any way)."""
    from .accounting import season_of
    from .wallet import worker_figures
    if not t['worker_id']:
        return 0
    return worker_figures(db, season_of(db), t['worker_id'])['payable']


def _view(db, t):
    names = {}
    for col in ('paid_by', 'entered_by'):
        if t[col]:
            u = db.execute('SELECT full_name FROM users WHERE id=?', (t[col],)).fetchone()
            names[col] = u['full_name'] if u else None
    b = db.execute('SELECT name FROM brigadiers WHERE id=?', (t['brigadier_id'],)).fetchone() if t['brigadier_id'] else None
    amount = _payable(db, t) if t['status'] == 'TAYYOR' else t['amount']
    return {'number': number_text(t['number']), 'status': t['status'], 'status_label': STATUS[t['status']][0],
            'kind': t['kind'] or 'odam', 'kg': t['kg'], 'rate': t['rate'], 'amount': amount,
            'brigade': b['name'] if b else None, 'paid_at': t['paid_at'], 'paid_by': names.get('paid_by'),
            'entered_at': t['entered_at'], 'entered_by': names.get('entered_by'), 'void_reason': t['void_reason']}


def _find(db, code, manual_code=''):
    number, sig = parse(code, manual_code)
    t = db.execute('SELECT * FROM tezpul_talons WHERE number=?', (number,)).fetchone() if number else None
    if not t or not sig or not secrets.compare_digest(sig, t['code']):
        raise Refused('soxta', 'TALON HAQIQIY EMAS — soxta yoki bizniki emas.' if number and sig else
                      'Talon tanilmadi. QR ni qayta skanerlang yoki raqam va kodni to‘g‘ri yozing.',
                      {'id': t['id']} if t else None)
    if t['status'] == 'BEKOR':
        raise Refused('bekor', f'№{number_text(t["number"])} BEKOR QILINGAN: {t["void_reason"] or ""}.',
                      {**_view(db, t), 'id': t['id']})
    if t['status'] == 'TOLANDI':
        raise Refused('tolangan', f'№{number_text(t["number"])} ALLAQACHON TO‘LANGAN.', {**_view(db, t), 'id': t['id']})
    if t['status'] == 'KUTILMOQDA' or (t['status'] == 'TAYYOR' and not t['worker_id']):
        raise UserError(f'№{number_text(t["number"])} eski tartibda yozilgan — rahbar uni bekor qilsin.')
    return t


def _logged(actor, code, fn):
    """Run fn; a refused scan (fake, paid, cancelled, written twice) goes to the rahbar's error list after the rollback."""
    try:
        return fn()
    except Refused as err:
        if err.kind in ('soxta', 'bekor', 'tolangan', 'kiritilgan'):
            with tx() as db:
                _log(db, actor, {'tolangan': 'takror'}.get(err.kind, err.kind), code, (err.talon or {}).get('id'))
        raise


def _kg(value):
    try:
        kg = round(float(str(value or '').replace(' ', '').replace(',', '.')), 1)
    except ValueError:
        raise UserError('Kg ni raqam bilan yozing (masalan 37,5).')
    if kg <= 0:
        raise UserError('Talondagi kg ni yozing.')
    return kg


# ------------------------------------------------------------------ the field: scan → kg → SAQLASH

def _entered_check(db, t):
    if t['status'] == 'TAYYOR' and _live_harvest(db, t):
        v = _view(db, t)
        raise Refused('kiritilgan', f'№{v["number"]} ga {t["kg"]:g} kg allaqachon yozilgan '
                                    f'({(t["entered_at"] or "")[11:16]}, {v["entered_by"] or ""}). Bir talon — bir marta.',
                      {**v, 'id': t['id']})


def field_lookup(actor, code, manual_code=''):
    _need(actor, 'harvest.write')

    def go():
        db = get_db()
        t = _find(db, code, manual_code)
        _entered_check(db, t)
        return _view(db, t)
    return _logged(actor, code, go)


def enter(actor, load_id, code, kg, *, group=False, manual_code='', client_uuid=None):
    """The terim hisobchisi saved one talon: its kg becomes a hand weighing of the open trip (Qo‘l terimi)."""
    _need(actor, 'harvest.write')
    return _logged(actor, code, lambda: _enter(actor, load_id, code, kg, group, manual_code, client_uuid))


def _enter(actor, load_id, code, kg, group, manual_code, client_uuid):
    from .services import _new_worker, add_harvest
    with tx() as db:
        if client_uuid:
            dup = db.execute('''SELECT t.* FROM tezpul_talons t JOIN harvests h ON h.id=t.harvest_id
                                WHERE h.client_uuid=?''', (client_uuid,)).fetchone()
            if dup:          # the same save sent twice (bad network): already written
                return {'already': True, **_view(db, dup)}
        t = _find(db, code, manual_code)
        _entered_check(db, t)
        kg = _kg(kg)
        load = db.execute('SELECT * FROM trailer_loads WHERE id=?', (load_id,)).fetchone()
        if not load:
            raise UserError('Telashka topilmadi.')
        name = f'Talon {number_text(t["number"])}' + (' (starshi)' if group else '')
        wid = t['worker_id']
        if wid:
            db.execute('UPDATE workers SET full_name=?, name_key=?, group_size=?, active=1 WHERE id=?',
                       (name, name_key(name), 1 if group else None, wid))
        else:
            wid = _new_worker(db, actor, name, load['brigadier_id'])
            db.execute('UPDATE workers SET talon_no=?, group_size=? WHERE id=?', (t['number'], 1 if group else None, wid))
        try:
            hid, _ = add_harvest(actor, load_id=load_id, method='hand', kg=kg, worker_id=wid, client_uuid=client_uuid,
                                 confirm_duplicate=True)
        except UserError as err:
            if 'chegara' in str(err) and not group:
                raise UserError(f'{kg:g} kg — bitta odamga juda ko‘p. Bu guruh (starshi) taloni bo‘lsa — '
                                '“👥 Starshi” ni tanlab qayta saqlang.')
            raise
        h = db.execute('SELECT rate, amount FROM harvests WHERE id=?', (hid,)).fetchone()
        db.execute('''UPDATE tezpul_talons SET status='TAYYOR', kind=?, worker_id=?, harvest_id=?, load_id=?, brigadier_id=?,
                          kg=?, rate=?, amount=?, entered_by=?, entered_at=? WHERE id=?''',
                   ('guruh' if group else 'odam', wid, hid, load_id, load['brigadier_id'], kg, h['rate'], h['amount'],
                    actor.user_id, now_str(), t['id']))
        audit(db, actor, 'TEZPUL_KG', 'tezpul_talon', t['id'],
              new={'number': t['number'], 'kg': kg, 'kind': 'guruh' if group else 'odam', 'load_id': load_id, 'harvest_id': hid})
        return {'already': False, **_view(db, db.execute('SELECT * FROM tezpul_talons WHERE id=?', (t['id'],)).fetchone())}


def trip_counter(load_id):
    r = q('''SELECT COUNT(*) n, COALESCE(SUM(t.kg),0) kg FROM tezpul_talons t JOIN harvests h ON h.id=t.harvest_id
             WHERE t.load_id=? AND h.voided_at IS NULL''', (load_id,), one=True)
    return dict(r)


# ------------------------------------------------------------------ the kassa: scan → PUL BERILDI

def _ready(db, t):
    if t['status'] == 'BOSH' or not _live_harvest(db, t):
        raise Refused('kgsiz', f'№{number_text(t["number"])}: KG HALI YOZILMAGAN — terim hisobchisi talonni skanerlab '
                               'kg ni kiritsin. Pul berilmaydi.', {**_view(db, t), 'id': t['id']})


def lookup(actor, code, manual_code=''):
    """What the cashier sees after the scan."""
    _need(actor, 'payouts.pay')

    def go():
        db = get_db()
        t = _find(db, code, manual_code)
        _ready(db, t)
        v = _view(db, t)
        if not v['amount']:
            raise Refused('tolangan', f'№{v["number"]} bo‘yicha to‘lanadigan pul qolmagan.', {**v, 'id': t['id']})
        return v
    return _logged(actor, code, go)


def pay(actor, code, *, manual_code='', client_uuid=None):
    """“PUL BERILDI”: the whole amount owed on the talon, from the cashier's own kassa."""
    _need(actor, 'payouts.pay')
    return _logged(actor, code, lambda: _pay(actor, code, manual_code, client_uuid))


def _pay(actor, code, manual_code, client_uuid):
    from . import accounting as A
    from .db import doc_number
    from .wallet import worker_figures
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT * FROM tezpul_talons WHERE pay_uuid=?', (client_uuid,)).fetchone()
            if dup:          # the same tap sent twice (bad network): the first one already did it
                return {'paid': True, 'already': True, **_view(db, dup)}
        t = _find(db, code, manual_code)
        _ready(db, t)
        year = A.season_of(db)
        f = worker_figures(db, year, t['worker_id'])
        amount = f['payable']
        if amount <= 0:
            if f['uncalc_kg']:
                raise UserError('Bu telashkada qo‘l terimi narxi yozilmagan — summa hisoblanmagan. Buxgalterga ayting.')
            raise Refused('tolangan', f'№{number_text(t["number"])} bo‘yicha to‘lanadigan pul qolmagan.', {'id': t['id']})
        box = A._cashbox(db, actor, None)
        ts = now_str()
        # the guard: only one transaction can move this talon to TOLANDI
        n = db.execute('''UPDATE tezpul_talons SET status='TOLANDI', amount=?, paid_by=?, paid_at=?, pay_date=?, cashbox_id=?,
                              pay_uuid=? WHERE id=? AND status='TAYYOR' ''',
                       (amount, actor.user_id, ts, today_str(), box, client_uuid, t['id'])).rowcount
        if n != 1:
            raise Refused('tolangan', f'№{number_text(t["number"])} ALLAQACHON TO‘LANGAN.', {'id': t['id']})
        no = doc_number(db, 'PAY', year)
        note = f'TEZ-PUL talon №{number_text(t["number"])} · {t["kg"]:g} kg'
        pid = db.execute('''INSERT INTO payouts(doc_no, season_year, kind, purpose, worker_id, amount, note, client_uuid,
                                cashbox_id, prepared_by, prepared_at, status, paid_by, paid_at)
                            VALUES (?,?,'worker','pay',?,?,?,?,?,?,?,'BERILDI',?,?)''',
                         (no, year, t['worker_id'], amount, note, client_uuid, box, actor.user_id, ts, actor.user_id, ts)).lastrowid
        cid, _ = A.book_cash(db, actor, direction='OUT', category='worker_pay', amount=amount, cashbox_id=box,
                             worker_id=t['worker_id'], payout_id=pid, counterparty=f['name'], note=note, doc_no=no, feed=False)
        db.execute('UPDATE payouts SET cash_entry_id=? WHERE id=?', (cid, pid))
        db.execute('UPDATE tezpul_talons SET cash_entry_id=? WHERE id=?', (cid, t['id']))
        audit(db, actor, 'TEZPUL_PAID', 'tezpul_talon', t['id'],
              new={'number': t['number'], 'kg': t['kg'], 'amount': amount, 'cashbox_id': box, 'cash_entry_id': cid,
                   'payout_id': pid, 'doc_no': no})
        A.mirror_payout(db, pid)
        A.mirror_worker(db, t['worker_id'], year)
        return {'paid': True, 'already': False, 'doc_no': no,
                **_view(db, db.execute('SELECT * FROM tezpul_talons WHERE id=?', (t['id'],)).fetchone())}


# ------------------------------------------------------------------ the rahbar

LIVE = "t.harvest_id IS NOT NULL AND EXISTS (SELECT 1 FROM harvests h WHERE h.id=t.harvest_id AND h.voided_at IS NULL)"


def panel(day=None):
    """The rahbar's figures for one day (default today) + the whole season."""
    day = day or today_str()
    db = get_db()
    entered = db.execute(f'''SELECT COUNT(*) n, COALESCE(SUM(t.kg),0) kg, COALESCE(SUM(t.amount),0) amount FROM tezpul_talons t
                             WHERE substr(t.entered_at,1,10)=? AND {LIVE}''', (day,)).fetchone()
    paid = db.execute('''SELECT COUNT(*) n, COALESCE(SUM(kg),0) kg, COALESCE(SUM(amount),0) amount FROM tezpul_talons
                         WHERE status='TOLANDI' AND pay_date=?''', (day,)).fetchone()
    unpaid = db.execute(f'''SELECT COUNT(*) n, COALESCE(SUM(t.kg),0) kg, COALESCE(SUM(t.amount),0) amount FROM tezpul_talons t
                            WHERE t.status='TAYYOR' AND {LIVE}''').fetchone()
    season = db.execute('''SELECT COUNT(*) n, COALESCE(SUM(kg),0) kg, COALESCE(SUM(amount),0) amount FROM tezpul_talons
                           WHERE status='TOLANDI' ''').fetchone()
    counts = {r['status']: r['n'] for r in db.execute('SELECT status, COUNT(*) n FROM tezpul_talons GROUP BY status')}
    punkt = {r['b']: r['kg'] for r in db.execute(
        '''SELECT tl.brigadier_id b, SUM(nr.accepted_kg) kg FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id
           JOIN trailer_loads tl ON tl.id=wb.load_id
           WHERE wb.status='QABUL' AND tl.method='hand' AND nr.received_date=? GROUP BY tl.brigadier_id''', (day,))}
    brigades = [dict(r, punkt_kg=punkt.get(r['bid'])) for r in db.execute(
        f'''SELECT t.brigadier_id bid, COALESCE(b.name, 'Brigadasiz') name, COUNT(*) n, SUM(t.kg) kg, SUM(t.amount) amount,
                   SUM(CASE WHEN t.status='TOLANDI' THEN t.amount ELSE 0 END) paid,
                   SUM(CASE WHEN t.kind='guruh' THEN 1 ELSE 0 END) groups
            FROM tezpul_talons t LEFT JOIN brigadiers b ON b.id=t.brigadier_id
            WHERE substr(t.entered_at,1,10)=? AND {LIVE} GROUP BY t.brigadier_id ORDER BY kg DESC''', (day,))]
    from .accounting import box_balance
    cashiers = [dict(r, balance=box_balance(db, r['cashbox_id'])) for r in db.execute(
        '''SELECT t.paid_by, u.full_name name, t.cashbox_id, cb.name box, COUNT(*) n, SUM(t.kg) kg, SUM(t.amount) amount,
                  MAX(t.paid_at) last_at
           FROM tezpul_talons t LEFT JOIN users u ON u.id=t.paid_by LEFT JOIN cashboxes cb ON cb.id=t.cashbox_id
           WHERE t.status='TOLANDI' AND t.pay_date=? GROUP BY t.paid_by, t.cashbox_id ORDER BY amount DESC''', (day,))]
    clerks = db.execute(f'''SELECT u.full_name name, COUNT(*) n, SUM(t.kg) kg FROM tezpul_talons t
                            LEFT JOIN users u ON u.id=t.entered_by
                            WHERE substr(t.entered_at,1,10)=? AND {LIVE} GROUP BY t.entered_by ORDER BY n DESC''', (day,)).fetchall()
    errors = db.execute('''SELECT e.*, u.full_name name FROM tezpul_events e LEFT JOIN users u ON u.id=e.user_id
                           WHERE substr(e.created_at,1,10)=? ORDER BY e.id DESC LIMIT 50''', (day,)).fetchall()
    recent = db.execute('''SELECT t.*, b.name brigade, u.full_name paid_name FROM tezpul_talons t
                           LEFT JOIN brigadiers b ON b.id=t.brigadier_id LEFT JOIN users u ON u.id=t.paid_by
                           WHERE t.status='TOLANDI' AND t.pay_date=? ORDER BY t.paid_at DESC LIMIT 30''', (day,)).fetchall()
    return {'day': day, 'entered': dict(entered), 'paid': dict(paid), 'unpaid': dict(unpaid), 'season': dict(season),
            'counts': counts, 'brigades': brigades, 'cashiers': cashiers, 'clerks': clerks, 'errors': errors,
            'recent': recent, 'issued': sum(counts.values()), 'blank': counts.get('BOSH', 0),
            'legacy': counts.get('KUTILMOQDA', 0)}


def today_counter(actor):
    """The small line under the cashier's camera: how many talons this cashier paid today."""
    r = q('''SELECT COUNT(*) n, COALESCE(SUM(kg),0) kg, COALESCE(SUM(amount),0) amount FROM tezpul_talons
             WHERE status='TOLANDI' AND pay_date=? AND paid_by=?''', (today_str(), actor.user_id), one=True)
    return dict(r)


def talons_for_print(batch=None, first=None, last=None):
    if batch:
        return q('SELECT * FROM tezpul_talons WHERE batch=? ORDER BY number', (batch,))
    return q('SELECT * FROM tezpul_talons WHERE number BETWEEN ? AND ? ORDER BY number', (_num(first) or 0, _num(last) or 0))


def batches():
    return q('''SELECT t.batch, MIN(t.number) a, MAX(t.number) b, COUNT(*) n, MIN(t.created_at) created_at,
                       SUM(t.status='TOLANDI') paid, SUM(t.status='BEKOR') void, SUM(t.status='BOSH') blank
                FROM tezpul_talons t GROUP BY t.batch ORDER BY t.batch DESC''')
