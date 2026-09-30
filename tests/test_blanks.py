"""Punkt blanks: printed in advance (numbered, with a QR), filled by hand at the punkt, photographed and tied to the
trip — without the blank's photo the trip cannot be received; one blank serves one trip only; the office sees which
blank went where and which numbers were skipped."""
import re

from conftest import jpeg, uuid4
from surxon.db import q
from test_documents import pdf_text
from test_punkt import add, setup


def trip_to_punkt(tally, world, trailer='TL-01'):
    r = tally.post('/telashkalar/ochish', {'trailer_id': world['eq'][trailer], 'field_id': world['f']['D-04'],
                                          'tractor_id': world['eq']['T-01'], 'client_uuid': uuid4()}).get_json()
    for i in range(4):
        assert add(tally, r['load_id'], f'Terimchi {i}', '120')['ok']
    assert tally.post(f'/yuk/{r["load_id"]}/toldi', files={'photos': jpeg()}).get_json()['ok']
    return r['load_id']


def test_blanks_print_attach_required_once_and_office_check(app, world):
    tally, yunus, ali, st = setup(app, world)
    admin = world['admin']
    l1 = trip_to_punkt(tally, world)
    with app.app_context():
        w1 = q('SELECT id FROM waybills WHERE load_id=?', (l1,), one=True)['id']

    # before any blank is printed the punkt receives as before (nothing breaks)
    # print 600 blanks: numbered PB-0001 … PB-0600, one A4 page each with our part and the punkt's part
    r = admin.post('/admin/blankalar', {'count': '600'})
    assert r.get_json()['ok'], r.get_data(as_text=True)
    pdf = admin.get('/admin/blankalar/1.pdf')
    assert pdf.status_code == 200 and len(re.findall(rb'/Type\s*/Page[^s]', pdf.data)) == 1200      # 2 copies of each number
    one = admin.get('/admin/blankalar/1.pdf?nusxa=1')
    assert len(re.findall(rb'/Type\s*/Page[^s]', one.data)) == 600
    first = pdf_text(pdf.data[:]).split('\n')
    text = pdf_text(pdf.data)
    assert 'PB-0001' in text and 'PB-0600' in text and 'QABUL QILINGAN PAXTA (NETTO)' in text and 'KOMBAYN' in text
    assert '1-NUSXA' in text and '2-NUSXA' in text and 'JO‘NATILGAN PAXTA (NETTO)' in text
    assert admin.post('/admin/blankalar', {'action': 'receiver', 'receiver': 'NAMUNA KLASTER MCHJ (STIR 300000000)'}).get_json()['ok']
    assert 'QABUL QILUVCHI: NAMUNA KLASTER MCHJ' in pdf_text(admin.get('/admin/blankalar/1.pdf?nusxa=1').data)
    assert admin.post('/admin/blankalar', {'count': '5000'}).get_json()['ok'] is False
    with app.app_context():
        b3 = q("SELECT * FROM punkt_blanks WHERE number='PB-0003'", one=True)
        b1 = q("SELECT * FROM punkt_blanks WHERE number='PB-0001'", one=True)

    # now the punkt cannot close the trip without the filled blank's number and photo
    page = yunus.get(f'/punkt/yuk/{w1}').get_data(as_text=True)
    assert 'blank_code' in page and 'blank_photo' in page
    no_blank = yunus.post(f'/punkt/yuk/{w1}/qabul', {'gross_kg': '5470', 'tare_kg': '5000'}).get_json()
    assert no_blank['ok'] is False and 'blank' in no_blank['error'].lower()
    no_photo = yunus.post(f'/punkt/yuk/{w1}/qabul', {'gross_kg': '5470', 'tare_kg': '5000', 'blank_code': 'PB-0003'}).get_json()
    assert no_photo['ok'] is False and 'rasm' in no_photo['error'].lower()

    # the phone camera opens the blank's QR link → the trips waiting for a blank → tap → the trip with the number filled in
    scan = yunus.get(f'/punkt/blanka/{b3["token"]}').get_data(as_text=True)
    assert 'PB-0003' in scan and f'blanka=PB-0003' in scan
    assert 'value="PB-0003"' in yunus.get(f'/punkt/yuk/{w1}?blanka=PB-0003').get_data(as_text=True)
    ok = yunus.post(f'/punkt/yuk/{w1}/qabul', {'gross_kg': '5470', 'tare_kg': '5000', 'blank_code': 'PB-0003',
                                                'reason': 'Tarozilar farqi'}, files={'blank_photo': jpeg((200, 10, 10))}).get_json()
    assert ok['ok'], ok
    with app.app_context():
        b = q("SELECT * FROM punkt_blanks WHERE number='PB-0003'", one=True)
        assert b['waybill_id'] == w1 and b['photo_id']
    assert yunus.get(f'/punkt/blanka/{b3["token"]}').headers['Location'].endswith(f'/punkt/yuk/{w1}')   # used → its trip

    # one blank = one trip: the same blank on the next trip is refused
    l2 = trip_to_punkt(tally, world, trailer='TL-02')
    with app.app_context():
        w2 = q('SELECT id FROM waybills WHERE load_id=?', (l2,), one=True)['id']
    again = yunus.post(f'/punkt/yuk/{w2}/qabul', {'station_kg': '480', 'blank_code': 'PB-0003'},
                       files={'blank_photo': jpeg()}).get_json()
    assert again['ok'] is False and 'allaqachon' in again['error']
    # another punkt cannot attach to this punkt's trip
    assert ali.post(f'/punkt/yuk/{w2}/blanka', {'blank_code': 'PB-0005'}, files={'blank_photo': jpeg()}).status_code in (302, 403)

    # the office check: 1 used, PB-0001 and PB-0002 skipped (a later one was used), the rest blank
    body = admin.get('/admin/blankalar').get_data(as_text=True)
    assert 'Reysga biriktirilgan 1' in body and 'O‘tkazib yuborilgan 2' in body
    skipped = admin.get('/admin/blankalar?holat=otkazilgan').get_data(as_text=True)
    assert 'PB-0001' in skipped and 'PB-0002' in skipped and 'PB-0004' not in skipped
    assert admin.post('/admin/blankalar', {'action': 'spoil', 'id': b1['id'], 'reason': 'yirtilgan'}).get_json()['ok']
    assert yunus.post(f'/punkt/yuk/{w2}/qabul', {'station_kg': '480', 'blank_code': 'PB-0001'},
                      files={'blank_photo': jpeg()}).get_json()['ok'] is False            # spoiled blank never reused
    # the office trip page shows the blank
    assert 'Punkt blankasi: PB-0003' in admin.get(f'/nakladnoy/{w1}').get_data(as_text=True)
    assert admin.get(f'/admin/blankalar/rasm/{b["id"]}').status_code == 302
    # switched off → receiving works without a blank again
    admin.post('/admin/sozlamalar', {'set_punkt_blank_required': '0'})
    assert yunus.post(f'/punkt/yuk/{w2}/qabul', {'station_kg': '480'}).get_json()['ok']


def test_clerk_scans_blank_when_closing_and_punkt_only_photographs(app, world):
    """The field clerk scans the paper blank's QR while closing the trailer: the blank is tied to the trip there.
    A blank already used elsewhere is refused before anything closes; the punkt then only photographs the paper."""
    from test_dala import open_trip, weigh
    tally, yunus, ali, st = setup(app, world)
    admin = world['admin']
    admin.post('/admin/blankalar', {'count': '5'})
    with app.app_context():
        tok2 = q("SELECT token FROM punkt_blanks WHERE number='PB-0002'", one=True)['token']
    l1 = open_trip(tally, world)
    assert weigh(tally, l1, '150', name='Ali', new=True)['ok'] and weigh(tally, l1, '150', name='Soli', new=True)['ok']
    page = tally.get(f'/dala/reys/{l1}/yopish').get_data(as_text=True)
    assert 'blank_code' in page and 'Skanerlash' in page and 'pk-map' not in page       # no “mark the picked part” map
    r = tally.post(f'/dala/reys/{l1}/yopish', {'blank_code': f'https://x/punkt/blanka/{tok2}'}, files={'photos': jpeg()}).get_json()
    assert r['ok'] and 'PB-0002 biriktirildi' in r['message'], r
    with app.app_context():
        w1 = q('SELECT id FROM waybills WHERE load_id=?', (l1,), one=True)['id']
        b = q("SELECT * FROM punkt_blanks WHERE number='PB-0002'", one=True)
        assert b['waybill_id'] == w1 and b['photo_id'] is None
    docs = tally.get(f'/dala/reys/{l1}/hujjatlar').get_data(as_text=True)
    assert 'Terilgan joyni belgilash' not in docs
    # the same blank on another trailer: refused, and that trailer stays open
    l2 = open_trip(tally, world, trailer='TL-02')
    assert weigh(tally, l2, '200', name='Vali', new=True)['ok']
    r = tally.post(f'/dala/reys/{l2}/yopish', {'blank_code': 'PB-0002'}, files={'photos': jpeg()}).get_json()
    assert r['ok'] is False and 'allaqachon' in r['error']
    with app.app_context():
        assert q('SELECT status FROM trailer_loads WHERE id=?', (l2,), one=True)['status'] == 'OCHIQ'
    # the punkt: the blank is known, only its photo is asked
    no_photo = yunus.post(f'/punkt/yuk/{w1}/qabul', {'station_kg': '298'}).get_json()
    assert no_photo['ok'] is False and 'rasm' in no_photo['error'].lower()
    ok = yunus.post(f'/punkt/yuk/{w1}/qabul', {'station_kg': '298'}, files={'blank_photo': jpeg()}).get_json()
    assert ok['ok'], ok
    with app.app_context():
        assert q("SELECT photo_id FROM punkt_blanks WHERE number='PB-0002'", one=True)['photo_id']


def test_combine_choices_show_the_owner(app, world):
    from test_dala import open_trip
    tally, yunus, ali, st = setup(app, world)
    with app.app_context():
        from surxon.db import get_db
        get_db().execute("UPDATE equipment SET ownership='external', operator_name='Farhod aka' WHERE code='K-02'")
    lid = open_trip(tally, world, method='combine', rate='300')
    page = tally.get(f'/dala/reys/{lid}').get_data(as_text=True)
    assert 'K-02 · Farhod aka (xizmat)' in page and 'K-01 · SURXON' in page
