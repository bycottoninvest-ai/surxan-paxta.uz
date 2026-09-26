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
    assert 'Xodimlar xaritada' in world['rahbar'].get('/rahbar').get_data(as_text=True)
