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
    assert f'TL-{year}-000026' in home and 'Yo‘lda' in home and '3 700 kg' in home and wb['number'] in home
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
    assert '3 700 kg' in page and 'Jo‘natilgan' in page and 'dala hisobi' in page

    # 7. KELDI
    assert yunus.post(f'/punkt/yuk/{wid}/keldi', {}).get_json()['ok']
    assert yunus.get('/punkt/holat').get_json()['counts']['keldi'] == 1

    # 8-10. punkt scale 3 480 kg → −220 kg (−5.95 %) → reason is required
    r = yunus.post(f'/punkt/yuk/{wid}/qabul', {'station_kg': '3480'}).get_json()
    assert r['ok'] is False and 'sabab' in r['error'].lower()
    r = yunus.post(f'/punkt/yuk/{wid}/qabul', {'station_kg': '3480', 'reason': 'Paxta to‘kilgan'}).get_json()
    assert r['ok'], r
    assert (r['diff_kg'], r['diff_pct'], r['level']) == (-220, -5.95, 'alert')

    # 11. QABUL QILINDI — a second tap never makes a second receipt
    r2 = yunus.post(f'/punkt/yuk/{wid}/qabul', {'station_kg': '3400', 'reason': 'Boshqa sabab', 'note': 'xato'}).get_json()
    assert r2['ok'] and r2.get('already')
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM nayman_receipts WHERE waybill_id=?', (wid,)) == 1
        rec = q('SELECT * FROM nayman_receipts WHERE waybill_id=?', (wid,), one=True)
        assert (rec['accepted_kg'], rec['diff_kg'], rec['diff_reason']) == (3480, -220, 'Paxta to‘kilgan')
        assert q('SELECT status FROM waybills WHERE id=?', (wid,), one=True)['status'] == 'QABUL'
        acts = [a['action'] for a in q("SELECT action FROM audit_logs WHERE entity_type='waybill' AND entity_id=? ORDER BY id",
                                       (str(wid),))]
        assert 'ARRIVED' in acts and 'RECEIVE' in acts
    done = norm(yunus.get(f'/punkt/yuk/{wid}').get_data(as_text=True))
    assert '4. Qabul qilindi' in done and '3 480 kg' in done and '-220 kg' in done and 'Tasdiqlangan nakladnoy' in done

    # reports see it
    rep = world['admin'].get('/hisobot/punktlar').get_data(as_text=True)
    assert 'Nayman-1' in rep
    rep = world['admin'].get('/hisobot/farq-sabablari').get_data(as_text=True)
    assert 'Paxta to‘kilgan' in rep


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


def test_nakladnoy_pdf_on_phone_view_share_download(app, world):
    """No printer in the field: the tally clerk opens / shares / downloads the punkt copy from the phone.
    Opening it again never makes a new waybill or a new document; the copy has kg but no price or wages."""
    tally, yunus, ali, st = setup(app, world)
    with app.app_context():
        get_db().execute("INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES ('price_per_kg','9876','x')")
    lid = tally.post('/telashkalar/ochish', {'trailer_id': world['eq']['TL-02'], 'field_id': world['f']['D-04'],
                                            'client_uuid': uuid4()}).get_json()['load_id']
    assert add(tally, lid, 'Gulbohar opa', '85')['ok'] and add(tally, lid, 'Mirjalol', '95')['ok']
    r = tally.post(f'/yuk/{lid}/toldi', files={'photos': jpeg()}).get_json()
    assert r['ok']
    wid = r['waybill_id']
    page = tally.get(r['redirect']).get_data(as_text=True)          # the tally clerk lands on the trip page
    assert 'data-pdf-share' in page and 'PDFni yuklab olish' in page and f'/nakladnoy/{wid}/nakladnoy.pdf' in page
    with app.app_context():
        docs_before = scalar('SELECT COUNT(*) FROM documents WHERE waybill_id=?', (wid,))
        wbs_before = scalar('SELECT COUNT(*) FROM waybills')
    view = tally.get(f'/nakladnoy/{wid}/nakladnoy.pdf')
    assert view.status_code == 200 and view.mimetype == 'application/pdf' and view.data[:4] == b'%PDF'
    assert 'attachment' not in view.headers.get('Content-Disposition', '')
    dl = tally.get(f'/nakladnoy/{wid}/nakladnoy.pdf?download=1')
    assert 'attachment' in dl.headers['Content-Disposition'] and 'nakladnoy.pdf' in dl.headers['Content-Disposition']
    assert dl.data == view.data                                     # the very same document every time
    text = norm(pdf_text(view.data))
    assert '180' in text and 'PUNKT UCHUN NAKLADNOY' in text
    assert '9876' not in text and '9 876' not in text and 'so‘m' not in text and 'Gulbohar' not in text
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM documents WHERE waybill_id=?', (wid,)) == docs_before
        assert scalar('SELECT COUNT(*) FROM waybills') == wbs_before
    # other roles/brigades: a brigadier of another brigade may not fetch it; unknown ids are 404
    assert world['nurim'].get(f'/nakladnoy/{wid}/nakladnoy.pdf').status_code == 403
    assert tally.get('/nakladnoy/99999/nakladnoy.pdf').status_code == 404


def test_confirmed_waybill_with_stamp_verify_and_paper_photo(app, world):
    tally, yunus, ali, _st = setup(app, world)
    lid = open_load(tally, world)
    for i in range(4):
        assert add(tally, lid, f'Akmal {i}', '250')['ok']
    assert tally.post(f'/yuk/{lid}/toldi', files={'photos': jpeg()}).get_json()['ok']
    with app.app_context():
        wid = q('SELECT id FROM waybills WHERE load_id=?', (lid,), one=True)['id']
    assert yunus.get(f'/punkt/yuk/{wid}/qabul.pdf').status_code == 404               # nothing to confirm before receipt
    r = yunus.post(f'/punkt/yuk/{wid}/qabul', {'gross_kg': '3710', 'tare_kg': '2700', 'reason': 'Namlik o‘zgarishi'}).get_json()
    assert r['ok']
    pdf = yunus.get(f'/punkt/yuk/{wid}/qabul.pdf')
    assert pdf.status_code == 200 and pdf.data[:4] == b'%PDF'
    text = norm(pdf_text(pdf.data))
    assert 'TASDIQLANGAN NAKLADNOY' in text and '1 010 kg' in text and '3 710 kg' in text and 'Elektron muhr' in text
    assert 'QABUL QILINDI' in text and 'Tekshiruv kodi:' in text and 'so‘m' not in text
    code = text.split('Tekshiruv kodi:')[1].split()[0]
    anon = app.test_client()
    ok = anon.get(f'/tekshir/{wid}/{code}')
    assert ok.status_code == 200 and 'Muhr haqiqiy' in ok.get_data(as_text=True) and '1 010 kg' in norm(ok.get_data(as_text=True))
    assert anon.get(f'/tekshir/{wid}/AAAAAAAAAA').status_code == 404
    # the paper with the punkt's real stamp: photographed and kept; the company sees the confirmed PDF too
    assert yunus.post(f'/punkt/yuk/{wid}/pechat', files={'photo': jpeg((10, 10, 200))}).get_json()['ok']
    assert 'Pechatli qog‘oz' in yunus.get(f'/punkt/yuk/{wid}').get_data(as_text=True)
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM photos WHERE entity_type='waybill_stamp' AND entity_id=?", (wid,)) == 1
    assert 'pechatli qog‘oz rasmi tizimda' in norm(pdf_text(yunus.get(f'/punkt/yuk/{wid}/qabul.pdf').data))
    assert 'Punkt tasdiqlagan nakladnoy' in world['admin'].get(f'/nakladnoy/{wid}').get_data(as_text=True)
    assert ali.get(f'/punkt/yuk/{wid}/qabul.pdf').status_code == 403                 # another punkt: no


def test_office_enters_punkt_kg_from_stamped_paper(app, world):
    """The punkt does not use the system: it weighs, writes its kg on the paper and stamps it. The office types the kg
    and uploads the photo — the confirmed PDF says so honestly."""
    tally, _yunus, _ali, _st = setup(app, world)
    lid = open_load(tally, world)
    assert add(tally, lid, 'Akmal', '200')['ok']
    assert tally.post(f'/yuk/{lid}/toldi', files={'photos': jpeg()}).get_json()['ok']
    with app.app_context():
        wid = q('SELECT id FROM waybills WHERE load_id=?', (lid,), one=True)['id']
    bux = world['bux']
    r = bux.post(f'/nayman/{wid}', {'accepted_kg': '198', 'received_date': '2026-09-27', 'diff_reason': 'Tarozi farqi'}).get_json()
    assert r['ok'], r
    assert bux.post(f'/nakladnoy/{wid}/pechat', files={'photo': jpeg((200, 30, 30))}).get_json()['ok']
    page = bux.get(f'/nakladnoy/{wid}').get_data(as_text=True)
    assert 'Pechatli qog‘oz nakladnoy' in page and 'Punkt tasdiqlagan nakladnoy' in page
    text = norm(pdf_text(bux.get(f'/punkt/yuk/{wid}/qabul.pdf').data))
    assert 'korxona kiritdi' in text and '198 kg' in text and 'pechatli qog‘oz rasmi tizimda' in text
    assert world['juma'].post(f'/nakladnoy/{wid}/pechat', files={'photo': jpeg()}).status_code in (302, 403)
