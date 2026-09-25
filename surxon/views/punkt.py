"""Receiving point (qabul punkti) screens: trips on the way, arrival, punkt scale, difference, QABUL QILINDI.

A punkt operator (role 'station') sees only the trips sent to the punkt they are assigned to; every action is
checked again in services.py, so hiding buttons is never the only protection.
"""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .. import queries
from ..db import q
from ..photos import read_upload
from ..scale import scale_reading
from ..security import perm_required
from ..services import STATION_DIFF_REASONS, after_waybill_change, diff_level, mark_arrived, receive_at_station
from ..settings import get_float
from ..utils import UserError, parse_int, parse_number, today_str
from . import done, post_actor, season_arg

bp = Blueprint('punkt', __name__, url_prefix='/punkt')


def my_station():
    """Operator: their own punkt (fixed). Admin/manager: optional ?punkt= filter (None = all)."""
    if g.user['role'] == 'station':
        if not g.user['station_id']:
            abort(403)
        return g.user['station_id']
    return parse_int(request.args.get('punkt'), 'Punkt', required=False)


def _trip_or_404(waybill_id):
    wb = queries.waybill(waybill_id)
    if not wb or wb['status'] == 'BEKOR':
        abort(404)
    if g.user['role'] == 'station' and wb['station_id'] != g.user['station_id']:
        abort(403)
    return wb


def _stations():
    return q('SELECT * FROM stations WHERE active=1 ORDER BY name')


@bp.get('')
@perm_required('station.view')
def home():
    sid = my_station()
    station = q('SELECT * FROM stations WHERE id=?', (sid,), one=True) if sid else None
    trips = queries.station_trips(sid, state='open')
    today = today_str()
    return render_template('punkt_home.html', station=station, stations=_stations(), sid=sid,
                           counts=queries.station_counts(sid, today),
                           on_way=[t for t in trips if not t['arrived_at']], arrived=[t for t in trips if t['arrived_at']],
                           received=queries.station_trips(sid, state='received', day=today, limit=50))


@bp.get('/holat')
@perm_required('station.view')
def status_json():
    """Polled by the punkt screen: counts + newest trip on the way (for the "Yangi yuk yo‘lda" banner)."""
    sid = my_station()
    trips = queries.station_trips(sid, state='open', limit=50)
    newest = max(trips, key=lambda t: t['id']) if trips else None
    return jsonify(ok=True, counts=queries.station_counts(sid, today_str()), open_ids=[t['id'] for t in trips],
                   newest=({'id': newest['id'], 'trip_no': newest['trip_no'], 'field': newest['field_name'],
                            'kg': newest['net_kg'], 'tractor': newest['tractor_code'],
                            'sent_at': (newest['sent_at'] or '')[11:16],
                            'url': url_for('punkt.trip', waybill_id=newest['id'])} if newest else None))


@bp.get('/topish')
@perm_required('station.view')
def find():
    text = request.args.get('q', '')
    wid = queries.find_trip(text, my_station(), season_arg())
    if not wid:
        flash(f'“{text.strip()}” topilmadi. Raqamni tekshiring yoki “Yo‘lda” ro‘yxatidan tanlang.', 'error')
        return redirect(url_for('punkt.home'))
    return redirect(url_for('punkt.trip', waybill_id=wid))


@bp.get('/q/<path:code>')
@perm_required('station.view')
def qr(code):
    """Target of the QR on the paper waybill: any phone camera opens it straight on the trip."""
    wid = queries.find_trip(code, my_station())
    if not wid:
        flash('QR bo‘yicha yuk topilmadi (yoki boshqa punktga jo‘natilgan).', 'error')
        return redirect(url_for('punkt.home'))
    return redirect(url_for('punkt.trip', waybill_id=wid))


@bp.get('/skaner')
@perm_required('station.view')
def scanner():
    return render_template('punkt_scan.html')


@bp.get('/yuk/<int:waybill_id>')
@perm_required('station.view')
def trip(waybill_id):
    wb = _trip_or_404(waybill_id)
    level = diff_level(wb['net_kg'], wb['accepted_kg']) if wb['accepted_kg'] is not None else None
    return render_template('punkt_trip.html', wb=wb, state=queries.trip_state(wb), level=level,
                           reasons=STATION_DIFF_REASONS, warn=get_float('punkt_warn_pct', 1),
                           alert=get_float('punkt_alert_pct', 3), scale=scale_reading(probe=True),
                           timeline=queries.load_timeline(wb['load_id']), event_text=queries.EVENT_TEXT)


@bp.post('/yuk/<int:waybill_id>/keldi')
@perm_required('station.receive')
def arrived(waybill_id):
    _trip_or_404(waybill_id)
    mark_arrived(post_actor(), waybill_id)
    return done('Belgilandi: yuk punktga KELDI.', url_for('punkt.trip', waybill_id=waybill_id))


@bp.post('/yuk/<int:waybill_id>/qabul')
@perm_required('station.receive')
def receive(waybill_id):
    wb = _trip_or_404(waybill_id)
    actor = post_actor()
    kg = parse_number(request.form.get('station_kg'), 'Punkt tarozisi (kg)', max_value=get_float('max_gross_kg', 40000))
    res = receive_at_station(actor, waybill_id, station_kg=kg, reason=request.form.get('reason', ''),
                             note=request.form.get('note', ''), photo=read_upload(request.files.get('photo')))
    url = url_for('punkt.trip', waybill_id=waybill_id)
    if res['already']:
        return done(f'{wb["trip_no"]} allaqachon qabul qilingan — ikkinchi marta yozilmadi.', url, already=True)
    after_waybill_change(actor, waybill_id, 'punktda qabul qilindi')
    return done(f'QABUL QILINDI: {wb["trip_no"]} · punkt {kg:g} kg · farq {res["diff_kg"]:+g} kg ({res["diff_pct"]:+.2f}%)',
                url, diff_kg=res['diff_kg'], diff_pct=res['diff_pct'], level=res['level'])


@bp.get('/tarix')
@perm_required('station.view')
def history():
    day = request.args.get('date') or today_str()
    sid = my_station()
    rows = queries.station_trips(sid, state='received', day=day, limit=500)
    return render_template('punkt_history.html', rows=rows, day=day, sid=sid, stations=_stations(),
                           total_field=sum(r['net_kg'] for r in rows),
                           total_station=sum(r['accepted_kg'] or 0 for r in rows))


@bp.get('/tarozi')
@perm_required('station.receive')
def scale_read():
    """Electronic scale reading through the configured adapter (manual mode: nothing to read)."""
    try:
        return jsonify(ok=True, **scale_reading())
    except UserError as e:
        return jsonify(ok=False, error=str(e))
