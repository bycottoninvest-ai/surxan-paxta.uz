"""Solyarka on the phone: the fuel keeper takes (station QR → liters → OLINDI) and gives (machine QR → liters →
camera photo → BERILDI); the accountant runs tickets, prices, funding and stations; the director sees everything.

Every write re-checks the permission in surxon.fuel. The keeper never sees a menu beyond these screens (security gate).
"""
import io

from flask import Blueprint, Response, abort, g, jsonify, redirect, render_template, request, url_for

from .. import fuel as F
from ..db import get_db, q
from ..photos import read_upload
from ..reporting import period_range
from ..security import can, current_actor, perm_required
from ..settings import get_setting
from ..utils import UserError, parse_int, parse_money, today_str
from . import done, form_uuid, post_actor, season_arg

bp = Blueprint('yoqilgi', __name__)


# ------------------------------------------------------------------ keeper (phone)

@bp.get('/yoqilgi')
@perm_required('fuel.operate', 'fuel.view', 'fuel.manage')
def home():
    if not can('fuel.operate') or g.user['role'] == 'admin' and request.args.get('op') != '1':
        return redirect(url_for('yoqilgi.manage'))
    db = get_db()
    return render_template('fuel_home.html', have=F.keeper_liters(db, g.user['id']), ops=F.my_ops(g.user['id']))


@bp.get('/yoqilgi/olish')
@perm_required('fuel.operate')
def op_take():
    return render_template('fuel_take.html')


@bp.get('/yoqilgi/berish')
@perm_required('fuel.operate')
def op_give():
    return render_template('fuel_give.html', have=F.keeper_liters(get_db(), g.user['id']), reasons=F.REASONS)


@bp.post('/yoqilgi/skan')
@perm_required('fuel.operate')
def op_scan():
    want = request.form.get('want')
    sid, t = F.scan(post_actor(), request.form.get('payload'), want)
    db = get_db()
    if want == 'station':
        tickets = [{'id': x['id'], 'doc_no': x['doc_no'], 'price': x['price_per_l'], 'balance': x['balance']}
                   for x in F.active_tickets(db, t['id'])]
        return jsonify(ok=True, scan_id=sid, station={'code': t['code'], 'name': t['name']}, tickets=tickets,
                       have=F.keeper_liters(db, g.user['id']))
    info = F.machine_info(db, t['id'])
    kinds = {'traktor': 'Traktor', 'kombayn': 'Kombayn', 'mashina': 'Mashina', 'telashka': 'Telashka'}
    return jsonify(ok=True, scan_id=sid, have=F.keeper_liters(db, g.user['id']),
                   machine={'code': t['code'], 'kind': kinds.get(t['kind'], t['kind']), 'plate': t['plate'] or '',
                            'operator': t['operator_name'] or '', 'carrier': bool(t['fuel_carrier']),
                            'today_l': F.fmt_l(info['today_l']), 'today_n': info['today_n'],
                            'last': (info['last_at'][11:16] + (' (bugun)' if info['last_at'][:10] == today_str() else
                                     ' · ' + info['last_at'][8:10] + '.' + info['last_at'][5:7])
                                     + f' · {F.fmt_l(info["last_l"])} L') if info['last_at'] else '—'})


@bp.post('/yoqilgi/olish')
@perm_required('fuel.operate')
def op_take_save():
    oid, again = F.take(post_actor(), scan_id=parse_int(request.form.get('scan_id'), 'QR skan'),
                        liters=request.form.get('liters'), ticket_id=parse_int(request.form.get('ticket_id'), 'Tiket', required=False),
                        client_uuid=form_uuid())
    return done('Solyarka olindi.' if not again else 'Bu olish avval saqlangan — ikkinchi marta yozilmadi.',
                url_for('yoqilgi.op_view', op_id=oid, yangi=1))


@bp.post('/yoqilgi/berish/tekshir')
@perm_required('fuel.operate')
def op_give_check():
    r = F.give_check(post_actor(), scan_id=parse_int(request.form.get('scan_id'), 'QR skan'), liters=request.form.get('liters'))
    return jsonify(ok=True, liters=F.fmt_l(r['liters']), have=F.fmt_l(r['have']), after=F.fmt_l(r['after']), flags=r['flags'])


@bp.post('/yoqilgi/berish')
@perm_required('fuel.operate')
def op_give_save():
    oid, again = F.give(post_actor(), scan_id=parse_int(request.form.get('scan_id'), 'QR skan'), liters=request.form.get('liters'),
                        photo=read_upload(request.files.get('photo')), reason=request.form.get('reason', ''),
                        reason_note=request.form.get('reason_note', ''), client_uuid=form_uuid())
    return done('Solyarka berildi.' if not again else 'Bu berish avval saqlangan — ikkinchi marta yozilmadi.',
                url_for('yoqilgi.op_view', op_id=oid, yangi=1))


@bp.get('/yoqilgi/amal/<int:op_id>')
@perm_required('fuel.operate', 'fuel.view', 'fuel.manage')
def op_view(op_id):
    o = F.op(op_id)
    if not o or (g.user['role'] == 'fuel' and o['keeper_id'] != g.user['id']):
        abort(404)
    cost = F.give_cost(get_db(), o) if o['kind'] == 'BERISH' and not o['voided_at'] and (can('fuel.view') or can('fuel.manage')) else None
    return render_template('fuel_op.html', o=o, fresh=request.args.get('yangi') == '1', cost=cost,
                           have=F.keeper_liters(get_db(), o['keeper_id']))


@bp.get('/yoqilgi/tarix')
@perm_required('fuel.operate')
def op_history():
    day = request.args.get('day') or today_str()
    return render_template('fuel_history.html', ops=F.my_ops(g.user['id'], day), day=day,
                           have=F.keeper_liters(get_db(), g.user['id']))


# ------------------------------------------------------------------ accountant / director

@bp.route('/yoqilgi/boshqaruv', methods=['GET', 'POST'])
@perm_required('fuel.view', 'fuel.manage')
def manage():
    if request.method == 'POST':
        actor = post_actor()
        a = request.form.get('action')
        if a == 'station':
            F.save_station(actor, parse_int(request.form.get('id'), 'ID', required=False), code=request.form.get('code', ''),
                           name=request.form.get('name', ''), address=request.form.get('address', ''),
                           approved=request.form.get('approved') == '1', active=request.form.get('active', '1') == '1')
            msg = 'Zapravka saqlandi.'
        elif a == 'ticket':
            F.create_ticket(actor, station_id=parse_int(request.form.get('station_id'), 'Zapravka'),
                            price_per_l=parse_money(request.form.get('price_per_l'), 'Litr narxi'),
                            amount=parse_money(request.form.get('amount'), 'Summa'), paid_from=request.form.get('paid_from', ''),
                            cashbox_id=parse_int(request.form.get('cashbox_id'), 'Kassa', required=False),
                            note=request.form.get('note', ''), client_uuid=form_uuid())
            msg = 'Tiket ochildi.'
        elif a == 'fund':
            F.add_funds(actor, parse_int(request.form.get('ticket_id'), 'Tiket'), amount=parse_money(request.form.get('amount'), 'Summa'),
                        paid_from=request.form.get('paid_from', ''),
                        cashbox_id=parse_int(request.form.get('cashbox_id'), 'Kassa', required=False),
                        note=request.form.get('note', ''), client_uuid=form_uuid())
            msg = 'Tiketga pul qo‘shildi.'
        elif a == 'price':
            F.set_price(actor, parse_int(request.form.get('ticket_id'), 'Tiket'), parse_money(request.form.get('price_per_l'), 'Narx'))
            msg = 'Yangi narx saqlandi — faqat keyingi olishlarga.'
        elif a == 'close':
            F.close_ticket(actor, parse_int(request.form.get('ticket_id'), 'Tiket'),
                           station_balance=parse_money(request.form.get('station_balance'), 'Zapravka qoldig‘i', required=False),
                           note=request.form.get('note', ''))
            msg = 'Tiket yopildi. Mas’ullardagi litr o‘zgarmadi.'
        elif a == 'void':
            F.void_op(actor, parse_int(request.form.get('op_id'), 'Amal'), request.form.get('reason', ''))
            msg = 'Amal bekor qilindi (tarixda qoldi).'
        elif a == 'machine':
            F.set_equipment_fuel(actor, parse_int(request.form.get('equipment_id'), 'Texnika'),
                                 fuel_type=request.form.get('fuel_type') or None, carrier=request.form.get('carrier') == '1')
            msg = 'Texnika saqlandi.'
        else:
            raise UserError('Noma’lum amal.')
        return done(msg, url_for('yoqilgi.manage', period=request.args.get('period', 'bugun')))
    p = request.args.get('period', 'bugun')
    a, b, label = period_range(p, year=season_arg())
    from ..accounting import cashboxes
    return render_template('fuel_manage.html', period=p, period_label=label, t=F.totals(a, b), tickets=F.tickets_overview(),
                           keepers=F.keepers(), stations=q('SELECT * FROM fuel_stations ORDER BY active DESC, code'),
                           ops=F.ops(a, b, 100), machines=F.by_machine(a, b), boxes=cashboxes(),
                           equipment=q("SELECT * FROM equipment WHERE active=1 AND kind<>'telashka' ORDER BY kind, code"),
                           fuel_types=F.FUEL_TYPES)


@bp.get('/yoqilgi/qr.pdf')
@perm_required('fuel.manage', 'masterdata.write')
def qr_pdf():
    """Printable QR labels: approved stations and diesel machines (the code, name and plate printed under each)."""
    from ..pdfdoc import build_qr_labels_pdf
    db = get_db()
    items = []
    for s in q('SELECT * FROM fuel_stations WHERE active=1 AND approved=1 ORDER BY code'):
        items.append((F.qr_text('Z', s['qr_token']), s['code'], s['name']))
    for e in q("SELECT * FROM equipment WHERE active=1 AND fuel_type='solyarka' ORDER BY kind, code"):
        items.append((F.qr_text('T', F.equipment_token(db, e['id'])), e['code'], ' · '.join(x for x in (e['plate'], e['operator_name']) if x)))
    if not items:
        raise UserError('Chop etish uchun QR yo‘q: zapravkani tasdiqlang yoki texnikaga “Solyarka” turini belgilang.')
    pdf = build_qr_labels_pdf(items, company=get_setting('company_name'))
    return Response(pdf, mimetype='application/pdf', headers={'Content-Disposition': 'inline; filename="solyarka_qr.pdf"',
                                                             'Cache-Control': 'private, no-store'})
