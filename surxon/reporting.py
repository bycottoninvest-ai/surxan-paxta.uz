"""Finance summaries (day / week / month / season / custom), the daily Telegram report and alerts, report PDF.

Figures always come from the database (harvest rows with their frozen rates, punkt receipts, cash entries).
Telegram and Google Sheets only receive copies; losing a message never loses data.
"""
import io
from datetime import date, timedelta

from flask import current_app

from .db import get_db, q, scalar
from .outbox import enqueue
from .settings import get_setting
from .utils import now_str, today_str

PERIODS = [('bugun', 'Bugun'), ('kecha', 'Kecha'), ('hafta', 'Hafta'), ('oy', 'Oy'), ('mavsum', 'Mavsum'),
           ('custom', 'Tanlangan')]


def period_range(period, date_from=None, date_to=None, year=None):
    """(from, to, label) for a named period; 'custom' uses the given dates."""
    t = date.fromisoformat(today_str())
    if period == 'kecha':
        d = t - timedelta(days=1)
        return d.isoformat(), d.isoformat(), 'Kecha'
    if period == 'hafta':
        return (t - timedelta(days=6)).isoformat(), t.isoformat(), 'Oxirgi 7 kun'
    if period == 'oy':
        return t.replace(day=1).isoformat(), t.isoformat(), t.strftime('%m.%Y')
    if period == 'mavsum':
        y = year or t.year
        return f'{y}-01-01', f'{y}-12-31', f'{y} mavsumi'
    if period == 'custom' and date_from and date_to:
        a, b = sorted([date_from, date_to])
        return a, b, f'{a[8:10]}.{a[5:7]}.{a[:4]} – {b[8:10]}.{b[5:7]}.{b[:4]}'
    return t.isoformat(), t.isoformat(), 'Bugun'


def finance_summary(date_from, date_to, cashbox_id=None):
    from .accounting import box_balance, cotton_totals
    db = get_db()
    year = int(date_from[:4])
    cot = cotton_totals(year, date_from, date_to)
    hand = q('''SELECT COALESCE(SUM(amount),0) amt, COALESCE(SUM(CASE WHEN amount IS NULL THEN kg END),0) uncalc
                FROM harvests WHERE method='hand' AND voided_at IS NULL AND work_date BETWEEN ? AND ?''',
             (date_from, date_to), one=True)
    comb = (scalar('''SELECT COALESCE(SUM(amount),0) FROM harvests WHERE method='combine' AND voided_at IS NULL
                      AND work_date BETWEEN ? AND ?''', (date_from, date_to))
            + scalar('''SELECT COALESCE(SUM(amount),0) FROM combine_work WHERE voided_at IS NULL AND work_date BETWEEN ? AND ?''',
                     (date_from, date_to)))
    exp = q('''SELECT category, SUM(amount) amount, COUNT(*) n FROM expenses WHERE voided_at IS NULL
               AND expense_date BETWEEN ? AND ? GROUP BY category ORDER BY amount DESC''', (date_from, date_to))
    box_where, box_p = ('AND cashbox_id=?', [cashbox_id]) if cashbox_id else ('', [])
    cash = q(f'''SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount END),0) i,
                        COALESCE(SUM(CASE WHEN direction='OUT' THEN amount END),0) o,
                        COALESCE(SUM(CASE WHEN category='worker_pay' THEN amount END),0) wages,
                        COALESCE(SUM(CASE WHEN category='advance' THEN amount END),0) advances,
                        COALESCE(SUM(CASE WHEN category='combine_pay' THEN amount END),0) combine_paid
                 FROM cash_entries WHERE voided_at IS NULL AND entry_date BETWEEN ? AND ? {box_where}''',
             [date_from, date_to] + box_p, one=True)
    boxes = [cashbox_id] if cashbox_id else [r['id'] for r in db.execute('SELECT id FROM cashboxes').fetchall()]
    end_balance = sum(box_balance(db, b, upto_day=date_to) for b in boxes)
    unchecked = scalar("SELECT COUNT(*) FROM expenses WHERE status='TEKSHIRILMAGAN' AND voided_at IS NULL "
                       "AND expense_date BETWEEN ? AND ?", (date_from, date_to))
    closes = q('''SELECT cd.*, cb.name box FROM cash_days cd JOIN cashboxes cb ON cb.id=cd.cashbox_id
                  WHERE cd.day BETWEEN ? AND ? ORDER BY cd.day''', (date_from, date_to))
    return {'from': date_from, 'to': date_to, 'cotton': cot, 'wages_earned': hand['amt'], 'wages_uncalc_kg': hand['uncalc'],
            'combine_earned': comb, 'expenses': [dict(r) for r in exp], 'expenses_total': sum(r['amount'] for r in exp),
            'cash_in': cash['i'], 'cash_out': cash['o'], 'wages_paid': cash['wages'], 'advances': cash['advances'],
            'combine_paid': cash['combine_paid'], 'cash_balance': end_balance, 'unchecked_expenses': unchecked,
            'day_closes': [dict(r) for r in closes]}


def _n(v):
    return f'{int(round(v)):,}'.replace(',', ' ')


def report_text(day):
    s = finance_summary(day, day)
    c = s['cotton']
    lines = [f'SURXAN-PAXTA.UZ · {day[8:10]}.{day[5:7]}.{day[:4]}', 'KUNLIK HISOBOT', '',
             f'Terildi: {_n(c["field_kg"])} kg',
             f'Punkt qabul: {_n(c["punkt_kg"])} kg ({c["received"]} ta telashka)',
             f'Farq: {_n(c["diff_kg"])} kg' + (f' ({c["diff_pct"]:+.2f}%)' if c['diff_pct'] is not None else ''), '',
             f'Terim puli: {_n(s["wages_earned"])} so‘m' + (f' (+{_n(s["wages_uncalc_kg"])} kg narxsiz)' if s['wages_uncalc_kg'] else ''),
             f'Kombayn: {_n(s["combine_earned"])} so‘m',
             f'Xarajat: {_n(s["expenses_total"])} so‘m']
    for e in s['expenses'][:6]:
        lines.append(f'  · {e["category"]}: {_n(e["amount"])}')
    lines += ['', f'Kassa kirim: {_n(s["cash_in"])} so‘m', f'Kassa chiqim: {_n(s["cash_out"])} so‘m',
              f'Kassa qoldiq: {_n(s["cash_balance"])} so‘m']
    warn = []
    from .settings import get_float
    alert_pct = get_float('punkt_alert_pct', 3) or 3
    big = [r for r in q('''SELECT tl.trip_no, wb.net_kg, nr.accepted_kg FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                           JOIN nayman_receipts nr ON nr.waybill_id=wb.id WHERE substr(nr.created_at,1,10)=?''', (day,))
           if r['net_kg'] and abs(r['accepted_kg'] - r['net_kg']) / r['net_kg'] * 100 > alert_pct]
    for r in big:
        warn.append(f'🔴 Katta kg farqi: {r["trip_no"]} {_n(r["accepted_kg"] - r["net_kg"])} kg')
    for d in s['day_closes']:
        if d['diff']:
            warn.append(f'🔴 Kassa farqi ({d["box"]}): {_n(d["diff"])} so‘m')
    if s['unchecked_expenses']:
        warn.append(f'🟡 Tasdiqlanmagan xarajat: {s["unchecked_expenses"]} ta')
    if warn:
        lines += ['', 'Ogohlantirishlar:'] + warn
    lines += ['', 'Asl ma’lumot serverda. Bu kanalga yozilmaydi.']
    return '\n'.join(lines)


def enqueue_daily_report(db, day):
    """Once per day (the outbox key is the date): at day close or at the scheduled time, whichever comes first."""
    enqueue(db, 'telegram_report', 'daily', f'daily:{day}', {'text': report_text(day)})


def enqueue_alert(db, key, text):
    enqueue(db, 'telegram_report', 'alert', f'alert:{key}', {'text': f'SURXAN-PAXTA.UZ\n{text}'})


def maybe_schedule_daily_report():
    """Called by the outbox worker loop: after 'report_time' (default 21:00) queue today's report once."""
    from .db import tx
    at = (get_setting('report_time') or '21:00').strip()
    if now_str()[11:16] < at:
        return False
    day = today_str()
    db = get_db()
    if db.execute("SELECT 1 FROM outbox WHERE channel='telegram_report' AND kind='daily' AND ref=?", (f'daily:{day}',)).fetchone():
        return False
    with tx(db):
        enqueue_daily_report(db, day)
    return True


def summary_pdf(s, title, company):
    """Server-side PDF of a finance summary (same numbers as the screen and Excel)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from pathlib import Path
    from .pdfdoc import _fonts
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(title)
    W, H = A4
    x0, x1 = 20 * mm, W - 20 * mm
    y = H - 16 * mm
    logo = Path(current_app.static_folder) / 'img' / 'logo-dark.png'
    if logo.exists():
        c.drawImage(str(logo), x0, y - 16 * mm, width=50 * mm, height=16 * mm, mask='auto', preserveAspectRatio=True)
    c.setFont('DejaVu-Bold', 14)
    c.drawRightString(x1, y - 6 * mm, title)
    c.setFont('DejaVu', 9)
    c.drawRightString(x1, y - 12 * mm, f'{company} · yaratildi {now_str()[:16]}')
    y -= 26 * mm
    cot = s['cotton']

    def row(k, v, bold=False, shade=False):
        nonlocal y
        if y < 20 * mm:
            c.showPage()
            y = H - 20 * mm
        if shade:
            c.setFillColor(colors.HexColor('#eef4fb'))
            c.rect(x0, y - 7 * mm, x1 - x0, 7 * mm, stroke=0, fill=1)
            c.setFillColor(colors.black)
        c.setFont('DejaVu-Bold' if bold else 'DejaVu', 10.5)
        c.drawString(x0 + 2 * mm, y - 5 * mm, k)
        c.drawRightString(x1 - 2 * mm, y - 5 * mm, v)
        c.setStrokeColor(colors.HexColor('#d8e1ec'))
        c.line(x0, y - 7 * mm, x1, y - 7 * mm)
        y -= 7 * mm

    def head(t):
        nonlocal y
        y -= 3 * mm
        c.setFont('DejaVu-Bold', 11.5)
        c.drawString(x0, y - 5 * mm, t)
        y -= 7 * mm

    head('Paxta')
    row('Terilgan (dala)', f'{_n(cot["field_kg"])} kg', True)
    row('Punkt qabul', f'{_n(cot["punkt_kg"])} kg ({cot["received"]} ta telashka)')
    row('Farq (qabul qilingan telashkalar)', f'{_n(cot["diff_kg"])} kg' +
        (f' ({cot["diff_pct"]:+.2f}%)' if cot['diff_pct'] is not None else ''))
    head('Hisoblangan')
    row('Terimchilar puli', f'{_n(s["wages_earned"])} so‘m', True)
    if s['wages_uncalc_kg']:
        row('Narxsiz (hisoblanmagan) terim', f'{_n(s["wages_uncalc_kg"])} kg')
    row('Kombayn', f'{_n(s["combine_earned"])} so‘m')
    head('Xarajatlar')
    for e in s['expenses']:
        row(e['category'], f'{_n(e["amount"])} so‘m')
    row('JAMI XARAJAT', f'{_n(s["expenses_total"])} so‘m', True, True)
    head('Kassa')
    row('Kassa kirim', f'{_n(s["cash_in"])} so‘m')
    row('Kassa chiqim', f'{_n(s["cash_out"])} so‘m')
    row('  shundan ish haqi / avans / kombayn', f'{_n(s["wages_paid"])} / {_n(s["advances"])} / {_n(s["combine_paid"])}')
    row('KASSA QOLDIQ (davr oxiri)', f'{_n(s["cash_balance"])} so‘m', True, True)
    if s['day_closes']:
        head('Kun yopilishlari')
        for d in s['day_closes']:
            row(f'{d["day"]} · {d["box"]}', f'tizim {_n(d["system_balance"])} · real {_n(d["counted"])} · farq {_n(d["diff"])}')
    c.showPage()
    c.save()
    return buf.getvalue()
