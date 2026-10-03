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
