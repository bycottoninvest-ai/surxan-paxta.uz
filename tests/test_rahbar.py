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
