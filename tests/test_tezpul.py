"""SURXON TEZ-PUL: numbered QR talons — the field clerk writes the kg (Qo‘l terimi), the kassa pays each talon once
(synthetic data only)."""
import threading
import time

from surxon.db import q, scalar
from conftest import make_user, uuid4
from test_dala import open_trip
from test_hamyon import box_balance, cash_in


def talons(app, admin, count=4):
    r = admin.post('/tezpul/talonlar', {'action': 'create', 'count': str(count)}).get_json()
    assert r['ok'], r
    with app.app_context():
        return [dict(t) for t in q('SELECT * FROM tezpul_talons ORDER BY number DESC LIMIT ?', (count,))][::-1]


def qr(t):
    return f'SPX-T:{t["number"]:06d}:{t["code"]}'


def enter(c, lid, code, kg, uuid=None, manual=''):
    return c.post(f'/tezpul/dala/{lid}/saqlash', {'code': code, 'kg': kg, 'manual_code': manual,
                                                 'client_uuid': uuid or uuid4()})


def pay(c, code, uuid=None, manual=''):
    return c.post('/tezpul/tolash', {'code': code, 'manual_code': manual, 'client_uuid': uuid or uuid4()})


def setup(app, world, n=4, rate='2000'):
    admin = world['admin']
    tally = make_user(app, admin, 'terim1', 'tally')
    cash_in(world['bux'], '1000000')
    lid = open_trip(tally, world, rate=rate)
    return tally, lid, talons(app, admin, n)


def test_field_two_steps_then_kassa_two_steps(app, world):
    admin, kassa = world['admin'], world['kassa']
    tally, lid, ts = setup(app, world)
    assert kassa.get('/').headers['Location'].endswith('/tezpul/kassa')        # the cashier's camera opens at once
    r = tally.get(f'/dala/reys/{lid}')                                   # no names in the field: the scanner opens
    assert r.status_code == 302 and r.headers['Location'].endswith(f'/tezpul/dala/{lid}')
    assert 'TALON SKANERI' in tally.get(f'/dala/reys/{lid}?eski=1').get_data(as_text=True)
    page = tally.get(f'/tezpul/dala/{lid}').get_data(as_text=True)
    assert 'Talonni skanerlang' in page and 'SAQLASH' in page and 'Starshi varaqlarini rasmga olish' in page
    # the cashier cannot pay a talon that has no kg yet
    r = kassa.post('/tezpul/talon', {'code': qr(ts[0])})
    assert r.status_code == 409 and r.get_json()['refused'] == 'kgsiz'
    # field: scan → 37,5 → SAQLASH: an ordinary hand weighing of the trip at the trip's rate
    assert tally.post('/tezpul/dala/talon', {'code': qr(ts[0])}).get_json()['ok']
    r = enter(tally, lid, qr(ts[0]), '37,5').get_json()
    assert r['ok'] and r['result']['amount'] == 75_000 and r['count'] == {'n': 1, 'kg': 37.5}
    with app.app_context():
        h = q("""SELECT h.*, w.full_name, w.talon_no FROM harvests h JOIN workers w ON w.id=h.worker_id
                 WHERE h.load_id=?""", (lid,), one=True)
        assert h['method'] == 'hand' and h['kg'] == 37.5 and h['rate'] == 2000 and h['amount'] == 75_000
        assert h['full_name'] == f'Talon {ts[0]["number"]:06d}' and h['talon_no'] == ts[0]['number']
    # the talon card is not offered in the field's name search
    assert not tally.get('/api/workers?q=talon').get_json()['workers']
    # the same talon again in the field: refused (one talon — one weighing)
    r = enter(tally, lid, qr(ts[0]), '40')
    assert r.status_code == 409 and r.get_json()['refused'] == 'kiritilgan'
    # kassa: scan → the amount → PUL BERILDI; money out of the existing kassa as a worker payment
    t = kassa.post('/tezpul/talon', {'code': qr(ts[0])}).get_json()['talon']
    assert t['amount'] == 75_000 and t['kg'] == 37.5
    r = pay(kassa, qr(ts[0])).get_json()
    assert r['ok'] and r['result']['amount'] == 75_000 and r['result']['count'] == 1
    assert box_balance(app) == 1_000_000 - 75_000
    with app.app_context():
        ce = q("SELECT * FROM cash_entries WHERE category='worker_pay'", one=True)
        assert ce['amount'] == 75_000 and ce['worker_id'] == h['worker_id'] and ce['payout_id'] and ce['doc_no'].startswith('PAY-')
        assert q('SELECT status FROM payouts WHERE id=?', (ce['payout_id'],), one=True)['status'] == 'BERILDI'
    # paid: a second scan / a photocopy / the field — red everywhere
    for resp in (kassa.post('/tezpul/talon', {'code': qr(ts[0])}), pay(kassa, qr(ts[0])), enter(tally, lid, qr(ts[0]), '5')):
        assert resp.status_code == 409 and resp.get_json()['refused'] == 'tolangan'
    assert box_balance(app) == 925_000
    # the same tap sent twice (bad network) is one payment
    assert enter(tally, lid, qr(ts[1]), '20').get_json()['ok']
    u = uuid4()
    assert pay(kassa, qr(ts[1]), u).get_json()['result']['amount'] == 40_000
    assert pay(kassa, qr(ts[1]), u).get_json()['result']['already']
    assert box_balance(app) == 885_000
    # a home-made QR (real number, guessed code), garbage, a cancelled talon; typed by hand with its code
    assert enter(tally, lid, f'SPX-T:{ts[2]["number"]:06d}:00000000', '30').get_json()['refused'] == 'soxta'
    assert kassa.post('/tezpul/talon', {'code': 'hello'}).get_json()['refused'] == 'soxta'
    assert admin.post('/tezpul/talonlar', {'action': 'void', 'number': str(ts[3]['number']), 'reason': 'yo‘qolgan'}).get_json()['ok']
    assert enter(tally, lid, qr(ts[3]), '10').get_json()['refused'] == 'bekor'
    assert enter(tally, lid, str(ts[2]['number']), '5', manual=ts[2]['code'].lower()).get_json()['ok']
    r = admin.post('/tezpul/talonlar', {'action': 'void', 'number': str(ts[2]['number']), 'reason': 'x'})
    assert r.status_code == 422 and 'kg yozilgan' in r.get_json()['error']
    # the rahbar's panel; the cashier and the clerk cannot open it
    page = world['rahbar'].get('/tezpul/panel').get_data(as_text=True).replace(' ', ' ').replace('\xa0', ' ')
    assert 'Juma ota' in page and '115 000' in page and 'Soxta' in page and 'Terim1' in page
    assert kassa.get('/tezpul/panel').status_code == 302 and tally.get('/tezpul/panel').status_code == 302
    assert kassa.get('/tezpul/talonlar').status_code == 302
    x = world['rahbar'].get('/tezpul/panel?format=xlsx')
    assert x.status_code == 200 and x.data[:2] == b'PK'
    pdf = admin.get('/tezpul/talonlar/1.pdf')
    assert pdf.status_code == 200 and pdf.data[:4] == b'%PDF'


def test_starshi_brings_his_teams_talons_one_payment(app, world):
    """Two modes at the kassa: one talon — one payment; a starshi's 50 talons scanned one after another — one PUL BERILDI."""
    kassa = world['kassa']
    tally, lid, ts = setup(app, world, 52)
    cash_in(world['bux'], '4000000')                              # 5 000 000 in the box
    r = enter(tally, lid, qr(ts[0]), '25000')                     # over the group limit (20 t): a typo
    assert r.status_code == 422 and 'bitta talonga juda ko‘p' in r.get_json()['error']
    for k, t in enumerate(ts):
        assert enter(tally, lid, qr(t), str(20 + k % 5)).get_json()['ok']
    # mode 1: one person
    assert pay(kassa, qr(ts[0])).get_json()['result']['amount'] == 40_000
    # mode 2: the starshi's 50 talons in one tap (a talon scanned twice counts once)
    codes = [qr(t) for t in ts[1:51]] + [qr(ts[1])]
    expect = sum((20 + k % 5) * 2000 for k in range(1, 51))
    u = uuid4()
    r = kassa.post('/tezpul/tolash', {'code': codes, 'manual_code': [''] * len(codes), 'client_uuid': u}).get_json()
    assert r['ok'] and r['result']['count'] == 50 and r['result']['amount'] == expect
    again = kassa.post('/tezpul/tolash', {'code': codes, 'client_uuid': u}).get_json()   # the same tap sent twice
    assert again['ok'] and again['result']['already'] and again['result']['amount'] == expect
    assert box_balance(app) == 5_000_000 - 40_000 - expect
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM cash_entries WHERE category='worker_pay'") == 51
        assert scalar("SELECT COUNT(DISTINCT pay_batch) FROM tezpul_talons WHERE status='TOLANDI'") == 2
    # one paid talon inside a new batch stops the whole batch (nothing is paid twice, nothing half-paid)
    r = kassa.post('/tezpul/tolash', {'code': [qr(ts[51]), qr(ts[0])], 'client_uuid': uuid4()})
    assert r.status_code == 409 and r.get_json()['talon']['number'] == f'{ts[0]["number"]:06d}'
    with app.app_context():
        assert q('SELECT status FROM tezpul_talons WHERE id=?', (ts[51]['id'],), one=True)['status'] == 'TAYYOR'
    page = world['rahbar'].get('/tezpul/panel').get_data(as_text=True)
    assert '50 talon' in page


def test_kassa_short_and_voided_weighing(app, world):
    admin, kassa = world['admin'], world['kassa']
    tally, lid, ts = setup(app, world, 2)
    cash_out = 1_000_000 - 100_000
    from surxon.db import tx
    from surxon.accounting import book_cash
    from surxon.security import Actor
    with app.app_context():                                       # the box has only 100 000 left
        with tx() as db:
            book_cash(db, Actor(scalar("SELECT id FROM users WHERE username='buxgalter'"), 'accountant'), direction='OUT',
                      category='other_out', amount=cash_out, cashbox_id=1, counterparty='test')
    enter(tally, lid, qr(ts[0]), '60')                            # 120 000 > 100 000 in the box
    r = pay(kassa, qr(ts[0]))
    assert r.status_code == 422 and 'yetarli pul' in r.get_json()['error']
    with app.app_context():
        assert q('SELECT status FROM tezpul_talons WHERE id=?', (ts[0]['id'],), one=True)['status'] == 'TAYYOR'
        hid = q('SELECT harvest_id FROM tezpul_talons WHERE id=?', (ts[0]['id'],), one=True)['harvest_id']
    # the weighing is cancelled (written wrong): the kassa refuses, the field may write it again
    assert admin.post(f'/terim/{hid}/bekor', {'reason': 'xato kg'}).status_code in (200, 302)
    with app.app_context():
        assert q('SELECT voided_at FROM harvests WHERE id=?', (hid,), one=True)['voided_at']
    assert pay(kassa, qr(ts[0])).get_json()['refused'] == 'kgsiz'
    assert enter(tally, lid, qr(ts[0]), '40').get_json()['result']['amount'] == 80_000       # fits in the box now
    assert pay(kassa, qr(ts[0])).get_json()['result']['amount'] == 80_000


def test_two_cashiers_press_at_the_same_second_pay_once(app, world):
    admin = world['admin']
    tally, lid, ts = setup(app, world, 1)
    enter(tally, lid, qr(ts[0]), '50')
    kassa2 = make_user(app, admin, 'dilnoza', 'cashier')
    results, start = [], threading.Event()

    def go(c):
        start.wait()
        results.append(pay(c, qr(ts[0])).status_code)
    th = [threading.Thread(target=go, args=(c,)) for c in (world['kassa'], kassa2, world['kassa'], kassa2)]
    [x.start() for x in th]
    start.set()
    [x.join() for x in th]
    assert sorted(results) == [200, 409, 409, 409], results
    assert box_balance(app) == 1_000_000 - 100_000
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM cash_entries WHERE category='worker_pay'") == 1


def test_offline_resend_is_written_once(app, world):
    tally, lid, ts = setup(app, world, 1)
    u = uuid4()
    assert enter(tally, lid, qr(ts[0]), '25', uuid=u).get_json()['ok']
    again = enter(tally, lid, qr(ts[0]), '25', uuid=u).get_json()           # the phone's queue sends it again
    assert again['ok'] and again['result']['already']
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM harvests WHERE load_id=? AND voided_at IS NULL', (lid,)) == 1


def test_season_scale_1000_pickers_2000_talons(app, world):
    """2 000 talons (1 000 pickers, two each): written in the field, each paid once, money and kg add up."""
    admin, bux = world['admin'], world['bux']
    tally = make_user(app, admin, 'terim1', 'tally')
    cash_in(bux, '500000000')
    t0 = time.time()
    assert admin.post('/tezpul/talonlar', {'action': 'create', 'count': '2000'}).get_json()['ok']
    with app.app_context():
        rows = [dict(x) for x in q('SELECT number, code FROM tezpul_talons ORDER BY number')]
    assert len(rows) == 2000 and len({x['code'] for x in rows}) > 1990
    from surxon import tezpul as T
    from surxon.security import Actor
    trips = [open_trip(tally, world, rate='2000', trailer=tr, field=f) for tr, f in (('TL-01', 'D-04'), ('TL-02', 'D-01'))]
    with app.app_context():
        clerk = Actor(scalar("SELECT id FROM users WHERE username='terim1'"), 'tally')
        cashier = Actor(scalar("SELECT id FROM users WHERE username='asadbek'"), 'cashier')
        total_kg = 0
        for i, x in enumerate(rows):
            kg = 20 + (i % 7) * 2.5
            T.enter(clerk, trips[i % 2], qr(x), kg, client_uuid=uuid4())
            total_kg += kg
        t1 = time.time()
        total = sum(T.pay(cashier, qr(x), client_uuid=uuid4())['amount'] for x in rows)
        assert scalar("SELECT COUNT(*) FROM tezpul_talons WHERE status='TOLANDI'") == 2000
        assert total == round(total_kg * 2000) == scalar("SELECT SUM(amount) FROM cash_entries WHERE category='worker_pay'")
        assert scalar("SELECT SUM(kg) FROM harvests WHERE method='hand' AND voided_at IS NULL") == total_kg
    t2 = time.time()
    page = world['rahbar'].get('/tezpul/panel')
    assert page.status_code == 200 and time.time() - t2 < 3
    print(f'2000 talons: field {t1 - t0:.1f}s, kassa {t2 - t1:.1f}s')


def test_trailer_qr_opens_the_trip_or_its_opening_page(app, world):
    """The QR stuck on a trailer: empty trailer → its opening page with this trailer chosen; open trip → the scanner."""
    admin = world['admin']
    tally = make_user(app, admin, 'terim1', 'tally')
    talons(app, admin, 1)
    with app.app_context():
        e = q("SELECT id, qr_token FROM equipment WHERE code='TL-03'", one=True)
        if not e['qr_token']:
            from surxon.db import get_db
            get_db().execute("UPDATE equipment SET qr_token='tq-test-03' WHERE id=?", (e['id'],))
        tok = q('SELECT qr_token FROM equipment WHERE id=?', (e['id'],), one=True)['qr_token']
    r = tally.get(f'/tq/{tok}')
    assert r.status_code == 302 and r.headers['Location'].endswith(f'/dala/yangi?telashka={e["id"]}')
    page = tally.get(f'/dala/yangi?telashka={e["id"]}').get_data(as_text=True)
    assert 'TL-03 — ochish' in page and 'Kombayn' in page
    lid = open_trip(tally, world, rate='2000', trailer='TL-03')
    r = tally.get(f'/tq/{tok}')
    assert r.status_code == 302 and r.headers['Location'].endswith(f'/dala/reys/{lid}')
    assert tally.get(f'/dala/reys/{lid}').headers['Location'].endswith(f'/tezpul/dala/{lid}')
    assert 'Telashka QR' in tally.get('/dala').get_data(as_text=True)


def test_trailer_qr_after_another_clerks_trip(app, world):
    """The trailer's trip belongs to another clerk: while it is live a clear message, once weighed at the punkt the
    QR opens a new trip — never a 403."""
    admin = world['admin']
    tally = make_user(app, admin, 'terim1', 'tally')
    other = make_user(app, admin, 'terim2', 'tally')
    with app.app_context():
        e = q("SELECT id, qr_token FROM equipment WHERE code='TL-03'", one=True)
        if not e['qr_token']:
            from surxon.db import get_db
            get_db().execute("UPDATE equipment SET qr_token='tq-test-03' WHERE id=?", (e['id'],))
        tok = q('SELECT qr_token FROM equipment WHERE id=?', (e['id'],), one=True)['qr_token']
    lid = open_trip(other, world, rate='2000', trailer='TL-03')
    r = tally.get(f'/tq/{tok}', follow_redirects=True)
    assert r.status_code == 200 and 'boshqa hisobchi ochgan' in r.get_data(as_text=True)
    with app.app_context():
        from surxon.db import get_db
        get_db().execute("UPDATE trailer_loads SET status='TORTILDI' WHERE id=?", (lid,))
    r = tally.get(f'/tq/{tok}')
    assert r.status_code == 302 and r.headers['Location'].endswith(f'/dala/yangi?telashka={e["id"]}')
    assert tally.get(f'/dala/yangi?telashka={e["id"]}').status_code == 200


def test_one_talon_for_a_starshis_whole_team(app, world):
    """Only the starshi gets a talon: his team's total kg on top (5 t), the names in pen underneath — one scan, one kg,
    one payment. Over one person's 250 kg the talon becomes the starshi's by itself."""
    kassa = world['kassa']
    tally, lid, ts = setup(app, world, 2)
    cash_in(world['bux'], '10000000')
    r = enter(tally, lid, qr(ts[0]), '5000').get_json()
    assert r['ok'] and r['result']['amount'] == 10_000_000 and r['count'] == {'n': 1, 'kg': 5000}
    with app.app_context():
        t = q('SELECT * FROM tezpul_talons WHERE id=?', (ts[0]['id'],), one=True)
        assert t['kind'] == 'guruh' and t['kg'] == 5000
        assert scalar('SELECT group_size FROM workers WHERE id=?', (t['worker_id'],)) == 1
        assert scalar("SELECT SUM(kg) FROM harvests WHERE load_id=? AND voided_at IS NULL", (lid,)) == 5000
    assert enter(tally, lid, qr(ts[1]), '40').get_json()['ok']          # an ordinary talon stays one person's
    with app.app_context():
        assert q('SELECT kind FROM tezpul_talons WHERE id=?', (ts[1]['id'],), one=True)['kind'] == 'odam'
    look = kassa.post('/tezpul/talon', {'code': qr(ts[0])}).get_json()['talon']
    assert look['kind'] == 'guruh' and look['amount'] == 10_000_000
    assert pay(kassa, qr(ts[0])).get_json()['result']['amount'] == 10_000_000
    assert pay(kassa, qr(ts[0])).status_code == 409                      # never twice
    assert 'Starshi taloni' in kassa.get('/tezpul/kassa').get_data(as_text=True)


def _sheet_photo(shade=220):
    """A synthetic 'sheet': the names half red, the kg half blue — to see what goes to the reader."""
    import io
    from PIL import Image
    img = Image.new('RGB', (1000, 1400), (220, 30, 30))
    img.paste((30, 30, shade), (500, 0, 1000, 1400))
    buf = io.BytesIO()
    img.save(buf, 'JPEG')
    return buf.getvalue()


def test_starshi_talon_print_sheets(app, world):
    admin = world['admin']
    talons(app, admin, 2)
    r = admin.get('/tezpul/talonlar/1-starshi.pdf?varaq=5')
    assert r.status_code == 200 and r.data[:4] == b'%PDF'
    import re
    assert len(re.findall(rb'/Type\s*/Page[^s]', r.data)) == 10                 # 2 talons × 5 sheets
    assert admin.get('/tezpul/talonlar/1-starshi.pdf?varaq=9').status_code in (302, 422)
    page = admin.get('/tezpul/talonlar').get_data(as_text=True)
    assert 'Starshi taloni' in page and 'Kichik' not in page


def test_sheet_photos_are_kept_and_read_without_the_names(app, world, monkeypatch):
    """Without the AI key: the photo is kept with the talon (finance photo), kg is typed. With it: only the kg part of
    the photo is read; rows that do not add up or are unclear come back flagged; the clerk still saves the kg."""
    import io
    from PIL import Image
    from surxon import talon_ai
    tally, lid, ts = setup(app, world, 1)
    assert 'Starshi varaqlarini rasmga olish' in tally.get(f'/tezpul/dala/{lid}').get_data(as_text=True)
    files = {'photo': [(io.BytesIO(_sheet_photo()), 'v.jpg')]}
    r = tally.c.post('/tezpul/dala/rasm', data={'_csrf': tally.csrf(), 'code': qr(ts[0]), 'sheet': '1', 'cut': '0.5', **files},
                     headers={'X-Requested-With': 'fetch'}, content_type='multipart/form-data').get_json()
    assert r['ok'] and r['ai'] is None and r['ai_on'] is False
    with app.app_context():
        ph = q("SELECT * FROM photos WHERE entity_type='tezpul_talon'", one=True)
        assert ph['category'] == 'payment' and ph['entity_id'] == ts[0]['id']
    # AI on (the reader is replaced here — no real request): the part sent has no red (the names half)
    sent = []

    def fake_ask(data):
        sent.append(Image.open(io.BytesIO(data)).convert('RGB'))
        return {'rows': [{'row': 1, 'before': 37, 'after': 33, 'total': 70, 'unclear': False},
                         {'row': 2, 'before': 45, 'after': 29, 'total': 69, 'unclear': False},
                         {'row': 3, 'before': 30, 'after': 35, 'total': 65, 'unclear': True},
                         {'row': 4, 'before': 20, 'after': None, 'total': None, 'unclear': False},
                         {'row': 5, 'before': None, 'after': None, 'total': None, 'unclear': False}],
                'sheet_total': {'before': None, 'after': None, 'total': 224}, 'note': ''}
    app.config['SURXON'].ANTHROPIC_API_KEY = 'test-key-not-real'
    monkeypatch.setattr(talon_ai, '_ask', fake_ask)
    try:
        assert 'tizim hisoblaydi' in tally.get(f'/tezpul/dala/{lid}').get_data(as_text=True)
        files = {'photo': [(io.BytesIO(_sheet_photo(180)), 'v2.jpg')]}
        r = tally.c.post('/tezpul/dala/rasm', data={'_csrf': tally.csrf(), 'code': qr(ts[0]), 'sheet': '2', 'cut': '0.55', **files},
                         headers={'X-Requested-With': 'fetch'}, content_type='multipart/form-data').get_json()
    finally:
        app.config['SURXON'].ANTHROPIC_API_KEY = ''
    img = sent[0]
    px = [img.getpixel((x, y)) for x in range(0, img.width, 20) for y in range(0, img.height, 50)]
    assert all(p[0] < 120 for p in px)                                  # no red: the names half never left the server
    ai = r['ai']
    flags = {x['row']: x['flag'] for x in ai['rows']}
    assert flags == {1: '', 2: 'sum', 3: 'unclear', 4: 'half'} and ai['problems'] == 3
    assert ai['computed'] == 70 + 69 + 65 + 20 and ai['total_ok'] is True
    # the kg is still written by the clerk (here: the checked sum) with the sheets as confirmed and the head count
    import json as _json
    with app.app_context():
        pids = [x['id'] for x in q("SELECT id FROM photos WHERE entity_type='tezpul_talon' ORDER BY id")]
    sheets = [{'sheet': 1, 'photo_id': pids[0], 'kg': 0.0, 'rows': []},
              {'sheet': 2, 'photo_id': pids[1], 'kg': 224, 'rows': [{'row': x['row'], 'before': x['before'], 'after': x['after'],
                                                                   'total': x['total']} for x in ai['rows']]},
              {'sheet': 'x'}]                                                  # junk is dropped
    r = tally.post(f'/tezpul/dala/{lid}/saqlash', {'code': qr(ts[0]), 'kg': '224', 'client_uuid': uuid4(),
                                                   'sheets': _json.dumps(sheets), 'odam': '5'}).get_json()
    assert r['ok']
    with app.app_context():
        t = q('SELECT kind, people FROM tezpul_talons WHERE id=?', (ts[0]['id'],), one=True)
        assert t['kind'] == 'guruh' and t['people'] == 5                  # a starshi talon even under 250 kg
        assert [s['sheet'] for s in q('SELECT sheet FROM tezpul_sheets ORDER BY sheet')] == [1, 2]
        jobs = q("SELECT * FROM outbox WHERE kind='tezpul_starshi'")
        assert jobs and all('tezpul_starshi' in j['payload_json'] for j in jobs)
    pdf = world['rahbar'].get(f'/tezpul/talon/{ts[0]["number"]}/hisobot.pdf')
    assert pdf.status_code == 200 and pdf.data[:4] == b'%PDF'
    assert tally.get(f'/tezpul/talon/{ts[0]["number"]}/hisobot.pdf').status_code in (302, 403)
    from surxon import tezpul as T
    assert [T.sheets_needed(n) for n in (1, 19, 20, 30, 40, 41, 100)] == [1, 1, 2, 2, 2, 3, 5]
    # after the kg is written no more sheets can be added to that talon
    files = {'photo': [(io.BytesIO(_sheet_photo(150)), 'v3.jpg')]}
    r = tally.c.post('/tezpul/dala/rasm', data={'_csrf': tally.csrf(), 'code': qr(ts[0]), 'sheet': '3', **files},
                     headers={'X-Requested-With': 'fetch'}, content_type='multipart/form-data')
    assert r.status_code == 409
    page = world['rahbar'].get('/tezpul/panel').get_data(as_text=True)
    assert 'varaq rasmlari' in page and '/media/' in page and 'PDF hisobot' in page


def test_reader_check_rules():
    from surxon.talon_ai import check
    r = check({'rows': [{'row': 2, 'before': 10, 'after': 5, 'total': 15, 'unclear': False},
                        {'row': 2, 'before': 1, 'after': 1, 'total': 2, 'unclear': False},       # duplicate row: dropped
                        {'row': 1, 'before': '7,5', 'after': None, 'total': 7.5, 'unclear': False}],
               'sheet_total': {'before': None, 'after': None, 'total': 30}, 'note': 'x'})
    assert [x['row'] for x in r['rows']] == [1, 2] and r['computed'] == 22.5 and r['total_ok'] is False


def test_ai_key_pasted_on_the_integrations_page(app, world, monkeypatch):
    """When nobody can reach the server's .env, the admin pastes the Claude key on Admin → Integratsiyalar: it is checked,
    kept in a private file (not in the database, the audit or any page — only its last 4 characters show); .env wins."""
    import os
    from surxon import talon_ai
    admin = world['admin']
    page = admin.get('/admin/integratsiyalar').get_data(as_text=True)
    assert 'ai_key_save' in page and 'Ulanmagan — kg qo‘lda yoziladi' in page
    monkeypatch.setattr(talon_ai, '_verify', lambda k: k.endswith('good'))   # no real request to Anthropic
    good = 'sk-ant-api03-' + 'x' * 40 + '-good'
    for wrong in ('salom', 'sk-ant-api03-' + 'y' * 40 + '-bad'):          # not a key / Anthropic refuses it
        admin.post('/admin/integratsiyalar', {'action': 'ai_key_save', 'ai_key': wrong}, json_resp=False)
        with app.app_context():
            assert not talon_ai.enabled()
    world['bux'].post('/admin/integratsiyalar', {'action': 'ai_key_save', 'ai_key': good}, json_resp=False)
    with app.app_context():
        assert not talon_ai.enabled()                                       # only the admin
    admin.post('/admin/integratsiyalar', {'action': 'ai_key_save', 'ai_key': f'  {good}\n'}, json_resp=False)
    with app.app_context():
        assert talon_ai.enabled() and talon_ai.api_key() == good and talon_ai.source() == 'site'
        path = talon_ai._key_path()
        assert path.parent == app.config['SURXON'].DATA_DIR and oct(os.stat(path).st_mode & 0o777) == '0o600'
        assert app.config['SURXON'].UPLOAD_DIR not in path.parents          # not in the photo backups
        dump = '\n'.join(str(tuple(r)) for t in ('settings', 'audit_logs') for r in q(f'SELECT * FROM {t}'))
        assert good not in dump and '…good' in dump
    page = admin.get('/admin/integratsiyalar').get_data(as_text=True)
    assert good not in page and 'Ulangan …good' in page and 'Kalitni o‘chirish' in page
    # the server's .env key comes first; the page then has no paste box
    app.config['SURXON'].ANTHROPIC_API_KEY = 'sk-ant-api03-' + 'e' * 40 + '-envk'
    try:
        with app.app_context():
            assert talon_ai.source() == 'env' and talon_ai.api_key().endswith('envk')
        page = admin.get('/admin/integratsiyalar').get_data(as_text=True)
        assert 'Ulangan (server .env)' in page and 'name="ai_key"' not in page
    finally:
        app.config['SURXON'].ANTHROPIC_API_KEY = ''
    admin.post('/admin/integratsiyalar', {'action': 'ai_key_clear'}, json_resp=False)
    with app.app_context():
        assert not talon_ai.enabled() and not talon_ai._key_path().exists()


def test_trailers_close_with_their_kg_and_the_evening_count_pays(app, world):
    """Hand trailers close during the day with their own kg (no talon yet); in the evening the hisobchi — or the
    kassir — counts the starshi's talon for the brigade; the kassir pays it. Trailer kg, talon kg and punkt kg stay
    three separate numbers (nothing counted twice)."""
    from conftest import jpeg
    admin, kassa = world['admin'], world['kassa']
    tally, lid, ts = setup(app, world, 3)
    cash_in(world['bux'], '6000000')
    r = tally.post(f'/dala/reys/{lid}/yopish', files={'photos': jpeg()})
    assert r.status_code in (200, 422) and not (r.get_json() or {}).get('ok')                 # no kg: refused
    assert 'Telashkadagi paxta, kg' in tally.get(f'/dala/reys/{lid}/yopish').get_data(as_text=True)
    assert tally.post(f'/dala/reys/{lid}/yopish', {'kg': '1300'}, files={'photos': jpeg((10, 20, 30))}).get_json()['ok']
    lid2 = open_trip(tally, world, rate='2000', trailer='TL-02')                                  # after lunch
    assert tally.post(f'/dala/reys/{lid2}/yopish', {'kg': '1250'}, files={'photos': jpeg((40, 50, 60))}).get_json()['ok']
    with app.app_context():
        assert [x['internal_kg'] for x in q('SELECT internal_kg FROM trailer_loads ORDER BY id')] == [1300, 1250]
        bid = scalar('SELECT brigadier_id FROM trailer_loads WHERE id=?', (lid,))
    # the evening count: the hisobchi's screen and the kassir's screen both open it
    page = tally.get('/tezpul/kechki').get_data(as_text=True)
    assert 'KECHKI HISOB' in page and '2 telashka' in page
    assert kassa.get('/tezpul/kechki').status_code == 200
    r = tally.post('/tezpul/kechki/saqlash', {'brigada': bid, 'code': qr(ts[0]), 'kg': '2480', 'client_uuid': uuid4(),
                                              'odam': '30', 'sheets': '[]'}).get_json()
    assert r['ok'] and r['result']['amount'] == 2480 * 2000
    with app.app_context():
        h = q('SELECT * FROM harvests WHERE voided_at IS NULL', one=True)
        assert h['load_id'] == lid2 and h['kg'] == 2480                    # the brigade's last trailer of the day…
        assert scalar('SELECT internal_kg FROM trailer_loads WHERE id=?', (lid2,)) == 1250   # …keeps its own kg
        t = q('SELECT kind, people FROM tezpul_talons WHERE id=?', (ts[0]['id'],), one=True)
        assert t['kind'] == 'guruh' and t['people'] == 30
    assert pay(kassa, qr(ts[0])).get_json()['result']['amount'] == 2480 * 2000
    # the kassir can count too (the panel then shows “wrote and paid himself” in red)
    r = kassa.post('/tezpul/kechki/saqlash', {'brigada': bid, 'code': qr(ts[1]), 'kg': '300', 'client_uuid': uuid4(),
                                              'odam': '4'}).get_json()
    assert r['ok']
    assert pay(kassa, qr(ts[1])).get_json()['ok']
    page = world['rahbar'].get('/tezpul/panel').get_data(as_text=True)
    assert 'Telashkalar (dala)' in page, 'col'
    import re
    assert re.search(r'<td class="num">2\W550 <span class="badge b-red"', page)      # trailers 1300 + 1250; talons 2780
    assert 'o‘zi kg yozib, o‘zi pul bergan' in page
    # no hand trailer for a brigade today: refused
    r = tally.post('/tezpul/kechki/saqlash', {'brigada': 99999, 'code': qr(ts[2]), 'kg': '100', 'client_uuid': uuid4()})
    assert r.status_code == 422 and 'brigadani tanlang' in r.get_json()['error']
