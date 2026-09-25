"""Codex's acceptance example, end to end in a separate test database (never the working one):
3 pickers 100/80/60 kg → 360 000 so‘m; advance 50 000 + payment 100 000 to the first; 210 000 still owed to the others;
cash 1 000 000 − 50 000 − 100 000 − 20 000 = 830 000; weighbridge 1 240 − 1 000 = 240 net; Nayman 238 (−2).
A later rate of 1 700 never changes the 360 000; double taps never double anything; field/net/accepted stay apart."""
from conftest import jpeg, make_user, uuid4
from surxon.db import get_db, q, scalar
from test_acct import balance, flat, income, prepare, wid


def test_codex_acceptance_example(app, world):
    admin, bux, scale = world['admin'], world['bux'], world['tarozi']
    tally = make_user(app, admin, 'mirjalol', 'tally')
    admin.post('/admin/sozlamalar', {'set_worker_rate_hand': '1500'})
    assert income(bux, '1 000 000')                                             # cash at the start of the day

    # field: 3 pickers on one trip
    lid = tally.post('/telashkalar/ochish', {'trailer_id': world['eq']['TL-01'], 'field_id': world['f']['D-04'],
                                            'tractor_id': world['eq']['T-01'], 'client_uuid': uuid4()}).get_json()['load_id']
    same = uuid4()
    for name, kg, cu in (('Ishchi Bir', '100', same), ('Ishchi Ikki', '80', uuid4()), ('Ishchi Uch', '60', uuid4())):
        assert tally.post('/terim', {'load_id': lid, 'method': 'hand', 'worker_name': name, 'kg': kg,
                                     'client_uuid': cu}).get_json()['ok']
    # the same save sent again (double tap / offline replay) → still one record
    tally.post('/terim', {'load_id': lid, 'method': 'hand', 'worker_name': 'Ishchi Bir', 'kg': '100', 'client_uuid': same})
    with app.app_context():
        assert scalar('SELECT SUM(kg) FROM harvests WHERE load_id=? AND voided_at IS NULL', (lid,)) == 240
    assert tally.post(f'/yuk/{lid}/toldi', files={'photos': jpeg()}).get_json()['ok']

    # weighbridge: brutto 1 240, tara 1 000 → netto 240 → one numbered waybill + PDF
    assert scale.post(f'/tarozi/{lid}', {'step': 'gross', 'gross_kg': '1240'}).get_json()['ok']
    r = scale.post(f'/tarozi/{lid}', {'step': 'tare', 'tare_kg': '1000'}).get_json()
    assert r['ok'], r
    again = scale.post(f'/tarozi/{lid}', {'step': 'tare', 'tare_kg': '1000'}).get_json()
    with app.app_context():
        wb = q('SELECT * FROM waybills WHERE load_id=?', (lid,), one=True)
        assert wb['net_kg'] == 240 and scalar('SELECT COUNT(*) FROM waybills') == 1
        assert scalar("SELECT COUNT(*) FROM documents WHERE waybill_id=? AND kind='nayman'", (wb['id'],)) >= 1

    # Nayman accepts 238 → difference −2 (accepted minus net), reason required
    r = bux.post(f'/nayman/{wb["id"]}', {'accepted_kg': '238', 'received_date': wb['document_date'],
                                         'diff_reason': 'Namlik / tabiiy kamayish'}).get_json()
    assert r['ok'], r
    with app.app_context():
        rc = q('SELECT * FROM nayman_receipts WHERE waybill_id=?', (wb['id'],), one=True)
        assert (rc['accepted_kg'], rc['diff_kg']) == (238, -2)
        from surxon import queries
        k = queries.day_kpis(scalar('SELECT MAX(year) FROM seasons'), wb['document_date'], None)
        assert (k['harvest'], k['net'], k['accepted']) == (240, 240, 238)       # kept apart, never summed

    # wages: 150 000 + 120 000 + 90 000 = 360 000 (the −2 kg does not cut anyone's pay)
    w1, w2, w3 = (wid(app, n) for n in ('Ishchi Bir', 'Ishchi Ikki', 'Ishchi Uch'))
    assert [balance(app, w)['earned'] for w in (w1, w2, w3)] == [150_000, 120_000, 90_000]

    # first picker: advance 50 000, then the rest (100 000); the cashier's double tap pays once
    kassa = world['kassa']
    a = prepare(bux, w1, '50 000', purpose='advance')
    assert a['ok'] and kassa.post(f'/buxgalteriya/tolov/{a["payout_id"]}/berildi', {}).get_json()['ok']
    p = prepare(bux, w1)
    assert p['ok']
    assert kassa.post(f'/buxgalteriya/tolov/{p["payout_id"]}/berildi', {}).get_json()['ok']
    assert kassa.post(f'/buxgalteriya/tolov/{p["payout_id"]}/berildi', {}).get_json().get('already')
    with app.app_context():
        assert q('SELECT amount FROM payouts WHERE id=?', (p['payout_id'],), one=True)['amount'] == 100_000
    assert balance(app, w1)['balance'] == 0
    assert balance(app, w2)['balance'] + balance(app, w3)['balance'] == 210_000

    # expense 20 000 → cash 830 000
    assert bux.post('/buxgalteriya/xarajat/yangi', {'category': 'Boshqa', 'amount': '20 000', 'client_uuid': uuid4(),
                                                    'from_cash': '1'}).get_json()['ok']
    from surxon.accounting import total_balance
    with app.app_context():
        assert total_balance(get_db()) == 830_000

    # the next day's rate 1 700 does not rewrite the 360 000
    admin.post('/admin/sozlamalar', {'set_worker_rate_hand': '1700'})
    assert sum(balance(app, w)['earned'] for w in (w1, w2, w3)) == 360_000

    # the tally clerk cannot do the cashier's or accountant's job through a URL
    r = tally.post('/buxgalteriya/tolov', {'kind': 'worker', 'target_id': w2, 'purpose': 'pay', 'client_uuid': uuid4()})
    assert r.status_code in (302, 403) or r.get_json()['ok'] is False
    assert tally.get('/kassir').status_code in (302, 403)
    assert tally.get('/buxgalteriya').status_code in (302, 403)
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM payouts") == 2
