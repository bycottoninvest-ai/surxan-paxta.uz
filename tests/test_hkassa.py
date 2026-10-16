"""Hisobchilar kassasi: the cashier hands a field clerk his day's cash, the clerk confirms “OLDIM”, pays the TEZ-PUL
talons from his own box, and in the evening the cashier counts back what is left — a difference is a Farq that stays
on the clerk's box (synthetic data only)."""
from surxon.db import q, scalar
from conftest import uuid4
from test_hamyon import box_balance
from test_tezpul import enter, pay, qr, setup


def _txt(resp):
    return resp.get_data(as_text=True).replace('\u00a0', ' ').replace('\u202f', ' ')


def _ids(app):
    with app.app_context():
        tid = scalar("SELECT id FROM users WHERE username='terim1'")
        return tid


def test_clerk_pays_talons_from_his_own_cash(app, world):
    admin, kassa, rahbar = world['admin'], world['kassa'], world['rahbar']
    tally, lid, ts = setup(app, world, 4)                      # the main box holds 1 000 000
    tid = _ids(app)
    # not switched on: the clerk cannot pay, the cashier cannot give him money
    assert tally.post('/tezpul/talon', {'code': qr(ts[0])}).status_code in (302, 403)
    assert not kassa.post('/kassa/hisobchilar/berish', {'tally_id': tid, 'amount': '500 000'}).get_json()['ok']
    # only the admin switches it on
    assert not kassa.post('/kassa/hisobchilar/yoqish', {'user_id': tid, 'on': '1'}).get_json()['ok']
    assert admin.post('/kassa/hisobchilar/yoqish', {'user_id': tid, 'on': '1'}).get_json()['ok']
    with app.app_context():
        clerk_box = scalar('SELECT cashbox_id FROM users WHERE id=?', (tid,))
        assert clerk_box and scalar('SELECT name FROM cashboxes WHERE id=?', (clerk_box,)).startswith('Hisobchi: ')
    # morning: 500 000 leave the cashier's box at once, wait for the clerk's “OLDIM”; the same tap twice = once
    u = uuid4()
    assert kassa.post('/kassa/hisobchilar/berish', {'tally_id': tid, 'amount': '500 000', 'client_uuid': u}).get_json()['ok']
    assert kassa.post('/kassa/hisobchilar/berish', {'tally_id': tid, 'amount': '500 000', 'client_uuid': u}).get_json()['ok']
    assert box_balance(app, 1) == 500_000 and box_balance(app, clerk_box) == 0
    with app.app_context():
        fid = scalar("SELECT id FROM tally_floats WHERE status='KUTILMOQDA'")
        assert scalar('SELECT COUNT(*) FROM tally_floats') == 1
    home = _txt(tally.get('/dala'))
    assert 'MENING KASSAM' in home and 'OLDIM' in home and '500 000' in home
    # nothing in his box yet: the talon is refused
    enter(tally, lid, qr(ts[0]), '50')                        # 50 kg × 2000 = 100 000
    r = pay(tally, qr(ts[0]))
    assert r.status_code == 422 and 'yetarli pul' in r.get_json()['error']
    # only the clerk himself confirms; a second tap does nothing
    other = kassa.post(f'/dala/kassam/{fid}/oldim')
    assert other.status_code in (302, 403) or not other.get_json()['ok']
    assert tally.post(f'/dala/kassam/{fid}/oldim').get_json()['ok']
    assert tally.post(f'/dala/kassam/{fid}/oldim').get_json()['ok']
    assert box_balance(app, clerk_box) == 500_000
    assert not kassa.post(f'/kassa/hisobchilar/{fid}/bekor', {'reason': 'xato'}).get_json()['ok']    # taken: no way back
    # he pays from his own box
    r = pay(tally, qr(ts[0])).get_json()
    assert r['ok'] and r['result']['amount'] == 100_000 and r['hand'] == 400_000, r
    with app.app_context():
        t = q('SELECT * FROM tezpul_talons WHERE id=?', (ts[0]['id'],), one=True)
        assert (t['status'], t['paid_by'], t['cashbox_id']) == ('TOLANDI', tid, clerk_box)
    page = _txt(tally.get('/tezpul/kassa'))
    assert 'Qo‘limda' in page and '400 000' in page and 'Mening kassam' in page
    page = _txt(kassa.get('/kassa/hisobchilar'))
    assert 'Terim1' in page and '400 000' in page and 'Kechki qabul' in page
    # evening: more than he should have is refused; 390 000 counted → Farq 10 000 stays on his box
    r = kassa.post('/kassa/hisobchilar/qabul', {'tally_id': tid, 'counted': '450 000'}).get_json()
    assert not r['ok'] and 'ko‘p' in r['error']
    r = kassa.post('/kassa/hisobchilar/qabul', {'tally_id': tid, 'counted': '390 000', 'note': 'kam chiqdi',
                                                'client_uuid': uuid4()}).get_json()
    assert r['ok'] and r['result'] == {'expected': 400_000, 'counted': 390_000, 'diff': 10_000}, r
    assert box_balance(app, clerk_box) == 10_000 and box_balance(app, 1) == 890_000
    with app.app_context():
        from surxon.accounting import cash_totals
        from surxon.db import get_db
        assert cash_totals(get_db()) == {'in': 1_000_000, 'out': 100_000}      # moves between our boxes are not income
        from surxon.hkassa import overview
        row = overview()['rows'][0]
        assert (row['given'], row['paid'], row['returned'], row['farq'], row['hand'], row['state']) == \
            (500_000, 100_000, 390_000, 10_000, 10_000, 'farq')
    panel = _txt(rahbar.get('/tezpul/panel'))
    assert 'Hisobchilar kassasi' in panel and '10 000' in panel and 'pulni o‘zi beradigan hisobchi' in panel
    # still money in his hand: cannot be switched off; the rahbar cannot hand out money
    assert not admin.post('/kassa/hisobchilar/yoqish', {'user_id': tid, 'on': '0'}).get_json()['ok']
    assert rahbar.post('/kassa/hisobchilar/berish', {'tally_id': tid, 'amount': '1000'}).status_code in (302, 403)


def test_cashier_takes_back_money_not_confirmed_and_reminder(app, world):
    admin, kassa = world['admin'], world['kassa']
    tally, lid, ts = setup(app, world, 1)
    tid = _ids(app)
    assert admin.post('/kassa/hisobchilar/yoqish', {'user_id': tid, 'on': '1'}).get_json()['ok']
    assert kassa.post('/kassa/hisobchilar/berish', {'tally_id': tid, 'amount': '300000'}).get_json()['ok']
    with app.app_context():
        fid = scalar("SELECT id FROM tally_floats WHERE status='KUTILMOQDA'")
    assert not kassa.post('/kassa/hisobchilar/berish', {'tally_id': tid, 'amount': '5 000 000'}).get_json()['ok']   # box short
    # the evening reminder lists him (money given, never confirmed)
    assert admin.post('/admin/sozlamalar', {'set_hkassa_remind_time': '00:00'}).status_code == 200
    with app.app_context():
        from surxon.hkassa import tick
        from surxon.db import get_db
        get_db().execute("INSERT INTO settings(key, value, updated_at) VALUES ('hkassa_remind_time', '00:00', '') "
                         "ON CONFLICT(key) DO UPDATE SET value='00:00'")
        get_db().commit()
        assert tick() is True and tick() is False                 # once a day
    assert kassa.post(f'/kassa/hisobchilar/{fid}/bekor', {'reason': 'olmadi'}).get_json()['ok']
    assert box_balance(app, 1) == 1_000_000
    assert not tally.post(f'/dala/kassam/{fid}/oldim').get_json()['ok']       # cancelled: nothing to confirm
    assert admin.post('/kassa/hisobchilar/yoqish', {'user_id': tid, 'on': '0'}).get_json()['ok']
    assert tally.post('/tezpul/talon', {'code': qr(ts[0])}).status_code in (302, 403)
