"""Storage Box (off-server backups) can be connected from the admin page; the password never shows up anywhere."""
import os
import stat
import subprocess

from surxon.db import q
from surxon.outbox import configured


class Done:
    returncode, stderr = 0, ''

    def __init__(self, out):
        self.stdout = out


def test_admin_connects_storage_box(app, admin, monkeypatch):
    seen = {}

    def fake_run(args, **kw):
        seen['args'], seen['input'] = args, kw.get('input')
        return Done('OBSCURED123\n')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    with app.app_context():
        assert not configured('offsite')
    assert not admin.post('/admin/integratsiyalar', {'action': 'offsite_save', 'host': 'bad host', 'user': 'u1',
                                                     'password': 'secret-pass'}).get_json()['ok']
    r = admin.post('/admin/integratsiyalar', {'action': 'offsite_save', 'host': 'u123456.your-storagebox.de',
                                              'user': 'u123456', 'password': 'MyBoxPass!2026', 'port': '23'}).get_json()
    assert r['ok'], r
    assert seen['args'] == ['rclone', 'obscure', '-'] and seen['input'] == 'MyBoxPass!2026'   # never on the command line
    cfg = app.config['SURXON']
    f = cfg.DATA_DIR / 'rclone.conf'
    text = f.read_text()
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert 'MyBoxPass' not in text and 'pass = OBSCURED123' in text and 'port = 23' in text
    with app.app_context():
        assert configured('offsite') and cfg.OFFSITE_RCLONE_REMOTE == 'storagebox:surxon-zaxira'
        assert os.environ['RCLONE_CONFIG'] == str(f)
        logs = ' '.join(x['new_json'] or '' for x in q("SELECT new_json FROM audit_logs WHERE entity_id='offsite'"))
        assert 'MyBoxPass' not in logs and 'OBSCURED' not in logs and 'u123456' in logs
    page = admin.get('/admin/integratsiyalar').get_data(as_text=True)
    assert 'u123456.your-storagebox.de' in page and 'MyBoxPass' not in page and 'OBSCURED' not in page
    assert admin.post('/admin/integratsiyalar', {'action': 'offsite_clear'}).get_json()['ok']
    with app.app_context():
        assert not configured('offsite') and not f.exists()
    os.environ.pop('RCLONE_CONFIG', None)


def test_only_admin(app, world):
    r = world['bux'].post('/admin/integratsiyalar', {'action': 'offsite_save', 'host': 'u1.your-storagebox.de',
                                                     'user': 'u1', 'password': 'xxxxxxx'})
    assert r.status_code in (302, 403)


def test_update_button_requests_server_update(app, world):
    admin = world['admin']
    page = admin.get('/admin/zaxira').get_data(as_text=True)
    assert 'Server yangilanishi' in page and 'Hozir yangilash' in page
    assert admin.post('/admin/zaxira', {'action': 'update'}).get_json()['ok']
    cfg = app.config['SURXON']
    assert (cfg.DATA_DIR / 'deploy_request').exists()
    (cfg.DATA_DIR / 'deploy_status.json').write_text('{"at":"2026-09-27 10:00:00","ok":true,"message":"Yangilandi: v2.18.1"}')
    assert 'Yangilandi: v2.18.1' in admin.get('/admin/zaxira').get_data(as_text=True)
    assert world['bux'].post('/admin/zaxira', {'action': 'update'}).status_code in (302, 403)
