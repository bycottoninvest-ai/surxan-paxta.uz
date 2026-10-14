"""DALA NAKLADNOYI v3: nobody uses the website in the field. The paper is filled by hand (a trailer filled in up to 3
places — a tick at the brigadier, the field, kg), Yunus photographs it inside the UY list, the system reads it (the
reader is replaced here — no real request), he checks it, and every row becomes a closed trip in that UY."""
import json

from conftest import jpeg
from surxon.db import q
from test_punkt import setup


def prepare(app, world, monkeypatch, raw):
    from surxon import talon_ai
    tally, yunus, ali, st = setup(app, world)
    admin = world['admin']
    admin.post('/admin/blankalar', {'count': '5'})
    admin.post('/admin/uy-blankalar', {'count': '2'})
    admin.post('/admin/sozlamalar', {'set_worker_rate_hand': '1500'})
    app.config['SURXON'].ANTHROPIC_API_KEY = 'test-key-not-real'
    seen = []
    monkeypatch.setattr(talon_ai, 'read_blank', lambda data, names: (seen.append(names), raw)[1])
    with app.app_context():
        g = q("SELECT * FROM load_groups ORDER BY id LIMIT 1", one=True)
    page = yunus.get(f'/punkt/uy/{g["token"]}').get_data(as_text=True)
    assert 'DALA NAKLADNOYINI RASMGA OLISH' in page
    return yunus, ali, g['id'], seen


def read(c, gid, code='', color=(200, 200, 200)):
    return c.post(f'/punkt/uy/{gid}/pb-oqish', {'code': code}, files={'photo': jpeg(color)}).get_json()


def save(c, gid, r, **over):
    d = {'code': r['token'], 'photo_id': r['photo_id'], 'trailer_id': r['read']['trailer_id'], 'method': r['read']['method'],
         'combine_id': r['read']['combine_id'], 'total_kg': r['read']['total_kg'],
         'rows': [{'brigadier_id': x['brigadier_id'], 'field_id': x['field_id'], 'kg': x['kg']} for x in r['read']['rows']]}
    d.update(over)
    return c.post(f'/punkt/uy/{gid}/pb-saqlash', {'data': json.dumps(d)}).get_json()


RAW = {'blank_no': 'PB-0001', 'trailer': '02', 'method': 'hand', 'combine_no': None, 'date': '14.10', 'note': '',
       'rows': [{'row': 1, 'brigadier': 'Nurim ota', 'field': 'D-01', 'kg': 520, 'unclear': False},
                {'row': 2, 'brigadier': 'Juma ota', 'field': '4', 'kg': 610, 'unclear': False}],
       'total_kg': 1130}


def test_field_paper_becomes_trips_in_the_uy_and_is_received(app, world, monkeypatch):
    yunus, ali, gid, seen = prepare(app, world, monkeypatch, RAW)
    try:
        r = read(yunus, gid)
        assert r['ok'] and r['blank'] == 'PB-0001' and not r['already']
        assert 'Nurim ota' in seen[0] and 'Juma ota' in seen[0]          # the printed names go to the reader
        rd = r['read']
        assert rd['trailer_id'] == world['eq']['TL-02'] and rd['method'] == 'hand' and rd['sum_ok']
        assert [(x['brigadier_id'], x['field_id'], x['kg'], x['flag']) for x in rd['rows']] == [
            (world['b']['Nurim ota'], world['f']['D-01'], 520, ''), (world['b']['Juma ota'], world['f']['D-04'], 610, '')]
        # the other punkt cannot touch this UY
        assert not read(ali, gid).get('ok')
        s = save(yunus, gid, r)
        assert s['ok'], s
        assert save(yunus, gid, r)['ok']                                   # sent twice: written once
        with app.app_context():
            trips = q('''SELECT tl.*, wb.id wid, wb.net_kg FROM trailer_loads tl JOIN waybills wb ON wb.load_id=tl.id
                         WHERE tl.pb_blank_id IS NOT NULL ORDER BY tl.pb_row''')
            assert [(t['brigadier_id'], t['field_id'], t['net_kg'], t['manual_kg'], t['status']) for t in trips] == [
                (world['b']['Nurim ota'], world['f']['D-01'], 520, 520, 'TORTILDI'),
                (world['b']['Juma ota'], world['f']['D-04'], 610, 610, 'TORTILDI')]
            assert {t['trailer_id'] for t in trips} == {world['eq']['TL-02']}
            blank = q("SELECT * FROM punkt_blanks WHERE number='PB-0001'", one=True)
            assert blank['waybill_id'] == trips[0]['wid'] and blank['photo_id']
            items = q('SELECT waybill_id FROM load_group_items WHERE group_id=?', (gid,))
            assert {i['waybill_id'] for i in items} == {t['wid'] for t in trips}
            assert q("SELECT COUNT(*) FROM audit_logs WHERE action='PB_INTAKE'", one=True)[0] == 1
        # the same paper photographed again just joins (nothing new)
        again = read(yunus, gid)
        assert again['ok'] and again['already']
        # both brigades have today's trips for the evening count
        from surxon.tezpul import evening_brigades
        with app.app_context():
            assert {b['id'] for b in evening_brigades()} >= {world['b']['Nurim ota'], world['b']['Juma ota']}
        # the UY is received as before: netto shared by the paper's kg
        assert yunus.post(f'/punkt/uy/{gid}/yopish').get_json()['ok']
        r = yunus.post(f'/punkt/uy/{gid}/qabul', {'gross_kg': '11230', 'tare_kg': '10100', 'load_no': '463951'},
                       files={'photo': jpeg()}).get_json()
        assert r['ok'], r
        with app.app_context():
            acc = {x['waybill_id']: x['accepted_kg'] for x in q('SELECT waybill_id, accepted_kg FROM nayman_receipts')}
            assert round(sum(acc.values())) == 1130
    finally:
        app.config['SURXON'].ANTHROPIC_API_KEY = ''


def test_field_paper_flags_and_checks(app, world, monkeypatch):
    raw = dict(RAW, rows=[{'row': 1, 'brigadier': 'Juma ota', 'field': 'D-01', 'kg': 500, 'unclear': False},
                          {'row': 2, 'brigadier': None, 'field': None, 'kg': 300, 'unclear': True}], total_kg=900)
    yunus, ali, gid, _ = prepare(app, world, monkeypatch, raw)
    try:
        r = read(yunus, gid)
        rd = r['read']
        assert not rd['sum_ok'] and rd['sum_kg'] == 800
        assert 'Nurim ota dalasi' in rd['rows'][0]['flag'] and rd['rows'][1]['flag']
        bad = save(yunus, gid, r)
        assert not bad['ok']                                                # the second row has no brigadier / field
        bad = save(yunus, gid, r, rows=[{'brigadier_id': world['b']['Juma ota'], 'field_id': world['f']['D-01'], 'kg': 500}])
        assert not bad['ok'] and 'boshqa brigadir' in bad['error']          # field ↔ brigadier still checked
        ok = save(yunus, gid, r, rows=[{'brigadier_id': world['b']['Nurim ota'], 'field_id': world['f']['D-01'], 'kg': 500}])
        assert ok['ok'], ok
    finally:
        app.config['SURXON'].ANTHROPIC_API_KEY = ''


def test_blank_v3_print_has_the_brigadiers(app, world):
    from surxon.pdfdoc import build_blanks_pdf
    with app.test_request_context():
        pdf = build_blanks_pdf([{'number': 'PB-0001', 'token': 't1'}], company='SURXON', domain='x', brigadiers=['A ota', 'B ota'])
    assert pdf[:4] == b'%PDF'
    world['admin'].post('/admin/blankalar', {'count': '2'})
    r = world['admin'].get('/admin/blankalar/1.pdf')
    assert r.status_code == 200 and r.data[:4] == b'%PDF'
