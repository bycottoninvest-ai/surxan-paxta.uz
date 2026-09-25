"""“Mening kassam” on the phone — four big buttons: pay a worker, write an expense, cash in, today's operations.

Each form is two steps on one page: “Tekshirish” (server computes the figures, nothing saved) and the final
“…tasdiqlash” (saved once; refused with fresh figures when the box or the debt changed meanwhile). Only a server
answer turns into “saqlandi” on the screen; without internet the page keeps what was typed and says so.
The money rules themselves live in surxon.wallet / surxon.accounting and check the permission again.
"""
from flask import Blueprint, Response, abort, g, jsonify, render_template, request, url_for

from .. import accounting as A
from .. import wallet as W
from ..db import q
from ..photos import read_upload
from ..security import can, current_actor, perm_required
from ..settings import get_setting
from ..utils import UserError, now_str, parse_int, parse_money, today_str
from . import done, form_uuid, post_actor, season_arg

bp = Blueprint('hamyon', __name__)

ANY_MONEY = ('payouts.pay', 'expenses.write', 'cash.write')


def _review(preview, template):
    return jsonify(ok=True, expect=preview['expect'], html=render_template(template, p=preview))


def _stale(err, template):
    return jsonify(ok=False, stale=True, error=str(err), expect=err.preview['expect'],
                   html=render_template(template, p=err.preview)), 409


@bp.get('/hamyon')
@perm_required(*ANY_MONEY)
def home():
    pending = 0
    if can('expenses.approve'):
        pending = q("SELECT COUNT(*) n FROM cash_corrections WHERE status='KUTILMOQDA'", one=True)['n']
    ready = 0
    if can('payouts.pay') and not can('payouts.prepare'):
        boxes = W.visible_boxes(g.user)
        ready = q(f"SELECT COUNT(*) n FROM payouts WHERE status='TAYYOR' AND (cashbox_id IS NULL OR cashbox_id IN "
                  f"({','.join('?' * len(boxes)) or 'NULL'}))", boxes, one=True)['n']
    return render_template('hamyon_home.html', boxes=W.boxes_with_balance(g.user), pending=pending, ready=ready,
                           own_box=g.user['role'] == 'cashier' and bool(g.user['cashbox_id']))


# ------------------------------------------------------------------ pay a worker

@bp.get('/hamyon/tolov')
@perm_required('payouts.pay')
def pay():
    if can('payouts.prepare'):
        return render_template('hamyon_pay_find.html', workers=W.search_workers(season_arg(), ''))
    # cashier: the orders the accountant prepared for this box
    boxes = W.visible_boxes(g.user)
    orders = q(f'''SELECT p.*, w.full_name worker_name, w.note worker_note, e.code combine_code FROM payouts p
                   LEFT JOIN workers w ON w.id=p.worker_id LEFT JOIN equipment e ON e.id=p.combine_id
                   WHERE p.status='TAYYOR' AND (p.cashbox_id IS NULL OR p.cashbox_id IN ({','.join('?' * len(boxes)) or 'NULL'}))
                   ORDER BY p.id''', boxes)
    return render_template('hamyon_orders.html', orders=orders)


@bp.get('/hamyon/ishchilar')
@perm_required('payouts.prepare')
def workers_json():
    rows = W.search_workers(season_arg(), request.args.get('q', ''))
    return jsonify(ok=True, workers=[{k: w[k] for k in ('id', 'name', 'note', 'debt', 'payable', 'last_brigade', 'last_field',
                                                         'namesake', 'brigades')} for w in rows])


@bp.get('/hamyon/tolov/<int:worker_id>')
@perm_required('payouts.prepare')
def pay_worker(worker_id):
    from ..db import get_db
    f = W.worker_figures(get_db(), season_arg(), worker_id)
    return render_template('hamyon_pay.html', w=f, boxes=W.boxes_with_balance(g.user))


def _pay_args(worker_id):
    return dict(worker_id=worker_id, amount=parse_money(request.form.get('amount'), 'Summa', required=False),
                cashbox_id=parse_int(request.form.get('cashbox_id'), 'Kassa', required=False))


@bp.post('/hamyon/tolov/<int:worker_id>/tekshir')
@perm_required('payouts.prepare')
def pay_check(worker_id):
    return _review(W.pay_preview(post_actor(), **_pay_args(worker_id)), 'partials/hm_review_pay.html')


@bp.post('/hamyon/tolov/<int:worker_id>/tasdiq')
@perm_required('payouts.prepare')
def pay_confirm(worker_id):
    a = _pay_args(worker_id)
    try:
        res = W.give_pay(post_actor(), a['worker_id'], a['amount'], cashbox_id=a['cashbox_id'], client_uuid=form_uuid(),
                         expect=request.form.get('expect') or None)
    except W.Stale as err:
        return _stale(err, 'partials/hm_review_pay.html')
    return done(('Bu to‘lov avval saqlangan — ikkinchi marta berilmadi. ' if res['already'] else '') + f'To‘lov saqlandi: {res["doc_no"]}.',
                url_for('hamyon.entry', cash_id=res['cash_id'], yangi=1), doc_no=res['doc_no'])


@bp.get('/hamyon/buyruq/<int:payout_id>')
@perm_required('payouts.pay')
def order(payout_id):
    p, prev = W.order_preview(current_actor(), payout_id)
    if p['status'] == 'BERILDI' and p['cash_entry_id']:
        return render_template('hamyon_order.html', p=p, prev=None, paid_url=url_for('hamyon.entry', cash_id=p['cash_entry_id']))
    return render_template('hamyon_order.html', p=p, prev=prev, paid_url=None)


@bp.post('/hamyon/buyruq/<int:payout_id>/tasdiq')
@perm_required('payouts.pay')
def order_confirm(payout_id):
    try:
        res = W.order_pay(post_actor(), payout_id, expect=request.form.get('expect') or None)
    except W.Stale as err:
        return _stale(err, 'partials/hm_review_pay.html')
    msg = (f'{res["doc_no"]} allaqachon berilgan — ikkinchi marta berilmadi.' if res['already']
           else f'{A.fmt_som(res["amount"])} berildi: {res["to"]} ({res["doc_no"]}).')
    return done(msg, url_for('hamyon.entry', cash_id=res['cash_id'], yangi=1), doc_no=res['doc_no'])


# ------------------------------------------------------------------ expense

@bp.get('/hamyon/xarajat')
@perm_required('expenses.write')
def expense():
    return render_template('hamyon_expense.html', types=W.PHONE_EXPENSES, boxes=W.boxes_with_balance(g.user),
                           fields=q('SELECT id, code, name FROM fields WHERE active=1 ORDER BY code'),
                           equipment=q("SELECT id, code, kind FROM equipment WHERE active=1 AND kind<>'telashka' ORDER BY kind, code"))


def _expense_args():
    return dict(category=request.form.get('category', ''), payer=request.form.get('payer', ''),
                amount=parse_money(request.form.get('amount'), 'Summa', required=False),
                cashbox_id=parse_int(request.form.get('cashbox_id'), 'Kassa', required=False))


@bp.post('/hamyon/xarajat/tekshir')
@perm_required('expenses.write')
def expense_check():
    return _review(W.expense_preview(post_actor(), **_expense_args()), 'partials/hm_review_money.html')


@bp.post('/hamyon/xarajat/tasdiq')
@perm_required('expenses.write')
def expense_confirm():
    a = _expense_args()
    link = {t[0]: t[2] for t in W.PHONE_EXPENSES}.get(a['category'])
    try:
        cid = W.write_expense(post_actor(), **a, note=request.form.get('note', ''),
                              field_id=parse_int(request.form.get('field_id'), 'Dala', required=False) if link == 'field' else None,
                              equipment_id=parse_int(request.form.get('equipment_id'), 'Texnika', required=False) if link == 'equipment' else None,
                              photo=read_upload(request.files.get('photo')), client_uuid=form_uuid(),
                              expect=request.form.get('expect') or None)
    except W.Stale as err:
        return _stale(err, 'partials/hm_review_money.html')
    return done('Xarajat saqlandi.', url_for('hamyon.entry', cash_id=cid, yangi=1))


# ------------------------------------------------------------------ income

@bp.get('/hamyon/kirim')
@perm_required('cash.write')
def income():
    return render_template('hamyon_income.html', sources=A.income_sources(), boxes=W.boxes_with_balance(g.user))


def _income_args():
    return dict(source=request.form.get('source', ''), amount=parse_money(request.form.get('amount'), 'Summa', required=False),
                cashbox_id=parse_int(request.form.get('cashbox_id'), 'Kassa', required=False))


@bp.post('/hamyon/kirim/tekshir')
@perm_required('cash.write')
def income_check():
    return _review(W.income_preview(post_actor(), **_income_args()), 'partials/hm_review_money.html')


@bp.post('/hamyon/kirim/tasdiq')
@perm_required('cash.write')
def income_confirm():
    try:
        cid = W.write_income(post_actor(), **_income_args(), note=request.form.get('note', ''),
                             photo=read_upload(request.files.get('photo')), client_uuid=form_uuid(),
                             expect=request.form.get('expect') or None)
    except W.Stale as err:
        return _stale(err, 'partials/hm_review_money.html')
    return done('Kirim saqlandi.', url_for('hamyon.entry', cash_id=cid, yangi=1))


# ------------------------------------------------------------------ today and one operation

@bp.get('/hamyon/bugun')
@perm_required(*ANY_MONEY)
def today():
    rows = W.today_ops(g.user)
    live = [r for r in rows if not r['voided_at']]
    return render_template('hamyon_today.html', rows=rows, boxes=W.boxes_with_balance(g.user),
                           inflow=sum(r['amount'] for r in live if r['direction'] == 'IN'),
                           outflow=sum(r['amount'] for r in live if r['direction'] == 'OUT'))


def _entry_or_404(cash_id):
    if not (can('cash.view') or any(can(p) for p in ANY_MONEY)):
        abort(403)
    e = W.get_entry(g.user, cash_id)
    if not e:
        abort(404)
    return e


@bp.get('/hamyon/amal/<int:cash_id>')
@perm_required('cash.view', *ANY_MONEY)
def entry(cash_id):
    e = _entry_or_404(cash_id)
    from .main import can_see_photo
    photos = [p for p in q('''SELECT * FROM photos WHERE (entity_type='cash_entry' AND entity_id=?)
                              OR (entity_type='expense' AND entity_id=?) ORDER BY id''', (cash_id, e['expense_id'] or -1))
              if can_see_photo(p)]
    corrs = W.corrections_of(cash_id)
    pending = next((c for c in corrs if c['status'] == 'KUTILMOQDA' and c['cash_entry_id'] == cash_id), None)
    plan = W.pending_plan(pending['id'])[1] if pending and can('expenses.approve') else None
    return render_template('hamyon_entry.html', e=e, photos=photos, corrs=corrs, pending=pending, plan=plan,
                           fresh=request.args.get('yangi') == '1',
                           may_fix=W.can_correct(current_actor(), e) and not pending and request.args.get('ro') != '1',
                           may_refund=e['kind'] == 'pay' and not e['voided_at'] and can('payouts.prepare') and request.args.get('ro') != '1')


@bp.get('/hamyon/amal/<int:cash_id>.pdf')
@perm_required('cash.view', *ANY_MONEY)
def entry_pdf(cash_id):
    e = _entry_or_404(cash_id)
    from ..pdfdoc import build_cash_pdf
    pdf = build_cash_pdf(e, W.corrections_of(cash_id), company=get_setting('company_name'),
                         printed_by=g.user['full_name'], printed_at=now_str())
    disp = 'attachment' if request.args.get('download') == '1' else 'inline'
    return Response(pdf, mimetype='application/pdf', headers={'Content-Disposition': f'{disp}; filename="{e["doc_no"]}.pdf"',
                                                             'Cache-Control': 'private, no-store'})


# ------------------------------------------------------------------ corrections

def _fix_args():
    return dict(reason=request.form.get('reason', ''), note=request.form.get('note', ''),
                new_amount=parse_money(request.form.get('new_amount'), 'Yangi summa', required=False),
                new_worker_id=parse_int(request.form.get('new_worker_id'), 'Ishchi', required=False),
                new_category=request.form.get('new_category') or None, new_party=request.form.get('new_party') or None)


@bp.get('/hamyon/amal/<int:cash_id>/tuzatish')
@perm_required(*ANY_MONEY)
def fix(cash_id):
    e = _entry_or_404(cash_id)
    if not W.can_correct(current_actor(), e):
        raise UserError('Bu amalni tuzatib bo‘lmaydi yoki huquqingiz yo‘q.')
    return render_template('hamyon_fix.html', e=e, reasons=W.reasons_for(e['kind']), other=W.OTHER,
                           categories=W.PHONE_EXPENSE_NAMES, applies_now=can('expenses.approve'))


@bp.post('/hamyon/amal/<int:cash_id>/tuzatish/tekshir')
@perm_required(*ANY_MONEY)
def fix_check(cash_id):
    actor = post_actor()
    return _review(W.correction_preview(actor, g.user, cash_id, **_fix_args()), 'partials/hm_review_fix.html')


@bp.post('/hamyon/amal/<int:cash_id>/tuzatish/tasdiq')
@perm_required(*ANY_MONEY)
def fix_confirm(cash_id):
    a = _fix_args()
    try:
        res = W.request_correction(post_actor(), g.user, cash_id, a.pop('reason'), client_uuid=form_uuid(),
                                   expect=request.form.get('expect') or None, **a)
    except W.Stale as err:
        return _stale(err, 'partials/hm_review_fix.html')
    if res['status'] == 'BAJARILDI':
        return done('Tuzatish saqlandi. Asl yozuv tarixda qoldi.',
                    url_for('hamyon.entry', cash_id=res['new_cash_id'] or cash_id))
    return done('Tuzatish so‘rovi yuborildi — buxgalter tasdiqlagach kuchga kiradi.', url_for('hamyon.entry', cash_id=cash_id))


@bp.post('/hamyon/tuzatish/<int:corr_id>/tasdiq')
@perm_required('expenses.approve')
def fix_approve(corr_id):
    res = W.approve_correction(post_actor(), corr_id)
    c = q('SELECT cash_entry_id FROM cash_corrections WHERE id=?', (corr_id,), one=True)
    return done('Tuzatish tasdiqlandi.' if not res['already'] else 'Bu tuzatish allaqachon hal qilingan.',
                url_for('hamyon.entry', cash_id=res['new_cash_id'] or c['cash_entry_id']))


@bp.post('/hamyon/tuzatish/<int:corr_id>/rad')
@perm_required('expenses.approve')
def fix_reject(corr_id):
    W.reject_correction(post_actor(), corr_id, request.form.get('note', ''))
    c = q('SELECT cash_entry_id FROM cash_corrections WHERE id=?', (corr_id,), one=True)
    return done('Tuzatish so‘rovi rad etildi — asl yozuv o‘zgarmadi.', url_for('hamyon.entry', cash_id=c['cash_entry_id']))


@bp.get('/hamyon/tuzatishlar')
@perm_required('cash.view', 'expenses.approve')
def fixes():
    return render_template('hamyon_fixes.html', rows=W.corrections_list())


@bp.post('/hamyon/amal/<int:cash_id>/qaytdi')
@perm_required('payouts.prepare')
def refund(cash_id):
    e = _entry_or_404(cash_id)
    if e['kind'] != 'pay':
        raise UserError('Faqat ishchiga berilgan pul qaytariladi.')
    no = A.refund_payout(post_actor(), e['payout_id'], request.form.get('reason'))
    return done(f'Pul kassaga qaytgani yozildi: {no}. Asl to‘lov tarixda qoldi.', url_for('hamyon.entry', cash_id=cash_id))
