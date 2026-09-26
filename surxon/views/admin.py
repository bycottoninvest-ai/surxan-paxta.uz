"""Master data and administration: users, brigades, fields, equipment, settings, seasons, backups."""
from flask import (Blueprint, abort, current_app, flash, make_response, redirect, render_template, request,
                   send_from_directory, url_for)

from .. import queries
from ..db import q, scalar
from ..security import PERMISSIONS, ROLES, perm_required
from ..services import (close_season, reopen_season, save_brigadier, save_cashbox, save_equipment, save_field, save_settings, save_station,
                        save_user, telegram_link_code, unlink_telegram)
from ..settings import all_settings, get_setting
from ..utils import UserError, clean_text, parse_int, parse_number, today_str
from . import checkbox, done, post_actor, season_arg

bp = Blueprint('admin', __name__, url_prefix='/admin')

KNOWN_AREA_TOTAL = 320.0  # figure quoted earlier by the owner; kept only to flag the unresolved 9.8 ha


@bp.route('/foydalanuvchilar', methods=['GET', 'POST'])
@perm_required('users.manage')
def users():
    if request.method == 'POST':
        actor = post_actor()
        uid = parse_int(request.form.get('id'), 'ID', required=False)
        save_user(actor, uid, username=request.form.get('username'), full_name=request.form.get('full_name'),
                  role=request.form.get('role'), password=request.form.get('password', ''),
                  brigadier_id=parse_int(request.form.get('brigadier_id'), 'Brigada', required=False),
                  phone=request.form.get('phone', ''), active=checkbox('active') if uid else True,
                  station_id=parse_int(request.form.get('station_id'), 'Punkt', required=False),
                  cashbox_id=parse_int(request.form.get('cashbox_id'), 'Kassa', required=False))
        return done('Foydalanuvchi saqlandi.' + ('' if uid else ' Birinchi kirishda parolni almashtirish so‘raladi.'),
                    url_for('admin.users'))
    edit = None
    if request.args.get('edit', '').isdigit():
        edit = q('SELECT * FROM users WHERE id=?', (int(request.args['edit']),), one=True)
    return render_template('admin_users.html', rows=q('''SELECT u.*, b.name brigadier_name, st.name station_name FROM users u
                                                         LEFT JOIN brigadiers b ON b.id=u.brigadier_id
                                                         LEFT JOIN stations st ON st.id=u.station_id
                                                         ORDER BY u.active DESC, u.role, u.full_name'''),
                           brigadiers=q('SELECT * FROM brigadiers WHERE active=1 ORDER BY name'), roles=ROLES, edit=edit,
                           stations=q('SELECT * FROM stations WHERE active=1 ORDER BY name'),
                           cashboxes=q('SELECT * FROM cashboxes WHERE active=1 ORDER BY id'),
                           permissions=PERMISSIONS)


STAFF_ROLES = [
    ('manager', 'Rahbar', 'Hamma narsani ko‘radi: terim, punkt, pul, hisobotlar, kuzatuv (rasm/video).'),
    ('tally', 'Hisobchi (terim)', 'Dalada har bir terimchining kg ini yozadi, telashka ochadi, “Tugatish”/TOLDI qiladi.'),
    ('station', 'Punkt operatori', 'Punktda keladigan telashkani QR bilan topadi, punkt tarozisi kg ini yozib qabul qiladi.'),
    ('accountant', 'Buxgalter', 'Kassa kirimi, xarajat, ishchilarga to‘lovni tayyorlaydi, kunni yopadi, hisobot.'),
    ('cashier', 'Kassir', 'Buxgalter tayyorlagan to‘lovni odamga beradi va “BERILDI” bosadi, xarajat yozadi.'),
    ('fuel', 'Yoqilg‘i mas’uli', 'Solyarkani zapravkadan QR bilan oladi va traktor/kombaynga QR + rasm bilan beradi. Tiket va narxni o‘zgartira olmaydi.'),
    ('scale', 'Tarozi xodimi', 'Katta tarozida brutto/tara tortadi (kombayn yuklari uchun). Kerak bo‘lmasa bo‘sh qoladi.'),
    ('driver', 'Haydovchi', 'Ixtiyoriy: telashka to‘lganda TOLDI + rasm. Kerak bo‘lmasa bo‘sh qoladi.'),
    ('admin', 'Admin', 'Tizim sozlamalari, loginlar, zaxira. Odatda faqat siz.'),
]


@bp.route('/xodimlar', methods=['GET', 'POST'])
@perm_required('users.manage')
def staff():
    from ..services import quick_add_user, reset_password_random, set_user_active
    issued = None
    if request.method == 'POST':
        actor = post_actor()
        action = request.form.get('action')
        uid = parse_int(request.form.get('uid'), 'Xodim', required=False)
        try:
            if action == 'add':
                _uid, login, pw = quick_add_user(actor, request.form.get('full_name', ''), request.form.get('role'))
                issued = {'name': clean_text(request.form.get('full_name', ''), 120), 'login': login, 'password': pw}
            elif action == 'reset':
                name, login, pw = reset_password_random(actor, uid)
                issued = {'name': name, 'login': login, 'password': pw, 'reset': True}
            elif action in ('off', 'on'):
                name = set_user_active(actor, uid, action == 'on')
                flash(f'{name}: ' + ('qayta faollashtirildi.' if action == 'on' else 'o‘chirildi (kira olmaydi, tarixi saqlanadi).'),
                      'success')
                return redirect(url_for('admin.staff'))
        except UserError as e:
            flash(str(e), 'error')
            return redirect(url_for('admin.staff'))
    people = q('SELECT u.*, st.name station_name FROM users u LEFT JOIN stations st ON st.id=u.station_id '
               'ORDER BY u.active DESC, u.full_name')
    resp = make_response(render_template('admin_staff.html', groups=STAFF_ROLES, people=people, issued=issued,
                                         base_url=request.host_url.rstrip('/')))
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@bp.post('/foydalanuvchi/<int:uid>/telegram')
@perm_required('users.manage')
def user_telegram(uid):
    actor = post_actor()
    if request.form.get('action') == 'unlink':
        unlink_telegram(actor, uid)
        return done('Telegram bog‘lanishi uzildi.', url_for('admin.users'))
    code = telegram_link_code(actor, uid)
    bot = current_app.config['SURXON'].TELEGRAM_BOT_USERNAME
    link = f'https://t.me/{bot}?start={code}' if bot else None
    msg = f'Telegram ulash kodi: {code} (2 kun amal qiladi). Xodim botga /start {code} yuborsin.'
    if link:
        msg += f' Yoki havola: {link}'
    return done(msg, url_for('admin.users'), code=code, link=link)


@bp.route('/brigadirlar', methods=['GET', 'POST'])
@perm_required('masterdata.write')
def brigadiers():
    if request.method == 'POST':
        save_brigadier(post_actor(), parse_int(request.form.get('id'), 'ID', required=False), request.form.get('name'),
                       request.form.get('full_name', ''), request.form.get('phone', ''),
                       checkbox('active') if request.form.get('id') else True)
        return done('Brigadir saqlandi.', url_for('admin.brigadiers'))
    year = season_arg()
    return render_template('admin_brigadiers.html', rows=queries.brigadier_results(year), year=year,
                           all_rows=q('SELECT * FROM brigadiers ORDER BY active DESC, name'))


@bp.route('/kassalar', methods=['GET', 'POST'])
@perm_required('users.manage')
def cashboxes():
    if request.method == 'POST':
        cid = parse_int(request.form.get('id'), 'ID', required=False)
        save_cashbox(post_actor(), cid, name=request.form.get('name'), active=checkbox('active') if cid else True)
        return done('Kassa saqlandi.', url_for('admin.cashboxes'))
    from ..accounting import box_balance
    from ..db import get_db
    rows = [dict(r, balance=box_balance(get_db(), r['id']),
                 cashiers=', '.join(u['full_name'] for u in q('SELECT full_name FROM users WHERE cashbox_id=? AND active=1', (r['id'],))))
            for r in q('SELECT * FROM cashboxes ORDER BY active DESC, id')]
    return render_template('admin_cashboxes.html', rows=rows)


@bp.route('/punktlar', methods=['GET', 'POST'])
@perm_required('masterdata.write')
def stations():
    if request.method == 'POST':
        sid = parse_int(request.form.get('id'), 'ID', required=False)
        save_station(post_actor(), sid, name=request.form.get('name'), address=request.form.get('address', ''),
                     active=checkbox('active') if sid else True, coords=request.form.get('coords'))
        return done('Punkt saqlandi.', url_for('admin.stations'))
    rows = q('''SELECT st.*, (SELECT GROUP_CONCAT(u.full_name, ', ') FROM users u WHERE u.station_id=st.id AND u.active=1) operators,
                       (SELECT COUNT(*) FROM trailer_loads tl JOIN waybills wb ON wb.load_id=tl.id
                         WHERE tl.station_id=st.id AND wb.status='YARATILDI') open_trips
                FROM stations st ORDER BY st.active DESC, st.name''')
    return render_template('admin_stations.html', rows=rows)


@bp.route('/dalalar', methods=['GET', 'POST'])
@perm_required('masterdata.write', 'reports.view')
def fields():
    if request.method == 'POST':
        from ..security import require
        require('masterdata.write')
        fid = save_field(post_actor(), parse_int(request.form.get('id'), 'ID', required=False),
                         code=request.form.get('code'), name=request.form.get('name'),
                         area_ha=parse_number(request.form.get('area_ha'), 'Maydon (ga)', max_value=5000),
                         brigadier_id=parse_int(request.form.get('brigadier_id'), 'Brigadir', required=False),
                         notes=request.form.get('notes', ''), polygon_json=request.form.get('polygon_json') or None,
                         active=checkbox('active') if request.form.get('id') else True)
        return done('Dala saqlandi.', url_for('admin.field_detail', field_id=fid))
    year = season_arg()
    rows = queries.field_yields(year)
    total_area = sum(r['area_ha'] for r in rows)
    brig_area = q('''SELECT b.name, COALESCE(SUM(f.area_ha),0) area FROM brigadiers b
                     LEFT JOIN fields f ON f.brigadier_id=b.id AND f.active=1 WHERE b.active=1 GROUP BY b.id ORDER BY b.name''')
    return render_template('admin_fields.html', rows=rows, year=year, total_area=total_area, known_total=KNOWN_AREA_TOTAL,
                           brig_area=brig_area, brigadiers=q('SELECT * FROM brigadiers WHERE active=1 ORDER BY name'),
                           map_center=get_setting('map_center'))


@bp.route('/dalalar/import', methods=['GET', 'POST'])
@perm_required('masterdata.write')
def fields_import():
    from .. import field_import as FI
    if request.method == 'POST':
        f = request.files.get('file')
        iid = FI.create_import(post_actor(), f.read() if f else None, f.filename if f else '')
        return done('Fayl o‘qildi — xaritada tekshiring, keyin tasdiqlang. Hali hech narsa saqlanmadi.',
                    url_for('admin.fields_import_review', import_id=iid))
    return render_template('admin_fields_import.html', imports=q('''SELECT i.id, i.filename, i.count, i.map_total_ha, i.status,
                           i.created_at, i.saved_at, i.result_json, u.full_name by_name FROM field_imports i
                           LEFT JOIN users u ON u.id=i.created_by ORDER BY i.id DESC LIMIT 20'''))


@bp.route('/dalalar/import/<int:import_id>', methods=['GET', 'POST'])
@perm_required('masterdata.write')
def fields_import_review(import_id):
    from .. import field_import as FI
    if request.method == 'POST':
        actor = post_actor()
        if request.form.get('action') == 'cancel':
            FI.cancel_import(actor, import_id)
            return done('Import bekor qilindi — bazaga hech narsa yozilmadi.', url_for('admin.fields_import'))
        choices = {}
        for key, val in request.form.items():
            if '__' not in key:
                continue
            name, _, idx = key.partition('__')
            if idx.isdigit():
                choices.setdefault(int(idx), {})[name] = val
        res = FI.confirm_import(actor, import_id, choices, season_arg())
        return done(f'Saqlandi: {res["created"]} ta yangi dala, {res["updated"]} ta yangilandi, {res["skipped"]} ta tashlab ketildi.',
                    url_for('admin.fields'))
    imp, rows = FI.get_import(import_id)
    if not imp:
        abort(404)
    current_total = scalar("SELECT COALESCE(SUM(area_ha),0) FROM fields WHERE active=1")
    return render_template('admin_fields_import_review.html', imp=imp, rows=rows, crops=FI.CROPS, known_total=KNOWN_AREA_TOTAL,
                           current_total=current_total, brigadiers=q('SELECT * FROM brigadiers WHERE active=1 ORDER BY name'),
                           map_center=get_setting('map_center'))


@bp.route('/dalalar/chizish', methods=['GET', 'POST'])
@perm_required('masterdata.write')
def field_draw():
    from .. import field_import as FI
    if request.method == 'POST':
        fid, ha = FI.create_drawn_field(post_actor(), code=request.form.get('code', ''), name=request.form.get('name', ''),
                                        polygon=request.form.get('polygon_json') or '[]',
                                        brigadier_id=parse_int(request.form.get('brigadier_id'), 'Brigadir', required=False),
                                        confirmed_ha=request.form.get('confirmed_ha'), crop=request.form.get('crop'), year=season_arg())
        return done(f'Yangi dala saqlandi: xaritada {ha:.2f} ga.', url_for('admin.field_detail', field_id=fid))
    import json as _j
    return render_template('admin_field_draw.html', code=FI.next_code(), crops=FI.CROPS, map_center=get_setting('map_center'),
                           brigadiers=q('SELECT * FROM brigadiers WHERE active=1 ORDER BY name'),
                           others=[{'code': f['code'], 'poly': _j.loads(f['polygon_json'])} for f in
                                   q('SELECT code, polygon_json FROM fields WHERE polygon_json IS NOT NULL AND active=1')])


@bp.post('/dala/<int:field_id>/maydon')
@perm_required('masterdata.write')
def field_confirm_area(field_id):
    from .. import field_import as FI
    FI.confirm_area(post_actor(), field_id, request.form.get('area_ha'))
    return done('Maydon tasdiqlandi.', url_for('admin.field_detail', field_id=field_id))


@bp.get('/dala/<int:field_id>')
@perm_required('masterdata.write', 'reports.view')
def field_detail(field_id):
    f = q('SELECT f.*, b.name brigadier_name FROM fields f LEFT JOIN brigadiers b ON b.id=f.brigadier_id WHERE f.id=?',
          (field_id,), one=True)
    if not f:
        abort(404)
    history = q('''SELECT tl.season_year year, COUNT(tl.id) loads, COALESCE(SUM(w.net_kg),0) net_kg
                   FROM trailer_loads tl LEFT JOIN weighings w ON w.load_id=tl.id AND w.status='YAKUNLANDI'
                   WHERE tl.field_id=? AND tl.status<>'BEKOR' GROUP BY tl.season_year ORDER BY tl.season_year DESC''', (field_id,))
    from .. import field_import as FI
    year = season_arg()
    return render_template('field_detail.html', f=f, history=history, stats=FI.field_stats(field_id, year), year=year,
                           assignments=q('''SELECT a.*, b.name brigadier_name, u.full_name by_name FROM field_assignments a
                                            LEFT JOIN brigadiers b ON b.id=a.brigadier_id LEFT JOIN users u ON u.id=a.set_by
                                            WHERE a.field_id=? ORDER BY a.id DESC''', (field_id,)),
                           fseason=q('SELECT * FROM field_seasons WHERE field_id=? AND year=?', (field_id, year), one=True),
                           trip_photos=q('''SELECT p.* FROM photos p JOIN trailer_loads tl ON tl.id=p.load_id WHERE tl.field_id=?
                                            AND p.voided_at IS NULL AND p.category IN ('trailer','cotton','nayman','weigh_gross','weigh_tare')
                                            ORDER BY p.id DESC LIMIT 12''', (field_id,)),
                           loads=q(queries.LOAD_SELECT + ' WHERE tl.field_id=? ORDER BY tl.id DESC LIMIT 30', (field_id,)),
                           photos=q('SELECT * FROM photos WHERE field_id=? AND voided_at IS NULL ORDER BY id DESC LIMIT 24',
                                    (field_id,)),
                           brigadiers=q('SELECT * FROM brigadiers WHERE active=1 ORDER BY name'),
                           map_center=get_setting('map_center'), audit_rows=queries.history('field', field_id))


@bp.route('/texnikalar', methods=['GET', 'POST'])
@perm_required('dashboard')
def equipment():
    if request.method == 'POST':
        from ..security import require
        require('masterdata.write')
        actor = post_actor()
        eid = save_equipment(actor, parse_int(request.form.get('id'), 'ID', required=False),
                             kind=request.form.get('kind'), code=request.form.get('code'), plate=request.form.get('plate', ''),
                             operator_name=request.form.get('operator_name', ''), ownership=request.form.get('ownership') or 'own',
                             notes=request.form.get('notes', ''), active=checkbox('active') if request.form.get('id') else True)
        if 'imei' in request.form:
            from ..fleet import save_machine_gps
            save_machine_gps(actor, eid, imei=request.form.get('imei', ''), norm_field=request.form.get('norm_field_lph'),
                             norm_road=request.form.get('norm_road_lpkm'), norm_idle=request.form.get('norm_idle_lph'),
                             hours=request.form.get('work_hours'))
        return done('Texnika saqlandi.', url_for('admin.equipment'))
    year = season_arg()
    rows = q('''SELECT e.*,
                   (SELECT COUNT(*) FROM trailer_loads tl WHERE (tl.trailer_id=e.id OR tl.tractor_id=e.id) AND tl.season_year=?
                      AND tl.status<>'BEKOR') trips,
                   (SELECT COALESCE(SUM(kg),0) FROM harvests h WHERE h.combine_id=e.id AND h.season_year=? AND h.voided_at IS NULL) combine_kg,
                   (SELECT status FROM trailer_loads tl WHERE (tl.trailer_id=e.id OR tl.tractor_id=e.id)
                      AND tl.status IN ('OCHIQ','TOLDI') LIMIT 1) busy,
                   t.imei, t.last_seen gps_seen
                FROM equipment e LEFT JOIN trackers t ON t.equipment_id=e.id ORDER BY e.active DESC, e.kind, e.code''', (year, year))
    edit = None
    if request.args.get('edit', '').isdigit():
        edit = q('''SELECT e.*, t.imei FROM equipment e LEFT JOIN trackers t ON t.equipment_id=e.id WHERE e.id=?''',
                 (int(request.args['edit']),), one=True)
    from .. import fleet
    return render_template('admin_equipment.html', rows=rows, edit=edit, year=year, unknown=fleet.unknown_trackers(),
                           kind_norms=fleet.KIND_NORMS, def_hours=get_setting('fleet_work_hours'),
                           today=today_str())


@bp.route('/sozlamalar', methods=['GET', 'POST'])
@perm_required('settings.manage')
def settings():
    if request.method == 'POST':
        actor = post_actor()
        changed = save_settings(actor, {k[4:]: v for k, v in request.form.items() if k.startswith('set_')})
        return done(f'Sozlamalar saqlandi ({len(changed)} ta o‘zgarish).', url_for('admin.settings'))
    cfg = current_app.config['SURXON']
    status = {
        'telegram_token': bool(cfg.TELEGRAM_BOT_TOKEN), 'webhook_secret': bool(cfg.TELEGRAM_WEBHOOK_SECRET),
        'bot_username': cfg.TELEGRAM_BOT_USERNAME, 'domain': cfg.DOMAIN, 'cookie_secure': cfg.COOKIE_SECURE,
        'db': str(cfg.DB_PATH), 'uploads': str(cfg.UPLOAD_DIR),
    }
    return render_template('admin_settings.html', rows=all_settings(), status=status)


@bp.route('/mavsumlar', methods=['GET', 'POST'])
@perm_required('seasons.manage', 'reports.view')
def seasons():
    if request.method == 'POST':
        from ..security import require
        require('seasons.manage')
        actor = post_actor()
        year = parse_int(request.form.get('year'), 'Yil')
        action = request.form.get('action')
        if action == 'close':
            close_season(actor, year, request.form.get('reason', ''))
            msg = f'{year}-mavsum yopildi va arxivlandi. Ma’lumotlar abadiy saqlanadi.'
        elif action == 'reopen':
            reopen_season(actor, year, request.form.get('reason', ''))
            msg = f'{year}-mavsum qayta ochildi.'
        elif action == 'new':
            if year < 2020 or year > 2100:
                raise UserError('Yil noto‘g‘ri.')
            save_settings(actor, {'current_season': str(year)})
            from ..db import get_db
            from ..services import ensure_season
            ensure_season(get_db(), year)
            msg = f'Joriy mavsum: {year}.'
        else:
            raise UserError('Noma’lum amal.')
        return done(msg, url_for('admin.seasons'))
    return render_template('admin_seasons.html', rows=queries.season_comparison(),
                           area=scalar('SELECT COALESCE(SUM(area_ha),0) FROM fields WHERE active=1'))


@bp.route('/zaxira', methods=['GET', 'POST'])
@perm_required('backup.manage')
def backups():
    cfg = current_app.config['SURXON']
    if request.method == 'POST':
        post_actor()
        from ..backup import run_backup
        return done(run_backup(cfg), url_for('admin.backups'))
    files = sorted((p for p in cfg.BACKUP_DIR.glob('surxon_*') if p.is_file()), key=lambda p: p.name, reverse=True)
    from datetime import datetime
    from ..db import q as _q
    off = _q("SELECT * FROM channel_status WHERE channel='offsite'", one=True)
    return render_template('admin_backups.html', keep=cfg.BACKUP_KEEP_DAYS, offsite_on=bool(cfg.refresh_offsite()), offsite=off,
                           files=[(p.name, p.stat().st_size, datetime.fromtimestamp(p.stat().st_mtime, cfg.TZ).strftime('%d.%m.%Y %H:%M'))
                                  for p in files[:60]])


@bp.get('/zaxira/<name>')
@perm_required('backup.manage')
def backup_download(name):
    if '/' in name or '..' in name or not name.startswith('surxon_'):
        abort(404)
    return send_from_directory(current_app.config['SURXON'].BACKUP_DIR, name, as_attachment=True)
