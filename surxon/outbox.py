"""Reliable delivery to external archives.

Business operations call ``enqueue()`` inside their own transaction, so a job
exists if and only if the record exists. A worker (``python -m surxon.outbox``
in docker-compose, or ``flask outbox-run``) sends pending jobs with retries.

Channels
- telegram_archive: private Telegram channel; receives waybill PDFs and trip photos.
- sheets:           Google Sheets control copy (append-only rows).
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
    'offsite': 'Mustaqil (serverdan tashqari) zaxira',
}
MAX_ATTEMPTS = 12


def enqueue(db, channel, kind, ref, payload):
    """Add a delivery job (idempotent per channel+kind+ref). Must run inside the caller's transaction."""
    db.execute('INSERT OR IGNORE INTO outbox(channel, kind, ref, payload_json, created_at) VALUES (?,?,?,?,?)',
               (channel, kind, str(ref), json.dumps(payload, ensure_ascii=False, default=str), now_str()))


def configured(channel, cfg=None):
    cfg = cfg or current_app.config['SURXON']
    if channel == 'telegram_archive':
        return bool(cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_ARCHIVE_CHAT_ID)
    if channel == 'sheets':
        return bool(cfg.GOOGLE_SHEETS_ID and cfg.GOOGLE_SERVICE_ACCOUNT_FILE)
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


def telegram_upload(method, fields, files):
    cfg = current_app.config['SURXON']
    body, ctype = _multipart(fields, files)
    req = urllib.request.Request(f'https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/{method}', data=body,
                                 headers={'Content-Type': ctype})
    with urllib.request.urlopen(req, timeout=60) as resp:
        res = json.loads(resp.read().decode())
    if not res.get('ok'):
        raise RuntimeError(res.get('description') or 'Telegram xatosi')
    return res


def _send_telegram(job, payload):
    cfg = current_app.config['SURXON']
    path = cfg.UPLOAD_DIR / payload['path']
    data = path.read_bytes()
    fields = {'chat_id': cfg.TELEGRAM_ARCHIVE_CHAT_ID, 'caption': payload.get('caption', '')[:1000]}
    if job['kind'] == 'document':
        telegram_upload('sendDocument', fields, {'document': (payload.get('filename', path.name), data, 'application/pdf')})
    elif job['kind'] == 'photo':
        telegram_upload('sendPhoto', fields, {'photo': (path.name, data, 'image/jpeg')})
    else:
        raise RuntimeError(f'Noma’lum tur: {job["kind"]}')


_sheets_session = None


def sheets_append(sheet, rows):
    """Append rows to a Google Sheet tab using a service account (scope: spreadsheets)."""
    global _sheets_session
    cfg = current_app.config['SURXON']
    if _sheets_session is None:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            cfg.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        _sheets_session = AuthorizedSession(creds)
    url = (f'https://sheets.googleapis.com/v4/spreadsheets/{cfg.GOOGLE_SHEETS_ID}/values/'
           f'{urllib.request.quote(sheet)}!A1:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS')
    r = _sheets_session.post(url, json={'values': rows}, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f'Sheets {r.status_code}: {r.text[:300]}')


def _send_sheets(job, payload):
    sheets_append(payload['sheet'], [payload['row']])


SENDERS = {'telegram_archive': _send_telegram, 'sheets': _send_sheets}


def run_once(limit=50):
    """Send due jobs for configured channels. Returns {'sent': n, 'errors': n, 'skipped_channels': [...]}."""
    from .services import ensure_missing_documents
    ensure_missing_documents()
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
            telegram_upload('sendMessage', {'chat_id': cfg.TELEGRAM_ARCHIVE_CHAT_ID,
                                            'text': f'✅ SURXON PAXTA: arxiv kanali sinovi · {now_str()}'}, {})
        elif channel == 'sheets':
            sheets_append('Sinov', [[now_str(), 'SURXON PAXTA ulanish sinovi']])
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
