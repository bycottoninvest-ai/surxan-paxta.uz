"""Field contours: KML/GeoJSON → review → confirm; no duplicates on re-import; ids, trips and typed areas survive."""
import io
import json

from surxon.db import get_db, q, scalar
from conftest import make_user, open_load


def square(lon, lat, d=0.005):
    return [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]


def kml(polys):
    marks = ''.join(f'<Placemark><name>{n}</name><Polygon><outerBoundaryIs><LinearRing><coordinates>'
                    + ' '.join(f'{x},{y},0' for x, y in ring) + '</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>'
                    for n, ring in polys)
    return f'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>{marks}</Document></kml>'.encode()


FILE = kml([('Dala 11', square(59.60, 42.30)), ('Dala 12', square(59.61, 42.30)),
            ('Dala 13', [[59.62, 42.30], [59.625, 42.30], [59.625, 42.305]])])       # open ring → rejected


def upload(client, data, name='dalalar.kml'):
    resp = client.c.post('/admin/dalalar/import', data={'_csrf': client.csrf(), 'file': (io.BytesIO(data), name)},
                         headers={**client.headers, 'X-Requested-With': 'fetch', 'Accept': 'application/json'},
                         content_type='multipart/form-data')
    body = resp.get_json()
    assert body['ok'], body
    return int(body['redirect'].rsplit('/', 1)[1])


def confirm(client, iid, rows):
    data = {}
    for i, v in rows.items():
        for k, val in v.items():
            data[f'{k}__{i}'] = val
    return client.post(f'/admin/dalalar/import/{iid}', data).get_json()


def test_review_then_confirm_and_reimport(app, world):
    admin, b = world['admin'], world['b']
    before = scalar_count(app)
    iid = upload(admin, FILE)
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM fields') == before          # nothing written before the confirm
        rows = json.loads(q('SELECT data_json FROM field_imports WHERE id=?', (iid,), one=True)['data_json'])
    assert [r['code'] for r in rows] == ['D-11', 'D-12', 'D-13'] and rows[2]['problems'] and not rows[0]['problems']
    assert abs(rows[0]['map_ha'] - 22.9) < 1.5                           # ~0.005° × 0.005° at 42°N
    page = admin.get(f'/admin/dalalar/import/{iid}').get_data(as_text=True)
    assert 'Ko‘rib chiqilmoqda' in page and '320' in page and 'nuqta kerak' in page
    r = confirm(admin, iid, {1: {'take': '1', 'code': 'D-11', 'name': 'Dala 11', 'brigadier_id': b['Juma ota'], 'active': '1',
                                 'crop': 'Paxta'},
                             2: {'take': '1', 'code': 'D-12', 'name': 'Dala 12', 'confirmed_ha': '20', 'active': '1'},
                             3: {'take': '1', 'code': 'D-13', 'name': 'Dala 13'}})
    assert not r['ok'] and 'nuqta kerak' in r['error']                    # a broken contour is named, nothing saved
    r = confirm(admin, iid, {1: {'take': '1', 'code': 'D-11', 'name': 'Dala 11', 'brigadier_id': b['Juma ota'], 'active': '1',
                                 'crop': 'Paxta'},
                             2: {'take': '1', 'code': 'D-12', 'name': 'Dala 12', 'confirmed_ha': '20', 'active': '1'}})
    assert r['ok'], r
    with app.app_context():
        f1 = q("SELECT * FROM fields WHERE source_id='KML-001'", one=True)
        f2 = q("SELECT * FROM fields WHERE source_id='KML-002'", one=True)
        assert f1['area_source'] == 'xarita' and f1['area_ha'] == f1['map_area_ha'] and f1['brigadier_id'] == b['Juma ota']
        assert f2['area_source'] == 'tasdiqlangan' and f2['area_ha'] == 20 and f2['map_area_ha'] > 20
        assert json.loads(f1['polygon_json'])[0] == [42.30, 59.60]      # [lat, lon], coordinates as given
        assert q('SELECT crop FROM field_seasons WHERE field_id=?', (f1['id'],), one=True)['crop'] == 'Paxta'
        assert scalar('SELECT COUNT(*) FROM field_assignments WHERE field_id=?', (f1['id'],)) == 1
    # a trip on D-01, then the field is renamed and moved to another brigade by a second import of the same file
    iid2 = upload(admin, FILE)
    with app.app_context():
        rows = json.loads(q('SELECT data_json FROM field_imports WHERE id=?', (iid2,), one=True)['data_json'])
    assert rows[0]['match'] == 'update' and rows[1]['match'] == 'update'
    r = confirm(admin, iid2, {1: {'take': '1', 'code': 'D-11', 'name': 'Katta dala (1)', 'brigadier_id': b['Nurim ota'], 'active': '1'},
                              2: {'take': '1', 'code': 'D-12', 'name': 'Dala 12', 'active': '1'}})
    assert r['ok'], r
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM fields WHERE source_id LIKE 'KML-%'") == 2          # no duplicates
        g1 = q("SELECT * FROM fields WHERE source_id='KML-001'", one=True)
        g2 = q("SELECT * FROM fields WHERE source_id='KML-002'", one=True)
        assert g1['id'] == f1['id'] and g1['name'] == 'Katta dala (1)' and g1['brigadier_id'] == b['Nurim ota']
        assert g2['area_ha'] == 20 and g2['area_source'] == 'tasdiqlangan'                    # confirmed area kept
        assert scalar('SELECT COUNT(*) FROM field_assignments WHERE field_id=?', (f1['id'],)) == 2
    # the same saved import confirmed again changes nothing
    assert confirm(admin, iid, {1: {'take': '1', 'code': 'X', 'name': 'X'}})['ok']
    with app.app_context():
        assert q("SELECT name FROM fields WHERE source_id='KML-001'", one=True)['name'] == 'Katta dala (1)'


def scalar_count(app):
    with app.app_context():
        return scalar('SELECT COUNT(*) FROM fields')


def test_existing_field_keeps_id_trips_and_typed_area(app, world):
    admin, juma = world['admin'], world['juma']
    lid = open_load(juma, world, field='D-04')                           # D-04 exists (126.2 ga typed), a trip on it
    with app.app_context():
        d4 = q("SELECT * FROM fields WHERE code='D-04'", one=True)
    gj = json.dumps({'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'source_id': 'KML-004', 'nomi': 'Dala 04'},
         'geometry': {'type': 'Polygon', 'coordinates': [square(59.63, 42.31)]}},
        {'type': 'Feature', 'properties': {'source_id': 'KML-009', 'nomi': 'Dala 09'},
         'geometry': {'type': 'MultiPolygon', 'coordinates': []}}]}).encode()
    iid = upload(admin, gj, 'dalalar.geojson')
    with app.app_context():
        rows = json.loads(q('SELECT data_json FROM field_imports WHERE id=?', (iid,), one=True)['data_json'])
    assert rows[0]['match'] == 'link' and rows[1]['problems']
    assert confirm(admin, iid, {1: {'take': '1', 'code': 'D-04', 'name': 'Dala 04 (kontur)', 'active': '1'}})['ok']
    with app.app_context():
        f = q("SELECT * FROM fields WHERE code='D-04'", one=True)
        assert f['id'] == d4['id'] and f['area_ha'] == 126.2 and f['area_source'] == 'qo‘lda' and f['map_area_ha']
        assert f['polygon_json'] and q('SELECT field_id FROM trailer_loads WHERE id=?', (lid,), one=True)['field_id'] == f['id']
    # the field page shows the real figures and the area label; confirming the area is a separate step
    page = admin.get(f'/admin/dala/{f["id"]}').get_data(as_text=True)
    assert 'DALA TERIM KG' in page and 'PUNKT QABUL KG' in page and 'qo‘lda kiritilgan' in page


def test_only_masterdata_role_imports(app, world):
    for who in ('bux', 'kassa', 'juma'):
        r = world[who].c.post('/admin/dalalar/import', data={'_csrf': world[who].csrf(), 'file': (io.BytesIO(FILE), 'a.kml')},
                              headers={**world[who].headers, 'X-Requested-With': 'fetch'}, content_type='multipart/form-data')
        assert r.status_code in (302, 403), who
    assert world['rahbar'].get('/admin/dalalar/import').status_code == 200


def test_draw_a_new_field_on_the_map(app, world):
    admin, b = world['admin'], world['b']
    page = admin.get('/admin/dalalar/chizish').get_data(as_text=True)
    assert 'Xaritada yangi dala chizish' in page
    ring = [[42.40, 59.70], [42.40, 59.705], [42.405, 59.705], [42.405, 59.70]]
    r = admin.post('/admin/dalalar/chizish', {'code': 'D-58', 'name': 'Yangi 10 ga', 'polygon_json': json.dumps(ring),
                                              'brigadier_id': b['Bayram ota'], 'crop': 'Paxta'}).get_json()
    assert r['ok'], r
    with app.app_context():
        f = q("SELECT * FROM fields WHERE code='D-58'", one=True)
        assert f['area_source'] == 'xarita' and abs(f['area_ha'] - f['map_area_ha']) < 1e-9 and 20 < f['area_ha'] < 26
        assert scalar('SELECT COUNT(*) FROM field_assignments WHERE field_id=?', (f['id'],)) == 1
    # redrawing the contour on the field page: an unconfirmed area follows the new contour
    bigger = [[42.40, 59.70], [42.40, 59.71], [42.405, 59.71], [42.405, 59.70]]
    assert admin.post('/admin/dalalar', {'id': f['id'], 'code': 'D-58', 'name': 'Yangi 10 ga', 'area_ha': f['area_ha'],
                                         'brigadier_id': b['Bayram ota'], 'polygon_json': json.dumps(bigger), 'active': '1'}).get_json()['ok']
    with app.app_context():
        g = q("SELECT * FROM fields WHERE code='D-58'", one=True)
        assert g['area_source'] == 'xarita' and g['area_ha'] == g['map_area_ha'] and g['area_ha'] > 40
    # a self-crossing contour and a taken code are refused
    bow = [[42.40, 59.70], [42.405, 59.705], [42.40, 59.705], [42.405, 59.70]]
    assert not admin.post('/admin/dalalar/chizish', {'code': 'D-59', 'name': 'x', 'polygon_json': json.dumps(bow)}).get_json()['ok']
    assert not admin.post('/admin/dalalar/chizish', {'code': 'D-58', 'name': 'x', 'polygon_json': json.dumps(ring)}).get_json()['ok']
    assert world['bux'].get('/admin/dalalar/chizish').status_code == 302
