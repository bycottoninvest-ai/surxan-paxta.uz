"""Google Sheets can be connected from the admin page (no server login): link + service-account JSON, key never shown."""
import io
import json

from surxon.db import q
from surxon.outbox import configured, sheets_conf

SA = {'type': 'service_account', 'client_email': 'surxon@proj.iam.gserviceaccount.com', 'private_key_id': 'abc',
      'private_key': '-----BEGIN PRIVATE KEY-----\nMIIEv\n-----END PRIVATE KEY-----\n', 'project_id': 'proj'}
LINK = 'https://docs.google.com/spreadsheets/d/1eWl21webrxSAV_NDdvv8dX1lWmu5gSEMSD1fvMWh8Mw/edit?gid=1'


def test_admin_connects_sheets_from_page(app, admin):
    with app.app_context():
        assert not configured('sheets')
    bad = admin.post('/admin/integratsiyalar', {'action': 'sheets_save', 'sheet': LINK},
                     files={'sa_file': b'{"type": "authorized_user"}'})
    assert not bad.get_json()['ok']
    r = admin.post('/admin/integratsiyalar', {'action': 'sheets_save', 'sheet': LINK},
                   files={'sa_file': json.dumps(SA).encode()}).get_json()
    assert r['ok'] and SA['client_email'] in r['message']
    with app.app_context():
        sid, f = sheets_conf()
        assert sid == '1eWl21webrxSAV_NDdvv8dX1lWmu5gSEMSD1fvMWh8Mw' and configured('sheets')
        import os, stat
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600 and json.load(open(f))['client_email'] == SA['client_email']
        logs = ' '.join(r['new_json'] or '' for r in q("SELECT new_json FROM audit_logs WHERE entity_id='google_sheets'"))
        assert 'PRIVATE' not in logs and SA['client_email'] in logs
    page = admin.get('/admin/integratsiyalar').get_data(as_text=True)
    assert SA['client_email'] in page and 'PRIVATE KEY' not in page and 'Ulangan' in page
    # changing only the link keeps the saved key
    assert admin.post('/admin/integratsiyalar', {'action': 'sheets_save', 'sheet': LINK}).get_json()['ok']
    assert admin.post('/admin/integratsiyalar', {'action': 'sheets_clear'}).get_json()['ok']
    with app.app_context():
        assert not configured('sheets') and sheets_conf() == ('', '')


def test_only_admin_can_connect_sheets(app, world):
    r = world['bux'].post('/admin/integratsiyalar', {'action': 'sheets_save', 'sheet': LINK})
    assert r.status_code in (302, 403)
