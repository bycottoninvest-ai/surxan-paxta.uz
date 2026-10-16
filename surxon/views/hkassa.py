"""Hisobchilar kassasi screens: the cashier gives the field clerks their day's cash and takes back what is left;
the clerk confirms “OLDIM” on his phone. The money rules live in surxon.hkassa (and check the permission again)."""
from flask import Blueprint, g, render_template, request, url_for

from .. import accounting as A
from .. import hkassa as H
from ..db import get_db
from ..security import current_actor, login_required, perm_required
from ..utils import UserError, parse_int, today_str
from . import done, form_uuid, post_actor

bp = Blueprint('hkassa', __name__)


@bp.get('/kassa/hisobchilar')
@perm_required('hkassa.give')
def cashier():
    day = request.args.get('kun') or today_str()
    actor = current_actor()
    box = A._cashbox(get_db(), actor, None)
    return render_template('hkassa_cashier.html', o=H.overview(day), states=H.STATES, my_balance=A.box_balance(get_db(), box),
                           today=today_str(), clerks=H.candidates() if g.user['role'] == 'admin' else [])


@bp.post('/kassa/hisobchilar/berish')
@perm_required('hkassa.give')
def give():
    tid = parse_int(request.form.get('tally_id'), 'Hisobchi')
    H.give(post_actor(), tid, request.form.get('amount'), request.form.get('note', ''), client_uuid=form_uuid())
    return done('Berildi — hisobchi telefonida “OLDIM” ni bossin.', url_for('hkassa.cashier') + f'#h{tid}')


@bp.post('/kassa/hisobchilar/qabul')
@perm_required('hkassa.give')
def receive():
    tid = parse_int(request.form.get('tally_id'), 'Hisobchi')
    r = H.receive(post_actor(), tid, request.form.get('counted'), request.form.get('note', ''), client_uuid=form_uuid())
    msg = f'Qabul qilindi: {A.fmt_som(r["counted"])}.' + (f' ⚠ Farq: {A.fmt_som(r["diff"])} — hisobchida qoldi.' if r['diff']
                                                          else ' Farqsiz ✓')
    return done(msg, url_for('hkassa.cashier') + f'#h{tid}', result=r)


@bp.post('/kassa/hisobchilar/<int:fid>/bekor')
@perm_required('hkassa.give')
def cancel(fid):
    H.cancel(post_actor(), fid, request.form.get('reason', ''))
    return done('Pul kassaga qaytdi.', url_for('hkassa.cashier'))


@bp.post('/kassa/hisobchilar/yoqish')
@perm_required('hkassa.give')
def enable():
    uid = parse_int(request.form.get('user_id'), 'Hisobchi')
    on = request.form.get('on') == '1'
    H.enable(post_actor(), uid, on)
    return done('Yoqildi — endi bu hisobchi talon bo‘yicha pulni o‘zi beradi.' if on else 'O‘chirildi.',
                url_for('hkassa.cashier'))


@bp.get('/dala/kassam')
@login_required
def mine():
    from flask import redirect
    return redirect(url_for('dala.home'))


@bp.post('/dala/kassam/<int:fid>/oldim')
@login_required
def accept(fid):
    actor = post_actor()
    if not actor or not actor.pays_cash:
        raise UserError('Bu amal uchun huquqingiz yo‘q.')
    r = H.accept(actor, fid)
    return done(f'✓ {A.fmt_som(r["amount"])} qabul qilindi — endi talon bo‘yicha pul bera olasiz.', url_for('dala.home'))
