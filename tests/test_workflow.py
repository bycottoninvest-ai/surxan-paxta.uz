"""Business-rule tests: the full chain plus every acceptance case from the Codex review."""
import sqlite3
import threading

import pytest

from surxon.db import connect, get_db, q, scalar
from conftest import Client, jpeg, open_load, uuid4


def add(client, lid, name, kg, **extra):
    data = {'load_id': lid, 'method': 'hand', 'worker_name': name, 'kg': kg, 'client_uuid': uuid4(), **extra}
    return client.post('/terim', data).get_json()


def toldi(client, lid, photos=1):
    return client.post(f'/yuk/{lid}/toldi', files={'photos': [jpeg((i * 40, 90, 90)) for i in range(photos)]}).get_json()


def weigh(scale, lid, gross, tare, **extra):
    assert scale.post(f'/tarozi/{lid}', {'step': 'gross', 'gross_kg': gross}).get_json()['ok']
    return scale.post(f'/tarozi/{lid}', {'step': 'tare', 'tare_kg': tare, **extra}).get_json()


# ------------------------------------------------------------------ full chain

def test_full_chain_field_to_payment(app, world):
    juma, scale, bux, kassa, admin = world['juma'], world['tarozi'], world['bux'], world['kassa'], world['admin']
    lid = open_load(juma, world)
    r = add(juma, lid, 'Gulbahor opa', '85')
    assert r['ok'] and r['load_total'] == 85
    assert add(juma, lid, 'Mirjalol', '95,5')['ok']             # comma decimal accepted
    r = juma.post('/terim', {'load_id': lid, 'method': 'combine', 'combine_id': world['eq']['K-01'], 'kg': '1850',
                             'client_uuid': uuid4()}).get_json()
    assert r['ok'] and r['load_total'] == 2030.5
    assert toldi(juma, lid)['ok']
    with app.app_context():
        ld = q('SELECT * FROM trailer_loads WHERE id=?', (lid,), one=True)
        assert (ld['status'], ld['hand_kg'], ld['combine_kg'], ld['internal_kg']) == ('TOLDI', 180.5, 1850, 2030.5)
    # after TOLDI nothing more can be added to this trip
    assert add(juma, lid, 'Asadbek', '50')['ok'] is False
    r = weigh(scale, lid, '4650', '2600')                       # net 2050, diff +19.5 (<2%) -> no reason needed
    assert r['ok'], r
    wid = r['waybill_id']
    with app.app_context():
        wb = q('SELECT * FROM waybills WHERE id=?', (wid,), one=True)
        w = q('SELECT * FROM weighings WHERE load_id=?', (lid,), one=True)
        assert wb['number'] == 'PA-000001' and wb['net_kg'] == 2050
        assert w['gross_at'] and w['tare_at'] and w['diff_kg'] == 19.5
    r = bux.post(f'/nayman/{wid}', {'accepted_kg': '2030', 'received_date': '2026-01-01', 'diff_reason': 'Namlik / tabiiy kamayish',
                                    'price_per_kg': '7800'}).get_json()
    assert r['ok'], r
    r = bux.post('/tolovlar', {'amount': '12 000 000', 'payment_date': '2026-01-02', 'waybill_id': wid, 'client_uuid': uuid4()}).get_json()
    assert r['ok'], r
    with app.app_context():
        rec = q('SELECT * FROM nayman_receipts WHERE waybill_id=?', (wid,), one=True)
        assert rec['diff_kg'] == -20 and rec['amount'] == 2030 * 7800
        from surxon.queries import finance_summary, day_kpis
        year = q('SELECT year FROM seasons LIMIT 1', one=True)['year']
        fin = finance_summary(year)
        assert fin['debt'] == 2030 * 7800 - 12_000_000
        # the three measures stay separate: field 2030.5, weighbridge 2050, Nayman 2030
        assert scalar('SELECT SUM(kg) FROM harvests WHERE voided_at IS NULL') == 2030.5
        assert scalar('SELECT SUM(net_kg) FROM weighings') == 2050
        # audit shows who did every step
        actions = {(a['action'], a['entity_type']) for a in q('SELECT action, entity_type FROM audit_logs')}
        for need in [('OPEN', 'trailer_load'), ('CREATE', 'harvest'), ('TOLDI', 'trailer_load'), ('GROSS', 'weighing'),
                     ('TARE', 'weighing'), ('CREATE', 'waybill'), ('CREATE', 'nayman_receipt'), ('CREATE', 'payment')]:
            assert need in actions, need
        who = {a['action']: a['user_id'] for a in q("SELECT action, user_id FROM audit_logs WHERE entity_type IN ('weighing','trailer_load')")}
        assert who['TOLDI'] != who['TARE']
    # visible on dashboard, waybill page and photo archive
    assert 'PA-000001' in admin.get('/').get_data(as_text=True)
    page = admin.get(f'/nakladnoy/{wid}').get_data(as_text=True)
    assert 'PA-000001' in page and 'TOLDI' in page
    assert 'Telashka' in admin.get('/foto').get_data(as_text=True)


def test_worker_payment_and_cash_rules(app, world):
    admin, kassa = world['admin'], world['kassa']
    lid = open_load(world['juma'], world)
    add(world['juma'], lid, 'Sadoqat opa', '100')
    # no opening balance -> paying out is refused (no invented money)
    with app.app_context():
        wid = q("SELECT id FROM workers WHERE full_name='Sadoqat opa'", one=True)['id']
    r = kassa.post('/kassa', {'category': 'worker_pay', 'amount': '50000', 'entry_date': '2026-01-01', 'worker_id': wid,
                              'client_uuid': uuid4()}).get_json()
    assert r['ok'] is False and 'yetarli' in r['error']
    assert kassa.post('/kassa', {'category': 'opening', 'amount': '1000000', 'entry_date': '2026-01-01', 'client_uuid': uuid4()}).get_json()['ok']
    assert kassa.post('/kassa', {'category': 'opening', 'amount': '5', 'entry_date': '2026-01-01', 'client_uuid': uuid4()}).get_json()['ok'] is False
    assert kassa.post('/kassa', {'category': 'advance', 'amount': '30000', 'entry_date': '2026-01-01', 'worker_id': wid,
                                 'client_uuid': uuid4()}).get_json()['ok']
    assert world['bux'].post('/xarajatlar', {'category': 'Yoqilg‘i', 'amount': '200000', 'expense_date': '2026-01-01', 'from_cash': '1',
                                             'client_uuid': uuid4()}).get_json()['ok']
    admin.post('/admin/sozlamalar', {'set_worker_rate_hand': '1500'})
    html = kassa.get('/ishchilar/hisob-kitob').get_data(as_text=True)
    assert '150' in html  # 100 kg * 1500
    with app.app_context():
        from surxon.services import cash_balance
        year = q('SELECT year FROM seasons LIMIT 1', one=True)['year']
        assert cash_balance(get_db(), year) == 1_000_000 - 30_000 - 200_000


# ------------------------------------------------------------------ Codex acceptance cases

def test_same_trailer_two_trips_same_day_no_double_count(app, world):
    juma, scale = world['juma'], world['tarozi']
    lid1 = open_load(juma, world)
    add(juma, lid1, 'A One', '100')
    toldi(juma, lid1)
    # trailer busy until weighed
    r = juma.post('/telashkalar/ochish', {'trailer_id': world['eq']['TL-01'], 'field_id': world['f']['D-04']}).get_json()
    assert r['ok'] is False
    weigh(scale, lid1, '2700', '2600')
    lid2 = open_load(juma, world)
    add(juma, lid2, 'B Two', '50')
    toldi(juma, lid2)
    with app.app_context():
        assert q('SELECT internal_kg FROM trailer_loads WHERE id=?', (lid1,), one=True)[0] == 100
        assert q('SELECT internal_kg FROM trailer_loads WHERE id=?', (lid2,), one=True)[0] == 50


def test_double_click_and_offline_replay_create_one_record(app, world):
    juma = world['juma']
    lid = open_load(juma, world)
    key = uuid4()
    data = {'load_id': lid, 'method': 'hand', 'worker_name': 'Gulbahor opa', 'kg': '85', 'client_uuid': key}
    first = juma.post('/terim', data).get_json()
    second = juma.post('/terim', data).get_json()     # same form sent again (double tap / offline queue replay)
    assert first['ok'] and second['ok'] and first['harvest_id'] == second['harvest_id']
    # a *new* submission with same worker+kg within 3 min needs explicit confirmation
    r = juma.post('/terim', {**data, 'client_uuid': uuid4()}).get_json()
    assert r['ok'] is False and 'takror' in r['error']
    assert juma.post('/terim', {**data, 'client_uuid': uuid4(), 'confirm_duplicate': '1'}).get_json()['ok']
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM harvests') == 2
        assert scalar('SELECT COUNT(*) FROM workers') == 1


def test_parallel_weighings_get_unique_sequential_numbers(app, world):
    juma = world['juma']
    loads = []
    for tl in ('TL-01', 'TL-02', 'TL-03', 'TL-04'):
        lid = open_load(juma, world, trailer=tl)
        add(juma, lid, f'W {tl}', '100')
        toldi(juma, lid)
        loads.append(lid)
    from surxon.security import Actor
    from surxon.services import record_gross, record_tare
    with app.app_context():
        uid = q("SELECT id FROM users WHERE username='tarozi01'", one=True)['id']
    actor = Actor(uid, 'scale')
    for lid in loads:
        with app.test_request_context():
            record_gross(actor, lid, 2700)
    errors, numbers = [], []

    def run(lid):
        try:
            with app.test_request_context():
                numbers.append(record_tare(actor, lid, 2600)['number'])
        except Exception as e:  # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=run, args=(lid,)) for lid in loads]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert sorted(numbers) == ['PA-000001', 'PA-000002', 'PA-000003', 'PA-000004']


def test_brigadier_cannot_touch_other_brigade(app, world):
    juma, nurim = world['juma'], world['nurim']
    # Juma cannot open a trip on Nurim's field
    r = juma.post('/telashkalar/ochish', {'trailer_id': world['eq']['TL-02'], 'field_id': world['f']['D-01']}).get_json()
    assert r['ok'] is False
    lid = open_load(nurim, world, trailer='TL-02', field='D-01')
    r = add(juma, lid, 'Intruder', '10')
    assert r['ok'] is False
    assert juma.get(f'/yuk/{lid}').status_code == 403
    assert juma.post(f'/yuk/{lid}/toldi', files={'photos': jpeg()}).status_code == 403
    # brigadier can't weigh, can't enter Nayman, can't see cash
    assert juma.post(f'/tarozi/{lid}', {'step': 'gross', 'gross_kg': '1'}).status_code == 403
    assert juma.get('/kassa').status_code in (302, 403)
    assert juma.post('/admin/foydalanuvchilar', {'username': 'x', 'full_name': 'x', 'role': 'admin', 'password': 'Abcdefg12'}).status_code == 403


def test_service_layer_enforces_roles_even_without_views(app, world):
    from surxon.security import Actor
    from surxon.services import record_gross, add_payment
    from surxon.utils import UserError
    with app.test_request_context():
        with pytest.raises(UserError):
            record_gross(Actor(1, 'brigadier', brigadier_id=1, source='telegram'), 1, 100)
        with pytest.raises(UserError):
            add_payment(Actor(1, 'scale'), amount=1, payment_date='2026-01-01')


def test_bad_photo_leaves_no_partial_record(app, world):
    juma = world['juma']
    lid = open_load(juma, world)
    add(juma, lid, 'Gulbahor opa', '85')
    import io
    r = juma.c.post(f'/yuk/{lid}/toldi', data={'_csrf': juma.csrf(), 'photos': [(io.BytesIO(b'not an image'), 'x.jpg')]},
                    headers={'X-Requested-With': 'fetch'}, content_type='multipart/form-data').get_json()
    assert r['ok'] is False
    with app.app_context():
        assert q('SELECT status FROM trailer_loads WHERE id=?', (lid,), one=True)[0] == 'OCHIQ'
        assert scalar('SELECT COUNT(*) FROM photos') == 0
        assert scalar("SELECT COUNT(*) FROM audit_logs WHERE action='TOLDI'") == 0
    # TOLDI without any photo is refused (min 1 photo by default)
    assert juma.post(f'/yuk/{lid}/toldi', {}).get_json()['ok'] is False


def test_weighing_correction_updates_document_and_keeps_history(app, world):
    juma, scale, rahbar = world['juma'], world['tarozi'], world['rahbar']
    lid = open_load(juma, world)
    add(juma, lid, 'Gulbahor opa', '100')
    toldi(juma, lid)
    wid = weigh(scale, lid, '2700', '2600')['waybill_id']
    # the scale operator cannot silently re-weigh
    assert scale.post(f'/tarozi/{lid}', {'step': 'gross', 'gross_kg': '9999'}).get_json()['ok'] is False
    assert scale.post(f'/tarozi/{lid}/tuzatish', {'gross_kg': '2750', 'tare_kg': '2600', 'reason': 'x'}).status_code == 403
    r = rahbar.post(f'/tarozi/{lid}/tuzatish', {'gross_kg': '2750', 'tare_kg': '2600', 'reason': 'Tablo noto‘g‘ri o‘qilgan'}).get_json()
    assert r['ok'], r
    with app.app_context():
        assert q('SELECT net_kg FROM waybills WHERE id=?', (wid,), one=True)[0] == 150
        assert q('SELECT net_kg FROM weighings WHERE load_id=?', (lid,), one=True)[0] == 150
        a = q("SELECT * FROM audit_logs WHERE action='CORRECT'", one=True)
        assert '"net_kg": 100' in a['old_json'] and a['reason']


def test_large_difference_requires_reason(app, world):
    juma, scale = world['juma'], world['tarozi']
    lid = open_load(juma, world)
    add(juma, lid, 'Gulbahor opa', '100')
    toldi(juma, lid)
    assert scale.post(f'/tarozi/{lid}', {'step': 'gross', 'gross_kg': '3000'}).get_json()['ok']
    r = scale.post(f'/tarozi/{lid}', {'step': 'tare', 'tare_kg': '2600'}).get_json()   # net 400 vs 100 internal
    assert r['ok'] is False and 'sabab' in r['error'].lower()
    r = scale.post(f'/tarozi/{lid}', {'step': 'tare', 'tare_kg': '2600', 'diff_reason': 'Kombayn terimi tortilmagan'}).get_json()
    assert r['ok']


def test_input_validation(app, world):
    juma = world['juma']
    lid = open_load(juma, world)
    for bad in ('nan', 'inf', '-5', '0', 'abc', '999999'):
        assert add(juma, lid, 'Gulbahor opa', bad)['ok'] is False, bad
    assert world['bux'].post('/tolovlar', {'amount': '-100', 'payment_date': '2026-01-01'}).get_json()['ok'] is False
    assert world['bux'].post('/tolovlar', {'amount': '100', 'payment_date': '2099-01-01'}).get_json()['ok'] is False


def test_audit_log_is_append_only(app, world):
    with app.app_context():
        db = get_db()
        with pytest.raises(sqlite3.DatabaseError):
            db.execute('UPDATE audit_logs SET action=?', ('X',))
        with pytest.raises(sqlite3.DatabaseError):
            db.execute('DELETE FROM audit_logs')


def test_void_is_soft_and_excluded_from_totals(app, world):
    juma = world['juma']
    lid = open_load(juma, world)
    add(juma, lid, 'Gulbahor opa', '100')
    hid = add(juma, lid, 'Mirjalol', '40')['harvest_id']
    assert juma.post(f'/terim/{hid}/bekor', {'reason': 'xato kiritildi'}).get_json()['ok']
    assert juma.post(f'/terim/{hid}/bekor', {'reason': ''}).get_json()['ok'] is False
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM harvests') == 2                      # nothing deleted
        assert scalar('SELECT SUM(kg) FROM harvests WHERE voided_at IS NULL') == 100


def test_closed_season_is_frozen_and_old_reports_do_not_change(app, world):
    juma, scale, admin = world['juma'], world['tarozi'], world['admin']
    lid = open_load(juma, world)
    add(juma, lid, 'Gulbahor opa', '100')
    toldi(juma, lid)
    weigh(scale, lid, '12720', '100', diff_reason='kombayn tortilmagan')                                # 12620 kg net on 126.2 ha -> 100 kg/ha
    with app.app_context():
        year = q('SELECT year FROM seasons LIMIT 1', one=True)['year']
    before = admin.get(f'/hisobot/dalalar?season={year}').get_data(as_text=True)
    rr = admin.post('/admin/mavsumlar', {'year': year, 'action': 'close', 'reason': 'mavsum tugadi'}).get_json()
    assert rr['ok'], rr
    # next year the field is re-measured
    admin.post('/admin/dalalar', {'id': world['f']['D-04'], 'code': 'D-04', 'name': 'Dala 4', 'area_ha': '200',
                                  'brigadier_id': world['b']['Juma ota'], 'active': '1'})
    after = admin.get(f'/hisobot/dalalar?season={year}').get_data(as_text=True)
    import re
    cells = lambda html: re.findall(r'<td[^>]*>([^<]*)</td>', html)
    assert '126.2' in cells(after) and '100' in cells(after)
    assert before.count('126.2') == after.count('126.2')
    # writes into a closed season are refused
    r = world['bux'].post('/tolovlar', {'amount': '5', 'payment_date': '2026-01-01', 'waybill_id': 1}).get_json()
    assert r['ok'] is False and 'yopilgan' in r['error']


def test_photos_are_private(app, world):
    juma = world['juma']
    lid = open_load(juma, world)
    add(juma, lid, 'Gulbahor opa', '85')
    toldi(juma, lid)
    with app.app_context():
        p = q('SELECT * FROM photos LIMIT 1', one=True)
    anon = app.test_client()
    assert anon.get('/media/' + p['path']).status_code == 302
    assert anon.get('/static/uploads/' + p['path']).status_code == 404
    assert juma.get('/media/' + p['thumb_path']).status_code == 200
    assert world['nurim'].get('/media/' + p['path']).status_code == 403   # other brigade
    assert juma.get('/media/../../etc/passwd').status_code in (403, 404)


def test_service_worker_never_caches_private_pages(app):
    sw = app.test_client().get('/service-worker.js').get_data(as_text=True)
    assert "url.pathname.startsWith('/static/')" in sw
    assert 'surxon-paxta-v1' not in sw or 'caches.delete' in sw
    assert "req.method !== 'GET'" in sw


def test_login_throttle_and_password_change(app, world):
    c = Client(app)
    c.get('/login')
    for _ in range(8):
        c.c.post('/login', data={'username': 'nurim', 'password': 'wrong', '_csrf': c.csrf()})
    r = c.c.post('/login', data={'username': 'nurim', 'password': 'Worker2026x', '_csrf': c.csrf()})
    assert '/login' in r.headers['Location']          # blocked even with the right password
    j = world['juma']
    r = j.c.post('/parol', data={'_csrf': j.csrf(), 'current': 'Worker2026x', 'new': 'NewPass2026', 'new2': 'NewPass2026'})
    assert r.status_code == 302
    Client(app, 'juma', 'NewPass2026')


def test_backup_from_live_wal_database_restores(app, world, tmp_path):
    juma = world['juma']
    lid = open_load(juma, world)
    add(juma, lid, 'Gulbahor opa', '85')
    cfg = app.config['SURXON']
    # keep a writer connection open so fresh commits sit in the -wal file (plain cp would miss them)
    live = connect(cfg.DB_PATH)
    live.execute("INSERT INTO settings(key, value, updated_at) VALUES ('probe','in-wal','x')")
    from surxon.backup import run_backup
    msg = run_backup(cfg)
    assert 'Zaxira tayyor' in msg
    backup = sorted(cfg.BACKUP_DIR.glob('surxon_db_*.sqlite3'))[-1]
    restored = sqlite3.connect(backup)
    assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert restored.execute("SELECT value FROM settings WHERE key='probe'").fetchone()[0] == 'in-wal'
    assert restored.execute('SELECT SUM(kg) FROM harvests').fetchone()[0] == 85
    live.close()
    # the restored file boots as a working app
    from surxon import create_app
    app2 = create_app(TESTING=True, DATA_DIR=tmp_path / 'r', DB_PATH=backup, UPLOAD_DIR=tmp_path / 'r/u', BACKUP_DIR=tmp_path / 'r/b')
    with app2.app_context():
        assert scalar('SELECT COUNT(*) FROM trailer_loads') == 1


def test_tally_clerk_enters_all_brigades_and_combine_pay(app, world):
    """One 'Hisobchi (terim)' login works for every brigade; combine pay uses its own so'm/kg rate."""
    admin = world['admin']
    from conftest import make_user
    tally = make_user(app, admin, 'sadokat', 'tally')
    for field, trailer in (('D-04', 'TL-01'), ('D-01', 'TL-02')):     # Juma ota's and Nurim ota's fields
        lid = open_load(tally, world, trailer=trailer, field=field)
        assert add(tally, lid, 'Gulbahor opa', '40')['ok']
    lid = open_load(tally, world, trailer='TL-03', field='D-04')
    assert tally.post('/terim', {'load_id': lid, 'method': 'combine', 'combine_id': world['eq']['K-01'], 'kg': '1000',
                                 'client_uuid': uuid4()}).get_json()['ok']
    # no rate yet -> no money column (never shown as 0)
    html = admin.get('/hisobot/kombaynlar').get_data(as_text=True)
    assert 'K-01' in html and 'Kombayn haqi' not in html
    r = admin.post('/admin/sozlamalar', {'set_combine_rate': '1500', 'set_worker_rate_hand': '1500'})
    assert r.get_json()['ok'], r.get_data(as_text=True)
    html = admin.get('/hisobot/kombaynlar').get_data(as_text=True)
    flat = ''.join(html.split()).replace('\u202f', '').replace('\xa0', '')
    assert 'Kombayn haqi (1500 so‘m/kg)' in html and '1500000' in flat
    html = admin.get('/hisobot/terimchilar').get_data(as_text=True)
    assert 'Ish haqi (1500 so‘m/kg)' in html
