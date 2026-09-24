"""Field → trailer → weighbridge → waybill → Nayman operations."""
from datetime import timedelta, date

from flask import Blueprint, abort, g, render_template, request, url_for

from .. import queries
from ..db import q
from ..photos import uploads_from_request, read_upload
from ..security import can, perm_required, require
from ..services import (NAYMAN_DIFF_REASONS, add_harvest, after_waybill_change, attach_load_photos, correct_weighing, diff_needs_reason,
                        expected_payment, mark_full, open_load, record_gross, record_nayman, record_tare, reopen_load,
                        void_harvest, void_load, void_waybill)
from ..settings import get_float, get_setting
from ..utils import UserError, parse_date, parse_int, parse_number, today_str
from . import PER_PAGE, checkbox, done, form_uuid, page_arg, paginate, post_actor, scope, season_arg

bp = Blueprint('ops', __name__)


def _choices():
    brig = scope()
    fields = q('SELECT f.*, b.name brigadier_name FROM fields f LEFT JOIN brigadiers b ON b.id=f.brigadier_id '
               'WHERE f.active=1' + (' AND f.brigadier_id=?' if brig else '') + ' ORDER BY f.code', (brig,) if brig else ())
    return {
        'fields': fields,
        'brigadiers': q('SELECT * FROM brigadiers WHERE active=1' + (' AND id=?' if brig else '') + ' ORDER BY name',
                        (brig,) if brig else ()),
        'tractors': q("SELECT * FROM equipment WHERE kind='traktor' AND active=1 ORDER BY code"),
        'trailers': q("SELECT * FROM equipment WHERE kind='telashka' AND active=1 ORDER BY code"),
        'combines': q("SELECT * FROM equipment WHERE kind='kombayn' AND active=1 ORDER BY code"),
    }


def _load_or_404(load_id):
    ld = queries.load(load_id)
    if not ld:
        abort(404)
    if scope() and ld['brigadier_id'] != scope():
        abort(403)
    return ld


# ------------------------------------------------------------------ harvest (terim)

@bp.route('/terim', methods=['GET', 'POST'])
@perm_required('harvest.write', 'reports.view')
def harvest():
    if request.method == 'POST':
        require('harvest.write')
        actor = post_actor()
        method = request.form.get('method') or 'hand'
        load_id = parse_int(request.form.get('load_id'), 'Telashka (yuk)')
        kg = parse_number(request.form.get('kg'), 'Kg')
        hid, info = add_harvest(
            actor, load_id=load_id, method=method, kg=kg,
            worker_id=parse_int(request.form.get('worker_id'), 'Ishchi', required=False),
            worker_name=request.form.get('worker_name'),
            combine_id=parse_int(request.form.get('combine_id'), 'Kombayn', required=False),
            note=request.form.get('note', ''), client_uuid=form_uuid(), confirm_duplicate=checkbox('confirm_duplicate'))
        photo = read_upload(request.files.get('photo'))
        if photo and not info.get('duplicate'):
            attach_load_photos(actor, load_id, [photo], category='worker' if method == 'hand' else 'combine')
        if info.get('duplicate'):
            msg = 'Bu yozuv avval saqlangan (takror yuborilmadi).'
        else:
            msg = f'Saqlandi: {kg:g} kg. Telashkada jami {info["load_total"]:g} kg.'
            if info.get('created_worker'):
                msg += ' Yangi ishchi ro‘yxatga qo‘shildi.'
        return done(msg, url_for('ops.harvest', load=load_id), harvest_id=hid, load_total=info.get('load_total'))
    year = season_arg()
    brig = scope()
    open_loads = queries.loads(year, statuses=('OCHIQ',), brig=brig)
    sel = request.args.get('load', '')
    current = None
    if sel.isdigit():
        current = queries.load(int(sel))
        if current and brig and current['brigadier_id'] != brig:
            current = None
    elif len(open_loads) == 1:
        current = open_loads[0]
    entries = queries.load_harvests(current['id']) if current else []
    today_rows = q('''SELECT h.*, w.full_name worker_name, t.code trailer_code, f.name field_name, c.code combine_code
                      FROM harvests h LEFT JOIN workers w ON w.id=h.worker_id JOIN equipment t ON t.id=h.trailer_id
                      LEFT JOIN fields f ON f.id=h.field_id LEFT JOIN equipment c ON c.id=h.combine_id
                      WHERE h.season_year=? AND h.work_date=? AND h.voided_at IS NULL''' +
                   (' AND h.brigadier_id=?' if brig else '') + ' ORDER BY h.id DESC LIMIT 300',
                   (year, request.args.get('date') or today_str()) + ((brig,) if brig else ()))
    return render_template('harvest.html', open_loads=open_loads, current=current, entries=entries,
                           today_rows=today_rows, year=year, max_hand=get_float('max_hand_kg', 250), **_choices())


@bp.post('/terim/<int:harvest_id>/bekor')
@perm_required('harvest.write')
def harvest_void(harvest_id):
    void_harvest(post_actor(), harvest_id, request.form.get('reason'))
    return done('Terim yozuvi bekor qilindi (tarixda saqlanadi).', request.referrer or url_for('ops.harvest'))


# ------------------------------------------------------------------ trailers / loads

@bp.get('/telashkalar')
@perm_required('dashboard')
def loads():
    year = season_arg()
    status = request.args.get('status')
    statuses = (status,) if status in ('OCHIQ', 'TOLDI', 'TORTILDI', 'BEKOR') else None
    rows, has_more = paginate(queries.loads(year, statuses=statuses, brig=scope(), day=request.args.get('date') or None,
                                            limit=PER_PAGE + 1, offset=(page_arg() - 1) * PER_PAGE), page_arg())
    choices = _choices()
    choices.pop('trailers')
    return render_template('loads.html', trailers=queries.active_trailers(), year=year, status=status, rows=rows,
                           page=page_arg(), has_more=has_more, **choices)


@bp.post('/telashkalar/ochish')
@perm_required('load.open')
def load_open():
    actor = post_actor()
    lid = open_load(actor, trailer_id=parse_int(request.form.get('trailer_id'), 'Telashka'),
                    field_id=parse_int(request.form.get('field_id'), 'Dala'),
                    brigadier_id=parse_int(request.form.get('brigadier_id'), 'Brigadir', required=False) or scope(),
                    tractor_id=parse_int(request.form.get('tractor_id'), 'Traktor', required=False),
                    vehicle_plate=request.form.get('vehicle_plate', ''), driver_name=request.form.get('driver_name', ''),
                    note=request.form.get('note', ''), client_uuid=form_uuid())
    return done('Telashka ochildi. Endi terim kiritishingiz mumkin.', url_for('ops.harvest', load=lid), load_id=lid)


@bp.get('/yuk/<int:load_id>')
@perm_required('dashboard')
def load_detail(load_id):
    ld = _load_or_404(load_id)
    workers = q('''SELECT w.id, w.full_name, SUM(h.kg) kg, COUNT(*) n, MIN(h.created_at) first_at, MAX(h.created_at) last_at
                   FROM harvests h JOIN workers w ON w.id=h.worker_id
                   WHERE h.load_id=? AND h.voided_at IS NULL AND h.method='hand' GROUP BY w.id ORDER BY kg DESC''', (load_id,))
    # Worker kg stays exactly as weighed in the field; no automatic re-allocation of the weighbridge net.
    hand_total = sum(w['kg'] for w in workers) or 0
    return render_template('load_detail.html', ld=ld, entries=queries.load_harvests(load_id, include_void=True),
                           workers=workers, hand_total=hand_total, photos=queries.photos_for(load_id=load_id),
                           timeline=queries.load_timeline(load_id), min_photos=int(get_float('toldi_min_photos', 1) or 0))


@bp.post('/yuk/<int:load_id>/toldi')
@perm_required('load.full')
def load_full(load_id):
    _load_or_404(load_id)
    actor = post_actor()
    res = mark_full(actor, load_id, note=request.form.get('note', ''), photos=uploads_from_request(request, 'photos'))
    msg = ('Bu telashka allaqachon TOLDI.' if res['already'] else
           f'TOLDI saqlandi. Ichki hisob: {res["internal_kg"]:g} kg. Endi umumiy taroziga olib boring.')
    from ..telegram_bot import notify_async
    if not res['already']:
        ld = queries.load(load_id)
        notify_async(f'🚛 {ld["trailer_code"]} TOLDI · {ld["field_name"]} · {ld["brigadier_name"]}\n'
                     f'Ichki hisob: {res["internal_kg"]:,.0f} kg'.replace(',', ' '), roles=('admin', 'manager', 'scale'))
    return done(msg, url_for('ops.load_detail', load_id=load_id))


@bp.post('/yuk/<int:load_id>/rasm')
@perm_required('photos.upload')
def load_photos(load_id):
    _load_or_404(load_id)
    files = uploads_from_request(request, 'photos')
    if not files:
        raise UserError('Rasm tanlang.')
    attach_load_photos(post_actor(), load_id, files, category=request.form.get('category') or 'trailer')
    return done(f'{len(files)} ta rasm saqlandi.', url_for('ops.load_detail', load_id=load_id))


@bp.post('/yuk/<int:load_id>/qayta-ochish')
@perm_required('load.reopen')
def load_reopen(load_id):
    reopen_load(post_actor(), load_id, request.form.get('reason'))
    return done('Telashka qayta ochildi.', url_for('ops.load_detail', load_id=load_id))


@bp.post('/yuk/<int:load_id>/bekor')
@perm_required('records.void')
def load_void(load_id):
    void_load(post_actor(), load_id, request.form.get('reason'))
    return done('Yuk bekor qilindi.', url_for('ops.loads'))


# ------------------------------------------------------------------ weighbridge (tarozi)

@bp.get('/tarozi')
@perm_required('weigh.write', 'waybill.view')
def scale_queue():
    year = season_arg()
    return render_template('scale_queue.html', year=year,
                           waiting=queries.loads(year, statuses=('TOLDI',)),
                           recent=queries.loads(year, statuses=('TORTILDI',), limit=15))


@bp.route('/tarozi/<int:load_id>', methods=['GET', 'POST'])
@perm_required('weigh.write', 'waybill.view')
def weigh(load_id):
    ld = _load_or_404(load_id)
    if request.method == 'POST':
        require('weigh.write')
        actor = post_actor()
        step = request.form.get('step')
        photo = read_upload(request.files.get('photo'))
        if step == 'gross':
            gross = parse_number(request.form.get('gross_kg'), 'Brutto', max_value=get_float('max_gross_kg', 40000))
            record_gross(actor, load_id, gross, vehicle_plate=request.form.get('vehicle_plate', ''),
                         driver_name=request.form.get('driver_name', ''), scale_no=request.form.get('scale_no', ''),
                         photo=photo)
            return done(f'Brutto saqlandi: {gross:g} kg. Mashina yukni topshirgach tara torting.',
                        url_for('ops.weigh', load_id=load_id))
        if step == 'tare':
            tare = parse_number(request.form.get('tare_kg'), 'Tara')
            res = record_tare(actor, load_id, tare, diff_reason=request.form.get('diff_reason', ''), photo=photo)
            if not res['already']:
                after_waybill_change(actor, res['waybill_id'], 'yaratildi')
                from ..telegram_bot import notify_async
                notify_async(f'⚖️ {ld["trailer_code"]} tortildi · Netto {res["net_kg"]:,.0f} kg\n'
                             f'📄 Nakladnoy {res["number"]} yaratildi'.replace(',', ' '),
                             roles=('admin', 'manager', 'accountant'))
            return done(f'Tortish yakunlandi. Netto {res["net_kg"]:g} kg. Nakladnoy {res["number"]} avtomatik yaratildi.',
                        url_for('ops.waybill_detail', waybill_id=res['waybill_id']), waybill_id=res['waybill_id'])
        raise UserError('Noma’lum amal.')
    w = q('SELECT * FROM weighings WHERE load_id=?', (load_id,), one=True)
    return render_template('weigh.html', ld=ld, w=w, photos=queries.photos_for(load_id=load_id),
                           threshold=get_float('diff_threshold_pct', 2), max_gross=get_float('max_gross_kg', 40000))


@bp.post('/tarozi/<int:load_id>/tuzatish')
@perm_required('weigh.correct')
def weigh_correct(load_id):
    correct_weighing(post_actor(), load_id, parse_number(request.form.get('gross_kg'), 'Brutto'),
                     parse_number(request.form.get('tare_kg'), 'Tara', required=False, allow_zero=True),
                     request.form.get('reason'))
    return done('Tortish tuzatildi. Eski qiymatlar audit tarixida saqlandi.', url_for('ops.load_detail', load_id=load_id))


# ------------------------------------------------------------------ waybills

@bp.get('/nakladnoylar')
@perm_required('waybill.view')
def waybills():
    year = season_arg()
    period = request.args.get('period', '')
    day = today_str() if period == 'today' else None
    since = (date.fromisoformat(today_str()) - timedelta(days=6)).isoformat() if period == 'week' else None
    rows = queries.waybills(year, day=day, since=since, search=(request.args.get('q') or '').strip(),
                            status=request.args.get('status', ''), brig=scope(),
                            limit=PER_PAGE + 1, offset=(page_arg() - 1) * PER_PAGE)
    rows, has_more = paginate(rows, page_arg())
    totals = {'net': sum(r['net_kg'] for r in rows if r['status'] != 'BEKOR'),
              'accepted': sum(r['accepted_kg'] or 0 for r in rows if r['status'] != 'BEKOR')}
    return render_template('waybills.html', rows=rows, year=year, period=period, totals=totals,
                           page=page_arg(), has_more=has_more)


@bp.get('/nakladnoy/<int:waybill_id>')
@perm_required('waybill.view')
def waybill_detail(waybill_id):
    wb = queries.waybill(waybill_id)
    if not wb:
        abort(404)
    if scope() and wb['brigadier_id'] != scope():
        abort(403)
    workers = q('''SELECT w.full_name, SUM(h.kg) kg FROM harvests h JOIN workers w ON w.id=h.worker_id
                   WHERE h.load_id=? AND h.voided_at IS NULL GROUP BY w.id ORDER BY kg DESC''', (wb['load_id'],))
    tpl = 'waybill_print.html' if request.args.get('print') else 'waybill_detail.html'
    docs = q('''SELECT d.*, u.full_name created_name FROM documents d LEFT JOIN users u ON u.id=d.created_by
                WHERE d.waybill_id=? ORDER BY d.version DESC, d.kind''', (waybill_id,))
    return render_template(tpl, wb=wb, workers=workers, photos=queries.photos_for(load_id=wb['load_id']),
                           timeline=queries.load_timeline(wb['load_id']), company=get_setting('company_name'), docs=docs,
                           payments=q('SELECT * FROM payments WHERE waybill_id=? ORDER BY id', (waybill_id,)))


@bp.post('/nakladnoy/<int:waybill_id>/bekor')
@perm_required('waybill.void')
def waybill_void(waybill_id):
    void_waybill(post_actor(), waybill_id, request.form.get('reason'))
    return done('Nakladnoy bekor qilindi. Raqam qayta ishlatilmaydi (izchillik uchun).',
                url_for('ops.waybill_detail', waybill_id=waybill_id))


# ------------------------------------------------------------------ Nayman

@bp.get('/nayman')
@perm_required('nayman.write', 'waybill.view')
def nayman_list():
    year = season_arg()
    return render_template('nayman_list.html', year=year,
                           pending=queries.waybills(year, status='YARATILDI', brig=scope()),
                           received=queries.waybills(year, status='QABUL', brig=scope(), limit=100))


@bp.route('/nayman/<int:waybill_id>', methods=['GET', 'POST'])
@perm_required('nayman.write', 'waybill.view')
def nayman(waybill_id):
    wb = queries.waybill(waybill_id)
    if not wb:
        abort(404)
    if request.method == 'POST':
        require('nayman.write')
        actor = post_actor()
        price = parse_number(request.form.get('price_per_kg'), 'Narx', required=False)
        rid, diff = record_nayman(
            actor, waybill_id, accepted_kg=parse_number(request.form.get('accepted_kg'), 'Qabul qilingan kg', allow_zero=True),
            received_date=parse_date(request.form.get('received_date'), 'Qabul sanasi'),
            receiver_name=request.form.get('receiver_name', ''), diff_reason=request.form.get('diff_reason', ''),
            note=request.form.get('note', ''), price_per_kg=price,
            photo=read_upload(request.files.get('photo')), edit_reason=request.form.get('edit_reason', ''))
        if diff:
            from ..telegram_bot import notify_async
            notify_async(f'🏭 Nayman {wb["number"]}: qabul {wb["net_kg"] + diff:,.0f} kg, farq {diff:+,.0f} kg'
                         .replace(',', ' '), roles=('admin', 'manager', 'accountant'))
        return done(f'Nayman qabuli saqlandi. Farq: {diff:+g} kg.', url_for('ops.waybill_detail', waybill_id=waybill_id))
    receipt = q('SELECT * FROM nayman_receipts WHERE waybill_id=?', (waybill_id,), one=True)
    default_price = get_float('price_per_kg', None)
    exp = expected_payment(receipt['received_date'], receipt['amount']) if receipt and receipt['amount'] else None
    return render_template('nayman.html', wb=wb, receipt=receipt, reasons=NAYMAN_DIFF_REASONS,
                           default_price=default_price, expected=exp)


@bp.get('/hujjat/<int:doc_id>')
@perm_required('waybill.view')
def document(doc_id):
    """Stored PDF (private). Brigadiers only see their own brigade's documents."""
    from flask import current_app, send_from_directory
    d = q('''SELECT d.*, tl.brigadier_id, wb.number FROM documents d JOIN waybills wb ON wb.id=d.waybill_id
             JOIN trailer_loads tl ON tl.id=wb.load_id WHERE d.id=?''', (doc_id,), one=True)
    if not d:
        abort(404)
    if scope() and d['brigadier_id'] != scope():
        abort(403)
    if d['kind'] == 'ichki' and not can('reports.finance'):
        abort(403)
    return send_from_directory(current_app.config['SURXON'].UPLOAD_DIR, d['path'], mimetype='application/pdf',
                               as_attachment=request.args.get('download') == '1',
                               download_name=d['path'].rsplit('/', 1)[-1])


@bp.post('/nakladnoy/<int:waybill_id>/pdf')
@perm_required('weigh.write', 'nayman.write', 'waybill.void')
def waybill_pdf(waybill_id):
    from ..services import create_waybill_documents
    v = create_waybill_documents(post_actor(), waybill_id, request.form.get('reason') or 'qo‘lda qayta yaratildi')
    return done(f'PDF yaratildi ({v}-versiya).', url_for('ops.waybill_detail', waybill_id=waybill_id))
