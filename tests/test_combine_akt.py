"""Kombayn akt-sverka, end to end as it happens: two combines in one trailer, a hand trailer and a combine trailer
weighed together in one umumiy yuk, the cluster's PQ-17 for the whole load, a payment, a trailer still on the way and
a combine that did not work. Every kg and every so‘m must add up across the owners."""
import io

from conftest import jpeg, uuid4
from surxon.db import get_db, q
from test_pq17 import pq17_pdf
from test_punkt import setup


def _close(app, tally, world, trailer, weighings, method='combine'):
    from test_dala import open_trip, weigh
    lid = open_trip(tally, world, trailer=trailer, method=method, rate='1500' if method == 'hand' else '1000')
    for i, (kg, cid) in enumerate(weighings):
        r = weigh(tally, lid, kg, name=f'{trailer} t{i}', new=method == 'hand', combine_id=cid, confirm=True)
        assert r['ok'], r
    assert tally.post(f'/dala/reys/{lid}/yopish', files={'photos': jpeg()}).get_json()['ok']
    with app.app_context():
        return q('SELECT id FROM waybills WHERE load_id=?', (lid,), one=True)['id']


def test_combine_akt_sverka_adds_up(app, world):
    tally, yunus, ali, st = setup(app, world)
    admin, bux, kassa = world['admin'], world['bux'], world['kassa']
    assert admin.post('/admin/sozlamalar', {'set_combine_rate_standard': '1000'}).get_json()['ok']   # as on the server
    with app.app_context():
        db = get_db()
        db.execute("UPDATE equipment SET ownership='external', operator_name='Farhod aka' WHERE code='K-02'")
        db.execute("""INSERT INTO equipment(kind, code, ownership, operator_name, active, created_at)
                      VALUES ('kombayn','K-03','external','Rustam',1,'2026-09-01 00:00:00')""")
        db.commit()
        k = {r['code']: r['id'] for r in q("SELECT id, code FROM equipment WHERE kind='kombayn'")}
    wa = _close(app, tally, world, 'TL-01', [('900', k['K-01']), ('300', k['K-02'])])      # two combines, one trailer
    wb = _close(app, tally, world, 'TL-02', [('140', None), ('160', None)], method='hand')   # hand picking 300
    wc = _close(app, tally, world, 'TL-03', [('600', k['K-02'])])
    _close(app, tally, world, 'TL-04', [('500', k['K-01'])])                                # still on the way

    # the three come in together: one UY, 2 100 field kg → 1 995 at the punkt (95 %)
    admin.post('/admin/uy-blankalar', {'count': '1'})
    with app.app_context():
        g = q('SELECT * FROM load_groups', one=True)
    yunus.get(f'/punkt/uy/{g["token"]}')
    for w in (wa, wb, wc):
        assert yunus.post(f'/punkt/uy/{g["id"]}/qosh', {'waybill_id': w}).get_json()['ok']
    assert yunus.post(f'/punkt/uy/{g["id"]}/yopish').get_json()['ok']
    r = yunus.post(f'/punkt/uy/{g["id"]}/qabul', {'station_kg': '1995', 'load_no': '777001', 'reason': 'Tarozilar farqi'},
                   files={'photo': jpeg()}).get_json()
    assert r['ok'], r

    from surxon.accounting import combine_balances, combine_statement
    with app.app_context():
        year = q('SELECT MAX(year) y FROM seasons', one=True)['y']

    def bal():
        with app.app_context():
            return {x['code']: x for x in combine_balances(year)}
    c = bal()
    # punkt kg shared by field kg: TL-01 1 140 (K-01 855, K-02 285), TL-03 570 → K-02 855
    assert (c['K-01']['punkt_kg'], c['K-02']['punkt_kg']) == (855, 855)
    assert (c['K-01']['earned'], c['K-02']['earned']) == (855_000, 855_000)          # standard 1 000 so‘m/kg, on punkt kg
    assert c['K-01']['waiting_kg'] == 500 and c['K-03']['kg'] == 0 and c['K-03']['earned'] == 0

    # the cluster's PQ-17 for the whole load: 1 995 netto, 105 kg dirt and moisture off → 1 890 sof
    rr = bux.c.post('/buxgalteriya/pq17', data={'files': [(io.BytesIO(pq17_pdf('XH1000000077', '777001', netto=1995, deduction=105,
                                                                                )), 'uy.pdf')],
                                                 '_csrf': bux.csrf()}, content_type='multipart/form-data',
                    headers={'X-Requested-With': 'fetch', 'Accept': 'application/json'}).get_json()
    assert rr['ok'], rr
    c = bal()
    assert (c['K-01']['sof_kg'], c['K-02']['sof_kg']) == (810, 810)
    assert (c['K-01']['earned'], c['K-02']['earned']) == (810_000, 810_000)
    assert c['K-01']['provisional_amount'] == 0 and c['K-02']['provisional_amount'] == 0
    with app.app_context():                                     # the hand trailer's part closes the 1 890 exactly
        from surxon.pq17 import by_waybill
        assert round(c['K-01']['sof_kg'] + c['K-02']['sof_kg'] + by_waybill()[wb]['kond_kg'], 1) == 1890

    # Farhod aka is paid 500 000 → 310 000 left; the akt-sverka says the same
    assert bux.post('/buxgalteriya/kirim', {'amount': '2 000 000', 'source': 'Direktor', 'client_uuid': uuid4()}).get_json()['ok']
    r = bux.post('/buxgalteriya/tolov', {'kind': 'combine', 'target_id': k['K-02'], 'amount': '500 000', 'client_uuid': uuid4()}).get_json()
    assert r['ok'] and kassa.post(f'/buxgalteriya/tolov/{r["payout_id"]}/berildi', {}).get_json()['ok']
    c = bal()
    assert (c['K-02']['paid'], c['K-02']['balance']) == (500_000, 310_000)
    with app.app_context():
        st = combine_statement(year, k['K-02'])
    assert sum(t['pay_kg'] for t in st['trips']) == 810 and {t['uy'] for t in st['trips']} == {g['number']}
    assert sum(d['amount'] for d in st['days']) == 810_000 and sum(p['amount'] for p in st['payments']) == 500_000

    # every combine's page and PDF open, the one that did not work too
    from test_documents import pdf_text
    for code in ('K-01', 'K-02', 'K-03'):
        assert bux.get(f'/buxgalteriya/kombaynlar/{k[code]}').status_code == 200
        pdf = bux.get(f'/buxgalteriya/kombaynlar/{k[code]}.pdf')
        assert pdf.status_code == 200 and pdf.data[:4] == b'%PDF', code
    text = pdf_text(bux.get(f'/buxgalteriya/kombaynlar/{k["K-02"]}.pdf').data).replace(' ', ' ')
    assert 'Farhod aka' in text and '810 000' in text and '310 000' in text and '500 000' in text


def test_nazorat_finds_mistakes_and_tells_people(app, world):
    """The self-check: a PQ-17 tied to no trip, a combine paid more than it earned, a trip left open — found, written
    to the report group and to the admin's own chat once, listed on the Nazorat page, and closed when fixed."""
    tally, yunus, ali, st = setup(app, world)
    admin, bux, kassa = world['admin'], world['bux'], world['kassa']
    assert admin.post('/admin/sozlamalar', {'set_combine_rate_standard': '1000'}).get_json()['ok']
    with app.app_context():
        db = get_db()
        db.execute("UPDATE users SET telegram_id='555' WHERE username='admin'")
        db.commit()
        k1 = q("SELECT id FROM equipment WHERE code='K-01'", one=True)['id']
    w = _close(app, tally, world, 'TL-01', [('600', k1)])
    assert yunus.post(f'/punkt/yuk/{w}/qabul', {'station_kg': '600'}).get_json()['ok']
    # a stranger PQ-17 (load 999999, 480 kg) — no trip of ours
    bux.c.post('/buxgalteriya/pq17', data={'files': [(io.BytesIO(pq17_pdf('XH1000000099', '999999')), 'x.pdf')], '_csrf': bux.csrf()},
               content_type='multipart/form-data', headers={'X-Requested-With': 'fetch'})
    # K-01 earned 600 000 (punkt kg, estimate) but is paid 700 000
    assert bux.post('/buxgalteriya/kirim', {'amount': '1 000 000', 'source': 'Direktor', 'client_uuid': uuid4()}).get_json()['ok']
    r = bux.post('/buxgalteriya/tolov', {'kind': 'combine', 'target_id': k1, 'amount': '700 000', 'client_uuid': uuid4()}).get_json()
    if r['ok']:
        assert kassa.post(f'/buxgalteriya/tolov/{r["payout_id"]}/berildi', {}).get_json()['ok']
    else:                                   # the payout form itself refuses more than the balance — also fine
        with app.app_context():
            from surxon.utils import now_str, today_str
            get_db().execute('''INSERT INTO cash_entries(season_year, entry_date, direction, category, amount, combine_id, created_at)
                                VALUES ((SELECT MAX(year) FROM seasons), ?, 'OUT', 'combine_pay', 700000, ?, ?)''',
                             (today_str(), k1, now_str()))
            get_db().commit()
    from test_dala import open_trip
    lid = open_trip(tally, world, trailer='TL-02')
    with app.app_context():
        get_db().execute("UPDATE trailer_loads SET opened_at='2026-01-01 08:00:00' WHERE id=?", (lid,))
        get_db().commit()
        from surxon.nazorat import tick
        res = tick(force=True)
        msgs = [m['payload_json'] for m in q("SELECT payload_json FROM outbox WHERE ref LIKE 'nazorat:%'")]
        again = tick(force=True)                        # nothing new → nothing sent twice
        assert len(q("SELECT 1 FROM outbox WHERE ref LIKE 'nazorat:%'")) == len(msgs)
    assert res['new'] >= 3 and again['new'] == 0
    assert any('"555"' in m for m in msgs) and any('XH1000000099' in m for m in msgs) and any('ko\\u2018p to\\u2018langan' in m or 'ko‘p to‘langan' in m for m in msgs)
    page = bux.get('/nazorat').get_data(as_text=True)
    assert 'PQ-17 XH1000000099 hech bir reysga bog‘lanmagan' in page and 'K-01: hisoblangandan ko‘p to‘langan' in page
    assert 'reys 24 soatdan ko‘p ochiq turibdi' in page and 'Nazorat (' in page
    assert world['juma'].get('/nazorat').status_code in (302, 403)          # brigadier: no
    # fixed: the open trip is cancelled → closed on the next check
    with app.app_context():
        get_db().execute("UPDATE trailer_loads SET status='BEKOR' WHERE id=?", (lid,))
        get_db().commit()
        tick(force=True)
        assert q("SELECT resolved_at FROM nazorat_alerts WHERE key=?", (f'open:{lid}',), one=True)['resolved_at']


def test_combine_never_overpaid_and_period_akt(app, world):
    """Real money: an estimated (no PQ-17 yet) sum is paid only at the safe share, the cashier checks again when the
    PQ-17 comes in lower, and the akt can be printed for one day or a date range."""
    tally, yunus, ali, st = setup(app, world)
    admin, bux, kassa = world['admin'], world['bux'], world['kassa']
    assert admin.post('/admin/sozlamalar', {'set_combine_rate_standard': '1000'}).get_json()['ok']
    with app.app_context():
        k1 = q("SELECT id FROM equipment WHERE code='K-01'", one=True)['id']
    w = _close(app, tally, world, 'TL-01', [('480', k1)])
    assert yunus.post(f'/punkt/yuk/{w}/qabul', {'station_kg': '480', 'load_no': '555001'}).get_json()['ok']
    assert bux.post('/buxgalteriya/kirim', {'amount': '2 000 000', 'source': 'Direktor', 'client_uuid': uuid4()}).get_json()['ok']
    # 480 000 earned on punkt kg, no PQ-17 → only 90 % = 432 000 may be paid now
    r = bux.post('/buxgalteriya/tolov', {'kind': 'combine', 'target_id': k1, 'amount': '480 000', 'client_uuid': uuid4()}).get_json()
    assert not r['ok'] and '432 000' in r['error'].replace('\xa0', ' ').replace('\u202f', ' ')
    r = bux.post('/buxgalteriya/tolov', {'kind': 'combine', 'target_id': k1, 'amount': '432 000', 'client_uuid': uuid4()}).get_json()
    assert r['ok'], r
    page = bux.get(f'/buxgalteriya/kombaynlar/{k1}').get_data(as_text=True).replace('\xa0', ' ').replace('\u202f', ' ')
    assert 'ushlab turiladi' in page
    # the PQ-17 comes in much lower (380 kg sof) before the cashier hands the money out → refused
    bux.c.post('/buxgalteriya/pq17', data={'files': [(io.BytesIO(pq17_pdf('XH1000000005', '555001', deduction=100)), 'x.pdf')],
                                           '_csrf': bux.csrf()}, content_type='multipart/form-data', headers={'X-Requested-With': 'fetch'})
    r2 = kassa.post(f'/buxgalteriya/tolov/{r["payout_id"]}/berildi', {}).get_json()
    assert not r2['ok'] and '380 000' in r2['error'].replace('\xa0', ' ').replace('\u202f', ' ')
    with app.app_context():
        from surxon.accounting import combine_balances
        c = combine_balances(q('SELECT MAX(year) y FROM seasons', one=True)['y'], k1)[0]
        assert (c['earned'], c['paid'], c['held']) == (380000, 0, 0)
        assert q("SELECT COALESCE(SUM(amount),0) s FROM cash_entries WHERE category='combine_pay'", one=True)['s'] == 0
        day = q('SELECT work_date FROM harvests LIMIT 1', one=True)['work_date']
    # the akt for that one day, and for a range that holds nothing
    page = bux.get(f'/buxgalteriya/kombaynlar/{k1}?dan={day}').get_data(as_text=True).replace('\xa0', ' ').replace('\u202f', ' ')
    assert 'Akt davri' in page and 'davr oxiriga qoldiq <b>380 000' in page
    pdf = bux.get(f'/buxgalteriya/kombaynlar/{k1}.pdf?dan={day}&gacha={day}')
    assert pdf.status_code == 200 and pdf.data[:4] == b'%PDF'
    page = bux.get(f'/buxgalteriya/kombaynlar/{k1}?dan=2026-01-01&gacha=2026-01-02').get_data(as_text=True).replace('\xa0', ' ').replace('\u202f', ' ')
    assert 'davrda hisoblangan <b>0' in page
