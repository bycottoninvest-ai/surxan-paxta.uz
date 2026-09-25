"""Buxgalteriya va kassa: every rule from the owner's spec, checked with real numbers (no mocks of the money logic)."""
import pytest

from surxon.db import get_db, q, scalar
from conftest import make_user, open_load, uuid4


def add(client, lid, name, kg):
    return client.post('/terim', {'load_id': lid, 'method': 'hand', 'worker_name': name, 'kg': kg, 'client_uuid': uuid4(),
                                  'confirm_duplicate': '1'}).get_json()


def flat(html):
    import re
    return ''.join(re.sub(r'<[^>]+>', '', html).split()).replace('\u202f', '').replace('\xa0', '')


def year_of(app):
    with app.app_context():
        return scalar('SELECT MAX(year) FROM seasons')


def wid(app, name):
    with app.app_context():
        return q('SELECT id FROM workers WHERE full_name=?', (name,), one=True)['id']


def income(bux, amount, source='Direktor'):
    r = bux.post('/buxgalteriya/kirim', {'amount': amount, 'source': source, 'client_uuid': uuid4()}).get_json()
    assert r['ok'], r
    return r['doc_no']


def balance(app, worker_id):
    from surxon.accounting import worker_balances
    with app.app_context():
        return worker_balances(year_of(app), worker_id=worker_id)[0]


def prepare(bux, worker_id, amount=None, purpose='pay'):
    data = {'kind': 'worker', 'target_id': worker_id, 'purpose': purpose, 'client_uuid': uuid4()}
    if amount:
        data['amount'] = amount
    return bux.post('/buxgalteriya/tolov', data).get_json()


def test_rate_history_is_never_recalculated(app, world):
    admin, juma = world['admin'], world['juma']
    admin.post('/admin/sozlamalar', {'set_worker_rate_hand': '1500'})
    lid = open_load(juma, world)
    assert add(juma, lid, 'Akmal Karimov', '100')['ok']              # 100 × 1 500 = 150 000
    admin.post('/admin/sozlamalar', {'set_worker_rate_hand': '1700'})
    assert add(juma, lid, 'Akmal Karimov', '100')['ok']              # 100 × 1 700 = 170 000
    w = balance(app, wid(app, 'Akmal Karimov'))
    assert (w['kg'], w['earned']) == (200, 320_000)
    with app.app_context():
        rows = q("SELECT rate, amount FROM harvests WHERE voided_at IS NULL ORDER BY id")
        assert [(r['rate'], r['amount']) for r in rows] == [(1500, 150_000), (1700, 170_000)]
    # the accountant's worker page shows both rates separately
    html = world['bux'].get(f'/buxgalteriya/ishchi/{w["id"]}').get_data(as_text=True)
    assert '1500so‘m/kg' in flat(html) and '1700so‘m/kg' in flat(html)


def test_payment_states_cashier_hands_out_once(app, world):
    admin, juma, bux, kassa = world['admin'], world['juma'], world['bux'], world['kassa']
    admin.post('/admin/sozlamalar', {'set_worker_rate_hand': '1500'})
    lid = open_load(juma, world)
    for kg in ('150', '150', '126'):                                  # 426 kg → 639 000
        assert add(juma, lid, 'Akmal Karimov', kg)['ok']
    w_id = wid(app, 'Akmal Karimov')
    assert balance(app, w_id)['status'] == 'HISOBLANDI'
    income(bux, '50 000 000')
    # 300 000 given earlier (a first order, handed out)
    r = prepare(bux, w_id, '300 000')
    assert r['ok'], r
    assert balance(app, w_id)['status'] == 'TO‘LOVGA TAYYOR'
    assert kassa.post(f'/buxgalteriya/tolov/{r["payout_id"]}/berildi', {}).get_json()['ok']
    b = balance(app, w_id)
    assert (b['earned'], b['paid'], b['balance'], b['status']) == (639_000, 300_000, 339_000, 'QISMAN TO‘LANDI')
    # the big button: "339 000 SO‘M TO‘LASH"
    assert '339000SO‘MTO‘LASH' in flat(bux.get('/buxgalteriya/ishchilar').get_data(as_text=True))
    # more than owed is refused, and a second open order for the same person too
    assert prepare(bux, w_id, '400 000')['ok'] is False
    r = prepare(bux, w_id)                                            # full remainder
    assert r['ok'] and prepare(bux, w_id, '1000')['ok'] is False
    # cashier sees it ready, presses BERILDI twice → paid once
    assert '339000SO‘MBERILDI' in flat(kassa.get('/kassir').get_data(as_text=True))
    first = kassa.post(f'/buxgalteriya/tolov/{r["payout_id"]}/berildi', {}).get_json()
    again = kassa.post(f'/buxgalteriya/tolov/{r["payout_id"]}/berildi', {}).get_json()
    assert first['ok'] and not first.get('already') and again['ok'] and again.get('already')
    b = balance(app, w_id)
    assert (b['paid'], b['balance'], b['status']) == (639_000, 0, 'TO‘LANDI')
    with app.app_context():
        p = q('SELECT * FROM payouts WHERE id=?', (r['payout_id'],), one=True)
        assert p['status'] == 'BERILDI' and p['paid_by'] and p['paid_at'] and p['cashbox_id']
        assert scalar("SELECT COUNT(*) FROM cash_entries WHERE payout_id=? AND category='worker_pay'", (r['payout_id'],)) == 1
        # who / how much / when / which cashier / which cash box — kept with the entry and in the audit
        c = q('SELECT * FROM cash_entries WHERE payout_id=?', (r['payout_id'],), one=True)
        assert c['amount'] == 339_000 and c['doc_no'] == p['doc_no'] and c['doc_no'].startswith('PAY-')
        assert scalar("SELECT COUNT(*) FROM audit_logs WHERE entity_type='payout' AND action='PAID'") == 2
    # a handed-out payment can't be edited: the amount column is not writable anywhere, only a refund (new entry)
    r2 = bux.post(f'/buxgalteriya/tolov/{r["payout_id"]}/qaytarish', {'reason': 'Xato odamga berildi'}).get_json()
    assert r2['ok']
    b = balance(app, w_id)
    assert (b['paid'], b['balance']) == (300_000, 339_000)
    with app.app_context():
        assert q('SELECT amount, status FROM payouts WHERE id=?', (r['payout_id'],), one=True)['amount'] == 339_000


def test_advance_is_deducted_automatically(app, world):
    admin, juma, bux, kassa = world['admin'], world['juma'], world['bux'], world['kassa']
    income(bux, '1 000 000')
    lid = open_load(juma, world)
    add(juma, lid, 'Davron', '1')                                     # creates the worker (no rate yet → hisoblanmagan)
    w_id = wid(app, 'Davron')
    r = prepare(bux, w_id, '150 000', purpose='advance')
    assert r['ok'], r
    assert kassa.post(f'/buxgalteriya/tolov/{r["payout_id"]}/berildi', {}).get_json()['ok']
    admin.post('/admin/sozlamalar', {'set_worker_rate_hand': '2000'})
    for kg in ('125', '125'):                                         # 250 kg × 2 000 = 500 000
        assert add(juma, lid, 'Davron', kg)['ok']
    b = balance(app, w_id)
    assert (b['earned'], b['advances'], b['balance'], b['uncalc_kg']) == (500_000, 150_000, 350_000, 1)
    assert prepare(bux, w_id)['ok']
    with app.app_context():
        assert q("SELECT amount FROM payouts WHERE status='TAYYOR'", one=True)['amount'] == 350_000
        # the advance stays in history
        assert scalar("SELECT COUNT(*) FROM cash_entries WHERE category='advance' AND worker_id=?", (w_id,)) == 1


def test_kassa_income_minus_outflow_equals_balance(app, world):
    bux = world['bux']
    nos = [income(bux, '50 000 000'), income(bux, '20 000 000'), income(bux, '30 000 000')]
    y = year_of(app)
    assert nos == [f'INC-{y}-000001', f'INC-{y}-000002', f'INC-{y}-000003']          # each income is a new record
    for cat, amt in (('Yoqilg‘i', '3 000 000'), ('Ovqat', '1 000 000')):
        assert bux.post('/buxgalteriya/xarajat/yangi', {'category': cat, 'amount': amt, 'client_uuid': uuid4()}).get_json()['ok']
    from surxon.accounting import cash_totals, total_balance
    with app.app_context():
        t = cash_totals(get_db())
        assert (t['in'], t['out'], total_balance()) == (100_000_000, 4_000_000, 96_000_000)
        assert scalar("SELECT COALESCE(SUM(amount),0) FROM cash_entries WHERE source='Direktor'") == 100_000_000
    # double tap on the income form (same client_uuid) → one record
    u = uuid4()
    bux.post('/buxgalteriya/kirim', {'amount': '5', 'source': 'Boshqa', 'client_uuid': u})
    bux.post('/buxgalteriya/kirim', {'amount': '5', 'source': 'Boshqa', 'client_uuid': u})
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM cash_entries WHERE client_uuid=?", (u,)) == 1
    # money out beyond the cash box is refused (no invented money)
    r = bux.post('/buxgalteriya/xarajat/yangi', {'category': 'Boshqa', 'amount': '999 000 000', 'client_uuid': uuid4()}).get_json()
    assert r['ok'] is False and 'yetarli' in r['error']


def test_day_close_difference_and_lock(app, world):
    bux = world['bux']
    income(bux, '27 500 000')
    bux.post('/buxgalteriya/xarajat/yangi', {'category': 'Ovqat', 'amount': '50 000', 'client_uuid': uuid4()})
    from surxon.utils import today_str
    with app.app_context():
        day = today_str()
        box = scalar('SELECT MIN(id) FROM cashboxes')
    page = flat(bux.get('/buxgalteriya/kun-yopish').get_data(as_text=True))
    assert '27450000so‘m' in page                                     # system: 27 500 000 − 50 000
    r = bux.post('/buxgalteriya/kun-yopish', {'cashbox_id': box, 'day': day, 'counted': '27 400 000'}).get_json()
    assert r['ok'] is False and 'sabab' in r['error']                 # −50 000 needs a reason
    r = bux.post('/buxgalteriya/kun-yopish', {'cashbox_id': box, 'day': day, 'counted': '27 400 000',
                                             'reason': 'Sanashda xato'}).get_json()
    assert r['ok'], r
    from surxon.accounting import total_balance
    with app.app_context():
        d = q('SELECT * FROM cash_days', one=True)
        assert (d['system_balance'], d['counted'], d['diff']) == (27_450_000, 27_400_000, -50_000)
        assert total_balance() == 27_400_000                          # the booked difference makes tomorrow start from real cash
        adj = q("SELECT * FROM cash_entries WHERE category='adjust_out'", one=True)
        assert adj['amount'] == 50_000 and adj['doc_no'].startswith('ADJ-')
        # daily report + alert queued once for the read-only Telegram channel
        assert scalar("SELECT COUNT(*) FROM outbox WHERE channel='telegram_report' AND kind='daily'") == 1
        assert scalar("SELECT COUNT(*) FROM outbox WHERE channel='telegram_report' AND kind='alert'") == 1
        text = q("SELECT payload_json FROM outbox WHERE kind='daily'", one=True)['payload_json']
        assert 'KUNLIK HISOBOT' in text and 'Kassa qoldiq' in text
    # the closed day is locked: no new entry, no void, no second close
    assert bux.post('/buxgalteriya/kirim', {'amount': '1', 'source': 'Boshqa', 'client_uuid': uuid4()}).get_json()['ok'] is False
    with app.app_context():
        cid = scalar("SELECT id FROM cash_entries WHERE category='income'")
    assert bux.post(f'/kassa/{cid}/bekor', {'reason': 'xato'}).get_json()['ok'] is False
    assert bux.post('/buxgalteriya/kun-yopish', {'cashbox_id': box, 'day': day, 'counted': '1'}).get_json()['ok'] is False


def test_combine_tariffs_tonne_day_hectare(app, world):
    bux, juma, kassa = world['bux'], world['juma'], world['kassa']
    income(bux, '10 000 000')
    k1, k2 = world['eq']['K-01'], world['eq']['K-02']
    assert bux.post('/buxgalteriya/kombaynlar', {'action': 'tariff', 'combine_id': k1, 'tariff_type': 'tonna',
                                                 'tariff_rate': '250 000'}).get_json()['ok']
    assert bux.post('/buxgalteriya/kombaynlar', {'action': 'tariff', 'combine_id': k2, 'tariff_type': 'kunlik',
                                                 'tariff_rate': '800 000'}).get_json()['ok']
    lid = open_load(juma, world)
    for kg in ('9300', '9300'):                                      # 18.6 t × 250 000 = 4 650 000
        assert juma.post('/terim', {'load_id': lid, 'method': 'combine', 'combine_id': k1, 'kg': kg,
                                    'client_uuid': uuid4(), 'confirm_duplicate': '1'}).get_json()['ok']
    for kg in ('1000', '2000'):                                      # two weighings, one working day
        assert juma.post('/terim', {'load_id': lid, 'method': 'combine', 'combine_id': k2, 'kg': kg,
                                    'client_uuid': uuid4()}).get_json()['ok']
    r = bux.post('/buxgalteriya/tolov', {'kind': 'combine', 'target_id': k1, 'amount': '2 000 000', 'client_uuid': uuid4()}).get_json()
    assert r['ok'] and kassa.post(f'/buxgalteriya/tolov/{r["payout_id"]}/berildi', {}).get_json()['ok']
    from surxon.accounting import combine_balances
    with app.app_context():
        c = {x['code']: x for x in combine_balances(year_of(app))}
    assert (c['K-01']['tonnes'], c['K-01']['earned'], c['K-01']['paid'], c['K-01']['balance']) == (18.6, 4_650_000, 2_000_000, 2_650_000)
    assert (c['K-02']['work_days'], c['K-02']['earned']) == (1, 800_000)
    # a new tariff applies from now on; what was booked keeps its rate
    bux.post('/buxgalteriya/kombaynlar', {'action': 'tariff', 'combine_id': k1, 'tariff_type': 'tonna', 'tariff_rate': '300 000'})
    with app.app_context():
        assert {x['code']: x for x in combine_balances(year_of(app))}['K-01']['earned'] == 4_650_000
    # hectare tariff: work entered by the accountant
    bux.post('/buxgalteriya/kombaynlar', {'action': 'tariff', 'combine_id': k2, 'tariff_type': 'gektar', 'tariff_rate': '500 000'})
    assert bux.post('/buxgalteriya/kombaynlar', {'action': 'work', 'combine_id': k2, 'unit': 'gektar', 'qty': '3.5'}).get_json()['ok']
    with app.app_context():
        assert {x['code']: x for x in combine_balances(year_of(app))}['K-02']['earned'] == 800_000 + 1_750_000


def test_expense_by_cashier_waits_for_accountant(app, world):
    bux, kassa = world['bux'], world['kassa']
    income(bux, '1 000 000')
    r = kassa.post('/buxgalteriya/xarajat/yangi', {'category': 'Yoqilg‘i', 'amount': '200 000', 'client_uuid': uuid4()}).get_json()
    assert r['ok'] and r['doc_no'].startswith('EXP-')
    with app.app_context():
        e = q('SELECT * FROM expenses', one=True)
    assert e['status'] == 'TEKSHIRILMAGAN'
    home = bux.get('/buxgalteriya').get_data(as_text=True)
    assert '1 ta xarajat tekshirilmagan' in home.replace('\xa0', ' ')
    assert kassa.post(f'/buxgalteriya/xarajat/{e["id"]}/tasdiq', {}).status_code == 403
    assert bux.post(f'/buxgalteriya/xarajat/{e["id"]}/tasdiq', {}).get_json()['ok']
    with app.app_context():
        assert q('SELECT status FROM expenses', one=True)['status'] == 'TASDIQLANGAN'


def test_permissions_money_is_closed_to_field_and_punkt(app, world):
    admin, kassa, juma = world['admin'], world['kassa'], world['juma']
    tally = make_user(app, admin, 'mirjalol', 'tally')
    with app.app_context():
        st = scalar('SELECT MIN(id) FROM stations')
    admin.post('/admin/foydalanuvchilar', {'username': 'yunus', 'full_name': 'Yunus', 'role': 'station', 'password': 'Worker2026x',
                                           'station_id': st})
    with app.app_context():
        get_db().execute("UPDATE users SET must_change_password=0 WHERE username='yunus'")
    from conftest import Client
    yunus = Client(app, 'yunus', 'Worker2026x')
    money = ['/buxgalteriya', '/buxgalteriya/kassa', '/buxgalteriya/ishchilar', '/buxgalteriya/hisobot', '/kassa',
             '/buxgalteriya/qarzlar', '/kassir']
    for who in (tally, yunus, juma):
        for url in money:
            assert who.get(url).status_code in (302, 403), (url,)
    # the cashier: only prepared payments and their own box
    for url in ('/buxgalteriya', '/buxgalteriya/kassa', '/buxgalteriya/hisobot', '/kassa', '/buxgalteriya/qarzlar', '/telashkalar',
                '/nakladnoylar', '/foto', '/hisobotlar'):
        assert kassa.get(url).status_code in (302, 403), url
    assert kassa.get('/kassir').status_code == 200
    assert kassa.post('/buxgalteriya/tolov', {'kind': 'worker', 'target_id': 1, 'amount': '1'}).status_code == 403
    assert kassa.post('/buxgalteriya/kirim', {'amount': '1', 'source': 'Direktor'}).status_code == 403
    # service layer enforces it too
    from surxon.accounting import prepare_payout
    from surxon.security import Actor
    from surxon.utils import UserError
    with app.test_request_context():
        uid = q("SELECT id FROM users WHERE username='asadbek'", one=True)['id']
        with pytest.raises(UserError):
            prepare_payout(Actor(uid, 'cashier'), kind='worker', target_id=1, amount=1)


def test_debts_settle_through_cash(app, world):
    bux = world['bux']
    income(bux, '5 000 000')
    assert bux.post('/buxgalteriya/qarzlar', {'direction': 'OLISH', 'counterparty': 'Nayman', 'amount': '1 000 000',
                                              'reason': 'Oldindan to‘lov', 'client_uuid': uuid4()}).get_json()['ok']
    assert bux.post('/buxgalteriya/qarzlar', {'direction': 'BERISH', 'counterparty': 'Yoqilg‘i shoxobchasi',
                                              'amount': '700 000', 'client_uuid': uuid4()}).get_json()['ok']
    from surxon.accounting import debts, total_balance
    with app.app_context():
        d = {x['direction']: x for x in debts(year_of(app))}
    assert bux.post('/buxgalteriya/qarzlar', {'action': 'settle', 'debt_id': d['OLISH']['id'], 'amount': '400 000'}).get_json()['ok']
    assert bux.post('/buxgalteriya/qarzlar', {'action': 'settle', 'debt_id': d['BERISH']['id']}).get_json()['ok']
    assert bux.post('/buxgalteriya/qarzlar', {'action': 'settle', 'debt_id': d['BERISH']['id'], 'amount': '1'}).get_json()['ok'] is False
    with app.app_context():
        d = {x['direction']: x for x in debts(year_of(app))}
        assert (d['OLISH']['remaining'], d['BERISH']['remaining']) == (600_000, 0)
        assert total_balance() == 5_000_000 + 400_000 - 700_000


def test_sheets_mirror_uses_operation_ids(app, world):
    bux = world['bux']
    income(bux, '1 000 000')
    bux.post('/buxgalteriya/xarajat/yangi', {'category': 'Ovqat', 'amount': '10 000', 'client_uuid': uuid4()})
    import json
    with app.app_context():
        jobs = [json.loads(r['payload_json']) for r in q("SELECT payload_json FROM outbox WHERE channel='sheets' AND kind='upsert'")]
    ids = {(j['sheet'], j['id']) for j in jobs}
    y = year_of(app)
    assert ('KASSA KIRIM-CHIQIM', f'INC-{y}-000001') in ids and ('XARAJATLAR', f'EXP-{y}-000001') in ids
    assert ('KASSA KIRIM-CHIQIM', f'EXP-{y}-000001') in ids          # the expense's cash movement carries the same id


def test_finance_report_numbers_and_exports(app, world):
    admin, juma, bux = world['admin'], world['juma'], world['bux']
    admin.post('/admin/sozlamalar', {'set_worker_rate_hand': '1500'})
    income(bux, '50 000 000')
    lid = open_load(juma, world)
    add(juma, lid, 'Akmal', '100')
    bux.post('/buxgalteriya/xarajat/yangi', {'category': 'Yoqilg‘i', 'amount': '3 450 000', 'client_uuid': uuid4()})
    html = flat(bux.get('/buxgalteriya/hisobot?period=bugun').get_data(as_text=True))
    for part in ('TERILGAN100kg', 'TERIMCHILARPULI150000so‘m', 'YOQILG‘I3450000so‘m', 'KASSAKIRIM50000000so‘m',
                 'KASSAQOLDIQ(davroxiri)46550000so‘m'):
        assert part in html, part
    pdf = bux.get('/buxgalteriya/hisobot?period=bugun&format=pdf')
    assert pdf.status_code == 200 and pdf.data.startswith(b'%PDF')
    x = bux.get('/buxgalteriya/hisobot?period=bugun&format=xlsx')
    assert x.status_code == 200 and x.data[:2] == b'PK'
    home = flat(bux.get('/buxgalteriya').get_data(as_text=True))
    assert '46550000' in home and 'KIRIM' in home and 'KUNNIYOPISH' in home


def test_erp_api_reads_accounting_read_only(app, world):
    from test_integrations import api, make_key
    admin, juma, bux, kassa = world['admin'], world['juma'], world['bux'], world['kassa']
    admin.post('/admin/sozlamalar', {'set_worker_rate_hand': '1500'})
    income(bux, '1 000 000')
    lid = open_load(juma, world)
    add(juma, lid, 'Akmal', '100')
    r = prepare(bux, wid(app, 'Akmal'))
    kassa.post(f'/buxgalteriya/tolov/{r["payout_id"]}/berildi', {})
    key = make_key(admin, ['payouts', 'cash', 'harvests'])
    b = api(app, key, '/worker-balances').get_json()
    assert b['data'][0]['earned'] == 150_000 and b['data'][0]['paid'] == 150_000 and b['data'][0]['worker_name'] is None
    p = api(app, key, '/payouts').get_json()['data'][0]
    assert p['status'] == 'BERILDI' and p['doc_no'].startswith('PAY-')
    c = api(app, key, '/cash-entries').get_json()['data']
    assert {x['doc_no'][:3] for x in c} == {'INC', 'PAY'}
    h = api(app, key, '/harvests').get_json()['data'][0]
    assert (h['rate'], h['amount']) == (1500, 150_000)
    assert api(app, key, '/debts').status_code == 403                       # scope not granted
    assert app.test_client().post('/api/erp/v1/payouts', headers={'Authorization': f'Bearer {key}'}).status_code == 405
