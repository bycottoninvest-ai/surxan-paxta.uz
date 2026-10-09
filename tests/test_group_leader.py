"""Guruh boshlig‘i: one weighing for a whole team (he shares the money himself); everyone else keeps the 250 kg check."""
from conftest import make_user
from surxon.db import q
from test_dala import open_trip, weigh


def test_group_leader_writes_team_kg_at_once(app, world):
    admin = world['admin']
    tally = make_user(app, admin, 'hisob1', 'tally')
    lid = open_trip(tally, world)
    r = weigh(tally, lid, '600', name='Mirjalol', new=True)              # an ordinary worker: 600 kg is a typo
    assert not r['ok'] and 'chegara 250' in r['error'] and 'Starshi qilib saqlash' in r['error']
    assert weigh(tally, lid, '40', name='Mirjalol', new=True)['ok']
    with app.app_context():
        wid = q("SELECT id FROM workers WHERE full_name='Mirjalol'", one=True)['id']
    # a brigadier cannot make anyone a group leader (admin, rahbar or the tally can)
    juma = world['juma']
    r = juma.post(f'/ishchi/{wid}', {'action': 'group', 'is_group': '1', 'group_size': '45'})
    assert r.status_code in (302, 403, 422) or not r.get_json()['ok']
    assert admin.post(f'/ishchi/{wid}', {'action': 'group', 'is_group': '1', 'group_size': '45'}).get_json()['ok']
    assert '👥 Starshi · 45 kishi' in admin.get(f'/ishchi/{wid}').get_data(as_text=True)
    found = tally.get('/api/workers?q=mirj').get_json()['workers']
    assert found and found[0]['group'] == 45
    assert weigh(tally, lid, '600', worker_id=wid)['ok']                    # the team's 600 kg in one line
    assert weigh(tally, lid, '10000', worker_id=wid)['ok']                  # 10 tonnes for the whole team
    r = weigh(tally, lid, '25000', worker_id=wid)                           # still a ceiling for typos
    assert not r['ok'] and 'chegara 20000' in r['error']
    r = weigh(tally, lid, '600', name='Boshqa odam', new=True)             # nobody else gets the larger limit
    assert not r['ok']
    with app.app_context():
        assert q('SELECT SUM(kg) s FROM harvests WHERE worker_id=? AND voided_at IS NULL', (wid,), one=True)['s'] == 10640
    # back to an ordinary worker
    assert admin.post(f'/ishchi/{wid}', {'action': 'group'}).get_json()['ok']
    assert not weigh(tally, lid, '600', worker_id=wid)['ok']


def test_tally_adds_a_new_starshi_from_the_field(app, world):
    """“👥 Yangi starshi (guruh)” on the field screen: the person is created as a group leader and his whole team's kg
    goes in one line at once — nobody has to mark him on the office page first."""
    admin = world['admin']
    tally = make_user(app, admin, 'hisob2', 'tally')
    lid = open_trip(tally, world)
    data = {'kg': '4200', 'worker_name': 'Rustam starshi', 'new_worker': '2', 'client_uuid': __import__('uuid').uuid4().hex}
    r = tally.post(f'/dala/reys/{lid}/tortish', data).get_json()
    assert r['ok'], r
    assert 'yangi starshi' in r['message'] and r['worker']['group'] == 1
    with app.app_context():
        w = q("SELECT * FROM workers WHERE full_name='Rustam starshi'", one=True)
        assert w['group_size'] == 1
        assert q("SELECT 1 FROM audit_logs WHERE action='GROUP_LEADER' AND entity_id=?", (w['id'],), one=True)
    # an ordinary new person still keeps the 250 kg check
    data = {'kg': '4200', 'worker_name': 'Oddiy odam', 'new_worker': '1', 'client_uuid': __import__('uuid').uuid4().hex}
    assert not tally.post(f'/dala/reys/{lid}/tortish', data).get_json()['ok']


def test_personal_hand_rate_only_for_that_person(app, world):
    """Admin gives one person (e.g. “Yunus-3”) his own hand rate: his weighings freeze at 4 000 so‘m/kg, everybody else on
    the same trip keeps the trip's rate; the tally cannot change it; clearing it brings the trip rate back."""
    admin = world['admin']
    tally = make_user(app, admin, 'hisob3', 'tally')
    lid = open_trip(tally, world, rate='1500')
    assert weigh(tally, lid, '30', name='Yunus-3', new=True)['ok']
    with app.app_context():
        wid = q("SELECT id FROM workers WHERE full_name='Yunus-3'", one=True)['id']
    r = tally.post(f'/ishchi/{wid}', {'action': 'rate', 'rate': '9000'})
    assert r.status_code in (302, 403, 422) or not r.get_json()['ok']
    assert not admin.post(f'/ishchi/{wid}', {'action': 'rate', 'rate': '-5'}).get_json()['ok']
    assert admin.post(f'/ishchi/{wid}', {'action': 'rate', 'rate': '4 000'}).get_json()['ok']
    assert 'Shaxsiy narx' in admin.get(f'/ishchi/{wid}').get_data(as_text=True)
    assert weigh(tally, lid, '50', worker_id=wid)['ok']
    assert weigh(tally, lid, '40', name='Boshqa terimchi', new=True)['ok']
    with app.app_context():
        rows = q('''SELECT w.full_name n, h.kg, h.rate, h.amount FROM harvests h JOIN workers w ON w.id=h.worker_id
                    WHERE h.load_id=? ORDER BY h.id''', (lid,))
        assert [(x['n'], x['rate'], x['amount']) for x in rows] == [
            ('Yunus-3', 1500, 45000),               # written before: keeps its frozen rate
            ('Yunus-3', 4000, 200000),
            ('Boshqa terimchi', 1500, 60000)]
        assert q("SELECT 1 FROM audit_logs WHERE action='RATE' AND entity_id=?", (wid,), one=True)
    assert admin.post(f'/ishchi/{wid}', {'action': 'rate', 'rate': ''}).get_json()['ok']
    assert weigh(tally, lid, '20', worker_id=wid)['ok']
    with app.app_context():
        assert q('SELECT rate FROM harvests WHERE load_id=? ORDER BY id DESC LIMIT 1', (lid,), one=True)['rate'] == 1500


def test_one_tap_makes_a_person_starshi_from_the_field(app, world):
    """“👥 Starshi qilib saqlash” after the 250 kg message: works for a new name and for a person already in the list."""
    admin = world['admin']
    tally = make_user(app, admin, 'hisob4', 'tally')
    lid = open_trip(tally, world, rate='4000')
    r = weigh(tally, lid, '1000', name='Yunus 3', new=True)
    assert not r['ok'] and 'Starshi qilib saqlash' in r['error']
    uid = __import__('uuid').uuid4().hex
    r = tally.post(f'/dala/reys/{lid}/tortish', {'kg': '1000', 'worker_name': 'Yunus 3', 'new_worker': '2', 'client_uuid': uid}).get_json()
    assert r['ok'], r
    assert weigh(tally, lid, '30', name='Odil', new=True)['ok']
    with app.app_context():
        oid = q("SELECT id FROM workers WHERE full_name='Odil'", one=True)['id']
    assert not weigh(tally, lid, '900', worker_id=oid)['ok']
    r = tally.post(f'/dala/reys/{lid}/tortish', {'kg': '900', 'worker_id': oid, 'worker_name': 'Odil', 'new_worker': '2',
                                                 'client_uuid': __import__('uuid').uuid4().hex}).get_json()
    assert r['ok'], r
    with app.app_context():
        assert q('SELECT group_size FROM workers WHERE id=?', (oid,), one=True)['group_size'] == 1
        assert q("SELECT COUNT(*) n FROM workers WHERE full_name='Odil'", one=True)['n'] == 1     # no second Odil
        assert q("SELECT 1 FROM audit_logs WHERE action='GROUP_LEADER' AND entity_id=?", (oid,), one=True)
