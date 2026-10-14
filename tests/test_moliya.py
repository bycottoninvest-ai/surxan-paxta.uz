"""BUXGALTER / MOLIYA: old debts closed (paid earlier in cash → the kassa does not move; paid now → it does), one by one
or many at once, a wrong kg fixed without losing the old one, every step audited (synthetic data only)."""
from conftest import make_user, uuid4
from surxon.db import get_db, q, scalar
from test_dala import open_trip, weigh
from test_hamyon import box_balance, cash_in


def setup(app, world):
    admin = world['admin']
    admin.post('/admin/sozlamalar', {'set_dala_qogoz': '0'})
    tally = make_user(app, admin, 'terim1', 'tally')
    lid = open_trip(tally, world, rate='2000')
    ids = {}
    for name, kg in (('Zulayho Test', '83'), ('Madina Test', '102'), ('Dilbar Test', '91')):
        r = weigh(tally, lid, kg, name=name, new=True)
        assert r['ok'], r
    with app.app_context():
        for r in q('SELECT id, full_name FROM workers'):
            ids[r['full_name'].split()[0]] = r['id']
    return ids


def bal(app, wid):
    from surxon.accounting import season_of, worker_balances
    with app.app_context():
        return next(iter(worker_balances(season_of(get_db()), worker_id=wid)))


def test_close_old_debt_without_touching_the_kassa(app, world):
    bux, rahbar = world['bux'], world['rahbar']
    ids = setup(app, world)
    cash_in(bux, '1000000')
    before = box_balance(app)
    page = bux.get('/buxgalteriya/moliya').get_data(as_text=True)
    assert 'To‘lanmaganlar' in page and 'Zulayho Test' in page and 'TANLANGANLARNI TO‘LANGAN DEB BELGILASH' in page
    z = ids['Zulayho']
    assert bal(app, z)['payable'] == 166000
    # the note is required; more than the debt is refused; the director only looks
    assert not bux.post('/buxgalteriya/qarz-yopish', {'worker_id': z, 'method': 'oldin_naqd', 'note': ''}).get_json()['ok']
    assert not bux.post('/buxgalteriya/qarz-yopish', {'worker_id': z, 'amount': '200000', 'method': 'oldin_naqd',
                                                      'note': 'oldin berilgan'}).get_json()['ok']
    assert not rahbar.post('/buxgalteriya/qarz-yopish', {'worker_id': z, 'method': 'oldin_naqd', 'note': 'x y z'}).get_json()['ok']
    r = bux.post('/buxgalteriya/qarz-yopish', {'worker_id': z, 'method': 'oldin_naqd', 'client_uuid': uuid4(),
                                               'note': 'Oldin naqd berilgan, programmada yopilmay qolgan'}).get_json()
    assert r['ok'], r
    b = bal(app, z)
    assert b['balance'] == 0 and b['status'] == 'TO‘LANDI' and b['closed'] == 166000
    assert box_balance(app) == before                                  # no cash moved
    with app.app_context():
        a = q("SELECT * FROM audit_logs WHERE action='DEBT_CLOSE' AND entity_id=?", (str(z),), one=True)
        assert a and '166000' in a['old_json'] and 'Oldin naqd' in a['reason']
        msg = q("SELECT payload_json payload FROM outbox WHERE kind='moliya'", one=True)
        assert msg and 'KASSASIZ YOPILDI' in msg['payload'] and 'Zulayho' in msg['payload']
    card = bux.get(f'/buxgalteriya/ishchi/{z}').get_data(as_text=True)
    assert 'Kassasiz yopilgan' in card and 'Qarzdorlik yopildi' in card
    # a mistaken closure is cancelled with a reason: the debt comes back, the row stays
    with app.app_context():
        cid = scalar('SELECT id FROM debt_closures WHERE worker_id=?', (z,))
    assert bux.post(f'/buxgalteriya/qarz-yopish/{cid}/bekor', {'reason': 'Xato odam tanlangan'}).get_json()['ok']
    assert bal(app, z)['payable'] == 166000
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM debt_closures WHERE worker_id=?', (z,)) == 1


def test_many_at_once_and_paid_now_from_the_kassa(app, world):
    bux = world['bux']
    ids = setup(app, world)
    cash_in(bux, '1000000')
    before = box_balance(app)
    r = bux.post('/buxgalteriya/qarz-yopish', {'worker_id': [ids['Zulayho'], ids['Madina']], 'method': 'bank',
                                               'note': 'Bank orqali o‘tkazilgan', 'client_uuid': uuid4()}).get_json()
    assert r['ok'], r
    assert bal(app, ids['Zulayho'])['balance'] == 0 and bal(app, ids['Madina'])['balance'] == 0
    assert box_balance(app) == before
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM audit_logs WHERE action='DEBT_CLOSE'") == 2
    # paid now: the kassa goes down by exactly that sum, once
    r = bux.post('/buxgalteriya/qarz-yopish', {'worker_id': ids['Dilbar'], 'method': 'hozir_naqd', 'note': 'Hozir berildi',
                                               'cashbox_id': '1', 'client_uuid': uuid4()}).get_json()
    assert r['ok'], r
    assert box_balance(app) == before - 182000 and bal(app, ids['Dilbar'])['balance'] == 0
    page = bux.get('/buxgalteriya/moliya?p=hammasi').get_data(as_text=True)
    assert 'Qarzdor yo‘q' in page and 'Real terim xarajati' in page


def test_kg_fix_keeps_the_old_row_and_shows_the_money_effect(app, world):
    bux, rahbar = world['bux'], world['rahbar']
    ids = setup(app, world)
    cash_in(bux, '1000000')
    m, z = ids['Madina'], ids['Zulayho']
    with app.app_context():
        hm = scalar('SELECT id FROM harvests WHERE worker_id=?', (m,))
        hz = scalar('SELECT id FROM harvests WHERE worker_id=?', (z,))
    # unpaid: 102 → 100 kg, the debt follows
    assert not bux.post(f'/buxgalteriya/tortish/{hm}/kg', {'kg': '100', 'reason': 'Operator xatosi', 'note': ''}).get_json()['ok']
    assert not rahbar.post(f'/buxgalteriya/tortish/{hm}/kg', {'kg': '100', 'reason': 'Operator xatosi', 'note': 'abc'}).get_json()['ok']
    r = bux.post(f'/buxgalteriya/tortish/{hm}/kg', {'kg': '100', 'reason': 'Operator xatosi', 'note': 'Daftarda 100'}).get_json()
    assert r['ok'], r
    assert bal(app, m)['earned'] == 200000 and bal(app, m)['payable'] == 200000 and bal(app, m)['fixes'] == 1
    with app.app_context():
        old = q('SELECT * FROM harvests WHERE id=?', (hm,), one=True)
        new = q('SELECT * FROM harvests WHERE corrects_id=?', (hm,), one=True)
        assert old['voided_at'] and 'KG tuzatish: 102 → 100' in old['void_reason'] and old['kg'] == 102
        assert new['kg'] == 100 and new['amount'] == 200000 and new['rate'] == 2000 and not new['voided_at']
        a = q("SELECT * FROM audit_logs WHERE action='KG_FIX' AND entity_type='harvest'", one=True)
        assert '"kg": 102' in a['old_json'] and '"kg": 100' in a['new_json'] and 'Operator xatosi' in a['reason']
    # already paid: 83 → 80 kg shows 6 000 so‘m paid too much
    r = bux.post(f'/buxgalteriya/qarz-yopish', {'worker_id': z, 'method': 'hozir_naqd', 'note': 'Berildi', 'cashbox_id': '1',
                                                'client_uuid': uuid4()}).get_json()
    assert r['ok'] and bal(app, z)['balance'] == 0
    r = bux.post(f'/buxgalteriya/tortish/{hz}/kg', {'kg': '80', 'reason': 'Tarozida xato', 'note': 'Qayta tortildi'}).get_json()
    assert r['ok'] and '6 000 so‘m ortiqcha to‘langan' in r['message'].replace(' ', ' ').replace('\xa0', ' ')
    b = bal(app, z)
    assert b['balance'] == -6000
    card = bux.get(f'/buxgalteriya/ishchi/{z}').get_data(as_text=True)
    assert 'Ortiqcha to‘langan' in card and 'TUZATILGAN' in card and 'KG tuzatildi' in card
