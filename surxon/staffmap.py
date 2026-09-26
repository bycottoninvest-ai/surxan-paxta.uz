"""Where staff are: Telegram live location sent to the bot, or the phone's position while a field page is open.

Nothing is hidden: a person shares the live location in Telegram themselves (“until I turn it off”) and can stop it any
time. Only admin and the director see the map. Positions are kept at most once a minute (or after moving 30 m) per
person; the map shows the last one (grey when older than STALE_MIN) and today's track on demand.
"""
import io
import math
from datetime import datetime, timedelta
from pathlib import Path

from flask import current_app

from .db import get_db, q, tx
from .utils import now_str

STALE_MIN = 30
KEEP_SEC, KEEP_M = 60, 30


def _m(a, b):
    k = math.cos(math.radians(a[0]))
    return math.hypot((a[0] - b[0]) * 110540, (a[1] - b[1]) * 111320 * k)


def record(*, lat, lon, acc=None, user_id=None, member_id=None, source='ilova'):
    """Store a position unless the same person was recorded < KEEP_SEC ago within KEEP_M metres. Returns True if stored."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return False
    with tx() as db:
        if user_id:
            last = db.execute('SELECT * FROM staff_positions WHERE user_id=? ORDER BY id DESC LIMIT 1', (user_id,)).fetchone()
        else:
            last = db.execute('SELECT * FROM staff_positions WHERE member_id=? ORDER BY id DESC LIMIT 1', (member_id,)).fetchone()
        now = now_str()
        if last:
            age = (datetime.strptime(now, '%Y-%m-%d %H:%M:%S') - datetime.strptime(last['at'], '%Y-%m-%d %H:%M:%S')).total_seconds()
            if age < KEEP_SEC and _m((lat, lon), (last['lat'], last['lon'])) < KEEP_M:
                return False
        db.execute('INSERT INTO staff_positions(user_id, member_id, lat, lon, acc, source, at) VALUES (?,?,?,?,?,?,?)',
                   (user_id, member_id, round(lat, 7), round(lon, 7), acc, source, now))
    return True


def fetch_avatar(member, telegram_id):
    """Telegram profile photo → a small JPEG in UPLOAD_DIR/avatars (once; errors are ignored)."""
    if member and member['avatar_path']:
        return
    try:
        from .telegram_bot import tg_api, tg_download
        res = tg_api('getUserProfilePhotos', {'user_id': int(telegram_id), 'limit': 1}).get('result') or {}
        photos = res.get('photos') or []
        if not photos:
            return
        data = tg_download(photos[0][-1]['file_id'])
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert('RGB')
        im.thumbnail((160, 160))
        rel = f'avatars/tg_{telegram_id}.jpg'
        path = Path(current_app.config['SURXON'].UPLOAD_DIR) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        im.save(path, 'JPEG', quality=85)
        with tx() as db:
            db.execute('UPDATE tg_members SET avatar_path=? WHERE telegram_id=?', (rel, str(telegram_id)))
            db.execute('UPDATE users SET avatar_path=COALESCE(avatar_path, ?) WHERE telegram_id=?', (rel, str(telegram_id)))
    except Exception as exc:          # no photo / privacy settings / network — the initial letter is shown instead
        current_app.logger.info('avatar not fetched for %s: %s', telegram_id, exc)


def people(hours=12):
    """Latest position of everyone seen in the last `hours`, with name, role and avatar."""
    from .security import ROLES
    since = (datetime.strptime(now_str(), '%Y-%m-%d %H:%M:%S') - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')
    now = datetime.strptime(now_str(), '%Y-%m-%d %H:%M:%S')
    rows = q('''SELECT p.* FROM staff_positions p JOIN (
                    SELECT COALESCE('u' || user_id, 'm' || member_id) k, MAX(id) mid FROM staff_positions
                    WHERE at>=? GROUP BY k) x ON x.mid=p.id''', (since,))
    out = []
    for r in rows:
        if r['user_id']:
            u = q('SELECT id, full_name, role, avatar_path FROM users WHERE id=?', (r['user_id'],), one=True)
            if not u:
                continue
            name, role, av, key = u['full_name'], ROLES.get(u['role'], u['role']), u['avatar_path'], f'u{u["id"]}'
            if not av:
                m = q('SELECT avatar_path FROM tg_members WHERE user_id=?', (u['id'],), one=True)
                av = m['avatar_path'] if m else None
        else:
            m = q('SELECT id, full_name, role_label, avatar_path FROM tg_members WHERE id=?', (r['member_id'],), one=True)
            if not m:
                continue
            name, role, av, key = m['full_name'], m['role_label'] or 'Telegram', m['avatar_path'], f'm{m["id"]}'
        age = int((now - datetime.strptime(r['at'], '%Y-%m-%d %H:%M:%S')).total_seconds() // 60)
        out.append({'key': key, 'name': name, 'role': role, 'avatar': av, 'lat': r['lat'], 'lon': r['lon'],
                    'acc': r['acc'], 'source': r['source'], 'at': r['at'][11:16], 'age_min': age, 'stale': age > STALE_MIN,
                    'field': _field_at(r['lat'], r['lon'])})
    return sorted(out, key=lambda x: (x['stale'], x['name']))


_fields_cache = {}


def _field_at(lat, lon):
    import json
    from . import geo
    stamp = q('SELECT COUNT(*) n, MAX(id) m FROM fields WHERE polygon_json IS NOT NULL', one=True)
    key = (stamp['n'], stamp['m'])
    if _fields_cache.get('key') != key:
        _fields_cache.update(key=key, rows=[(r['code'], json.loads(r['polygon_json'])) for r in
                                            q('SELECT code, polygon_json FROM fields WHERE active=1 AND polygon_json IS NOT NULL')])
    for code, poly in _fields_cache['rows']:
        pts = poly
        c = False
        for i in range(len(pts)):
            (yi, xi), (yj, xj) = pts[i], pts[i - 1]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                c = not c
        if c:
            return code
    return None


def track(key, day=None):
    """Today's positions of one person (key 'u12' / 'm5'), in time order."""
    day = day or now_str()[:10]
    col = 'user_id' if key.startswith('u') else 'member_id'
    try:
        pid = int(key[1:])
    except ValueError:
        return []
    return [[r['lat'], r['lon'], r['at'][11:16]] for r in
            q(f'SELECT lat, lon, at FROM staff_positions WHERE {col}=? AND substr(at,1,10)=? ORDER BY id LIMIT 5000', (pid, day))]
