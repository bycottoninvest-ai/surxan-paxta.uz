"""SURXON TEZ-PUL screens: the cashier's two steps (QR → kg + PUL BERILDI), the rahbar's day panel and the talon print.

The cashier screen never queues anything offline: a payment exists only when the server answered “paid”.
"""
from flask import Blueprint, Response, g, jsonify, redirect, render_template, request, url_for

from .. import tezpul as T
from ..db import q
from ..security import can, current_actor, perm_required
from ..settings import get_setting
from ..utils import UserError, now_str, parse_int, today_str
from . import done, form_uuid, post_actor

bp = Blueprint('tezpul', __name__)


@bp.get('/tezpul')
@perm_required('payouts.pay', 'tezpul.panel')
def home():
    return redirect(url_for('tezpul.kassa' if can('payouts.pay') and not can('tezpul.manage') else 'tezpul.panel'))


@bp.get('/tezpul/kassa')
@perm_required('payouts.pay')
def kassa():
    from .. import accounting as A
    from ..db import get_db
    db = get_db()
    box = A._cashbox(db, current_actor(), None)
    name = q('SELECT name FROM cashboxes WHERE id=?', (box,), one=True)
    return render_template('tezpul_kassa.html', rate=T.rate(), box_name=name['name'] if name else 'Kassa',
                           today=T.today_counter(current_actor()))


def _refused(err):
    return jsonify(ok=False, refused=err.kind, error=str(err), talon={k: v for k, v in (err.talon or {}).items() if k != 'id'}), 409


@bp.post('/tezpul/talon')
@perm_required('payouts.pay')
def lookup():
    try:
        t = T.lookup(post_actor(), request.form.get('code'), request.form.get('manual_code', ''))
    except T.Refused as err:
        return _refused(err)
    return jsonify(ok=True, talon=t)


@bp.post('/tezpul/tolash')
@perm_required('payouts.pay')
def pay():
    try:
        r = T.pay(post_actor(), request.form.get('code'), request.form.get('kg'),
                  manual_code=request.form.get('manual_code', ''), client_uuid=form_uuid())
    except T.Refused as err:
        return _refused(err)
    return jsonify(ok=True, result=r, today=T.today_counter(current_actor()))


# ------------------------------------------------------------------ rahbar

@bp.get('/tezpul/panel')
@perm_required('tezpul.panel')
def panel():
    day = request.args.get('kun') or today_str()
    if request.args.get('format') == 'xlsx':
        return _xlsx(day)
    return render_template('tezpul_panel.html', p=T.panel(day), status=T.STATUS, num=T.number_text, today=today_str())


def _xlsx(day):
    from ..exports import to_xlsx
    rows = q('''SELECT t.number, t.kg, t.rate, t.amount, t.paid_at, b.name brigade, u.full_name cashier, cb.name box
                FROM tezpul_talons t LEFT JOIN brigadiers b ON b.id=t.brigadier_id LEFT JOIN users u ON u.id=t.paid_by
                LEFT JOIN cashboxes cb ON cb.id=t.cashbox_id
                WHERE t.status='TOLANDI' AND t.pay_date=? ORDER BY t.paid_at''', (day,))
    spec = {'title': f'SURXON TEZ-PUL — {day[8:10]}.{day[5:7]}.{day[:4]} to‘langan talonlar',
            'columns': [('number', 'Talon №', 'text'), ('kg', 'Kg', 'kg'), ('rate', 'Narx, so‘m/kg', 'money'),
                        ('amount', 'Summa, so‘m', 'money'), ('brigade', 'Brigada', 'text'), ('cashier', 'Kassir', 'text'),
                        ('box', 'Kassa', 'text'), ('paid_at', 'Vaqt', 'text')],
            'rows': [dict(r, number=T.number_text(r['number'])) for r in rows],
            'totals': {'kg': sum(r['kg'] for r in rows), 'amount': sum(r['amount'] for r in rows)}}
    return Response(to_xlsx(spec), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename=tezpul_{day}.xlsx'})


@bp.get('/tezpul/panel/raqamlar')
@perm_required('tezpul.panel')
def panel_json():
    """The panel refreshes itself with this every 20 s."""
    p = T.panel(request.args.get('kun') or None)
    return jsonify(ok=True, today=p['today'], season=p['season'], waiting=len(p['waiting']), unpaid=p['unpaid'],
                   errors=len(p['errors']), at=now_str()[11:16])


@bp.post('/tezpul/panel/tasdiq')
@perm_required('tezpul.manage')
def approve():
    T.approve(post_actor(), request.form.get('number'), request.form.get('kg'))
    return done('Tasdiqlandi — kassir endi pul bera oladi.', url_for('tezpul.panel'))


@bp.post('/tezpul/panel/rad')
@perm_required('tezpul.manage')
def reject():
    T.reject(post_actor(), request.form.get('number'), request.form.get('reason'))
    return done('Rad etildi — talon qayta skanerlanadi.', url_for('tezpul.panel'))


# ------------------------------------------------------------------ talons (admin / rahbar)

@bp.route('/tezpul/talonlar', methods=['GET', 'POST'])
@perm_required('tezpul.manage')
def talons():
    if request.method == 'POST':
        action = request.form.get('action')
        actor = post_actor()
        bid = parse_int(request.form.get('brigadier_id'), 'Brigada', required=False)
        if action == 'create':
            batch, a, b = T.create_batch(actor, request.form.get('count'), bid)
            return done(f'{a} — {b} talonlar tayyor. Endi PDF ni chop eting.', url_for('tezpul.talons', yangi=batch))
        if action == 'assign':
            n = T.assign_range(actor, request.form.get('first'), request.form.get('last'), bid)
            return done(f'{n} ta talon brigadaga berildi.', url_for('tezpul.talons'))
        if action == 'void':
            T.void(actor, request.form.get('number'), request.form.get('reason'))
            return done('Talon bekor qilindi — endi unga pul berilmaydi.', url_for('tezpul.talons'))
        raise UserError('Noma’lum amal.')
    brigades = q('SELECT id, name FROM brigadiers WHERE active=1 ORDER BY name')
    return render_template('tezpul_talons.html', batches=T.batches(), ranges=T.ranges(), brigades=brigades,
                           num=T.number_text, new=request.args.get('yangi', type=int), rate=T.rate(), max_kg=T.max_kg())


@bp.get('/tezpul/talonlar/<int:batch>.pdf')
@perm_required('tezpul.manage')
def talons_pdf(batch):
    from ..pdfdoc import build_tezpul_talons_pdf
    rows = T.talons_for_print(batch=batch)
    if not rows:
        raise UserError('Bu partiyada talon yo‘q.')
    pdf = build_tezpul_talons_pdf(rows, company=get_setting('company_name'))
    return Response(pdf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'inline; filename=talonlar_{T.number_text(rows[0]["number"])}.pdf'})
