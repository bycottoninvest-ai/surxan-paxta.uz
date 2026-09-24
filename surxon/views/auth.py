import secrets

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..db import get_db, q, tx
from ..security import (Actor, audit, clear_failed_logins, current_actor, login_blocked, login_required,
                        record_failed_login, throttle_key, validate_csrf)
from ..services import change_password as svc_change_password
from ..utils import UserError, now_str

bp = Blueprint('auth', __name__)

# Burn the same CPU on unknown usernames so response time doesn't reveal which logins exist.
_DUMMY_HASH = generate_password_hash('surxon-dummy-password')


def _safe_next(target):
    if target and target.startswith('/') and not target.startswith('//') and '\\' not in target:
        return target
    return url_for('main.dashboard')


@bp.get('/login')
def login():
    if g.get('user'):
        return redirect(url_for('main.dashboard'))
    return render_template('login.html')


@bp.post('/login')
def do_login():
    validate_csrf()
    username = (request.form.get('username') or '').strip().lower()
    password = request.form.get('password') or ''
    key = throttle_key(username)
    if login_blocked(key):
        flash('Juda ko‘p noto‘g‘ri urinish. 15 daqiqadan keyin qayta urinib ko‘ring.', 'error')
        return redirect(url_for('auth.login'))
    user = q('SELECT * FROM users WHERE username=? AND active=1', (username,), one=True)
    ok = check_password_hash(user['password_hash'] if user else _DUMMY_HASH, password)
    if user and ok:
        clear_failed_logins(key)
        session.clear()
        session.permanent = bool(request.form.get('remember'))
        session['user_id'] = user['id']
        session['pw'] = user['password_hash'][-16:]
        session['csrf'] = secrets.token_urlsafe(32)
        with tx() as db:
            db.execute('UPDATE users SET last_login_at=? WHERE id=?', (now_str(), user['id']))
            audit(db, Actor.from_user(user, ip=request.remote_addr), 'LOGIN', 'user', user['id'])
        return redirect(_safe_next(request.args.get('next')))
    record_failed_login(key)
    flash('Login yoki parol noto‘g‘ri.', 'error')
    return redirect(url_for('auth.login', next=request.args.get('next') or None))


@bp.route('/logout', methods=['GET', 'POST'])
def logout():
    if g.get('user'):
        with tx() as db:
            audit(db, current_actor(), 'LOGOUT', 'user', g.user['id'])
    session.clear()
    resp = redirect(url_for('auth.login', bye=1))
    return resp


@bp.route('/parol', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        validate_csrf()
        current = request.form.get('current') or ''
        new = request.form.get('new') or ''
        if not check_password_hash(g.user['password_hash'], current):
            raise UserError('Joriy parol noto‘g‘ri.')
        if new != (request.form.get('new2') or ''):
            raise UserError('Yangi parollar bir xil emas.')
        if new == current:
            raise UserError('Yangi parol eskisidan farq qilishi kerak.')
        svc_change_password(current_actor(), g.user['id'], new)
        user = q('SELECT password_hash FROM users WHERE id=?', (g.user['id'],), one=True)
        session['pw'] = user['password_hash'][-16:]
        flash('Parol almashtirildi.', 'success')
        return redirect(url_for('main.dashboard'))
    return render_template('change_password.html')
