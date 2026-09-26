"""Buxgalteriya (accountant) and Kassir (cashier) screens — phone first, the same data on the computer.

Nothing here computes money on its own: all figures come from surxon.accounting / surxon.reporting, and every
write goes through a service that checks the permission again (hiding a button is never the protection).
"""
from flask import Blueprint, Response, abort, g, redirect, render_template, request, url_for

from .. import accounting as A
from .. import queries
from ..db import q, scalar
from ..exports import to_xlsx
from ..photos import read_upload
from ..reporting import PERIODS, finance_summary, period_range, summary_pdf
from ..security import can, perm_required, require
from ..services import EXPENSE_CATEGORIES, add_expense
from ..settings import get_setting
from ..utils import UserError, parse_date, parse_int, parse_money, parse_number, today_str
from . import checkbox, done, form_uuid, post_actor, season_arg

bp = Blueprint('acct', __name__)


def _period():
    p = request.args.get('period', 'bugun')
    a, b, label = period_range(p, request.args.get('from'), request.args.get('to'), season_arg())
    return p, a, b, label


def _ctx(**kw):
    p, a, b, label = _period()
    return dict(periods=PERIODS, period=p, date_from=a, date_to=b, period_label=label, **kw)


def _my_box():
    return g.user['cashbox_id'] if g.user['role'] == 'cashier' and g.user['cashbox_id'] else None


# ------------------------------------------------------------------ accountant home

@bp.get('/buxgalteriya')
@perm_required('acct.view')
def home():
    year = season_arg()
    t = today_str()
    cot = A.cotton_totals(year, t, t)
    today_money = finance_summary(t, t)
    owed = [w for w in A.worker_balances(year) if w['payable'] > 0]
    combines = A.combine_balances(year)
    items = A.todo(year, t)
    from ..pricing import prices
    return render_template('acct_home.html', year=year, cot=cot, s=today_money, balance=A.total_balance(),
                           pk=queries.finance_summary(year), prices=prices(),
                           owed_n=len(owed), owed_sum=sum(w['payable'] for w in owed),
                           combine_balance=sum(c['balance'] for c in combines),
                           combine_worked=[c for c in combines if c['kg'] or c['work_days'] or c['hectares']],
                           problems=sum(1 for i in items if i[0] != 'green'), items=items)


@bp.post('/buxgalteriya/narx')
@perm_required('nayman.write')
def prices():
    """Paxta narxi (punkt to‘laydi): qo‘l terimi / kombayn — changed any time, every sum follows."""
    from ..pricing import set_prices
    v = set_prices(post_actor(), hand=request.form.get('hand'), combine=request.form.get('combine'))
    return done('Paxta narxi saqlandi: qo‘l terimi ' + (v['price_hand_kg'] or '—') + ', kombayn '
                + (v['price_combine_kg'] or '—') + ' so‘m/kg. Punkt bilan hisob qayta hisoblandi.', url_for('acct.home'))


# ------------------------------------------------------------------ income

@bp.route('/buxgalteriya/kirim', methods=['GET', 'POST'])
@perm_required('cash.write')
def income():
    if request.method == 'POST':
        cid, no = A.add_income(post_actor(), amount=parse_money(request.form.get('amount')),
                               source=request.form.get('source', ''),
                               cashbox_id=parse_int(request.form.get('cashbox_id'), 'Kassa', required=False),
                               note=request.form.get('note', ''), photo=read_upload(request.files.get('photo')),
                               client_uuid=form_uuid())
        return done(f'Kirim saqlandi: {no}.', url_for('acct.home'), doc_no=no)
    year = season_arg()
    by_source = q('''SELECT COALESCE(source, counterparty, '—') source, SUM(amount) total, COUNT(*) n FROM cash_entries
                     WHERE season_year=? AND direction='IN' AND voided_at IS NULL AND category IN ('income','opening','other_in','nayman')
                     GROUP BY 1 ORDER BY total DESC''', (year,))
    recent = q('''SELECT c.*, cb.name box FROM cash_entries c LEFT JOIN cashboxes cb ON cb.id=c.cashbox_id
                  WHERE c.season_year=? AND c.direction='IN' ORDER BY c.id DESC LIMIT 15''', (year,))
    return render_template('acct_income.html', sources=A.income_sources(), boxes=A.cashboxes(), by_source=by_source,
                           recent=recent)


# ------------------------------------------------------------------ expenses

@bp.get('/buxgalteriya/xarajat')
@perm_required('expenses.write')
def expense_pick():
    return render_template('acct_expense_pick.html', buttons=A.EXPENSE_BUTTONS, shortcuts=A.EXPENSE_SHORTCUTS)


@bp.route('/buxgalteriya/xarajat/yangi', methods=['GET', 'POST'])
@perm_required('expenses.write')
def expense_new():
    category = request.values.get('category', '')
    if request.method == 'POST':
        eid = add_expense(post_actor(), amount=parse_money(request.form.get('amount')), expense_date=today_str(),
                          category=category, field_id=parse_int(request.form.get('field_id'), 'Dala', required=False),
                          station_id=parse_int(request.form.get('station_id'), 'Punkt', required=False),
                          equipment_id=parse_int(request.form.get('equipment_id'), 'Texnika', required=False),
                          brigadier_id=parse_int(request.form.get('brigadier_id'), 'Brigada', required=False),
                          note=request.form.get('note', ''), from_cash=True, client_uuid=form_uuid(),
                          cashbox_id=parse_int(request.form.get('cashbox_id'), 'Kassa', required=False),
                          photo=read_upload(request.files.get('photo')))
        no = q('SELECT doc_no, status FROM expenses WHERE id=?', (eid,), one=True)
        back = url_for('acct.home') if can('acct.view') else url_for('acct.cashier')
        return done(f'Xarajat saqlandi: {no["doc_no"]}' + (' (buxgalter tasdiqlaydi)' if no['status'] == 'TEKSHIRILMAGAN' else '.'),
                    back, doc_no=no['doc_no'])
    if category not in [b[0] for b in A.EXPENSE_BUTTONS] + EXPENSE_CATEGORIES:
        return redirect(url_for('acct.expense_pick'))
    return render_template('acct_expense_form.html', category=category, boxes=A.cashboxes(),
                           fields=q('SELECT id, code, name FROM fields WHERE active=1 ORDER BY code'),
                           stations=q('SELECT id, name FROM stations WHERE active=1 ORDER BY name'),
                           brigadiers=q('SELECT id, name FROM brigadiers WHERE active=1 ORDER BY name'),
                           equipment=q("SELECT id, code, kind FROM equipment WHERE active=1 AND kind<>'telashka' ORDER BY kind, code"))


@bp.get('/buxgalteriya/xarajatlar')
@perm_required('acct.view')
def expenses():
    p, a, b, label = _period()
    status = request.args.get('status', '')
    where, params = ['e.expense_date BETWEEN ? AND ?'], [a, b]
    if status:
        where = ['1=1'] if status == 'TEKSHIRILMAGAN' else where
        params = [] if status == 'TEKSHIRILMAGAN' else params
        where.append("e.status=? AND e.voided_at IS NULL"); params.append(status)
    for key, col in (('field', 'e.field_id'), ('punkt', 'e.station_id'), ('equipment', 'e.equipment_id')):
        v = parse_int(request.args.get(key), key, required=False)
        if v:
            where.append(f'{col}=?'); params.append(v)
    rows = q(f'''SELECT e.*, f.name field_name, st.name station_name, eq.code equipment_code, u.full_name created_name,
                        cu.full_name checked_name
                 FROM expenses e LEFT JOIN fields f ON f.id=e.field_id LEFT JOIN stations st ON st.id=e.station_id
                 LEFT JOIN equipment eq ON eq.id=e.equipment_id LEFT JOIN users u ON u.id=e.created_by
                 LEFT JOIN users cu ON cu.id=e.checked_by
                 WHERE {' AND '.join(where)} ORDER BY e.expense_date DESC, e.id DESC LIMIT 500''', params)
    live = [r for r in rows if not r['voided_at']]
    by_cat = {}
    for r in live:
        by_cat[r['category']] = by_cat.get(r['category'], 0) + r['amount']
    return render_template('acct_expenses.html', **_ctx(rows=rows, status=status, total=sum(r['amount'] for r in live),
                                                        by_cat=sorted(by_cat.items(), key=lambda x: -x[1]),
                                                        fields=q('SELECT id, name FROM fields ORDER BY code'),
                                                        stations=q('SELECT id, name FROM stations ORDER BY name')))


@bp.post('/buxgalteriya/xarajat/<int:expense_id>/tasdiq')
@perm_required('expenses.approve')
def expense_approve(expense_id):
    A.approve_expense(post_actor(), expense_id)
    return done('Xarajat tasdiqlandi.', request.referrer or url_for('acct.expenses', status='TEKSHIRILMAGAN'))


# ------------------------------------------------------------------ workers and payment orders

@bp.get('/buxgalteriya/ishchilar')
@perm_required('acct.view')
def workers():
    year = season_arg()
    tab = request.args.get('tab', 'qoldiq')
    rows = A.worker_balances(year, search=(request.args.get('q') or '').strip(),
                             brigadier_id=parse_int(request.args.get('brigada'), 'Brigada', required=False),
                             field_id=parse_int(request.args.get('field'), 'Dala', required=False),
                             day=request.args.get('day') or None)
    counts = {'qoldiq': sum(1 for r in rows if r['payable'] > 0), 'tayyor': sum(1 for r in rows if r['pending'] > 0),
              'barchasi': len(rows)}
    if tab == 'qoldiq':
        rows = [r for r in rows if r['payable'] > 0]
    elif tab == 'tayyor':
        rows = [r for r in rows if r['pending'] > 0]
    return render_template('acct_workers.html', rows=rows, tab=tab, counts=counts, year=year,
                           brigadiers=q('SELECT id, name FROM brigadiers WHERE active=1 ORDER BY name'),
                           fields=q('SELECT id, name FROM fields WHERE active=1 ORDER BY code'),
                           rate=A.hand_rate())


@bp.get('/buxgalteriya/ishchi/<int:worker_id>')
@perm_required('acct.view')
def worker(worker_id):
    year = season_arg()
    w = q('SELECT w.*, b.name brigadier_name FROM workers w LEFT JOIN brigadiers b ON b.id=w.brigadier_id WHERE w.id=?',
          (worker_id,), one=True)
    if not w:
        abort(404)
    bal = next(iter(A.worker_balances(year, worker_id=worker_id)), None)
    harvests, money, orders = A.worker_history(worker_id, year)
    rates = {}
    for h in harvests:
        if not h['voided_at']:
            key = h['rate']
            r = rates.setdefault(key, {'rate': key, 'kg': 0, 'amount': 0})
            r['kg'] += h['kg']
            r['amount'] += h['amount'] or 0
    return render_template('acct_worker.html', w=w, bal=bal, harvests=harvests, money=money, orders=orders,
                           rates=sorted(rates.values(), key=lambda r: (r['rate'] is None, r['rate'] or 0)), year=year,
                           categories=__import__('surxon.services', fromlist=['CASH_CATEGORIES']).CASH_CATEGORIES)


@bp.post('/buxgalteriya/tolov')
@perm_required('payouts.prepare')
def payout_prepare():
    kind = request.form.get('kind', 'worker')
    target = parse_int(request.form.get('target_id'), 'Kimga')
    amount = parse_money(request.form.get('amount'), required=False)
    purpose = request.form.get('purpose', 'pay')
    pid, no = A.prepare_payout(post_actor(), kind=kind, target_id=target, amount=amount, purpose=purpose,
                               note=request.form.get('note', ''), client_uuid=form_uuid())
    p = q('SELECT amount FROM payouts WHERE id=?', (pid,), one=True)
    return done(f'{no}: {A.fmt_som(p["amount"])} to‘lovga tayyor. Kassir “berildi” qilganda kassadan chiqadi.',
                request.form.get('next') or url_for('acct.workers'), payout_id=pid, doc_no=no)


@bp.get('/buxgalteriya/tolovlar')
@perm_required('payouts.view')
def payouts():
    status = request.args.get('status', 'TAYYOR')
    p, a, b, label = _period()
    where, params = ['p.status=?'], [status]
    if status != 'TAYYOR':
        where.append('substr(COALESCE(p.paid_at, p.voided_at, p.prepared_at),1,10) BETWEEN ? AND ?'); params += [a, b]
    box = _my_box()
    if box:
        where.append('(p.cashbox_id=? OR p.cashbox_id IS NULL)'); params.append(box)
    rows = q(f'''SELECT p.*, w.full_name worker_name, w.phone, e.code combine_code, e.operator_name, u.full_name prepared_name,
                        pu.full_name paid_name, cb.name box
                 FROM payouts p LEFT JOIN workers w ON w.id=p.worker_id LEFT JOIN equipment e ON e.id=p.combine_id
                 LEFT JOIN users u ON u.id=p.prepared_by LEFT JOIN users pu ON pu.id=p.paid_by
                 LEFT JOIN cashboxes cb ON cb.id=p.cashbox_id
                 WHERE {' AND '.join(where)} ORDER BY p.id DESC LIMIT 500''', params)
    return render_template('acct_payouts.html', **_ctx(rows=rows, status=status, total=sum(r['amount'] for r in rows)))


@bp.post('/buxgalteriya/tolov/<int:payout_id>/berildi')
@perm_required('payouts.pay')
def payout_pay(payout_id):
    res = A.pay_payout(post_actor(), payout_id)
    back = request.form.get('next') or (url_for('acct.cashier') if g.user['role'] == 'cashier' else url_for('acct.payouts'))
    if res['already']:
        return done(f'{res["doc_no"]} allaqachon berilgan — ikkinchi marta berilmadi.', back, already=True)
    return done(f'{A.fmt_som(res["amount"])} berildi: {res["to"]} ({res["doc_no"]}).', back, doc_no=res['doc_no'])


@bp.post('/buxgalteriya/tolov/<int:payout_id>/bekor')
@perm_required('payouts.prepare')
def payout_cancel(payout_id):
    A.cancel_payout(post_actor(), payout_id, request.form.get('reason'))
    return done('To‘lov bekor qilindi (pul berilmagan edi).', request.referrer or url_for('acct.payouts'))


@bp.post('/buxgalteriya/tolov/<int:payout_id>/qaytarish')
@perm_required('payouts.prepare')
def payout_refund(payout_id):
    no = A.refund_payout(post_actor(), payout_id, request.form.get('reason'))
    return done(f'Qaytarish yozildi: {no}. Asl to‘lov tarixda o‘zgarmay qoldi.', request.referrer or url_for('acct.payouts'))


# ------------------------------------------------------------------ cashier

@bp.get('/kassir')
@perm_required('payouts.pay')
def cashier():
    year = season_arg()
    t = today_str()
    box = _my_box()
    search = (request.args.get('q') or '').strip()
    where, params = ["p.status='TAYYOR'"], []
    if box:
        where.append('(p.cashbox_id=? OR p.cashbox_id IS NULL)'); params.append(box)
    if search:
        where.append('(w.full_name LIKE ? OR IFNULL(w.phone,\'\') LIKE ? OR e.code LIKE ? OR p.doc_no LIKE ?)')
        params += [f'%{search}%'] * 4
    ready = q(f'''SELECT p.*, w.full_name worker_name, w.phone, b.name brigadier_name, e.code combine_code, e.operator_name,
                         u.full_name prepared_name
                  FROM payouts p LEFT JOIN workers w ON w.id=p.worker_id LEFT JOIN brigadiers b ON b.id=w.brigadier_id
                  LEFT JOIN equipment e ON e.id=p.combine_id LEFT JOIN users u ON u.id=p.prepared_by
                  WHERE {' AND '.join(where)} ORDER BY p.id''', params)
    paid = q('''SELECT p.*, w.full_name worker_name, e.code combine_code FROM payouts p
                LEFT JOIN workers w ON w.id=p.worker_id LEFT JOIN equipment e ON e.id=p.combine_id
                WHERE p.status='BERILDI' AND substr(p.paid_at,1,10)=?''' + (' AND p.cashbox_id=?' if box else '') +
             ' ORDER BY p.paid_at DESC', (t,) + ((box,) if box else ()))
    boxes = [box] if box else [b['id'] for b in A.cashboxes()]
    from ..db import get_db
    db = get_db()
    shift = []
    for b in boxes:
        f = A.day_figures(db, b, t)
        f['name'] = scalar('SELECT name FROM cashboxes WHERE id=?', (b,))
        f['closed'] = bool(scalar('SELECT 1 FROM cash_days WHERE cashbox_id=? AND day=?', (b, t)))
        shift.append(f)
    return render_template('acct_cashier.html', ready=ready, paid=paid, shift=shift, search=search, year=year,
                           ready_sum=sum(r['amount'] for r in ready), paid_sum=sum(r['amount'] for r in paid))


# ------------------------------------------------------------------ combines

@bp.route('/buxgalteriya/kombaynlar', methods=['GET', 'POST'])
@perm_required('acct.view')
def combines():
    if request.method == 'POST':
        require('combine.finance')
        cid = parse_int(request.form.get('combine_id'), 'Kombayn')
        action = request.form.get('action')
        if action == 'tariff':
            ttype = request.form.get('tariff_type') or None
            A.set_combine_tariff(post_actor(), cid, tariff_type=ttype,
                                 tariff_rate=parse_money(request.form.get('tariff_rate'), required=bool(ttype)),
                                 operator_name=request.form.get('operator_name'),
                                 ownership=request.form.get('ownership') or None)
            return done('Tarif saqlandi. U bundan keyingi ishga qo‘llanadi; oldingi hisob o‘zgarmaydi.', url_for('acct.combines'))
        if action == 'work':
            A.add_combine_work(post_actor(), cid, work_date=parse_date(request.form.get('work_date') or today_str(), 'Sana'),
                               unit=request.form.get('unit'), qty=parse_number(request.form.get('qty'), 'Miqdor'),
                               field_id=parse_int(request.form.get('field_id'), 'Dala', required=False),
                               note=request.form.get('note', ''))
            return done('Ish hajmi yozildi.', url_for('acct.combines'))
        raise UserError('Noma’lum amal.')
    year = season_arg()
    rows = A.combine_balances(year)
    fuel = {r['equipment_id']: r['l'] for r in q('''SELECT equipment_id, SUM(liters) l FROM fuel_ops WHERE kind='BERISH'
                                                   AND voided_at IS NULL AND substr(created_at,1,4)=? GROUP BY equipment_id''',
                                                 (str(year),))}
    for r in rows:
        r['fuel_l'] = fuel.get(r['id'], 0)
    return render_template('acct_combines.html', rows=rows, year=year, tariffs=A.TARIFF_TYPES,
                           fields=q('SELECT id, name FROM fields WHERE active=1 ORDER BY code'),
                           total={k: sum(r[k] for r in rows) for k in ('earned', 'paid', 'balance', 'kg')})


# ------------------------------------------------------------------ cotton / punkt (read from field and punkt)

GROUPS = {'dala': ('Dala', 'f.name'), 'punkt': ('Punkt', 'st.name'), 'kun': ('Kun', 'wb.document_date'),
          'brigada': ('Brigada', 'b.name'), 'transport': ('Transport', "COALESCE(tr.code, t.code)")}


@bp.get('/buxgalteriya/paxta')
@perm_required('acct.view')
def cotton():
    p, a, b, label = _period()
    year = season_arg()
    by = request.args.get('by', 'dala')
    if by == 'reys':
        return render_template('acct_cotton.html', **_ctx(rows=[], by=by, groups=GROUPS, cot=A.cotton_totals(year, a, b),
                                                          trips=A.trip_ledger(year, a, b)))
    if by not in GROUPS:
        by = 'dala'
    col = GROUPS[by][1]
    rows = q(f'''SELECT {col} grp, COUNT(*) trips, SUM(wb.net_kg) field_kg,
                        SUM(CASE WHEN wb.status='QABUL' THEN wb.net_kg END) received_field_kg,
                        SUM(nr.accepted_kg) punkt_kg, SUM(CASE WHEN wb.status='QABUL' THEN nr.accepted_kg - wb.net_kg END) diff_kg,
                        SUM(CASE WHEN wb.status='YARATILDI' THEN 1 ELSE 0 END) open_n
                 FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id JOIN equipment t ON t.id=tl.trailer_id
                 LEFT JOIN equipment tr ON tr.id=tl.tractor_id LEFT JOIN fields f ON f.id=tl.field_id
                 LEFT JOIN brigadiers b ON b.id=tl.brigadier_id LEFT JOIN stations st ON st.id=tl.station_id
                 LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                 WHERE wb.status<>'BEKOR' AND wb.document_date BETWEEN ? AND ?
                 GROUP BY grp ORDER BY {'grp DESC' if by == 'kun' else 'field_kg DESC'}''', (a, b))
    out = []
    for r in rows:
        d = dict(r)
        d['pct'] = round(d['diff_kg'] / d['received_field_kg'] * 100, 2) if d['received_field_kg'] and d['diff_kg'] is not None else None
        out.append(d)
    return render_template('acct_cotton.html', **_ctx(rows=out, by=by, groups=GROUPS, cot=A.cotton_totals(year, a, b)))


# ------------------------------------------------------------------ cash book (phone summary) and day close

@bp.get('/buxgalteriya/kassa')
@perm_required('acct.view')
def cash():
    p, a, b, label = _period()
    box = parse_int(request.args.get('kassa'), 'Kassa', required=False)
    where, params = ['c.entry_date BETWEEN ? AND ?'], [a, b]
    if box:
        where.append('c.cashbox_id=?'); params.append(box)
    rows = q(f'''SELECT c.*, cb.name box, w.full_name worker_name, e.code combine_code, u.full_name created_name
                 FROM cash_entries c LEFT JOIN cashboxes cb ON cb.id=c.cashbox_id LEFT JOIN workers w ON w.id=c.worker_id
                 LEFT JOIN equipment e ON e.id=c.combine_id LEFT JOIN users u ON u.id=c.created_by
                 WHERE {' AND '.join(where)} ORDER BY c.entry_date DESC, c.id DESC LIMIT 1000''', params)
    from ..db import get_db
    from ..services import CASH_CATEGORIES
    db = get_db()
    boxes = [dict(bx, balance=A.box_balance(db, bx['id'])) for bx in A.cashboxes()]
    tot = A.cash_totals(db, box, a, b)
    return render_template('acct_cash.html', **_ctx(rows=rows, boxes=boxes, box=box, tot=tot, categories=CASH_CATEGORIES,
                                                    balance=sum(x['balance'] for x in boxes if not box or x['id'] == box)))


@bp.route('/buxgalteriya/kun-yopish', methods=['GET', 'POST'])
@perm_required('acct.view')
def day_close():
    from ..db import get_db
    db = get_db()
    if request.method == 'POST':
        require('dayclose')
        res = A.close_day(post_actor(), cashbox_id=parse_int(request.form.get('cashbox_id'), 'Kassa'),
                          day=parse_date(request.form.get('day'), 'Sana'), counted=parse_money(request.form.get('counted')),
                          reason=request.form.get('reason', ''), note=request.form.get('note', ''))
        msg = 'Kun yopildi.' + (f' Farq {A.fmt_som(res["diff"])} kassa kitobiga yozildi.' if res['diff'] else ' Farq yo‘q.')
        return done(msg, url_for('acct.day_close'))
    day = request.args.get('day') or today_str()
    box = parse_int(request.args.get('kassa'), 'Kassa', required=False) or A.default_cashbox(db)
    fig = A.day_figures(db, box, day)
    closed = q('SELECT * FROM cash_days WHERE cashbox_id=? AND day=?', (box, day), one=True)
    history = q('''SELECT cd.*, cb.name box, u.full_name closed_name FROM cash_days cd JOIN cashboxes cb ON cb.id=cd.cashbox_id
                   LEFT JOIN users u ON u.id=cd.closed_by ORDER BY cd.day DESC, cd.id DESC LIMIT 60''')
    return render_template('acct_day_close.html', day=day, box=box, boxes=A.cashboxes(), fig=fig, closed=closed,
                           history=history, reasons=A.DAY_DIFF_REASONS)


# ------------------------------------------------------------------ debts

@bp.route('/buxgalteriya/qarzlar', methods=['GET', 'POST'])
@perm_required('acct.view')
def debts():
    if request.method == 'POST':
        require('debts.write')
        if request.form.get('action') == 'settle':
            no = A.settle_debt(post_actor(), parse_int(request.form.get('debt_id'), 'Qarz'),
                               amount=parse_money(request.form.get('amount'), required=False), note=request.form.get('note', ''))
            return done(f'Kassa orqali yozildi: {no}.', url_for('acct.debts'))
        A.add_debt(post_actor(), direction=request.form.get('direction'), counterparty=request.form.get('counterparty'),
                   amount=parse_money(request.form.get('amount')), reason=request.form.get('reason', ''),
                   debt_date=parse_date(request.form.get('debt_date') or today_str(), 'Sana'),
                   due_date=parse_date(request.form.get('due_date'), 'Muddat') if request.form.get('due_date') else None,
                   note=request.form.get('note', ''), client_uuid=form_uuid())
        return done('Qarz yozuvi saqlandi.', url_for('acct.debts'))
    year = season_arg()
    rows = A.debts(year)
    return render_template('acct_debts.html', rows=rows, year=year,
                           olish=sum(r['remaining'] for r in rows if r['direction'] == 'OLISH'),
                           berish=sum(r['remaining'] for r in rows if r['direction'] == 'BERISH'))


# ------------------------------------------------------------------ period report (screen / Excel / PDF)

@bp.get('/buxgalteriya/hisobot')
@perm_required('reports.finance')
def report():
    p, a, b, label = _period()
    s = finance_summary(a, b)
    title = f'Moliya hisoboti — {label}'
    fmt = request.args.get('format')
    if fmt == 'xlsx':
        rows = [{'k': 'Terilgan (dala), kg', 'v': s['cotton']['field_kg']},
                {'k': 'Punkt qabul, kg', 'v': s['cotton']['punkt_kg']},
                {'k': 'Farq, kg', 'v': s['cotton']['diff_kg']},
                {'k': 'Farq, %', 'v': s['cotton']['diff_pct']},
                {'k': 'Terimchilar puli (hisoblangan)', 'v': s['wages_earned']},
                {'k': 'Narxsiz terim, kg (hisoblanmagan)', 'v': s['wages_uncalc_kg']},
                {'k': 'Kombayn (hisoblangan)', 'v': s['combine_earned']}]
        rows += [{'k': f'Xarajat: {e["category"]}', 'v': e['amount']} for e in s['expenses']]
        from ..reporting import brigade_summary
        for x in brigade_summary(a, b):
            rows += [{'k': f'{x["name"]}: terim, kg', 'v': x['kg']}, {'k': f'{x["name"]}: punkt qabul, kg', 'v': x['accepted_kg']},
                     {'k': f'{x["name"]}: ish haqi + kombayn + xarajat, so‘m', 'v': x['total_cost']}]
        rows += [{'k': 'JAMI XARAJAT', 'v': s['expenses_total']}, {'k': 'Kassa kirim', 'v': s['cash_in']},
                 {'k': 'Kassa chiqim', 'v': s['cash_out']}, {'k': 'shundan ish haqi', 'v': s['wages_paid']},
                 {'k': 'shundan avans', 'v': s['advances']}, {'k': 'shundan kombayn', 'v': s['combine_paid']},
                 {'k': 'KASSA QOLDIQ (davr oxiri)', 'v': s['cash_balance']}]
        data = to_xlsx({'title': f'{title} ({a} — {b})', 'columns': [('k', 'Ko‘rsatkich', 'text'), ('v', 'Qiymat', 'num')],
                        'rows': rows, 'sum': []})
        return Response(data.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f'attachment; filename=moliya_{a}_{b}.xlsx'})
    if fmt == 'pdf':
        pdf = summary_pdf(s, f'{title} ({a} — {b})', get_setting('company_name'))
        return Response(pdf, mimetype='application/pdf', headers={'Content-Disposition': f'inline; filename=moliya_{a}_{b}.pdf'})
    from ..reporting import brigade_summary
    return render_template('acct_report.html', **_ctx(s=s, title=title, brig=brigade_summary(a, b)))
