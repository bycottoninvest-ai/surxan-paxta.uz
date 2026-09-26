"""Azizbek ERP read-only API and TV wallboard."""
import re

from surxon.db import q
from conftest import Client, jpeg, open_load, uuid4


def make_key(admin, scopes, kind='erp'):
    admin.post('/admin/integratsiyalar', {'action': 'erp_on' if kind == 'erp' else 'tv_on'})
    data = {'action': 'create', 'kind': kind, 'name': 'Azizbek ERP'}
    html = admin.c.post('/admin/integratsiyalar', data={**data, 'scopes': scopes, '_csrf': admin.csrf()}).get_data(as_text=True)
    return re.search(r'spx_%s_[0-9a-f]{8}_[A-Za-z0-9_\-]+' % kind, html).group(0)


def api(app, key, path, **params):
    return app.test_client().get('/api/erp/v1' + path, query_string=params, headers={'Authorization': f'Bearer {key}'})


def chain(world, kg='100', gross='2700', tare='2600', trailer='TL-01'):
    juma = world['juma']
    lid = open_load(juma, world, trailer=trailer)
    juma.post('/terim', {'load_id': lid, 'method': 'hand', 'worker_name': 'Gulbahor opa', 'kg': kg, 'client_uuid': uuid4(),
                         'confirm_duplicate': '1'})
    juma.post(f'/yuk/{lid}/toldi', files={'photos': jpeg()})
    world['tarozi'].post(f'/tarozi/{lid}', {'step': 'gross', 'gross_kg': gross})
    return world['tarozi'].post(f'/tarozi/{lid}', {'step': 'tare', 'tare_kg': tare}).get_json()['waybill_id']


def test_key_is_hashed_and_shown_once(app, world):
    key = make_key(world['admin'], ['summary'])
    with app.app_context():
        row = q('SELECT * FROM integration_clients', one=True)
        assert key not in (row['key_hash'], row['key_prefix']) and len(row['key_hash']) == 64
    assert key not in world['admin'].get('/admin/integratsiyalar').get_data(as_text=True)


def test_auth_enable_scope_and_readonly(app, world):
    key = make_key(world['admin'], ['summary', 'waybills'])
    assert api(app, 'bad', '/summary').status_code == 401
    assert api(app, key, '/summary').status_code == 200
    assert api(app, key, '/payments').status_code == 403                    # scope not granted
    c = app.test_client()
    for m in ('post', 'put', 'patch', 'delete'):
        r = getattr(c, m)('/api/erp/v1/waybills', headers={'Authorization': f'Bearer {key}'})
        assert r.status_code == 405 and r.get_json()['error']['code'] == 'read_only'
    world['admin'].post('/admin/integratsiyalar', {'action': 'erp_off'})
    assert api(app, key, '/summary').status_code == 503
    world['admin'].post('/admin/integratsiyalar', {'action': 'erp_on'})
    with app.app_context():
        cid = q('SELECT id FROM integration_clients', one=True)['id']
    world['admin'].post('/admin/integratsiyalar', {'action': 'revoke', 'id': cid})
    assert api(app, key, '/summary').status_code == 401
    with app.app_context():
        assert q('SELECT COUNT(*) FROM integration_log', one=True)[0] >= 6


def test_rotate_invalidates_old_key(app, world):
    admin = world['admin']
    old = make_key(admin, ['summary'])
    with app.app_context():
        cid = q('SELECT id FROM integration_clients', one=True)['id']
    html = admin.c.post('/admin/integratsiyalar', data={'action': 'rotate', 'id': cid, '_csrf': admin.csrf()}).get_data(as_text=True)
    new = re.search(r'spx_erp_[0-9a-f]{8}_[A-Za-z0-9_\-]+', html).group(0)
    assert api(app, old, '/summary').status_code == 401
    assert api(app, new, '/summary').status_code == 200


def test_data_filters_pagination_and_updated_since(app, world):
    key = make_key(world['admin'], list(['summary', 'harvests', 'loads', 'weighings', 'waybills', 'nayman', 'receivables',
                                         'payments', 'expenses', 'cash', 'reference']))
    w1 = chain(world, trailer='TL-01')
    w2 = chain(world, trailer='TL-02')
    r = api(app, key, '/waybills', per_page=1).get_json()
    assert r['meta']['total'] == 2 and r['meta']['has_more'] and r['meta']['next_page'] == 2 and len(r['data']) == 1
    assert r['meta']['units']['mass'] == 'kg' and r['meta']['read_only'] is True
    mx = api(app, key, '/waybills').get_json()['meta']['max_updated_at']
    same = api(app, key, '/waybills', updated_since=mx).get_json()['data']
    assert all(x['updated_at'] >= mx for x in same)                  # inclusive boundary, nothing older
    r = api(app, key, '/harvests', field_id=world['f']['D-04']).get_json()
    assert r['meta']['total'] == 2 and r['data'][0]['worker_name'] is None      # names hidden without 'workers' scope
    assert api(app, key, '/harvests', field_id=world['f']['D-01']).get_json()['meta']['total'] == 0
    assert api(app, key, '/harvests', date_from='bad').status_code == 400
    # no price, no opening balance -> reported as not calculated, never 0
    s = api(app, key, '/summary').get_json()['data']
    assert s['finance']['nayman_debt']['amount'] is None and s['finance']['nayman_debt']['status'] == 'not_calculated'
    assert s['finance']['cash_balance']['amount'] is None and s['finance']['cash_balance']['status'] == 'not_calculated'
    rec = api(app, key, '/receivables').get_json()['data']
    assert {x['balance_status'] for x in rec} == {'awaiting_acceptance'}
    world['bux'].post(f'/nayman/{w1}', {'accepted_kg': '100', 'received_date': '2026-01-01'})
    world['bux'].post(f'/nayman/{w2}', {'accepted_kg': '100', 'received_date': '2026-01-01'})
    rec = {x['waybill_id']: x for x in api(app, key, '/receivables').get_json()['data']}
    assert rec[w2]['balance'] is None and rec[w2]['balance_status'] == 'price_not_set'     # no price agreed yet
    assert world['bux'].post('/buxgalteriya/narx', {'hand': '7000', 'combine': ''}).get_json()['ok']
    rec = {x['waybill_id']: x for x in api(app, key, '/receivables').get_json()['data']}
    assert rec[w1]['balance'] == 700000 and rec[w1]['balance_status'] == 'calculated' and rec[w1]['price_per_kg'] == 7000
    # a correction shows up through updated_since
    import time
    time.sleep(1.1)
    mx = api(app, key, '/waybills').get_json()['meta']['max_updated_at']
    time.sleep(1.1)
    world['rahbar'].post('/tarozi/1/tuzatish', {'gross_kg': '2710', 'tare_kg': '2600', 'reason': 'tablo'})
    changed = api(app, key, '/waybills', updated_since=mx).get_json()['data']
    assert [x['net_kg'] for x in changed if x['id'] == 1] == [110]
    for path in ('/loads', '/weighings', '/nayman-receipts', '/payments', '/expenses', '/cash-entries', '/cash-balance',
                 '/fields', '/brigadiers', '/equipment', '/meta'):
        assert api(app, key, path).status_code == 200, path


def test_tv_access_and_no_private_data(app, world):
    chain(world)
    world['kassa'].post('/kassa', {'category': 'opening', 'amount': '777777', 'entry_date': '2026-01-01', 'client_uuid': uuid4()})
    anon = app.test_client()
    assert anon.get('/tv').status_code == 403
    assert anon.get('/tv/data.json').status_code == 403
    key = make_key(world['admin'], [], kind='tv')
    tv = app.test_client()
    assert tv.get('/tv?k=' + key).status_code == 200
    data = tv.get('/tv/data.json').get_json()                 # cookie set on first open
    body = str(data)
    assert data['kpi']['net'] == 100
    assert '777777' not in body and 'amount' not in body and 'so‘m' not in body     # never money on the TV
    # “Bugungi real voqealar”: the trailer photo of the day, served to the TV as a thumbnail
    assert data['live'] and data['live'][0]['url'].startswith('/tv/foto/') and tv.get(data['live'][0]['url']).status_code == 200
    with app.app_context():
        from surxon.db import get_db
        get_db().execute('''INSERT INTO tg_members(full_name, status, source, created_at) VALUES ('Ali','FAOL','admin','x')''')
        pid = q("SELECT thumb_path FROM photos WHERE category='trailer' LIMIT 1", one=True)['thumb_path']
        from surxon.utils import now_str
        get_db().execute('''INSERT INTO media_items(member_id, kind, path, thumb_path, created_at) VALUES
                            ((SELECT MAX(id) FROM tg_members), 'video', 'kuzatuv/x.mp4', ?, ?)''', (pid, now_str()))
        iid = q('SELECT MAX(id) i FROM media_items', one=True)['i']
    live = tv.get('/tv/data.json').get_json()['live']
    assert any(x['video'] and x['url'] == f'/tv/kuzatuv/{iid}' for x in live)
    assert tv.get(f'/tv/kuzatuv/{iid}').status_code == 200 and anon.get(f'/tv/kuzatuv/{iid}').status_code == 403
    world['admin'].post('/admin/sozlamalar', {'set_tv_show_workers': '0'})
    assert 'Gulbahor' not in str(tv.get('/tv/data.json').get_json())            # names can be switched off
    assert world['juma'].get('/tv/data.json').status_code == 200   # logged-in users need no key
    world['admin'].post('/admin/integratsiyalar', {'action': 'tv_off'})
    assert tv.get('/tv/data.json').status_code == 403
