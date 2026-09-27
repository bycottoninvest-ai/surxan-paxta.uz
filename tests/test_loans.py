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


def docx(text):
    """A minimal Word file holding `text` (enough for the document reader)."""
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', '<w:document><w:body>' + ''.join(f'<w:p><w:r><w:t>{ln}</w:t></w:r></w:p>'
                                                                         for ln in text.split('\n')) + '</w:body></w:document>')
    return buf.getvalue()


def test_document_inbox_sorts_by_firm_and_akt_sverka(app, world, monkeypatch):
    bux = world['bux']
    bux.post('/kreditlar/firma', {'name': 'Test Agro Lizing', 'inn': '300000001', 'kind': 'lizing'})
    bux.post('/kreditlar/firma', {'name': 'Boshqa Bank ATB', 'inn': '300000002', 'kind': 'bank'})
    with app.app_context():
        pid = q("SELECT id FROM parties WHERE inn='300000001'", one=True)['id']
    bux.post(f'/kreditlar/firma/{pid}', {'kind': 'lizing', 'title': 'Traktor lizingi', 'number': 'LZ-77/2026', 'amount': '1000',
                                         'advance': '100', 'start_date': '2026-08-01'})
    with app.app_context():
        cid = q('SELECT id FROM contracts', one=True)['id']
    bux.post(f'/kreditlar/shartnoma/{cid}', {'action': 'schedule_one', 'due_date': '2026-09-30', 'amount': '500'})
    bux.post(f'/kreditlar/shartnoma/{cid}', {'action': 'schedule_one', 'due_date': '2026-12-31', 'amount': '400'})
    bux.post(f'/kreditlar/firma/{pid}', {'action': 'move', 'move_date': '2026-08-02', 'direction': 'OUT', 'amount': '100', 'contract_id': cid})
    bux.post(f'/kreditlar/firma/{pid}', {'action': 'move', 'move_date': '2026-10-01', 'direction': 'OUT', 'amount': '300', 'contract_id': cid})

    def upload(*files):
        return bux.c.post('/buxgalteriya/hujjatlar', data={'files': [(io.BytesIO(d), n) for n, d in files], '_csrf': bux.csrf()},
                          content_type='multipart/form-data', headers={'X-Requested-With': 'fetch'}).get_json()

    # an invoice with the firm's STIR → straight into that firm, typed “faktura”; the only contract is taken
    r = upload(('faktura.docx', docx('СЧЕТ-ФАКТУРА № 15\nПоставщик ИНН 300 000 001\nПокупатель ИНН 311720284')))
    assert r['ok'] and 'Test Agro Lizing' in r['message'] and 'Faktura' in r['message'], r
    # by the contract number only; then a paper with no firm → waits in the inbox; the same file again → not stored twice
    r = upload(('grafik.txt', 'LZ-77/2026 to‘lov grafigi'.encode()), ('nomalum.txt', b'qandaydir hujjat'))
    r2 = upload(('nomalum.txt', b'qandaydir hujjat'))
    assert 'avval yuborilgan' in r2['message']
    with app.app_context():
        rows = q('SELECT name, status, guess_party_id, guess_contract_id, guess_kind FROM doc_inbox ORDER BY id')
        assert [(x['status'], x['guess_party_id'], x['guess_kind']) for x in rows] == \
            [('filed', pid, 'faktura'), ('filed', pid, 'grafik'), ('new', None, None)]
        assert rows[1]['guess_contract_id'] == cid
        assert q("SELECT COUNT(*) n FROM contract_files WHERE party_id=?", (pid,), one=True)['n'] == 2
        iid = q("SELECT id FROM doc_inbox WHERE status='new'", one=True)['id']
    page = bux.get('/buxgalteriya/hujjatlar').get_data(as_text=True)
    assert 'nomalum.txt' in page and 'Ajratilmagan (1)' in page
    assert bux.post('/buxgalteriya/hujjatlar', {'action': 'file', 'inbox_id': iid, 'party_id': pid, 'kind': 'akt'}).get_json()['ok']

    # the bot: a Word file from the accountant is sorted the same way
    from surxon import telegram_bot
    sent = []
    monkeypatch.setattr(telegram_bot, 'send', lambda chat, text, *a, **k: sent.append(text))
    monkeypatch.setattr(telegram_bot, 'tg_download', lambda fid: docx('Договор № B-1\nБанк ИНН 300000002'))
    with app.app_context():
        from surxon.db import get_db
        get_db().execute("UPDATE users SET telegram_id='8081' WHERE username='buxgalter'")
        telegram_bot.process_update({'update_id': 31, 'message': {'chat': {'id': 8081, 'type': 'private'}, 'from': {'id': 8081, 'first_name': 'B'},
                                     'document': {'file_id': 'd1', 'file_name': 'kredit.docx', 'mime_type': 'application/msword'}}})
    assert '✅ kredit.docx → Boshqa Bank ATB' in sent[-1] and 'Shartnoma' in sent[-1], sent

    # akt-sverka: what fell due (advance 100 + 500 on 30.09) against what we paid (100 + 300) → we owe 200; 400 not due yet
    with app.app_context():
        from surxon import loans as L
        a = L.akt(pid, gacha='2026-10-15', today='2026-10-15')
        assert (a['due'], a['paid'], a['closing'], a['later']) == (600, 400, 200, 400)
        b = L.akt(pid, dan='2026-09-01', gacha='2026-10-15', today='2026-10-15')
        assert b['opening'] == 0 and b['closing'] == 200 and len(b['rows']) == 2
    page = bux.get(f'/kreditlar/firma/{pid}/akt-sverka?gacha=2026-10-15').get_data(as_text=True)
    assert 'SOLISHTIRISH DALOLATNOMASI' in page and 'Test Agro Lizing' in page and '300000001' in page

    # Buxgalteriya: one line leads to the section; the section lists firms grouped by type
    home = bux.get('/buxgalteriya').get_data(as_text=True)
    assert 'Akt-sverka va firmalar' in home and 'Test Agro Lizing' not in home
    firms = bux.get('/buxgalteriya/akt-sverka').get_data(as_text=True)
    assert 'Test Agro Lizing' in firms and 'Boshqa Bank ATB' in firms and 'Lizing kompaniyasi' in firms
    assert world['juma'].get('/buxgalteriya/akt-sverka').status_code == 302
    assert world['juma'].get('/buxgalteriya/hujjatlar').status_code == 302
