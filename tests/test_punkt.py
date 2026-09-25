"""Field → TUGATISH → PUNKTGA YO‘LDA → punkt scale → difference → QABUL QILINDI (the owner's test scenario)."""
import threading

from surxon.db import get_db, q, scalar
from conftest import jpeg, make_user, open_load, uuid4
from test_documents import pdf_text


def norm(text):
    return text.replace('\u202f', ' ').replace('\xa0', ' ')


def add(client, lid, name, kg):
    return client.post('/terim', {'load_id': lid, 'method': 'hand', 'worker_name': name, 'kg': kg,
                                  'client_uuid': uuid4()}).get_json()


def setup(app, world):
    admin = world['admin']
    assert admin.post('/admin/sozlamalar', {'set_auto_waybill_hand': '1'}).get_json()['ok']
    assert admin.post('/admin/punktlar', {'name': 'Nayman-2'}).get_json()['ok']
    with app.app_context():
        st = {r['name']: r['id'] for r in q('SELECT id, name FROM stations')}
    tally = make_user(app, admin, 'mirjalol', 'tally')
    r = admin.post('/admin/foydalanuvchilar', {'username': 'yunus', 'full_name': 'Yunus', 'role': 'station',
                                                'password': 'Worker2026x', 'station_id': st['Nayman-1']})
    assert r.get_json()['ok'], r.get_data(as_text=True)
    admin.post('/admin/foydalanuvchilar', {'username': 'ali', 'full_name': 'Ali', 'role': 'station',
                                           'password': 'Worker2026x', 'station_id': st['Nayman-2']})
    with app.app_context():
        get_db().execute("UPDATE users SET must_change_password=0 WHERE username IN ('yunus','ali')")
    from conftest import Client
    return tally, Client(app, 'yunus', 'Worker2026x'), Client(app, 'ali', 'Worker2026x'), st


def test_owner_scenario_field_to_punkt(app, world):
    tally, yunus, ali, st = setup(app, world)
    year = None
    with app.app_context():
        year = scalar('SELECT MAX(year) FROM seasons')
        # 25 trips already this season → the next one is TL-YYYY-000026, as in the owner's example
        get_db().execute("INSERT INTO counters(name, value) VALUES (?, 25)", (f'trip-{year}',))

    # 1-2. the tally clerk opens a trip; the server gives the number
    r = tally.post('/telashkalar/ochish', {'trailer_id': world['eq']['TL-01'], 'field_id': world['f']['D-04'],
                                          'tractor_id': world['eq']['T-01'], 'client_uuid': uuid4()}).get_json()
    assert r['ok'] and r['trip_no'] == f'TL-{year}-000026'
    lid = r['load_id']

    # 3. 50 pickers, 3 700 kg in total
    kgs = [74] * 50                                     # 50 × 74 = 3 700
    for i, kg in enumerate(kgs):
        assert add(tally, lid, f'Terimchi {i + 1:02d}', str(kg))['ok']

    # 4. TUGATISH
    r = tally.post(f'/yuk/{lid}/toldi', files={'photos': jpeg()}).get_json()
    assert r['ok'] and r['trip_no'] == f'TL-{year}-000026', r
    with app.app_context():
        ld = q('SELECT * FROM trailer_loads WHERE id=?', (lid,), one=True)
        wb = q('SELECT * FROM waybills WHERE load_id=?', (lid,), one=True)
        docs = {d['kind']: d for d in q('SELECT * FROM documents WHERE waybill_id=?', (wb['id'],))}
        cfg = app.config['SURXON']
        punkt_pdf = norm(pdf_text((cfg.UPLOAD_DIR / docs['nayman']['path']).read_bytes()))
        ichki_pdf = norm(pdf_text((cfg.UPLOAD_DIR / docs['ichki']['path']).read_bytes()))
    assert ld['status'] == 'TORTILDI' and ld['internal_kg'] == 3700          # locked
    assert (wb['net_kg'], wb['status'], wb['destination']) == (3700, 'YARATILDI', 'Nayman-1')
    # 2 documents: punkt waybill (no names, no price) and the internal harvest report (every picker's kg)
    assert f'TL-{year}-000026' in punkt_pdf and 'PUNKT UCHUN NAKLADNOY' in punkt_pdf and '3 700 kg' in punkt_pdf
    assert 'Terimchi 01' not in punkt_pdf and 'Narx' not in punkt_pdf
    assert 'ICHKI TERIM HISOBOTI' in ichki_pdf and 'Terimchi 01' in ichki_pdf and 'Terimchi 50' in ichki_pdf
    assert 'JAMI: 3 700 kg' in ichki_pdf
    # a closed trip takes no more kg from a normal user
    assert add(tally, lid, 'Kechikkan', '10')['ok'] is False

    # 5. Yunus (Nayman-1) sees it on the way; Ali (Nayman-2) does not
    home = norm(yunus.get('/punkt').get_data(as_text=True))
    assert f'TL-{year}-000026' in home and 'YO‘LDA' in home and '3 700 kg' in home
    assert f'TL-{year}-000026' not in ali.get('/punkt').get_data(as_text=True)
    st_json = yunus.get('/punkt/holat').get_json()
    assert st_json['counts']['yolda'] == 1 and st_json['newest']['trip_no'] == f'TL-{year}-000026'
    wid = wb['id']
    assert ali.get(f'/punkt/yuk/{wid}').status_code == 403
    assert ali.post(f'/punkt/yuk/{wid}/qabul', {'station_kg': '3480'}).status_code == 403

    # 6. paper waybill lost: found from the list, by number, by the QR link
    assert yunus.get('/punkt/topish?q=26').headers['Location'].endswith(f'/punkt/yuk/{wid}')
    assert yunus.get(f'/punkt/topish?q=TL-{year}-000026').headers['Location'].endswith(f'/punkt/yuk/{wid}')
    assert yunus.get(f'/punkt/q/TL-{year}-000026').headers['Location'].endswith(f'/punkt/yuk/{wid}')
    page = norm(yunus.get(f'/punkt/yuk/{wid}').get_data(as_text=True))
    assert '3 700 kg' in page and 'Daladagi vazn' in page

    # 7. KELDI
    assert yunus.post(f'/punkt/yuk/{wid}/keldi', {}).get_json()['ok']
    assert yunus.get('/punkt/holat').get_json()['counts']['keldi'] == 1

    # 8-10. punkt scale 3 480 kg → −220 kg (−5.95 %) → reason is required
    r = yunus.post(f'/punkt/yuk/{wid}/qabul', {'station_kg': '3480'}).get_json()
    assert r['ok'] is False and 'sabab' in r['error'].lower()
    r = yunus.post(f'/punkt/yuk/{wid}/qabul', {'station_kg': '3480', 'reason': 'Yo‘lda to‘kilgan / yo‘qotish'}).get_json()
    assert r['ok'], r
    assert (r['diff_kg'], r['diff_pct'], r['level']) == (-220, -5.95, 'alert')

    # 11. QABUL QILINDI — a second tap never makes a second receipt
    r2 = yunus.post(f'/punkt/yuk/{wid}/qabul', {'station_kg': '3400', 'reason': 'Boshqa', 'note': 'xato'}).get_json()
    assert r2['ok'] and r2.get('already')
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM nayman_receipts WHERE waybill_id=?', (wid,)) == 1
        rec = q('SELECT * FROM nayman_receipts WHERE waybill_id=?', (wid,), one=True)
        assert (rec['accepted_kg'], rec['diff_kg'], rec['diff_reason']) == (3480, -220, 'Yo‘lda to‘kilgan / yo‘qotish')
        assert q('SELECT status FROM waybills WHERE id=?', (wid,), one=True)['status'] == 'QABUL'
        acts = [a['action'] for a in q("SELECT action FROM audit_logs WHERE entity_type='waybill' AND entity_id=? ORDER BY id",
                                       (str(wid),))]
        assert 'ARRIVED' in acts and 'RECEIVE' in acts
    done = norm(yunus.get(f'/punkt/yuk/{wid}').get_data(as_text=True))
    assert 'Qabul qilindi!' in done and '-220 kg (-5.95%)' in done

    # reports see it
    rep = world['admin'].get('/hisobot/punktlar').get_data(as_text=True)
    assert 'Nayman-1' in rep
    rep = world['admin'].get('/hisobot/farq-sabablari').get_data(as_text=True)
    assert 'Yo‘lda to‘kilgan / yo‘qotish' in rep


def test_empty_trip_cannot_be_finished(app, world):
    tally, *_ = setup(app, world)
    lid = open_load(tally, world)
    r = tally.post(f'/yuk/{lid}/toldi', files={'photos': jpeg()}).get_json()
    assert r['ok'] is False and 'bo‘sh' in r['error']


def test_small_difference_needs_no_reason(app, world):
    tally, yunus, _ali, _st = setup(app, world)
    lid = open_load(tally, world)
    assert add(tally, lid, 'Akmal', '100')['ok']
    assert tally.post(f'/yuk/{lid}/toldi', files={'photos': jpeg()}).get_json()['ok']
    with app.app_context():
        wid = q('SELECT id FROM waybills WHERE load_id=?', (lid,), one=True)['id']
    r = yunus.post(f'/punkt/yuk/{wid}/qabul', {'station_kg': '99.5'}).get_json()   # −0.5 % → normal
    assert r['ok'] and r['level'] == 'ok'


def test_punkt_operator_is_locked_to_punkt_screens(app, world):
    _tally, yunus, _ali, _st = setup(app, world)
    for url in ('/', '/terim', '/telashkalar', '/nakladnoylar', '/kassa', '/admin/foydalanuvchilar', '/hisobotlar', '/foto',
                '/qidiruv?q=a'):
        r = yunus.get(url)
        assert r.status_code in (302, 403), url
        if r.status_code == 302:
            assert r.headers['Location'].endswith('/punkt'), (url, r.headers['Location'])
    assert yunus.get('/punkt').status_code == 200
    # service layer checks too, not only the screens
    from surxon.security import Actor
    from surxon.services import open_load as svc_open
    from surxon.utils import UserError
    import pytest
    with app.test_request_context():
        uid = q("SELECT id FROM users WHERE username='yunus'", one=True)['id']
        with pytest.raises(UserError):
            svc_open(Actor(uid, 'station'), trailer_id=world['eq']['TL-02'], field_id=world['f']['D-04'], brigadier_id=None)


def test_trip_numbers_unique_under_parallel_opening(app, world):
    tally, *_ = setup(app, world)
    from surxon.security import Actor
    from surxon.services import open_load as svc_open
    with app.app_context():
        uid = q("SELECT id FROM users WHERE username='mirjalol'", one=True)['id']
    actor = Actor(uid, 'tally')
    numbers, errors = [], []

    def run(tl):
        try:
            with app.test_request_context():
                lid = svc_open(actor, trailer_id=world['eq'][tl], field_id=world['f']['D-04'], brigadier_id=None)
                numbers.append(q('SELECT trip_no FROM trailer_loads WHERE id=?', (lid,), one=True)['trip_no'])
        except Exception as e:  # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=run, args=(tl,)) for tl in ('TL-01', 'TL-02', 'TL-03', 'TL-04')]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and len(set(numbers)) == 4
    assert sorted(int(n[-6:]) for n in numbers) == [1, 2, 3, 4]
