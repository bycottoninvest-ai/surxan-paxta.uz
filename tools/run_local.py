"""SURXON PAXTA — local TEST run on your own computer.

- Uses a SEPARATE test database in ./data-test (never ./data, where real accounting lives).
- Fills it with sample data on first start.
- Opens the browser and prints the address for phones on the same Wi-Fi.
- Telegram, archive channel, Google Sheets and offsite backup are forced OFF.

Run:  python tools/run_local.py        (or double-click start_test.bat / start_test.command)
Reset the test data:  python tools/run_local.py --reset
"""
import os
import secrets
import shutil
import socket
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / 'data-test'
PORT = int(os.environ.get('PORT', '5000'))


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))   # no packet is sent; just picks the Wi-Fi interface
        return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        s.close()


def main():
    if '--reset' in sys.argv and TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
        print('Test bazasi o‘chirildi (data-test). Haqiqiy data/ papkasiga tegilmadi.')
    TEST_DIR.mkdir(exist_ok=True)
    secret_file = TEST_DIR / '.secret'
    if not secret_file.exists():
        secret_file.write_text(secrets.token_urlsafe(32))
    # explicit test environment — values from .env are NOT used for these keys
    os.environ.update({
        'APP_MODE': 'test', 'DATA_DIR': str(TEST_DIR), 'SURXON_DB': str(TEST_DIR / 'surxon-test.sqlite3'),
        'UPLOAD_DIR': str(TEST_DIR / 'uploads'), 'BACKUP_DIR': str(TEST_DIR / 'backups'),
        'SECRET_KEY': secret_file.read_text().strip(), 'COOKIE_SECURE': '0',
        'ADMIN_USER': 'admin', 'ADMIN_PASSWORD': 'Test2026!', 'APP_DOMAIN': 'localhost',
        'TELEGRAM_BOT_TOKEN': '', 'TELEGRAM_WEBHOOK_SECRET': '', 'TELEGRAM_ARCHIVE_CHAT_ID': '',
        'GOOGLE_SHEETS_ID': '', 'OFFSITE_RCLONE_REMOTE': '',
    })
    sys.path.insert(0, str(ROOT))
    from surxon import create_app
    from surxon.db import get_db, scalar
    app = create_app()
    with app.app_context():
        get_db().execute("UPDATE users SET must_change_password=0 WHERE username='admin'")
        if not scalar('SELECT COUNT(*) FROM trailer_loads'):
            from surxon.demo import fill_demo
            print(fill_demo())
            get_db().execute('UPDATE users SET must_change_password=0')
    ip = lan_ip()
    print('\n' + '=' * 64)
    print('  SURXON PAXTA — TEST REJIMI (alohida sinov bazasi: data-test/)')
    print(f'  Kompyuterda:   http://localhost:{PORT}')
    print(f'  Telefonda:     http://{ip}:{PORT}   (telefon shu Wi-Fi da bo‘lsin)')
    print('  Kirish:        admin / Test2026!    ·  juma, tarozi01, buxgalter, asadbek, rahbar / Demo2026!')
    print('  TV ekran:      http://localhost:%d/tv  (admin bilan kirgandan keyin)' % PORT)
    print('  To‘xtatish:    shu oynada Ctrl+C')
    print('=' * 64 + '\n')
    if '--no-browser' not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{PORT}')).start()
    from werkzeug.serving import run_simple
    run_simple('0.0.0.0', PORT, app, threaded=True, use_reloader=False)


if __name__ == '__main__':
    main()
