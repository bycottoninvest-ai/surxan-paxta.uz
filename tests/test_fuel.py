"""Solyarka: QR-only take/give, ticket so‘m vs keeper liters, frozen price, FIFO cost, checks, roles, double taps."""
from surxon.db import get_db, q, scalar
from conftest import Client, jpeg, uuid4


def keeper(app, admin, username='yunus_y'):
    r = admin.post('/admin/foydalanuvchilar', {'username': username, 'full_name': username.title(), 'role': 'fuel',
                                                'password': 'Worker2026x'}).get_json()
    assert r['ok'], r
    with app.app_context():
        get_db().execute('UPDATE users SET must_change_password=0 WHERE username=?', (username,))
    return Client(app, username, 'Worker2026x')


def setup(app, world):
    admin, bux, rahbar = world['admin'], world['bux'], world['rahbar']
    assert bux.post('/buxgalteriya/kirim', {'amount': '20 000 000', 'source': 'Direktor', 'client_uuid': uuid4()}).get_json()['ok']
    assert bux.post('/yoqilgi/boshqaruv', {'action': 'station', 'code': 'ZP-01', 'name': 'Surxon Neft', 'approved': '1'}).get_json()['ok']
    with app.app_context():
        st = q('SELECT * FROM fuel_stations', one=True)
    r = bux.post('/yoqilgi/boshqaruv', {'action': 'ticket', 'station_id': st['id'], 'price_per_l': '12 000',
                                        'amount': '6 000 000', 'paid_from': 'kassa', 'client_uuid': uuid4()}).get_json()
    assert r['ok'], r
    for code, ftype, carrier in (('T-01', 'solyarka', ''), ('K-01', 'solyarka', '')):
        assert rahbar.post('/yoqilgi/boshqaruv', {'action': 'machine', 'equipment_id': world['eq'][code], 'fuel_type': ftype,
                                                  'carrier': carrier}).get_json()['ok']
    with app.app_context():
        tokens = {r['code']: r['qr_token'] for r in q("SELECT code, qr_token FROM equipment WHERE qr_token IS NOT NULL")}
    return keeper(app, admin), f'SPX-YQ:Z:{st["qr_token"]}', {k: f'SPX-YQ:T:{v}' for k, v in tokens.items()}


def scan(c, payload, want):
    return c.post('/yoqilgi/skan', {'payload': payload, 'want': want}).get_json()


def take(c, zp, liters, key=None):
    s = scan(c, zp, 'station')
    assert s['ok'], s
    return c.post('/yoqilgi/olish', {'scan_id': s['scan_id'], 'liters': liters, 'client_uuid': key or uuid4()}).get_json()


def give(c, qr, liters, photo=None, **extra):
    s = scan(c, qr, 'equipment')
    assert s['ok'], s
    return c.post('/yoqilgi/berish', {'scan_id': s['scan_id'], 'liters': liters, 'client_uuid': uuid4(), **extra},
                  files={'photo': photo or jpeg((liters * 3 % 255, 90, 40), (320, 240))}).get_json()


def liters(app, username='yunus_y'):
    from surxon.fuel import keeper_liters
    with app.app_context():
        return keeper_liters(get_db(), scalar('SELECT id FROM users WHERE username=?', (username,)))


def ticket_balance(app):
    from surxon.fuel import ticket_balance as tb
    with app.app_context():
        return tb(get_db(), scalar('SELECT id FROM fuel_tickets LIMIT 1'))


def test_take_and_give_two_balances(app, world):
    yq, zp, t = setup(app, world)
    with app.app_context():   # ticket money is one “Yoqilg‘i” expense, paid from the cash box once
        assert scalar("SELECT COUNT(*) FROM expenses WHERE category='Yoqilg‘i'") == 1
        assert scalar("SELECT COUNT(*) FROM cash_entries WHERE category='expense'") == 1
    # QR is mandatory and must be ours and of the right kind
    assert not scan(yq, 'hello', 'station')['ok']
    assert not scan(yq, t['T-01'], 'station')['ok']
    # take 300 L: ticket −3 600 000 so‘m, keeper +300 L; a double tap stores it once
    key = uuid4()
    s = scan(yq, zp, 'station')
    assert s['tickets'][0]['balance'] == 6_000_000
    r1 = yq.post('/yoqilgi/olish', {'scan_id': s['scan_id'], 'liters': '300', 'client_uuid': key}).get_json()
    r2 = yq.post('/yoqilgi/olish', {'scan_id': s['scan_id'], 'liters': '300', 'client_uuid': key}).get_json()
    assert r1['ok'] and r2['ok'] and r1['redirect'] == r2['redirect']
    assert liters(app) == 300 and ticket_balance(app) == 2_400_000
    # a used scan cannot be reused; the ticket cannot go below zero
    again = yq.post('/yoqilgi/olish', {'scan_id': s['scan_id'], 'liters': '10', 'client_uuid': uuid4()}).get_json()
    assert not again['ok']
    r = take(yq, zp, '250')                                    # 3 000 000 > 2 400 000
    assert not r['ok'] and 'yetmaydi' in r['error']
    # give 120 L to T-01 with a camera photo: liters move, the ticket is not charged again
    r = give(yq, t['T-01'], 120)
    assert r['ok'], r
    assert liters(app) == 180 and ticket_balance(app) == 2_400_000
    with app.app_context():
        o = q("SELECT * FROM fuel_ops WHERE kind='BERISH'", one=True)
        assert o['photo_id'] and o['scan_at'] and not o['flag'] and o['doc_no'].startswith('YQ-')
        assert scalar("SELECT COUNT(*) FROM expenses") == 1        # no new expense from take/give
    # more than held is refused; a photo cannot be reused; no photo is refused
    assert not give(yq, t['K-01'], 500)['ok']
    same = jpeg((1, 2, 3), (320, 240))
    assert give(yq, t['K-01'], 20, photo=same)['ok']
    r = give(yq, t['K-01'], 20, photo=same)
    assert not r['ok'] and 'oldingi' in r['error']
    s = scan(yq, t['K-01'], 'equipment')
    assert not yq.post('/yoqilgi/berish', {'scan_id': s['scan_id'], 'liters': '5', 'client_uuid': uuid4()}).get_json()['ok']


def test_unusual_needs_reason_and_alerts(app, world):
    yq, zp, t = setup(app, world)
    assert take(yq, zp, '450')['ok']
    s = scan(yq, t['T-01'], 'equipment')
    chk = yq.post('/yoqilgi/berish/tekshir', {'scan_id': s['scan_id'], 'liters': '250'}).get_json()
    assert chk['ok'] and chk['flags']                          # above the 200 L single-give line
    r = yq.post('/yoqilgi/berish', {'scan_id': s['scan_id'], 'liters': '250', 'client_uuid': uuid4()},
                files={'photo': jpeg((9, 9, 9), (320, 240))}).get_json()
    assert not r['ok'] and 'sabab' in r['error'].lower()
    r = yq.post('/yoqilgi/berish', {'scan_id': s['scan_id'], 'liters': '250', 'client_uuid': uuid4(), 'reason': 'Og‘ir ish'},
                files={'photo': jpeg((9, 9, 9), (320, 240))}).get_json()
    assert r['ok'], r
    # again within the hour → also flagged
    s = scan(yq, t['T-01'], 'equipment')
    chk = yq.post('/yoqilgi/berish/tekshir', {'scan_id': s['scan_id'], 'liters': '30'}).get_json()
    assert any('daqiqa' in f for f in chk['flags'])
    with app.app_context():
        o = q("SELECT * FROM fuel_ops WHERE kind='BERISH'", one=True)
        assert o['flag'] and o['reason'] == 'Og‘ir ish'
        assert scalar("SELECT COUNT(*) FROM outbox WHERE ref LIKE 'alert:fuelflag:%'") == 1


def test_price_is_frozen_fifo_and_close_keeps_liters(app, world):
    yq, zp, t = setup(app, world)
    bux = world['bux']
    with app.app_context():
        tid = scalar('SELECT id FROM fuel_tickets')
    assert take(yq, zp, '100')['ok']                              # 100 × 12 000
    assert bux.post('/yoqilgi/boshqaruv', {'action': 'price', 'ticket_id': tid, 'price_per_l': '13 000'}).get_json()['ok']
    assert take(yq, zp, '100')['ok']                              # 100 × 13 000
    with app.app_context():
        rows = q("SELECT price_per_l, amount FROM fuel_ops WHERE kind='OLISH' ORDER BY id")
        assert [(r['price_per_l'], r['amount']) for r in rows] == [(12_000, 1_200_000), (13_000, 1_300_000)]
    assert give(yq, t['T-01'], 150)['ok']                         # FIFO: 100 × 12 000 + 50 × 13 000
    from surxon.fuel import give_cost, op
    with app.app_context():
        assert give_cost(get_db(), op(scalar("SELECT id FROM fuel_ops WHERE kind='BERISH'"))) == 1_850_000
    assert ticket_balance(app) == 3_500_000
    # closing the ticket keeps the keeper's liters; a closed ticket cannot be used
    assert bux.post('/yoqilgi/boshqaruv', {'action': 'close', 'ticket_id': tid, 'station_balance': '3 500 000'}).get_json()['ok']
    assert liters(app) == 50
    r = take(yq, zp, '10')
    assert not r['ok'] and 'faol tiket' in r['error']
    # the accountant voids a give (kept in history), the liters come back
    with app.app_context():
        gid = scalar("SELECT id FROM fuel_ops WHERE kind='BERISH'")
        first_take = scalar("SELECT id FROM fuel_ops WHERE kind='OLISH' ORDER BY id LIMIT 1")
    assert not bux.post('/yoqilgi/boshqaruv', {'action': 'void', 'op_id': first_take, 'reason': 'xato'}).get_json()['ok']
    assert bux.post('/yoqilgi/boshqaruv', {'action': 'void', 'op_id': gid, 'reason': 'Noto‘g‘ri texnika'}).get_json()['ok']
    assert liters(app) == 200


def test_machines_fuel_type_and_carrier(app, world):
    yq, zp, t = setup(app, world)
    admin, rahbar = world['admin'], world['rahbar']
    admin.post('/admin/texnikalar', {'kind': 'mashina', 'code': 'LABO-1', 'plate': '95 A 123 BA'})
    with app.app_context():
        labo = scalar("SELECT id FROM equipment WHERE code='LABO-1'")
    assert rahbar.post('/yoqilgi/boshqaruv', {'action': 'machine', 'equipment_id': labo, 'fuel_type': 'benzin', 'carrier': '1'}).get_json()['ok']
    with app.app_context():
        tok = scalar('SELECT qr_token FROM equipment WHERE id=?', (labo,))
    r = scan(yq, f'SPX-YQ:T:{tok}', 'equipment')
    assert not r['ok'] and 'benzin' in r['error']                 # diesel only to diesel machines
    # the QR sheet prints approved stations and diesel machines
    pdf = world['bux'].get('/yoqilgi/qr.pdf')
    assert pdf.status_code == 200 and pdf.data[:4] == b'%PDF'


def test_roles_and_access(app, world):
    yq, zp, t = setup(app, world)
    other = keeper(app, world['admin'], 'ikkinchi_y')
    assert take(yq, zp, '50')['ok']
    with app.app_context():
        oid = scalar('SELECT id FROM fuel_ops')
    # the keeper lands on the fuel screen and sees only fuel screens and own operations
    assert yq.get('/').headers['Location'].endswith('/yoqilgi')
    assert 'SOLYARKA OLISH' in yq.get('/yoqilgi').get_data(as_text=True)
    for url in ('/yoqilgi/boshqaruv', '/hamyon', '/buxgalteriya', '/telashkalar', '/foto'):
        assert yq.get(url).status_code == 302, url
    assert other.get(f'/yoqilgi/amal/{oid}').status_code == 404
    assert yq.get(f'/yoqilgi/amal/{oid}').status_code == 200
    # the keeper cannot run the accountant's actions
    assert yq.post('/yoqilgi/boshqaruv', {'action': 'price', 'ticket_id': 1, 'price_per_l': '1'}).status_code in (302, 403)
    # director sees the overview; cannot change tickets
    page = world['rahbar'].get('/yoqilgi/boshqaruv').get_data(as_text=True)
    assert 'Tiketlar' in page and 'Tiket ochish' not in page
    r = world['rahbar'].post('/yoqilgi/boshqaruv', {'action': 'price', 'ticket_id': 1, 'price_per_l': '1'}).get_json()
    assert not r['ok']
    # a station whose QR is not approved cannot be used
    world['bux'].post('/yoqilgi/boshqaruv', {'action': 'station', 'code': 'ZP-02', 'name': 'Boshqa'})
    with app.app_context():
        tok = scalar("SELECT qr_token FROM fuel_stations WHERE code='ZP-02'")
    assert not scan(yq, f'SPX-YQ:Z:{tok}', 'station')['ok']


def test_second_give_hours_later_is_not_flagged(app, world):
    """Times are compared in the zone they are written in (the ‘again too soon’ check once misfired by hours)."""
    yq, zp, t = setup(app, world)
    assert take(yq, zp, '200')['ok']
    assert give(yq, t['T-01'], 50)['ok']
    with app.app_context():
        get_db().execute("UPDATE fuel_ops SET created_at=datetime(created_at, '-3 hours') WHERE kind='BERISH'")
    s = scan(yq, t['T-01'], 'equipment')
    assert yq.post('/yoqilgi/berish/tekshir', {'scan_id': s['scan_id'], 'liters': '40'}).get_json()['flags'] == []


def test_role_is_offered_on_user_pages(app, world):
    admin = world['admin']
    for url in ('/admin/foydalanuvchilar', '/admin/xodimlar'):
        assert 'Yoqilg‘i mas’uli' in admin.get(url).get_data(as_text=True), url
