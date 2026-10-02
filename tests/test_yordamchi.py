"""Yordamchi: the director's 23:00 report and the accountant's invoice (faktura) reminder for every PQ-17."""
import io

from surxon.db import get_db, q
from test_combine_akt import _close
from test_pq17 import pq17_pdf
from test_punkt import setup


def _upload(bux, code, load_no):
    return bux.c.post('/buxgalteriya/pq17', data={'files': [(io.BytesIO(pq17_pdf(code, load_no)), 'x.pdf')], '_csrf': bux.csrf()},
                      content_type='multipart/form-data', headers={'X-Requested-With': 'fetch'})


def test_director_report_and_faktura_reminder(app, world):
    tally, yunus, ali, st = setup(app, world)
    admin, bux = world['admin'], world['bux']
    assert admin.post('/admin/sozlamalar', {'set_combine_rate_standard': '1000'}).get_json()['ok']
    with app.app_context():
        db = get_db()
        db.execute("UPDATE users SET telegram_id='111' WHERE username='admin'")
        db.execute("UPDATE users SET telegram_id='222' WHERE username='buxgalter'")
        db.commit()
        k1 = q("SELECT id FROM equipment WHERE code='K-01'", one=True)['id']
    w = _close(app, tally, world, 'TL-01', [('480', k1)])
    assert yunus.post(f'/punkt/yuk/{w}/qabul', {'station_kg': '480', 'load_no': '555001'}).get_json()['ok']

    # a new PQ-17 → the accountant is told at once, in their own chat, with the kg, price and sum
    from surxon import yordamchi as Y
    with app.app_context():                                         # accepted, no PQ-17 yet → the accountant is asked for it
        assert 'PQ-17 HALI YUKLANMAGAN: 1 ta' in Y.accountant_text()
        get_db().execute("INSERT INTO settings(key, value, updated_at) VALUES ('pq17_remind_times', '00:00,00:01', '') "
                         "ON CONFLICT(key) DO UPDATE SET value='00:00,00:01'")
        get_db().commit()
        assert Y.maybe_remind() and not Y.maybe_remind()            # daytime reminder: once per time, the latest only
        r = q("SELECT payload_json FROM outbox WHERE kind='remind'")
        assert len(r) == 1 and '"222"' in r[0]['payload_json'] and 'PQ-17 si hali yuklanmagan' in r[0]['payload_json']
    page = bux.get('/buxgalteriya/kombaynlar').get_data(as_text=True)  # the standing banner on top of every page
    assert 'Buxgalter eslatmasi' in page and '1 ta yukning PQ-17 si yuklanmagan' in page
    assert 'Buxgalter eslatmasi' not in world['juma'].get('/').get_data(as_text=True)
    _upload(bux, 'XH1000000001', '555001')
    with app.app_context():
        new = q("SELECT * FROM outbox WHERE ref LIKE 'pq17new:%'")
        assert len(new) == 1 and '"222"' in new[0]['payload_json'] and 'XH1000000001' in new[0]['payload_json']
        assert '"111"' not in new[0]['payload_json']
    _upload(bux, 'XH1000000001', '555001')                          # the same file again: no second message
    with app.app_context():
        assert len(q("SELECT 1 FROM outbox WHERE ref LIKE 'pq17new:%'")) == 1

    # the faktura page lists it; marking the invoice signed takes it off the list
    page = bux.get('/buxgalteriya/faktura').get_data(as_text=True)
    assert 'XH1000000001' in page and 'SURXON imzosi kutilmoqda' in page
    with app.app_context():
        pid = q('SELECT id FROM pq17_docs', one=True)['id']

    # the evening: before 23:00 nothing; after it once — the director's facts to admin, the invoice list to the accountant
    from surxon import yordamchi as Y
    with app.app_context():
        get_db().execute("INSERT INTO settings(key, value, updated_at) VALUES ('director_report_time', '00:00', '') "
                         "ON CONFLICT(key) DO UPDATE SET value='00:00'")
        get_db().commit()
        assert Y.maybe_send_evening()
        assert not Y.maybe_send_evening()                           # once a day
        d = q("SELECT payload_json FROM outbox WHERE kind='director'")
        f = q("SELECT payload_json FROM outbox WHERE kind='faktura' AND ref LIKE 'faktura:%'")
        assert len(d) == 1 and '"111"' in d[0]['payload_json'] and len(f) == 1 and '"222"' in f[0]['payload_json']
        akt = q("SELECT payload_json FROM outbox WHERE kind='akt'")       # K-01 worked today → its day akt to both
        assert len(akt) == 2 and all(f'"cid": {k1}' in a['payload_json'] for a in akt)
        pdf, name, cap = Y.combine_day_akt(k1, q('SELECT work_date FROM harvests LIMIT 1', one=True)['work_date'])
        assert pdf[:4] == b'%PDF' and name.startswith('akt_K-01_') and 'HOZIR TO‘LASH MUMKIN' in cap
        sent = []
        import surxon.outbox as O
        real = O.telegram_upload
        O.telegram_upload = lambda method, fields, files, token=None: sent.append((method, fields['chat_id'], list(files)))
        try:
            O._send_report({'kind': 'akt'}, __import__('json').loads(akt[0]['payload_json']))
        finally:
            O.telegram_upload = real
        assert sent and sent[0][0] == 'sendDocument' and sent[0][2] == ['document']
        text = Y.director_text()
    for part in ('DIREKTOR HISOBOTI', 'Terildi: 480 kg', 'Punkt qabul qildi: 1 ta · 480 kg', 'KLASTER (PQ-17)',
                 'K-01 480 kg', 'ERTAGA QILISH KERAK', '1 ta PQ-17 ni imzolash kerak'):
        assert part in text, part

    page = admin.get('/yordamchi').get_data(as_text=True)
    assert 'DIREKTOR HISOBOTI' in page and 'PQ-17 VA FAKTURA' in page
    assert bux.get('/yordamchi').status_code in (302, 403)            # only the director / admin
    assert world['juma'].get('/buxgalteriya/faktura').status_code in (302, 403)

    assert bux.post('/buxgalteriya/faktura', {'action': 'inv_signed', 'ids': pid, 'invoice_no': 'F-12'}).get_json()['ok']
    page = bux.get('/buxgalteriya/faktura').get_data(as_text=True)
    assert 'XH1000000001' not in page and 'Hamma fakturalar imzolangan' in page
    with app.app_context():
        assert Y.accountant_text() == ''                            # PQ-17 in, invoice signed → nothing to ask
        assert 'imzolash kerak' not in Y.director_text()


def test_personal_message_goes_without_report_group(app, world):
    """No report group connected: a personal message (director report, akt, reminder) is still sent through the bot,
    and the Yordamchi page shows who has not linked Telegram and what happened to each message."""
    import surxon.outbox as O
    cfg = app.config['SURXON']
    old = (cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_REPORT_CHAT_ID)
    cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_REPORT_CHAT_ID = 'x:test', ''
    sent, real = [], O.telegram_upload
    O.telegram_upload = lambda method, fields, files, token=None: sent.append(fields['chat_id']) or {'ok': True}
    try:
        with app.app_context():
            db = get_db()
            db.execute("UPDATE users SET telegram_id='111' WHERE username='admin'")
            db.execute("DELETE FROM settings WHERE key='tg_report_chat_id'")
            db.commit()
            from surxon import yordamchi as Y
            Y.send_evening(force=True)
            O.run_once()
            assert '111' in sent
            assert q("SELECT status FROM outbox WHERE kind='director'", one=True)['status'] == 'sent'
    finally:
        O.telegram_upload = real
        cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_REPORT_CHAT_ID = old
    page = world['admin'].get('/yordamchi').get_data(as_text=True)
    assert 'Xabar kimga boradi' in page and '✓ yetkazildi' in page and '✗ bog‘lanmagan' in page
