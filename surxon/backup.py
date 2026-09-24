"""Consistent backups: SQLite online-backup API (safe while the app is running) + photo archive."""
import sqlite3
import tarfile
import time
from datetime import datetime
from pathlib import Path


def run_backup(cfg):
    cfg.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(cfg.TZ).strftime('%Y%m%d_%H%M%S')
    db_out = cfg.BACKUP_DIR / f'surxon_db_{stamp}.sqlite3'
    src = sqlite3.connect(str(cfg.DB_PATH))
    dst = sqlite3.connect(str(db_out))
    try:
        src.backup(dst)
        ok = dst.execute('PRAGMA integrity_check').fetchone()[0]
    finally:
        dst.close()
        src.close()
    if ok != 'ok':
        raise RuntimeError(f'Backup integrity check failed: {ok}')
    photos_out = cfg.BACKUP_DIR / f'surxon_photos_{stamp}.tar.gz'
    uploads = Path(cfg.UPLOAD_DIR)
    # Photos are append-only, so an incremental tar of the last 2 days plus a weekly full copy is enough.
    full = datetime.now(cfg.TZ).weekday() == 6 or not any(cfg.BACKUP_DIR.glob('surxon_photos_*_full.tar.gz'))
    if full:
        photos_out = cfg.BACKUP_DIR / f'surxon_photos_{stamp}_full.tar.gz'
    cutoff = 0 if full else time.time() - 2 * 86400
    count = 0
    with tarfile.open(photos_out, 'w:gz') as tar:
        for p in uploads.rglob('*'):
            if p.is_file() and p.stat().st_mtime >= cutoff:
                tar.add(p, arcname=str(p.relative_to(uploads)))
                count += 1
    keep_until = time.time() - cfg.BACKUP_KEEP_DAYS * 86400
    removed = 0
    for old in cfg.BACKUP_DIR.glob('surxon_*'):
        if old.stat().st_mtime < keep_until and not old.name.endswith('_full.tar.gz'):
            old.unlink()
            removed += 1
    # keep the 8 most recent full photo archives
    fulls = sorted(cfg.BACKUP_DIR.glob('surxon_photos_*_full.tar.gz'))
    for old in fulls[:-8]:
        old.unlink()
    return (f'Zaxira tayyor: {db_out.name} ({db_out.stat().st_size // 1024} KB), '
            f'{photos_out.name} ({count} ta rasm fayli{", to‘liq" if full else ""}). Eski fayllar o‘chirildi: {removed}.')


if __name__ == '__main__':
    import os
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from surxon.config import Config
    cfg = Config()
    cfg.TESTING = True  # do not require SECRET_KEY just to take a backup
    cfg.validate()
    interval = int(os.environ.get('BACKUP_INTERVAL_HOURS', '0'))
    while True:
        try:
            print(run_backup(cfg), flush=True)
        except Exception as exc:  # keep the loop alive; the next run retries
            print(f'BACKUP FAILED: {exc}', flush=True)
        if not interval:
            break
        time.sleep(interval * 3600)
