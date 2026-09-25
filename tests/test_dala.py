"""Field clerk's 4-step phone flow: open (rate so‘m/kg once) → person + kg → close with photo → two documents."""
from conftest import jpeg, make_user, uuid4
from surxon.db import get_db, q, scalar
from test_documents import pdf_text


def norm(t):
    return t.replace(' ', ' ').replace('\xa0', ' ')


def setup(app, world, username='mirjalol'):
    admin = world['admin']
    admin.post('/admin/sozlamalar', {'set_auto_waybill_hand': '1'})
    return make_user(app, admin, username, 'tally')


def open_trip(c, world, method='hand', rate='1500', trailer='TL-01', field='D-04'):
    r = c.post('/dala/yangi', {'field_id': world['f'][field], 'trailer_id': world['eq'][trailer], 'method': method,
                               'brigadier_id': world['b']['Juma ota' if field == 'D-04' else 'Nurim ota'], 'rate': rate,
                               'client_uuid': uuid4()}).get_json()
    assert r['ok'], r
    return r['load_id']


def weigh(c, lid, kg, name=None, worker_id=None, new=False, combine_id=None, cu=None, confirm=False):
    data = {'kg': kg, 'client_uuid': cu or uuid4()}
    if name:
        data['worker_name'] = name
    if worker_id:
        data['worker_id'] = worker_id
    if new:
        data['new_worker'] = '1'
    if combine_id:
        data['combine_id'] = combine_id
    if confirm:
        data['confirm_duplicate'] = '1'
    return c.post(f'/dala/reys/{lid}/tortish', data).get_json()


def test_hand_trip_rate_entry_close_and_two_documents(app, world):
    c = setup(app, world)
    assert c.get('/').headers['Location'].endswith('/dala')           # a clerk lands on the field screen
    page = c.get('/dala/yangi').get_data(as_text=True)
    assert 'Terim narxi' in page and 'so‘m/kg' in page and 'Qo‘l terimi' in page and 'Kombayn' in page
    lid = open_trip(c, world)
    with app.app_context():
        ld = q('SELECT * FROM trailer_loads WHERE id=?', (lid,), one=True)
        assert (ld['method'], ld['rate'], ld['rate_unit']) == ('hand', 1500, 'kg')
    trip = c.get(f'/dala/reys/{lid}').get_data(as_text=True)
    assert 'Saqlash va keyingisi' in trip and '1 500 so‘m/kg' in norm(trip)
    # 80 + 70 + 90, the first person is new (added right here), the same save sent twice counts once
    cu = uuid4()
    r = weigh(c, lid, '80', name='Ali Valiyev', new=True, cu=cu)
    assert r['ok'] and 'yangi odam' in r['message'] and r['totals']['kg'] == 80
    again = weigh(c, lid, '80', name='Ali Valiyev', new=True, cu=cu)
    assert again['ok'] and again['totals']['n'] == 1
    assert weigh(c, lid, '70', name='Vali')['ok']
    r = weigh(c, lid, '90', name='Soli')
    assert (r['totals']['people'], r['totals']['n'], r['totals']['kg'], r['totals']['amount']) == (3, 3, 240, 360_000)
    # Ali brings more cotton: a new weighing, the old one stays
    with app.app_context():
        ali = q("SELECT id FROM workers WHERE full_name='Ali Valiyev'", one=True)['id']
    r = weigh(c, lid, '20', worker_id=ali, name='Ali Valiyev')
    assert r['totals']['people'] == 3 and r['totals']['n'] == 4 and r['totals']['kg'] == 260
    # the same kg for the same person within minutes asks for confirmation instead of silently doubling
    r = weigh(c, lid, '20', worker_id=ali, name='Ali Valiyev')
    assert r['ok'] is False and 'takror' in r['error']
    assert weigh(c, lid, '20', worker_id=ali, name='Ali Valiyev', confirm=True)['ok']
    # a namesake is a different person when added as "+ Yangi odam"
    assert weigh(c, lid, '10', name='Vali', new=True)['ok']
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM workers WHERE full_name='Vali'") == 2
    # close: a photo is required; without it the trip stays open
    r = c.post(f'/dala/reys/{lid}/yopish', {})
    assert r.get_json()['ok'] is False
    with app.app_context():
        assert q('SELECT status FROM trailer_loads WHERE id=?', (lid,), one=True)['status'] == 'OCHIQ'
    r = c.post(f'/dala/reys/{lid}/yopish', files={'photos': jpeg()}).get_json()
    assert r['ok'] and r['redirect'].endswith(f'/dala/reys/{lid}/hujjatlar')
    again = c.post(f'/dala/reys/{lid}/yopish', files={'photos': jpeg((1, 2, 3))}).get_json()   # double tap
    assert again['ok']
    with app.app_context():
        ld = q('SELECT * FROM trailer_loads WHERE id=?', (lid,), one=True)
        wb = q('SELECT * FROM waybills WHERE load_id=?', (lid,), one=True)
        assert ld['status'] == 'TORTILDI' and wb['net_kg'] == 290 and scalar('SELECT COUNT(*) FROM waybills') == 1
    docs = c.get(f'/dala/reys/{lid}/hujjatlar').get_data(as_text=True)
    assert 'Ishchilar hisoboti' in docs and 'Punkt nakladnoyi' in docs and docs.count('data-pdf-share') == 2
    inner = norm(pdf_text(c.get(f'/dala/reys/{lid}/ishchilar.pdf').data))
    assert 'ISHCHILAR HISOBOTI' in inner and 'Ali Valiyev' in inner and 'Soli' in inner and '1 500' in inner
    assert '435 000' in inner                                         # 290 kg × 1 500
    outer = norm(pdf_text(c.get(f'/nakladnoy/{wb["id"]}/nakladnoy.pdf').data))
    assert '290' in outer and 'Dala hisobidagi kg' in outer and 'Ali' not in outer and '435' not in outer
    assert 'attachment' in c.get(f'/dala/reys/{lid}/ishchilar.pdf?download=1').headers['Content-Disposition']
    # after closing, nothing more can be added to this trip
    assert weigh(c, lid, '5', name='Soli')['ok'] is False


def test_rate_is_remembered_and_never_rewrites_old_trips(app, world):
    c = setup(app, world)
    lid1 = open_trip(c, world, rate='1500')
    assert weigh(c, lid1, '100', name='Akmal')['ok']
    form = c.get('/dala/yangi').get_data(as_text=True)
    assert 'value="1500"' in form                                     # last rate offered, not asked again
    lid2 = open_trip(c, world, rate='1700', trailer='TL-02')
    assert weigh(c, lid2, '100', name='Akmal')['ok']
    with app.app_context():
        amounts = [r['amount'] for r in q('SELECT amount FROM harvests ORDER BY id')]
        assert amounts == [150_000, 170_000]
    assert weigh(c, lid1, '10', name='Akmal')['ok']                   # the first trip keeps its own 1 500
    with app.app_context():
        assert q('SELECT rate, amount FROM harvests ORDER BY id DESC LIMIT 1', one=True)['amount'] == 15_000
    assert 'value="1700"' in c.get('/dala/yangi').get_data(as_text=True)
    assert c.post('/dala/yangi', {'field_id': world['f']['D-04'], 'trailer_id': world['eq']['TL-03'], 'method': 'hand',
                                  'brigadier_id': world['b']['Juma ota'], 'rate': '0', 'client_uuid': uuid4()}).get_json()['ok'] is False


def test_combine_trip_priced_per_kg(app, world):
    c = setup(app, world)
    admin = world['admin']
    with app.app_context():                                           # a day-tariff combine must not be paid twice
        get_db().execute("UPDATE equipment SET tariff_type='kunlik', tariff_rate=400000 WHERE code='K-01'")
    lid = open_trip(c, world, method='combine', rate='300')
    assert 'Kombayn' in c.get(f'/dala/reys/{lid}').get_data(as_text=True)
    r = weigh(c, lid, '1850', combine_id=world['eq']['K-01'])
    assert r['ok'] and r['totals']['amount'] == 555_000               # 1 850 kg × 300 so‘m/kg
    # a hand weighing cannot slip into a combine trip
    assert weigh(c, lid, '50', name='Ali')['ok'] is False
    with app.app_context():
        h = q('SELECT * FROM harvests WHERE load_id=?', (lid,), one=True)
        assert (h['rate'], h['rate_unit'], h['amount']) == (300, 'kg', 555_000)
        assert scalar('SELECT COUNT(*) FROM combine_work') == 0
        from surxon.accounting import combine_balances
        k = [x for x in combine_balances(scalar('SELECT MAX(year) FROM seasons')) if x['code'] == 'K-01'][0]
        assert k['earned'] == 555_000
    r = c.post(f'/dala/reys/{lid}/yopish', files={'photos': jpeg()}).get_json()
    assert r['ok']
    inner = norm(pdf_text(c.get(f'/dala/reys/{lid}/ishchilar.pdf').data))
    assert 'Kombayn K-01' in inner and '555 000' in inner


def test_clerk_sees_only_field_screens(app, world):
    c = setup(app, world)
    other = make_user(app, world['admin'], 'sadokat', 'tally')
    lid = open_trip(c, world)
    assert weigh(c, lid, '50', name='Ali')['ok']
    # everything outside the field work is closed server-side, whatever URL is typed
    for url in ('/foto', '/hisobotlar', '/xodimlar', '/admin/dalalar', '/admin/texnikalar', '/buxgalteriya', '/kassa',
                '/telashkalar', '/nakladnoy', '/kuzatuv'):
        r = c.get(url)
        assert r.status_code in (302, 403) and '/dala' in r.headers.get('Location', '/dala'), url
    # another clerk cannot open or write into my trip
    assert other.get(f'/dala/reys/{lid}').status_code == 403
    assert other.post(f'/dala/reys/{lid}/tortish', {'kg': '5', 'worker_name': 'X', 'client_uuid': uuid4()}).status_code == 403
    assert other.get(f'/dala/reys/{lid}/ishchilar.pdf').status_code == 403
    # a cash photo taken by the accountant is not visible to the clerk
    bux = world['bux']
    bux.post('/buxgalteriya/kirim', {'amount': '1 000 000', 'source': 'Direktor', 'client_uuid': uuid4()},
             files={'photo': jpeg((200, 10, 10))})
    with app.app_context():
        p = q("SELECT * FROM photos WHERE category='cash'", one=True)
    assert p and c.get(f'/media/{p["thumb_path"]}').status_code == 404
    assert world['juma'].get(f'/media/{p["thumb_path"]}').status_code == 404     # brigadier neither
    assert world['rahbar'].get(f'/media/{p["thumb_path"]}').status_code == 200   # the manager does
    assert p['thumb_path'] not in world['juma'].get('/foto').get_data(as_text=True)


def test_fifty_people_on_one_screen(app, world):
    c = setup(app, world)
    lid = open_trip(c, world)
    for i in range(50):
        assert weigh(c, lid, str(60 + i % 7), name=f'Terimchi {i:02d}')['ok']
    with app.app_context():
        t = q('SELECT COUNT(*) n, COUNT(DISTINCT worker_id) p, SUM(kg) kg, SUM(amount) a FROM harvests WHERE load_id=?',
              (lid,), one=True)
    assert (t['n'], t['p']) == (50, 50) and t['a'] == t['kg'] * 1500


def test_no_fields_message(app, world):
    c = setup(app, world)
    with app.app_context():
        get_db().execute('UPDATE fields SET active=0')
    page = c.get('/dala/yangi').get_data(as_text=True)
    assert 'Dala hali qo‘shilmagan' in page and 'Administrator dala qo‘shishi kerak' in page


def test_four_clerks_at_once_with_double_taps(app, world):
    """4 clerks open, weigh (every tap sent twice) and close at the same moment: unique trip and waybill numbers,
    no duplicated kg, one waybill per trip."""
    import threading
    world['admin'].post('/admin/sozlamalar', {'set_auto_waybill_hand': '1'})
    clerks = [make_user(app, world['admin'], n, 'tally') for n in ('hisobchi1', 'hisobchi2', 'hisobchi3', 'hisobchi4')]
    trailers = ['TL-01', 'TL-02', 'TL-03', 'TL-04']
    errors, gate = [], threading.Barrier(4)

    def work(i, c):
        try:
            gate.wait()
            data = {'field_id': world['f']['D-04'], 'trailer_id': world['eq'][trailers[i]], 'method': 'hand',
                    'brigadier_id': world['b']['Juma ota'], 'rate': '1500', 'client_uuid': uuid4()}
            r1, r2 = c.post('/dala/yangi', data).get_json(), c.post('/dala/yangi', data).get_json()
            assert r1['ok'] and r1['load_id'] == r2['load_id']
            for k in range(6):
                d = {'kg': str(50 + k), 'worker_name': f'T{i}-{k}', 'client_uuid': uuid4()}
                assert c.post(f'/dala/reys/{r1["load_id"]}/tortish', d).get_json()['ok']
                c.post(f'/dala/reys/{r1["load_id"]}/tortish', d)
            for _ in range(2):
                assert c.post(f'/dala/reys/{r1["load_id"]}/yopish', files={'photos': jpeg((i * 50, 60, 70))}).get_json()['ok']
        except Exception as e:                      # surfaced below
            errors.append(repr(e))
    threads = [threading.Thread(target=work, args=(i, c)) for i, c in enumerate(clerks)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    with app.app_context():
        trips = q('SELECT trip_no FROM trailer_loads')
        wbs = q('SELECT number, net_kg FROM waybills')
        assert len(trips) == 4 and len({t['trip_no'] for t in trips}) == 4
        assert len(wbs) == 4 and len({w['number'] for w in wbs}) == 4 and all(w['net_kg'] == 315 for w in wbs)
        assert scalar('SELECT COUNT(*) FROM harvests') == 24
