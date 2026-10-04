"""Guruh boshlig‘i: one weighing for a whole team (he shares the money himself); everyone else keeps the 250 kg check."""
from conftest import make_user
from surxon.db import q
from test_dala import open_trip, weigh


def test_group_leader_writes_team_kg_at_once(app, world):
    admin = world['admin']
    tally = make_user(app, admin, 'hisob1', 'tally')
    lid = open_trip(tally, world)
    r = weigh(tally, lid, '600', name='Mirjalol', new=True)              # an ordinary worker: 600 kg is a typo
    assert not r['ok'] and 'chegara 250' in r['error'] and 'Guruh boshlig‘i' in r['error']
    assert weigh(tally, lid, '40', name='Mirjalol', new=True)['ok']
    with app.app_context():
        wid = q("SELECT id FROM workers WHERE full_name='Mirjalol'", one=True)['id']
    # the tally cannot make anyone a group leader — only admin / rahbar
    r = tally.post(f'/ishchi/{wid}', {'action': 'group', 'is_group': '1', 'group_size': '45'})
    assert r.status_code in (302, 403, 422)
    assert admin.post(f'/ishchi/{wid}', {'action': 'group', 'is_group': '1', 'group_size': '45'}).get_json()['ok']
    assert '👥 Guruh boshlig‘i · 45 kishi' in admin.get(f'/ishchi/{wid}').get_data(as_text=True)
    found = tally.get('/api/workers?q=mirj').get_json()['workers']
    assert found and found[0]['group'] == 45
    assert weigh(tally, lid, '600', worker_id=wid)['ok']                    # the team's 600 kg in one line
    r = weigh(tally, lid, '3500', worker_id=wid)                           # still a ceiling for typos
    assert not r['ok'] and 'chegara 3000' in r['error']
    r = weigh(tally, lid, '600', name='Boshqa odam', new=True)             # nobody else gets the larger limit
    assert not r['ok']
    with app.app_context():
        assert q('SELECT SUM(kg) s FROM harvests WHERE worker_id=? AND voided_at IS NULL', (wid,), one=True)['s'] == 640
    # back to an ordinary worker
    assert admin.post(f'/ishchi/{wid}', {'action': 'group'}).get_json()['ok']
    assert not weigh(tally, lid, '600', worker_id=wid)['ok']
