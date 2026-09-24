"""Master data and administration: users, brigades, fields, equipment, settings, seasons, backups."""
from flask import Blueprint, abort, current_app, render_template, request, send_from_directory, url_for

from .. import queries
from ..db import q, scalar
from ..security import PERMISSIONS, ROLES, perm_required
from ..services import (close_season, reopen_season, save_brigadier, save_equipment, save_field, save_settings,
                        save_user, telegram_link_code, unlink_telegram)
from ..settings import all_settings, get_setting
from ..utils import UserError, parse_int, parse_number
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
                  phone=request.form.get('phone', ''), active=checkbox('active') if uid else True)
        return done('Foydalanuvchi saqlandi.' + ('' if uid else ' Birinchi kirishda parolni almashtirish so‘raladi.'),
                    url_for('admin.users'))
    edit = None
    if request.args.get('edit', '').isdigit():
        edit = q('SELECT * FROM users WHERE id=?', (int(request.args['edit']),), one=True)
    return render_template('admin_users.html', rows=q('''SELECT u.*, b.name brigadier_name FROM users u
                                                         LEFT JOIN brigadiers b ON b.id=u.brigadier_id ORDER BY u.active DESC, u.role, u.full_name'''),
                           brigadiers=q('SELECT * FROM brigadiers WHERE active=1 ORDER BY name'), roles=ROLES, edit=edit,
                           permissions=PERMISSIONS)


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
    return render_template('field_detail.html', f=f, history=history,
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
        save_equipment(post_actor(), parse_int(request.form.get('id'), 'ID', required=False),
                       kind=request.form.get('kind'), code=request.form.get('code'), plate=request.form.get('plate', ''),
                       operator_name=request.form.get('operator_name', ''), ownership=request.form.get('ownership') or 'own',
                       notes=request.form.get('notes', ''), active=checkbox('active') if request.form.get('id') else True)
        return done('Texnika saqlandi.', url_for('admin.equipment'))
    year = season_arg()
    rows = q('''SELECT e.*,
                   (SELECT COUNT(*) FROM trailer_loads tl WHERE (tl.trailer_id=e.id OR tl.tractor_id=e.id) AND tl.season_year=?
                      AND tl.status<>'BEKOR') trips,
                   (SELECT COALESCE(SUM(kg),0) FROM harvests h WHERE h.combine_id=e.id AND h.season_year=? AND h.voided_at IS NULL) combine_kg,
                   (SELECT status FROM trailer_loads tl WHERE (tl.trailer_id=e.id OR tl.tractor_id=e.id)
                      AND tl.status IN ('OCHIQ','TOLDI') LIMIT 1) busy
                FROM equipment e ORDER BY e.active DESC, e.kind, e.code''', (year, year))
    edit = None
    if request.args.get('edit', '').isdigit():
        edit = q('SELECT * FROM equipment WHERE id=?', (int(request.args['edit']),), one=True)
    return render_template('admin_equipment.html', rows=rows, edit=edit, year=year)


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
    return render_template('admin_backups.html', files=[(p.name, p.stat().st_size, p.stat().st_mtime) for p in files[:60]],
                           keep=cfg.BACKUP_KEEP_DAYS)


@bp.get('/zaxira/<name>')
@perm_required('backup.manage')
def backup_download(name):
    if '/' in name or '..' in name or not name.startswith('surxon_'):
        abort(404)
    return send_from_directory(current_app.config['SURXON'].BACKUP_DIR, name, as_attachment=True)
