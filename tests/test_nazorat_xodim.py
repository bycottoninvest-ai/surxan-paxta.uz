"""A mistake goes to the person who entered it, on Telegram, with the punkt paper photo and what to check."""
import io
import json

from conftest import jpeg
from surxon.db import get_db, q
from test_combine_akt import _close
from test_pq17 import pq17_pdf
from test_punkt import setup


def test_mistake_goes_to_who_entered_it_with_photos(app, world):
    tally, yunus, ali, st = setup(app, world)
    bux = world['bux']
    with app.app_context():
        get_db().execute("UPDATE users SET telegram_id='777' WHERE username='yunus'")
        get_db().commit()
        k1 = q("SELECT id FROM equipment WHERE code='K-01'", one=True)['id']
    w = _close(app, tally, world, 'TL-01', [('600', k1)])
    r = yunus.post(f'/punkt/yuk/{w}/qabul', {'station_kg': '600', 'load_no': '555001'}, files={'photo': jpeg()}).get_json()
    assert r['ok'], r
    # the cluster's PQ-17 says 480 kg — the punkt wrote 600
    bux.c.post('/buxgalteriya/pq17', data={'files': [(io.BytesIO(pq17_pdf('XH1000000007', '555001')), 'x.pdf')], '_csrf': bux.csrf()},
               content_type='multipart/form-data', headers={'X-Requested-With': 'fetch'})
    with app.app_context():
        from surxon.nazorat import tick
        tick(force=True)
        rows = q("SELECT payload_json FROM outbox WHERE kind='xodim'")
        assert len(rows) == 1
        p = json.loads(rows[0]['payload_json'])
        assert p['chat_id'] == '777' and 'PQ-17 dan farq' in p['text'] and 'brutto va tara' in p['text'] and p['photos']
        tick(force=True)                                                  # told once, not on every check
        assert len(q("SELECT 1 FROM outbox WHERE kind='xodim'")) == 1
        import surxon.outbox as O
        sent, real = [], O.telegram_upload
        O.telegram_upload = lambda method, fields, files, token=None: sent.append((method, fields.get('caption', '')[:20]))
        try:
            O._send_report({'kind': 'xodim'}, p)
        finally:
            O.telegram_upload = real
        assert sent[0][0] == 'sendPhoto' and sent[0][1].startswith('SURXAN-PAXTA.UZ')    # the question rides on the photo
