"""Authentication, role permissions, CSRF, login throttling and the audit trail."""
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from functools import wraps

from flask import abort, flash, g, jsonify, redirect, request, session, url_for

from .db import get_db, q
from .utils import now_str

ROLES = {
    'admin': 'Admin',
    'manager': 'Rahbar',
    'brigadier': 'Brigadir',
    'scale': 'Tarozi xodimi',
    'accountant': 'Buxgalter',
    'cashier': 'Kassa',
    'tally': 'Hisobchi (terim)',
    'driver': 'Haydovchi',
}

# permission -> roles that hold it. Admin implicitly holds every permission.
PERMISSIONS = {
    'dashboard': set(ROLES),
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
    'payments.view': {'manager', 'accountant', 'cashier'},
    'payments.write': {'manager', 'accountant', 'cashier'},
    'cash.view': {'manager', 'accountant', 'cashier'},
    'cash.write': {'accountant', 'cashier'},
    'expenses.write': {'manager', 'accountant', 'cashier'},
    'settlement.view': {'manager', 'accountant', 'cashier', 'tally'},
    'reports.view': {'manager', 'accountant', 'cashier', 'brigadier', 'scale', 'tally'},
    'reports.finance': {'manager', 'accountant', 'cashier'},
    'photos.view': set(ROLES),
    'photos.upload': {'manager', 'brigadier', 'scale', 'accountant', 'cashier', 'tally', 'driver'},
    'photos.void': {'manager'},
    'records.void': {'manager'},
    'masterdata.write': {'manager'},
    'audit.view': {'manager', 'accountant'},
    'users.manage': set(),
    'settings.manage': set(),
    'seasons.manage': set(),
    'backup.manage': set(),
}


def has_perm(user, perm):
    if not user:
        return False
    if user['role'] == 'admin':
        return True
    return user['role'] in PERMISSIONS.get(perm, set())


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

    @classmethod
    def from_user(cls, user, source='web', ip=None):
        return cls(user['id'], user['role'],
                   user['brigadier_id'] if user['role'] == 'brigadier' else None,
                   source, ip, user['full_name'])

    def can(self, perm):
        return has_perm({'role': self.role}, perm)


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
