"""SURXON TEZ-PUL: hand-picking paid in cash by numbered paper talons — no phone in the field.

In the field the scale keeper weighs, writes the kg on the talon (and “talon № — name” in the notebook) and gives the
talon to the picker. At the kassa the cashier scans the talon's QR, types the kg written on it and presses PUL BERILDI:
the money leaves the existing kassa (category “Qo‘l terim (talon)”), the kg is counted as hand-picked cotton. One talon is
paid once, ever — the status flips in one guarded UPDATE inside an IMMEDIATE transaction, so a second scan, a photocopy or
two cashiers pressing at the same second pay once. The QR holds only the number and a random code stored with the talon
(no kg, no money): a home-made QR with a real number but a wrong code is refused. More kg on one talon than
tezpul_max_kg waits for the rahbar's approval; after that the kg is fixed and the cashier cannot change it.
"""
import secrets

from .db import get_db, q, scalar, tx
from .security import audit
from .settings import get_float
from .utils import UserError, now_str, today_str

MAX_BATCH = 2000
PREFIX = 'SPX-T'
STATUS = {
    'BOSH': ('Yangi', 'b-gray'),
    'KUTILMOQDA': ('Rahbar tasdig‘ini kutmoqda', 'b-orange'),
    'TAYYOR': ('To‘lovga tayyor', 'b-blue'),
    'TOLANDI': ('To‘landi', 'b-green'),
    'BEKOR': ('Bekor qilingan', 'b-red'),
}


class Refused(UserError):
    """A scan that must show a big red screen (already paid, fake, cancelled) — carries what to show."""
    def __init__(self, kind, message, talon=None):
        super().__init__(message)
        self.kind, self.talon = kind, talon


def _need(actor, *roles_or_perm):
    if not actor:
        raise UserError('Bu amal uchun huquqingiz yo‘q.')
    perm = roles_or_perm[0]
    if isinstance(perm, str) and '.' in perm:
        if not actor.can(perm):
            raise UserError('Bu amal uchun huquqingiz yo‘q.')
    elif actor.role not in roles_or_perm and actor.role != 'admin':
        raise UserError('Bu amal uchun huquqingiz yo‘q.')


def rate():
    return int(get_float('tezpul_rate_kg', 2000) or 0)


def max_kg():
    return get_float('tezpul_max_kg', 150) or 0


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


def assign_range(actor, first, last, brigadier_id):
    """A pack of talons handed to a brigade (for the daily check by brigade). Paid ones keep their brigade."""
    _need(actor, 'tezpul.manage')
    a, b = _num(first), _num(last)
    if not a or not b or b < a:
        raise UserError('Talon raqamlarini to‘g‘ri yozing: dan … gacha.')
    with tx() as db:
        if brigadier_id and not db.execute('SELECT 1 FROM brigadiers WHERE id=?', (brigadier_id,)).fetchone():
            raise UserError('Brigada topilmadi.')
        n = db.execute("UPDATE tezpul_talons SET brigadier_id=? WHERE number BETWEEN ? AND ? AND status<>'TOLANDI'",
                       (brigadier_id or None, a, b)).rowcount
        audit(db, actor, 'ASSIGN', 'tezpul_talons', None, new={'from': a, 'to': b, 'brigadier_id': brigadier_id, 'count': n})
    return n


def void(actor, number, reason):
    """Lost / torn / stolen talon: never payable again. A paid one is not voided here (its money is in the kassa)."""
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
        db.execute("UPDATE tezpul_talons SET status='BEKOR', void_reason=?, voided_by=?, voided_at=? WHERE id=?",
                   (reason[:200], actor.user_id, now_str(), t['id']))
        audit(db, actor, 'VOID', 'tezpul_talon', t['id'], old={'status': t['status']}, new={'reason': reason[:200]})


# ------------------------------------------------------------------ the kassa

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


def _view(db, t):
    paid_name = None
    if t['paid_by']:
        u = db.execute('SELECT full_name FROM users WHERE id=?', (t['paid_by'],)).fetchone()
        paid_name = u['full_name'] if u else None
    b = db.execute('SELECT name FROM brigadiers WHERE id=?', (t['brigadier_id'],)).fetchone() if t['brigadier_id'] else None
    return {'number': number_text(t['number']), 'status': t['status'], 'status_label': STATUS[t['status']][0],
            'kg': t['kg'], 'rate': t['rate'], 'amount': t['amount'], 'brigade': b['name'] if b else None,
            'paid_at': t['paid_at'], 'paid_by': paid_name, 'void_reason': t['void_reason'],
            'kg_fixed': t['status'] in ('TAYYOR', 'KUTILMOQDA'), 'rate_now': rate()}


def _find(db, code, manual_code=''):
    number, sig = parse(code, manual_code)
    t = db.execute('SELECT * FROM tezpul_talons WHERE number=?', (number,)).fetchone() if number else None
    if not t or not sig or not secrets.compare_digest(sig, t['code']):
        raise Refused('soxta', 'TALON HAQIQIY EMAS — soxta yoki bizniki emas. Pul berilmaydi.' if number and sig else
                      'Talon tanilmadi. QR ni qayta skanerlang yoki raqam va kodni to‘g‘ri yozing.',
                      {'id': t['id']} if t else None)
    if t['status'] == 'BEKOR':
        raise Refused('bekor', f'№{number_text(t["number"])} BEKOR QILINGAN: {t["void_reason"] or ""}. Pul berilmaydi.',
                      {**_view(db, t), 'id': t['id']})
    if t['status'] == 'TOLANDI':
        raise Refused('tolangan', f'№{number_text(t["number"])} ALLAQACHON TO‘LANGAN.', {**_view(db, t), 'id': t['id']})
    return t


def _logged(actor, code, fn):
    """Run fn; a refused scan (fake, paid, cancelled) is written to the rahbar's error list after the rollback."""
    try:
        return fn()
    except Refused as err:
        with tx() as db:
            _log(db, actor, {'tolangan': 'takror'}.get(err.kind, err.kind), code, (err.talon or {}).get('id'))
        raise


def lookup(actor, code, manual_code=''):
    """What the cashier sees after the scan (nothing changes except the refused-scan log)."""
    _need(actor, 'payouts.pay')
    return _logged(actor, code, lambda: _view(get_db(), _find(get_db(), code, manual_code)))


def _kg(value):
    try:
        kg = round(float(str(value or '').replace(' ', '').replace(',', '.')), 1)
    except ValueError:
        raise UserError('Kg ni raqam bilan yozing (masalan 37,5).')
    if kg <= 0:
        raise UserError('Talondagi kg ni yozing.')
    if kg > 1000:
        raise UserError(f'{kg:g} kg — bitta talonga juda katta. Raqamni tekshiring.')
    return kg


def pay(actor, code, kg=None, *, manual_code='', client_uuid=None):
    """“PUL BERILDI”. Returns {'paid': True, ...} or {'wait': True, ...} (big kg sent to the rahbar, no money given)."""
    _need(actor, 'payouts.pay')
    return _logged(actor, code, lambda: _pay(actor, code, kg, manual_code, client_uuid))


def _pay(actor, code, kg, manual_code, client_uuid):
    from . import accounting as A
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT * FROM tezpul_talons WHERE pay_uuid=?', (client_uuid,)).fetchone()
            if dup:          # the same tap sent twice (bad network): the first one already did it
                return {'paid': True, 'already': True, **_view(db, dup)}
        t = _find(db, code, manual_code)
        if t['status'] == 'KUTILMOQDA':
            raise UserError(f'№{number_text(t["number"])}: {t["kg"]:g} kg — rahbar tasdig‘ini kutmoqda. Pul hali berilmaydi.')
        if t['status'] == 'TAYYOR':       # approved by the rahbar: the kg is fixed, whatever was typed
            kg = t['kg']
        else:
            kg = _kg(kg)
            limit = max_kg()
            if limit and kg > limit:
                db.execute("UPDATE tezpul_talons SET status='KUTILMOQDA', kg=?, entered_by=?, entered_at=? WHERE id=? AND status='BOSH'",
                           (kg, actor.user_id, now_str(), t['id']))
                audit(db, actor, 'TEZPUL_WAIT', 'tezpul_talon', t['id'], new={'kg': kg, 'limit': limit})
                return {'wait': True, **_view(db, db.execute('SELECT * FROM tezpul_talons WHERE id=?', (t['id'],)).fetchone())}
        r = rate()
        if not r:
            raise UserError('TEZ-PUL narxi (so‘m/kg) sozlanmagan — Admin → Sozlamalar → tezpul_rate_kg.')
        amount = int(round(kg * r))
        box = A._cashbox(db, actor, None)
        ts = now_str()
        # the guard: only one transaction can move this talon to TOLANDI
        n = db.execute('''UPDATE tezpul_talons SET status='TOLANDI', kg=?, rate=?, amount=?, paid_by=?, paid_at=?, pay_date=?,
                              cashbox_id=?, pay_uuid=?, entered_by=COALESCE(entered_by, ?), entered_at=COALESCE(entered_at, ?)
                          WHERE id=? AND status IN ('BOSH','TAYYOR')''',
                       (kg, r, amount, actor.user_id, ts, today_str(), box, client_uuid, actor.user_id, ts, t['id'])).rowcount
        if n != 1:
            raise Refused('tolangan', f'№{number_text(t["number"])} ALLAQACHON TO‘LANGAN.', {'id': t['id']})
        cid, doc_no = A.book_cash(db, actor, direction='OUT', category='tezpul_pay', amount=amount, cashbox_id=box,
                                  counterparty=f'Talon №{number_text(t["number"])}', source='TEZ-PUL',
                                  note=f'{kg:g} kg × {r:,} so‘m'.replace(',', ' '), feed=False)
        db.execute('UPDATE tezpul_talons SET cash_entry_id=? WHERE id=?', (cid, t['id']))
        audit(db, actor, 'TEZPUL_PAID', 'tezpul_talon', t['id'],
              new={'number': t['number'], 'kg': kg, 'rate': r, 'amount': amount, 'cashbox_id': box, 'cash_entry_id': cid})
        return {'paid': True, 'already': False, 'doc_no': doc_no,
                **_view(db, db.execute('SELECT * FROM tezpul_talons WHERE id=?', (t['id'],)).fetchone())}


# ------------------------------------------------------------------ the rahbar

def approve(actor, number, kg=None):
    """Big kg on one talon: the rahbar checks the notebook and approves (may correct the kg). Then the cashier pays it."""
    _need(actor, 'tezpul.manage')
    with tx() as db:
        t = db.execute('SELECT * FROM tezpul_talons WHERE number=?', (_num(number),)).fetchone()
        if not t or t['status'] != 'KUTILMOQDA':
            raise UserError('Bu talon tasdiq kutmayapti.')
        new_kg = _kg(kg) if kg not in (None, '') else t['kg']
        db.execute("UPDATE tezpul_talons SET status='TAYYOR', kg=?, approved_by=?, approved_at=? WHERE id=?",
                   (new_kg, actor.user_id, now_str(), t['id']))
        audit(db, actor, 'TEZPUL_APPROVE', 'tezpul_talon', t['id'], old={'kg': t['kg']}, new={'kg': new_kg})


def reject(actor, number, reason=''):
    """Wrong kg typed: back to a fresh talon (the cashier scans and types again); kept in the audit."""
    _need(actor, 'tezpul.manage')
    with tx() as db:
        t = db.execute('SELECT * FROM tezpul_talons WHERE number=?', (_num(number),)).fetchone()
        if not t or t['status'] != 'KUTILMOQDA':
            raise UserError('Bu talon tasdiq kutmayapti.')
        db.execute("UPDATE tezpul_talons SET status='BOSH', kg=NULL, entered_by=NULL, entered_at=NULL WHERE id=?", (t['id'],))
        audit(db, actor, 'TEZPUL_REJECT', 'tezpul_talon', t['id'], old={'kg': t['kg']}, new={'reason': (reason or '')[:200]})


def panel(day=None):
    """The rahbar's figures for one day (default today) + the season."""
    day = day or today_str()
    db = get_db()
    tot = db.execute('''SELECT COUNT(*) n, COALESCE(SUM(kg),0) kg, COALESCE(SUM(amount),0) amount FROM tezpul_talons
                        WHERE status='TOLANDI' AND pay_date=?''', (day,)).fetchone()
    season = db.execute('''SELECT COUNT(*) n, COALESCE(SUM(kg),0) kg, COALESCE(SUM(amount),0) amount FROM tezpul_talons
                           WHERE status='TOLANDI' ''').fetchone()
    counts = {r['status']: r['n'] for r in db.execute('SELECT status, COUNT(*) n FROM tezpul_talons GROUP BY status')}
    waiting = db.execute('''SELECT t.*, b.name brigade, u.full_name entered_name FROM tezpul_talons t
                            LEFT JOIN brigadiers b ON b.id=t.brigadier_id LEFT JOIN users u ON u.id=t.entered_by
                            WHERE t.status IN ('KUTILMOQDA','TAYYOR') ORDER BY t.entered_at''').fetchall()
    wait_amount = sum(int(round((w['kg'] or 0) * rate())) for w in waiting)
    punkt = {r['b']: r['kg'] for r in db.execute(
        '''SELECT tl.brigadier_id b, SUM(nr.accepted_kg) kg FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id
           JOIN trailer_loads tl ON tl.id=wb.load_id
           WHERE wb.status='QABUL' AND tl.method='hand' AND nr.received_date=? GROUP BY tl.brigadier_id''', (day,))}
    brigades = [dict(r, punkt_kg=punkt.get(r['bid'])) for r in db.execute(
        '''SELECT t.brigadier_id bid, COALESCE(b.name, 'Brigadasiz') name, COUNT(*) n, SUM(t.kg) kg, SUM(t.amount) amount
           FROM tezpul_talons t LEFT JOIN brigadiers b ON b.id=t.brigadier_id
           WHERE t.status='TOLANDI' AND t.pay_date=? GROUP BY t.brigadier_id ORDER BY kg DESC''', (day,))]
    from .accounting import box_balance
    cashiers = [dict(r, balance=box_balance(db, r['cashbox_id'])) for r in db.execute(
        '''SELECT t.paid_by, u.full_name name, t.cashbox_id, cb.name box, COUNT(*) n, SUM(t.kg) kg, SUM(t.amount) amount,
                  MAX(t.paid_at) last_at
           FROM tezpul_talons t LEFT JOIN users u ON u.id=t.paid_by LEFT JOIN cashboxes cb ON cb.id=t.cashbox_id
           WHERE t.status='TOLANDI' AND t.pay_date=? GROUP BY t.paid_by, t.cashbox_id ORDER BY amount DESC''', (day,))]
    errors = db.execute('''SELECT e.*, u.full_name name FROM tezpul_events e LEFT JOIN users u ON u.id=e.user_id
                           WHERE substr(e.created_at,1,10)=? ORDER BY e.id DESC LIMIT 50''', (day,)).fetchall()
    recent = db.execute('''SELECT t.*, b.name brigade, u.full_name paid_name FROM tezpul_talons t
                           LEFT JOIN brigadiers b ON b.id=t.brigadier_id LEFT JOIN users u ON u.id=t.paid_by
                           WHERE t.status='TOLANDI' AND t.pay_date=? ORDER BY t.paid_at DESC LIMIT 30''', (day,)).fetchall()
    return {'day': day, 'today': dict(tot), 'season': dict(season), 'counts': counts, 'waiting': waiting,
            'wait_amount': wait_amount, 'brigades': brigades, 'cashiers': cashiers, 'errors': errors, 'recent': recent,
            'rate': rate(), 'max_kg': max_kg(), 'issued': sum(counts.values()),
            'unpaid': counts.get('BOSH', 0) + counts.get('KUTILMOQDA', 0) + counts.get('TAYYOR', 0)}


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
                       SUM(t.status='TOLANDI') paid, SUM(t.status='BEKOR') void
                FROM tezpul_talons t GROUP BY t.batch ORDER BY t.batch DESC''')


def ranges():
    """Which brigade holds which numbers (consecutive runs)."""
    out, cur = [], None
    for r in q('''SELECT t.number, t.brigadier_id, b.name FROM tezpul_talons t LEFT JOIN brigadiers b ON b.id=t.brigadier_id
                  ORDER BY t.number'''):
        if cur and cur['bid'] == r['brigadier_id'] and r['number'] == cur['b'] + 1:
            cur['b'] = r['number']; cur['n'] += 1
        else:
            cur = {'bid': r['brigadier_id'], 'name': r['name'] or '—', 'a': r['number'], 'b': r['number'], 'n': 1}
            out.append(cur)
    return out


def season_paid_kg():
    return scalar("SELECT COALESCE(SUM(kg),0) FROM tezpul_talons WHERE status='TOLANDI'")
