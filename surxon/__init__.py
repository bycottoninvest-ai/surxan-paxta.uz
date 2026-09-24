"""SURXON PAXTA HISOB TIZIMI — Flask application factory."""
import json
import os
import secrets
from datetime import timedelta

import click
from flask import Flask, g, jsonify, render_template, request, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash

from . import db as dbmod
from .config import BASE_DIR, Config
from .security import (PERMISSIONS, ROLES, brigadier_scope, can, csrf_token, load_user, wants_json)
from .utils import UserError, fmt_date, fmt_money, fmt_num, now_str, today_str, weekday_name

VERSION = '2.0.0'


def create_app(**overrides):
    cfg = Config(**overrides)
    cfg.validate()
    app = Flask(__name__, template_folder=str(BASE_DIR / 'templates'), static_folder=str(BASE_DIR / 'static'))
    app.config.update(
        SURXON=cfg,
        SECRET_KEY=cfg.SECRET_KEY,
        MAX_CONTENT_LENGTH=cfg.MAX_UPLOAD_MB * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=cfg.COOKIE_SECURE,
        SESSION_COOKIE_NAME='surxon_session',
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        TESTING=cfg.TESTING,
        SEND_FILE_MAX_AGE_DEFAULT=timedelta(days=7),
    )
    if not cfg.TESTING:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.teardown_appcontext(dbmod.close_db)

    with app.app_context():
        conn = dbmod.get_db()
        dbmod.migrate(conn)
        seed(conn, cfg)

    from .views import admin, auth, finance, integrations, main, ops, people, reports
    for bp in (auth.bp, main.bp, ops.bp, people.bp, finance.bp, reports.bp, admin.bp, integrations.bp):
        app.register_blueprint(bp)
    from .telegram_bot import bp as tg_bp
    app.register_blueprint(tg_bp)

    app.before_request(load_user)
    register_template_helpers(app)
    register_errors(app)
    register_cli(app)

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        resp.headers.setdefault('Referrer-Policy', 'same-origin')
        resp.headers.setdefault('Permissions-Policy', 'geolocation=(self), camera=(self)')
        resp.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; img-src 'self' data: blob: https://*.arcgisonline.com https://*.tile.openstreetmap.org; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self' https://api.open-meteo.com; frame-ancestors 'self'")
        if request.endpoint and not request.endpoint.startswith('static') and g.get('user'):
            resp.headers['Cache-Control'] = 'no-store'
        return gzip_response(resp)

    return app


COMPRESSIBLE = ('text/', 'application/json', 'application/javascript', 'image/svg+xml', 'application/manifest+json')


def gzip_response(resp):
    import gzip
    if resp.direct_passthrough and (resp.mimetype or '').startswith(COMPRESSIBLE) and (resp.content_length or 0) < 600_000:
        resp.direct_passthrough = False  # small static text file: read it so it can be compressed
    if (resp.direct_passthrough or resp.status_code < 200 or resp.status_code >= 300 or 'Content-Encoding' in resp.headers
            or 'gzip' not in request.headers.get('Accept-Encoding', '')
            or not (resp.mimetype or '').startswith(COMPRESSIBLE)):
        return resp
    data = resp.get_data()
    if len(data) < 800:
        return resp
    resp.set_data(gzip.compress(data, compresslevel=6))
    resp.headers['Content-Encoding'] = 'gzip'
    resp.headers['Vary'] = 'Accept-Encoding'
    return resp


def seed(conn, cfg):
    """First-run data: admin login, the three known brigades and the agreed starting fleet."""
    from .services import ensure_season
    with dbmod.tx(conn):
        year = int(today_str_safe()[:4])
        ensure_season(conn, year)
        if not conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            password = cfg.ADMIN_PASSWORD
            generated = False
            if not password:
                password = secrets.token_urlsafe(12)
                generated = True
            conn.execute('''INSERT INTO users(username, password_hash, full_name, role, must_change_password, created_at)
                            VALUES (?,?,?,?,1,?)''',
                         (cfg.ADMIN_USER.lower(), generate_password_hash(password), 'Administrator', 'admin', now_str_safe()))
            if generated and not cfg.TESTING:
                path = cfg.DATA_DIR / 'BIRINCHI_ADMIN_PAROLI.txt'
                path.write_text(f'Login: {cfg.ADMIN_USER}\nParol: {password}\n'
                                'Birinchi kirishda parolni almashtiring, keyin bu faylni o‘chiring.\n', encoding='utf-8')
                os.chmod(path, 0o600)
        if not conn.execute('SELECT 1 FROM brigadiers LIMIT 1').fetchone():
            for name in ('Juma ota', 'Nurim ota', 'Bayram ota'):
                conn.execute('INSERT INTO brigadiers(name, created_at) VALUES (?,?)', (name, now_str_safe()))
        if not conn.execute('SELECT 1 FROM equipment LIMIT 1').fetchone():
            for kind, codes in (('traktor', ['T-01', 'T-02', 'T-03']), ('telashka', ['TL-01', 'TL-02', 'TL-03', 'TL-04']),
                                ('kombayn', ['K-01', 'K-02'])):
                for code in codes:
                    conn.execute('INSERT INTO equipment(kind, code, created_at) VALUES (?,?,?)', (kind, code, now_str_safe()))


def today_str_safe():
    try:
        return today_str()
    except RuntimeError:
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d')


def now_str_safe():
    try:
        return now_str()
    except RuntimeError:
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def register_template_helpers(app):
    from .photos import CATEGORIES

    app.jinja_env.filters.update(num=fmt_num, money=fmt_money, d=fmt_date, weekday=weekday_name,
                                 tojson_safe=lambda v: json.dumps(v, ensure_ascii=False))

    @app.context_processor
    def inject():
        user = g.get('user')
        year = None
        notes = []
        if user:
            from .queries import notifications
            from .services import current_season
            try:
                year = int(request.args.get('season')) if request.args.get('season', '').isdigit() else current_season(dbmod.get_db())
            except Exception:
                year = int(today_str()[:4])
            notes = notifications(user, year)
        seasons = [r['year'] for r in dbmod.q('SELECT year FROM seasons ORDER BY year DESC')] if user else []
        return {
            'current_user': user, 'role_labels': ROLES, 'csrf_token': csrf_token, 'can': can,
            'season': year, 'seasons': seasons, 'today': today_str(), 'now_time': now_str()[11:16],
            'domain': app.config['SURXON'].DOMAIN, 'version': VERSION, 'notifications': notes,
            'photo_categories': CATEGORIES, 'my_brigade': brigadier_scope(),
            'bot_username': app.config['SURXON'].TELEGRAM_BOT_USERNAME,
        }

    @app.template_global()
    def photo_url(path):
        return url_for('main.media', path=path) if path else url_for('static', filename='img/trailer.jpg')

    @app.template_global()
    def season_url(**kw):
        args = dict(request.view_args or {})
        args.update(request.args.to_dict())
        args.update(kw)
        return url_for(request.endpoint, **args)


def register_errors(app):
    from flask import flash, redirect

    @app.errorhandler(UserError)
    def user_error(e):
        if wants_json():
            return jsonify(ok=False, error=str(e)), 422
        flash(str(e), 'error')
        return redirect(request.referrer or url_for('main.dashboard'))

    @app.errorhandler(413)
    def too_large(e):
        msg = f'Fayl juda katta. Maksimum {app.config["SURXON"].MAX_UPLOAD_MB} MB.'
        if wants_json():
            return jsonify(ok=False, error=msg), 413
        flash(msg, 'error')
        return redirect(request.referrer or url_for('main.dashboard'))

    @app.errorhandler(HTTPException)
    def http_error(e):
        if wants_json():
            return jsonify(ok=False, error=e.description), e.code
        return render_template('error.html', code=e.code, message=e.description), e.code

    @app.errorhandler(Exception)
    def server_error(e):
        app.logger.exception('Unhandled error: %s', e)
        if app.config['TESTING']:
            raise e
        if wants_json():
            return jsonify(ok=False, error='Serverda xatolik. Ma’lumot saqlanmadi — qayta urinib ko‘ring.'), 500
        return render_template('error.html', code=500,
                               message='Kutilmagan xatolik. Ma’lumot saqlanmadi. Qayta urinib ko‘ring yoki Adminga xabar bering.'), 500


def register_cli(app):
    @app.cli.command('backup')
    def backup_cmd():
        """Consistent online backup of the database and photos."""
        from .backup import run_backup
        click.echo(run_backup(app.config['SURXON']))

    @app.cli.command('reset-password')
    @click.argument('username')
    @click.argument('password')
    def reset_password(username, password):
        """Emergency password reset from the server shell."""
        from .security import Actor
        from .services import change_password
        user = dbmod.q('SELECT * FROM users WHERE username=?', (username.lower(),), one=True)
        if not user:
            raise click.ClickException('Bunday login yo‘q')
        change_password(Actor(None, 'system', source='cli'), user['id'], password)
        dbmod.get_db().execute('UPDATE users SET must_change_password=1, active=1 WHERE id=?', (user['id'],))
        click.echo('Parol yangilandi (birinchi kirishda almashtirish talab qilinadi).')

    @app.cli.command('set-webhook')
    def set_webhook():
        """Register the Telegram webhook at https://DOMAIN/telegram/webhook/SECRET."""
        from .telegram_bot import tg_api
        cfg = app.config['SURXON']
        if not cfg.TELEGRAM_WEBHOOK_SECRET:
            raise click.ClickException('TELEGRAM_WEBHOOK_SECRET o‘rnatilmagan')
        url = f'https://{cfg.DOMAIN}/telegram/webhook/{cfg.TELEGRAM_WEBHOOK_SECRET}'
        res = tg_api('setWebhook', {'url': url, 'allowed_updates': ['message', 'callback_query'],
                                    'secret_token': cfg.TELEGRAM_WEBHOOK_SECRET, 'drop_pending_updates': False})
        click.echo(json.dumps(res, ensure_ascii=False))

    @app.cli.command('demo-data')
    @click.option('--yes', is_flag=True, help='Bo‘sh bazaga namunaviy ma’lumot yozish')
    def demo_data(yes):
        """Fill an EMPTY database with sample data (for training/demo only)."""
        from .demo import fill_demo
        if not yes:
            raise click.ClickException('Faqat sinov bazasi uchun. Tasdiqlash: flask demo-data --yes')
        click.echo(fill_demo())

    @app.cli.command('permissions')
    def permissions():
        for perm, roles in PERMISSIONS.items():
            click.echo(f'{perm:20} admin, ' + ', '.join(sorted(roles)))
