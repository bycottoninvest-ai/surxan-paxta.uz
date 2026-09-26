"""Admin can cancel a whole (test or mistaken) trip in one step — even after the punkt received it; nothing is erased."""
from surxon.db import get_db, q, scalar
from conftest import Client, make_user, uuid4
from test_rahbar import flat, trip


def setup(app, world):
    admin = world['admin']
    admin.post('/admin/sozlamalar', {'set_auto_waybill_hand': '1'})
    tally = make_user(app, admin, 'mirjalol', 'tally')
    admin.post('/admin/foydalanuvchilar', {'username': 'yunus', 'full_name': 'Yunus', 'role': 'station',
                                           'password': 'Worker2026x', 'station_id': 1})
    with app.app_context():
        get_db().execute("UPDATE users SET must_change_password=0 WHERE username='yunus'")
    return tally, Client(app, 'yunus', 'Worker2026x')


def test_admin_cancels_received_trip(app, world):
    admin = world['admin']
    tally, punkt = setup(app, world)
    wb, lid = trip(app, world, tally, ['100', '120'])
    assert punkt.post(f'/punkt/yuk/{wb}/qabul', {'station_kg': '215', 'reason': 'Tarozilar farqi'}).get_json()['ok']
    page = admin.get(f'/yuk/{lid}').get_data(as_text=True)
    assert 'Admin: reysni to‘liq bekor qilish' in page
    # not for the director, even though he may void open trips
    assert 'reysni to‘liq bekor' not in world['rahbar'].get(f'/yuk/{lid}').get_data(as_text=True)
    assert not world['rahbar'].post(f'/yuk/{lid}/admin-bekor', {'reason': 'sinov'}).get_json()['ok']
    assert not admin.post(f'/yuk/{lid}/admin-bekor', {'reason': ''}).get_json()['ok']       # reason required
    r = admin.post(f'/yuk/{lid}/admin-bekor', {'reason': 'Sinov reysi'}).get_json()
    assert r['ok'], r
    with app.app_context():
        assert scalar('SELECT status FROM trailer_loads WHERE id=?', (lid,)) == 'BEKOR'
        assert scalar('SELECT status FROM waybills WHERE id=?', (wb,)) == 'BEKOR'
        assert scalar('SELECT COUNT(*) FROM harvests WHERE load_id=? AND voided_at IS NULL', (lid,)) == 0
        assert scalar('SELECT COUNT(*) FROM nayman_receipts WHERE waybill_id=?', (wb,)) == 0
        kept = q("SELECT old_json FROM audit_logs WHERE entity_type='nayman_receipt' AND action='VOID'", one=True)
        assert kept and '215' in kept['old_json']                                            # full copy kept
        assert scalar('SELECT COUNT(*) FROM harvests WHERE load_id=?', (lid,)) == 2         # rows stay, marked void
    home = flat(world['rahbar'].get('/rahbar').get_data(as_text=True))
    assert '0 kg' in home                                                                    # panel back to zero
    assert not admin.post(f'/yuk/{lid}/admin-bekor', {'reason': 'yana'}).get_json()['ok']  # already cancelled


def test_blocked_when_worker_already_paid(app, world):
    admin, bux = world['admin'], world['bux']
    tally, _ = setup(app, world)
    wb, lid = trip(app, world, tally, ['100'])
    chk = bux.post('/hamyon/kirim/tekshir', {'amount': '1 000 000', 'source': 'Direktor'}).get_json()
    bux.post('/hamyon/kirim/tasdiq', {'amount': '1 000 000', 'source': 'Direktor', 'expect': chk['expect'], 'client_uuid': uuid4()})
    with app.app_context():
        w = scalar("SELECT worker_id FROM harvests WHERE load_id=?", (lid,))
    c = bux.post(f'/hamyon/tolov/{w}/tekshir', {'amount': '150 000'}).get_json()
    assert bux.post(f'/hamyon/tolov/{w}/tasdiq', {'amount': '150 000', 'expect': c['expect'], 'client_uuid': uuid4()}).get_json()['ok']
    r = admin.post(f'/yuk/{lid}/admin-bekor', {'reason': 'Sinov reysi'}).get_json()
    assert not r['ok'] and 'to‘lov' in r['error']
    with app.app_context():
        assert scalar('SELECT status FROM trailer_loads WHERE id=?', (lid,)) != 'BEKOR'


def test_admin_reopens_closed_trip_and_same_waybill_comes_back(app, world):
    admin = world['admin']
    tally, punkt = setup(app, world)
    wb, lid = trip(app, world, tally, ['100', '120'])
    with app.app_context():
        number = scalar('SELECT number FROM waybills WHERE id=?', (wb,))
    assert 'reysni davom ettirish' in admin.get(f'/yuk/{lid}').get_data(as_text=True)
    assert not world['rahbar'].post(f'/yuk/{lid}/davom', {'reason': 'to‘lmagan'}).get_json()['ok']     # admin only
    r = admin.post(f'/yuk/{lid}/davom', {'reason': 'Telashka to‘lmagan edi'}).get_json()
    assert r['ok'], r
    with app.app_context():
        assert scalar('SELECT status FROM trailer_loads WHERE id=?', (lid,)) == 'OCHIQ'
        assert scalar('SELECT status FROM waybills WHERE id=?', (wb,)) == 'BEKOR'
        assert scalar('SELECT COUNT(*) FROM weighings WHERE load_id=?', (lid,)) == 0
    # the tally sees it as open again and continues on the same trip
    assert 'reys/%d' % lid in tally.get('/dala').get_data(as_text=True)
    assert tally.post(f'/dala/reys/{lid}/tortish', {'worker_name': 'Yangi odam', 'kg': '90', 'client_uuid': uuid4()}).get_json()['ok']
    from conftest import jpeg
    assert tally.post(f'/dala/reys/{lid}/yopish', files={'photos': jpeg((1, 2, 3))}).get_json()['ok']
    with app.app_context():
        row = q('SELECT * FROM waybills WHERE load_id=?', (lid,), one=True)
        assert row['id'] == wb and row['number'] == number and row['status'] == 'YARATILDI' and row['net_kg'] == 310
        assert scalar('SELECT net_kg FROM weighings WHERE load_id=?', (lid,)) == 310
    # once the punkt received it, it can no longer be reopened
    assert punkt.post(f'/punkt/yuk/{wb}/qabul', {'station_kg': '305', 'reason': 'Tarozilar farqi'}).get_json()['ok']
    r = admin.post(f'/yuk/{lid}/davom', {'reason': 'yana'}).get_json()
    assert not r['ok'] and 'Punkt' in r['error']


def test_cancelled_trip_leaves_no_weight_on_dashboard(app, world):
    admin = world['admin']
    tally, _ = setup(app, world)
    wb, lid = trip(app, world, tally, ['100', '120'])
    from surxon import queries
    from surxon.utils import today_str
    with app.app_context():
        assert queries.day_kpis(2026, today_str())['net'] == 220
    assert admin.post(f'/yuk/{lid}/admin-bekor', {'reason': 'Sinov'}).get_json()['ok']
    with app.app_context():
        k = queries.day_kpis(2026, today_str())
        assert k['net'] == 0 and k['harvest'] == 0 and k['sent'] == 0


def test_cancelling_field_waybill_cancels_the_trip_and_old_ones_are_settled(app, world):
    admin = world['admin']
    tally, _ = setup(app, world)
    from surxon import queries
    from surxon.utils import today_str
    wb, lid = trip(app, world, tally, ['100', '120'])
    assert admin.post(f'/nakladnoy/{wb}/bekor', {'reason': 'Xato yopildi'}).get_json()['ok']
    with app.app_context():
        assert scalar('SELECT status FROM trailer_loads WHERE id=?', (lid,)) == 'BEKOR'
        k = queries.day_kpis(2026, today_str())
        assert k['harvest'] == 0 and k['net'] == 0
    # an older trip cancelled the old way (only its waybill) is settled on the next start
    wb2, lid2 = trip(app, world, tally, ['90'], trailer='TL-02')
    with app.app_context():
        get_db().execute("UPDATE waybills SET status='BEKOR', void_reason='eski usul' WHERE id=?", (wb2,))
        assert queries.day_kpis(2026, today_str())['harvest'] == 90
        from surxon.services import settle_cancelled_field_waybills
        assert settle_cancelled_field_waybills() == 1 and settle_cancelled_field_waybills() == 0
        assert scalar('SELECT status FROM trailer_loads WHERE id=?', (lid2,)) == 'BEKOR'
        assert queries.day_kpis(2026, today_str())['harvest'] == 0


def test_gps_is_saved_with_trip_and_weighings_and_shown_on_map(app, world):
    admin = world['admin']
    tally, _ = setup(app, world)
    r = tally.post('/dala/yangi', {'trailer_id': world['eq']['TL-01'], 'field_id': world['f']['D-04'], 'method': 'hand',
                                   'brigadier_id': world['b']['Juma ota'], 'rate': '1500', 'client_uuid': uuid4(),
                                   'lat': '41.2995', 'lon': '69.2401', 'acc': '12'}).get_json()
    lid = r['load_id']
    assert tally.post(f'/dala/reys/{lid}/tortish', {'worker_name': 'Ali', 'kg': '80', 'client_uuid': uuid4(),
                                                   'lat': '41.29961', 'lon': '69.24022', 'acc': '8'}).get_json()['ok']
    assert tally.post(f'/dala/reys/{lid}/tortish', {'worker_name': 'Vali', 'kg': '70', 'client_uuid': uuid4(),
                                                   'lat': 'x', 'lon': ''}).get_json()['ok']          # no GPS still saves
    with app.app_context():
        assert q('SELECT open_lat, open_acc FROM trailer_loads WHERE id=?', (lid,), one=True)['open_lat'] == 41.2995
        rows = q('SELECT lat, gps_acc FROM harvests WHERE load_id=? ORDER BY id', (lid,))
        assert rows[0]['lat'] == 41.29961 and rows[0]['gps_acc'] == 8 and rows[1]['lat'] is None
    page = admin.get('/?view=full').get_data(as_text=True)
    assert 'picked-data' in page and '41.29961' in page and 'Terilgan joylar' in page
    # the field picker page offers the location button
    assert 'Joylashuvdan aniqlash' in tally.get('/dala/yangi').get_data(as_text=True)


def test_tally_home_has_live_field_map_and_new_trip_preselects(app, world):
    import json as _j
    tally, _ = setup(app, world)
    with app.app_context():
        get_db().execute('UPDATE fields SET polygon_json=? WHERE id=?',
                         (_j.dumps([[41.30, 69.24], [41.30, 69.25], [41.31, 69.25], [41.31, 69.24]]), world['f']['D-04']))
    home = tally.get('/dala').get_data(as_text=True)
    assert 'd-live-map' in home and 'fieldgeo.js' in home and '"D-04"' in home
    page = tally.get(f'/dala/yangi?field={world["f"]["D-04"]}').get_data(as_text=True)
    assert f'value="{world["f"]["D-04"]}" data-brig' in page and 'selected' in page.split(f'value="{world["f"]["D-04"]}"')[1][:60]
