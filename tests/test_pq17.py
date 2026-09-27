"""PQ-17 (state cotton receipt, docs.agro.uz): read from the PDF, tied to the trip, compared (kg, hand / combine),
and from then on the punkt's money for that trip is the document's sum — conditioned weight × real price."""
import io

from conftest import jpeg, uuid4
from surxon.db import q
from test_punkt import add, setup


def pq17_pdf(code='XH1000000001', load_no='555001', method='Qoʻlda', netto=480, deduction=9, price=8066.41,
             base=7862, coef=1.026, date='27092026', inn='311720284'):
    """A PDF with the same text as a real PQ-17 (made-up numbers)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from surxon.pdfdoc import _fonts
    _fonts()
    kond = netto - deduction
    amount = round(kond * price, 2)
    lines = ['Taxiatosh код 1735228 Туман Қишлоқ кенгаши', f'SURXON TAXIATOSH TEXTILE MCHJ {inn}',
             'Жамоа, ширкат ва фермер хўжалиги куноййил ўлчов бирлиги', f'{date[:7]} {date[7]} кг ҚАБУЛ ВАРАҚАСИ № {code}',
             '2026 йилги пахта ҳариди учун I.Қабул қилинган пахтанинг табиий оғирлиги',
             'Накладной номери Терим тури Жамланган тўда номери Селекцион нави Сорти Синфи Соф табиий вазни',
             f'{load_no} {method} 10 Xin Lu Zao 52 1 1 {netto}', 'II. Пахтани жамланган тўдалари',
             f'10- Ombo r 10 Xin Lu Zao 52 Tex nik 1- терим 1 1 {netto} 2. 9 {netto - 5} 10 {kond} {price}2 {amount}',
             f'Харид нархи: {base} (Шартномадаги баҳоси, кг/сўм) * {coef} (1-сорт, 1-синф) = {price} сўм',
             'Пахта хомашёсининг ифлослиги ва намлиги учун хисобланган устама миқдори 0 кг',
             f'Пахта хомашёсининг ифлослиги ва намлиги учун хисобланган чегирма миқдори {deduction} кг',
             f'Пахтанинг харид нархи бўйича қиймати, ҚҚС билан 31 {amount}', 'шундан ҚҚС (31+32+33 да 12) 34 1000.5',
             'Qoraqalpog`iston Respublikasi код 1735 Taxiatosh 1735228 Туман FAYZ AGROKLASTER MCHJ Ташкилот ___________ 311919351']
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont('DejaVu', 7)
    for i, ln in enumerate(lines):
        c.drawString(20, 800 - i * 14, ln)
    c.save()
    return buf.getvalue()


def received_trip(app, world, tally, yunus, kg='480', trailer='TL-01', load_no=''):
    r = tally.post('/telashkalar/ochish', {'trailer_id': world['eq'][trailer], 'field_id': world['f']['D-04'],
                                          'tractor_id': world['eq']['T-01'], 'client_uuid': uuid4()}).get_json()
    for i in range(4):
        assert add(tally, r['load_id'], f'Terimchi {i}', '120')['ok']
    assert tally.post(f'/yuk/{r["load_id"]}/toldi', files={'photos': jpeg()}).get_json()['ok']
    with app.app_context():
        wid = q('SELECT id FROM waybills WHERE load_id=?', (r['load_id'],), one=True)['id']
    res = yunus.post(f'/punkt/yuk/{wid}/qabul', {'station_kg': kg, 'load_no': load_no, 'reason': 'Tarozilar farqi'}).get_json()
    assert res['ok'], res
    return wid


def test_parse_real_layout():
    from surxon.pq17 import parse
    d = parse(pq17_pdf())
    assert (d['code'], d['load_no'], d['method'], d['netto'], d['deduction_kg'], d['kond_kg']) == \
        ('XH1000000001', '555001', 'hand', 480, 9, 471)
    assert d['price'] == 8066.41 and d['farmer_inn'] == '311720284' and d['cluster_name'] == 'FAYZ AGROKLASTER MCHJ'
    assert d['doc_date'] == '2026-09-27' and d['coef'] == 1.026 and d['grade'] == 1


def test_pq17_matches_compares_and_prices_trips(app, world):
    tally, yunus, ali, st = setup(app, world)
    admin, bux = world['admin'], world['bux']
    bux.post('/buxgalteriya/narx', {'hand': '7800', 'combine': '7600'})
    w1 = received_trip(app, world, tally, yunus, kg='480', load_no='555001')          # punkt typed the load number
    w2 = received_trip(app, world, tally, yunus, kg='470', trailer='TL-02')           # no number, netto differs

    # upload two PQ-17s at once, plus the same file again and a stranger's
    files = [(io.BytesIO(pq17_pdf()), 'a.pdf'), (io.BytesIO(pq17_pdf('XH1000000002', '555002', netto=450)), 'b.pdf'),
             (io.BytesIO(pq17_pdf()), 'again.pdf'), (io.BytesIO(pq17_pdf('XH9000000009', '555009', inn='999999999')), 'other.pdf')]
    r = bux.c.post('/buxgalteriya/pq17', data={'files': files, '_csrf': bux.csrf()}, content_type='multipart/form-data',
                   headers={'X-Requested-With': 'fetch', 'Accept': 'application/json'}).get_json()
    assert r['ok'] and '2 ta PQ-17 qabul qilindi' in r['message'] and 'avval yuklangan' in r['message'] and 'boshqa xo‘jalik' in r['message']
    with app.app_context():
        d1 = q("SELECT * FROM pq17_docs WHERE code='XH1000000001'", one=True)
        d2 = q("SELECT * FROM pq17_docs WHERE code='XH1000000002'", one=True)
    assert d1['waybill_id'] == w1 and d1['match_how'] == 'yuk xati'
    assert d2['waybill_id'] is None                                                 # 450 ≠ 470 → not guessed

    page = bux.get('/buxgalteriya/pq17').get_data(as_text=True)
    assert 'bizda bunday reys topilmadi' in page and 'FAYZ AGROKLASTER MCHJ' in page
    assert '✓ mos' in bux.get('/buxgalteriya/pq17/klaster/311919351').get_data(as_text=True)
    # the office ties it by hand → the difference is shown
    assert bux.post('/buxgalteriya/pq17', {'action': 'link', 'doc_id': d2['id'], 'waybill_id': w2}).get_json()['ok']
    page = bux.get('/buxgalteriya/pq17').get_data(as_text=True)
    assert 'bizda 470, PQ-17 da 450' in page                                      # shown under “E’tibor bering”
    assert 'bizda 470, PQ-17 da 450' in bux.get('/buxgalteriya/pq17/klaster/311919351').get_data(as_text=True)
    # our signature and the invoice, as in hosil-qabuli.uz
    r = bux.post('/buxgalteriya/pq17/klaster/311919351', {'action': 'sign', 'ids': [d1['id'], d2['id']]}).get_json()
    assert r['ok']
    r = bux.post('/buxgalteriya/pq17/klaster/311919351', {'action': 'inv_signed', 'ids': [d1['id']], 'invoice_no': 'F-12'}).get_json()
    assert r['ok']
    cl = bux.get('/buxgalteriya/pq17/klaster/311919351').get_data(as_text=True)
    assert 'Faktura imzolangan' in cl and 'faktura F-12' in cl and 'Faktura yaratish kerak' in cl
    assert world['juma'].post('/buxgalteriya/pq17/klaster/311919351', {'action': 'sign', 'ids': [d1['id']]}).status_code in (302, 403)

    # money: the PQ-17 sum (conditioned kg × real price), not the typed 7800
    with app.app_context():
        from surxon.pricing import money, totals, prices
        m = money()
        assert m[w1]['confirmed'] and m[w1]['amount'] == round(471 * 8066.41) and m[w1]['kond_kg'] == 471
        t = totals(2026)
        assert t['confirmed_n'] == 2 and t['deduction_kg'] == 18
        assert prices()['hand'] == 8066.41                                        # estimate follows the newest PQ-17

    # a PQ-17 that arrives before the trip is received matches when the punkt receives it
    bux.c.post('/buxgalteriya/pq17', data={'files': [(io.BytesIO(pq17_pdf('XH1000000003', '555003')), 'c.pdf')], '_csrf': bux.csrf()},
               content_type='multipart/form-data', headers={'X-Requested-With': 'fetch'})
    w3 = received_trip(app, world, tally, yunus, kg='480', trailer='TL-03', load_no='555003')
    with app.app_context():
        assert q("SELECT waybill_id FROM pq17_docs WHERE code='XH1000000003'", one=True)['waybill_id'] == w3
    assert bux.get(f'/buxgalteriya/pq17/{d1["id"]}.pdf').status_code == 200
    assert world['juma'].get('/buxgalteriya/pq17').status_code == 302            # brigadier: no
    assert 'PQ-17 sverka' in bux.get('/buxgalteriya').get_data(as_text=True)


def test_pq17_by_telegram(app, world, monkeypatch):
    from surxon import telegram_bot
    tally, yunus, ali, st = setup(app, world)
    w1 = received_trip(app, world, tally, yunus, kg='480', load_no='555001')
    sent = []
    monkeypatch.setattr(telegram_bot, 'send', lambda chat, text, *a, **k: sent.append(text))
    monkeypatch.setattr(telegram_bot, 'tg_download', lambda fid: pq17_pdf())
    with app.app_context():
        from surxon.db import get_db
        get_db().execute("UPDATE users SET telegram_id='8080' WHERE username='buxgalter'")
        upd = {'update_id': 9, 'message': {'chat': {'id': 8080, 'type': 'private'}, 'from': {'id': 8080, 'first_name': 'B'},
                                           'document': {'file_id': 'f1', 'file_name': 'PQ17.pdf', 'mime_type': 'application/pdf'}}}
        telegram_bot.process_update(upd)
        assert 'PQ-17 XH1000000001 qabul qilindi' in sent[-1] and '✓ mos' in sent[-1] and 'konditsion 471' in sent[-1]
        telegram_bot.process_update(dict(upd, update_id=10))
        assert 'avval yuklangan' in sent[-1]
        assert q("SELECT waybill_id FROM pq17_docs", one=True)['waybill_id'] == w1


def test_pq17_goes_to_google_sheets(app, world):
    """The Sheets mirror gets a “PQ-17 SVERKA” tab (one row per receipt beside our trip) and the punkt debt lines."""
    tally, yunus, ali, st = setup(app, world)
    received_trip(app, world, tally, yunus, kg='480', load_no='555001')
    world['bux'].c.post('/buxgalteriya/pq17', data={'files': [(io.BytesIO(pq17_pdf()), 'a.pdf')], '_csrf': world['bux'].csrf()},
                        content_type='multipart/form-data')
    with app.app_context():
        from surxon.reporting import sheet_pq17_rows, sheet_summary_rows
        rows = sheet_pq17_rows()
        assert len(rows) == 1 and rows[0][0] == 'XH1000000001' and rows[0][-1] == '✓ mos' and rows[0][13] == 480
        ids = {r[0] for r in sheet_summary_rows()[0]}
        assert {'PQ17_KONDITSION_KG', 'PUNKT_QARZ', 'PUNKT_TOLADI'} <= ids
