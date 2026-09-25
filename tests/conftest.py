import io
import re
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from surxon import create_app  # noqa: E402
from surxon.db import get_db, q  # noqa: E402

ADMIN_PW = 'AdminPass2026'


@pytest.fixture
def app(tmp_path):
    app = create_app(TESTING=True, DATA_DIR=tmp_path, DB_PATH=tmp_path / 'test.sqlite3', UPLOAD_DIR=tmp_path / 'uploads',
                     BACKUP_DIR=tmp_path / 'backups', ADMIN_PASSWORD=ADMIN_PW, COOKIE_SECURE=False,
                     TELEGRAM_WEBHOOK_SECRET='hooksecret', TELEGRAM_BOT_TOKEN='TEST', APP_MODE='production')
    with app.app_context():
        get_db().execute('UPDATE users SET must_change_password=0')
        # most tests exercise the weighbridge path; the field-sum auto waybill has its own tests
        get_db().execute("INSERT INTO settings(key, value, updated_at) VALUES ('auto_waybill_hand','0','x')")
    yield app


def jpeg(color=(200, 200, 200), size=(64, 48)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, 'JPEG')
    return buf.getvalue()


class Client:
    """Test client that logs in and sends the CSRF token automatically."""

    def __init__(self, app, username=None, password=None, ua=None):
        self.app = app
        self.c = app.test_client()
        self.headers = {'User-Agent': ua} if ua else {}
        if username:
            self.login(username, password)

    def csrf(self):
        with self.c.session_transaction() as s:
            return s.get('csrf')

    def get(self, url, **kw):
        return self.c.get(url, headers={**self.headers, **kw.pop('headers', {})}, **kw)

    def login(self, username, password):
        self.get('/login')
        r = self.c.post('/login', data={'username': username, 'password': password, '_csrf': self.csrf()})
        assert r.status_code == 302 and '/login' not in r.headers['Location'], f'login failed for {username}'
        return r

    def post(self, url, data=None, json_resp=True, files=None, **kw):
        data = dict(data or {})
        data.setdefault('_csrf', self.csrf())
        if files:
            for k, v in files.items():
                data[k] = [(io.BytesIO(b), f'{k}{i}.jpg') for i, b in enumerate(v if isinstance(v, list) else [v])]
        headers = {'X-Requested-With': 'fetch', 'Accept': 'application/json'} if json_resp else {}
        return self.c.post(url, data=data, headers={**self.headers, **headers}, content_type='multipart/form-data', **kw)


def make_user(app, admin, username, role, brigadier=None, password='Worker2026x'):
    with app.app_context():
        bid = None
        if brigadier:
            bid = q('SELECT id FROM brigadiers WHERE name=?', (brigadier,), one=True)['id']
    r = admin.post('/admin/foydalanuvchilar', {'username': username, 'full_name': username.title(), 'role': role,
                                                'password': password, 'brigadier_id': bid or ''})
    assert r.status_code == 200 and r.get_json()['ok'], r.get_data(as_text=True)
    with app.app_context():
        get_db().execute('UPDATE users SET must_change_password=0 WHERE username=?', (username,))
    return Client(app, username, password)


@pytest.fixture
def admin(app):
    return Client(app, 'admin', ADMIN_PW)


@pytest.fixture
def world(app, admin):
    """Admin + fields for two brigades + one user per role."""
    with app.app_context():
        b = {r['name']: r['id'] for r in q('SELECT id, name FROM brigadiers')}
        eq = {r['code']: r['id'] for r in q('SELECT id, code FROM equipment')}
    for code, name, area, brig in (('D-01', 'Dala 1', 94, 'Nurim ota'), ('D-04', 'Dala 4', 126.2, 'Juma ota')):
        r = admin.post('/admin/dalalar', {'code': code, 'name': name, 'area_ha': area, 'brigadier_id': b[brig]})
        assert r.get_json()['ok']
    with app.app_context():
        f = {r['code']: r['id'] for r in q('SELECT id, code FROM fields')}
    w = {
        'admin': admin, 'b': b, 'eq': eq, 'f': f,
        'juma': make_user(app, admin, 'juma', 'brigadier', 'Juma ota'),
        'nurim': make_user(app, admin, 'nurim', 'brigadier', 'Nurim ota'),
        'tarozi': make_user(app, admin, 'tarozi01', 'scale'),
        'bux': make_user(app, admin, 'buxgalter', 'accountant'),
        'kassa': make_user(app, admin, 'asadbek', 'cashier'),
        'rahbar': make_user(app, admin, 'rahbar', 'manager'),
    }
    return w


def open_load(client, world, trailer='TL-01', field='D-04', tractor='T-01'):
    r = client.post('/telashkalar/ochish', {'trailer_id': world['eq'][trailer], 'field_id': world['f'][field],
                                            'tractor_id': world['eq'][tractor]})
    body = r.get_json()
    assert body['ok'], body
    return body['load_id']


def uuid4():
    import uuid
    return str(uuid.uuid4())


def number_from(html):
    m = re.search(r'PA-\d{6}', html)
    return m.group(0) if m else None
