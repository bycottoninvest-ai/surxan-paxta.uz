"""Server operation commands: restore-from-backup check and the honest status table."""
from conftest import open_load, uuid4


def test_backup_verify_restores_into_separate_db(app, world):
    juma = world['juma']
    lid = open_load(juma, world)
    juma.post('/terim', {'load_id': lid, 'method': 'hand', 'worker_name': 'Akmal', 'kg': '85', 'client_uuid': uuid4()})
    world['bux'].post('/buxgalteriya/kirim', {'amount': '1 000 000', 'source': 'Direktor', 'client_uuid': uuid4()})
    res = app.test_cli_runner().invoke(args=['backup-verify'])
    out = res.output
    assert res.exit_code == 0, out
    assert 'alohida bazaga tiklandi' in out and 'NATIJA: ISHLAYDI' in out
    assert 'Terim kg (bekor qilinmagan)' in out and 'FARQ' not in out
    assert 'Kassa qoldig‘i (so‘m)' in out and '1000000' in out
    # the status table reports it from the recorded check, and never calls an unconnected channel "working"
    st = app.test_cli_runner().invoke(args=['holat']).output
    lines = {l[:42].strip(): l[42:].split()[0] for l in st.splitlines() if l.strip()}
    assert lines['Zaxiradan tiklash (server nusxasi)'] == 'ISHLAYDI'
    assert lines['Zaxiradan tiklash (tashqi nusxa)'] == 'TEKSHIRILMAGAN'
    assert lines['Serverdagi kunlik zaxira'] == 'ISHLAYDI'
    assert lines['Google Sheets nazorat nusxasi'] == 'ULANMAGAN'
    assert lines['Mustaqil (serverdan tashqari) zaxira'] == 'ULANMAGAN'
    assert lines['Azizbek ERP API'] == 'ULANMAGAN'


def test_offsite_verify_without_remote_is_reported_not_faked(app, world):
    res = app.test_cli_runner().invoke(args=['backup-verify', '--offsite'])
    assert 'TASHQI zaxiradan tiklash: XATO' in res.output and 'ulanmagan' in res.output


def test_narx_command_sets_current_rates_only(app, world):
    juma = world['juma']
    lid = open_load(juma, world)
    juma.post('/terim', {'load_id': lid, 'method': 'hand', 'worker_name': 'Akmal', 'kg': '10', 'client_uuid': uuid4()})
    out = app.test_cli_runner().invoke(args=['narx', '--qol', '1500', '--kombayn-tonna', '1500000']).output
    assert '1 500 so‘m/kg' in out and 'K-01: 1 500 000 so‘m/tonna' in out
    juma.post('/terim', {'load_id': lid, 'method': 'hand', 'worker_name': 'Akmal', 'kg': '20', 'client_uuid': uuid4()})
    juma.post('/terim', {'load_id': lid, 'method': 'combine', 'combine_id': world['eq']['K-01'], 'kg': '3700', 'client_uuid': uuid4()})
    from surxon.db import q
    with app.app_context():
        rows = [(r['kg'], r['rate'], r['amount']) for r in q('SELECT kg, rate, amount FROM harvests ORDER BY id')]
        assert rows == [(10, None, None), (20, 1500, 30_000), (3700, 1_500_000, 5_550_000)]   # 3.7 t × 1 500 000
        assert q("SELECT COUNT(*) n FROM audit_logs WHERE action='TARIFF'", one=True)['n'] == 2


def _connect_fake_sheets(app, monkeypatch, tabs=None):
    import surxon.outbox as ob
    from fake_sheets import FakeSheets
    fake = FakeSheets(tabs)
    monkeypatch.setattr(ob, '_session', lambda: fake)
    cfg = app.config['SURXON']
    cfg.GOOGLE_SHEETS_ID, cfg.GOOGLE_SERVICE_ACCOUNT_FILE = 'sheet-id', '/x.json'
    return fake


def test_sheets_live_check_counts_once_and_voids_back(app, world, monkeypatch):
    # the owner's hand-made dashboard tab with a formula must stay untouched
    fake = _connect_fake_sheets(app, monkeypatch, {'Umumiy hisob': [['Ko‘rsatkich', 'Qiymat'],
                                                                   ['Kassa', "=VLOOKUP(\"KASSA_QOLDIQ\";'SPX UMUMIY'!A:C;3;FALSE)"]]})
    world['bux'].post('/buxgalteriya/kirim', {'amount': '5 000 000', 'source': 'Direktor', 'client_uuid': uuid4()})
    res = app.test_cli_runner().invoke(args=['sheets-sinov', '--katak', 'Umumiy hisob!B2'])
    out = res.output
    assert res.exit_code == 0, out
    assert 'NATIJA: ISHLAYDI' in out, out
    lines = [l for l in out.splitlines() if l[:2] in ('0)', '1)', '2)', '3)')]
    k = [l.split('KASSA_QOLDIQ = ')[1].split(' |')[0] for l in lines]
    assert k == ['5000000', '5001000', '5001000', '5000000']
    kassa = fake.tabs['SPX KASSA KIRIM-CHIQIM']
    ids = [r[0] for r in kassa[1:]]
    assert len(ids) == len(set(ids)) == 2                                    # INC-…1 and the test INC-…2, once each
    assert kassa[-1][9].startswith('BEKOR')
    assert fake.tabs['Umumiy hisob'][1][1].startswith('=VLOOKUP')             # formula untouched
    assert {'SPX UMUMIY', 'SPX TERIMCHILAR'} <= set(fake.tabs) or 'SPX UMUMIY' in fake.tabs


def test_sheets_inspect_is_read_only(app, world, monkeypatch):
    fake = _connect_fake_sheets(app, monkeypatch, {'Umumiy hisob': [['A', 'B'], ['x', '=1+1']], 'Ishchilar': [['Ism']]})
    before = {k: [list(r) for r in v] for k, v in fake.tabs.items()}
    out = app.test_cli_runner().invoke(args=['sheets-inspect']).output
    assert '[Umumiy hisob] (qo‘lda — tizim YOZMAYDI)' in out and 'formulali katak: 1' in out
    assert fake.tabs == before


def test_bulk_staff_logins(app, tmp_path):
    runner = app.test_cli_runner()
    r = runner.invoke(args=['xodimlar', 'Asadbek:hisobchi', 'Mirjalol:hisobchi', 'Yunus:punkt', 'Karim aka:kassir'])
    assert r.exit_code == 0, r.output
    assert 'login: asadbek' in r.output and 'login: yunus' in r.output and 'Punkt operatori' in r.output
    with app.app_context():
        from surxon.db import q
        y = q("SELECT * FROM users WHERE username='yunus'", one=True)
        k = q("SELECT * FROM users WHERE username='karim'", one=True)
        assert y['role'] == 'station' and y['station_id'] and k['role'] == 'cashier' and k['cashbox_id']
        assert y['must_change_password'] == 1
    # running again changes nothing; a bad role is refused
    r2 = runner.invoke(args=['xodimlar', 'Asadbek:hisobchi'])
    assert 'allaqachon bor' in r2.output
    assert runner.invoke(args=['xodimlar', 'Ali:bosh']).exit_code != 0
    assert (app.config['SURXON'].DATA_DIR / 'LOGINLAR.txt').read_text(encoding='utf-8').count('login:') == 4


def test_staff_page_add_reset_disable(app, admin):
    import re
    r = admin.post('/admin/xodimlar', {'action': 'add', 'role': 'tally', 'full_name': 'Mirjalol'}, json_resp=False)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and 'Login tayyor' in html and '>mirjalol<' in html
    pw = re.search(r'<span>Parol</span><b>([a-z0-9]{8})</b>', html).group(1)
    assert r.headers['Cache-Control'] == 'no-store'
    from conftest import Client
    c = Client(app, 'mirjalol', pw)                    # the generated password really works
    assert c.get('/').status_code in (200, 302)
    # the password is never shown again on a plain visit
    assert pw not in admin.get('/admin/xodimlar').get_data(as_text=True)
    with app.app_context():
        from surxon.db import q
        uid = q("SELECT id FROM users WHERE username='mirjalol'", one=True)['id']
    r = admin.post('/admin/xodimlar', {'action': 'reset', 'uid': uid}, json_resp=False)
    new_pw = re.search(r'<span>Parol</span><b>([a-z0-9]{8})</b>', r.get_data(as_text=True)).group(1)
    assert new_pw != pw
    admin.post('/admin/xodimlar', {'action': 'off', 'uid': uid}, json_resp=False)
    with app.app_context():
        assert q("SELECT active FROM users WHERE id=?", (uid,), one=True)['active'] == 0
    fresh = app.test_client()
    fresh.get('/login')
    # a disabled person cannot log in with either password
    with fresh.session_transaction() as s:
        tok = s.get('csrf')
    r = fresh.post('/login', data={'username': 'mirjalol', 'password': new_pw, '_csrf': tok})
    assert r.status_code != 302 or '/login' in r.headers.get('Location', '')
    # same name again = a second person with a different login (e.g. a namesake)
    r = admin.post('/admin/xodimlar', {'action': 'add', 'role': 'station', 'full_name': 'Mirjalol'}, json_resp=False)
    assert '>mirjalol2<' in r.get_data(as_text=True)
