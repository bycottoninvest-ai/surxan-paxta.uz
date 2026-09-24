"""Small shared helpers: clock, parsing and formatting."""
import re
import uuid
from datetime import datetime, date, timedelta

from flask import current_app


class UserError(ValueError):
    """A validation problem the operator can fix. Shown to them verbatim."""


def tz():
    return current_app.config['SURXON'].TZ


def now():
    return datetime.now(tz())


def now_str():
    return now().strftime('%Y-%m-%d %H:%M:%S')


def today_str():
    return now().strftime('%Y-%m-%d')


def parse_date(value, label='Sana', default=None):
    value = (value or '').strip()
    if not value:
        if default is not None:
            return default
        raise UserError(f'{label} kiritilishi shart.')
    try:
        d = datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        raise UserError(f'{label} noto‘g‘ri formatda (YYYY-MM-DD).')
    if d > now().date() + timedelta(days=1):
        raise UserError(f'{label} kelajakdagi sana bo‘lishi mumkin emas.')
    if d.year < 2020:
        raise UserError(f'{label} juda eski.')
    return d.isoformat()


_NUM_CLEAN = re.compile(r'[\s  _]')


def parse_number(value, label, *, min_value=None, max_value=None, allow_zero=False, required=True):
    raw = _NUM_CLEAN.sub('', str(value if value is not None else '')).replace(',', '.')
    if raw == '':
        if not required:
            return None
        raise UserError(f'{label} kiritilishi shart.')
    try:
        num = float(raw)
    except ValueError:
        raise UserError(f'{label}: son kiriting.')
    if num != num or num in (float('inf'), float('-inf')):
        raise UserError(f'{label}: noto‘g‘ri son.')
    if num < 0 or (num == 0 and not allow_zero):
        raise UserError(f'{label} 0 dan katta bo‘lishi kerak.')
    if min_value is not None and num < min_value:
        raise UserError(f'{label} kamida {fmt_num(min_value)} bo‘lishi kerak.')
    if max_value is not None and num > max_value:
        raise UserError(f'{label} {fmt_num(max_value)} dan oshmasligi kerak. Raqamni tekshiring.')
    return round(num, 1)


def parse_money(value, label='Summa', required=True, max_value=100_000_000_000):
    num = parse_number(value, label, max_value=max_value, required=required)
    return None if num is None else int(round(num))


def parse_int(value, label, required=True):
    raw = str(value or '').strip()
    if not raw:
        if required:
            raise UserError(f'{label} tanlanishi shart.')
        return None
    try:
        return int(raw)
    except ValueError:
        raise UserError(f'{label} noto‘g‘ri.')


def clean_text(value, max_len=500):
    text = (value or '').strip()
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    return text[:max_len]


def name_key(name):
    """Normalise a person's name for search/duplicate detection."""
    s = (name or '').lower()
    for a, b in (('‘', "'"), ('’', "'"), ('`', "'"), ('ʻ', "'"), ('ʼ', "'"), ('o\'', 'o'), ('g\'', 'g')):
        s = s.replace(a, b)
    s = re.sub(r'[^a-z0-9а-яёўқғҳ ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def valid_uuid(value):
    value = (value or '').strip()
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def fmt_num(value, digits=0):
    if value is None:
        return '—'
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    s = f'{v:,.{digits}f}'.replace(',', ' ')
    return s


def fmt_money(value):
    if value is None:
        return '—'
    return fmt_num(value) + ' so‘m'


WEEKDAYS = ['Dushanba', 'Seshanba', 'Chorshanba', 'Payshanba', 'Juma', 'Shanba', 'Yakshanba']


def weekday_name(iso):
    try:
        return WEEKDAYS[date.fromisoformat(iso[:10]).weekday()]
    except ValueError:
        return ''


def fmt_date(iso):
    if not iso:
        return '—'
    s = str(iso)
    if len(s) >= 10 and s[4] == '-':
        return f'{s[8:10]}.{s[5:7]}.{s[0:4]}' + (s[10:16] if len(s) > 10 else '')
    return s
