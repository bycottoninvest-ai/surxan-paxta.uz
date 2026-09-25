"""“Mening kassam” phone flow: pay a worker, expense, income, today's list, corrections, double taps, stale figures."""
import threading

from surxon.db import get_db, q, scalar
from conftest import Client, make_user, open_load, uuid4


def add(client, lid, name, kg):
    r = client.post('/terim', {'load_id': lid, 'method': 'hand', 'worker_name': name, 'kg': kg, 'client_uuid': uuid4(),
                               'confirm_duplicate': '1'}).get_json()
    assert r['ok'], r


def wid(app, name):
    with app.app_context():
        return q('SELECT id FROM workers WHERE full_name=?', (name,), one=True)['id']


def box_balance(app, box=1):
    from surxon.accounting import box_balance as bb
    with app.app_context():
        return bb(get_db(), box)


def debt(app, worker_id):
    from surxon.wallet import worker_figures
    with app.app_context():
        return worker_figures(get_db(), scalar('SELECT MAX(year) FROM seasons'), worker_id)['debt']


def cash_in(client, amount, source='Direktor'):
    r = client.post('/hamyon/kirim/tekshir', {'amount': amount, 'source': source}).get_json()
    assert r['ok'], r
    r = client.post('/hamyon/kirim/tasdiq', {'amount': amount, 'source': source, 'expect': r['expect'],
                                             'client_uuid': uuid4()}).get_json()
    assert r['ok'], r
    return r


def pay(client, worker_id, amount, uuid=None, expect=None):
    if expect is None:
        chk = client.post(f'/hamyon/tolov/{worker_id}/tekshir', {'amount': amount}).get_json()
        assert chk['ok'], chk
        expect = chk['expect']
    return client.post(f'/hamyon/tolov/{worker_id}/tasdiq', {'amount': amount, 'expect': expect,
                                                              'client_uuid': uuid or uuid4()})


def cash_id_from(resp):
    return int(resp.get_json()['redirect'].split('/amal/')[1].split('?')[0])


def setup(app, world):
    admin, juma, nurim = world['admin'], world['juma'], world['nurim']
    admin.post('/admin/sozlamalar', {'set_worker_rate_hand': '1500'})
    lid = open_load(juma, world)
    for kg in ('100', '100', '100'):
        add(juma, lid, 'Akmal Karimov', kg)          # 450 000 in Juma ota's brigade
    lid2 = open_load(nurim, world, trailer='TL-02', field='D-01')
    add(nurim, lid2, 'Akmal Karimov', '100')         # the same person in another brigade: 150 000
    add(nurim, lid2, 'Botir Aliyev', '100')
    return wid(app, 'Akmal Karimov'), wid(app, 'Botir Aliyev')


def test_one_card_per_worker_across_brigades_and_pay_in_two_steps(app, world):
    bux = world['bux']
    akmal, _ = setup(app, world)
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM workers WHERE full_name='Akmal Karimov'") == 1   # no second card
    page = bux.get(f'/hamyon/tolov/{akmal}').get_data(as_text=True).replace(' ', ' ').replace('\xa0', ' ')
    assert '600 000' in page and 'Juma ota' in page and 'Nurim ota' in page     # earned in both brigades together
    cash_in(bux, '1 000 000')
    # Tekshirish saves nothing
    chk = bux.post(f'/hamyon/tolov/{akmal}/tekshir', {'amount': '100 000'}).get_json()
    assert chk['ok'] and '500' in chk['html'] and debt(app, akmal) == 600_000
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM payouts') == 0
    # confirm twice with the same key (double tap / resent request) → one payment
    key = uuid4()
    r1 = pay(bux, akmal, '100 000', uuid=key, expect=chk['expect']).get_json()
    r2 = pay(bux, akmal, '100 000', uuid=key, expect=chk['expect']).get_json()
    assert r1['ok'] and r2['ok'] and r1['redirect'] == r2['redirect']
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM cash_entries WHERE category='worker_pay'") == 1
        p = q('SELECT * FROM payouts', one=True)
        assert p['status'] == 'BERILDI' and p['prepared_by'] == p['paid_by'] and p['cash_entry_id']
    assert debt(app, akmal) == 500_000 and box_balance(app) == 900_000
    # confirming against figures that changed since the check → refused with the fresh figures
    stale = pay(bux, akmal, '50 000', expect=chk['expect'])
    assert stale.status_code == 409 and stale.get_json()['stale'] and '450' in stale.get_json()['html']
    # more than the debt, or more than the cash box → refused
    over = bux.post(f'/hamyon/tolov/{akmal}/tekshir', {'amount': '600 000'}).get_json()
    assert not over['ok'] and 'qarz' in over['error']
    cash_in(bux, '10 000')   # box 910 000; debt 500 000 → full pay ok
    assert pay(bux, akmal, '500 000').get_json()['ok']
    assert debt(app, akmal) == 0 and box_balance(app) == 410_000
    # the entry page and its PDF
    with app.app_context():
        cid = scalar("SELECT id FROM cash_entries WHERE category='worker_pay' ORDER BY id DESC LIMIT 1")
    assert 'Kassa hujjati' in bux.get(f'/hamyon/amal/{cid}').get_data(as_text=True)
    pdf = bux.get(f'/hamyon/amal/{cid}.pdf')
    assert pdf.status_code == 200 and pdf.data[:4] == b'%PDF'


def test_box_short_refuses_payment(app, world):
    bux = world['bux']
    akmal, _ = setup(app, world)
    cash_in(bux, '100 000')
    r = bux.post(f'/hamyon/tolov/{akmal}/tekshir', {'amount': '200 000'}).get_json()
    assert not r['ok'] and 'yetarli pul yo‘q' in r['error']


def test_two_payments_at_the_same_moment_never_exceed_the_debt(app, world):
    bux = world['bux']
    akmal, _ = setup(app, world)
    cash_in(bux, '5 000 000')
    other = Client(app, 'buxgalter', 'Worker2026x')
    results, gate = [], threading.Barrier(2)

    def go(c):
        gate.wait()
        r = c.post(f'/hamyon/tolov/{akmal}/tasdiq', {'amount': '600 000', 'client_uuid': uuid4()})
        results.append(r.get_json()['ok'])
    ts = [threading.Thread(target=go, args=(c,)) for c in (bux, other)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [False, True]
    assert debt(app, akmal) == 0 and box_balance(app) == 4_400_000


def test_expense_and_income(app, world):
    bux, kassa = world['bux'], world['kassa']
    cash_in(bux, '500 000', source='Direktor')
    # wages are never written as an expense (they would be counted twice)
    r = bux.post('/hamyon/xarajat/tekshir', {'category': 'Ish haqi', 'amount': '10 000', 'payer': 'Ali'}).get_json()
    assert not r['ok']
    r = bux.post('/hamyon/xarajat/tekshir', {'category': 'Yoqilg‘i', 'amount': '200 000', 'payer': ''}).get_json()
    assert not r['ok'] and 'Kimga' in r['error']
    chk = bux.post('/hamyon/xarajat/tekshir', {'category': 'Yoqilg‘i', 'amount': '200 000', 'payer': 'AYOQSH'}).get_json()
    assert chk['ok'] and '300' in chk['html']
    data = {'category': 'Yoqilg‘i', 'amount': '200 000', 'payer': 'AYOQSH', 'expect': chk['expect'],
            'equipment_id': world['eq']['T-01'], 'client_uuid': uuid4()}
    r1, r2 = bux.post('/hamyon/xarajat/tasdiq', data).get_json(), bux.post('/hamyon/xarajat/tasdiq', data).get_json()
    assert r1['ok'] and r2['ok']
    with app.app_context():
        e = q('SELECT * FROM expenses WHERE voided_at IS NULL', one=True)
        assert scalar('SELECT COUNT(*) FROM expenses') == 1
        assert (e['category'], e['amount'], e['payer'], e['status'], e['equipment_id']) == \
            ('Yoqilg‘i', 200_000, 'AYOQSH', 'TASDIQLANGAN', world['eq']['T-01'])
    assert box_balance(app) == 300_000
    # the cashier writes one too: it waits for the accountant; the cashier has no “Kassaga kirim”
    chk = kassa.post('/hamyon/xarajat/tekshir', {'category': 'Ovqat', 'amount': '50 000', 'payer': 'Oshxona'}).get_json()
    assert chk['ok']
    assert kassa.post('/hamyon/xarajat/tasdiq', {'category': 'Ovqat', 'amount': '50 000', 'payer': 'Oshxona',
                                                 'expect': chk['expect'], 'client_uuid': uuid4()}).get_json()['ok']
    with app.app_context():
        assert q("SELECT status FROM expenses WHERE category='Ovqat'", one=True)['status'] == 'TEKSHIRILMAGAN'
    assert kassa.post('/hamyon/kirim/tekshir', {'amount': '1', 'source': 'Direktor'}).status_code == 403
    # today's list shows every operation with its state
    today = bux.get('/hamyon/bugun').get_data(as_text=True)
    assert 'AYOQSH' in today and 'Oshxona' in today and 'Buxgalter tekshiradi' in today and 'Tasdiqlangan' in today


def test_accountant_corrects_amount_person_and_duplicate(app, world):
    bux = world['bux']
    akmal, botir = setup(app, world)
    cash_in(bux, '2 000 000')
    cid = cash_id_from(pay(bux, akmal, '150 000'))          # should have been 100 000
    chk = bux.post(f'/hamyon/amal/{cid}/tuzatish/tekshir', {'reason': 'Noto‘g‘ri summa', 'new_amount': '100 000'}).get_json()
    assert chk['ok'] and '150' in chk['html'] and '100' in chk['html']
    r = bux.post(f'/hamyon/amal/{cid}/tuzatish/tasdiq', {'reason': 'Noto‘g‘ri summa', 'new_amount': '100 000',
                                                          'expect': chk['expect'], 'client_uuid': uuid4()}).get_json()
    assert r['ok'], r
    new_cid = cash_id_from_redirect = int(r['redirect'].split('/amal/')[1])
    with app.app_context():
        old = q('SELECT * FROM cash_entries WHERE id=?', (cid,), one=True)
        new = q('SELECT * FROM cash_entries WHERE id=?', (new_cid,), one=True)
        assert old['voided_at'] and old['amount'] == 150_000 and 'Noto‘g‘ri summa' in old['void_reason']   # kept
        assert new['amount'] == 100_000 and not new['voided_at'] and new['worker_id'] == akmal
        c = q('SELECT * FROM cash_corrections', one=True)
        assert c['status'] == 'BAJARILDI' and c['new_cash_entry_id'] == new_cid and c['decided_by']
        assert scalar("SELECT COUNT(*) FROM audit_logs WHERE entity_type='cash_entry' AND action='CORRECT'") == 1
    assert debt(app, akmal) == 500_000 and box_balance(app) == 1_900_000
    # the voided row cannot be corrected again; the replacement can
    assert bux.post(f'/hamyon/amal/{cid}/tuzatish/tekshir', {'reason': 'Takroriy to‘lov'}).get_json()['ok'] is False
    # wrong person: the money went to Botir, not Akmal
    chk = bux.post(f'/hamyon/amal/{new_cid}/tuzatish/tekshir', {'reason': 'Noto‘g‘ri odam', 'new_worker_id': botir}).get_json()
    assert chk['ok'] and 'Botir' in chk['html']
    assert bux.post(f'/hamyon/amal/{new_cid}/tuzatish/tasdiq', {'reason': 'Noto‘g‘ri odam', 'new_worker_id': botir,
                                                                 'expect': chk['expect'], 'client_uuid': uuid4()}).get_json()['ok']
    assert debt(app, akmal) == 600_000 and debt(app, botir) == 50_000 and box_balance(app) == 1_900_000
    # a duplicate: cancelled, money back in the box figure, original kept
    dup = cash_id_from(pay(bux, akmal, '10 000'))
    chk = bux.post(f'/hamyon/amal/{dup}/tuzatish/tekshir', {'reason': 'Takroriy to‘lov'}).get_json()
    assert chk['ok'] and 'bekor' in chk['html']
    assert bux.post(f'/hamyon/amal/{dup}/tuzatish/tasdiq', {'reason': 'Takroriy to‘lov', 'expect': chk['expect'],
                                                             'client_uuid': uuid4()}).get_json()['ok']
    assert debt(app, akmal) == 600_000 and box_balance(app) == 1_900_000
    # “Boshqa sabab” needs a short text
    other = cash_id_from(pay(bux, akmal, '20 000'))
    r = bux.post(f'/hamyon/amal/{other}/tuzatish/tekshir', {'reason': 'Boshqa sabab', 'new_amount': '15 000'}).get_json()
    assert not r['ok']
    assert 'Tuzatishlar' in bux.get('/hamyon/tuzatishlar').get_data(as_text=True)
    assert world['rahbar'].get('/hamyon/tuzatishlar').status_code == 200          # the director sees them


def test_cashier_requests_correction_accountant_decides(app, world):
    bux, kassa = world['bux'], world['kassa']
    cash_in(bux, '300 000')
    chk = kassa.post('/hamyon/xarajat/tekshir', {'category': 'Ovqat', 'amount': '80 000', 'payer': 'Oshxona'}).get_json()
    r = kassa.post('/hamyon/xarajat/tasdiq', {'category': 'Ovqat', 'amount': '80 000', 'payer': 'Oshxona',
                                              'expect': chk['expect'], 'client_uuid': uuid4()}).get_json()
    cid = int(r['redirect'].split('/amal/')[1].split('?')[0])
    chk = kassa.post(f'/hamyon/amal/{cid}/tuzatish/tekshir', {'reason': 'Noto‘g‘ri xarajat turi',
                                                               'new_category': 'Transport'}).get_json()
    assert chk['ok'] and 'Buxgalter tasdiqlagandan' in chk['html']
    r = kassa.post(f'/hamyon/amal/{cid}/tuzatish/tasdiq', {'reason': 'Noto‘g‘ri xarajat turi', 'new_category': 'Transport',
                                                            'expect': chk['expect'], 'client_uuid': uuid4()}).get_json()
    assert r['ok'] and 'so‘rovi' in r['message']
    with app.app_context():
        c = q('SELECT * FROM cash_corrections', one=True)
        assert c['status'] == 'KUTILMOQDA' and not q('SELECT voided_at FROM cash_entries WHERE id=?', (cid,), one=True)['voided_at']
    # the cashier cannot approve their own request
    assert kassa.post(f'/hamyon/tuzatish/{c["id"]}/tasdiq', {}).status_code in (302, 403)
    assert 'Tuzatishni tasdiqlash' in bux.get(f'/hamyon/amal/{cid}').get_data(as_text=True)
    assert bux.post(f'/hamyon/tuzatish/{c["id"]}/tasdiq', {}).get_json()['ok']
    assert bux.post(f'/hamyon/tuzatish/{c["id"]}/tasdiq', {}).get_json()['ok']          # double tap: once
    with app.app_context():
        assert q("SELECT category FROM expenses WHERE voided_at IS NULL", one=True)['category'] == 'Transport'
        assert scalar('SELECT COUNT(*) FROM expenses') == 2 and scalar("SELECT COUNT(*) FROM expenses WHERE voided_at IS NOT NULL") == 1
    assert box_balance(app) == 220_000
    # a rejected request changes nothing
    chk = kassa.post('/hamyon/xarajat/tekshir', {'category': 'Ovqat', 'amount': '5 000', 'payer': 'Non'}).get_json()
    r = kassa.post('/hamyon/xarajat/tasdiq', {'category': 'Ovqat', 'amount': '5 000', 'payer': 'Non',
                                              'expect': chk['expect'], 'client_uuid': uuid4()}).get_json()
    cid2 = int(r['redirect'].split('/amal/')[1].split('?')[0])
    kassa.post(f'/hamyon/amal/{cid2}/tuzatish/tasdiq', {'reason': 'Takroriy to‘lov', 'client_uuid': uuid4()})
    with app.app_context():
        c2 = q('SELECT id FROM cash_corrections WHERE cash_entry_id=?', (cid2,), one=True)
    assert bux.post(f'/hamyon/tuzatish/{c2["id"]}/rad', {'note': 'to‘g‘ri yozilgan'}).get_json()['ok']
    assert box_balance(app) == 215_000


def test_closed_day_is_not_rewritten(app, world):
    bux = world['bux']
    akmal, _ = setup(app, world)
    cash_in(bux, '1 000 000')
    cid = cash_id_from(pay(bux, akmal, '100 000'))
    with app.app_context():
        from surxon.utils import today_str
        get_db().execute('''INSERT INTO cash_days(cashbox_id, day, opening, inflow, outflow, system_balance, counted, diff,
                              closed_by, closed_at) VALUES (1, ?, 0, 1000000, 100000, 900000, 900000, 0, 1, ?)''',
                         (today_str(), today_str()))
    r = bux.post(f'/hamyon/amal/{cid}/tuzatish/tekshir', {'reason': 'Noto‘g‘ri summa', 'new_amount': '90 000'}).get_json()
    assert not r['ok'] and 'yopilgan' in r['error']


def test_screens_and_access(app, world):
    bux, kassa, admin = world['bux'], world['kassa'], world['admin']
    akmal, _ = setup(app, world)
    cash_in(bux, '1 000 000')
    # both money logins land on “Mening kassam”
    assert bux.get('/').headers['Location'].endswith('/hamyon')
    assert kassa.get('/').headers['Location'].endswith('/hamyon')
    for url in ('/hamyon', '/hamyon/tolov', f'/hamyon/tolov/{akmal}', '/hamyon/xarajat', '/hamyon/kirim', '/hamyon/bugun',
                '/hamyon/tuzatishlar'):
        assert bux.get(url).status_code == 200, url
    home = kassa.get('/hamyon').get_data(as_text=True)
    assert 'Ishchiga pul berish' in home and 'Xarajat yozish' in home and 'Kassaga kirim' not in home
    # cashier: prepared orders only, hands one out in two steps
    r = bux.post('/buxgalteriya/tolov', {'kind': 'worker', 'target_id': akmal, 'amount': '200 000',
                                         'client_uuid': uuid4()}).get_json()
    assert r['ok']
    assert 'Akmal Karimov' in kassa.get('/hamyon/tolov').get_data(as_text=True)
    page = kassa.get(f'/hamyon/buyruq/{r["payout_id"]}').get_data(as_text=True)
    assert 'berildi — tasdiqlash' in page
    import re
    expect = re.search(r'name="expect" value="([^"]+)"', page).group(1)
    a = kassa.post(f'/hamyon/buyruq/{r["payout_id"]}/tasdiq', {'expect': expect}).get_json()
    b = kassa.post(f'/hamyon/buyruq/{r["payout_id"]}/tasdiq', {'expect': expect}).get_json()
    assert a['ok'] and b['ok'] and 'ikkinchi marta berilmadi' in b['message']
    assert debt(app, akmal) == 400_000
    # a cashier tied to box 1 does not see another box's operations
    admin.post('/admin/kassalar', {'name': 'Dala kassasi'})
    with app.app_context():
        get_db().execute("UPDATE users SET cashbox_id=1 WHERE username='asadbek'")
        from surxon.accounting import book_cash
        from surxon.security import Actor
        from surxon.db import tx
        uid = scalar("SELECT id FROM users WHERE username='buxgalter'")
        with tx() as db:
            other_cid, _ = book_cash(db, Actor(uid, 'accountant'), direction='IN', category='income', amount=5000,
                                     cashbox_id=2, source='Direktor')
    assert kassa.get(f'/hamyon/amal/{other_cid}').status_code == 404
    assert bux.get(f'/hamyon/amal/{other_cid}').status_code == 200
    # field and punkt logins never reach the money screens
    tally = make_user(app, admin, 'mirjalol', 'tally')
    assert tally.get('/hamyon').status_code == 302 and tally.get('/hamyon/bugun').status_code == 302
    assert world['juma'].get('/hamyon/xarajat').status_code in (200, 302)   # brigadier: only if allowed to write expenses
