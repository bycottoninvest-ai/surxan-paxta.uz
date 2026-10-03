"""hosil-qabuli.uz Excel beside our punkt receipts, and the yuk xati № that is now required at the punkt."""
import io

from surxon.db import get_db, q
from test_pq17 import pq17_pdf, received_trip
from test_punkt import setup


def hq_xlsx(rows):
    """The site's export: a two-row header with merged group titles, then one row per load."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(['NO', 'OPERATOR', 'YUK XATI', None, 'FIZIK VAZNI, KG', None, None, 'KONDITSION VAZNI, KG', 'LABOR-YA XULOSASI', None,
               'MASKAN NOMI', 'TERIM TURI', 'PQ-17 RAQAMI', 'QAYT ETILGAN VAQT', None])
    ws.append([None, None, 'SANASI', 'RAQAMI', 'BRUTTO', 'TARA', 'NETTO', None, 'IFLOSLIK', 'NAMLIK', None, None, None, 'KIRISH', 'CHIQISH'])
    for i, (no, dt, netto, term) in enumerate(rows, 1):
        ws.append([i, 'AGRO_TAROZI', dt, no, netto + 8000 if netto else 9000, 8000 if netto else None, netto,
                   round(netto * 0.95) if netto else None, 6.5, 10.1, 'Nayman-2026', term, '-', dt, dt])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_site_layout():
    from surxon.hosil import parse
    rows = parse(hq_xlsx([('311058', '01.10.2026 15:04:38', 3080, 'Mashina bilan'), ('353283', '03.10.2026 12:52:57', 1270, 'Qo‘lda'),
                          ('363442', '03.10.2026 18:26:54', None, 'Qo‘lda')]))
    assert [r['load_no'] for r in rows] == ['311058', '353283', '363442']
    r = rows[0]
    assert (r['dt'], r['netto'], r['tara'], r['brutto'], r['method'], r['dirt'], r['moist']) == \
        ('2026-10-01 15:04', 3080, 8000, 11080, 'combine', 6.5, 10.1)
    assert rows[1]['method'] == 'hand' and rows[2]['netto'] is None


def test_parse_csv_keeps_first_rows():
    """The daily check sends a plain CSV (numbers as text) — the first data rows must not be taken for a header."""
    from surxon.hosil import parse
    csv = ('YUK XATI SANASI,YUK XATI RAQAMI,BRUTTO,TARA,NETTO,KONDITSION VAZNI,IFLOSLIK,NAMLIK,TERIM TURI,PQ-17 RAQAMI\n'
           '03.10.2026 19:59,367843,8370,6730,1640,1543,6.5,10.5,Mashina bilan,PQ-2936\n'
           '03.10.2026 18:26,363442,6670,4870,1800,1764,2.8,10.3,Qolda,PQ-2939\n'
           '03.10.2026 17:53,362185,8370,6790,1580,1487,6.5,10.5,Mashina bilan,\n').encode()
    rows = parse(csv, 'hq.csv')
    assert [r['load_no'] for r in rows] == ['367843', '363442', '362185']
    assert rows[0]['netto'] == 1640 and rows[1]['method'] == 'hand' and rows[2]['pq_no'] is None


def test_hosil_table_compares_and_writes_the_number(app, world):
    tally, yunus, ali, st = setup(app, world)
    bux = world['bux']
    w1 = received_trip(app, world, tally, yunus, kg='480', load_no='555001')            # number typed, same kg
    w2 = received_trip(app, world, tally, yunus, kg='470', trailer='TL-02')             # no number, kg = table netto
    w3 = received_trip(app, world, tally, yunus, kg='400', trailer='TL-03', load_no='555003')   # number, kg wrong
    with app.app_context():
        day = q('SELECT received_date d FROM nayman_receipts WHERE waybill_id=?', (w2,), one=True)['d']
    dd = f'{day[8:10]}.{day[5:7]}.{day[:4]} 12:00:00'
    data = hq_xlsx([('555001', dd, 480, 'Qo‘lda'), ('555002', dd, 470, 'Qo‘lda'), ('555003', dd, 450, 'Qo‘lda'),
                    ('555004', dd, 999, 'Mashina bilan')])
    r = bux.c.post('/buxgalteriya/hosil-qabuli', data={'file': (io.BytesIO(data), 'hq.xlsx'), '_csrf': bux.csrf()},
                   content_type='multipart/form-data', headers={'X-Requested-With': 'fetch', 'Accept': 'application/json'}).get_json()
    assert r['ok'] and '4 ta yangi' in r['message']
    with app.app_context():
        from surxon.hosil import compare
        st_ = {x['load_no']: x['state'] for x in compare()['rows']}
    assert st_ == {'555001': 'mos', '555002': 'raqamsiz', '555003': 'farq', '555004': 'yoq'}
    page = bux.get('/buxgalteriya/hosil-qabuli').get_data(as_text=True)
    assert 'Raqamni yozish' in page and 'bizda bu yuk topilmadi' in page and 'bizda 400 kg, klasterda 450 kg' in page
    # a PQ-17 for 555002 waits without a trip; writing the number ties it
    bux.c.post('/buxgalteriya/pq17', data={'files': [(io.BytesIO(pq17_pdf('XH1000000002', '555002', netto=470, date_text='')), 'b.pdf')],
                                           '_csrf': bux.csrf()}, content_type='multipart/form-data', headers={'X-Requested-With': 'fetch'})
    with app.app_context():
        get_db().execute("UPDATE pq17_docs SET waybill_id=NULL, match_how=NULL WHERE code='XH1000000002'")
    assert bux.post('/buxgalteriya/hosil-qabuli', {'action': 'assign', 'load_no': '555002', 'waybill_id': w2}).get_json()['ok']
    with app.app_context():
        d = q("SELECT * FROM pq17_docs WHERE code='XH1000000002'", one=True)
        assert d['waybill_id'] == w2 and d['doc_date'] == day
    # the same number never on two trips
    r = bux.post('/buxgalteriya/hosil-qabuli', {'action': 'assign', 'load_no': '555002', 'waybill_id': w1}).get_json()
    assert not r['ok'] and 'allaqachon' in r['error']


def test_problems_go_to_who_entered_them(app, world):
    tally, yunus, ali, st = setup(app, world)
    bux = world['bux']
    received_trip(app, world, tally, yunus, kg='400', load_no='555003')           # kg wrong
    received_trip(app, world, tally, yunus, kg='470', trailer='TL-02')            # nothing in the table explains it
    with app.app_context():
        day = q("SELECT MAX(received_date) d FROM nayman_receipts", one=True)['d']
        get_db().execute("UPDATE users SET telegram_id='7001' WHERE username='yunus'")
    dd = f'{day[8:10]}.{day[5:7]}.{day[:4]} 12:00:00'
    bux.c.post('/buxgalteriya/hosil-qabuli', data={'file': (io.BytesIO(hq_xlsx([('555003', dd, 450, 'Qo‘lda')])), 'hq.xlsx'),
                                                   '_csrf': bux.csrf()}, content_type='multipart/form-data')
    r = bux.post('/buxgalteriya/hosil-qabuli', {'action': 'notify'}).get_json()
    assert r['ok'] and '1 ta xodimga' in r['message']
    with app.app_context():
        jobs = q("SELECT payload_json FROM outbox WHERE kind='sverka'")
    texts = ' '.join(j['payload_json'] for j in jobs)
    assert '7001' in texts and '555003' in texts and 'klaster jadvalida' in texts


def test_correct_by_the_cluster_weighing(app, world):
    """311058-like: one trip with the field kg left → the table's netto; 289603-like: two trips received apart → one
    umumiy yuk with the table's netto shared by field kg; the PQ-17 then ties itself and the money follows it."""
    tally, yunus, ali, st = setup(app, world)
    bux = world['bux']
    w1 = received_trip(app, world, tally, yunus, kg='480')
    w2 = received_trip(app, world, tally, yunus, kg='480', trailer='TL-02')
    w3 = received_trip(app, world, tally, yunus, kg='480', trailer='TL-03')
    with app.app_context():
        day = q("SELECT MAX(received_date) d FROM nayman_receipts", one=True)['d']
    dd = f'{day[8:10]}.{day[5:7]}.{day[:4]} 12:00:00'
    bux.c.post('/buxgalteriya/hosil-qabuli', data={'file': (io.BytesIO(hq_xlsx([('311058', dd, 560, 'Qo‘lda'),
                                                                                 ('289603', dd, 1000, 'Qo‘lda')])), 'hq.xlsx'),
                                                   '_csrf': bux.csrf()}, content_type='multipart/form-data')
    assert 'Klaster kg i bilan tuzatish' in bux.get('/buxgalteriya/hosil-qabuli').get_data(as_text=True)
    r = bux.post('/buxgalteriya/hosil-qabuli', {'action': 'correct', 'load_no': '311058', 'waybill_ids': [w1]}).get_json()
    assert r['ok'], r
    r = bux.post('/buxgalteriya/hosil-qabuli', {'action': 'correct', 'load_no': '289603', 'waybill_ids': [w2, w3]}).get_json()
    assert r['ok'], r
    # the same number never twice, a trip already corrected is not taken again
    assert not bux.post('/buxgalteriya/hosil-qabuli', {'action': 'correct', 'load_no': '311058', 'waybill_ids': [w2]}).get_json()['ok']
    with app.app_context():
        r1 = q('SELECT * FROM nayman_receipts WHERE waybill_id=?', (w1,), one=True)
        assert (r1['accepted_kg'], r1['load_no']) == (560, '311058') and 'hosil-qabuli.uz' in r1['diff_reason']
        g = q("SELECT * FROM load_groups WHERE number='YX-289603'", one=True)
        assert g['accepted_kg'] == 1000 and g['status'] == 'QABUL'
        from surxon.groups import next_number
        assert next_number() < 1000                       # “YX-289603” never moves the UY paper numbers
        kgs = [x['accepted_kg'] for x in q('SELECT accepted_kg FROM nayman_receipts WHERE waybill_id IN (?,?)', (w2, w3))]
        assert sum(kgs) == 1000
        assert q("SELECT COUNT(*) n FROM audit_logs WHERE action='CORRECT'", one=True)['n'] == 2
        from surxon.hosil import compare
        st_ = {x['load_no']: x['state'] for x in compare()['rows']}
    assert st_ == {'311058': 'mos', '289603': 'mos'}
    # a PQ-17 for the group arrives → it ties to the whole group by the number
    bux.c.post('/buxgalteriya/pq17', data={'files': [(io.BytesIO(pq17_pdf('XH2000000001', '289603', netto=1000, deduction=20)), 'a.pdf')],
                                           '_csrf': bux.csrf()}, content_type='multipart/form-data', headers={'X-Requested-With': 'fetch'})
    with app.app_context():
        assert q("SELECT group_id FROM pq17_docs WHERE code='XH2000000001'", one=True)['group_id'] == g['id']
    assert world['juma'].post('/buxgalteriya/hosil-qabuli', {'action': 'correct', 'load_no': '311058',
                                                             'waybill_ids': [w1]}).status_code in (302, 403)


def test_punkt_needs_the_load_number(app, world):
    tally, yunus, ali, st = setup(app, world)
    with app.app_context():
        get_db().execute("UPDATE settings SET value='1' WHERE key='punkt_require_load_no'")
    import pytest
    with pytest.raises(AssertionError):
        received_trip(app, world, tally, yunus, kg='480')                              # no number → refused
    received_trip(app, world, tally, yunus, kg='480', trailer='TL-02', load_no='777001')
    with pytest.raises(AssertionError):
        received_trip(app, world, tally, yunus, kg='480', trailer='TL-03', load_no='777001')   # same number twice
