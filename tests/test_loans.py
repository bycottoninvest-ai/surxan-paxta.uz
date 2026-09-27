"""Credits, leasing, insurance: counterparties, contracts, schedule from the leasing Excel, payments laid over the
schedule (paid / overdue), important dates with reminders, files — manager and accountant only."""
import io

import openpyxl

from conftest import make_user
from surxon.db import q


def lizing_xlsx():
    """Same layout as the Agrosanoat leasing schedule (made-up numbers, “минг сўм”)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([None, '“TEST CE-1” русумли пахта териш машинаси'])
    ws.append([None, 'Техниканинг нархи, минг сўм', 1200])
    ws.append([None, 'Лизинг ставкаси (маржаси)нинг йиллик фоизи', 3])
    ws.append([None, 'Лизинг муддати', 12])
    ws.append([None, 'Олдиндан тўлов (аванс) миқдори, минг сўм', 200])
    ws.append([None, 'Техника лизингга берилган сана', '01.08.2026'])
    ws.append([None, '(минг сўм)'])
    ws.append([None, 'т/р', 'қолдиқ', 'сана', 'кун', 'қисм', 'фоиз', 'тўлов'])
    ws.append([None, '1', '1 000,000', '30.09.2026', '61', '250,000', '5,000', '255,000'])
    ws.append([None, None, '1 000,000', '31.08.2026', '31', '125,000', '2,500', '127,500'])     # monthly breakdown, not a payment
    ws.append([None, '2', '750,000', '31.12.2026', '92', '250,000', '4,000', '254,000'])
    ws.append([None, '3', '500,000', '31.03.2027', '90', '250,000', '3,000', '253,000'])
    ws.append([None, '4', '250,000', '30.06.2027', '91', '250,000', '2,000', '252,000'])
    ws.append([None, 'х', 'Жами', '2 922', '1 000,000', '14,000', '1 014,000'])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_leasing_schedule_payments_overdue_and_dates(app, world, monkeypatch):
    bux, rahbar = world['bux'], world['rahbar']
    r = bux.post('/kreditlar/firma', {'name': 'Autosanoat Agro Lizing', 'inn': '300000001', 'kind': 'lizing'}).get_json()
    assert r['ok'], r
    with app.app_context():
        pid = q('SELECT id FROM parties', one=True)['id']
        eq = q("SELECT id FROM equipment WHERE code='K-01'", one=True)['id']
    r = bux.post(f'/kreditlar/firma/{pid}', {'kind': 'lizing', 'title': 'Kombayn lizingi', 'number': 'L-1', 'equipment_id': eq}).get_json()
    assert r['ok']
    with app.app_context():
        cid = q('SELECT id FROM contracts', one=True)['id']
    up = bux.c.post(f'/kreditlar/shartnoma/{cid}', data={'action': 'schedule_xlsx', 'file': (io.BytesIO(lizing_xlsx()), 'grafik.xlsx'),
                                                          '_csrf': bux.csrf()}, content_type='multipart/form-data',
                    headers={'X-Requested-With': 'fetch'}).get_json()
    assert up['ok'] and '4 ta to‘lov' in up['message'] and '1 014 000' in up['message']
    with app.app_context():
        c = q('SELECT * FROM contracts WHERE id=?', (cid,), one=True)
        assert (c['amount'], c['advance'], c['months'], c['start_date']) == (1200000, 200000, 12, '2026-08-01')   # from the header
        assert q('SELECT COUNT(*) n FROM contract_files', one=True)['n'] == 1

    # advance + first payment made; “today” after the 2nd due date → the 2nd is overdue
    for d, amt in (('2026-08-05', '200000'), ('2026-09-28', '255000')):
        assert bux.post(f'/kreditlar/firma/{pid}', {'action': 'move', 'move_date': d, 'direction': 'OUT', 'amount': amt,
                                                    'contract_id': cid}).get_json()['ok']
    with app.app_context():
        from surxon import loans as L
        st = L.contract_state(q('SELECT * FROM contracts WHERE id=?', (cid,), one=True), today='2027-01-10')
        assert [l['state'] for l in st['lines']] == ['paid', 'overdue', 'due', 'due']
        assert st['debt'] == 200000 + 1014000 - 455000 and st['overdue_sum'] == 254000 and st['next']['date'] == '2027-03-31'
        up = L.upcoming(90, today='2027-01-10')
        assert up[0]['state'] == 'overdue' and up[0]['amount'] == 254000

    # an important date (insurance premium / inspection) with a reminder to the report channel
    assert bux.post(f'/kreditlar/firma/{pid}', {'action': 'duty', 'title': 'Sug‘urta mukofotini to‘lash', 'due_date': '2026-10-20',
                                                'remind_days': '10', 'contract_id': cid}).get_json()['ok']
    with app.app_context():
        from surxon import loans as L
        from surxon.db import get_db
        o = L.obligations(today='2026-10-15')[0]
        assert o['state'] == 'soon' and o['days_left'] == 5
        assert L.remind_due(today='2026-10-15') >= 1 and L.remind_due(today='2026-10-15') == 0     # once a day
        assert 'Sug‘urta mukofotini' in q("SELECT payload_json FROM outbox WHERE ref='loans:2026-10-15'", one=True)['payload_json']

    home = rahbar.get('/kreditlar').get_data(as_text=True)
    assert 'Autosanoat Agro Lizing' in home and 'Muhim muddatlar' in home
    page = bux.get(f'/kreditlar/shartnoma/{cid}').get_data(as_text=True).replace('\u202f', ' ').replace('\xa0', ' ')
    assert 'To‘lov grafigi' in page and '255 000' in page
    assert world['juma'].get('/kreditlar').status_code == 302                    # brigadier: no
    assert world['kassa'].post('/kreditlar/firma', {'name': 'X'}).status_code in (302, 403)
    with app.app_context():
        fid = q('SELECT id FROM contract_files', one=True)['id']
    assert bux.get(f'/kreditlar/fayl/{fid}').status_code == 200


def test_plain_schedule_and_insurance_one_payment(app, world):
    bux = world['bux']
    bux.post('/kreditlar/firma', {'name': 'INFINITY INSURANCE', 'kind': 'sugurta'})
    with app.app_context():
        pid = q('SELECT id FROM parties', one=True)['id']
    bux.post(f'/kreditlar/firma/{pid}', {'kind': 'sugurta', 'title': 'Kombayn sug‘urtasi', 'amount': '8633500',
                                         'end_date': '2034-09-10', 'terms': 'Zarar bo‘lsa 5 ish kuni ichida xabar berish'})
    with app.app_context():
        cid = q('SELECT id FROM contracts', one=True)['id']
    assert bux.post(f'/kreditlar/shartnoma/{cid}', {'action': 'schedule_one', 'due_date': '2026-10-22', 'amount': '8633500'}).get_json()['ok']
    page = bux.get(f'/kreditlar/shartnoma/{cid}').get_data(as_text=True).replace('\u202f', ' ').replace('\xa0', ' ')
    assert '5 ish kuni ichida xabar' in page and '8 633 500' in page
    with app.app_context():
        from surxon import loans as L
        rows = L.annuity_schedule(1200000, 12, 12, __import__('datetime').date(2026, 1, 1), every=3)
        assert len(rows) == 4 and rows[0]['due_date'] == '2026-04-30' and rows[0]['interest'] == 36000
