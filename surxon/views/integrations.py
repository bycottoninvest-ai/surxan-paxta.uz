"""Read-only integrations: Azizbek ERP API (/api/erp/v1/...) and the TV wallboard (/tv).

Security model
- Every client has its own random key. Only SHA-256(key) is stored; the key is shown once.
- ERP clients carry an explicit list of scopes (which datasets they may read).
- The API has no write endpoints at all: only GET is routed, and every query is SELECT.
- Every request (allowed or refused) is written to integration_log.
- The TV key only unlocks aggregate production figures — no money, no worker names.
"""
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from flask import (Blueprint, abort, current_app, g, jsonify, make_response, render_template, request, url_for)

from .. import queries
from ..db import get_db, q, scalar, tx
from ..security import audit, perm_required
from ..services import current_season
from ..settings import get_bool, get_float, get_setting
from ..utils import UserError, clean_text, now_str, today_str
from . import done, post_actor

bp = Blueprint('integrations', __name__)

API_VERSION = '1'
MAX_PER_PAGE = 500
RATE_LIMIT_PER_MIN = 120

SCOPES = {
    'summary': 'Umumiy ko‘rsatkichlar (kunlik/mavsum)',
    'reference': 'Ma’lumotnoma: dalalar, brigadalar, texnika',
    'harvests': 'Terim yozuvlari (qo‘l / kombayn, kg)',
    'workers': 'Ishchilar ismlari (shaxsiy ma’lumot)',
    'loads': 'Telashka reyslari',
    'weighings': 'Tarozi: brutto / tara / netto',
    'waybills': 'Nakladnoylar',
    'nayman': 'Nayman qabuli',
    'receivables': 'Naymandan qarzdorlik',
    'payments': 'To‘lovlar (tushum)',
    'expenses': 'Xarajatlar',
    'cash': 'Kassa yozuvlari, qoldig‘i va kun yopilishlari',
    'payouts': 'Ishchi / kombayn hisobi: hisoblangan, to‘langan, qoldiq, to‘lov buyruqlari',
    'debts': 'Debitor / kreditor',
}

UNITS = {'mass': 'kg', 'money': 'UZS (so‘m, butun son)', 'area': 'ga (gektar)', 'yield': 'kg/ga',
         'price': 'so‘m/kg', 'time': 'YYYY-MM-DD HH:MM:SS, mahalliy vaqt'}


def _hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


def _new_key(kind):
    prefix = secrets.token_hex(4)
    return prefix, f'spx_{kind}_{prefix}_{secrets.token_urlsafe(32)}'


# =================================================================== admin page

@bp.route('/admin/integratsiyalar', methods=['GET', 'POST'])
@perm_required('settings.manage')
def index():
    if request.method == 'POST':
        actor = post_actor()
        action = request.form.get('action')
        new_key = None
        with tx() as db:
            if action in ('erp_on', 'erp_off', 'tv_on', 'tv_off'):
                key = 'erp_enabled' if action.startswith('erp') else 'tv_enabled'
                val = '1' if action.endswith('on') else '0'
                db.execute('INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) ON CONFLICT(key) '
                           'DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by',
                           (key, val, now_str(), actor.user_id))
                audit(db, actor, 'UPDATE', 'integration', key, new={key: val})
                msg = ('Ulanish yoqildi.' if val == '1' else 'Ulanish o‘chirildi — barcha kalitlar vaqtincha ishlamaydi.')
            elif action == 'create':
                kind = request.form.get('kind') if request.form.get('kind') in ('erp', 'tv') else 'erp'
                name = clean_text(request.form.get('name'), 60) or ('Azizbek ERP' if kind == 'erp' else 'TV ekran')
                scopes = [s for s in request.form.getlist('scopes') if s in SCOPES] if kind == 'erp' else ['tv']
                if kind == 'erp' and not scopes:
                    raise UserError('Kamida bitta ma’lumot turiga ruxsat bering.')
                prefix, new_key = _new_key(kind)
                cur = db.execute('''INSERT INTO integration_clients(kind, name, key_prefix, key_hash, scopes, created_by, created_at)
                                    VALUES (?,?,?,?,?,?,?)''', (kind, name, prefix, _hash(new_key), json.dumps(scopes),
                                                                actor.user_id, now_str()))
                audit(db, actor, 'CREATE', 'integration_client', cur.lastrowid, new={'kind': kind, 'name': name, 'scopes': scopes})
                msg = 'Kalit yaratildi. Uni hozir nusxalab oling — keyin qayta ko‘rsatilmaydi.'
            elif action in ('test_telegram_archive', 'test_telegram_report', 'test_sheets', 'test_offsite', 'outbox_run',
                            'outbox_retry'):
                msg = None
            elif action in ('gmaps_save', 'gmaps_clear'):
                # Google Map Tiles key: a browser key (sent to every map page anyway), restricted in Google Cloud to this
                # site and to the Map Tiles API — so it may be kept in settings. Shown masked, never in full.
                val = ''
                if action == 'gmaps_save':
                    import re as _re
                    val = (request.form.get('gmaps_key') or '').strip()
                    if not _re.fullmatch(r'AIza[0-9A-Za-z_-]{30,}', val):
                        raise UserError('Kalit ko‘rinishi noto‘g‘ri — Google Cloud’dan “AIza…” bilan boshlanadigan kalitni nusxalang.')
                db.execute('INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) ON CONFLICT(key) '
                           'DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by',
                           ('google_maps_key', val, now_str(), actor.user_id))
                audit(db, actor, 'UPDATE', 'integration', 'google_maps_key', new={'google_maps_key': ('…' + val[-4:]) if val else ''})
                msg = 'Google kaliti saqlandi. Xaritani yangilang (Ctrl+Shift+R) → qatlamlar → “Google sun’iy yo‘ldosh”.' if val \
                    else 'Google kaliti o‘chirildi — bepul Esri foni ishlatiladi.'
            elif action in ('offsite_save', 'offsite_clear'):
                msg = _offsite_save(db, actor, action)
            elif action in ('sheets_save', 'sheets_clear'):
                msg = _sheets_save(db, actor, action)
            elif action == 'tg_channel':
                # the admin picks which channel (one the bot is admin of) is the archive / the report channel
                role = request.form.get('role')
                key = {'archive': 'tg_archive_chat_id', 'report': 'tg_report_chat_id'}.get(role)
                ch = db.execute("SELECT * FROM tg_chats WHERE chat_id=? AND type='channel'",
                                (request.form.get('chat_id', ''),)).fetchone()
                if not key or not ch:
                    raise UserError('Kanal topilmadi.')
                other = 'tg_report_chat_id' if key == 'tg_archive_chat_id' else 'tg_archive_chat_id'
                if (db.execute('SELECT value FROM settings WHERE key=?', (other,)).fetchone() or {'value': ''})['value'] == ch['chat_id']:
                    raise UserError('Bu kanal allaqachon boshqa vazifaga tanlangan. Arxiv va hisobot alohida kanal bo‘lsin.')
                db.execute('INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) ON CONFLICT(key) '
                           'DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by',
                           (key, ch['chat_id'], now_str(), actor.user_id))
                audit(db, actor, 'UPDATE', 'integration', key, new={key: ch['chat_id'], 'title': ch['title']})
                msg = (f'“{ch["title"]}” — {"arxiv" if role == "archive" else "hisobot"} kanali qilib tanlandi. '
                       'Endi “Sinov” tugmasi bilan haqiqiy xabar yuboring.')
            else:
                cid = int(request.form.get('id') or 0)
                c = db.execute('SELECT * FROM integration_clients WHERE id=?', (cid,)).fetchone()
                if not c:
                    raise UserError('Kalit topilmadi.')
                if action == 'scopes':
                    scopes = [s for s in request.form.getlist('scopes') if s in SCOPES]
                    db.execute('UPDATE integration_clients SET scopes=? WHERE id=?', (json.dumps(scopes), cid))
                    audit(db, actor, 'UPDATE', 'integration_client', cid, old={'scopes': json.loads(c['scopes'])},
                          new={'scopes': scopes})
                    msg = 'Ruxsatlar saqlandi.'
                elif action == 'revoke':
                    db.execute('UPDATE integration_clients SET active=0, revoked_at=?, revoked_by=? WHERE id=?',
                               (now_str(), actor.user_id, cid))
                    audit(db, actor, 'REVOKE', 'integration_client', cid, reason=clean_text(request.form.get('reason')))
                    msg = 'Kalit bekor qilindi.'
                elif action == 'rotate':
                    if not c['active']:
                        raise UserError('Bekor qilingan kalitni almashtirib bo‘lmaydi — yangisini yarating.')
                    prefix, new_key = _new_key(c['kind'])
                    db.execute('UPDATE integration_clients SET active=0, revoked_at=?, revoked_by=? WHERE id=?',
                               (now_str(), actor.user_id, cid))
                    cur = db.execute('''INSERT INTO integration_clients(kind, name, key_prefix, key_hash, scopes, created_by, created_at)
                                        VALUES (?,?,?,?,?,?,?)''', (c['kind'], c['name'], prefix, _hash(new_key), c['scopes'],
                                                                    actor.user_id, now_str()))
                    audit(db, actor, 'ROTATE', 'integration_client', cid, new={'new_client_id': cur.lastrowid})
                    msg = 'Kalit almashtirildi: eski kalit endi ishlamaydi. Yangisini hozir nusxalab oling.'
                else:
                    raise UserError('Noma’lum amal.')
        if action and action.startswith('test_'):
            from ..outbox import send_test
            try:
                send_test(action[5:])
                return done('Sinov muvaffaqiyatli: haqiqiy ulanish ishladi.', url_for('integrations.index'))
            except Exception as exc:
                raise UserError(f'Sinov muvaffaqiyatsiz: {exc}')
        if action == 'outbox_run':
            from ..outbox import run_once
            r = run_once()
            return done(f'Yuborildi: {r["sent"]}, xato: {r["errors"]}. Ulanmagan: {", ".join(r["skipped_channels"]) or "yo‘q"}.',
                        url_for('integrations.index'))
        if action == 'outbox_retry':
            from ..outbox import retry_failed
            return done(f'{retry_failed()} ta xato yuborish qayta navbatga qo‘yildi.', url_for('integrations.index'))
        if new_key:
            # shown exactly once, never stored in plain text
            g.new_key = new_key
            return render(new_key=new_key, message=msg)
        return done(msg, url_for('integrations.index'))
    return render()


def _sheets_save(db, actor, action):
    """Google Sheets: the spreadsheet link/id goes to settings, the service-account JSON key to a private file in
    DATA_DIR (never shown again, never in the audit — only its client_email, which is not secret)."""
    import re as _re
    from ..outbox import SA_FILE_NAME
    cfg = current_app.config['SURXON']
    path = Path(cfg.DATA_DIR) / SA_FILE_NAME

    def put(key, val):
        db.execute('INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) ON CONFLICT(key) '
                   'DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by',
                   (key, val, now_str(), actor.user_id))
    if action == 'sheets_clear':
        put('google_sheets_id', ''); put('google_sheets_email', '')
        path.unlink(missing_ok=True)
        audit(db, actor, 'UPDATE', 'integration', 'google_sheets', new={'google_sheets': 'o‘chirildi'})
        return 'Google Sheets uzildi. Yozuvlar navbatda qoladi — qayta ulansa yuboriladi.'
    link = (request.form.get('sheet') or '').strip()
    m = _re.search(r'/spreadsheets/d/([A-Za-z0-9_-]{20,})', link) or _re.fullmatch(r'([A-Za-z0-9_-]{20,})', link)
    if not m:
        raise UserError('Jadval havolasini to‘liq nusxalang (https://docs.google.com/spreadsheets/d/… ).')
    sid = m.group(1)
    raw = b''
    f = request.files.get('sa_file')
    if f and f.filename:
        raw = f.read(20000)
    elif (request.form.get('sa_json') or '').strip():
        raw = request.form['sa_json'].strip().encode()
    email = get_setting('google_sheets_email') or ''
    if raw:
        try:
            info = json.loads(raw.decode('utf-8-sig'))
        except ValueError:
            raise UserError('JSON faylni o‘qib bo‘lmadi — Google Cloud’dan yuklangan .json faylni tanlang.')
        if not isinstance(info, dict) or info.get('type') != 'service_account' or not info.get('client_email') \
                or 'PRIVATE KEY' not in (info.get('private_key') or ''):
            raise UserError('Bu xizmat akkaunti (service account) kaliti emas. Google Cloud → IAM → Service accounts → '
                            'Keys → Add key → JSON.')
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(json.dumps(info).encode())
        os.replace(tmp, path)
        email = info['client_email']
    elif not path.exists():
        raise UserError('Xizmat akkaunti JSON faylini tanlang.')
    put('google_sheets_id', sid); put('google_sheets_email', email)
    audit(db, actor, 'UPDATE', 'integration', 'google_sheets', new={'sheet_id': sid, 'client_email': email})
    return (f'Saqlandi. Endi jadvalda “Share / Поделиться” → {email} ni “Editor” qilib qo‘shing, keyin “Sinov” tugmasini '
            'bosing.')


def _rclone_obscure(password):
    """rclone keeps SFTP passwords “obscured” in its config; the password goes through stdin, never the command line."""
    import subprocess
    try:
        r = subprocess.run(['rclone', 'obscure', '-'], input=password, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise UserError('Serverda rclone topilmadi — bu sahifa faqat serverdagi dasturda ishlaydi.')
    if r.returncode != 0 or not r.stdout.strip():
        raise UserError('Parolni saqlab bo‘lmadi (rclone). Qayta urinib ko‘ring.')
    return r.stdout.strip()


def _offsite_save(db, actor, action):
    """Hetzner Storage Box (SFTP) for backups outside this server. The config (with the obscured password) is a private
    file in DATA_DIR; the page shows only the host and user. A remote set in the server .env takes precedence."""
    import re as _re
    cfg = current_app.config['SURXON']
    path = Path(cfg.DATA_DIR) / cfg.OFFSITE_FILE

    def put(key, val):
        db.execute('INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) ON CONFLICT(key) '
                   'DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by',
                   (key, val, now_str(), actor.user_id))
    if action == 'offsite_clear':
        path.unlink(missing_ok=True)
        put('offsite_host', ''); put('offsite_user', '')
        cfg.refresh_offsite()
        audit(db, actor, 'UPDATE', 'integration', 'offsite', new={'offsite': 'uzildi'})
        return 'Storage Box uzildi. Zaxiralar faqat serverning o‘zida qoladi.'
    host = (request.form.get('host') or '').strip().lower()
    user = (request.form.get('user') or '').strip()
    password = request.form.get('password') or ''
    port = (request.form.get('port') or '23').strip()
    if not _re.fullmatch(r'[a-z0-9]([a-z0-9.-]{1,120})[a-z0-9]', host) or '.' not in host:
        raise UserError('Host noto‘g‘ri. Masalan: u123456.your-storagebox.de')
    if not _re.fullmatch(r'[A-Za-z0-9._-]{2,64}', user):
        raise UserError('Foydalanuvchi nomi noto‘g‘ri. Masalan: u123456')
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise UserError('Port noto‘g‘ri (Storage Box uchun 23).')
    if len(password) < 6:
        raise UserError('Storage Box parolini kiriting.')
    conf = (f'[storagebox]\ntype = sftp\nhost = {host}\nuser = {user}\nport = {int(port)}\n'
            f'pass = {_rclone_obscure(password)}\nshell_type = unix\nmd5sum_command = none\nsha1sum_command = none\n')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as out:
        out.write(conf)
    os.replace(tmp, path)
    cfg.refresh_offsite()
    put('offsite_host', host); put('offsite_user', user)
    audit(db, actor, 'UPDATE', 'integration', 'offsite', new={'host': host, 'user': user, 'port': int(port)})
    return 'Storage Box saqlandi. Endi “Sinov” tugmasini bosing — u yerga haqiqiy fayl yoziladi.'


def _offsite_state():
    cfg = current_app.config['SURXON']
    remote = cfg.refresh_offsite()
    return {'on': bool(remote), 'env': bool(os.environ.get('OFFSITE_RCLONE_REMOTE')),
            'host': get_setting('offsite_host') or '', 'user': get_setting('offsite_user') or '', 'test': cfg.APP_MODE == 'test'}


def _sheets_state():
    from ..outbox import sheets_conf
    cfg = current_app.config['SURXON']
    sid, sa = sheets_conf()
    return {'on': bool(sid and sa), 'env': bool(cfg.GOOGLE_SHEETS_ID and cfg.GOOGLE_SERVICE_ACCOUNT_FILE),
            'id': sid or get_setting('google_sheets_id') or '', 'email': get_setting('google_sheets_email') or '',
            'test': cfg.APP_MODE == 'test', 'prefix': get_setting('sheets_prefix') or ''}


def render(new_key=None, message=None):
    clients = q('''SELECT c.*, u.full_name created_name FROM integration_clients c LEFT JOIN users u ON u.id=c.created_by
                   ORDER BY c.active DESC, c.id DESC''')
    log = q('''SELECT l.*, c.name, c.kind, c.key_prefix FROM integration_log l LEFT JOIN integration_clients c ON c.id=l.client_id
               ORDER BY l.id DESC LIMIT 100''')
    from ..outbox import status as outbox_status
    recent_jobs = q('SELECT * FROM outbox ORDER BY id DESC LIMIT 30')
    return render_template('admin_integrations.html', clients=clients, log=log, scopes=SCOPES, new_key=new_key,
                           archives=outbox_status(), recent_jobs=recent_jobs,
                           message=message, erp_on=get_bool('erp_enabled'), tv_on=get_bool('tv_enabled'),
                           base=request.host_url.rstrip('/'), loads=json.loads,
                           channels=q("SELECT * FROM tg_chats WHERE type='channel' ORDER BY last_seen_at DESC"),
                           gmaps_tail=(lambda k: ('…' + k[-4:]) if k else '')(get_setting('google_maps_key') or ''),
                           gmaps_env=bool(current_app.config['SURXON'].GOOGLE_MAPS_KEY),
                           sheets=_sheets_state(), offsite=_offsite_state(),
                           chosen={'archive': get_setting('tg_archive_chat_id'), 'report': get_setting('tg_report_chat_id')},
                           bot_user=current_app.config['SURXON'].TELEGRAM_BOT_USERNAME)


# =================================================================== auth helpers

def _client_from_key(key, kind):
    if not key or not key.startswith(f'spx_{kind}_'):
        return None
    c = q('SELECT * FROM integration_clients WHERE key_hash=? AND kind=?', (_hash(key), kind), one=True)
    if not c or not c['active']:
        return None
    return c


def _log(client_id, status, rows=None, started=None):
    try:
        get_db().execute('''INSERT INTO integration_log(client_id, at, at_epoch, method, path, query, status, rows, ip, ms)
                            VALUES (?,?,?,?,?,?,?,?,?,?)''',
                         (client_id, now_str(), time.time(), request.method, request.path, request.query_string.decode()[:500],
                          status, rows, request.headers.get('X-Forwarded-For', request.remote_addr),
                          int((time.time() - started) * 1000) if started else None))
        if client_id:
            get_db().execute('UPDATE integration_clients SET last_used_at=?, last_ip=? WHERE id=?',
                             (now_str(), request.headers.get('X-Forwarded-For', request.remote_addr), client_id))
    except Exception:
        current_app.logger.exception('integration log failed')


def _err(status, code, message, client_id=None, started=None):
    _log(client_id, status, started=started)
    resp = jsonify(error={'code': code, 'message': message})
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


# =================================================================== ERP API

API = '/api/erp/v1'


def erp_endpoint(scope):
    """Decorator: bearer-key auth, enabled check, scope check, rate limit, logging."""
    def deco(fn):
        def wrapper(*a, **kw):
            started = time.time()
            if not get_bool('erp_enabled'):
                return _err(503, 'integration_disabled', 'Azizbek ERP ulanishi Admin tomonidan o‘chirilgan.', started=started)
            auth = request.headers.get('Authorization', '')
            key = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
            client = _client_from_key(key, 'erp')
            if not client:
                return _err(401, 'invalid_key', 'Kalit noto‘g‘ri yoki bekor qilingan.', started=started)
            scopes = set(json.loads(client['scopes']))
            if scope and scope not in scopes:
                return _err(403, 'scope_denied', f'Bu kalitga “{scope}” ma’lumotini o‘qish ruxsati berilmagan.',
                            client['id'], started)
            recent = scalar('SELECT COUNT(*) FROM integration_log WHERE client_id=? AND at_epoch>?',
                            (client['id'], time.time() - 60))
            if recent >= RATE_LIMIT_PER_MIN:
                return _err(429, 'rate_limited', f'Daqiqasiga {RATE_LIMIT_PER_MIN} tadan ortiq so‘rov.', client['id'], started)
            g.erp_client, g.erp_scopes = client, scopes
            try:
                payload = fn(*a, **kw)
            except UserError as e:
                return _err(400, 'bad_request', str(e), client['id'], started)
            _log(client['id'], 200, rows=len(payload.get('data', [])) if isinstance(payload.get('data'), list) else 1,
                 started=started)
            resp = jsonify(payload)
            resp.headers['Cache-Control'] = 'no-store'
            return resp
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


class Filters:
    def __init__(self, date_col, updated_expr, field_col=None, season_col=None):
        a = request.args
        db = get_db()
        self.season = int(a['season']) if a.get('season', '').isdigit() else current_season(db)
        self.where, self.params = [], []
        if season_col:
            self.where.append(f'{season_col}=?'); self.params.append(self.season)
        for key, op in (('date_from', '>='), ('date_to', '<=')):
            v = a.get(key)
            if v:
                if len(v) != 10 or v[4] != '-' or v[7] != '-':
                    raise UserError(f'{key} formati YYYY-MM-DD bo‘lishi kerak.')
                self.where.append(f'substr({date_col},1,10) {op} ?'); self.params.append(v)
        if a.get('field_id'):
            if not field_col:
                raise UserError('Bu ma’lumot dala bo‘yicha filtrlanmaydi.')
            if not a['field_id'].isdigit():
                raise UserError('field_id son bo‘lishi kerak.')
            self.where.append(f'{field_col}=?'); self.params.append(int(a['field_id']))
        self.updated_expr = updated_expr
        if a.get('updated_since'):
            v = a['updated_since'].replace('T', ' ')[:19]
            self.where.append(f'{updated_expr} >= ?'); self.params.append(v)  # inclusive: same-second changes are never missed
        self.page = max(1, int(a['page'])) if a.get('page', '').isdigit() else 1
        pp = int(a['per_page']) if a.get('per_page', '').isdigit() else 100
        self.per_page = min(max(pp, 1), MAX_PER_PAGE)
        self.echo = {k: a.get(k) for k in ('season', 'date_from', 'date_to', 'field_id', 'updated_since') if a.get(k)}
        self.echo['season'] = self.season

    def run(self, select, order):
        where = (' WHERE ' + ' AND '.join(self.where)) if self.where else ''
        total = scalar(f'SELECT COUNT(*) FROM ({select}){where}', self.params)
        rows = q(f'SELECT * FROM ({select}){where} ORDER BY {order} LIMIT ? OFFSET ?',
                 self.params + [self.per_page, (self.page - 1) * self.per_page])
        max_upd = scalar(f'SELECT MAX(updated_at) FROM ({select}){where}', self.params, default=None)
        return [dict(r) for r in rows], total, max_upd


def envelope(data, f=None, total=None, max_updated=None, extra=None):
    meta = {
        'api_version': API_VERSION, 'generated_at': now_str(), 'timezone': 'Asia/Tashkent (UTC+05:00)',
        'units': UNITS, 'read_only': True,
    }
    if f is not None:
        meta.update(filters=f.echo, page=f.page, per_page=f.per_page, total=total,
                    has_more=f.page * f.per_page < total, next_page=f.page + 1 if f.page * f.per_page < total else None,
                    max_updated_at=max_updated,
                    sync_hint='Keyingi so‘rovda updated_since=max_updated_at bering. Chegara kiradi (>=), shuning uchun yozuvlarni id bo‘yicha yangilang (upsert).')
    if extra:
        meta.update(extra)
    return {'meta': meta, 'data': data}


def upd(*cols):
    """SQL expression: latest of the given timestamp columns (NULLs ignored)."""
    first = cols[0]
    return 'MAX(' + ', '.join(f'COALESCE({c}, {first})' for c in cols) + ')'


@bp.get(API + '/meta')
@erp_endpoint(None)
def api_meta():
    return envelope({
        'system': 'SURXON PAXTA HISOB TIZIMI', 'company': 'SURXON TAXIATOSH TEXTILE',
        'client': g.erp_client['name'], 'scopes': sorted(g.erp_scopes), 'all_scopes': SCOPES,
        'seasons': [dict(r) for r in q('SELECT year, status, closed_at FROM seasons ORDER BY year DESC')],
        'current_season': current_season(get_db()),
        'measures': {
            'field_kg': 'Dalada ishchi/kombayn bo‘yicha tortilgan ichki kg (harvests).',
            'net_kg': 'Umumiy tarozidagi yakuniy netto (weighings.net_kg) — yakuniy og‘irlik.',
            'accepted_kg': 'Nayman qabul qilgan kg (nayman). Uchalasi alohida; ularni qo‘shib bo‘lmaydi.',
        },
    })


@bp.get(API + '/summary')
@erp_endpoint('summary')
def api_summary():
    db = get_db()
    year = int(request.args['season']) if request.args.get('season', '').isdigit() else current_season(db)
    day = request.args.get('date') or today_str()
    k = queries.day_kpis(year, day)
    fin_allowed = bool({'receivables', 'cash'} & g.erp_scopes)
    season_net = scalar('''SELECT COALESCE(SUM(w.net_kg),0) FROM weighings w JOIN trailer_loads tl ON tl.id=w.load_id
                           WHERE tl.season_year=? AND w.status='YAKUNLANDI' ''', (year,))
    out = {
        'season': year, 'date': day,
        'day': {'field_kg': k['harvest'], 'field_hand_kg': k['hand'], 'field_combine_kg': k['combine'],
                'workers': k['workers'], 'net_kg': k['net'], 'shipped_kg': k['sent'], 'accepted_kg': k['accepted'],
                'acceptance_diff_kg': k['diff'], 'in_transit_waybills': k['in_transit'], 'in_transit_kg': k['in_transit_kg'],
                'trailers_busy': k['trailers_busy'], 'trailers_total': k['trailers_total'], 'trailers_full_waiting_scale': k['trailers_full']},
        'season_totals': {'net_kg': season_net},
    }
    if fin_allowed:
        out['finance'] = _finance_block(year)
    return envelope(out)


def _finance_block(year):
    fin = queries.finance_summary(year)
    has_opening = bool(scalar("SELECT COUNT(*) FROM cash_entries WHERE season_year=? AND category='opening' AND voided_at IS NULL",
                              (year,)))
    block = {}
    if 'receivables' in g.erp_scopes:
        pending = scalar("SELECT COUNT(*) FROM waybills WHERE season_year=? AND status='YARATILDI'", (year,))
        if fin['unpriced'] or not fin['receivable']:
            block['nayman_debt'] = {'amount': None, 'status': 'not_calculated',
                                    'reason': f'{fin["unpriced"]} ta Nayman qabulida narx kiritilmagan' if fin['unpriced']
                                    else 'Narxli Nayman qabuli hali yo‘q',
                                    'priced_amount': fin['receivable'] or 0, 'received': fin['received']}
        else:
            block['nayman_debt'] = {'amount': fin['debt'], 'status': 'calculated', 'accrued': fin['receivable'],
                                    'received': fin['received']}
        block['nayman_debt']['waybills_awaiting_acceptance'] = pending
    if 'cash' in g.erp_scopes:
        if has_opening:
            block['cash_balance'] = {'amount': fin['cash_balance'], 'status': 'calculated', 'opening': fin['cash_opening'],
                                     'inflow': fin['cash_in'], 'outflow': fin['cash_out']}
        else:
            block['cash_balance'] = {'amount': None, 'status': 'not_calculated',
                                     'reason': 'Mavsum uchun boshlang‘ich kassa qoldig‘i kiritilmagan',
                                     'inflow_recorded': fin['cash_in'], 'outflow_recorded': fin['cash_out']}
    if 'expenses' in g.erp_scopes:
        block['expenses_total'] = {'amount': fin['expenses'], 'status': 'calculated'}
    return block


@bp.get(API + '/fields')
@erp_endpoint('reference')
def api_fields():
    year = int(request.args['season']) if request.args.get('season', '').isdigit() else current_season(get_db())
    rows = [dict(r) for r in queries.field_yields(year)]
    for r in rows:
        r['polygon'] = json.loads(r.pop('polygon_json')) if r.get('polygon_json') else None
        r['yield_kg_per_ha'] = round(r['net_kg'] / r['area_ha']) if r['area_ha'] else None
    return envelope(rows, extra={'season': year, 'note': 'net_kg — tarozi netto; internal_kg — dalada tortilgan.'})


@bp.get(API + '/brigadiers')
@erp_endpoint('reference')
def api_brigadiers():
    year = int(request.args['season']) if request.args.get('season', '').isdigit() else current_season(get_db())
    return envelope([dict(r) for r in queries.brigadier_results(year)], extra={'season': year})


@bp.get(API + '/equipment')
@erp_endpoint('reference')
def api_equipment():
    return envelope([dict(r) for r in q('SELECT id, kind, code, plate, ownership, active FROM equipment ORDER BY kind, code')])


def _status_fields(row, voided_col='voided_at'):
    row['is_voided'] = bool(row.get(voided_col))
    return row


@bp.get(API + '/harvests')
@erp_endpoint('harvests')
def api_harvests():
    name = 'w.full_name' if 'workers' in g.erp_scopes else 'NULL'
    sel = f'''SELECT h.id, h.season_year, h.work_date, h.load_id, h.method, h.kg, h.worker_id, {name} AS worker_name,
                     h.field_id, f.code field_code, h.brigadier_id, b.name brigadier_name, t.code trailer_code,
                     c.code combine_code, h.rate, h.rate_unit, h.amount, h.source, h.created_at, h.voided_at, h.void_reason,
                     {upd('h.created_at', 'h.voided_at')} AS updated_at
              FROM harvests h LEFT JOIN workers w ON w.id=h.worker_id LEFT JOIN fields f ON f.id=h.field_id
              LEFT JOIN brigadiers b ON b.id=h.brigadier_id LEFT JOIN equipment t ON t.id=h.trailer_id
              LEFT JOIN equipment c ON c.id=h.combine_id'''
    f = Filters('work_date', 'updated_at', 'field_id', 'season_year')
    rows, total, mx = f.run(sel, 'id')
    return envelope([_status_fields(r) for r in rows], f, total, mx,
                    extra={'measure': 'field_kg (dalada tortilgan ichki kg — yakuniy og‘irlik emas)',
                           'worker_names': 'workers' in g.erp_scopes})


@bp.get(API + '/loads')
@erp_endpoint('loads')
def api_loads():
    sel = f'''SELECT tl.id, tl.season_year, tl.load_date, tl.status, t.code trailer_code, tr.code tractor_code,
                     tl.field_id, f.code field_code, f.name field_name, b.name brigadier_name,
                     tl.hand_kg, tl.combine_kg, tl.internal_kg, tl.opened_at, tl.full_at, tl.weighed_at,
                     w.net_kg, wb.number waybill_number, tl.voided_at, tl.void_reason,
                     {upd('tl.opened_at', 'tl.full_at', 'tl.weighed_at', 'tl.voided_at', 'w.updated_at')} AS updated_at
              FROM trailer_loads tl JOIN equipment t ON t.id=tl.trailer_id LEFT JOIN equipment tr ON tr.id=tl.tractor_id
              LEFT JOIN fields f ON f.id=tl.field_id LEFT JOIN brigadiers b ON b.id=tl.brigadier_id
              LEFT JOIN weighings w ON w.load_id=tl.id LEFT JOIN waybills wb ON wb.load_id=tl.id'''
    f = Filters('load_date', 'updated_at', 'field_id', 'season_year')
    rows, total, mx = f.run(sel, 'id')
    for r in rows:
        r['internal_kg_status'] = 'final_snapshot' if r['status'] in ('TOLDI', 'TORTILDI') else 'open_not_final'
    return envelope(rows, f, total, mx, extra={'statuses': {'OCHIQ': 'terim davom etmoqda', 'TOLDI': 'to‘ldi, tarozini kutmoqda',
                                                            'TORTILDI': 'tortilgan', 'BEKOR': 'bekor qilingan'}})


@bp.get(API + '/weighings')
@erp_endpoint('weighings')
def api_weighings():
    sel = f'''SELECT w.id, w.load_id, tl.season_year, tl.field_id, t.code trailer_code, w.gross_kg, w.gross_at, w.tare_kg,
                     w.tare_at, w.net_kg, w.internal_kg, w.diff_kg, w.diff_reason, w.status,
                     {upd('w.created_at', 'w.tare_at', 'w.updated_at')} AS updated_at
              FROM weighings w JOIN trailer_loads tl ON tl.id=w.load_id JOIN equipment t ON t.id=tl.trailer_id'''
    f = Filters('gross_at', 'updated_at', 'field_id', 'season_year')
    rows, total, mx = f.run(sel, 'id')
    return envelope(rows, f, total, mx, extra={'measure': 'net_kg — yakuniy og‘irlik; status=BRUTTO bo‘lsa netto hali yo‘q'})


@bp.get(API + '/waybills')
@erp_endpoint('waybills')
def api_waybills():
    sel = f'''SELECT wb.id, wb.number, wb.season_year, wb.document_date, wb.status, wb.net_kg, wb.destination,
                     tl.field_id, f.code field_code, b.name brigadier_name, t.code trailer_code, wb.created_at,
                     wb.voided_at, wb.void_reason, {upd('wb.created_at', 'wb.updated_at', 'wb.voided_at')} AS updated_at
              FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id JOIN equipment t ON t.id=tl.trailer_id
              LEFT JOIN fields f ON f.id=tl.field_id LEFT JOIN brigadiers b ON b.id=tl.brigadier_id'''
    f = Filters('document_date', 'updated_at', 'field_id', 'season_year')
    rows, total, mx = f.run(sel, 'number')
    return envelope(rows, f, total, mx, extra={'statuses': {'YARATILDI': 'jo‘natilgan, Nayman qabuli kutilmoqda (kamomad emas)',
                                                            'QABUL': 'Nayman qabul qilgan', 'BEKOR': 'bekor qilingan'}})


@bp.get(API + '/nayman-receipts')
@erp_endpoint('nayman')
def api_nayman():
    sel = f'''SELECT nr.id, nr.waybill_id, wb.number waybill_number, wb.season_year, tl.field_id, wb.net_kg shipped_kg,
                     nr.accepted_kg, nr.diff_kg, nr.diff_reason, nr.received_date, nr.created_at,
                     {upd('nr.created_at', 'nr.updated_at')} AS updated_at
              FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id JOIN trailer_loads tl ON tl.id=wb.load_id'''
    f = Filters('received_date', 'updated_at', 'field_id', 'season_year')
    rows, total, mx = f.run(sel, 'id')
    return envelope(rows, f, total, mx)


@bp.get(API + '/receivables')
@erp_endpoint('receivables')
def api_receivables():
    sel = f'''SELECT wb.id waybill_id, wb.number waybill_number, wb.season_year, tl.field_id, wb.document_date, wb.status,
                     wb.net_kg shipped_kg, nr.accepted_kg, nr.received_date, nr.price_per_kg, nr.amount accrued,
                     (SELECT COALESCE(SUM(amount),0) FROM payments p WHERE p.waybill_id=wb.id AND p.voided_at IS NULL) paid,
                     {upd('wb.created_at', 'wb.updated_at', 'nr.updated_at', 'nr.created_at',
                          "(SELECT MAX(COALESCE(p.voided_at, p.created_at)) FROM payments p WHERE p.waybill_id=wb.id)")} AS updated_at
              FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
              WHERE wb.status<>'BEKOR' '''
    f = Filters('document_date', 'updated_at', 'field_id', 'season_year')
    rows, total, mx = f.run(sel, 'waybill_number')
    for r in rows:
        if r['accepted_kg'] is None:
            r['balance'], r['balance_status'] = None, 'awaiting_acceptance'
        elif r['accrued'] is None:
            r['balance'], r['balance_status'] = None, 'price_not_set'
        else:
            r['balance'], r['balance_status'] = r['accrued'] - r['paid'], 'calculated'
    unassigned = scalar('SELECT COALESCE(SUM(amount),0) FROM payments WHERE season_year=? AND waybill_id IS NULL AND voided_at IS NULL',
                        (f.season,))
    return envelope(rows, f, total, mx, extra={
        'balance_statuses': {'calculated': 'accrued − paid', 'price_not_set': 'narx kiritilmagan — qarz hisoblanmagan',
                             'awaiting_acceptance': 'Nayman hali qabul qilmagan'},
        'unassigned_payments': unassigned,
        'note': 'Nakladnoyga bog‘lanmagan to‘lovlar (unassigned_payments) qarzni kamaytiradi, lekin alohida ko‘rsatiladi.'})


@bp.get(API + '/payments')
@erp_endpoint('payments')
def api_payments():
    sel = f'''SELECT p.id, p.season_year, p.payment_date, p.amount, p.method, p.payer, p.waybill_id, wb.number waybill_number,
                     p.note, p.created_at, p.voided_at, p.void_reason, {upd('p.created_at', 'p.voided_at')} AS updated_at
              FROM payments p LEFT JOIN waybills wb ON wb.id=p.waybill_id'''
    f = Filters('payment_date', 'updated_at', None, 'season_year')
    rows, total, mx = f.run(sel, 'id')
    return envelope([_status_fields(r) for r in rows], f, total, mx)


@bp.get(API + '/expenses')
@erp_endpoint('expenses')
def api_expenses():
    sel = f'''SELECT e.id, e.doc_no, e.status, e.season_year, e.expense_date, e.category, e.amount, e.field_id, f.code field_code,
                     eq.code equipment_code, e.payer, e.note, CASE WHEN e.cash_entry_id IS NOT NULL THEN 1 ELSE 0 END from_cash,
                     e.created_at, e.voided_at, e.void_reason, {upd('e.created_at', 'e.voided_at')} AS updated_at
              FROM expenses e LEFT JOIN fields f ON f.id=e.field_id LEFT JOIN equipment eq ON eq.id=e.equipment_id'''
    f = Filters('expense_date', 'updated_at', 'field_id', 'season_year')
    rows, total, mx = f.run(sel, 'id')
    return envelope([_status_fields(r) for r in rows], f, total, mx)


@bp.get(API + '/cash-entries')
@erp_endpoint('cash')
def api_cash():
    name = 'w.full_name' if 'workers' in g.erp_scopes else 'NULL'
    sel = f'''SELECT c.id, c.doc_no, c.cashbox_id, cb.name cashbox_name, c.season_year, c.entry_date, c.direction, c.category,
                     c.amount, c.worker_id, {name} AS worker_name, c.combine_id, c.source,
                     c.counterparty, c.note, c.expense_id, c.payment_id, c.payout_id, c.debt_id, c.created_at, c.voided_at,
                     c.void_reason,
                     {upd('c.created_at', 'c.voided_at')} AS updated_at
              FROM cash_entries c LEFT JOIN workers w ON w.id=c.worker_id LEFT JOIN cashboxes cb ON cb.id=c.cashbox_id'''
    f = Filters('entry_date', 'updated_at', None, 'season_year')
    rows, total, mx = f.run(sel, 'id')
    return envelope([_status_fields(r) for r in rows], f, total, mx)


@bp.get(API + '/cash-balance')
@erp_endpoint('cash')
def api_cash_balance():
    year = int(request.args['season']) if request.args.get('season', '').isdigit() else current_season(get_db())
    return envelope(_finance_block(year).get('cash_balance'), extra={'season': year})


@bp.get(API + '/cash-days')
@erp_endpoint('cash')
def api_cash_days():
    sel = '''SELECT cd.id, cd.day, cd.cashbox_id, cb.name cashbox_name, cd.opening, cd.inflow, cd.outflow, cd.system_balance,
                    cd.counted, cd.diff, cd.reason, cd.closed_at, cd.closed_at AS updated_at, CAST(substr(cd.day,1,4) AS INTEGER) season_year
             FROM cash_days cd JOIN cashboxes cb ON cb.id=cd.cashbox_id'''
    f = Filters('day', 'updated_at', None, 'season_year')
    rows, total, mx = f.run(sel, 'id')
    return envelope(rows, f, total, mx, extra={'note': 'diff = counted − system_balance; farq kassa kitobiga ADJ yozuvi bo‘lib tushgan.'})


@bp.get(API + '/payouts')
@erp_endpoint('payouts')
def api_payouts():
    name = 'w.full_name' if 'workers' in g.erp_scopes else 'NULL'
    sel = f'''SELECT p.id, p.doc_no, p.season_year, p.kind, p.purpose, p.worker_id, {name} AS worker_name, p.combine_id,
                     e.code combine_code, p.amount, p.status, p.prepared_at, p.paid_at, p.cashbox_id, p.voided_at, p.void_reason,
                     substr(p.prepared_at,1,10) prepared_date, {upd('p.prepared_at', 'p.paid_at', 'p.voided_at')} AS updated_at
              FROM payouts p LEFT JOIN workers w ON w.id=p.worker_id LEFT JOIN equipment e ON e.id=p.combine_id'''
    f = Filters('prepared_date', 'updated_at', None, 'season_year')
    rows, total, mx = f.run(sel, 'id')
    return envelope(rows, f, total, mx, extra={'statuses': {'TAYYOR': 'kassirda kutilmoqda', 'BERILDI': 'naqd berilgan',
                                                             'BEKOR': 'berilmasdan bekor qilingan'}})


@bp.get(API + '/worker-balances')
@erp_endpoint('payouts')
def api_worker_balances():
    from ..accounting import worker_balances
    year = int(request.args['season']) if request.args.get('season', '').isdigit() else current_season(get_db())
    show_names = 'workers' in g.erp_scopes
    rows = [{'worker_id': r['id'], 'worker_name': r['full_name'] if show_names else None, 'kg': r['kg'], 'earned': r['earned'],
             'uncalculated_kg': r['uncalc_kg'], 'advances': r['advances'], 'paid': r['paid'], 'pending_order': r['pending'],
             'balance': r['balance'], 'status': r['status']} for r in worker_balances(year)]
    return envelope(rows, extra={'season': year, 'note': 'earned — har tortish o‘z vaqtidagi narxda (qayta hisoblanmaydi); '
                                                        'uncalculated_kg — narx kiritilmagan paytdagi kg (0 deb hisoblanmagan).'})


@bp.get(API + '/combines')
@erp_endpoint('payouts')
def api_combines():
    from ..accounting import combine_balances
    year = int(request.args['season']) if request.args.get('season', '').isdigit() else current_season(get_db())
    keys = ('id', 'code', 'operator_name', 'tariff_type', 'tariff_rate', 'kg', 'work_days', 'hectares', 'earned', 'paid',
            'pending', 'balance', 'uncalc_kg')
    return envelope([{k: c[k] for k in keys} for c in combine_balances(year)], extra={'season': year})


@bp.get(API + '/debts')
@erp_endpoint('debts')
def api_debts():
    from ..accounting import debts
    year = int(request.args['season']) if request.args.get('season', '').isdigit() else current_season(get_db())
    return envelope(debts(year), extra={'season': year, 'directions': {'OLISH': 'biz olishimiz kerak',
                                                                         'BERISH': 'biz berishimiz kerak'}})


@bp.route(API + '/<path:rest>', methods=['POST', 'PUT', 'PATCH', 'DELETE'])
def api_write_refused(rest):
    return _err(405, 'read_only', 'Bu API faqat o‘qish uchun. Ma’lumot kiritish, o‘zgartirish va o‘chirish mumkin emas.')


# =================================================================== TV wallboard

def _tv_allowed():
    if g.get('user'):
        return True, None
    if not get_bool('tv_enabled'):
        return False, None
    key = request.args.get('k') or request.cookies.get('surxon_tv') or ''
    c = _client_from_key(key, 'tv')
    return (c is not None), (c, key)


@bp.get('/tv')
def tv():
    ok, info = _tv_allowed()
    if not ok:
        return render_template('tv_denied.html'), 403
    gkey = current_app.config['SURXON'].GOOGLE_MAPS_KEY or (get_setting('google_maps_key') or '').strip()
    resp = make_response(render_template('tv.html', gmaps_key=gkey))
    if info and request.args.get('k'):
        # keep the key in an httpOnly cookie so it disappears from the address bar after first open
        resp.set_cookie('surxon_tv', info[1], max_age=3600 * 24 * 365, httponly=True, samesite='Lax',
                        secure=current_app.config['SURXON'].COOKIE_SECURE)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@bp.get('/tv/data.json')
def tv_data():
    started = time.time()
    ok, info = _tv_allowed()
    if not ok:
        return _err(403, 'tv_denied', 'TV kaliti noto‘g‘ri yoki TV ulanishi o‘chirilgan.', started=started)
    from ..tvboard import build
    data = build(current_season(get_db()))
    if info and info[0]:
        _log(info[0]['id'], 200, started=started)
    resp = jsonify(data)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@bp.get('/tv/foto/<int:photo_id>')
def tv_photo(photo_id):
    """Thumbnails of field / trailer photos only (never cash, fuel or document photos)."""
    ok, _ = _tv_allowed()
    if not ok:
        abort(403)
    p = q("SELECT * FROM photos WHERE id=? AND voided_at IS NULL AND category IN ('trailer','field')", (photo_id,), one=True)
    if not p:
        abort(404)
    from flask import send_from_directory
    resp = make_response(send_from_directory(current_app.config['SURXON'].UPLOAD_DIR, p['thumb_path'] or p['path'], max_age=3600))
    resp.headers['Cache-Control'] = 'private, max-age=3600'
    return resp
