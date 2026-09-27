"""QR stickers for the machines: one A4 page each (tractor / combine carry the solyarka QR, a trailer a link that
opens its current trip for whoever scans it)."""
import re

from conftest import make_user
from surxon.db import q
from test_dala import open_trip, setup as tally_setup
from test_documents import pdf_text


def test_one_a4_page_per_machine_and_trailer_link_opens_current_trip(app, world):
    admin = world['admin']
    r = admin.get('/admin/texnikalar/qr.pdf')
    assert r.status_code == 200 and r.mimetype == 'application/pdf'
    with app.app_context():
        machines = q('SELECT code, kind, qr_token FROM equipment WHERE active=1')
    with app.app_context():
        pumps = q('SELECT code FROM fuel_stations WHERE active=1 AND approved=1')
    assert len(re.findall(rb'/Type\s*/Page[^s]', r.data)) == len(machines) + len(pumps)
    text = pdf_text(r.data)
    assert 'TRAKTOR' in text and 'KOMBAYN' in text and 'PRITSEP' in text
    for m in machines:
        assert m['code'] in text and m['qr_token']              # every machine got its own token

    only = admin.get('/admin/texnikalar/qr.pdf?turi=telashka')
    assert len(re.findall(rb'/Type\s*/Page[^s]', only.data)) == sum(m['kind'] == 'telashka' for m in machines)
    assert 'TRAKTOR' not in pdf_text(only.data) and 'ZAPRAVKA' not in pdf_text(only.data)

    tok = next(m['qr_token'] for m in machines if m['code'] == 'TL-01')
    # nothing open yet → a friendly message, not an error page
    assert admin.get(f'/tq/{tok}').status_code == 302
    clerk = tally_setup(app, world)
    lid = open_trip(clerk, world, trailer='TL-01')
    assert clerk.get(f'/tq/{tok}').headers['Location'].endswith(f'/dala/reys/{lid}')
    assert admin.get(f'/tq/{tok}').headers['Location'].endswith(f'/yuk/{lid}')
    assert '/dala' in clerk.get('/tq/nonexistent').headers['Location']


def test_fuel_station_gets_its_own_page(app, world):
    from test_fuel import setup as fuel_setup
    fuel_setup(app, world)
    with app.app_context():
        pumps = q('SELECT code FROM fuel_stations WHERE active=1 AND approved=1')
    assert pumps
    r = world['admin'].get('/admin/texnikalar/qr.pdf?turi=zapravka')
    assert len(re.findall(rb'/Type\s*/Page[^s]', r.data)) == len(pumps)
    assert 'ZAPRAVKA' in pdf_text(r.data) and 'Solyarka olishda' in pdf_text(r.data)


def test_qr_sheet_is_for_admin_only(app, world):
    kassa = world['kassa']
    r = kassa.get('/admin/texnikalar/qr.pdf')
    assert r.status_code == 302 and 'pdf' not in r.headers.get('Content-Type', '')
    assert world['admin'].get('/admin/texnikalar').status_code == 200
    assert 'QR kodlar (A4' in world['admin'].get('/admin/texnikalar').get_data(as_text=True)
