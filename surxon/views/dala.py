"""Field clerk (hisobchi) on the phone — four steps, nothing else:
1. Yangi telashka: field, free trailer, hand/combine, brigade (suggested from the field), rate so‘m/kg (last one offered).
2. One screen per trip: search/add a person (or pick the combine), kg, “Saqlash va keyingisi”; running totals.
3. “Telashkani yopish”: totals, a photo, “Tasdiqlash va yopish” (only a server-confirmed close counts).
4. Two documents: the internal workers report and the punkt nakladnoy — view / share / save.
"""
from flask import Blueprint, Response, abort, g, jsonify, redirect, render_template, request, url_for

from .. import queries
from ..db import q, tx
from ..photos import uploads_from_request
from ..security import can, perm_required
from ..services import add_harvest, after_waybill_change, mark_full, open_load
from ..settings import get_setting
from ..utils import UserError, now_str, parse_int, parse_number, today_str
from . import done, form_uuid, post_actor, scope

bp = Blueprint('dala', __name__)


def _trip(load_id):
    """The trip, if this person may work on it: a clerk only on trips they opened or wrote into."""
    ld = queries.load(load_id)
    if not ld:
        abort(404)
    user = g.user
    if user['role'] == 'tally':
        mine = ld['opened_by'] == user['id'] or q('SELECT 1 FROM harvests WHERE load_id=? AND entered_by=? LIMIT 1',
                                                   (load_id, user['id']), one=True)
        if not mine:
            abort(403)
    elif scope() and ld['brigadier_id'] != scope():
        abort(403)
    elif not can('harvest.write') and not can('reports.finance'):
        abort(403)
    return ld


def _lines(load_id):
    """Per person (or combine) and rate: weighings, kg, amount — what the workers report shows."""
    rows = q('''SELECT COALESCE('w' || h.worker_id, 'c' || h.combine_id) key,
                       COALESCE(w.full_name, 'Kombayn ' || e.code) name, h.rate, COUNT(*) n, SUM(h.kg) kg,
                       CASE WHEN SUM(h.amount IS NULL) > 0 THEN NULL ELSE SUM(h.amount) END amount, MIN(h.id) first_id
                FROM harvests h LEFT JOIN workers w ON w.id=h.worker_id LEFT JOIN equipment e ON e.id=h.combine_id
                WHERE h.load_id=? AND h.voided_at IS NULL GROUP BY key, h.rate ORDER BY first_id''', (load_id,))
    return [dict(r) for r in rows]


def _totals(load_id):
    r = q('''SELECT COUNT(*) n, COALESCE(SUM(kg),0) kg, COUNT(DISTINCT COALESCE('w' || worker_id, 'c' || combine_id)) people,
                    COALESCE(SUM(amount),0) amount, SUM(amount IS NULL) uncalc
             FROM harvests WHERE load_id=? AND voided_at IS NULL''', (load_id,), one=True)
    return {'n': r['n'], 'kg': r['kg'], 'people': r['people'], 'amount': r['amount'], 'uncalc': r['uncalc'] or 0}


def _recent(load_id, limit=8):
    return [dict(r) for r in q('''SELECT h.id, h.kg, h.amount, h.created_at, h.entered_by,
                                         COALESCE(w.full_name, 'Kombayn ' || e.code) name
                                  FROM harvests h LEFT JOIN workers w ON w.id=h.worker_id
                                  LEFT JOIN equipment e ON e.id=h.combine_id
                                  WHERE h.load_id=? AND h.voided_at IS NULL ORDER BY h.id DESC LIMIT ?''', (load_id, limit))]


@bp.get('/dala')
@perm_required('harvest.write')
def home():
    uid = g.user['id']
    mine = '(tl.opened_by=? OR EXISTS (SELECT 1 FROM harvests h WHERE h.load_id=tl.id AND h.entered_by=?))'
    open_trips = q(f'''SELECT tl.*, t.code trailer_code, f.name field_name, b.name brigadier_name,
                             (SELECT COALESCE(SUM(kg),0) FROM harvests h WHERE h.load_id=tl.id AND h.voided_at IS NULL) kg,
                             (SELECT COUNT(*) FROM harvests h WHERE h.load_id=tl.id AND h.voided_at IS NULL) n
                      FROM trailer_loads tl JOIN equipment t ON t.id=tl.trailer_id LEFT JOIN fields f ON f.id=tl.field_id
                      LEFT JOIN brigadiers b ON b.id=tl.brigadier_id
                      WHERE tl.status='OCHIQ' AND {mine} ORDER BY tl.id DESC''', (uid, uid))
    done_trips = q(f'''SELECT tl.id, tl.trip_no, tl.status, tl.full_at, t.code trailer_code, f.name field_name,
                             wb.number waybill_number, w.net_kg
                      FROM trailer_loads tl JOIN equipment t ON t.id=tl.trailer_id LEFT JOIN fields f ON f.id=tl.field_id
                      LEFT JOIN waybills wb ON wb.load_id=tl.id AND wb.status<>'BEKOR' LEFT JOIN weighings w ON w.load_id=tl.id
                      WHERE tl.status IN ('TOLDI','TORTILDI') AND {mine} AND tl.load_date >= date(?, '-7 days')
                      ORDER BY tl.id DESC LIMIT 30''', (uid, uid, today_str()))
    today = q('''SELECT COALESCE(SUM(kg),0) kg, COUNT(DISTINCT worker_id) people FROM harvests
                 WHERE entered_by=? AND work_date=? AND voided_at IS NULL''', (uid, today_str()), one=True)
    return render_template('dala_home.html', open_trips=open_trips, done_trips=done_trips, today=today)


def _gps():
    """Phone position sent with the form (optional): (lat, lon, accuracy m) or None. Never required to save."""
    try:
        lat, lon = float(request.form.get('lat', '')), float(request.form.get('lon', ''))
        acc = float(request.form.get('acc') or 0)
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0) or acc < 0 or acc > 5000:
        return None
    return round(lat, 7), round(lon, 7), round(acc, 1)


@bp.route('/dala/yangi', methods=['GET', 'POST'])
@perm_required('load.open')
def new_trip():
    if request.method == 'POST':
        lid = open_load(post_actor(), trailer_id=parse_int(request.form.get('trailer_id'), 'Telashka'),
                        field_id=parse_int(request.form.get('field_id'), 'Dala'),
                        brigadier_id=parse_int(request.form.get('brigadier_id'), 'Brigada', required=False),
                        tractor_id=parse_int(request.form.get('tractor_id'), 'Traktor', required=False),
                        method=request.form.get('method') or 'hand', rate=request.form.get('rate'),
                        client_uuid=form_uuid())
        gps = _gps()
        if gps:
            with tx() as db:
                db.execute('UPDATE trailer_loads SET open_lat=?, open_lon=?, open_acc=? WHERE id=? AND open_lat IS NULL',
                           gps + (lid,))
        return done('Telashka ochildi.', url_for('dala.trip', load_id=lid), load_id=lid)
    busy = {r['trailer_id'] for r in q("SELECT trailer_id FROM trailer_loads WHERE status IN ('OCHIQ','TOLDI')")}
    trailers = [t for t in q("SELECT id, code, plate FROM equipment WHERE kind='telashka' AND active=1 ORDER BY code")
                if t['id'] not in busy]
    brig = scope()
    fields = q('SELECT f.id, f.code, f.name, f.brigadier_id, f.polygon_json, b.name brigadier_name FROM fields f '
               'LEFT JOIN brigadiers b ON b.id=f.brigadier_id WHERE f.active=1'
               + (' AND (f.brigadier_id=? OR f.brigadier_id IS NULL)' if brig else '') + ' ORDER BY f.code',
               (brig,) if brig else ())
    return render_template('dala_new.html', trailers=trailers, fields=fields,
                           brigadiers=q('SELECT id, name FROM brigadiers WHERE active=1' + (' AND id=?' if brig else '')
                                        + ' ORDER BY name', (brig,) if brig else ()),
                           tractors=q("SELECT id, code FROM equipment WHERE kind='traktor' AND active=1 ORDER BY code"),
                           rate_hand=get_setting('worker_rate_hand'), rate_combine=get_setting('combine_rate_kg'))


@bp.get('/dala/reys/<int:load_id>')
@perm_required('harvest.write')
def trip(load_id):
    ld = _trip(load_id)
    if ld['status'] != 'OCHIQ':
        return redirect(url_for('dala.docs', load_id=load_id))
    method = ld['method'] or 'hand'
    return render_template('dala_trip.html', ld=ld, method=method, totals=_totals(load_id), recent=_recent(load_id),
                           combines=q("SELECT id, code, operator_name FROM equipment WHERE kind='kombayn' AND active=1 "
                                      "ORDER BY code") if method == 'combine' else [])


@bp.post('/dala/reys/<int:load_id>/tortish')
@perm_required('harvest.write')
def weigh(load_id):
    ld = _trip(load_id)
    method = ld['method'] or 'hand'
    actor = post_actor()
    worker_id = parse_int(request.form.get('worker_id'), 'Ishchi', required=False)
    hid, info = add_harvest(actor, load_id=load_id, method=method,
                            kg=parse_number(request.form.get('kg'), 'Kg'),
                            worker_id=worker_id if method == 'hand' else None,
                            worker_name=(request.form.get('worker_name') or '').strip() if method == 'hand' else None,
                            new_worker=request.form.get('new_worker') == '1',
                            combine_id=parse_int(request.form.get('combine_id'), 'Kombayn', required=False),
                            client_uuid=form_uuid(), confirm_duplicate=request.form.get('confirm_duplicate') == '1')
    gps = _gps()
    if gps:
        with tx() as db:
            db.execute('UPDATE harvests SET lat=?, lon=?, gps_acc=? WHERE id=? AND lat IS NULL', gps + (hid,))
    t = _totals(load_id)
    row = q('''SELECT h.kg, COALESCE(w.full_name, 'Kombayn ' || e.code) name FROM harvests h
               LEFT JOIN workers w ON w.id=h.worker_id LEFT JOIN equipment e ON e.id=h.combine_id WHERE h.id=?''', (hid,), one=True)
    msg = ('Bu yozuv avval saqlangan (takror yuborilmadi).' if info.get('duplicate')
           else f'Saqlandi: {row["name"]} — {row["kg"]:g} kg' + (' (yangi odam)' if info.get('created_worker') else ''))
    return done(msg, url_for('dala.trip', load_id=load_id), totals=t, recent=_recent(load_id))


@bp.get('/dala/reys/<int:load_id>/holat')
@perm_required('harvest.write')
def state(load_id):
    _trip(load_id)
    return jsonify(ok=True, totals=_totals(load_id), recent=_recent(load_id))


@bp.route('/dala/reys/<int:load_id>/yopish', methods=['GET', 'POST'])
@perm_required('load.full')
def close(load_id):
    ld = _trip(load_id)
    if request.method == 'POST':
        actor = post_actor()
        res = mark_full(actor, load_id, photos=uploads_from_request(request, 'photos'))
        if res.get('waybill_id') and not res['already']:
            after_waybill_change(actor, res['waybill_id'], 'yaratildi')
            from ..telegram_bot import notify_async
            notify_async(f'🚜 Telashka yopildi: {ld["trip_no"]} · {ld["field_name"]} · {ld["brigadier_name"]}\n'
                         f'{res["net_kg"]:,.0f} kg · {res["number"]}'.replace(',', ' '), roles=('admin', 'manager'),
                         station_id=ld['station_id'])
        return done('Telashka yopildi. Hujjatlar tayyor.', url_for('dala.docs', load_id=load_id))
    if ld['status'] != 'OCHIQ':
        return redirect(url_for('dala.docs', load_id=load_id))
    return render_template('dala_close.html', ld=ld, totals=_totals(load_id), lines=_lines(load_id))


@bp.get('/dala/reys/<int:load_id>/hujjatlar')
@perm_required('harvest.write', 'reports.finance')
def docs(load_id):
    ld = _trip(load_id)
    return render_template('dala_docs.html', ld=ld, totals=_totals(load_id))


@bp.get('/dala/reys/<int:load_id>/ishchilar.pdf')
@perm_required('harvest.write', 'reports.finance')
def workers_pdf(load_id):
    ld = _trip(load_id)
    from ..pdfdoc import build_workers_report_pdf
    pdf = build_workers_report_pdf(ld, _lines(load_id), company=get_setting('company_name'), generated_at=now_str(),
                                   generated_by=g.user['full_name'])
    name = f'{ld["trip_no"] or ld["id"]}_ishchilar_hisoboti.pdf'
    disp = 'attachment' if request.args.get('download') == '1' else 'inline'
    return Response(pdf, mimetype='application/pdf', headers={'Content-Disposition': f'{disp}; filename="{name}"',
                                                             'Cache-Control': 'private, no-store'})
