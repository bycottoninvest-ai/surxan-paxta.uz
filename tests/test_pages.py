"""Every page renders for every role, on desktop and phone, with and without data."""
from surxon.db import get_db, q
from conftest import Client, jpeg, open_load

PHONE = 'Mozilla/5.0 (Linux; Android 13; SM-A135F) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36'

GET_PAGES = ['/', '/?view=full', '/?view=mobile', '/terim', '/telashkalar', '/tarozi', '/nakladnoylar', '/nayman',
             '/ishchilar', '/ishchilar/hisob-kitob', '/tolovlar', '/kassa', '/xarajatlar', '/hisobotlar', '/foto', '/audit',
             '/admin/foydalanuvchilar', '/admin/brigadirlar', '/admin/dalalar', '/admin/texnikalar', '/admin/sozlamalar',
             '/admin/mavsumlar', '/admin/zaxira', '/admin/integratsiyalar', '/parol', '/qidiruv?q=TL', '/tv',
             '/hisobot/kunlik', '/hisobot/nakladnoylar', '/hisobot/terimchilar', '/hisobot/brigadirlar', '/hisobot/dalalar',
             '/hisobot/telashkalar', '/hisobot/nayman', '/hisobot/tolovlar', '/hisobot/xarajatlar', '/hisobot/kassa',
             '/hisobot/mavsumlar']


def crawl(client, extra=()):
    bad = []
    for url in list(GET_PAGES) + list(extra):
        r = client.get(url)
        if r.status_code not in (200, 302, 403):  # 403 = role correctly refused
            bad.append((url, r.status_code))
    return bad


def test_login_page_and_health(app):
    c = app.test_client()
    assert c.get('/login').status_code == 200
    assert c.get('/health').get_json()['ok'] is True
    assert c.get('/').status_code == 302  # requires login
    assert c.get('/tv').status_code == 403


def test_all_pages_all_roles_empty_and_with_data(app, world):
    for name in ('admin', 'rahbar', 'juma', 'tarozi', 'bux', 'kassa'):
        assert crawl(world[name]) == [], name
    # add real data: one full chain
    lid = open_load(world['juma'], world)
    world['juma'].post('/terim', {'load_id': lid, 'method': 'hand', 'worker_name': 'Gulbahor opa', 'kg': '85'})
    world['juma'].post(f'/yuk/{lid}/toldi', files={'photos': jpeg()})
    world['tarozi'].post(f'/tarozi/{lid}', {'step': 'gross', 'gross_kg': '2685'})
    r = world['tarozi'].post(f'/tarozi/{lid}', {'step': 'tare', 'tare_kg': '2600'}).get_json()
    wid = r['waybill_id']
    extra = [f'/yuk/{lid}', f'/tarozi/{lid}', f'/nakladnoy/{wid}', f'/nakladnoy/{wid}?print=1', f'/nayman/{wid}',
             '/ishchi/1', f'/admin/dala/{world["f"]["D-04"]}', '/hisobot/kunlik?format=xlsx']
    for name in ('admin', 'rahbar', 'juma', 'tarozi', 'bux', 'kassa'):
        assert crawl(world[name], extra) == [], name
    for name, cl in (('admin', world['admin']), ('juma', world['juma'])):
        phone = Client(app)
        phone.c = cl.c
        phone.headers = {'User-Agent': PHONE}
        assert crawl(phone, extra) == [], name + ' phone'


def test_phone_gets_light_home_and_desktop_gets_dashboard(app, world):
    phone = Client(app, 'juma', 'Worker2026x', ua=PHONE)
    html = phone.get('/').get_data(as_text=True)
    assert 'm-actions' in html and 'Terim kiritish' in html
    assert 'chart.umd.js' not in html and 'leaflet.js' not in html      # no heavy libraries on the phone home
    desk = world['admin'].get('/').get_data(as_text=True)
    assert 'harvest-chart' in desk and 'fields-map' in desk


def test_role_home_screens(app, world):
    for user, text in (('tarozi01', 'Tarozi navbati'), ('buxgalter', 'Nayman qabuli'), ('asadbek', 'Kassa kirim/chiqim'),
                       ('rahbar', 'To‘liq dashboard')):
        html = Client(app, user, 'Worker2026x', ua=PHONE).get('/').get_data(as_text=True)
        assert text in html, user
