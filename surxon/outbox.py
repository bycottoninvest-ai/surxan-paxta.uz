"""Reliable delivery to external archives.

Business operations call ``enqueue()`` inside their own transaction, so a job
exists if and only if the record exists. A worker (``python -m surxon.outbox``
in docker-compose, or ``flask outbox-run``) sends pending jobs with retries.

Channels
- telegram_archive: private Telegram channel; receives waybill PDFs and trip photos.
- sheets:           Google Sheets mirror. Rows with an operation id (INC-/EXP-/PAY-…) are upserted on that id,
                    so a retried job never adds a duplicate row. The database stays the source of truth.
- telegram_report:  read-only Telegram channel: daily report, alerts and the event feed (nobody writes to it).
                    Kuzatuv photos/videos go to telegram_archive.
A channel whose settings are missing is reported as NOT CONNECTED; its jobs stay
pending and are delivered once it is configured — nothing is silently dropped.
"""
import json
import secrets
import time
import urllib.request

from flask import current_app

from .db import get_db, tx
from .utils import now_str

CHANNELS = {
    'telegram_archive': 'Telegram arxiv kanali',
    'sheets': 'Google Sheets nazorat nusxasi',
    'telegram_report': 'Telegram hisobot kanali (faqat o‘qish)',
    'offsite': 'Mustaqil (serverdan tashqari) zaxira',
}
MAX_ATTEMPTS = 12


def enqueue(db, channel, kind, ref, payload):
    """Add a delivery job (idempotent per channel+kind+ref). Must run inside the caller's transaction."""
    db.execute('INSERT OR IGNORE INTO outbox(channel, kind, ref, payload_json, created_at) VALUES (?,?,?,?,?)',
               (channel, kind, str(ref), json.dumps(payload, ensure_ascii=False, default=str), now_str()))


def chat_id(channel, cfg=None):
    """Telegram channel id: the one the admin picked on the Integrations page (bot is admin there), else .env."""
    cfg = cfg or current_app.config['SURXON']
    key = {'telegram_archive': 'tg_archive_chat_id', 'telegram_report': 'tg_report_chat_id'}[channel]
    try:
        from .settings import get_setting
        val = (get_setting(key) or '').strip()
    except Exception:
        val = ''
    return val or (cfg.TELEGRAM_ARCHIVE_CHAT_ID if channel == 'telegram_archive' else cfg.TELEGRAM_REPORT_CHAT_ID)


def configured(channel, cfg=None):
    cfg = cfg or current_app.config['SURXON']
    if channel == 'telegram_archive':
        return bool(cfg.TELEGRAM_BOT_TOKEN and chat_id('telegram_archive', cfg))
    if channel == 'sheets':
        return bool(cfg.GOOGLE_SHEETS_ID and cfg.GOOGLE_SERVICE_ACCOUNT_FILE)
    if channel == 'telegram_report':
        return bool((cfg.TELEGRAM_REPORT_BOT_TOKEN or cfg.TELEGRAM_BOT_TOKEN) and chat_id('telegram_report', cfg))
    if channel == 'offsite':
        return bool(cfg.OFFSITE_RCLONE_REMOTE)
    return False


def record_result(channel, ok, error=None, db=None):
    db = db or get_db()
    db.execute('INSERT OR IGNORE INTO channel_status(channel) VALUES (?)', (channel,))
    if ok:
        db.execute('UPDATE channel_status SET last_ok_at=?, ok_count=ok_count+1 WHERE channel=?', (now_str(), channel))
    else:
        db.execute('UPDATE channel_status SET last_error_at=?, last_error=?, error_count=error_count+1 WHERE channel=?',
                   (now_str(), str(error)[:500], channel))


def status():
    """Honest state per channel: not_connected / configured_untested / working / error."""
    db = get_db()
    out = []
    for ch, label in CHANNELS.items():
        row = db.execute('SELECT * FROM channel_status WHERE channel=?', (ch,)).fetchone()
        counts = {r['status']: r['n'] for r in db.execute(
            'SELECT status, COUNT(*) n FROM outbox WHERE channel=? GROUP BY status', (ch,))}
        conf = configured(ch)
        if not conf:
            state = 'not_connected'
        elif row and row['last_ok_at'] and (not row['last_error_at'] or row['last_ok_at'] >= row['last_error_at']):
            state = 'working'
        elif row and row['last_error_at']:
            state = 'error'
        else:
            state = 'configured_untested'
        out.append({'channel': ch, 'label': label, 'configured': conf, 'state': state,
                    'last_ok_at': row['last_ok_at'] if row else None, 'last_error_at': row['last_error_at'] if row else None,
                    'last_error': row['last_error'] if row else None, 'pending': counts.get('pending', 0),
                    'sent': counts.get('sent', 0), 'failed': counts.get('failed', 0)})
    return out


# ------------------------------------------------------------------ senders

def _multipart(fields, files):
    boundary = secrets.token_hex(16)
    parts = []
    for k, v in fields.items():
        parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode(), str(v).encode(), b'\r\n']
    for k, (name, data, ctype) in files.items():
        parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                  f'Content-Type: {ctype}\r\n\r\n'.encode(), data, b'\r\n']
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


def telegram_upload(method, fields, files, token=None):
    cfg = current_app.config['SURXON']
    body, ctype = _multipart(fields, files)
    req = urllib.request.Request(f'https://api.telegram.org/bot{token or cfg.TELEGRAM_BOT_TOKEN}/{method}', data=body,
                                 headers={'Content-Type': ctype})
    with urllib.request.urlopen(req, timeout=60) as resp:
        res = json.loads(resp.read().decode())
    if not res.get('ok'):
        raise RuntimeError(res.get('description') or 'Telegram xatosi')
    return res


def _send_telegram(job, payload):
    cfg = current_app.config['SURXON']
    if job['kind'] == 'copy':
        # a kuzatuv video over the 20 MB download limit: Telegram copies it server-side into the archive channel
        telegram_upload('copyMessage', {'chat_id': chat_id('telegram_archive'), 'from_chat_id': payload['from_chat_id'],
                                        'message_id': payload['message_id'],
                                        'caption': payload.get('caption', '')[:1000]}, {})
        return
    path = cfg.UPLOAD_DIR / payload['path']
    data = path.read_bytes()
    fields = {'chat_id': chat_id('telegram_archive'), 'caption': payload.get('caption', '')[:1000]}
    if job['kind'] == 'document':
        telegram_upload('sendDocument', fields, {'document': (payload.get('filename', path.name), data, 'application/pdf')})
    elif job['kind'] == 'photo':
        telegram_upload('sendPhoto', fields, {'photo': (path.name, data, 'image/jpeg')})
    elif job['kind'] == 'video':
        telegram_upload('sendVideo', fields, {'video': (path.name, data, 'video/mp4')})
    else:
        raise RuntimeError(f'Noma’lum tur: {job["kind"]}')


def _send_report(job, payload):
    cfg = current_app.config['SURXON']
    telegram_upload('sendMessage', {'chat_id': chat_id('telegram_report'), 'text': payload['text'][:4000]}, {},
                    token=cfg.TELEGRAM_REPORT_BOT_TOKEN or cfg.TELEGRAM_BOT_TOKEN)


_sheets_session = None


def _session():
    global _sheets_session
    cfg = current_app.config['SURXON']
    if _sheets_session is None:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            cfg.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        _sheets_session = AuthorizedSession(creds)
    return _sheets_session


def _values_url(sheet, rng):
    cfg = current_app.config['SURXON']
    return (f'https://sheets.googleapis.com/v4/spreadsheets/{cfg.GOOGLE_SHEETS_ID}/values/'
            f'{urllib.request.quote(f"{chr(39)}{sheet}{chr(39)}!{rng}")}')


def tab_name(sheet):
    """System tabs carry a prefix so the sync never writes into a tab people built by hand (with their formulas)."""
    from .settings import get_setting
    prefix = get_setting('sheets_prefix')
    return f'{prefix}{sheet}' if prefix is not None else sheet


def _ensure_tab(sheet, header):
    """Create the tab (with its header row) the first time it is used. If the tab already exists with a different
    header, nothing is written — it belongs to someone else and may hold formulas."""
    cfg = current_app.config['SURXON']
    sess = _session()
    r = sess.get(_values_url(sheet, '1:1'), timeout=30)
    if r.status_code == 400 and 'Unable to parse range' in r.text:
        rr = sess.post(f'https://sheets.googleapis.com/v4/spreadsheets/{cfg.GOOGLE_SHEETS_ID}:batchUpdate',
                       json={'requests': [{'addSheet': {'properties': {'title': sheet}}}]}, timeout=30)
        if rr.status_code >= 300:
            raise RuntimeError(f'Sheets {rr.status_code}: {rr.text[:300]}')
        r = sess.get(_values_url(sheet, 'A1:A1'), timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f'Sheets {r.status_code}: {r.text[:300]}')
    existing = (r.json().get('values') or [[]])[0]
    if header and existing and [str(x).strip() for x in existing[:len(header)]] != [str(x) for x in header]:
        raise RuntimeError(f'“{sheet}” varag‘ida boshqa sarlavhalar bor — formulalar buzilmasligi uchun yozilmadi. '
                           f'Sozlamada sheets_prefix ni o‘zgartiring yoki varaqni qayta nomlang.')
    if header and not existing:
        w = sess.put(_values_url(sheet, 'A1') + '?valueInputOption=RAW', json={'values': [header]}, timeout=30)
        if w.status_code >= 300:
            raise RuntimeError(f'Sheets {w.status_code}: {w.text[:300]}')


def sheets_upsert(sheet, row, header=None):
    """Row whose first cell is a unique id: update that row if the id is already there, else append it.
    Only the columns of `row` are written; cells to the right (e.g. people's own formulas) are never touched."""
    sheet = tab_name(sheet)
    _ensure_tab(sheet, header)
    sess = _session()
    r = sess.get(_values_url(sheet, 'A:A'), timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f'Sheets {r.status_code}: {r.text[:300]}')
    ids = [v[0] if v else '' for v in r.json().get('values', [])]
    if row[0] in ids:
        n = ids.index(row[0]) + 1
        w = sess.put(_values_url(sheet, f'A{n}') + '?valueInputOption=RAW', json={'values': [row]}, timeout=30)
        if w.status_code >= 300:
            raise RuntimeError(f'Sheets {w.status_code}: {w.text[:300]}')
        return 'updated'
    sheets_append(sheet, [row])
    return 'appended'


def sheets_upsert_many(sheet, rows, header=None):
    """Many id-keyed rows in 3 API calls (read ids, update existing, append new) — used for full refreshes."""
    cfg = current_app.config['SURXON']
    sheet = tab_name(sheet)
    _ensure_tab(sheet, header)
    sess = _session()
    r = sess.get(_values_url(sheet, 'A:A'), timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f'Sheets {r.status_code}: {r.text[:300]}')
    ids = [v[0] if v else '' for v in r.json().get('values', [])]
    updates, new = [], []
    for row in rows:
        if row[0] in ids:
            updates.append({'range': f"'{sheet}'!A{ids.index(row[0]) + 1}", 'values': [row]})
        else:
            new.append(row)
            ids.append(row[0])
    if updates:
        w = sess.post(f'https://sheets.googleapis.com/v4/spreadsheets/{cfg.GOOGLE_SHEETS_ID}/values:batchUpdate',
                      json={'valueInputOption': 'RAW', 'data': updates}, timeout=60)
        if w.status_code >= 300:
            raise RuntimeError(f'Sheets {w.status_code}: {w.text[:300]}')
    if new:
        sheets_append(sheet, new)
    return {'updated': len(updates), 'appended': len(new)}


def sheets_read(sheet, rng='A1:Z2000'):
    r = _session().get(_values_url(sheet, rng), timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f'Sheets {r.status_code}: {r.text[:300]}')
    return r.json().get('values', [])


def sheets_inspect():
    """Read-only look at the spreadsheet: every tab, its header row, size and how many cells hold formulas."""
    cfg = current_app.config['SURXON']
    sess = _session()
    r = sess.get(f'https://sheets.googleapis.com/v4/spreadsheets/{cfg.GOOGLE_SHEETS_ID}'
                 '?fields=properties.title,sheets.properties(title,gridProperties)', timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f'Sheets {r.status_code}: {r.text[:300]}')
    meta = r.json()
    out = {'title': meta['properties']['title'], 'tabs': []}
    for sh in meta.get('sheets', []):
        t = sh['properties']['title']
        g = sh['properties'].get('gridProperties', {})
        v = sess.get(_values_url(t, 'A1:ZZ300') + '?valueRenderOption=FORMULA', timeout=30)
        vals = v.json().get('values', []) if v.status_code < 300 else []
        formulas = [(ri + 1, ci + 1, c) for ri, row in enumerate(vals) for ci, c in enumerate(row)
                    if isinstance(c, str) and c.startswith('=')]
        out['tabs'].append({'title': t, 'rows': g.get('rowCount'), 'cols': g.get('columnCount'),
                            'header': vals[0] if vals else [], 'filled_rows': len(vals), 'formula_cells': len(formulas),
                            'formula_samples': formulas[:5]})
    return out


def sheets_append(sheet, rows):
    """Append rows to a Google Sheet tab using a service account (scope: spreadsheets)."""
    url = _values_url(sheet, 'A1') + ':append?valueInputOption=RAW&insertDataOption=INSERT_ROWS'
    r = _session().post(url, json={'values': rows}, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f'Sheets {r.status_code}: {r.text[:300]}')


def _send_sheets(job, payload):
    if job['kind'] == 'upsert_many':
        sheets_upsert_many(payload['sheet'], payload['rows'], payload.get('header'))
    elif payload.get('id'):
        sheets_upsert(payload['sheet'], payload['row'], payload.get('header'))
    else:
        sheets_append(tab_name(payload['sheet']), [payload['row']])


SENDERS = {'telegram_archive': _send_telegram, 'sheets': _send_sheets, 'telegram_report': _send_report}


def run_once(limit=50):
    """Send due jobs for configured channels. Returns {'sent': n, 'errors': n, 'skipped_channels': [...]}."""
    from .services import ensure_missing_documents
    ensure_missing_documents()
    try:
        from .reporting import maybe_refresh_sheet_summary
        maybe_refresh_sheet_summary()
    except Exception as exc:
        print(f'sheet summary refresh failed: {exc}', flush=True)
    try:
        from .kuzatuv import tick
        tick()
    except Exception as exc:  # kuzatuv schedule/delivery problems never block the archive deliveries
        print(f'kuzatuv tick failed: {exc}', flush=True)
    try:
        from .reporting import maybe_schedule_daily_report
        maybe_schedule_daily_report()
    except Exception as exc:  # never block deliveries because of the report scheduler
        print(f'daily report scheduling failed: {exc}', flush=True)
    db = get_db()
    sent = errors = 0
    skipped = [ch for ch in SENDERS if not configured(ch)]
    jobs = db.execute(f'''SELECT * FROM outbox WHERE status='pending' AND next_try_at <= ?
                          AND channel IN ({','.join('?' * len(SENDERS))}) ORDER BY id LIMIT ?''',
                      (time.time(), *SENDERS.keys(), limit)).fetchall()
    for job in jobs:
        if job['channel'] in skipped:
            continue
        try:
            SENDERS[job['channel']](job, json.loads(job['payload_json']))
        except Exception as exc:  # network, auth, missing file — retried with backoff
            errors += 1
            attempts = job['attempts'] + 1
            with tx(db):
                db.execute('UPDATE outbox SET attempts=?, last_error=?, next_try_at=?, status=? WHERE id=?',
                           (attempts, str(exc)[:500], time.time() + min(3600, 30 * 2 ** attempts),
                            'failed' if attempts >= MAX_ATTEMPTS else 'pending', job['id']))
                record_result(job['channel'], False, exc, db)
            continue
        sent += 1
        with tx(db):
            db.execute("UPDATE outbox SET status='sent', sent_at=?, attempts=attempts+1, last_error=NULL WHERE id=?",
                       (now_str(), job['id']))
            record_result(job['channel'], True, db=db)
    return {'sent': sent, 'errors': errors, 'skipped_channels': skipped}


def retry_failed():
    with tx() as db:
        n = db.execute("UPDATE outbox SET status='pending', attempts=0, next_try_at=0 WHERE status='failed'").rowcount
    return n


def send_test(channel):
    """Real round-trip test used by the admin page and `flask smoke-check`."""
    if not configured(channel):
        raise RuntimeError('Sozlanmagan')
    cfg = current_app.config['SURXON']
    try:
        if channel == 'telegram_archive':
            telegram_upload('sendMessage', {'chat_id': chat_id('telegram_archive'),
                                            'text': f'✅ SURXON PAXTA: arxiv kanali sinovi · {now_str()}'}, {})
        elif channel == 'sheets':
            sheets_upsert('Sinov', ['TEST-1', now_str(), 'SURXAN-PAXTA.UZ ulanish sinovi'], ['ID', 'Vaqt', 'Matn'])
        elif channel == 'telegram_report':
            telegram_upload('sendMessage', {'chat_id': chat_id('telegram_report'),
                                            'text': f'✅ SURXAN-PAXTA.UZ hisobot kanali sinovi · {now_str()}'}, {},
                            token=cfg.TELEGRAM_REPORT_BOT_TOKEN or cfg.TELEGRAM_BOT_TOKEN)
        elif channel == 'offsite':
            from .backup import offsite_copy
            offsite_copy(cfg, test=True)
        with tx() as db:
            record_result(channel, True, db=db)
    except Exception as exc:
        with tx() as db:
            record_result(channel, False, exc, db)
        raise


if __name__ == '__main__':
    import os
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from surxon import create_app
    app = create_app()
    interval = int(os.environ.get('OUTBOX_INTERVAL_SECONDS', '30'))
    while True:
        with app.app_context():
            try:
                res = run_once()
                if res['sent'] or res['errors']:
                    print(f'{now_str()} outbox: {res}', flush=True)
            except Exception as exc:
                print(f'OUTBOX FAILED: {exc}', flush=True)
        time.sleep(interval)
