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
