"""SURXON TEZ-PUL screens: the field clerk's two steps (QR → kg → SAQLASH), the cashier's two steps (QR → PUL BERILDI),
the rahbar's day panel and the talon print.

The cashier screen never queues anything offline: a payment exists only when the server answered “paid”.
"""
from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, url_for

from .. import tezpul as T
from ..db import q
from ..security import can, current_actor, perm_required
from ..settings import get_setting
from ..utils import UserError, now_str, today_str
from . import done, form_uuid, post_actor

bp = Blueprint('tezpul', __name__)


@bp.get('/tezpul')
@perm_required('payouts.pay', 'tezpul.panel')
def home():
    return redirect(url_for('tezpul.kassa' if can('payouts.pay') and not can('tezpul.manage') else 'tezpul.panel'))


def _refused(err):
    return jsonify(ok=False, refused=err.kind, error=str(err), talon={k: v for k, v in (err.talon or {}).items() if k != 'id'}), 409


# ------------------------------------------------------------------ field clerk

@bp.get('/tezpul/dala/<int:load_id>')
@perm_required('harvest.write')
def dala(load_id):
    from .dala import _trip
    ld = _trip(load_id)
    if ld['status'] != 'OCHIQ' or (ld['method'] or 'hand') != 'hand':
        flash('Talon faqat ochiq qo‘l terimi telashkasiga yoziladi.', 'error')
        return redirect(url_for('dala.trip', load_id=load_id))
    return render_template('tezpul_dala.html', ld=ld, count=T.trip_counter(load_id))


@bp.post('/tezpul/dala/talon')
@perm_required('harvest.write')
def dala_lookup():
    try:
        t = T.field_lookup(post_actor(), request.form.get('code'), request.form.get('manual_code', ''))
    except T.Refused as err:
        return _refused(err)
    return jsonify(ok=True, talon=t)


@bp.post('/tezpul/dala/<int:load_id>/saqlash')
@perm_required('harvest.write')
def dala_save(load_id):
    from .dala import _trip
    _trip(load_id)                                   # brigade scope + existence
    try:
        r = T.enter(post_actor(), load_id, request.form.get('code'), request.form.get('kg'),
                    group=request.form.get('group') == '1', manual_code=request.form.get('manual_code', ''),
                    client_uuid=form_uuid())
    except T.Refused as err:
        return _refused(err)
    return jsonify(ok=True, result=r, count=T.trip_counter(load_id))


# ------------------------------------------------------------------ cashier

@bp.get('/tezpul/kassa')
@perm_required('payouts.pay')
def kassa():
    from .. import accounting as A
    from ..db import get_db
    box = A._cashbox(get_db(), current_actor(), None)
    name = q('SELECT name FROM cashboxes WHERE id=?', (box,), one=True)
    return render_template('tezpul_kassa.html', box_name=name['name'] if name else 'Kassa', today=T.today_counter(current_actor()))


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
        r = T.pay(post_actor(), request.form.get('code'), manual_code=request.form.get('manual_code', ''),
                  client_uuid=form_uuid())
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
    rows = q('''SELECT t.number, t.kind, t.kg, t.rate, t.amount, t.status, t.entered_at, t.paid_at, b.name brigade,
                       e.full_name clerk, u.full_name cashier, tl.trip_no
                FROM tezpul_talons t LEFT JOIN brigadiers b ON b.id=t.brigadier_id LEFT JOIN users u ON u.id=t.paid_by
                LEFT JOIN users e ON e.id=t.entered_by LEFT JOIN trailer_loads tl ON tl.id=t.load_id
                WHERE substr(t.entered_at,1,10)=? OR t.pay_date=? ORDER BY t.number''', (day, day))
    spec = {'title': f'SURXON TEZ-PUL — {day[8:10]}.{day[5:7]}.{day[:4]} talonlar',
            'columns': [('number', 'Talon №', 'text'), ('kind', 'Turi', 'text'), ('kg', 'Kg', 'kg'), ('rate', 'Narx, so‘m/kg', 'money'),
                        ('amount', 'Summa, so‘m', 'money'), ('status', 'Holat', 'text'), ('brigade', 'Brigada', 'text'),
                        ('trip_no', 'Telashka', 'text'), ('clerk', 'Hisobchi', 'text'), ('entered_at', 'Kg yozilgan', 'text'),
                        ('cashier', 'Kassir', 'text'), ('paid_at', 'To‘langan', 'text')],
            'rows': [dict(r, number=T.number_text(r['number']), kind='Starshi' if r['kind'] == 'guruh' else 'Odam',
                          status=T.STATUS[r['status']][0]) for r in rows],
            'totals': {'kg': sum(r['kg'] or 0 for r in rows), 'amount': sum(r['amount'] or 0 for r in rows)}}
    return Response(to_xlsx(spec), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename=tezpul_{day}.xlsx'})


@bp.get('/tezpul/panel/raqamlar')
@perm_required('tezpul.panel')
def panel_json():
    """The panel refreshes itself with this every 20 s."""
    p = T.panel(request.args.get('kun') or None)
    return jsonify(ok=True, entered=p['entered'], paid=p['paid'], unpaid=p['unpaid'], at=now_str()[11:16])


# ------------------------------------------------------------------ talons (admin / rahbar)

@bp.route('/tezpul/talonlar', methods=['GET', 'POST'])
@perm_required('tezpul.manage')
def talons():
    if request.method == 'POST':
        action = request.form.get('action')
        actor = post_actor()
        if action == 'create':
            batch, a, b = T.create_batch(actor, request.form.get('count'))
            return done(f'{a} — {b} talonlar tayyor. Endi PDF ni chop eting.', url_for('tezpul.talons', yangi=batch))
        if action == 'void':
            T.void(actor, request.form.get('number'), request.form.get('reason'))
            return done('Talon bekor qilindi — endi u ishlatilmaydi.', url_for('tezpul.talons'))
        raise UserError('Noma’lum amal.')
    return render_template('tezpul_talons.html', batches=T.batches(), num=T.number_text, new=request.args.get('yangi', type=int))


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
