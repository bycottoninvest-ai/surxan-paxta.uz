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

VERSION = '2.6.1'


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
        try:
            saved = dbmod.backup_before_upgrade(conn)
        except Exception as e:      # e.g. another service (worker/backup) made the same copy this second
            saved = None
            app.logger.warning('Yangilashdan oldingi nusxa olinmadi (%s) — boshqa xizmat olgan bo‘lishi mumkin.', e)
        if saved:
            app.logger.warning('Baza yangi versiyaga o‘tishdan oldin saqlandi: %s', saved)
        dbmod.migrate(conn)
        seed(conn, cfg)

    from .views import acct, admin, auth, finance, integrations, kuzatuv, main, ops, people, punkt, reports
    for bp in (auth.bp, main.bp, ops.bp, people.bp, finance.bp, reports.bp, admin.bp, integrations.bp, punkt.bp, acct.bp,
               kuzatuv.bp):
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
        if request.endpoint and not request.endpoint.startswith('static') and (g.get('user') or resp.mimetype == 'text/html'):
            resp.headers['Cache-Control'] = 'no-store'  # phones must never show an old page after an update
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
        if not conn.execute('SELECT 1 FROM stations LIMIT 1').fetchone():
            conn.execute("INSERT INTO stations(name, created_at) VALUES ('Nayman-1', ?)", (now_str_safe(),))
        conn.execute('UPDATE trailer_loads SET station_id=(SELECT MIN(id) FROM stations WHERE active=1) '
                     'WHERE station_id IS NULL')
        if not conn.execute('SELECT 1 FROM cashboxes LIMIT 1').fetchone():
            conn.execute("INSERT INTO cashboxes(name, created_at) VALUES ('Paxta mavsumi kassasi', ?)", (now_str_safe(),))
        for t in ('cash_entries', 'expenses'):
            conn.execute(f'UPDATE {t} SET cashbox_id=(SELECT MIN(id) FROM cashboxes WHERE active=1) WHERE cashbox_id IS NULL')
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
            'test_mode': app.config['SURXON'].APP_MODE == 'test',
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

    @app.cli.command('xodimlar')
    @click.argument('rows', nargs=-1, required=True)
    def xodimlar(rows):
        """Create staff logins in one go: "Asadbek:hisobchi" "Yunus:punkt" "Karim aka:kassir" ...
        Roles: hisobchi, punkt, buxgalter, kassir, rahbar, tarozi, haydovchi, admin. Passwords are generated here,
        printed once and saved to data/LOGINLAR.txt (never sent anywhere). Existing people are left untouched."""
        import os
        from .security import Actor
        from .services import STAFF_ROLE_ALIASES, quick_add_user
        actor = Actor(None, 'admin', source='cli', name='server')
        out = []
        for row in rows:
            name, _, role = row.rpartition(':')
            name, role = name.strip(), STAFF_ROLE_ALIASES.get(role.strip().lower(), role.strip().lower())
            if not name or role not in STAFF_ROLE_ALIASES.values():
                raise click.ClickException(f'“{row}” — format: "Ism:rol" (rol: {", ".join(sorted(STAFF_ROLE_ALIASES))})')
            if dbmod.q('SELECT 1 FROM users WHERE lower(full_name)=lower(?) AND role=?', (name, role), one=True):
                click.echo(f'  = {name}: allaqachon bor, o‘zgartirilmadi')
                continue
            _uid, username, pw = quick_add_user(actor, name, role)
            out.append((name, ROLES.get(role, role), username, pw))
        if not out:
            return
        lines = [f'{a:<22} {b:<20} login: {c:<14} parol: {d}' for a, b, c, d in out]
        path = app.config['SURXON'].DATA_DIR / 'LOGINLAR.txt'
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')
        os.chmod(path, 0o600)
        click.echo('\n'.join(lines))
        click.echo(f'\nSaqlandi: {path}. Har kim birinchi kirganda o‘z parolini qo‘yadi.')

    @app.cli.command('set-webhook')
    def set_webhook():
        """Register the Telegram webhook at https://DOMAIN/telegram/webhook/SECRET."""
        from .telegram_bot import tg_api
        cfg = app.config['SURXON']
        if not cfg.TELEGRAM_WEBHOOK_SECRET:
            raise click.ClickException('TELEGRAM_WEBHOOK_SECRET o‘rnatilmagan')
        url = f'https://{cfg.DOMAIN}/telegram/webhook/{cfg.TELEGRAM_WEBHOOK_SECRET}'
        res = tg_api('setWebhook', {'url': url, 'allowed_updates': ['message', 'callback_query', 'my_chat_member', 'chat_member', 'channel_post'],
                                    'secret_token': cfg.TELEGRAM_WEBHOOK_SECRET, 'drop_pending_updates': False})
        click.echo(json.dumps(res, ensure_ascii=False))

    @app.cli.command('demo-data')
    @click.option('--yes', is_flag=True, help='Bo‘sh bazaga namunaviy ma’lumot yozish')
    def demo_data(yes):
        """Fill an EMPTY database with sample data (for training/demo only)."""
        from .demo import fill_demo
        if app.config['SURXON'].APP_MODE != 'test':
            raise click.ClickException('Namunaviy ma’lumot faqat TEST rejimida (APP_MODE=test, alohida data-test papkasi) yoziladi.')
        if not yes:
            raise click.ClickException('Faqat sinov bazasi uchun. Tasdiqlash: flask demo-data --yes')
        click.echo(fill_demo())

    @app.cli.command('outbox-run')
    def outbox_run():
        """Send pending archive jobs once (the docker 'worker' service does this every 30 s)."""
        from .outbox import run_once
        click.echo(run_once(limit=500))

    @app.cli.command('smoke-check')
    @click.option('--send-tests', is_flag=True, help='Arxiv kanallariga haqiqiy sinov xabari yuborish')
    def smoke_check(send_tests):
        """Post-deploy check: database, config, Telegram webhook, archives. Prints an honest status table."""
        from .outbox import CHANNELS, configured, send_test, status
        cfg = app.config['SURXON']
        rows = []
        try:
            dbmod.scalar('SELECT COUNT(*) FROM users')
            rows.append(('Baza', 'OK', str(cfg.DB_PATH)))
        except Exception as exc:
            rows.append(('Baza', 'XATO', str(exc)))
        rows.append(('HTTPS cookie', 'OK' if cfg.COOKIE_SECURE else 'O‘CHIQ', 'COOKIE_SECURE'))
        if cfg.TELEGRAM_BOT_TOKEN:
            try:
                from .telegram_bot import tg_api
                info = tg_api('getWebhookInfo')['result']
                ok = info.get('url', '').startswith(f'https://{cfg.DOMAIN}/telegram/webhook/')
                rows.append(('Telegram webhook', 'OK' if ok else 'SOZLANMAGAN',
                             (info.get('url') or 'yo‘q').split('/webhook/')[0] + f' · kutilayotgan: {info.get("pending_update_count")}'
                             + (f' · oxirgi xato: {info.get("last_error_message")}' if info.get('last_error_message') else '')))
            except Exception as exc:
                rows.append(('Telegram webhook', 'XATO', str(exc)))
        else:
            rows.append(('Telegram bot', 'ULANMAGAN', 'TELEGRAM_BOT_TOKEN yo‘q'))
        for ch, label in CHANNELS.items():
            if not configured(ch):
                rows.append((label, 'ULANMAGAN', 'sozlama yo‘q'))
                continue
            if send_tests:
                try:
                    send_test(ch)
                    rows.append((label, 'ISHLAYAPTI', 'sinov yuborildi'))
                except Exception as exc:
                    rows.append((label, 'XATO', str(exc)[:200]))
            else:
                st = next(s for s in status() if s['channel'] == ch)
                rows.append((label, st['state'].upper(), f'oxirgi muvaffaqiyat: {st["last_ok_at"] or "hali yo‘q"}'))
        for name, state, note in rows:
            click.echo(f'{name:40} {state:12} {note}')

    @app.cli.command('backup-verify')
    @click.option('--offsite', is_flag=True, help='Tashqi joydan yuklab olib ham tekshirish')
    def backup_verify(offsite):
        """Make a fresh backup, restore it into a separate database and compare it with the live one."""
        from .backup import offsite_copy, run_backup, verify_restore
        from .outbox import record_result
        cfg = app.config['SURXON']
        click.echo(run_backup(cfg))
        for off in ([False, True] if offsite else [False]):
            name = 'restore_offsite' if off else 'restore_local'
            try:
                if off:
                    offsite_copy(cfg)
                res = verify_restore(cfg, offsite=off)
            except Exception as exc:
                with dbmod.tx() as db:
                    record_result(name, False, exc, db)
                click.echo(f'{"TASHQI" if off else "SERVER"} zaxiradan tiklash: XATO — {exc}')
                continue
            click.echo(f'\n{"TASHQI JOYDAN" if off else "SERVERDAGI"} zaxiradan alohida bazaga tiklandi: {res["source"]}')
            for label, got, live, same in res['rows']:
                click.echo(f'  {label:32} tiklangan={got!s:>14}  ishchi={live!s:>14}  {"OK" if same else "FARQ"}')
            for p in res['problems']:
                click.echo(f'  MUAMMO: {p}')
            ok = res['all_equal']
            with dbmod.tx() as db:
                record_result(name, ok, None if ok else 'farq: ' + '; '.join(res['problems'] or ['sonlar mos emas']), db)
            click.echo('  NATIJA: ' + ('ISHLAYDI — tiklangan baza ishchi baza bilan bir xil.' if ok else 'XATO — yuqoridagi farqlarni ko‘ring.'))

    @app.cli.command('sheets-inspect')
    def sheets_inspect_cmd():
        """Read-only: list the spreadsheet's tabs, header rows and formula cells before syncing anything."""
        from .outbox import configured, sheets_inspect, tab_name
        if not configured('sheets'):
            raise click.ClickException('Google Sheets ulanmagan (GOOGLE_SHEETS_ID yoki xizmat akkaunti fayli yo‘q).')
        info = sheets_inspect()
        click.echo(f'Jadval: {info["title"]}')
        for t in info['tabs']:
            mine = t['title'].startswith(tab_name(''))
            click.echo(f'\n[{t["title"]}] {"(tizim varag‘i)" if mine else "(qo‘lda — tizim YOZMAYDI)"} '
                       f'{t["filled_rows"]} qator, formulali katak: {t["formula_cells"]}')
            click.echo('  sarlavha: ' + ' | '.join(str(x) for x in t['header'][:20]))
            for r, c, f in t['formula_samples']:
                click.echo(f'  formula {r}-qator {c}-ustun: {f[:80]}')

    @app.cli.command('sheets-sinov')
    @click.option('--katak', multiple=True, help="Dashboarddagi katak, masalan \"Umumiy hisob!B4\" (qadamlarda o‘qib ko‘rsatiladi)")
    def sheets_sinov(katak):
        """Live proof: 1 000 so‘m test income → Sheets row + dashboard change → resend (no double) → void (back)."""
        from .accounting import add_income
        from .outbox import configured, run_once, sheets_read, tab_name
        from .reporting import maybe_refresh_sheet_summary
        from .security import Actor
        from .services import void_cash_entry
        if not configured('sheets'):
            raise click.ClickException('Google Sheets ulanmagan.')
        actor = Actor(None, 'admin', source='cli', name='Sheets sinovi')

        def sync():
            with app.test_request_context():
                maybe_refresh_sheet_summary()
                res = run_once(limit=500)
            if res['errors']:
                err = dbmod.q("SELECT last_error FROM outbox WHERE channel='sheets' AND last_error IS NOT NULL ORDER BY id DESC",
                              one=True)
                raise click.ClickException(f'Sheets yozishda xato: {err["last_error"] if err else res}')

        def look(title, doc):
            rows = sheets_read(tab_name('KASSA KIRIM-CHIQIM'), 'A1:J5000')
            mine = [r for r in rows if r and r[0] == doc]
            summary = {r[0]: r[2] for r in sheets_read(tab_name('UMUMIY'), 'A1:C200') if len(r) > 2}
            extra = ''
            for k in katak:
                sh, rng = k.split('!', 1)
                v = sheets_read(sh, rng)
                extra += f' | {k} = {v[0][0] if v and v[0] else "(bo‘sh)"}'
            click.echo(f'{title:38} SPX qatorlari: {len(rows) - 1:>4} | {doc} qatori: {len(mine)} '
                       f'({mine[0][9] if mine and len(mine[0]) > 9 else "-"}) | KASSA_QOLDIQ = {summary.get("KASSA_QOLDIQ")}{extra}')
            return len(rows), len(mine), summary.get('KASSA_QOLDIQ')

        sync()
        n0, _, k0 = look('0) Boshlanish', '-')
        with app.test_request_context():
            cid, doc = add_income(actor, amount=1000, source='SINOV', note='Google Sheets sinovi — darhol bekor qilinadi')
        sync()
        n1, m1, k1 = look('1) 1 000 so‘m sinov kirimi yozildi', doc)
        dbmod.get_db().execute("UPDATE outbox SET status='pending', next_try_at=0 WHERE channel='sheets' AND ref LIKE ?",
                               (f'KASSA KIRIM-CHIQIM:{doc}:%',))
        sync()
        n2, m2, k2 = look('2) O‘sha yozuv qayta yuborildi', doc)
        with app.test_request_context():
            void_cash_entry(actor, cid, 'Google Sheets sinovi tugadi')
        sync()
        n3, m3, k3 = look('3) Sinov yozuvi bekor qilindi', doc)
        ok = m1 == 1 and m2 == 1 and n2 == n1 and m3 == 1 and str(k3) == str(k0)
        click.echo('NATIJA: ' + ('ISHLAYDI — qator bitta, qayta yuborishda ko‘paymadi, bekor qilinganda jami qaytdi.' if ok
                                 else 'XATO — yuqoridagi qadamlarni ko‘ring.'))

    @app.cli.command('holat')
    def holat():
        """Every part of the system: ISHLAYDI / ULANMAGAN / TEKSHIRILMAGAN / XATO — honest, from real checks only."""
        import urllib.request
        from .outbox import CHANNELS, status
        cfg = app.config['SURXON']
        rows = []

        def add(name, state, note=''):
            rows.append((name, state, note))
        try:
            n = dbmod.scalar('SELECT COUNT(*) FROM users')
            add('Server va baza', 'ISHLAYDI', f'{cfg.DB_PATH} · v{VERSION} · {cfg.APP_MODE}')
        except Exception as exc:
            add('Server va baza', 'XATO', str(exc))
        try:
            with urllib.request.urlopen(f'https://{cfg.DOMAIN}/health', timeout=15) as r:
                add('HTTPS', 'ISHLAYDI' if r.status == 200 else 'XATO', f'https://{cfg.DOMAIN}')
        except urllib.error.HTTPError as e:
            # the temporary address is behind a password (401) — HTTPS itself works
            add('HTTPS', 'ISHLAYDI' if e.code in (401, 200) else 'XATO', f'https://{cfg.DOMAIN} (javob {e.code})')
        except Exception as exc:
            add('HTTPS', 'XATO', f'https://{cfg.DOMAIN}: {exc}')
        if cfg.TELEGRAM_BOT_TOKEN:
            try:
                from .telegram_bot import tg_api
                me = tg_api('getMe')['result']
                info = tg_api('getWebhookInfo')['result']
                ok = info.get('url', '').startswith(f'https://{cfg.DOMAIN}/telegram/webhook/')
                add('Telegram bot', 'ISHLAYDI' if ok and not info.get('last_error_message') else 'XATO',
                    f'@{me.get("username")} · webhook {"to‘g‘ri" if ok else "sozlanmagan"}'
                    + (f' · oxirgi xato: {info["last_error_message"]}' if info.get('last_error_message') else ''))
            except Exception as exc:
                add('Telegram bot', 'XATO', str(exc)[:200])
        else:
            add('Telegram bot', 'ULANMAGAN', 'TELEGRAM_BOT_TOKEN yo‘q')
        word = {'working': 'ISHLAYDI', 'not_connected': 'ULANMAGAN', 'configured_untested': 'TEKSHIRILMAGAN', 'error': 'XATO'}
        for st in status():
            add(st['label'], word[st['state']], (f'oxirgi muvaffaqiyat {st["last_ok_at"]}' if st['last_ok_at'] else '')
                + (f' · navbatda {st["pending"]}' if st['pending'] else '') + (f' · xato: {st["last_error"][:120]}' if st['state'] == 'error' else ''))
        backups = sorted(cfg.BACKUP_DIR.glob('surxon_db_*.sqlite3'))
        if backups:
            import time as _t
            age = (_t.time() - backups[-1].stat().st_mtime) / 3600
            add('Serverdagi kunlik zaxira', 'ISHLAYDI' if age < 26 else 'XATO', f'{backups[-1].name} · {age:.1f} soat oldin')
        else:
            add('Serverdagi kunlik zaxira', 'TEKSHIRILMAGAN', 'hali zaxira yo‘q — flask backup-verify')
        for key, label in (('restore_local', 'Zaxiradan tiklash (server nusxasi)'), ('restore_offsite', 'Zaxiradan tiklash (tashqi nusxa)')):
            r = dbmod.q('SELECT * FROM channel_status WHERE channel=?', (key,), one=True)
            if not r:
                add(label, 'TEKSHIRILMAGAN', 'flask backup-verify' + (' --offsite' if 'tashqi' in label else ''))
            elif r['last_ok_at'] and (not r['last_error_at'] or r['last_ok_at'] >= r['last_error_at']):
                add(label, 'ISHLAYDI', f'oxirgi tekshiruv {r["last_ok_at"]}')
            else:
                add(label, 'XATO', (r['last_error'] or '')[:150])
        erp = dbmod.scalar("SELECT COUNT(*) FROM integration_clients WHERE kind='erp' AND revoked_at IS NULL")
        erp_on = dbmod.q("SELECT value FROM settings WHERE key='erp_enabled'", one=True)
        erp_used = dbmod.scalar("SELECT COUNT(*) FROM integration_log WHERE status < 300")
        add('Azizbek ERP API', 'ISHLAYDI' if erp and erp_on and erp_on['value'] == '1' and erp_used else
            ('TEKSHIRILMAGAN' if erp else 'ULANMAGAN'), f'faol kalit: {erp}, muvaffaqiyatli so‘rov: {erp_used}')
        tv = dbmod.scalar("SELECT COUNT(*) FROM integration_clients WHERE kind='tv' AND revoked_at IS NULL")
        add('TV ekran', 'TEKSHIRILMAGAN' if tv else 'ULANMAGAN', f'faol TV kaliti: {tv}')
        add('Elektron tarozi', 'ULANMAGAN', 'kg qo‘lda kiritiladi (tarozi modeli/porti berilmagan)')
        for name, state, note in rows:
            click.echo(f'{name:42} {state:15} {note}')

    @app.cli.command('narx')
    @click.option('--qol', type=int, help='Qo‘l terimi narxi, so‘m/kg')
    @click.option('--kombayn-tonna', type=int, help='Barcha kombaynlar uchun narx, so‘m/tonna')
    def narx(qol, kombayn_tonna):
        """Set the current pay rates (from now on; earlier weighings keep their own rate). Audited."""
        from .accounting import set_combine_tariff
        from .security import Actor
        from .services import save_settings
        actor = Actor(None, 'admin', source='cli', name='sozlash (server)')
        with app.test_request_context():
            if qol:
                save_settings(actor, {'worker_rate_hand': str(qol)})
                click.echo(f'Qo‘l terimi: {qol:,} so‘m/kg (shu paytdan boshlab)'.replace(',', ' '))
            if kombayn_tonna:
                for c in dbmod.q("SELECT id, code FROM equipment WHERE kind='kombayn' AND active=1"):
                    set_combine_tariff(actor, c['id'], tariff_type='tonna', tariff_rate=kombayn_tonna)
                    click.echo(f'{c["code"]}: {kombayn_tonna:,} so‘m/tonna'.replace(',', ' '))

    @app.cli.command('permissions')
    def permissions():
        for perm, roles in PERMISSIONS.items():
            click.echo(f'{perm:20} admin, ' + ', '.join(sorted(roles)))
