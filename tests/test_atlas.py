"""Dalalar xaritasi PDF (A4): all fields, one brigadier or one field — made even without internet (plain grid)."""
import io

from surxon.db import q


def test_fields_atlas_pdf(app, world, monkeypatch):
    from surxon import atlas
    monkeypatch.setattr(atlas, '_tile', lambda z, x, y: None)          # no internet in tests
    admin = world['admin']
    r = admin.get('/admin/dalalar/xarita.pdf')
    assert r.status_code == 200 and r.mimetype == 'application/pdf' and r.data[:4] == b'%PDF'
    from pypdf import PdfReader
    text = ''.join(p.extract_text() for p in PdfReader(io.BytesIO(r.data)).pages)
    assert 'Dalalar xaritasi' in text and 'Dalalar ro' in text and 'Brigadirlar' in text
    with app.app_context():
        f = q('SELECT id, code FROM fields WHERE polygon_json IS NOT NULL LIMIT 1', one=True)
    if f:
        one = admin.get(f'/admin/dalalar/xarita.pdf?dala={f["id"]}')
        assert one.status_code == 200 and f['code'] in ''.join(p.extract_text() for p in PdfReader(io.BytesIO(one.data)).pages)
    assert admin.get('/admin/dalalar/xarita.pdf?dala=999999').status_code == 404
    assert 'Xarita PDF (A4)' in admin.get('/admin/dalalar').get_data(as_text=True)
