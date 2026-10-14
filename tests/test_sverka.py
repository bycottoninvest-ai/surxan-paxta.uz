"""KUNLIK SVERKA: the day's dala → punkt → pul reckoning is made by itself from what was entered (synthetic data)."""
from conftest import Client, jpeg, uuid4
from surxon.db import get_db, q, scalar
from test_tezpul import cash_in, qr, setup


def test_day_reckoning_goes_from_waiting_to_green_to_red(app, world):
    admin, rahbar = world['admin'], world['rahbar']
    tally, lid, ts = setup(app, world, 3)
    cash_in(world['bux'], '9000000')
    admin.post('/admin/sozlamalar', {'set_auto_waybill_hand': '1'})
    admin.post('/admin/foydalanuvchilar', {'username': 'yunus', 'full_name': 'Yunus', 'role': 'station',
                                           'password': 'Worker2026x', 'station_id': 1})
    with app.app_context():
        get_db().execute("UPDATE users SET must_change_password=0 WHERE username='yunus'")
    punkt = Client(app, 'yunus', 'Worker2026x')
    from test_dala import open_trip
    assert tally.post(f'/dala/reys/{lid}/yopish', {'kg': '1300'}, files={'photos': jpeg((10, 20, 30))}).get_json()['ok']
    lid2 = open_trip(tally, world, rate='2000', trailer='TL-02')
    assert tally.post(f'/dala/reys/{lid2}/yopish', {'kg': '1250'}, files={'photos': jpeg((40, 50, 60))}).get_json()['ok']
    from surxon import sverka as S
    from surxon.utils import today_str
    with app.app_context():
        bid = scalar('SELECT brigadier_id FROM trailer_loads WHERE id=?', (lid,))
        wbs = [r['id'] for r in q('SELECT id FROM waybills ORDER BY id')]
        c = S.check(today_str())
        assert len(wbs) == 2 and c['totals']['dala'] == 2550 and c['rows'][0]['level'] == 'yellow'   # no talon yet
        assert 'Talon hali yozilmagan' in c['rows'][0]['why']
    r = tally.post('/tezpul/kechki/saqlash', {'brigada': bid, 'code': qr(ts[0]), 'kg': '2480', 'client_uuid': uuid4(),
                                              'odam': '30', 'sheets': '[]'}).get_json()
    assert r['ok'], r
    with app.app_context():
        c = S.check(today_str())
        row = c['rows'][0]
        assert row['talon_kg'] == 2480 and row['waiting'] == 2 and row['level'] == 'wait' and c['level'] == 'wait'
    for wb, kg in zip(wbs, ('1290', '1240')):
        assert punkt.post(f'/punkt/yuk/{wb}/qabul', {'station_kg': kg, 'reason': 'Tarozilar farqi'}).get_json()['ok']
    with app.app_context():
        c = S.check(today_str())
        row = c['rows'][0]
        assert (row['punkt'], row['waiting'], row['level']) == (2530, 0, 'green') and c['level'] == 'green'
    page = rahbar.get('/rahbar/sverka').get_data(as_text=True)
    assert 'KUNLIK SVERKA' in page and 'Hammasi mos' in page and '2\u202f530' in page
    assert 'KUNLIK SVERKA' in rahbar.get('/rahbar').get_data(as_text=True)              # the banner on the director's home
    # a second talon pushes the pay over the punkt kg: red, with the words why
    assert tally.post('/tezpul/kechki/saqlash', {'brigada': bid, 'code': qr(ts[1]), 'kg': '300', 'client_uuid': uuid4(),
                                                 'odam': '4'}).get_json()['ok']
    with app.app_context():
        c = S.check(today_str())
        row = c['rows'][0]
        assert row['level'] == 'red' and c['counts']['red'] == 1 and 'Pul punktdan 250 kg ko‘pga yozilgan' == row['why']
        from surxon.yordamchi import director_text
        txt = director_text(today_str())
        assert '⚖️ SVERKA' in txt and '🔴 1 ta brigadada farq' in txt and 'Pul punktdan 250 kg' in txt
    page = rahbar.get('/rahbar/sverka').get_data(as_text=True)
    assert '1 ta brigadada farq' in page and 'sv-c-red' in page
    assert rahbar.get('/rahbar/sverka?sana=2020-01-01').status_code == 200            # an empty day
    assert tally.get('/rahbar/sverka').status_code in (302, 403)                        # not for the field clerk


def test_cancelled_trip_and_talon_are_not_counted(app, world):
    tally, lid, ts = setup(app, world, 2)
    assert tally.post(f'/dala/reys/{lid}/yopish', {'kg': '900'}, files={'photos': jpeg((1, 2, 3))}).get_json()['ok']
    from surxon import sverka as S
    from surxon.utils import today_str
    with app.app_context():
        assert S.check(today_str())['totals']['dala'] == 900
        get_db().execute("UPDATE trailer_loads SET status='BEKOR' WHERE id=?", (lid,))
        c = S.check(today_str())
        assert c['rows'] == [] and c['level'] == 'empty'
