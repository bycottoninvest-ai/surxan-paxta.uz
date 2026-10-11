"""Separate phone apps for each section (SURXON Terim, Kassir, Buxgalter, Punkt, Rahbar, Admin).

The admin sends a section's link in Telegram (/ilova/kassa); the worker opens it and taps “O‘RNATISH”, and the phone
gets an icon of its own that opens straight on that section's screen. Each app is the same site with its own install
manifest (own id, name, colour and icon), so one phone can hold two of them — e.g. Terim and Kassa for a clerk who
stands in for the cashier. On one phone they share one login.
"""
import io
from functools import lru_cache
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, g, jsonify, redirect, render_template, request, url_for

from ..security import acts_as, has_perm

bp = Blueprint('apps', __name__)

# key -> name, label on the icon, colour, who it is for, what opens
APPS = {
    'terim': {'name': 'SURXON Terim', 'label': 'TERIM', 'color': '#1e9e4a', 'who': 'Terim hisobchisi',
              'opens': 'Telashka QR skaneri va talon skaneri'},
    'kassa': {'name': 'SURXON Kassir', 'label': 'KASSIR', 'color': '#e8730c', 'who': 'Kassir',
              'opens': 'TEZ-PUL kassa: talon skaneri → PUL BERILDI'},
    'buxgalter': {'name': 'SURXON Buxgalter', 'label': 'BUXGALTER', 'color': '#1b6fe0', 'who': 'Buxgalter',
                  'opens': 'Buxgalteriya: kassa, to‘lovlar, xarajat'},
    'punkt': {'name': 'SURXON Punkt', 'label': 'PUNKT', 'color': '#7b3fd1', 'who': 'Punkt (Yunus)',
              'opens': 'Punkt ekrani: keladigan telashkalar'},
    'rahbar': {'name': 'SURXON Rahbar', 'label': 'RAHBAR', 'color': '#0b2a55', 'who': 'Rahbar, direktor',
               'opens': 'Direktor paneli (faqat ko‘rish)'},
    'admin': {'name': 'SURXON Admin', 'label': 'ADMIN', 'color': '#c01b43', 'who': 'Admin',
              'opens': 'Butun tizim: bosh sahifa, sozlamalar, xodimlar'},
}


def suits(user, key):
    """Is this app for this login?"""
    if not user:
        return False
    if key == 'terim':
        return acts_as(user, 'tally') or has_perm(user, 'harvest.write')
    if key == 'kassa':
        return acts_as(user, 'cashier') or has_perm(user, 'payouts.pay')
    if key == 'buxgalter':
        return has_perm(user, 'acct.view')
    if key == 'punkt':
        return has_perm(user, 'station.view')
    if key == 'rahbar':
        return has_perm(user, 'reports.finance')
    if key == 'admin':
        return user['role'] == 'admin'
    return False


def _start(key):
    """Where the app opens for this login (None: this app is not for them)."""
    if not suits(g.user, key):
        return None
    if key == 'terim':
        return url_for('dala.home')
    if key == 'kassa':
        from ..tezpul import in_use
        return url_for('tezpul.kassa' if in_use() else 'hamyon.home')
    return url_for({'buxgalter': 'hamyon.home', 'punkt': 'punkt.home', 'rahbar': 'rahbar.home',
                    'admin': 'main.dashboard'}[key])


def app_for_page():
    """The app whose install manifest this page links, so an installed app keeps its own name and icon."""
    ep = request.endpoint or ''
    if ep.startswith('apps.'):
        return (request.view_args or {}).get('key')
    if ep == 'auth.login':                    # an installed app that opened on the login page keeps its own icon
        nxt = request.args.get('next', '')
        key = nxt.split('/')[2] if nxt.startswith('/ilova/') and nxt.count('/') >= 2 else None
        return key if key in APPS else None
    if ep.startswith('dala.') or ep.startswith('tezpul.dala'):
        return 'terim'
    if ep in ('tezpul.kassa', 'tezpul.home'):
        return 'kassa'
    if ep.startswith('hamyon.'):
        user = g.get('user')
        return 'kassa' if user and acts_as(user, 'cashier') else 'buxgalter'
    if ep.startswith('punkt.'):
        return 'punkt'
    if ep.startswith('rahbar.'):
        return 'rahbar'
    user = g.get('user')
    if user and user['role'] == 'admin' and (ep == 'main.dashboard' or ep.startswith('admin.')):
        return 'admin'
    return None


def _app(key):
    a = APPS.get(key)
    if not a:
        abort(404)
    return a


@bp.get('/ilova/<key>')
def install(key):
    """The page sent in Telegram: one big “O‘RNATISH” button (Android), the steps for iPhone, and a way out of
    Telegram's own browser, which cannot install apps."""
    return render_template('app_install.html', key=key, a=_app(key))


@bp.get('/ilova/<key>/ochish')
def open_app(key):
    """start_url of the installed app."""
    _app(key)
    if not g.get('user'):
        return redirect(url_for('auth.login', next=request.path))
    start = _start(key)
    if not start and g.user['role'] != 'admin':
        # a second login on this phone (e.g. the clerk's Terim next to the cashier's Kassir): sign in once more, the
        # phone then keeps both and every app uses its own
        return redirect(url_for('auth.login', next=request.path, ilova=key))
    return redirect(start or url_for('main.dashboard'))


@bp.get('/ilova/<key>/manifest.webmanifest')
def manifest(key):
    a = _app(key)
    data = {
        'id': f'/ilova/{key}', 'name': a['name'], 'short_name': a['name'].split()[-1],
        'start_url': url_for('apps.open_app', key=key), 'scope': '/', 'display': 'standalone',
        'background_color': '#f3f8fd', 'theme_color': a['color'], 'lang': 'uz',
        'icons': [{'src': url_for('apps.icon', key=key, size=192), 'sizes': '192x192', 'type': 'image/png'},
                  {'src': url_for('apps.icon', key=key, size=512), 'sizes': '512x512', 'type': 'image/png',
                   'purpose': 'any maskable'}],
    }
    resp = jsonify(data)
    resp.mimetype = 'application/manifest+json'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@bp.get('/ilova/<key>/icon-<int:size>.png')
def icon(key, size):
    _app(key)
    if size not in (180, 192, 512):
        abort(404)
    resp = Response(_icon_png(key, size, current_app.static_folder), mimetype='image/png')
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


@lru_cache(maxsize=32)
def _icon_png(key, size, static_folder):
    """The SURXON flower on the app's colour with its name underneath (kept inside the maskable safe zone)."""
    from PIL import Image, ImageDraw, ImageFont
    a = APPS[key]
    S = 512
    img = Image.new('RGB', (S, S), a['color'])
    d = ImageDraw.Draw(img)
    src = Image.open(Path(static_folder) / 'img' / 'icon-512.png').convert('RGB')
    flower = src.crop((46, 46, 466, 466)).resize((250, 250), Image.LANCZOS)
    mask = Image.new('L', (250, 250), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 249, 249), fill=255)
    img.paste(flower, ((S - 250) // 2, 92), mask)
    font_path = Path(__file__).resolve().parent.parent / 'fonts' / 'DejaVuSans-Bold.ttf'
    label = a['label']
    fs = 64
    while fs > 30:
        font = ImageFont.truetype(str(font_path), fs)
        if d.textlength(label, font=font) <= 360:
            break
        fs -= 2
    d.text((S / 2, 400), label, fill='white', font=font, anchor='mm')
    if size != S:
        img = img.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, 'PNG', optimize=True)
    return buf.getvalue()
