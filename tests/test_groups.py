"""Umumiy yuk (UY): one tractor tows several trailers from different fields, the punkt weighs them once on one paper.
Yunus scans the UY paper, then every trailer's field blank; the list and the total build themselves. After the weighing
the punkt netto is shared over the trailers by their field kg, every trailer is QABUL, and one PQ-17 ties to the group."""
import io
import re

from conftest import jpeg
from surxon.db import q
from test_pq17 import pq17_pdf
from test_punkt import setup


def closed_trip(app, tally, world, trailer, kgs, blank=None, method='hand', combine=None):
    from test_dala import open_trip, weigh
    lid = open_trip(tally, world, trailer=trailer, method=method, rate='1500' if method == 'hand' else '300')
    for i, kg in enumerate(kgs):
        r = weigh(tally, lid, kg, name=f'{trailer} terimchi {i}', new=True, combine_id=combine, confirm=True)
        assert r['ok'], r
    data = {'blank_code': blank} if blank else {}
    r = tally.post(f'/dala/reys/{lid}/yopish', data, files={'photos': jpeg()}).get_json()
    assert r['ok'], r
    with app.app_context():
        return q('SELECT id FROM waybills WHERE load_id=?', (lid,), one=True)['id']


def test_umumiy_yuk_scan_close_weigh_split_and_pq17(app, world):
    tally, yunus, ali, st = setup(app, world)
    admin, bux = world['admin'], world['bux']
    admin.post('/admin/blankalar', {'count': '10'})
    r = admin.post('/admin/uy-blankalar', {'count': '3'}).get_json()
    assert r['ok'], r
    pdf = admin.get('/admin/uy-blankalar/1.pdf')
    assert pdf.status_code == 200 and pdf.data[:4] == b'%PDF'
    assert 'UY-0008' in admin.get('/admin/blankalar').get_data(as_text=True)
    uy2 = admin.get('/admin/uy-blankalar/chop.pdf?dan=8&soni=2')
    assert uy2.status_code == 200 and len(re.findall(rb'/Type\s*/Page[^s]', uy2.data)) == 2
    with app.app_context():
        uy = {r['number']: r for r in q('SELECT * FROM load_groups')}
        k2 = q("SELECT id FROM equipment WHERE code='K-02'", one=True)['id']
        from surxon.db import get_db
        get_db().execute("UPDATE equipment SET ownership='external', operator_name='Farhod aka' WHERE code='K-02'")
    assert set(uy) == {'UY-0008', 'UY-0009', 'UY-0010'} and len({u['token'] for u in uy.values()}) == 3

    w1 = closed_trip(app, tally, world, 'TL-01', ['140', '160'], blank='PB-0001')              # 300
    w2 = closed_trip(app, tally, world, 'TL-02', ['200', '100'], blank='PB-0002')              # 300
    w3 = closed_trip(app, tally, world, 'TL-03', ['190', '210'], method='combine', combine=k2)  # 400, blank not scanned

    # 1. Yunus scans the UY paper → it opens at his punkt
    page = yunus.get(f'/punkt/uy/{uy["UY-0008"]["token"]}').get_data(as_text=True)
    assert 'UY-0008' in page
    with app.app_context():
        g = q("SELECT * FROM load_groups WHERE number='UY-0008'", one=True)
    assert g['status'] == 'OCHIQ' and g['station_id'] == st['Nayman-1']
    gid = g['id']
    assert ali.get(f'/punkt/uy/{g["token"]}').status_code in (302, 403)                      # another punkt: no

    # 2. every trailer's blank: the list and the total grow
    with app.app_context():
        tok1 = q("SELECT token FROM punkt_blanks WHERE number='PB-0001'", one=True)['token']
    r = yunus.post(f'/punkt/uy/{gid}/qosh', {'code': f'https://surxan-paxta.uz/punkt/blanka/{tok1}'}).get_json()
    assert r['ok'] and r['n'] == 1 and r['kg'] == 300, r
    again = yunus.post(f'/punkt/uy/{gid}/qosh', {'code': 'PB-0001'}).get_json()
    assert again['ok'] is False and 'allaqachon shu ro‘yxatda' in again['error']
    assert yunus.post(f'/punkt/uy/{gid}/qosh', {'code': 'PB-0002'}).get_json()['kg'] == 600
    unknown = yunus.post(f'/punkt/uy/{gid}/qosh', {'code': 'PB-9999'}).get_json()
    assert unknown['ok'] is False and 'topilmadi' in unknown['error']
    # a blank the clerk did not scan in the field: pick its trailer once
    r = yunus.post(f'/punkt/uy/{gid}/qosh', {'code': 'PB-0003'}).get_json()
    assert r['ok'] is False and r.get('need_trip') and 'biriktirilmagan' in r['error']
    r = yunus.post(f'/punkt/uy/{gid}/qosh', {'code': 'PB-0003', 'waybill_id': w3}).get_json()
    assert r['ok'] and r['n'] == 3 and r['kg'] == 1000, r
    # the same trailer cannot go into another UY
    g2 = yunus.get(f'/punkt/uy/{uy["UY-0009"]["token"]}')
    with app.app_context():
        gid2 = q("SELECT id FROM load_groups WHERE number='UY-0009'", one=True)['id']
    other = yunus.post(f'/punkt/uy/{gid2}/qosh', {'code': 'PB-0002'}).get_json()
    assert g2.status_code == 200 and other['ok'] is False and 'UY-0008' in other['error']

    page = yunus.get(f'/punkt/uy/{g["token"]}').get_data(as_text=True)
    assert 'Farhod aka (xizmat)' in page and 'Qo‘l terimi' in page
    home = yunus.get('/punkt').get_data(as_text=True)                                          # whose cotton, on each card
    assert 'K-02 · Farhod aka (xizmat)' in home and '✋ Qo‘l terimi' in home and 'UY-0008' in home

    # 3. “Hammasi shu”: the paper gets 3 trailers, 1 000 kg
    assert yunus.post(f'/punkt/uy/{gid}/yopish').get_json()['ok']
    page = yunus.get(f'/punkt/uy/{g["token"]}').get_data(as_text=True)
    assert '1000' in page.replace('\u00a0', '').replace('\u202f', '').replace(' ', '') and 'brutto' in page.lower()
    late = yunus.post(f'/punkt/uy/{gid}/qosh', {'code': 'PB-0004'}).get_json()
    assert late['ok'] is False

    # 4. after the weighing: brutto / tara / yuk xati + photo → shared by field kg, all QABUL
    no_photo = yunus.post(f'/punkt/uy/{gid}/qabul', {'gross_kg': '15985', 'tare_kg': '15000', 'load_no': '777001'}).get_json()
    assert no_photo['ok'] is False and 'rasm' in no_photo['error'].lower()
    no_reason = yunus.post(f'/punkt/uy/{gid}/qabul', {'gross_kg': '15900', 'tare_kg': '15000', 'load_no': '777001'},
                           files={'photo': jpeg()}).get_json()
    assert no_reason['ok'] is False and 'sabab' in no_reason['error'].lower()
    r = yunus.post(f'/punkt/uy/{gid}/qabul', {'gross_kg': '15985', 'tare_kg': '15000', 'load_no': '777001',
                                               'reason': 'Tarozilar farqi'}, files={'photo': jpeg()}).get_json()
    assert r['ok'] and 'QABUL QILINDI' in r['message'], r
    assert yunus.post(f'/punkt/uy/{gid}/qabul', {'station_kg': '985'}, files={'photo': jpeg()}).get_json()['ok']   # 2nd tap: no-op
    with app.app_context():
        rec = {r['waybill_id']: r for r in q('SELECT * FROM nayman_receipts')}
        assert len(rec) == 3
        assert (rec[w1]['accepted_kg'], rec[w2]['accepted_kg'], rec[w3]['accepted_kg']) == (295.5, 295.5, 394.0)
        assert round(sum(r['accepted_kg'] for r in rec.values()), 1) == 985
        assert {r['load_no'] for r in rec.values()} == {'777001'}
        assert {q('SELECT status FROM waybills WHERE id=?', (w,), one=True)['status'] for w in (w1, w2, w3)} == {'QABUL'}
        g = q('SELECT * FROM load_groups WHERE id=?', (gid,), one=True)
        assert g['status'] == 'QABUL' and g['accepted_kg'] == 985 and g['sent_kg'] == 1000 and g['photo_id']
    assert yunus.post(f'/punkt/uy/{gid}/ochish').get_json()['ok'] is False                   # received: stays closed
    wl = admin.get('/nakladnoylar').get_data(as_text=True)                                    # the waybill list marks them
    assert wl.count('🚜 UY-0008') == 3

    # 5. one PQ-17 from the cluster for the whole load → shared over the three trailers
    bux.post('/buxgalteriya/narx', {'hand': '7800', 'combine': '7600'})
    r = bux.c.post('/buxgalteriya/pq17', data={'files': [(io.BytesIO(pq17_pdf('XH1000000077', '777001', netto=985, deduction=15)),
                                                           'uy.pdf')], '_csrf': bux.csrf()},
                   content_type='multipart/form-data', headers={'X-Requested-With': 'fetch', 'Accept': 'application/json'}).get_json()
    assert r['ok'], r
    with app.app_context():
        d = q("SELECT * FROM pq17_docs WHERE code='XH1000000077'", one=True)
        assert d['group_id'] == gid and d['waybill_id'] is None
        from surxon.pricing import money
        m = money()
        assert all(m[w]['confirmed'] for w in (w1, w2, w3))
        total = sum(m[w]['amount'] for w in (w1, w2, w3))
        assert abs(total - round(970 * 8066.41)) <= 3
    page = bux.get('/buxgalteriya/pq17').get_data(as_text=True)
    assert 'bizda bunday reys topilmadi' not in page

    # 6. Farhod aka's combine: day by day hisob-kitob with its share of the punkt kg, and the PDF to give him
    page = bux.get(f'/buxgalteriya/kombaynlar/{k2}').get_data(as_text=True)
    assert 'Farhod aka (xizmat)' in page and 'UY-0008' in page and '394' in page
    pdf = bux.get(f'/buxgalteriya/kombaynlar/{k2}.pdf')
    from test_documents import pdf_text
    text = pdf_text(pdf.data)
    assert pdf.status_code == 200 and 'KOMBAYN HISOB-KITOBI' in text and 'Farhod aka (xizmat)' in text and '394' in text
    assert f'/buxgalteriya/kombaynlar/{k2}' in bux.get('/buxgalteriya/kombaynlar').get_data(as_text=True)


def test_umumiy_yuk_guards(app, world):
    tally, yunus, ali, st = setup(app, world)
    admin = world['admin']
    admin.post('/admin/blankalar', {'count': '5'})
    admin.post('/admin/uy-blankalar', {'count': '2'})
    assert admin.post('/admin/uy-blankalar', {'count': '5000'}).get_json()['ok'] is False
    # numbering: starts at UY-0008 (1–7 were used on paper), goes on after the last one; a taken number is refused
    busy = admin.post('/admin/uy-blankalar', {'count': '3', 'start': '9'}).get_json()
    assert busy['ok'] is False and 'UY-0009 allaqachon bor' in busy['error'] and 'UY-0010' in busy['error']
    assert admin.post('/admin/uy-blankalar', {'count': '2', 'start': '50'}).get_json()['ok']
    assert admin.post('/admin/uy-blankalar', {'count': '1'}).get_json()['ok']
    with app.app_context():
        assert [r['number'] for r in q('SELECT number FROM load_groups ORDER BY id')] == \
            ['UY-0008', 'UY-0009', 'UY-0050', 'UY-0051', 'UY-0052']
    with app.app_context():
        t1, t2 = [r['token'] for r in q('SELECT token FROM load_groups ORDER BY id')][:2]
    w1 = closed_trip(app, tally, world, 'TL-01', ['150', '150'], blank='PB-0001')
    yunus.get(f'/punkt/uy/{t1}')
    with app.app_context():
        gid = q('SELECT id FROM load_groups WHERE token=?', (t1,), one=True)['id']
    # an empty list cannot be closed
    assert yunus.post(f'/punkt/uy/{gid}/yopish').get_json()['ok'] is False
    # a trailer without a blank, picked from the list; then removed
    assert yunus.post(f'/punkt/uy/{gid}/qosh', {'waybill_id': w1}).get_json()['ok']
    assert yunus.post(f'/punkt/uy/{gid}/olib', {'waybill_id': w1}).get_json()['ok']
    # a trip already received alone cannot join
    assert yunus.post(f'/punkt/yuk/{w1}/qabul', {'station_kg': '298'}, files={'blank_photo': jpeg()}).get_json()['ok']
    r = yunus.post(f'/punkt/uy/{gid}/qosh', {'code': 'PB-0001'}).get_json()
    assert r['ok'] is False and 'qabul qilingan' in r['error']
    # the UY start page finds a paper by its number
    assert yunus.get('/punkt/uy?q=UY-0009').headers['Location'].endswith(f'/punkt/uy/{t2}')
    assert 'Umumiy yuk' in yunus.get('/punkt').get_data(as_text=True)


def test_split_is_exact():
    from surxon.groups import split
    parts = split(1000, [333, 333, 334])
    assert round(sum(parts), 1) == 1000 and parts[:2] == [333.0, 333.0]
    assert split(985, [300, 300, 400]) == [295.5, 295.5, 394.0]


def test_trailers_already_on_the_way_join_a_uy_without_blanks(app, world):
    """Trailers closed before the blanks existed (no PB): Yunus opens a UY and taps them in from the list —
    nothing is lost, each keeps its combine / hand label and gets its share."""
    tally, yunus, ali, st = setup(app, world)
    admin = world['admin']
    with app.app_context():
        k1 = q("SELECT id FROM equipment WHERE code='K-01'", one=True)['id']
    w1 = closed_trip(app, tally, world, 'TL-01', ['600', '600'], method='combine', combine=k1)
    w2 = closed_trip(app, tally, world, 'TL-02', ['150', '160'])
    admin.post('/admin/uy-blankalar', {'count': '1'})
    with app.app_context():
        g = q('SELECT * FROM load_groups', one=True)
    page = yunus.get(f'/punkt/uy/{g["token"]}').get_data(as_text=True)
    assert 'Blanksiz telashka qo‘shish (2)' in page and 'TL-01' in page and 'TL-02' in page
    assert yunus.post(f'/punkt/uy/{g["id"]}/qosh', {'waybill_id': w1}).get_json()['ok']
    assert yunus.post(f'/punkt/uy/{g["id"]}/qosh', {'waybill_id': w2}).get_json()['kg'] == 1510
    page = yunus.get(f'/punkt/uy/{g["token"]}').get_data(as_text=True)
    assert 'K-01 · SURXON' in page and 'blanksiz' in page
    assert yunus.post(f'/punkt/uy/{g["id"]}/yopish').get_json()['ok']
    assert 'Dala jami bilan' in yunus.get(f'/punkt/uy/{g["token"]}').get_data(as_text=True)     # one tap: the field total
    r = yunus.post(f'/punkt/uy/{g["id"]}/qabul', {'station_kg': '1510', 'gross_kg': '', 'tare_kg': '', 'load_no': ''},
                   files={'photo': jpeg()}).get_json()
    assert r['ok'], r
    with app.app_context():
        assert {r['waybill_id']: r['accepted_kg'] for r in q('SELECT * FROM nayman_receipts')} == {w1: 1200, w2: 310}
