"""Business rules that must stay configurable (never hard-coded)."""
from .db import get_db

DEFAULTS = {
    'company_name': ('SURXON TAXIATOSH TEXTILE', 'Kompaniya nomi (nakladnoyda chiqadi)'),
    'current_season': ('', 'Joriy mavsum yili (bo‘sh = joriy yil)'),
    'destination_name': ('Nayman paxta qabul punkti', 'Qabul qiluvchi (nakladnoyda)'),
    'price_per_kg': ('', 'Paxta narxi, so‘m/kg (bo‘sh = hali kelishilmagan)'),
    'worker_rate_hand': ('', 'Qo‘l terimi narxi, so‘m/kg. Har tortishda shu paytdagi narx saqlanadi; o‘zgartirilsa eski hisob o‘zgarmaydi (bo‘sh = hisoblanmaydi)'),
    'combine_rate_kg': ('', 'Kombayn terimi narxi, so‘m/kg (oxirgi telashkada kiritilgani — keyingisida o‘zi chiqadi)'),
    'income_sources': ('Direktor, Nayman (paxta puli), Boshqa', 'Kassa kirimi manbalari (vergul bilan)'),
    'sheets_prefix': ('SPX ', 'Google Sheets: tizim yozadigan varaqlar nomi oldidagi belgi (qo‘lda qilingan varaqlarga tegmaslik uchun)'),
    'report_time': ('21:00', 'Telegram kunlik hisobot vaqti (HH:MM); kun yopilsa, darhol yuboriladi'),
    'auto_waybill_hand': ('1', '“Tugatish” bosilganda telashka yopiladi, nakladnoy dala vazni bilan avtomatik chiqadi '
                                'va “PUNKTGA YO‘LDA” bo‘ladi (1). 0 — eski tartib: avval umumiy tarozida brutto/tara.'),
    'punkt_warn_pct': ('1', 'Punkt farqi: shu % gacha normal (yashil), sabab so‘ralmaydi'),
    'scale_adapter': ('manual', 'Punkt tarozisi ulanishi: manual = kg qo‘lda (elektron tarozi hali ulanmagan)'),
    'punkt_alert_pct': ('3', 'Punkt farqi: shu % dan katta — “Katta farq” (qizil)'),
    'diff_threshold_pct': ('2', 'Ichki hisob va tarozi farqi shu % dan oshsa sabab majburiy'),
    'nayman_diff_reason_required': ('1', 'Nayman qabulida farq bo‘lsa sabab majburiy (1/0)'),
    'toldi_min_photos': ('1', 'TOLDI uchun kamida nechta rasm majburiy'),
    'max_hand_kg': ('250', 'Bitta qo‘l terimi yozuvi uchun maksimal kg (xatoni ushlash uchun)'),
    'max_combine_kg': ('15000', 'Bitta kombayn yozuvi uchun maksimal kg'),
    'max_gross_kg': ('40000', 'Tarozi brutto uchun maksimal kg'),
    'payment_rule_enabled': ('0', 'To‘lov qoidasi yoqilganmi (1/0) — shartnoma tasdiqlangach yoqing'),
    'payment_rule_percent': ('80', 'To‘lov qoidasi: foiz'),
    'payment_rule_days': ('5', 'To‘lov qoidasi: necha kun ichida'),
    'weather_lat': ('42.3167', 'Ob-havo: kenglik (Taxiatosh)'),
    'weather_lon': ('59.6000', 'Ob-havo: uzunlik (Taxiatosh)'),
    'weather_place': ('Taxiatosh', 'Ob-havo joy nomi'),
    'map_center': ('42.3167,59.6000', 'Xarita markazi (lat,lon)'),
    'daily_target_kg': ('', 'Kunlik terim rejasi, kg (bo‘sh = reja ko‘rsatilmaydi)'),
    'report_feed': ('1', 'Hisobot kanaliga har bir muhim hodisa (reys tugadi, punkt qabul, kassa kirim/chiqim, kuzatuv '
                         'kechikishi) darhol yoziladi (1/0). Kunlik hisobot bundan tashqari keladi'),
    'kuzatuv_deadline_min': ('120', 'Kuzatuv: rasm/video so‘rovi javobi uchun necha daqiqa beriladi (keyin “KECHIKDI”)'),
    'kuzatuv_late_alert': ('1', 'Kuzatuv: javob kechiksa hisobot kanaliga yozilsin (1/0)'),
    'notify_telegram': ('1', 'Rahbar/Adminga Telegram xabarnomalar (1/0)'),
    'erp_enabled': ('0', 'Azizbek ERP ulanishi yoqilgan (1/0) — Integratsiyalar sahifasida boshqariladi'),
    'tv_enabled': ('0', 'TV ekrani ulanishi yoqilgan (1/0) — Integratsiyalar sahifasida boshqariladi'),
}


def get_setting(key, db=None):
    db = db or get_db()
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    if row is not None and row[0] is not None:
        return row[0]
    return DEFAULTS.get(key, ('', ''))[0]


def get_float(key, default=None, db=None):
    raw = (get_setting(key, db) or '').strip().replace(',', '.')
    try:
        return float(raw)
    except ValueError:
        return default


def get_bool(key, db=None):
    return (get_setting(key, db) or '0').strip() in ('1', 'true', 'yes', 'ha')


def all_settings(db=None):
    db = db or get_db()
    stored = {r['key']: r for r in db.execute('SELECT * FROM settings')}
    out = []
    for key, (default, label) in DEFAULTS.items():
        row = stored.get(key)
        out.append({'key': key, 'label': label, 'value': row['value'] if row else default,
                    'updated_at': row['updated_at'] if row else None})
    return out
