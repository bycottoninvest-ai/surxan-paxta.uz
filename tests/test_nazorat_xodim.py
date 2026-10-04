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
        assert p['chat_id'] == '777' and 'PQ-17 dan farq' in p['text'] and 'brutto, tara' in p['text'] and '1️⃣' in p['text'] and '/punkt/yuk/' in p['text'] and p['photos']
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


def test_hosil_session_closed_tells_on_telegram(app, world):
    """The office computer's PQ-17 task marks hosil-qabuli.uz closed → director and accountant are told at once;
    marked open again → the problem closes; silent for too long → a warning."""
    admin, bux = world['admin'], world['bux']
    with app.app_context():
        get_db().execute("UPDATE users SET telegram_id='111' WHERE username='admin'")
        get_db().commit()
    assert '— hali belgilanmagan' in bux.get('/buxgalteriya/hosil-holat').get_data(as_text=True)
    assert bux.post('/buxgalteriya/hosil-holat', {'holat': 'yopiq'}).get_json()['ok']
    with app.app_context():
        msgs = [m['payload_json'] for m in q("SELECT payload_json FROM outbox WHERE kind='alert'")]
        assert any('hosil-qabuli.uz yopilgan' in m and '"111"' in m for m in msgs)
        assert q("SELECT 1 FROM nazorat_alerts WHERE key LIKE 'hq:yopiq:%' AND resolved_at IS NULL", one=True)
    assert bux.post('/buxgalteriya/hosil-holat', {'holat': 'ochiq'}).get_json()['ok']
    with app.app_context():
        assert not q("SELECT 1 FROM nazorat_alerts WHERE key LIKE 'hq:yopiq:%' AND resolved_at IS NULL", one=True)
        get_db().execute("UPDATE settings SET value='2026-01-01 08:00:00' WHERE key='hq_session_at'")
        get_db().commit()
        from surxon.nazorat import issues
        assert any(i['title'] == 'PQ-17 avtomatik yuklash ishlamayapti' for i in issues())
    assert world['juma'].get('/buxgalteriya/hosil-holat').status_code in (302, 403)


def test_cluster_load_we_have_not_received_goes_to_the_punkt(app, world):
    """A PQ-17 for a load the punkt has not received yet → Yunus is told on Telegram which yuk xati to receive."""
    tally, yunus, ali, st = setup(app, world)
    bux = world['bux']
    bux.c.post('/buxgalteriya/pq17', data={'files': [(io.BytesIO(pq17_pdf('XH1000000009', '388190')), 'x.pdf')], '_csrf': bux.csrf()},
               content_type='multipart/form-data', headers={'X-Requested-With': 'fetch'})
    with app.app_context():
        from surxon.nazorat import tick
        tick(force=True)                       # found before Yunus linked Telegram — still told once he has
        get_db().execute("UPDATE users SET telegram_id='777' WHERE username='yunus'")
        get_db().commit()
        tick(force=True)
        tick(force=True)
        assert len(q("SELECT 1 FROM outbox WHERE kind='xodim'")) == 1
        rows = [json.loads(r['payload_json']) for r in q("SELECT payload_json FROM outbox WHERE kind='xodim'")]
        assert any(p['chat_id'] == '777' and '388190' in p['text'] and 'QABUL QILINMAGAN' in p['text'] and '388190 ni yozing' in p['text'] for p in rows)
