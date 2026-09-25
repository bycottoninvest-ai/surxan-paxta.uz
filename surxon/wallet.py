"""“Mening kassam” — the phone money screens: pay a worker, write an expense, cash in, today's list, corrections.

Every operation is two calls. *Preview* saves nothing and returns the figures computed on the server (debt after,
cash box after). *Confirm* saves once (client_uuid) and re-reads the box and the person's balance inside the write
transaction: when they differ from what the person saw on the check screen the confirm is refused with the fresh
figures (Stale), so nobody confirms against numbers that another user has changed in the meantime.

All money rules stay in surxon.accounting / surxon.services (frozen rates, KIRIM − CHIQIM = QOLDIQ, closed days,
one payout per tap). Nothing here deletes or overwrites a saved operation: a correction voids the original (kept,
visible) and writes a replacement, with the who/when/why/old/new in cash_corrections and the audit log.
"""
import json

from . import accounting as A
from .db import doc_number, get_db, q, scalar, tx
from .security import audit
from .services import CASH_CATEGORIES, EXPENSE_CATEGORIES, NOT_EXPENSES, add_expense
from .utils import UserError, clean_text, now_str, today_str

# the six tiles on the phone; the third value is the extra link the tile asks for (only for that type)
PHONE_EXPENSES = [('Yoqilg‘i', 'fuel', 'equipment'), ('Ovqat', 'food', 'field'), ('Transport', 'truck', 'field'),
                  ('Ta‘mirlash', 'wrench', 'equipment'), ('Ehtiyot qism', 'gear', 'equipment'), ('Boshqa', 'dots', None)]
PHONE_EXPENSE_NAMES = [e[0] for e in PHONE_EXPENSES]

CORRECTION_REASONS = ['Noto‘g‘ri summa', 'Noto‘g‘ri odam', 'Noto‘g‘ri xarajat turi', 'Takroriy to‘lov', 'Boshqa sabab']
OTHER = 'Boshqa sabab'
KIND_LABEL = {'pay': 'Ishchiga to‘lov', 'expense': 'Xarajat', 'income': 'Kassaga kirim'}


class Stale(UserError):
    """The box or the person's balance changed after the check screen: show the new figures, confirm again."""

    def __init__(self, preview):
        super().__init__('Hisob o‘zgardi (shu orada boshqa amal yozildi). Yangi raqamlarni ko‘rib, qayta tasdiqlang.')
        self.preview = preview


def som(v):
    return A.fmt_som(v)


def _box_name(db, box):
    return db.execute('SELECT name FROM cashboxes WHERE id=?', (box,)).fetchone()['name']


def visible_boxes(user):
    """The boxes this login works with: a cashier only their own; others every active box."""
    if user['role'] == 'cashier' and user['cashbox_id']:
        return [user['cashbox_id']]
    return [b['id'] for b in A.cashboxes()]


def boxes_with_balance(user):
    db = get_db()
    return [{'id': b, 'name': _box_name(db, b), 'balance': A.box_balance(db, b)} for b in visible_boxes(user)]


# ------------------------------------------------------------------ workers

def worker_figures(db, year, worker_id):
    """One person's whole season across every brigade and field they worked in (one card per person)."""
    w = db.execute('SELECT id, full_name, note, phone FROM workers WHERE id=?', (worker_id,)).fetchone()
    if not w:
        raise UserError('Ishchi topilmadi.')
    r = next(iter(A.worker_balances(year, worker_id=worker_id)), None)
    earned = r['earned'] if r else 0
    given = (r['paid'] + r['advances']) if r else 0
    debt = earned - given
    pending = r['pending'] if r else 0
    last = db.execute('''SELECT b.name brigade, f.name field, h.work_date FROM harvests h
                         LEFT JOIN brigadiers b ON b.id=h.brigadier_id LEFT JOIN fields f ON f.id=h.field_id
                         WHERE h.worker_id=? AND h.voided_at IS NULL ORDER BY h.id DESC LIMIT 1''', (worker_id,)).fetchone()
    brigades = [x['name'] for x in db.execute(
        '''SELECT DISTINCT b.name FROM harvests h JOIN brigadiers b ON b.id=h.brigadier_id
           WHERE h.worker_id=? AND h.season_year=? AND h.voided_at IS NULL ORDER BY b.name''', (worker_id, year))]
    return {'id': w['id'], 'name': w['full_name'], 'note': w['note'] or '', 'phone': w['phone'] or '',
            'earned': earned, 'given': given, 'debt': debt, 'pending': pending, 'payable': max(0, debt - pending),
            'uncalc_kg': r['uncalc_kg'] if r else 0, 'kg': r['kg'] if r else 0,
            'last_brigade': last['brigade'] if last else None, 'last_field': last['field'] if last else None,
            'brigades': brigades}


def search_workers(year, text, limit=25):
    """Name or number (“12”, “№12”). Namesakes are told apart by number, note and where they last worked."""
    text = (text or '').strip().lstrip('№#').strip()
    db = get_db()
    if text.isdigit():
        ids = [r['id'] for r in db.execute('SELECT id FROM workers WHERE id=?', (int(text),))]
    elif text:
        ids = [r['id'] for r in db.execute('''SELECT id FROM workers WHERE full_name LIKE ? OR IFNULL(note,'') LIKE ?
                                              ORDER BY full_name, id LIMIT ?''', (f'%{text}%', f'%{text}%', limit))]
    else:   # nothing typed: the people who are owed money, biggest first
        ids = [r['id'] for r in sorted((r for r in A.worker_balances(year) if r['payable'] > 0),
                                       key=lambda r: -r['payable'])[:limit]]
    out = [worker_figures(db, year, i) for i in ids]
    names = {}
    for w in out:
        names[w['name'].lower()] = names.get(w['name'].lower(), 0) + 1
    for w in out:
        w['namesake'] = names[w['name'].lower()] > 1 or bool(scalar(
            'SELECT COUNT(*) FROM workers WHERE name_key=(SELECT name_key FROM workers WHERE id=?) AND id<>?', (w['id'], w['id'])))
    return out


# ------------------------------------------------------------------ pay a worker (accountant: prepares and hands out)

def _pay_check(db, box, f, amount):
    if not amount or amount <= 0:
        raise UserError('Beriladigan summani kiriting.')
    if amount > f['payable']:
        extra = f' (kassirda {som(f["pending"])} tayyor to‘lov bor)' if f['pending'] else ''
        raise UserError(f'{f["name"]}: qolgan qarz {som(f["payable"])}{extra}. Qarzdan ko‘p berib bo‘lmaydi — '
                        'avans kerak bo‘lsa, buxgalteriyadagi “Avans” tartibi bilan.')
    bal = A.box_balance(db, box)
    if amount > bal:
        raise UserError(f'Kassada yetarli pul yo‘q: qoldiq {som(bal)}, berilmoqchi {som(amount)}.')
    return {'kind': 'pay', 'worker': f, 'amount': amount, 'debt_after': f['debt'] - amount, 'box_id': box,
            'box_name': _box_name(db, box), 'box_before': bal, 'box_after': bal - amount,
            'expect': f'{f["debt"]}:{f["pending"]}:{bal}'}


def pay_preview(actor, worker_id, amount, cashbox_id=None):
    A._need(actor, 'payouts.prepare')
    A._need(actor, 'payouts.pay')
    db = get_db()
    box = A._cashbox(db, actor, cashbox_id)
    return _pay_check(db, box, worker_figures(db, A.season_of(db), worker_id), amount)


def give_pay(actor, worker_id, amount, *, cashbox_id=None, client_uuid=None, expect=None, note=''):
    """“To‘lovni tasdiqlash”: the payment order and the cash-out in one transaction (the accountant does both)."""
    A._need(actor, 'payouts.prepare')
    A._need(actor, 'payouts.pay')
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT id, doc_no, cash_entry_id FROM payouts WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return {'already': True, 'doc_no': dup['doc_no'], 'cash_id': dup['cash_entry_id']}
        year = A.season_of(db)
        box = A._cashbox(db, actor, cashbox_id)
        f = worker_figures(db, year, worker_id)
        p = _pay_check(db, box, f, amount)
        if expect and expect != p['expect']:
            raise Stale(p)
        A.assert_day_open(db, box, today_str())
        no, ts = doc_number(db, 'PAY', year), now_str()
        pid = db.execute('''INSERT INTO payouts(doc_no, season_year, kind, purpose, worker_id, amount, note, client_uuid,
                                cashbox_id, prepared_by, prepared_at, status, paid_by, paid_at)
                            VALUES (?,?,'worker','pay',?,?,?,?,?,?,?,'BERILDI',?,?)''',
                         (no, year, worker_id, amount, clean_text(note, 200), client_uuid, box, actor.user_id, ts,
                          actor.user_id, ts)).lastrowid
        cid, _ = A.book_cash(db, actor, direction='OUT', category='worker_pay', amount=amount, cashbox_id=box,
                             worker_id=worker_id, payout_id=pid, counterparty=f['name'], note=note, doc_no=no)
        db.execute('UPDATE payouts SET cash_entry_id=? WHERE id=?', (cid, pid))
        audit(db, actor, 'PAID', 'payout', pid, new={'doc_no': no, 'to': f['name'], 'worker_id': worker_id, 'amount': amount,
                                                      'debt_before': f['debt'], 'debt_after': f['debt'] - amount,
                                                      'cashbox_id': box, 'cash_entry_id': cid, 'direct': True})
        A.mirror_payout(db, pid)
        A.mirror_worker(db, worker_id, year)
        return {'already': False, 'doc_no': no, 'cash_id': cid}


# ------------------------------------------------------------------ cashier: hand out a prepared order

def _order_check(db, actor, p, box):
    if p['kind'] == 'worker':
        f = worker_figures(db, p['season_year'], p['worker_id'])
    else:
        c = next(iter(A.combine_balances(p['season_year'], p['combine_id'])), None)
        f = {'id': p['combine_id'], 'name': c['code'] if c else 'Kombayn', 'note': '', 'earned': c['earned'] if c else 0,
             'given': c['paid'] if c else 0, 'debt': c['balance'] if c else 0, 'pending': p['amount'], 'payable': 0,
             'uncalc_kg': 0, 'brigades': [], 'last_brigade': None, 'last_field': None}
    bal = A.box_balance(db, box)
    if p['amount'] > bal:
        raise UserError(f'Kassada yetarli pul yo‘q: qoldiq {som(bal)}, berilmoqchi {som(p["amount"])}.')
    debt_after = f['debt'] - p['amount'] if p['purpose'] == 'pay' else f['debt'] - p['amount']
    return {'kind': 'order', 'worker': f, 'amount': p['amount'], 'debt_after': debt_after, 'box_id': box,
            'box_name': _box_name(db, box), 'box_before': bal, 'box_after': bal - p['amount'], 'doc_no': p['doc_no'],
            'purpose': p['purpose'], 'expect': f'{f["debt"]}:{bal}'}


def order_preview(actor, payout_id):
    A._need(actor, 'payouts.pay')
    db = get_db()
    p = db.execute('SELECT * FROM payouts WHERE id=?', (payout_id,)).fetchone()
    if not p:
        raise UserError('To‘lov topilmadi.')
    box = A._cashbox(db, actor, p['cashbox_id'])
    if actor.role == 'cashier' and p['cashbox_id'] and box != p['cashbox_id']:
        raise UserError('Bu to‘lov boshqa kassadan beriladi.')
    return p, (_order_check(db, actor, p, box) if p['status'] == 'TAYYOR' else None)


def order_pay(actor, payout_id, expect=None):
    def check(db, p, box):
        prev = _order_check(db, actor, p, box)
        if expect and expect != prev['expect']:
            raise Stale(prev)
    res = A.pay_payout(actor, payout_id, check=check)
    res['cash_id'] = scalar('SELECT cash_entry_id FROM payouts WHERE id=?', (payout_id,))
    return res


# ------------------------------------------------------------------ expense and income

def _expense_category(category):
    category = clean_text(category, 60)
    if category.lower() in NOT_EXPENSES:
        raise UserError('Ish haqi, avans va kombayn puli xarajat sifatida yozilmaydi.')
    if category not in PHONE_EXPENSE_NAMES and category not in EXPENSE_CATEGORIES:
        raise UserError('Xarajat turini tanlang.')
    return category


def _expense_check(db, actor, box, category, amount, payer):
    if not amount or amount <= 0:
        raise UserError('Summani kiriting.')
    if not clean_text(payer, 80):
        raise UserError('Kimga berilganini yozing.')
    bal = A.box_balance(db, box)
    if amount > bal:
        raise UserError(f'Kassada yetarli pul yo‘q: qoldiq {som(bal)}, xarajat {som(amount)}.')
    return {'kind': 'expense', 'category': category, 'amount': amount, 'payer': clean_text(payer, 80), 'box_id': box,
            'box_name': _box_name(db, box), 'box_before': bal, 'box_after': bal - amount,
            'needs_check': not actor.can('expenses.approve'), 'expect': str(bal)}


def expense_preview(actor, *, category, amount, payer, cashbox_id=None):
    A._need(actor, 'expenses.write')
    db = get_db()
    return _expense_check(db, actor, A._cashbox(db, actor, cashbox_id), _expense_category(category), amount, payer)


def write_expense(actor, *, category, amount, payer, cashbox_id=None, field_id=None, equipment_id=None, note='',
                  photo=None, client_uuid=None, expect=None):
    category = _expense_category(category)

    def check(db, box):
        prev = _expense_check(db, actor, box, category, amount, payer)
        if expect and expect != prev['expect']:
            raise Stale(prev)
    eid = add_expense(actor, amount=amount, expense_date=today_str(), category=category, field_id=field_id,
                      equipment_id=equipment_id, payer=payer, note=note, from_cash=True, client_uuid=client_uuid,
                      cashbox_id=cashbox_id, photo=photo, check=check)
    return scalar('SELECT cash_entry_id FROM expenses WHERE id=?', (eid,))


def _income_check(db, box, source, amount):
    if not amount or amount <= 0:
        raise UserError('Summani kiriting.')
    if not clean_text(source, 80):
        raise UserError('Pul qayerdan yoki kimdan olinganini tanlang.')
    bal = A.box_balance(db, box)
    return {'kind': 'income', 'source': clean_text(source, 80), 'amount': amount, 'box_id': box,
            'box_name': _box_name(db, box), 'box_before': bal, 'box_after': bal + amount, 'expect': str(bal)}


def income_preview(actor, *, source, amount, cashbox_id=None):
    A._need(actor, 'cash.write')
    db = get_db()
    return _income_check(db, A._cashbox(db, actor, cashbox_id), source, amount)


def write_income(actor, *, source, amount, cashbox_id=None, note='', photo=None, client_uuid=None, expect=None):
    def check(db, box):
        prev = _income_check(db, box, source, amount)
        if expect and expect != prev['expect']:
            raise Stale(prev)
    cid, _ = A.add_income(actor, amount=amount, source=source, cashbox_id=cashbox_id, note=note, photo=photo,
                          client_uuid=client_uuid, check=check)
    return cid


# ------------------------------------------------------------------ today's list and one operation

ENTRY_SELECT = '''SELECT ce.*, w.full_name worker_name, w.note worker_note, eq.code combine_code, cb.name box_name,
                         u.full_name by_name, vu.full_name voided_name, e.category exp_category, e.status exp_status,
                         e.payer exp_payer, e.field_id exp_field_id, e.equipment_id exp_equipment_id,
                         f.name field_name, eqx.code equipment_code, p.purpose pay_purpose, p.status payout_status,
                         p.prepared_by, pu.full_name prepared_name,
                         (SELECT c.status FROM cash_corrections c WHERE c.cash_entry_id=ce.id
                            AND c.status IN ('KUTILMOQDA','BAJARILDI')) corr_status,
                         (SELECT c.id FROM cash_corrections c WHERE c.new_cash_entry_id=ce.id AND c.status='BAJARILDI') replaces_corr
                  FROM cash_entries ce LEFT JOIN workers w ON w.id=ce.worker_id LEFT JOIN equipment eq ON eq.id=ce.combine_id
                  LEFT JOIN cashboxes cb ON cb.id=ce.cashbox_id LEFT JOIN users u ON u.id=ce.created_by
                  LEFT JOIN users vu ON vu.id=ce.voided_by LEFT JOIN expenses e ON e.id=ce.expense_id
                  LEFT JOIN fields f ON f.id=e.field_id LEFT JOIN equipment eqx ON eqx.id=e.equipment_id
                  LEFT JOIN payouts p ON p.id=ce.payout_id LEFT JOIN users pu ON pu.id=p.prepared_by'''


def entry_kind(e):
    if e['category'] in ('worker_pay', 'advance') and e['worker_id'] and e['payout_id']:
        return 'pay'
    if e['category'] == 'expense' and e['expense_id']:
        return 'expense'
    if e['category'] == 'income':
        return 'income'
    return None


def describe(e):
    """(who, what, status label, badge class) for one cash row as the phone shows it."""
    e = dict(e)
    who = e['worker_name'] or e['combine_code'] or e['exp_payer'] or e['counterparty'] or e['source'] or ''
    what = CASH_CATEGORIES.get(e['category'], ('', e['category']))[1]
    if e['category'] == 'expense' and e['exp_category']:
        what = e['exp_category']
    if e['voided_at']:
        status, badge = ('Tuzatilgan' if e['corr_status'] == 'BAJARILDI' else 'Bekor qilingan'), 'b-gray'
    elif e['corr_status'] == 'KUTILMOQDA':
        status, badge = 'Tuzatish kutilmoqda', 'b-orange'
    elif e['exp_status'] == 'TEKSHIRILMAGAN':
        status, badge = 'Buxgalter tekshiradi', 'b-orange'
    else:
        status, badge = 'Tasdiqlangan', 'b-green'
    return {**e, 'who': who, 'what': what, 'status_label': status, 'badge': badge, 'kind': entry_kind(e),
            'sign': 1 if e['direction'] == 'IN' else -1}


def today_ops(user, day=None):
    day = day or today_str()
    boxes = visible_boxes(user)
    if not boxes:
        return []
    marks = ','.join('?' * len(boxes))
    rows = q(f'''{ENTRY_SELECT} WHERE ce.cashbox_id IN ({marks}) AND (ce.entry_date=? OR substr(ce.created_at,1,10)=?)
                 ORDER BY ce.id DESC LIMIT 300''', boxes + [day, day])
    return [describe(r) for r in rows]


def get_entry(user, cash_id):
    row = q(f'{ENTRY_SELECT} WHERE ce.id=?', (cash_id,), one=True)
    if not row:
        return None
    if user['role'] == 'cashier' and row['cashbox_id'] not in visible_boxes(user):
        return None
    return describe(row)


def corrections_of(cash_id):
    return q('''SELECT c.*, ru.full_name requested_name, du.full_name decided_name, ne.doc_no new_doc_no
                FROM cash_corrections c LEFT JOIN users ru ON ru.id=c.requested_by LEFT JOIN users du ON du.id=c.decided_by
                LEFT JOIN cash_entries ne ON ne.id=c.new_cash_entry_id
                WHERE c.cash_entry_id=? OR c.new_cash_entry_id=? ORDER BY c.id''', (cash_id, cash_id))


# ------------------------------------------------------------------ corrections

def reasons_for(kind):
    if kind == 'pay':
        return [r for r in CORRECTION_REASONS if r != 'Noto‘g‘ri xarajat turi']
    if kind == 'expense':
        return list(CORRECTION_REASONS)
    if kind == 'income':
        return [r for r in CORRECTION_REASONS if r != 'Noto‘g‘ri xarajat turi']
    return []


def _may_request(actor, kind):
    need = {'pay': 'payouts.pay', 'expense': 'expenses.write', 'income': 'cash.write'}.get(kind)
    return bool(need) and actor.can(need)


def can_correct(user_actor, e):
    return bool(e) and not e['voided_at'] and e['kind'] is not None and _may_request(user_actor, e['kind'])


def _plan(db, e, reason, *, note='', new_amount=None, new_worker_id=None, new_category=None, new_party=None):
    """What the correction will do, with its effect on the box and the people's debt. Nothing is written."""
    kind = entry_kind(e)
    if not kind:
        raise UserError('Bu turdagi amal telefondan tuzatilmaydi — buxgalteriya sahifasida.')
    if e['voided_at']:
        raise UserError('Bu amal allaqachon bekor qilingan / tuzatilgan.')
    if reason not in reasons_for(kind):
        raise UserError('Tuzatish sababini tanlang.')
    note = clean_text(note, 300)
    if reason == OTHER and len(note) < 3:
        raise UserError('“Boshqa sabab” tanlansa, sababni qisqa yozing.')
    if kind == 'pay' and db.execute("SELECT 1 FROM cash_entries WHERE payout_id=? AND category LIKE 'refund%' AND voided_at IS NULL",
                                    (e['payout_id'],)).fetchone():
        raise UserError('Bu to‘lovning puli qaytarilgan — endi tuzatilmaydi.')
    A.assert_day_open(db, e['cashbox_id'], e['entry_date'])
    old_amount = e['amount']
    old_who = e['worker_name'] or e['exp_payer'] or e['counterparty'] or e['source'] or ''
    old_cat = e['exp_category'] if kind == 'expense' else None
    amount, worker_id, category, party, cancel = old_amount, e['worker_id'], old_cat, old_who, False
    if reason == 'Noto‘g‘ri summa':
        if not new_amount or new_amount <= 0:
            raise UserError('To‘g‘ri summani kiriting.')
        if new_amount == old_amount:
            raise UserError('Yangi summa eskisi bilan bir xil.')
        amount = new_amount
    elif reason == 'Noto‘g‘ri odam':
        if kind == 'pay':
            if not new_worker_id or new_worker_id == e['worker_id']:
                raise UserError('To‘g‘ri ishchini tanlang.')
            if not db.execute('SELECT 1 FROM workers WHERE id=?', (new_worker_id,)).fetchone():
                raise UserError('Ishchi topilmadi.')
            worker_id = new_worker_id
        else:
            party = clean_text(new_party, 80)
            if not party or party == old_who:
                raise UserError('To‘g‘ri odam / manbani yozing.')
    elif reason == 'Noto‘g‘ri xarajat turi':
        category = _expense_category(new_category or '')
        if category == old_cat:
            raise UserError('Yangi tur eskisi bilan bir xil.')
    elif reason == 'Takroriy to‘lov':
        cancel = True
    else:   # Boshqa sabab: a new amount, or the whole record is cancelled
        if new_amount:
            amount = new_amount
        else:
            cancel = True
    year = e['season_year']
    bal = A.box_balance(db, e['cashbox_id'])
    signed_old = old_amount if e['direction'] == 'OUT' else -old_amount     # what voiding gives back to the box
    new_out = 0 if cancel else (amount if e['direction'] == 'OUT' else -amount)
    box_after = bal + signed_old - new_out
    if box_after < 0:
        raise UserError(f'Bu tuzatishdan keyin kassa manfiy bo‘lib qoladi ({som(box_after)}). Summani tekshiring.')
    people = []
    if kind == 'pay':
        old_f = worker_figures(db, year, e['worker_id'])
        after_old = old_f['debt'] + old_amount - (amount if not cancel and worker_id == e['worker_id'] else 0)
        people.append({'name': old_f['name'], 'id': old_f['id'], 'before': old_f['debt'], 'after': after_old})
        if not cancel and worker_id != e['worker_id']:
            nf = worker_figures(db, year, worker_id)
            people.append({'name': nf['name'], 'id': nf['id'], 'before': nf['debt'], 'after': nf['debt'] - amount})
        if not cancel and e['pay_purpose'] == 'pay':
            target = next(p for p in people if p['id'] == worker_id)
            pend = worker_figures(db, year, worker_id)['pending']
            if target['after'] - pend < 0:
                raise UserError(f'{target["name"]}: bu summa qarzidan ko‘p — to‘lov qarzdan oshmaydi.')
        party = next(p['name'] for p in people if p['id'] == worker_id)
    return {'kind': kind, 'reason': reason, 'note': note, 'cancel': cancel,
            'old': {'amount': old_amount, 'who': old_who, 'category': old_cat, 'doc_no': e['doc_no']},
            'new': None if cancel else {'amount': amount, 'who': party, 'category': category, 'worker_id': worker_id},
            'box_name': e['box_name'], 'box_before': bal, 'box_after': box_after, 'people': people,
            'expect': f'{bal}:' + ','.join(str(p['before']) for p in people)}


def _entry_row(db, cash_id):
    row = db.execute(f'{ENTRY_SELECT} WHERE ce.id=?', (cash_id,)).fetchone()
    if not row:
        raise UserError('Amal topilmadi.')
    return row


def correction_preview(actor, user, cash_id, reason, **kw):
    e = get_entry(user, cash_id)
    if not e:
        raise UserError('Amal topilmadi.')
    if not _may_request(actor, e['kind']):
        raise UserError('Bu amalni tuzatish huquqingiz yo‘q.')
    if q("SELECT 1 FROM cash_corrections WHERE cash_entry_id=? AND status='KUTILMOQDA'", (cash_id,), one=True):
        raise UserError('Bu amal uchun tuzatish so‘rovi allaqachon bor — buxgalter qaror qiladi.')
    p = _plan(get_db(), _entry_row(get_db(), cash_id), reason, **kw)
    p['applies_now'] = actor.can('expenses.approve')
    return p


def request_correction(actor, user, cash_id, reason, *, client_uuid=None, expect=None, **kw):
    """Accountant (approve right): applied at once. Anyone else: saved as a request the accountant decides on."""
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT * FROM cash_corrections WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return {'already': True, 'id': dup['id'], 'status': dup['status'], 'new_cash_id': dup['new_cash_entry_id']}
        e = get_entry(user, cash_id)
        if not e or not _may_request(actor, e['kind']):
            raise UserError('Bu amalni tuzatish huquqingiz yo‘q.')
        if db.execute("SELECT 1 FROM cash_corrections WHERE cash_entry_id=? AND status='KUTILMOQDA'", (cash_id,)).fetchone():
            raise UserError('Bu amal uchun tuzatish so‘rovi allaqachon bor.')
        row = _entry_row(db, cash_id)
        plan = _plan(db, row, reason, **kw)
        plan['applies_now'] = actor.can('expenses.approve')
        if expect and expect != plan['expect']:
            raise Stale(plan)
        new = plan['new'] or {}
        cid = db.execute('''INSERT INTO cash_corrections(cash_entry_id, reason, note, new_amount, new_worker_id, new_category,
                                new_party, cancel, old_json, new_json, status, client_uuid, requested_by, requested_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,'KUTILMOQDA',?,?,?)''',
                         (cash_id, reason, plan['note'], new.get('amount'),
                          new.get('worker_id') if plan['kind'] == 'pay' else None, new.get('category'),
                          new.get('who') if plan['kind'] != 'pay' else None, int(plan['cancel']),
                          json.dumps(plan['old'], ensure_ascii=False), json.dumps(plan['new'], ensure_ascii=False),
                          client_uuid, actor.user_id, now_str())).lastrowid
        audit(db, actor, 'REQUEST', 'cash_correction', cid, old=plan['old'], new=plan['new'] or {'cancel': True},
              reason=f'{reason}: {plan["note"]}' if plan['note'] else reason)
        if actor.can('expenses.approve'):
            new_cash = _apply(db, actor, cid, row, plan)
            return {'already': False, 'id': cid, 'status': 'BAJARILDI', 'new_cash_id': new_cash}
        from .reporting import feed
        feed(db, f'corr:{cid}', f'✏️ Tuzatish so‘raldi: {row["doc_no"]} · {reason}\nBuxgalter tasdiqlashi kerak.')
        return {'already': False, 'id': cid, 'status': 'KUTILMOQDA', 'new_cash_id': None}


def _stored_kwargs(c):
    return {'note': c['note'] or '', 'new_amount': c['new_amount'], 'new_worker_id': c['new_worker_id'],
            'new_category': c['new_category'], 'new_party': c['new_party']}


def pending_plan(corr_id):
    """The live effect of a waiting request (for the accountant's decision screen)."""
    c = q('SELECT * FROM cash_corrections WHERE id=?', (corr_id,), one=True)
    if not c or c['status'] != 'KUTILMOQDA':
        return c, None
    try:
        return c, _plan(get_db(), _entry_row(get_db(), c['cash_entry_id']), c['reason'], **_stored_kwargs(c))
    except UserError as err:
        return c, {'error': str(err)}


def approve_correction(actor, corr_id):
    A._need(actor, 'expenses.approve')
    with tx() as db:
        c = db.execute('SELECT * FROM cash_corrections WHERE id=?', (corr_id,)).fetchone()
        if not c:
            raise UserError('Tuzatish topilmadi.')
        if c['status'] != 'KUTILMOQDA':
            return {'already': True, 'status': c['status'], 'new_cash_id': c['new_cash_entry_id']}
        row = _entry_row(db, c['cash_entry_id'])
        plan = _plan(db, row, c['reason'], **_stored_kwargs(c))
        new_cash = _apply(db, actor, corr_id, row, plan)
        return {'already': False, 'status': 'BAJARILDI', 'new_cash_id': new_cash}


def reject_correction(actor, corr_id, note=''):
    A._need(actor, 'expenses.approve')
    with tx() as db:
        n = db.execute("""UPDATE cash_corrections SET status='RAD', decided_by=?, decided_at=?, decision_note=?
                          WHERE id=? AND status='KUTILMOQDA'""", (actor.user_id, now_str(), clean_text(note, 200), corr_id)).rowcount
        if n:
            audit(db, actor, 'REJECT', 'cash_correction', corr_id, reason=clean_text(note, 200) or None)
        return bool(n)


def _apply(db, actor, corr_id, row, plan):
    """Void the original (kept), write the replacement, mark the correction done — one transaction."""
    ts = now_str()
    why = f'Tuzatildi (#{corr_id}): {plan["reason"]}' + (f' — {plan["note"]}' if plan['note'] else '')
    n = db.execute("""UPDATE cash_corrections SET status='BAJARILDI', decided_by=?, decided_at=? WHERE id=? AND status='KUTILMOQDA'""",
                   (actor.user_id, ts, corr_id)).rowcount
    if n != 1:
        raise UserError('Bu tuzatish allaqachon hal qilingan.')
    db.execute('UPDATE cash_entries SET voided_at=?, voided_by=?, void_reason=? WHERE id=? AND voided_at IS NULL',
               (ts, actor.user_id, why, row['id']))
    new_cash, new = None, plan['new']
    year = row['season_year']
    if plan['kind'] == 'pay':
        db.execute("UPDATE payouts SET status='BEKOR', voided_at=?, voided_by=?, void_reason=? WHERE id=?",
                   (ts, actor.user_id, why, row['payout_id']))
        A.mirror_payout(db, row['payout_id'])
        if new:
            no = doc_number(db, 'PAY', year)
            pid = db.execute('''INSERT INTO payouts(doc_no, season_year, kind, purpose, worker_id, amount, note, cashbox_id,
                                    prepared_by, prepared_at, status, paid_by, paid_at)
                                VALUES (?,?,'worker',?,?,?,?,?,?,?,'BERILDI',?,?)''',
                             (no, year, row['pay_purpose'] or 'pay', new['worker_id'], new['amount'],
                              f'{row["doc_no"]} o‘rniga', row['cashbox_id'], actor.user_id, ts, actor.user_id, ts)).lastrowid
            new_cash, _ = A.book_cash(db, actor, direction='OUT', category=row['category'], amount=new['amount'],
                                      entry_date=row['entry_date'], cashbox_id=row['cashbox_id'], worker_id=new['worker_id'],
                                      payout_id=pid, counterparty=new['who'], note=f'{row["doc_no"]} o‘rniga (tuzatish)',
                                      doc_no=no)
            db.execute('UPDATE payouts SET cash_entry_id=? WHERE id=?', (new_cash, pid))
            A.mirror_payout(db, pid)
        for p in plan['people']:
            A.mirror_worker(db, p['id'], year)
    elif plan['kind'] == 'expense':
        e = db.execute('SELECT * FROM expenses WHERE id=?', (row['expense_id'],)).fetchone()
        db.execute('UPDATE expenses SET voided_at=?, voided_by=?, void_reason=? WHERE id=?', (ts, actor.user_id, why, e['id']))
        A.mirror_expense(db, e['id'])
        if new:
            no = doc_number(db, 'EXP', year)
            eid = db.execute('''INSERT INTO expenses(season_year, expense_date, category, amount, field_id, brigadier_id,
                                    equipment_id, payer, note, created_by, created_at, doc_no, cashbox_id, station_id,
                                    status, checked_by, checked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'TASDIQLANGAN',?,?)''',
                             (year, e['expense_date'], new['category'], new['amount'], e['field_id'], e['brigadier_id'],
                              e['equipment_id'], new['who'], f'{e["doc_no"]} o‘rniga (tuzatish). {e["note"] or ""}'.strip(),
                              actor.user_id, ts, no, e['cashbox_id'], e['station_id'], actor.user_id, ts)).lastrowid
            new_cash, _ = A.book_cash(db, actor, direction='OUT', category='expense', amount=new['amount'],
                                      entry_date=e['expense_date'], cashbox_id=e['cashbox_id'], expense_id=eid,
                                      counterparty=new['who'], note=f'{new["category"]}: {e["doc_no"]} o‘rniga'[:200], doc_no=no)
            db.execute('UPDATE expenses SET cash_entry_id=? WHERE id=?', (new_cash, eid))
            A.mirror_expense(db, eid)
    else:   # income
        if new:
            new_cash, _ = A.book_cash(db, actor, direction='IN', category='income', amount=new['amount'],
                                      entry_date=row['entry_date'], cashbox_id=row['cashbox_id'], source=new['who'],
                                      counterparty=new['who'], note=f'{row["doc_no"]} o‘rniga (tuzatish)')
    A.mirror_cash(db, row['id'])
    db.execute('UPDATE cash_corrections SET new_cash_entry_id=? WHERE id=?', (new_cash, corr_id))
    audit(db, actor, 'CORRECT', 'cash_entry', row['id'], old=plan['old'], new=plan['new'] or {'cancel': True},
          reason=why)
    from .reporting import feed
    new_txt = 'bekor qilindi' if not new else f'→ {som(new["amount"])} · {new["who"]}' + (f' · {new["category"]}' if new.get('category') else '')
    feed(db, f'corrdone:{corr_id}', f'✏️ TUZATISH {row["doc_no"]}: {som(plan["old"]["amount"])} · {plan["old"]["who"]} {new_txt}\n'
                                    f'Sabab: {plan["reason"]}' + (f' — {plan["note"]}' if plan['note'] else ''))
    return new_cash


def corrections_list(limit=200):
    return q('''SELECT c.*, ce.doc_no, ce.amount old_amount, ce.entry_date, ru.full_name requested_name,
                       du.full_name decided_name, ne.doc_no new_doc_no
                FROM cash_corrections c JOIN cash_entries ce ON ce.id=c.cash_entry_id
                LEFT JOIN users ru ON ru.id=c.requested_by LEFT JOIN users du ON du.id=c.decided_by
                LEFT JOIN cash_entries ne ON ne.id=c.new_cash_entry_id ORDER BY c.id DESC LIMIT ?''', (limit,))
