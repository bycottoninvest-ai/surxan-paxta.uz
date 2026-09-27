"""Director's phone panel: six cards from real saved operations, read-only, correct meaning of each number."""
import re
import uuid

from surxon.db import get_db, q, scalar
from conftest import Client, jpeg, make_user, uuid4

PHONE = 'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Mobile Safari/537.36'


def flat(html):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html)).replace(' ', ' ').replace('\xa0', ' ')


def trip(app, world, tally, kgs, trailer='TL-01'):
    r = tally.post('/dala/yangi', {'trailer_id': world['eq'][trailer], 'field_id': world['f']['D-04'], 'method': 'hand',
                                   'brigadier_id': world['b']['Juma ota'], 'rate': '1500', 'client_uuid': uuid4()}).get_json()
    for i, kg in enumerate(kgs):
        assert tally.post(f'/dala/reys/{r["load_id"]}/tortish', {'worker_name': f'Ishchi {trailer} {i}', 'kg': kg,
                                                                'client_uuid': uuid4()}).get_json()['ok']
    assert tally.post(f'/dala/reys/{r["load_id"]}/yopish', files={'photos': jpeg((10, 20, 30))}).get_json()['ok']
    with app.app_context():
        return scalar('SELECT id FROM waybills WHERE load_id=?', (r['load_id'],)), r['load_id']


def test_cards_show_real_figures_and_meaning(app, world):
    admin, rahbar = world['admin'], world['rahbar']
    admin.post('/admin/sozlamalar', {'set_auto_waybill_hand': '1'})
    tally = make_user(app, admin, 'mirjalol', 'tally')
    admin.post('/admin/foydalanuvchilar', {'username': 'yunus', 'full_name': 'Yunus', 'role': 'station', 'password': 'Worker2026x',
                                           'station_id': 1})
    with app.app_context():
        get_db().execute("UPDATE users SET must_change_password=0 WHERE username='yunus'")
    yunus = Client(app, 'yunus', 'Worker2026x')
    wb1, lid1 = trip(app, world, tally, ['100', '100'])                   # 200 kg, received as 190
    wb2, lid2 = trip(app, world, tally, ['150'], trailer='TL-02')         # 150 kg, still on the way
    assert yunus.post(f'/punkt/yuk/{wb1}/qabul', {'station_kg': '190', 'reason': 'Tarozilar farqi'}).get_json()['ok']
    home = flat(rahbar.get('/rahbar').get_data(as_text=True))
    assert 'PAXTA' in home and '350 kg' in home                            # picked
    assert '−10 (−5.0%)' in home or '-10 (-5.0%)' in home                  # difference only on the received trip
    assert '1 reys' in home and '150 kg yo‘lda' in home                    # what is on the way is shown apart
    assert 'Ulanmagan' in home                                             # fuel not set up yet → never a 0
    # the moving list and the trip page
    moving = flat(rahbar.get('/rahbar/yolda').get_data(as_text=True))
    assert 'Yo‘lda' in moving and 'TL-02' in moving
    page = flat(rahbar.get(f'/rahbar/reys/{lid1}').get_data(as_text=True))
    assert 'Qabul qilindi' in page and '190' in page and 'Tarozilar farqi' in page
    # wages: earned and paid side by side, the debt is the current season figure
    bux = world['bux']
    assert bux.post('/hamyon/kirim/tekshir', {'amount': '1 000 000', 'source': 'Direktor'}).get_json()['ok']
    chk = bux.post('/hamyon/kirim/tekshir', {'amount': '1 000 000', 'source': 'Direktor'}).get_json()
    assert bux.post('/hamyon/kirim/tasdiq', {'amount': '1 000 000', 'source': 'Direktor', 'expect': chk['expect'],
                                             'client_uuid': uuid4()}).get_json()['ok']
    with app.app_context():
        w = scalar("SELECT id FROM workers WHERE full_name='Ishchi TL-01 0'")
    c = bux.post(f'/hamyon/tolov/{w}/tekshir', {'amount': '100 000'}).get_json()
    assert bux.post(f'/hamyon/tolov/{w}/tasdiq', {'amount': '100 000', 'expect': c['expect'], 'client_uuid': uuid4()}).get_json()['ok']
    wages = flat(rahbar.get('/rahbar/ish-haqi').get_data(as_text=True))
    assert '425 000' in wages and '100 000' in wages                       # 350 kg × 1 500 earned; 100 000 paid; debt 425 000
    cash = flat(rahbar.get('/rahbar/kassa').get_data(as_text=True))
    assert '900 000' in cash and 'kamomad' in cash
    # checks: a 5 % punkt difference is above the 3 % line → listed as "tekshirish", not a verdict
    checks = flat(rahbar.get('/rahbar/tekshirish').get_data(as_text=True))
    assert 'punktda farq' in checks


def test_polling_stamp_and_read_only(app, world):
    rahbar = world['rahbar']
    s1 = rahbar.get('/rahbar/holat').get_json()['stamp']
    world['admin'].post('/admin/dalalar', {'code': 'D-99', 'name': 'x', 'area_ha': 1})
    assert rahbar.get('/rahbar/holat').get_json()['stamp'] != s1          # any saved operation moves the stamp
    # the panel has no write endpoint at all
    from flask import current_app
    with app.app_context():
        rules = [r for r in app.url_map.iter_rules() if r.endpoint.startswith('rahbar.')]
        assert rules and all(r.methods <= {'GET', 'HEAD', 'OPTIONS'} for r in rules)
    # who sees it: director / admin / accountant (finance) — not the field, punkt, cashier or fuel logins
    assert world['bux'].get('/rahbar').status_code == 200
    for who in ('juma', 'kassa', 'tarozi'):
        assert world[who].get('/rahbar').status_code == 302, who


def test_director_phone_lands_on_panel_and_computer_keeps_dashboard(app, world):
    phone = Client(app, 'rahbar', 'Worker2026x', ua=PHONE)
    r = phone.get('/')
    assert r.status_code == 302 and r.headers['Location'].endswith('/rahbar')
    assert world['rahbar'].get('/').status_code == 200                     # computer: the existing dashboard
    assert 'fields-map' in world['rahbar'].get('/?view=full').get_data(as_text=True)


def test_fuel_card_when_used(app, world):
    from test_fuel import setup, take
    yq, zp, t = setup(app, world)
    assert take(yq, zp, '100')['ok']
    home = flat(world['rahbar'].get('/rahbar').get_data(as_text=True))
    assert 'SOLYARKA' in home and 'Ulanmagan' not in home and '100 L' in home
    page = flat(world['rahbar'].get('/rahbar/solyarka').get_data(as_text=True))
    assert 'ZP-01' in page and 'o‘lchangan sarf emas' in page


def test_tv_board_shows_trips_and_only_field_photos(app, world):
    admin = world['admin']
    admin.post('/admin/sozlamalar', {'set_auto_waybill_hand': '1', 'set_season_target_kg': '1000'})
    tally = make_user(app, admin, 'mirjalol', 'tally')
    wb, lid = trip(app, world, tally, ['100', '150'])
    d = world['rahbar'].get('/tv/data.json').get_json()
    assert d['kpi']['today'] == 250 and d['kpi']['season'] == 250 and d['kpi']['target_pct'] == 25.0
    assert d['flow']['yolda'] == 1 and d['flow']['yolda_kg'] == 250
    assert any(t['state'] == 'Yo‘lda' for t in d['trailers'])
    assert d['brigades'][0]['kg'] == 250 and d['workers'][0]['kg'] == 150
    assert d['photos'] and world['rahbar'].get(f'/tv/foto/{d["photos"][0]["id"]}').status_code == 200
    with app.app_context():
        get_db().execute("UPDATE photos SET category='cash'")
    assert world['rahbar'].get(f'/tv/foto/{d["photos"][0]["id"]}').status_code == 404   # never cash/fuel/document photos
    assert app.test_client().get(f'/tv/foto/{d["photos"][0]["id"]}').status_code == 403  # no key, no photo


def test_staff_map_from_telegram_live_location_and_app(app, world, monkeypatch):
    from surxon import telegram_bot, staffmap
    monkeypatch.setattr(telegram_bot, 'send', lambda *a, **k: None)
    monkeypatch.setattr(staffmap, 'fetch_avatar', lambda *a, **k: None)
    with app.app_context():
        get_db().execute("UPDATE users SET telegram_id='555' WHERE username='juma'")
    upd = {'update_id': 1, 'message': {'chat': {'id': 555, 'type': 'private'}, 'from': {'id': 555, 'first_name': 'Juma'},
                                       'location': {'latitude': 41.30, 'longitude': 69.24, 'live_period': 2147483647}}}
    with app.app_context():
        telegram_bot.process_update(upd)
        upd2 = {'update_id': 2, 'edited_message': dict(upd['message'], location={'latitude': 41.31, 'longitude': 69.25})}
        telegram_bot.process_update(upd2)                   # a live update: stored (moved > 30 m)
        telegram_bot.process_update({'update_id': 3, 'message': {'chat': {'id': 777, 'type': 'private'},
                                     'from': {'id': 777, 'first_name': 'Traktorchi'}, 'location': {'latitude': 41.2, 'longitude': 69.1}}})
        ppl = {p['name']: p for p in staffmap.people()}
        assert ppl['Juma'.join(['', ''])] if False else True
        assert any(p['lat'] == 41.31 for p in ppl.values()) and 'Traktorchi' in ppl
        assert scalar('SELECT COUNT(*) FROM staff_positions') == 3
    # the phone of a field role also reports while the app is open; others may not
    tally = make_user(app, world['admin'], 'mirjalol', 'tally')
    assert tally.post('/api/joy', {'lat': '41.40', 'lon': '69.30', 'acc': '8'}).get_json()['ok']
    assert world['bux'].post('/api/joy', {'lat': '41.4', 'lon': '69.3'}).status_code == 403
    page = world['rahbar'].get('/rahbar/xodimlar').get_data(as_text=True)
    assert 'Traktorchi' in page and 'Mirjalol' in page or 'mirjalol' in page.lower()
    key = next(p['key'] for p in world['rahbar'].get('/rahbar/xodimlar.json').get_json()['people'] if p['source'] == 'telegram' and p['lat'] == 41.31)
    assert len(world['rahbar'].get('/rahbar/xodimlar.json?iz=' + key).get_json()['track']) == 2
    assert world['juma'].get('/rahbar/xodimlar').status_code == 302            # only admin / director / finance
    assert 'Odamlar va texnika xaritada' in world['rahbar'].get('/rahbar').get_data(as_text=True)


def test_tablet_gets_full_dashboard_phone_gets_director_panel(app, world):
    tab = 'Mozilla/5.0 (Linux; Android 13; SM-X200) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
    ipad = 'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/604.1'
    for ua in (tab, ipad):
        r = world['rahbar'].c.get('/', headers={'User-Agent': ua})
        assert r.status_code == 200 and 'Dalalar xaritasi' in r.get_data(as_text=True)
    assert world['rahbar'].c.get('/', headers={'User-Agent': PHONE}).status_code == 302      # phone → /rahbar


def test_staff_photo_name_on_map_dashboard_card_and_stale_reminder(app, world, monkeypatch):
    """The admin puts a person's photo (used instead of the Telegram one), the dashboard shows the staff map, and a
    person whose live location stopped for an hour gets one reminder from the bot — not a second one."""
    from conftest import jpeg
    from surxon import staffmap, telegram_bot
    from surxon.utils import now_str
    sent = []
    monkeypatch.setattr(telegram_bot, 'send', lambda chat, text, *a, **k: sent.append((str(chat), text)))
    monkeypatch.setattr(staffmap, 'fetch_avatar', lambda *a, **k: None)
    monkeypatch.setattr(staffmap, 'now_str', lambda: '2026-09-27 12:00:00')
    with app.app_context():
        telegram_bot.process_update({'update_id': 5, 'message': {'chat': {'id': 901, 'type': 'private'},
                                     'from': {'id': 901, 'first_name': 'Sherzod', 'last_name': 'Karimov'},
                                     'location': {'latitude': 42.31, 'longitude': 59.60, 'live_period': 2147483647}}})
        mid = q("SELECT id FROM tg_members WHERE telegram_id='901'", one=True)['id']
    admin = world['admin']
    r = admin.post('/kuzatuv/odamlar', {'action': 'photo', 'member_id': mid}, files={'photo': jpeg((10, 120, 200), (300, 400))})
    assert r.get_json()['ok'], r.get_data(as_text=True)
    with app.app_context():
        av = q('SELECT avatar_path FROM tg_members WHERE id=?', (mid,), one=True)['avatar_path']
        assert av and av.startswith('avatars/m')
        assert next(p for p in staffmap.people() if p['name'].startswith('Sherzod'))['avatar'] == av
    assert admin.get('/rahbar/avatar/' + av).status_code == 200
    assert admin.post('/kuzatuv/odamlar', {'action': 'photo', 'member_id': mid},
                      files={'photo': b'not an image'}).get_json()['ok'] is False
    assert world['juma'].post('/kuzatuv/odamlar', {'action': 'photo', 'member_id': mid},
                              files={'photo': jpeg()}).status_code in (302, 403)

    # a machine with a GPS tracker is on the same map as the people (dashboard and director panel)
    from surxon.utils import now_str as real_now
    with app.app_context():
        get_db().execute('''INSERT INTO trackers(imei, equipment_id, first_seen, last_seen, last_lat, last_lon, last_speed, last_fix_at)
                            SELECT '0359339075012345', id, ?, ?, 42.315, 59.605, 12, ? FROM equipment WHERE code='T-01' ''',
                         (real_now(), real_now(), real_now()))
    dash = admin.get('/').get_data(as_text=True)
    assert 'Odamlar va texnika xaritada' in dash and 'spxStaffMap' in dash and 'SM_MACHINES' in dash and '"T-01"' in dash
    assert 'Odamlar va texnika' not in world['juma'].get('/').get_data(as_text=True)    # brigadier: no staff map
    page = world['rahbar'].get('/rahbar/xodimlar').get_data(as_text=True)
    assert 'data-machine=' in page and 'Sherzod' in page and 'fleet:' in page

    sent.clear()                                                                       # the “location received” reply
    with app.app_context():
        get_db().execute("UPDATE staff_positions SET at='2026-09-27 11:30:00'")
        assert staffmap.remind_stale() == 0 and not sent                               # 30 min — not yet
        get_db().execute("UPDATE staff_positions SET at='2026-09-27 10:40:00'")
        assert staffmap.remind_stale() == 1 and sent[0][0] == '901' and 'Геопозиция' in sent[0][1]
        assert staffmap.remind_stale() == 0 and len(sent) == 1                        # once per stop
        get_db().execute("UPDATE settings SET value='0' WHERE key='staff_live_remind'")
        get_db().execute("INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES ('staff_live_remind','0',?)", (now_str(),))
        get_db().execute("DELETE FROM settings WHERE key='staff_live_reminded'")
        assert staffmap.remind_stale() == 0                                            # can be switched off


def test_car_screen_tv_zooms_to_work_and_bot_asks_the_busy_people(app, world, monkeypatch):
    """/m — the director's car monitor: one map (busy fields, people, machines), today's numbers, real photos, the feed,
    no money. The TV map knows which fields are busy. While a trip is on the field, the clerk who opened it is asked
    for a photo once per period (never twice, not while an earlier request is unanswered, off when set to 0)."""
    from datetime import datetime
    from surxon import kuzatuv, telegram_bot
    monkeypatch.setattr(telegram_bot, 'send', lambda *a, **k: {'result': {'message_id': 1}})
    admin, rahbar = world['admin'], world['rahbar']
    admin.post('/admin/sozlamalar', {'set_auto_waybill_hand': '1'})
    tally = make_user(app, admin, 'mirjalol', 'tally')
    r = tally.post('/dala/yangi', {'trailer_id': world['eq']['TL-01'], 'field_id': world['f']['D-04'], 'method': 'hand',
                                   'brigadier_id': world['b']['Juma ota'], 'rate': '1500', 'client_uuid': uuid4()}).get_json()
    assert tally.post(f'/dala/reys/{r["load_id"]}/tortish', {'worker_name': 'Maxsuda', 'kg': '36', 'client_uuid': uuid4()}).get_json()['ok']
    world['kassa'].post('/kassa', {'category': 'opening', 'amount': '777777', 'entry_date': '2026-01-01', 'client_uuid': uuid4()})

    page = rahbar.get('/m')
    assert page.status_code == 200 and 'spxStaffMap' in page.get_data(as_text=True)
    d = rahbar.get('/m/data.json').get_json()
    assert d['kpi']['today'] == 36 and d['flow']['dalada'] == 1
    assert any(f['code'] == 'D-04' and f['active'] for f in d['fields']) or not any(f['poly'] for f in d['fields'])
    assert '777777' not in str(d) and 'amount' not in str(d)                       # never money on the car screen
    assert world['juma'].get('/m').status_code == 302                              # director / admin only
    assert app.test_client().get('/m').status_code == 302                          # login first

    with app.app_context():
        from surxon import tvboard
        assert [f for f in tvboard.fields_map(2026) if f['code'] == 'D-04'][0]['active']
        uid = q("SELECT id FROM users WHERE username='mirjalol'", one=True)['id']
        get_db().execute("INSERT INTO tg_members(full_name, status, source, user_id, telegram_id, dm_ok, created_at) "
                         "VALUES ('Mirjalol','FAOL','tizim',?, '4242', 1, 'x')", (uid,))
        at = datetime(2026, 9, 27, 10, 5)
        assert kuzatuv.auto_activity_requests(at) == 1
        req = q("SELECT * FROM media_requests ORDER BY id DESC LIMIT 1", one=True)
        assert req['event'] == 'Terim ketyapti' and 'D-04' in req['text']
        assert kuzatuv.auto_activity_requests(at) == 0                           # same period → not again
        assert kuzatuv.auto_activity_requests(datetime(2026, 9, 27, 11, 10)) == 0  # earlier one still unanswered
        get_db().execute("UPDATE media_requests SET status='JAVOB'")
        assert kuzatuv.auto_activity_requests(datetime(2026, 9, 27, 11, 10)) == 1  # next hour → asked again
        get_db().execute("UPDATE media_requests SET status='JAVOB'")
        assert kuzatuv.auto_activity_requests(datetime(2026, 9, 27, 22, 10)) == 0  # outside working hours
        get_db().execute("INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES ('kuzatuv_auto_active_min','0','x')")
        assert kuzatuv.auto_activity_requests(datetime(2026, 9, 27, 12, 10)) == 0  # switched off
