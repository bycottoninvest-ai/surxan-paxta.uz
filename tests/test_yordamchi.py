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
        text = Y.director_text()
    for part in ('DIREKTOR HISOBOTI', 'Terildi: 480 kg', 'Punkt qabul qildi: 1 ta · 480 kg', 'KLASTER (PQ-17)',
                 'K-01 480 kg', 'ERTAGA QILISH KERAK', '1 ta PQ-17 ni imzolash kerak'):
        assert part in text, part

    page = admin.get('/yordamchi').get_data(as_text=True)
    assert 'DIREKTOR HISOBOTI' in page and 'FAKTURA KUTAYOTGAN' in page
    assert bux.get('/yordamchi').status_code in (302, 403)            # only the director / admin
    assert world['juma'].get('/buxgalteriya/faktura').status_code in (302, 403)

    assert bux.post('/buxgalteriya/faktura', {'action': 'inv_signed', 'ids': pid, 'invoice_no': 'F-12'}).get_json()['ok']
    page = bux.get('/buxgalteriya/faktura').get_data(as_text=True)
    assert 'XH1000000001' not in page and 'Hamma fakturalar imzolangan' in page
    with app.app_context():
        assert Y.accountant_text() == ''
        assert 'imzolash kerak' not in Y.director_text()
