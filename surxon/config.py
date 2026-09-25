"""Runtime configuration. Every secret comes from the environment, never from source."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent


def _timezone(name):
    """IANA zone; Windows has no tz database unless the 'tzdata' package is installed, so fall back to UTC+5."""
    try:
        return ZoneInfo(name)
    except Exception:
        from datetime import timedelta, timezone
        return timezone(timedelta(hours=5), 'UTC+05')


class Config:
    def __init__(self, **overrides):
        env = os.environ
        self.DEBUG = env.get('DEBUG', '0') == '1'
        self.TESTING = False
        self.DOMAIN = env.get('APP_DOMAIN', 'surxan-paxta.uz')
        self.SECRET_KEY = env.get('SECRET_KEY', '')
        self.DATA_DIR = Path(env.get('DATA_DIR', BASE_DIR / 'instance'))
        self.DB_PATH = Path(env.get('SURXON_DB', self.DATA_DIR / 'surxon.sqlite3'))
        self.UPLOAD_DIR = Path(env.get('UPLOAD_DIR', self.DATA_DIR / 'uploads'))
        self.BACKUP_DIR = Path(env.get('BACKUP_DIR', self.DATA_DIR / 'backups'))
        self.BACKUP_KEEP_DAYS = int(env.get('BACKUP_KEEP_DAYS', '30'))
        self.COOKIE_SECURE = env.get('COOKIE_SECURE', '1') == '1'
        self.ADMIN_USER = env.get('ADMIN_USER', 'admin')
        self.ADMIN_PASSWORD = env.get('ADMIN_PASSWORD', '')
        self.TELEGRAM_BOT_TOKEN = env.get('TELEGRAM_BOT_TOKEN', '').strip()
        self.TELEGRAM_WEBHOOK_SECRET = env.get('TELEGRAM_WEBHOOK_SECRET', '').strip()
        self.TELEGRAM_BOT_USERNAME = env.get('TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
        self.TZ = _timezone(env.get('APP_TZ', 'Asia/Tashkent'))
        self.MAX_UPLOAD_MB = int(env.get('MAX_UPLOAD_MB', '25'))
        # External archives — each one stays "not connected" until its settings are present.
        self.TELEGRAM_ARCHIVE_CHAT_ID = env.get('TELEGRAM_ARCHIVE_CHAT_ID', '').strip()
        # read-only reporting channel (daily report + alerts). Its own bot is optional; nobody writes to it.
        self.TELEGRAM_REPORT_CHAT_ID = env.get('TELEGRAM_REPORT_CHAT_ID', '').strip()
        self.TELEGRAM_REPORT_BOT_TOKEN = env.get('TELEGRAM_REPORT_BOT_TOKEN', '').strip()
        self.GOOGLE_SHEETS_ID = env.get('GOOGLE_SHEETS_ID', '').strip()
        self.GOOGLE_SERVICE_ACCOUNT_FILE = env.get('GOOGLE_SERVICE_ACCOUNT_FILE', '').strip()
        self.OFFSITE_RCLONE_REMOTE = env.get('OFFSITE_RCLONE_REMOTE', '').strip()
        self.APP_MODE = env.get('APP_MODE', 'production').strip().lower()
        for k, v in overrides.items():
            setattr(self, k, v)
        if self.APP_MODE == 'test':
            # A test copy must never talk to the real bot, archive channel, Sheets or offsite storage.
            self.TELEGRAM_BOT_TOKEN = self.TELEGRAM_ARCHIVE_CHAT_ID = ''
            self.TELEGRAM_REPORT_CHAT_ID = self.TELEGRAM_REPORT_BOT_TOKEN = ''
            self.GOOGLE_SHEETS_ID = self.GOOGLE_SERVICE_ACCOUNT_FILE = self.OFFSITE_RCLONE_REMOTE = ''

    def validate(self):
        if not self.SECRET_KEY:
            if self.DEBUG or self.TESTING:
                self.SECRET_KEY = 'dev-only-secret-not-for-production'
            else:
                raise RuntimeError(
                    'SECRET_KEY muhit o‘zgaruvchisi o‘rnatilmagan. .env faylida uzun tasodifiy qiymat bering.')
        for d in (self.DATA_DIR, self.UPLOAD_DIR, self.BACKUP_DIR, self.DB_PATH.parent):
            Path(d).mkdir(parents=True, exist_ok=True)
