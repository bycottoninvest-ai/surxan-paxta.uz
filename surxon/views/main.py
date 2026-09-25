import json

from flask import (Blueprint, abort, current_app, g, jsonify, make_response, redirect, render_template, request,
                   send_from_directory, url_for)

from .. import queries
from ..db import get_db, q, scalar
from ..photos import CATEGORIES, uploads_from_request
from ..security import can, login_required, perm_required, require
from ..services import upload_archive_photo, void_photo
from ..settings import get_float, get_setting
from ..utils import UserError, parse_date, parse_int, today_str
from . import done, post_actor, scope, season_arg

bp = Blueprint('main', __name__)


@bp.get('/health')
def health():
    try:
        scalar('SELECT 1')
        ok = True
    except Exception:
        ok = False
    from .. import VERSION
    return jsonify(ok=ok, service='surxon-paxta', version=VERSION), (200 if ok else 503)


@bp.get('/')
@login_required
def dashboard():
    if g.user['role'] == 'station':
        return redirect(url_for('punkt.home'))
    if g.user['role'] in ('accountant', 'cashier') and not request.args.get('view'):
        return redirect(url_for('hamyon.home'))
    if g.user['role'] == 'tally':
        return redirect(url_for('dala.home'))
    if g.user['role'] == 'fuel':
        return redirect(url_for('yoqilgi.home'))
    year = season_arg()
    day = request.args.get('date') or today_str()
    try:
        day = parse_date(day)
    except UserError:
        day = today_str()
    brig = scope()
    kpi = queries.day_kpis(year, day, brig)
    view = request.args.get('view') or request.cookies.get('view')
    if view not in ('full', 'mobile'):
        view = 'mobile' if is_phone() else 'full'
    if view == 'mobile' and g.user['role'] in ('admin', 'manager') and not request.args.get('eski'):
        resp = make_response(redirect(url_for('rahbar.home')))       # director's phone panel (read-only)
    elif view == 'mobile':
        resp = make_response(mobile_home(year, day, brig, kpi))
    else:
        resp = make_response(full_dashboard(year, day, brig, kpi))
    if request.args.get('view') in ('full', 'mobile'):
        resp.set_cookie('view', request.args['view'], max_age=3600 * 24 * 180, samesite='Lax')
    return resp


def is_phone():
    ua = request.user_agent.string or ''
    return any(k in ua for k in ('Mobi', 'Android', 'iPhone', 'iPod'))


def mobile_home(year, day, brig, kpi):
    role = g.user['role']
    ctx = {'kpi': kpi, 'year': year, 'day': day}
    if role in ('brigadier', 'tally', 'driver'):
        ctx['open_loads'] = queries.loads(year, statuses=('OCHIQ',), brig=brig)
    elif role == 'scale':
        ctx['waiting'] = queries.loads(year, statuses=('TOLDI',))
    else:
        if role in ('admin', 'manager'):
            ctx['trailers'] = queries.active_trailers()
        ctx['fin'] = queries.finance_summary(year) if role in ('admin', 'manager', 'accountant', 'cashier') else None
        ctx['has_opening'] = bool(scalar("SELECT COUNT(*) FROM cash_entries WHERE season_year=? AND category='opening' "
                                         "AND voided_at IS NULL", (year,)))
        ctx['cash_today'] = q('''SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount END),0) inflow,
                                        COALESCE(SUM(CASE WHEN direction='OUT' THEN amount END),0) outflow
                                 FROM cash_entries WHERE entry_date=? AND voided_at IS NULL''', (day,), one=True)
    return render_template('mobile_home.html', kuz=_kuz(day), **ctx)


def _kuz(day):
    if not can('kuzatuv.view'):
        return None
    from ..kuzatuv import day_stats, latest
    return {'stats': day_stats(day), 'latest': latest(6)}


def full_dashboard(year, day, brig, kpi):
    tractors, combines = queries.equipment_live(day)
    fields = queries.field_yields(year, brig)
    brigs = queries.brigadier_results(year)
    max_brig = max([b['net_kg'] for b in brigs] + [1])
    series = {m: queries.harvest_series(year, m, day, brig) for m in ('daily', 'weekly', 'season', 'hourly')}
    fin = queries.finance_summary(year) if (g.user['role'] in ('admin', 'manager', 'accountant', 'cashier')) else None
    setup = {
        'fields': scalar('SELECT COUNT(*) FROM fields WHERE active=1'),
        'area': scalar('SELECT COALESCE(SUM(area_ha),0) FROM fields WHERE active=1'),
        'users': scalar('SELECT COUNT(*) FROM users'),
        'price': get_setting('price_per_kg'),
    }
    return render_template(
        'dashboard.html', year=year, day=day, k=kpi, trailers=queries.active_trailers(), tractors=tractors,
        combines=combines, fields=fields, brigs=brigs, max_brig=max_brig, series=series,
        top=queries.top_workers(year, day, 10, brig), waybills=queries.waybills(year, day=day, brig=brig, limit=8),
        receipts=q('''SELECT nr.*, wb.number FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id
                      WHERE nr.received_date=? ORDER BY nr.id DESC LIMIT 6''', (day,)),
        events=queries.recent_events(6),
        photos=q('SELECT * FROM photos WHERE voided_at IS NULL' + ('' if sees_finance_photos() else
                 " AND category NOT IN ('cash','expense','payment')") + ' ORDER BY id DESC LIMIT 6'),
        seasons_cmp=queries.season_comparison(), fin=fin, setup=setup, kuz=_kuz(day),
        target=get_float('daily_target_kg', None), map_center=get_setting('map_center'),
        fields_json=[{'id': f['id'], 'code': f['code'], 'name': f['name'], 'area': f['area_ha'],
                      'confirmed': f['area_source'] != 'xarita',
                      'brigadier': f['brigadier_name'], 'net': f['net_kg'], 'active': f['active_loads'],
                      'poly': json.loads(f['polygon_json']) if f['polygon_json'] else None,
                      'url': url_for('admin.field_detail', field_id=f['id'])} for f in fields],
        weather={'lat': get_setting('weather_lat'), 'lon': get_setting('weather_lon'), 'place': get_setting('weather_place')})


@bp.get('/qidiruv')
@login_required
def search():
    term = (request.args.get('q') or '').strip()
    results = queries.search(term) if len(term) >= 2 else []
    for r in results:
        r['url'] = url_for(r.pop('endpoint'), **r.pop('args'))
    if request.args.get('format') == 'json':
        return jsonify(results=results)
    return render_template('search.html', term=term, results=results)


@bp.get('/api/workers')
@login_required
def api_workers():
    term = (request.args.get('q') or '').strip()
    from ..utils import name_key
    key = name_key(term)
    if len(key) < 1:
        rows = q('''SELECT w.id, w.full_name, w.phone, w.photo_id FROM workers w WHERE w.active=1
                    ORDER BY (SELECT MAX(id) FROM harvests h WHERE h.worker_id=w.id) DESC, w.full_name LIMIT 15''')
    else:
        rows = q('''SELECT id, full_name, phone, photo_id FROM workers
                    WHERE active=1 AND (name_key LIKE ? OR name_key LIKE ? OR phone LIKE ?)
                    ORDER BY CASE WHEN name_key LIKE ? THEN 0 ELSE 1 END, full_name LIMIT 15''',
                 (key + '%', '% ' + key + '%', '%' + term + '%', key + '%'))
    out = []
    for r in rows:
        thumb = None
        if r['photo_id']:
            p = q('SELECT thumb_path FROM photos WHERE id=?', (r['photo_id'],), one=True)
            thumb = url_for('main.media', path=p['thumb_path']) if p else None
        out.append({'id': r['id'], 'name': r['full_name'], 'phone': r['phone'] or '', 'thumb': thumb})
    return jsonify(workers=out)


@bp.get('/media/<path:path>')
@login_required
def media(path):
    """Photos are private: only logged-in users can load them."""
    if '..' in path or path.startswith('/'):
        abort(404)
    photo = q('SELECT * FROM photos WHERE path=? OR thumb_path=?', (path, path), one=True)
    if not photo:
        return _kuzatuv_media(path)
    if not can_see_photo(photo):
        abort(404)
    brig = scope()
    if brig and photo['brigadier_id'] not in (None, brig) and photo['uploaded_by'] != g.user['id']:
        abort(403)
    if photo['voided_at'] and not g.user['role'] in ('admin', 'manager'):
        abort(404)
    resp = make_response(send_from_directory(current_app.config['SURXON'].UPLOAD_DIR, path, max_age=86400 * 30))
    resp.headers['Cache-Control'] = 'private, max-age=2592000'
    return resp


def _kuzatuv_media(path):
    """Kuzatuv photos/videos: only for those who may see the kuzatuv page. Range requests work, so videos play on phones."""
    item = q('SELECT * FROM media_items WHERE path=? OR thumb_path=?', (path, path), one=True)
    if not item or not can('kuzatuv.view') or (item['voided_at'] and g.user['role'] not in ('admin', 'manager')):
        abort(404)
    resp = make_response(send_from_directory(current_app.config['SURXON'].UPLOAD_DIR, path, max_age=86400 * 30,
                                             conditional=True))
    resp.headers['Cache-Control'] = 'private, max-age=2592000'
    return resp


FINANCE_PHOTO_CATEGORIES = ('cash', 'expense', 'payment')


def sees_finance_photos():
    return can('acct.view') or can('cash.view') or can('payments.view')


def can_see_photo(photo):
    """Money photos (kassa, xarajat cheklari, to‘lovlar) only for finance roles or whoever took them; a field clerk
    only the photos of the trips they worked on (or took themselves)."""
    uid = g.user['id']
    if photo['uploaded_by'] == uid:
        return True
    if photo['category'] in FINANCE_PHOTO_CATEGORIES and not sees_finance_photos():
        return False
    if g.user['role'] == 'fuel':      # the fuel keeper sees only the photos they took
        return False
    if g.user['role'] == 'station':
        return bool(photo['load_id'] and photo['category'] in ('trailer', 'cotton', 'nayman') and q(
            'SELECT 1 FROM trailer_loads WHERE id=? AND station_id=?', (photo['load_id'], g.user['station_id']), one=True))
    if g.user['role'] == 'tally':
        return bool(photo['load_id'] and q('''SELECT 1 FROM trailer_loads tl WHERE tl.id=? AND (tl.opened_by=? OR EXISTS
                                            (SELECT 1 FROM harvests h WHERE h.load_id=tl.id AND h.entered_by=?))''',
                                         (photo['load_id'], uid, uid), one=True))
    return True


@bp.route('/foto', methods=['GET', 'POST'])
@perm_required('photos.view')
def photos():
    if request.method == 'POST':
        require('photos.upload')
        actor = post_actor()
        files = uploads_from_request(request, 'photos')
        if not files:
            raise UserError('Rasm tanlang.')
        category = request.form.get('category') or 'other'
        load_id = parse_int(request.form.get('load_id'), 'Yuk', required=False)
        waybill_id = parse_int(request.form.get('waybill_id'), 'Nakladnoy', required=False)
        field_id = parse_int(request.form.get('field_id'), 'Dala', required=False)
        for data in files:
            upload_archive_photo(actor, data, category=category, caption=request.form.get('caption', ''),
                                 load_id=load_id, waybill_id=waybill_id, field_id=field_id,
                                 lat=request.form.get('lat'), lon=request.form.get('lon'))
        return done(f'{len(files)} ta rasm arxivga saqlandi.', request.referrer or url_for('main.photos'))
    year = season_arg()
    where, params = ['p.voided_at IS NULL', '(p.season_year=? OR p.season_year IS NULL)'], [year]
    f = request.args
    if f.get('category') in CATEGORIES:
        where.append('p.category=?'); params.append(f['category'])
    if f.get('date'):
        where.append('substr(p.uploaded_at,1,10)=?'); params.append(f['date'])
    if f.get('field', '').isdigit():
        where.append('p.field_id=?'); params.append(int(f['field']))
    if f.get('trailer', '').isdigit():
        where.append('p.load_id IN (SELECT id FROM trailer_loads WHERE trailer_id=?)'); params.append(int(f['trailer']))
    if f.get('brigadier', '').isdigit():
        where.append('p.brigadier_id=?'); params.append(int(f['brigadier']))
    if scope():
        where.append('(p.brigadier_id=? OR p.uploaded_by=?)'); params += [scope(), g.user['id']]
    if not sees_finance_photos():
        where.append(f"(p.category NOT IN ({','.join('?' * len(FINANCE_PHOTO_CATEGORIES))}) OR p.uploaded_by=?)")
        params += [*FINANCE_PHOTO_CATEGORIES, g.user['id']]
    page = max(1, int(f.get('page', '1') or 1) if f.get('page', '1').isdigit() else 1)
    rows = q(f'''SELECT p.*, u.full_name uploaded_name, t.code trailer_code, fl.name field_name, b.name brigadier_name,
                        wb.number waybill_number, tl.id lid
                 FROM photos p LEFT JOIN users u ON u.id=p.uploaded_by
                 LEFT JOIN trailer_loads tl ON tl.id=p.load_id LEFT JOIN equipment t ON t.id=tl.trailer_id
                 LEFT JOIN fields fl ON fl.id=p.field_id LEFT JOIN brigadiers b ON b.id=p.brigadier_id
                 LEFT JOIN waybills wb ON wb.id=p.waybill_id
                 WHERE {' AND '.join(where)} ORDER BY p.id DESC LIMIT 61 OFFSET ?''', params + [(page - 1) * 60])
    return render_template('photos.html', rows=rows[:60], has_more=len(rows) > 60, page=page, year=year,
                           fields=q('SELECT id, code, name FROM fields ORDER BY code'),
                           trailers=q("SELECT id, code FROM equipment WHERE kind='telashka' ORDER BY code"),
                           brigadiers=q('SELECT id, name FROM brigadiers ORDER BY name'),
                           open_loads=q('''SELECT tl.id, t.code FROM trailer_loads tl JOIN equipment t ON t.id=tl.trailer_id
                                           WHERE tl.status IN ('OCHIQ','TOLDI') ORDER BY t.code'''))


@bp.post('/foto/<int:photo_id>/yashirish')
@perm_required('photos.void')
def photo_void(photo_id):
    void_photo(post_actor(), photo_id, request.form.get('reason'))
    return done('Rasm arxivdan yashirildi (o‘chirilmadi, audit saqlandi).', request.referrer or url_for('main.photos'))


@bp.get('/manifest.webmanifest')
def manifest():
    data = {
        'name': 'SURXON Paxta Hisob Tizimi', 'short_name': 'SURXON Paxta', 'start_url': '/', 'scope': '/',
        'display': 'standalone', 'background_color': '#f3f8fd', 'theme_color': '#06335f', 'lang': 'uz',
        'icons': [{'src': url_for('static', filename='img/icon-192.png'), 'sizes': '192x192', 'type': 'image/png'},
                  {'src': url_for('static', filename='img/icon-512.png'), 'sizes': '512x512', 'type': 'image/png',
                   'purpose': 'any maskable'}],
    }
    resp = jsonify(data)
    resp.mimetype = 'application/manifest+json'
    return resp


@bp.get('/service-worker.js')
def service_worker():
    resp = make_response(send_from_directory(current_app.static_folder, 'service-worker.js'))
    resp.headers['Content-Type'] = 'application/javascript'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp


@bp.get('/offline')
def offline():
    return render_template('offline.html')
