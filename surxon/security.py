"""Authentication, role permissions, CSRF, login throttling and the audit trail."""
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from functools import wraps

from flask import abort, flash, g, jsonify, redirect, request, session, url_for

from .db import get_db, q
from .utils import now_str, today_str

ROLES = {
    'admin': 'Admin',
    'manager': 'Rahbar',
    'brigadier': 'Brigadir',
    'scale': 'Tarozi xodimi',
    'accountant': 'Buxgalter',
    'cashier': 'Kassir',
    'tally': 'Hisobchi (terim)',
    'driver': 'Haydovchi',
    'station': 'Punkt operatori',
    'fuel': 'Yoqilg‘i mas’uli',
}

# Punkt operators get a closed, minimal app: only these endpoints (checked on every request).
STATION_ENDPOINTS = {'static', 'auth.login', 'auth.logout', 'auth.change_password', 'main.health', 'main.verify_receipt', 'main.manifest',
                     'main.service_worker', 'main.offline', 'main.dashboard', 'main.trailer_qr',
                     'apps.install', 'apps.open_app', 'apps.manifest', 'apps.icon'}
# trip photos on the punkt cards (checked per photo in main.media: only trips sent to that punkt)
STATION_EXTRA = {'main.media'}

# permission -> roles that hold it. Admin implicitly holds every permission.
PERMISSIONS = {
    'dashboard': set(ROLES) - {'station'},
    'station.view': {'manager', 'station', 'accountant'},
    'station.receive': {'manager', 'station'},
    'harvest.write': {'manager', 'brigadier', 'tally'},
    'load.open': {'manager', 'brigadier', 'tally'},
    'load.full': {'manager', 'brigadier', 'tally', 'driver'},
    'load.reopen': {'manager'},
    'workers.write': {'manager', 'brigadier', 'tally', 'scale'},
    'weigh.write': {'manager', 'scale'},
    'weigh.correct': {'manager'},
    'waybill.view': {'manager', 'brigadier', 'scale', 'accountant', 'cashier', 'driver'},
    'waybill.void': {'manager'},
    'nayman.write': {'manager', 'accountant'},
    'payments.view': {'manager', 'accountant'},
    'payments.write': {'manager', 'accountant'},
    'cash.view': {'manager', 'accountant'},
    'cash.write': {'accountant'},
    'cash.shift': {'manager', 'accountant', 'cashier'},
    'expenses.write': {'manager', 'accountant', 'cashier'},
    'expenses.approve': {'accountant'},
    'settlement.view': {'manager', 'accountant'},
    'acct.view': {'manager', 'accountant'},
    'payouts.view': {'manager', 'accountant', 'cashier'},
    'payouts.prepare': {'accountant'},
    'payouts.pay': {'accountant', 'cashier'},
    'tezpul.panel': {'manager', 'accountant'},      # SURXON TEZ-PUL: the day's talon figures (read)
    'tezpul.manage': {'manager'},                  # print talons, hand packs to brigades, approve big kg, cancel a talon
    'combine.finance': {'accountant'},
    'debts.write': {'accountant'},
    'loans.view': {'manager', 'accountant'},        # credits, leasing, contracts, schedules
    'loans.write': {'manager', 'accountant'},
    'dayclose': {'accountant'},
    'reports.view': {'manager', 'accountant', 'brigadier', 'scale', 'tally'},
    'reports.finance': {'manager', 'accountant'},
    'photos.view': set(ROLES) - {'station'},
    'photos.upload': {'manager', 'brigadier', 'scale', 'accountant', 'cashier', 'tally', 'driver'},
    'photos.void': {'manager'},
    'records.void': {'manager'},
    'kuzatuv.view': {'manager'},
    'kuzatuv.request': {'manager'},
    'kuzatuv.manage': {'manager'},
    'masterdata.write': {'manager'},
    'audit.view': {'manager', 'accountant'},
    'fuel.operate': {'fuel'},                       # take diesel at a station, give it to a machine (own history)
    'fuel.manage': {'accountant'},                  # tickets, price, funding, stations, reconciliation, corrections
    'fuel.view': {'manager', 'accountant'},
    'users.manage': set(),
    'settings.manage': set(),
    'seasons.manage': set(),
    'backup.manage': set(),
}


# O‘rinbosar: while a colleague is away the admin lends a field clerk the cashier's role (or a cashier the clerk's) for
# a few days. Only this pair; it ends by itself after the last day.
STANDIN_PAIRS = {'tally': 'cashier', 'cashier': 'tally'}
STANDIN_MAX_DAYS = 7


def standin(user):
    """The role this person also holds today as a stand-in, or None."""
    if not user:
        return None
    keys = user.keys() if hasattr(user, 'keys') else user
    if 'standin_role' not in keys or not user['standin_role'] or STANDIN_PAIRS.get(user['role']) != user['standin_role']:
        return None
    return user['standin_role'] if (user['standin_until'] or '') >= today_str() else None


def acts_as(user, role):
    return bool(user) and (user['role'] == role or standin(user) == role)


def has_perm(user, perm):
    if not user:
        return False
    if user['role'] == 'admin':
        return True
    roles = PERMISSIONS.get(perm, set())
    return user['role'] in roles or standin(user) in roles


def can(perm):
    return has_perm(g.get('user'), perm)


def brigadier_scope(user=None):
    """Brigadier accounts only see and write their own brigade's work."""
    user = user or g.get('user')
    if user and user['role'] == 'brigadier' and user['brigadier_id']:
        return user['brigadier_id']
    return None


@dataclass
class Actor:
    user_id: int | None
    role: str | None
    brigadier_id: int | None = None
    source: str = 'web'
    ip: str | None = None
    name: str = ''
    station_id: int | None = None       # punkt operator: the only receiving point they may act on
    standin_role: str | None = None     # o‘rinbosar: the second role held today (see standin())
    standin_until: str | None = None

    @classmethod
    def from_user(cls, user, source='web', ip=None):
        keys = user.keys() if hasattr(user, 'keys') else user
        return cls(user['id'], user['role'],
                   user['brigadier_id'] if user['role'] == 'brigadier' else None,
                   source, ip, user['full_name'],
                   user['station_id'] if user['role'] == 'station' and 'station_id' in keys else None,
                   user['standin_role'] if 'standin_role' in keys else None,
                   user['standin_until'] if 'standin_until' in keys else None)

    def _as_user(self):
        return {'role': self.role, 'standin_role': self.standin_role, 'standin_until': self.standin_until}

    def can(self, perm):
        return has_perm(self._as_user(), perm)

    def acts_as(self, role):
        return acts_as(self._as_user(), role)


def current_actor():
    user = g.get('user')
    return Actor.from_user(user, 'web', request.headers.get('X-Forwarded-For', request.remote_addr)) if user else None


def audit(db, actor, action, entity_type, entity_id, old=None, new=None, reason=None):
    db.execute(
        'INSERT INTO audit_logs(user_id, action, entity_type, entity_id, old_json, new_json, reason, source, ip, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?)',
        (actor.user_id if actor else None, action, entity_type, None if entity_id is None else str(entity_id),
         json.dumps(old, ensure_ascii=False, default=str) if old is not None else None,
         json.dumps(new, ensure_ascii=False, default=str) if new is not None else None,
         reason, actor.source if actor else 'system', actor.ip if actor else None, now_str()))


# ---------------------------------------------------------------- CSRF

def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']


def validate_csrf():
    sent = request.form.get('_csrf') or request.headers.get('X-CSRF-Token') or ''
    expected = session.get('csrf') or ''
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, description='Sahifa eskirgan (CSRF). Sahifani yangilab, qayta urinib ko‘ring.')


# ---------------------------------------------------------------- login throttling

MAX_ATTEMPTS = 8
WINDOW_SECONDS = 15 * 60


def throttle_key(username):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr) or '-'
    return f'{ip.split(",")[0].strip()}|{(username or "").lower()}'


def login_blocked(key):
    db = get_db()
    cutoff = time.time() - WINDOW_SECONDS
    db.execute('DELETE FROM login_attempts WHERE at < ?', (cutoff,))
    n = db.execute('SELECT COUNT(*) FROM login_attempts WHERE key=? AND at>=?', (key, cutoff)).fetchone()[0]
    return n >= MAX_ATTEMPTS


def record_failed_login(key):
    get_db().execute('INSERT INTO login_attempts(key, at) VALUES (?,?)', (key, time.time()))


def clear_failed_logins(key):
    get_db().execute('DELETE FROM login_attempts WHERE key=?', (key,))


# ---------------------------------------------------------------- decorators

def wants_json():
    return request.headers.get('X-Requested-With') == 'fetch' or request.accept_mimetypes.best == 'application/json'


def load_user():
    g.user = None
    uid = session.get('user_id')
    if uid:
        user = q('SELECT * FROM users WHERE id=? AND active=1', (uid,), one=True)
        if user and session.get('pw') == user['password_hash'][-16:]:
            g.user = user
        else:
            session.clear()
    return station_gate()


# Cashier: only prepared payments, their own cash box shift and quick expenses.
CASHIER_ENDPOINTS = STATION_ENDPOINTS | {'acct.cashier', 'acct.payout_pay', 'acct.payouts', 'acct.expense_pick',
                                         'acct.expense_new', 'main.media'}


# SURXON TEZ-PUL: the cashier's two-step screen
TEZPUL_CASHIER = {'tezpul.home', 'tezpul.kassa', 'tezpul.lookup', 'tezpul.pay'}
TEZPUL_FIELD = {'tezpul.dala', 'tezpul.dala_lookup', 'tezpul.dala_save'}


# Field clerk: only the four-step field screens (and the few trip endpoints they use) — no reports, money, staff,
# equipment/field admin or the general photo archive, whatever URL is typed.
TALLY_ENDPOINTS = STATION_ENDPOINTS | {'main.api_workers', 'main.api_position', 'main.media', 'ops.harvest', 'ops.load_open', 'ops.load_full',
                                       'ops.load_detail', 'ops.harvest_void', 'ops.load_photos', 'ops.waybill_external_pdf'}


# Fuel keeper: only the fuel screens (take / give / own history).
FUEL_ENDPOINTS = STATION_ENDPOINTS | {'main.media', 'main.api_position'}


def _gate_open(role, ep):
    if role == 'tally':
        return ep in TALLY_ENDPOINTS or ep.startswith('dala.') or ep in TEZPUL_FIELD
    if role == 'station':
        return ep in STATION_ENDPOINTS or ep in STATION_EXTRA or ep.startswith('punkt.')
    if role == 'cashier':
        return ep in CASHIER_ENDPOINTS or ep.startswith('hamyon.') or ep in TEZPUL_CASHIER
    if role == 'fuel':
        return ep in FUEL_ENDPOINTS or ep.startswith('yoqilgi.op_') or ep == 'yoqilgi.home'
    return False


def station_gate():
    """Punkt operator and cashier logins open only their own screens — enforced here for every request, not just
    hidden menus. A stand-in also opens the screens of the role they hold today."""
    user = g.get('user')
    ep = request.endpoint or ''
    if not user or user['role'] not in ('station', 'cashier', 'tally', 'fuel'):
        return None
    if _gate_open(user['role'], ep) or _gate_open(standin(user), ep):
        return None
    if wants_json():
        return jsonify(ok=False, error='Bu amal uchun huquqingiz yo‘q.'), 403
    return redirect(url_for({'station': 'punkt.home', 'cashier': 'hamyon.home', 'tally': 'dala.home',
                             'fuel': 'yoqilgi.home'}[user['role']]))


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not g.get('user'):
            if wants_json():
                return jsonify(ok=False, error='Sessiya tugagan. Qayta kiring.', login=True), 401
            return redirect(url_for('auth.login', next=request.full_path if request.query_string else request.path))
        if g.user['must_change_password'] and request.endpoint not in ('auth.change_password', 'auth.logout'):
            flash('Xavfsizlik uchun avval parolingizni almashtiring.', 'error')
            return redirect(url_for('auth.change_password'))
        return fn(*args, **kwargs)
    return wrapper


def perm_required(*perms):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if not any(can(p) for p in perms):
                if wants_json():
                    return jsonify(ok=False, error='Bu amal uchun huquqingiz yo‘q.'), 403
                flash('Bu bo‘lim uchun huquqingiz yetarli emas.', 'error')
                return redirect(url_for('main.dashboard'))
            return fn(*args, **kwargs)
        return wrapper
    return deco


def require(perm):
    """Inline permission check for POST branches of mixed GET/POST views."""
    if not can(perm):
        abort(403)
