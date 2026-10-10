"""SURXON TEZ-PUL: numbered QR talons, paid once in cash at the kassa (synthetic data only)."""
import threading
import time

from surxon.db import get_db, q, scalar
from conftest import Client, make_user, uuid4
from test_hamyon import box_balance, cash_in


def talons(app, admin, count=4, brigade=None, world=None):
    data = {'action': 'create', 'count': str(count)}
    if brigade:
        data['brigadier_id'] = world['b'][brigade]
    r = admin.post('/tezpul/talonlar', data).get_json()
    assert r['ok'], r
    with app.app_context():
        return [dict(t) for t in q('SELECT * FROM tezpul_talons ORDER BY number DESC LIMIT ?', (count,))][::-1]


def qr(t):
    return f'SPX-T:{t["number"]:06d}:{t["code"]}'


def pay(c, code, kg, uuid=None, manual=''):
    return c.post('/tezpul/tolash', {'code': code, 'kg': kg, 'manual_code': manual, 'client_uuid': uuid or uuid4()})


def test_scan_type_kg_pay_once_from_the_existing_kassa(app, world):
    admin, kassa, bux = world['admin'], world['kassa'], world['bux']
    cash_in(bux, '1000000')
    ts = talons(app, admin, 4, 'Juma ota', world)
    assert kassa.get('/').headers['Location'].endswith('/tezpul/kassa')        # the camera screen opens at once
    page = kassa.get('/tezpul/kassa').get_data(as_text=True)
    assert 'Talonni skanerlang' in page and 'PUL BERILDI' in page
    r = kassa.post('/tezpul/talon', {'code': qr(ts[0])}).get_json()
    assert r['ok'] and r['talon']['number'] == f'{ts[0]["number"]:06d}'
    assert r['talon']['status'] == 'BOSH' and r['talon']['brigade'] == 'Juma ota'
    r = pay(kassa, qr(ts[0]), '37,5').get_json()
    assert r['ok'] and r['result']['paid'] and r['result']['amount'] == 75_000      # 37,5 kg × 2 000
    assert box_balance(app) == 1_000_000 - 75_000                                    # out of the existing kassa
    with app.app_context():
        ce = q("SELECT * FROM cash_entries WHERE category='tezpul_pay'", one=True)
        assert ce['amount'] == 75_000 and ce['direction'] == 'OUT' and ce['counterparty'] == f'Talon №{ts[0]["number"]:06d}'
        assert ce['doc_no'].startswith('PAY-')
    # the same talon again (a photocopy, a second scan): red, no money
    r = kassa.post('/tezpul/talon', {'code': qr(ts[0])})
    assert r.status_code == 409 and r.get_json()['refused'] == 'tolangan' and r.get_json()['talon']['amount'] == 75_000
    r = pay(kassa, qr(ts[0]), '37,5')
    assert r.status_code == 409 and r.get_json()['refused'] == 'tolangan'
    assert box_balance(app) == 925_000
    # the same tap sent twice (bad network) is one payment, reported as done
    u = uuid4()
    assert pay(kassa, qr(ts[1]), '20', u).get_json()['result']['amount'] == 40_000
    again = pay(kassa, qr(ts[1]), '20', u).get_json()
    assert again['ok'] and again['result']['already']
    assert box_balance(app) == 885_000
    # a home-made QR with a real number but a guessed code; garbage; a cancelled talon
    r = pay(kassa, f'SPX-T:{ts[2]["number"]:06d}:00000000', '30')
    assert r.status_code == 409 and r.get_json()['refused'] == 'soxta'
    assert kassa.post('/tezpul/talon', {'code': 'hello'}).get_json()['refused'] == 'soxta'
    assert admin.post('/tezpul/talonlar', {'action': 'void', 'number': str(ts[3]['number']), 'reason': 'yo‘qolgan'}).get_json()['ok']
    r = pay(kassa, qr(ts[3]), '10')
    assert r.status_code == 409 and r.get_json()['refused'] == 'bekor'
    # typed by hand: number + the code printed under the QR
    assert pay(kassa, str(ts[2]['number']), '5', manual=ts[2]['code'].lower()).get_json()['result']['amount'] == 10_000
    with app.app_context():
        kinds = sorted(r['kind'] for r in q('SELECT kind FROM tezpul_events'))
        assert kinds == ['bekor', 'soxta', 'soxta', 'takror', 'takror']
    # the rahbar sees it all; the cashier cannot open the panel or print talons
    page = world['rahbar'].get('/tezpul/panel').get_data(as_text=True).replace('\u202f', ' ').replace('\xa0', ' ')
    assert 'Juma ota' in page and '125 000' in page and 'Soxta' in page
    assert kassa.get('/tezpul/panel').status_code == 302 and kassa.get('/tezpul/talonlar').status_code == 302
    x = world['rahbar'].get('/tezpul/panel?format=xlsx')
    assert x.status_code == 200 and x.data[:2] == b'PK'


def test_big_kg_waits_for_the_rahbar_and_the_cashier_cannot_change_it(app, world):
    admin, kassa, bux, rahbar = world['admin'], world['kassa'], world['bux'], world['rahbar']
    cash_in(bux, '2000000')
    t = talons(app, admin, 1)[0]
    r = pay(kassa, qr(t), '400').get_json()                     # one talon, 400 kg: over the 150 kg limit
    assert r['ok'] and r['result']['wait'] and box_balance(app) == 2_000_000
    r = pay(kassa, qr(t), '40')
    assert r.status_code == 422 and 'rahbar tasdig' in r.get_json()['error']
    assert kassa.post('/tezpul/panel/tasdiq', {'number': t['number'], 'kg': '40'}).status_code in (302, 403)
    assert rahbar.post('/tezpul/panel/tasdiq', {'number': t['number'], 'kg': '140'}).get_json()['ok']   # checked the notebook
    r = pay(kassa, qr(t), '999').get_json()                     # the kg typed now is ignored: 140 kg stands
    assert r['result']['kg'] == 140 and r['result']['amount'] == 280_000
    with app.app_context():
        acts = [a['action'] for a in q("SELECT action FROM audit_logs WHERE entity_type='tezpul_talon' ORDER BY id")]
        assert acts == ['TEZPUL_WAIT', 'TEZPUL_APPROVE', 'TEZPUL_PAID']


def test_no_money_in_the_box_and_rate_from_settings(app, world):
    admin, kassa, bux = world['admin'], world['kassa'], world['bux']
    t1, t2 = talons(app, admin, 2)
    r = pay(kassa, qr(t1), '10')
    assert r.status_code == 422 and 'yetarli pul' in r.get_json()['error']
    with app.app_context():
        assert q('SELECT status FROM tezpul_talons WHERE id=?', (t1['id'],), one=True)['status'] == 'BOSH'   # rolled back
    cash_in(bux, '100000')
    assert admin.post('/admin/sozlamalar', {'set_tezpul_rate_kg': '2500'}).get_json()['ok']
    assert pay(kassa, qr(t1), '10').get_json()['result']['amount'] == 25_000
    assert admin.post('/admin/sozlamalar', {'set_tezpul_rate_kg': '2000'}).get_json()['ok']
    assert pay(kassa, qr(t2), '10').get_json()['result']['amount'] == 20_000
    with app.app_context():                                       # the first one keeps the rate it was paid at
        assert q('SELECT rate FROM tezpul_talons WHERE id=?', (t1['id'],), one=True)['rate'] == 2500


def test_two_cashiers_press_at_the_same_second_pay_once(app, world):
    admin, bux = world['admin'], world['bux']
    cash_in(bux, '1000000')
    kassa2 = make_user(app, admin, 'dilnoza', 'cashier')
    t = talons(app, admin, 1)[0]
    results, start = [], threading.Event()

    def go(c):
        start.wait()
        results.append(pay(c, qr(t), '50').status_code)
    th = [threading.Thread(target=go, args=(c,)) for c in (world['kassa'], kassa2, world['kassa'], kassa2)]
    [x.start() for x in th]
    start.set()
    [x.join() for x in th]
    assert sorted(results) == [200, 409, 409, 409], results
    assert box_balance(app) == 1_000_000 - 100_000
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM cash_entries WHERE category='tezpul_pay'") == 1


def test_season_scale_1000_pickers_2000_talons(app, world):
    """1 000 pickers, 2 000 talons (two each): every talon paid once, totals add up, the panel stays quick."""
    admin, kassa, bux = world['admin'], world['kassa'], world['bux']
    cash_in(bux, '500000000')
    t0 = time.time()
    r = admin.post('/tezpul/talonlar', {'action': 'create', 'count': '2000'}).get_json()
    assert r['ok']
    pdf = admin.get('/tezpul/talonlar/1.pdf')
    assert pdf.status_code == 200 and pdf.data[:4] == b'%PDF'
    with app.app_context():
        rows = [dict(x) for x in q('SELECT number, code FROM tezpul_talons ORDER BY number')]
    assert len(rows) == 2000 and len({x['code'] for x in rows}) > 1990
    from surxon import tezpul as T
    from surxon.security import Actor
    with app.app_context():
        cid = scalar("SELECT id FROM users WHERE username='asadbek'")
        actor = Actor(cid, 'cashier')
        total_kg = total = 0
        for i, x in enumerate(rows):                  # picker i//2 brings two talons
            kg = 20 + (i % 7) * 2.5
            res = T.pay(actor, qr(x), kg, client_uuid=uuid4())
            total_kg += kg
            total += res['amount']
        assert scalar("SELECT COUNT(*) FROM tezpul_talons WHERE status='TOLANDI'") == 2000
        assert scalar("SELECT SUM(amount) FROM cash_entries WHERE category='tezpul_pay'") == total == round(total_kg * 2000)
    t1 = time.time()
    page = world['rahbar'].get('/tezpul/panel')
    assert page.status_code == 200 and time.time() - t1 < 3
    assert t1 - t0 < 240
