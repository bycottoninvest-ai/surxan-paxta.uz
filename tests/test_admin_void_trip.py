"""Admin can cancel a whole (test or mistaken) trip in one step — even after the punkt received it; nothing is erased."""
from surxon.db import get_db, q, scalar
from conftest import Client, make_user, uuid4
from test_rahbar import flat, trip


def setup(app, world):
    admin = world['admin']
    admin.post('/admin/sozlamalar', {'set_auto_waybill_hand': '1'})
    tally = make_user(app, admin, 'mirjalol', 'tally')
    admin.post('/admin/foydalanuvchilar', {'username': 'yunus', 'full_name': 'Yunus', 'role': 'station',
                                           'password': 'Worker2026x', 'station_id': 1})
    with app.app_context():
        get_db().execute("UPDATE users SET must_change_password=0 WHERE username='yunus'")
    return tally, Client(app, 'yunus', 'Worker2026x')


def test_admin_cancels_received_trip(app, world):
    admin = world['admin']
    tally, punkt = setup(app, world)
    wb, lid = trip(app, world, tally, ['100', '120'])
    assert punkt.post(f'/punkt/yuk/{wb}/qabul', {'station_kg': '215', 'reason': 'Tarozilar farqi'}).get_json()['ok']
    page = admin.get(f'/yuk/{lid}').get_data(as_text=True)
    assert 'Admin: reysni to‘liq bekor qilish' in page
    # not for the director, even though he may void open trips
    assert 'reysni to‘liq bekor' not in world['rahbar'].get(f'/yuk/{lid}').get_data(as_text=True)
    assert not world['rahbar'].post(f'/yuk/{lid}/admin-bekor', {'reason': 'sinov'}).get_json()['ok']
    assert not admin.post(f'/yuk/{lid}/admin-bekor', {'reason': ''}).get_json()['ok']       # reason required
    r = admin.post(f'/yuk/{lid}/admin-bekor', {'reason': 'Sinov reysi'}).get_json()
    assert r['ok'], r
    with app.app_context():
        assert scalar('SELECT status FROM trailer_loads WHERE id=?', (lid,)) == 'BEKOR'
        assert scalar('SELECT status FROM waybills WHERE id=?', (wb,)) == 'BEKOR'
        assert scalar('SELECT COUNT(*) FROM harvests WHERE load_id=? AND voided_at IS NULL', (lid,)) == 0
        assert scalar('SELECT COUNT(*) FROM nayman_receipts WHERE waybill_id=?', (wb,)) == 0
        kept = q("SELECT old_json FROM audit_logs WHERE entity_type='nayman_receipt' AND action='VOID'", one=True)
        assert kept and '215' in kept['old_json']                                            # full copy kept
        assert scalar('SELECT COUNT(*) FROM harvests WHERE load_id=?', (lid,)) == 2         # rows stay, marked void
    home = flat(world['rahbar'].get('/rahbar').get_data(as_text=True))
    assert '0 kg' in home                                                                    # panel back to zero
    assert not admin.post(f'/yuk/{lid}/admin-bekor', {'reason': 'yana'}).get_json()['ok']  # already cancelled


def test_blocked_when_worker_already_paid(app, world):
    admin, bux = world['admin'], world['bux']
    tally, _ = setup(app, world)
    wb, lid = trip(app, world, tally, ['100'])
    chk = bux.post('/hamyon/kirim/tekshir', {'amount': '1 000 000', 'source': 'Direktor'}).get_json()
    bux.post('/hamyon/kirim/tasdiq', {'amount': '1 000 000', 'source': 'Direktor', 'expect': chk['expect'], 'client_uuid': uuid4()})
    with app.app_context():
        w = scalar("SELECT worker_id FROM harvests WHERE load_id=?", (lid,))
    c = bux.post(f'/hamyon/tolov/{w}/tekshir', {'amount': '150 000'}).get_json()
    assert bux.post(f'/hamyon/tolov/{w}/tasdiq', {'amount': '150 000', 'expect': c['expect'], 'client_uuid': uuid4()}).get_json()['ok']
    r = admin.post(f'/yuk/{lid}/admin-bekor', {'reason': 'Sinov reysi'}).get_json()
    assert not r['ok'] and 'to‘lov' in r['error']
    with app.app_context():
        assert scalar('SELECT status FROM trailer_loads WHERE id=?', (lid,)) != 'BEKOR'
