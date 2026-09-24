"""Waybill PDFs (server-side, archived, versioned) and delivery to external archives."""
import json

import pytest

from surxon.db import get_db, q, scalar
from conftest import jpeg, open_load, uuid4

SENT = []


def chain(world, price=None):
    if price:
        world['admin'].post('/admin/sozlamalar', {'set_price_per_kg': str(price)})
    juma, scale = world['juma'], world['tarozi']
    lid = open_load(juma, world)
    juma.post('/terim', {'load_id': lid, 'method': 'hand', 'worker_name': 'Gulbahor opa', 'kg': '100', 'client_uuid': uuid4()})
    juma.post(f'/yuk/{lid}/toldi', files={'photos': jpeg()})
    scale.post(f'/tarozi/{lid}', {'step': 'gross', 'gross_kg': '2701'})
    return lid, scale.post(f'/tarozi/{lid}', {'step': 'tare', 'tare_kg': '2600'}).get_json()['waybill_id']


def pdf_text(data):
    import io
    from pypdf import PdfReader
    return '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


def test_pdf_created_on_weighing_and_nayman_copy_has_no_price(app, world):
    lid, wid = chain(world, price=7800)
    with app.app_context():
        docs = q('SELECT * FROM documents WHERE waybill_id=? ORDER BY kind', (wid,))
        assert [(d['kind'], d['version']) for d in docs] == [('ichki', 1), ('nayman', 1)]
        cfg = app.config['SURXON']
        nay = (cfg.UPLOAD_DIR / [d for d in docs if d['kind'] == 'nayman'][0]['path']).read_bytes()
        ich = (cfg.UPLOAD_DIR / [d for d in docs if d['kind'] == 'ichki'][0]['path']).read_bytes()
    assert nay.startswith(b'%PDF') and ich.startswith(b'%PDF')
    nt, it = pdf_text(nay), pdf_text(ich)
    assert 'PA-000001' in nt and 'Nayman uchun nusxa' in nt and '101 kg' in nt and 'Gulbahor' not in nt
    assert 'Narx' not in nt and 'summa' not in nt.lower() and '7 800' not in nt and '787 800' not in nt
    assert 'Narx' in it and '7 800' in it and '787 800' in it          # 101 kg * 7800
    assert 'Jo‘natuvchi' in nt                                           # Uzbek letters render
    # downloads: brigadier of the brigade gets the Nayman copy, not the internal one
    nd = [d for d in docs if d['kind'] == 'nayman'][0]['id']
    idd = [d for d in docs if d['kind'] == 'ichki'][0]['id']
    assert world['juma'].get(f'/hujjat/{nd}').status_code == 200
    assert world['juma'].get(f'/hujjat/{idd}').status_code == 403
    assert world['nurim'].get(f'/hujjat/{nd}').status_code == 403
    assert world['bux'].get(f'/hujjat/{idd}').status_code == 200
    assert app.test_client().get(f'/hujjat/{nd}').status_code == 302
    # the HTML print copy (for Nayman) never shows price or amount
    html = world['admin'].get(f'/nakladnoy/{wid}?print=1').get_data(as_text=True)
    assert '7 800' not in html and 'Jami summa' not in html and 'Narx' not in html


def test_correction_creates_new_version_and_keeps_old(app, world):
    lid, wid = chain(world)
    world['rahbar'].post(f'/tarozi/{lid}/tuzatish', {'gross_kg': '2711', 'tare_kg': '2600', 'reason': 'tablo'})
    world['rahbar'].post(f'/nakladnoy/{wid}/pdf', {})
    with app.app_context():
        rows = q('SELECT version, kind, superseded FROM documents WHERE waybill_id=? ORDER BY version, kind', (wid,))
        assert [(r['version'], r['superseded']) for r in rows] == [(1, 1), (1, 1), (2, 1), (2, 1), (3, 0), (3, 0)]


def test_missing_pdf_is_regenerated_by_worker(app, world, monkeypatch):
    import surxon.services as sv
    real = sv.create_waybill_documents
    monkeypatch.setattr(sv, 'create_waybill_documents', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('font')))
    lid, wid = chain(world)                                   # weighing still commits even if PDF fails
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM waybills') == 1 and scalar('SELECT COUNT(*) FROM documents') == 0
    monkeypatch.setattr(sv, 'create_waybill_documents', real)
    with app.test_request_context():
        from surxon.outbox import run_once
        run_once()
        assert scalar('SELECT COUNT(*) FROM documents') == 2


def test_archive_channels_not_connected_until_configured(app, world):
    chain(world)
    with app.test_request_context():
        from surxon.outbox import run_once, status
        st = {s['channel']: s for s in status()}
        assert st['telegram_archive']['state'] == 'not_connected'
        assert st['sheets']['state'] == 'not_connected' and st['offsite']['state'] == 'not_connected'
        assert st['telegram_archive']['pending'] >= 3       # 2 PDFs + TOLDI photo are queued, not lost
        res = run_once()
        assert res['sent'] == 0 and 'telegram_archive' in res['skipped_channels']
    html = world['admin'].get('/admin/integratsiyalar').get_data(as_text=True)
    assert 'Ulanmagan</span>' in html and 'b-green">Ishlayapti' not in html


def test_archive_delivery_when_configured(app, world, monkeypatch):
    import surxon.outbox as ob
    sent, rows = [], []
    monkeypatch.setattr(ob, 'telegram_upload', lambda method, fields, files: sent.append((method, fields, list(files))) or {'ok': True})
    monkeypatch.setattr(ob, 'sheets_append', lambda sheet, r: rows.append((sheet, r)))
    cfg = app.config['SURXON']
    cfg.TELEGRAM_ARCHIVE_CHAT_ID, cfg.GOOGLE_SHEETS_ID, cfg.GOOGLE_SERVICE_ACCOUNT_FILE = '-100123', 'sheet', '/x.json'
    lid, wid = chain(world)
    world['bux'].post('/tolovlar', {'amount': '1000', 'payment_date': '2026-01-01', 'waybill_id': wid, 'client_uuid': uuid4()})
    with app.test_request_context():
        res = ob.run_once()
        assert res['errors'] == 0
        methods = [m for m, _, _ in sent]
        assert methods.count('sendDocument') == 2 and methods.count('sendPhoto') == 1
        assert all(f['chat_id'] == '-100123' for _, f, _ in sent)
        assert {s for s, _ in rows} == {'Nakladnoylar', 'Tolovlar'}
        assert ob.run_once()['sent'] == 0                    # nothing sent twice
        st = {s['channel']: s['state'] for s in ob.status()}
        assert st['telegram_archive'] == 'working' and st['sheets'] == 'working' and st['offsite'] == 'not_connected'


def test_archive_errors_retry_and_show_error(app, world, monkeypatch):
    import surxon.outbox as ob

    def boom(*a, **k):
        raise RuntimeError('Forbidden: bot is not a member of the channel')
    monkeypatch.setattr(ob, 'telegram_upload', boom)
    app.config['SURXON'].TELEGRAM_ARCHIVE_CHAT_ID = '-100123'
    chain(world)
    with app.test_request_context():
        ob.run_once()
        st = {s['channel']: s for s in ob.status()}
        assert st['telegram_archive']['state'] == 'error' and 'not a member' in st['telegram_archive']['last_error']
        assert scalar("SELECT COUNT(*) FROM outbox WHERE status='pending' AND attempts=1") >= 3


def test_schema_upgrade_from_v1(tmp_path):
    """A v1 database (without the v2 tables) is upgraded in place without losing data."""
    import sqlite3
    from surxon import create_app
    from surxon.db import SCHEMA
    db = sqlite3.connect(tmp_path / 'old.sqlite3')
    v1 = SCHEMA.split('-- v2: generated documents')[0] + 'CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);'
    db.executescript(v1)
    db.execute('INSERT INTO schema_version VALUES (1)')
    db.execute("INSERT INTO brigadiers(name, created_at) VALUES ('Eski brigada', 'x')")
    db.commit(); db.close()
    app = create_app(TESTING=True, DATA_DIR=tmp_path, DB_PATH=tmp_path / 'old.sqlite3', UPLOAD_DIR=tmp_path / 'u', BACKUP_DIR=tmp_path / 'b')
    with app.app_context():
        assert scalar('SELECT version FROM schema_version') == 2
        assert scalar("SELECT COUNT(*) FROM brigadiers WHERE name='Eski brigada'") == 1
        assert scalar("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('documents','outbox','channel_status')") == 3
