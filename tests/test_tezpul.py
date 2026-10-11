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


def enter(c, lid, code, kg, group=False, uuid=None, manual=''):
    return c.post(f'/tezpul/dala/{lid}/saqlash', {'code': code, 'kg': kg, 'group': '1' if group else '', 'manual_code': manual,
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
    assert 'Talonni skanerlang' in page and 'SAQLASH' in page and 'Starshi' in page
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
    assert t['amount'] == 75_000 and t['kg'] == 37.5 and t['kind'] == 'odam'
    r = pay(kassa, qr(ts[0])).get_json()
    assert r['ok'] and r['result']['amount'] == 75_000 and r['result']['doc_no'].startswith('PAY-')
    assert box_balance(app) == 1_000_000 - 75_000
    with app.app_context():
        ce = q("SELECT * FROM cash_entries WHERE category='worker_pay'", one=True)
        assert ce['amount'] == 75_000 and ce['worker_id'] == h['worker_id'] and ce['payout_id']
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


def test_starshi_talon_for_a_whole_team(app, world):
    tally, lid, ts = setup(app, world, 2)
    r = enter(tally, lid, qr(ts[0]), '800')                       # one person cannot have 800 kg
    assert r.status_code == 422 and 'Starshi' in r.get_json()['error']
    r = enter(tally, lid, qr(ts[0]), '800', group=True).get_json()   # the starshi's team, one talon
    assert r['ok'] and r['result']['kind'] == 'guruh' and r['result']['amount'] == 1_600_000
    cash_in(world['bux'], '1000000')
    t = world['kassa'].post('/tezpul/talon', {'code': qr(ts[0])}).get_json()['talon']
    assert t['kind'] == 'guruh' and t['amount'] == 1_600_000
    assert pay(world['kassa'], qr(ts[0])).get_json()['result']['amount'] == 1_600_000


def test_kassa_short_and_voided_weighing(app, world):
    admin, kassa = world['admin'], world['kassa']
    tally, lid, ts = setup(app, world, 2)
    enter(tally, lid, qr(ts[0]), '600', group=True)               # 1 200 000 > 1 000 000 in the box
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
    assert enter(tally, lid, qr(ts[0]), '60').get_json()['result']['amount'] == 120_000
    assert pay(kassa, qr(ts[0])).get_json()['result']['amount'] == 120_000


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
