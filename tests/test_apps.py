"""Separate phone apps per section, and the o‘rinbosar (a clerk stands in for the cashier, or the other way round)."""
import io

from surxon.db import get_db, q, scalar
from conftest import make_user
from test_dala import open_trip
from test_tezpul import enter, pay, qr, setup, talons


def test_each_section_has_its_own_installable_app(app, world):
    anon = app.test_client()
    page = anon.get('/ilova/kassa')
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and 'SURXON Kassir' in html and 'O‘RNATISH' in html and '/ilova/kassa/manifest.webmanifest' in html
    m = anon.get('/ilova/kassa/manifest.webmanifest').get_json()
    assert m['id'] == '/ilova/kassa' and m['start_url'] == '/ilova/kassa/ochish' and m['display'] == 'standalone'
    assert {i['sizes'] for i in m['icons']} == {'192x192', '512x512'}
    ids = {anon.get(f'/ilova/{k}/manifest.webmanifest').get_json()['id'] for k in ('terim', 'kassa', 'buxgalter', 'punkt', 'rahbar', 'admin')}
    assert len(ids) == 6                                       # five apps can sit side by side on one phone
    from PIL import Image
    png = anon.get('/ilova/terim/icon-512.png')
    assert png.status_code == 200 and Image.open(io.BytesIO(png.data)).size == (512, 512)
    assert anon.get('/ilova/yoq').status_code == 404
    # the installed app opens on the login page (with its own icon) and then straight on its section
    r = anon.get('/ilova/kassa/ochish')
    assert r.status_code == 302 and '/login' in r.headers['Location'] and 'ilova' in r.headers['Location']
    assert '/ilova/kassa/manifest.webmanifest' in anon.get(r.headers['Location']).get_data(as_text=True)
    assert world['kassa'].get('/ilova/kassa/ochish').headers['Location'].endswith(('/tezpul/kassa', '/hamyon'))
    tally = make_user(app, world['admin'], 'terim1', 'tally')
    assert tally.get('/ilova/terim/ochish').headers['Location'].endswith('/dala')
    assert '/login?' in tally.get('/ilova/kassa/ochish').headers['Location']   # not theirs: sign in with that app's login
    assert 'ilova=admin' in tally.get('/ilova/admin/ochish').headers['Location']
    assert world['rahbar'].get('/ilova/rahbar/ochish').headers['Location'].endswith('/rahbar')
    assert world['admin'].get('/ilova/admin/ochish').headers['Location'].endswith('/')
    assert '/ilova/admin/manifest.webmanifest' in world['admin'].get('/').get_data(as_text=True)
    # the section's pages link their own app, so the installed icon and name stay
    assert '/ilova/terim/manifest.webmanifest' in tally.get('/dala').get_data(as_text=True)
    assert '/ilova/buxgalter/manifest.webmanifest' in world['bux'].get('/hamyon').get_data(as_text=True)
    staff = world['admin'].get('/admin/xodimlar').get_data(as_text=True)
    assert 'Telefon ilovalari' in staff and 't.me/share/url' in staff and '/ilova/punkt' in staff


def test_standin_clerk_pays_for_a_day_and_the_panel_shows_it_red(app, world):
    admin, kassa = world['admin'], world['kassa']
    tally, lid, ts = setup(app, world)
    with app.app_context():
        uid = scalar("SELECT id FROM users WHERE username='terim1'")
        box = scalar('SELECT id FROM cashboxes WHERE active=1 ORDER BY id LIMIT 1')
    assert enter(tally, lid, qr(ts[0]), '20').get_json()['ok']
    assert enter(tally, lid, qr(ts[1]), '10').get_json()['ok']
    # without the stand-in the clerk has no kassa
    assert tally.get('/tezpul/kassa').status_code == 302
    assert pay(tally, qr(ts[0])).status_code in (302, 403)
    # only clerk <-> cashier, with a kassa, 1–7 days
    r = admin.post('/admin/xodimlar', {'action': 'standin', 'uid': uid, 'days': '1'}, json_resp=False)
    assert 'kassadan' in admin.get('/admin/xodimlar').get_data(as_text=True)
    with app.app_context():
        assert q('SELECT standin_role FROM users WHERE id=?', (uid,), one=True)['standin_role'] is None
    admin.post('/admin/xodimlar', {'action': 'standin', 'uid': uid, 'days': '9', 'cashbox_id': box}, json_resp=False)
    with app.app_context():
        assert q('SELECT standin_role FROM users WHERE id=?', (uid,), one=True)['standin_role'] is None
        bux_id = scalar("SELECT id FROM users WHERE role='accountant' LIMIT 1")
    admin.post('/admin/xodimlar', {'action': 'standin', 'uid': bux_id, 'days': '1'}, json_resp=False)
    with app.app_context():
        assert q('SELECT standin_role FROM users WHERE id=?', (bux_id,), one=True)['standin_role'] is None
    admin.post('/admin/xodimlar', {'action': 'standin', 'uid': uid, 'days': '1', 'cashbox_id': box}, json_resp=False)
    with app.app_context():
        u = q('SELECT * FROM users WHERE id=?', (uid,), one=True)
        assert u['standin_role'] == 'cashier' and u['cashbox_id'] == box
        assert scalar("SELECT COUNT(*) FROM audit_logs WHERE action='STANDIN'") >= 1
    assert 'Kassir o‘rnida' in admin.get('/admin/xodimlar').get_data(as_text=True)
    # today the clerk also has the kassa — the field screens stay as they were
    assert 'bugun kassir o‘rnidasiz' in tally.get('/dala').get_data(as_text=True)
    assert tally.get('/tezpul/kassa').status_code == 200
    assert tally.get(f'/tezpul/dala/{lid}').status_code == 200
    r = pay(tally, qr(ts[0])).get_json()
    assert r['ok'] and r['result']['amount'] == 40_000
    assert pay(kassa, qr(ts[1])).get_json()['ok']                 # the real cashier's payment is not flagged
    page = world['rahbar'].get('/tezpul/panel').get_data(as_text=True)
    assert 'o‘zi kg yozib, o‘zi pul bergan' in page and page.count('o‘zi kg yozib, o‘zi pul bergan') == 1
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM tezpul_talons WHERE status='TOLANDI' AND paid_by=entered_by") == 1
    # the stand-in ends by itself after its last day
    with app.app_context():
        get_db().execute("UPDATE users SET standin_until='2000-01-01' WHERE id=?", (uid,))
    assert tally.get('/tezpul/kassa').status_code == 302
    assert 'bugun kassir o‘rnidasiz' not in tally.get('/dala').get_data(as_text=True)
    # and the admin can take it back at once
    with app.app_context():
        get_db().execute("UPDATE users SET standin_until='2999-01-01' WHERE id=?", (uid,))
    assert tally.get('/tezpul/kassa').status_code == 200
    admin.post('/admin/xodimlar', {'action': 'standin_off', 'uid': uid}, json_resp=False)
    assert tally.get('/tezpul/kassa').status_code == 302
    with app.app_context():
        assert q('SELECT cashbox_id FROM users WHERE id=?', (uid,), one=True)['cashbox_id'] is None


def test_standin_cashier_writes_talons_only_on_own_trips(app, world):
    admin, kassa = world['admin'], world['kassa']
    tally, lid, ts = setup(app, world)
    with app.app_context():
        kid = scalar("SELECT id FROM users WHERE username='asadbek'")
    assert kassa.get('/dala').status_code == 302
    admin.post('/admin/xodimlar', {'action': 'standin', 'uid': kid, 'days': '2'}, json_resp=False)
    with app.app_context():
        assert q('SELECT standin_role FROM users WHERE id=?', (kid,), one=True)['standin_role'] == 'tally'
    assert kassa.get('/dala').status_code == 200
    assert 'TERIM — siz bugun hisobchi' in kassa.get('/hamyon').get_data(as_text=True)
    assert kassa.get(f'/tezpul/dala/{lid}').status_code == 403          # the other clerk's trip stays theirs
    own = open_trip(kassa, world, rate='2000', trailer='TL-02', field='D-01')
    assert enter(kassa, own, qr(ts[2]), '15').get_json()['ok']
    assert kassa.get('/tezpul/kassa').status_code == 200                # still the cashier


def test_one_phone_two_logins_each_app_its_own(app, world):
    """The clerk's SURXON Terim and the cashier's SURXON Kassir on one phone: each app keeps its own login."""
    from conftest import Client
    admin = world['admin']
    tally, lid, ts = setup(app, world)
    phone = Client(app, 'terim1', 'Worker2026x')                      # Terim installed first
    assert phone.get('/ilova/terim/ochish').headers['Location'].endswith('/dala')
    r = phone.get('/ilova/kassa/ochish')                               # Kassir: not the clerk's — sign in once more
    assert r.status_code == 302 and 'ilova=kassa' in r.headers['Location']
    page = phone.get(r.headers['Location']).get_data(as_text=True)
    assert 'SURXON Kassir' in page and 'ikkala loginni eslab qoladi' in page
    r = phone.c.post(r.headers['Location'], data={'username': 'asadbek', 'password': 'Worker2026x', '_csrf': phone.csrf()})
    assert r.status_code == 302 and r.headers['Location'].endswith('/ilova/kassa/ochish')
    assert phone.get('/ilova/kassa/ochish').headers['Location'].endswith('/tezpul/kassa')
    # back to the Terim icon: the clerk again, not the kassa
    assert phone.get('/ilova/terim/ochish').headers['Location'].endswith('/dala')
    assert phone.get('/dala').status_code == 200
    assert enter(phone, lid, qr(ts[0]), '20').get_json()['ok']
    # both apps stay open: a button pressed in the Kassir app goes as the cashier, one in Terim as the clerk
    assert pay(phone, qr(ts[0])).get_json()['ok']
    assert enter(phone, lid, qr(ts[1]), '15').get_json()['ok']
    with app.app_context():
        rows = {r['number']: dict(r) for r in q("""SELECT t.number, e.username entered, p.username paid FROM tezpul_talons t
                    LEFT JOIN users e ON e.id=t.entered_by LEFT JOIN users p ON p.id=t.paid_by WHERE t.status<>'BOSH'""")}
    assert rows[ts[0]['number']] == {'number': ts[0]['number'], 'entered': 'terim1', 'paid': 'asadbek'}
    assert rows[ts[1]['number']]['entered'] == 'terim1'
    # a shared page opened from the field screen stays the clerk's; the trailer QR is the clerk's
    phone.get('/ilova/kassa/ochish')
    assert phone.get('/', headers={'Referer': 'http://localhost/dala'}).headers['Location'].endswith('/dala')
    phone.get('/ilova/kassa/ochish')
    with app.app_context():
        get_db().execute("UPDATE equipment SET qr_token='tq-test-03' WHERE code='TL-03'")
    assert '/dala/yangi' in phone.get('/tq/tq-test-03').headers['Location']
    # a wrong password for the second login keeps the app's login page; Chiqish signs the phone out of both
    other = Client(app, 'terim1', 'Worker2026x')
    r = other.c.post('/login?next=/ilova/kassa/ochish&ilova=kassa', data={'username': 'asadbek', 'password': 'xato', '_csrf': other.csrf()})
    assert 'ilova=kassa' in r.headers['Location']
    phone.get('/logout')
    assert '/login' in phone.get('/dala').headers['Location']
