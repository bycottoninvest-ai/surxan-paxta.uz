"""Server-side waybill PDF (no browser needed).

Two copies are produced for every waybill version:
- 'nayman' — the copy handed to Nayman: weights and parties only, NO price and NO amount.
- 'ichki'  — internal archive copy: same document plus price/amount when a price is known.
"""
import io
from pathlib import Path

from flask import current_app
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONT_DIR = Path(__file__).resolve().parent / 'fonts'
_fonts_ready = False


def _fonts():
    global _fonts_ready
    if not _fonts_ready:
        pdfmetrics.registerFont(TTFont('DejaVu', str(FONT_DIR / 'DejaVuSans.ttf')))
        pdfmetrics.registerFont(TTFont('DejaVu-Bold', str(FONT_DIR / 'DejaVuSans-Bold.ttf')))
        _fonts_ready = True


def _num(v, digits=0):
    if v is None:
        return '—'
    return f'{v:,.{digits}f}'.replace(',', ' ')


def _date(iso):
    if not iso:
        return '—'
    s = str(iso)
    return f'{s[8:10]}.{s[5:7]}.{s[0:4]}' + (s[10:16] if len(s) > 10 else '')


def build_waybill_pdf(wb, *, copy, company, version, generated_at, generated_by, workers_count):
    """Return PDF bytes for one waybill row (queries.waybill) — copy is 'nayman' or 'ichki'."""
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f'Nakladnoy {wb["number"]}')
    c.setAuthor(company)
    c.setSubject('Nayman nusxasi' if copy == 'nayman' else 'Ichki arxiv nusxasi')
    W, H = A4
    x0, x1 = 20 * mm, W - 20 * mm
    y = H - 18 * mm

    logo = Path(current_app.static_folder) / 'img' / 'logo-dark.png'
    if logo.exists():
        c.drawImage(str(logo), W / 2 - 40 * mm, y - 22 * mm, width=80 * mm, height=24 * mm, mask='auto',
                    preserveAspectRatio=True, anchor='c')
    y -= 30 * mm
    c.setLineWidth(1.2)
    c.line(x0, y, x1, y)
    y -= 12 * mm
    c.setFont('DejaVu-Bold', 17)
    c.drawCentredString(W / 2, y, f'NAKLADNOY № {wb["number"]}')
    y -= 7 * mm
    c.setFont('DejaVu', 10)
    label = 'Nayman uchun nusxa' if copy == 'nayman' else 'Ichki arxiv nusxasi (narx bilan)'
    c.drawCentredString(W / 2, y, f'{label} · {version}-versiya')
    c.setFont('DejaVu-Bold', 11)
    c.drawRightString(x1, y - 8 * mm, _date(wb['document_date']))
    y -= 16 * mm

    if wb['status'] == 'BEKOR':
        c.saveState()
        c.setFillColor(colors.HexColor('#c01b43'))
        c.setFont('DejaVu-Bold', 44)
        c.translate(W / 2, H / 2)
        c.rotate(30)
        c.drawCentredString(0, 0, 'BEKOR QILINGAN')
        c.restoreState()

    def table(rows, y, col=62 * mm, row_h=8 * mm, bold_last=False, big=False):
        for i, (k, v) in enumerate(rows):
            last = bold_last and i == len(rows) - 1
            if last:
                c.setFillColor(colors.HexColor('#e8f6ec'))
                c.rect(x0, y - row_h, x1 - x0, row_h, stroke=0, fill=1)
                c.setFillColor(colors.black)
            c.rect(x0, y - row_h, col, row_h)
            c.rect(x0 + col, y - row_h, x1 - x0 - col, row_h)
            c.setFont('DejaVu', 10)
            c.drawString(x0 + 2.5 * mm, y - row_h + 2.6 * mm, k)
            c.setFont('DejaVu-Bold', 14 if (last and big) else 10.5)
            if big:
                c.drawRightString(x1 - 3 * mm, y - row_h + 2.6 * mm, v)
            else:
                c.drawString(x0 + col + 2.5 * mm, y - row_h + 2.6 * mm, v)
            y -= row_h
        return y

    y = table([
        ('Jo‘natuvchi', company),
        ('Qabul qiluvchi', wb['destination'] or 'Nayman'),
        ('Brigadir', wb['brigadier_name'] or '—'),
        ('Dala', f'{wb["field_code"] or ""} · {wb["field_name"] or ""}'),
        ('Traktor / telashka', f'{wb["tractor_code"] or "—"} / {wb["trailer_code"]}'),
        ('Mashina', wb['vehicle_plate'] or '—'),
        ('Haydovchi', wb['driver_name'] or '—'),
        ('Tarozi №', wb['scale_no'] or '—'),
        ('Ishchilar soni', str(workers_count)),
    ], y)
    y -= 6 * mm
    weights = [('Brutto', f'{_num(wb["gross_kg"])} kg'), ('Tara', f'{_num(wb["tare_kg"])} kg'),
               ('Netto', f'{_num(wb["net_kg"])} kg')]
    y = table(weights, y, bold_last=True, big=True)
    if copy == 'ichki' and wb['price_per_kg']:
        y -= 4 * mm
        y = table([('Narx', f'{_num(wb["price_per_kg"])} so‘m/kg'),
                   ('Jami summa (ichki hisob)', f'{_num(wb["net_kg"] * wb["price_per_kg"])} so‘m')], y, big=True)
    elif copy == 'ichki':
        y -= 4 * mm
        c.setFont('DejaVu', 9)
        c.drawString(x0, y - 4 * mm, 'Narx hali kiritilmagan — summa hisoblanmagan.')
        y -= 6 * mm
    y -= 6 * mm
    c.setFont('DejaVu', 9)
    c.drawString(x0, y, f'Brutto: {_date(wb["gross_at"])}   ·   Tara: {_date(wb["tare_at"])}')
    y -= 22 * mm
    c.setLineWidth(0.8)
    c.line(x0, y, x0 + 70 * mm, y)
    c.line(x1 - 70 * mm, y, x1, y)
    c.setFont('DejaVu', 10)
    c.drawCentredString(x0 + 35 * mm, y - 5 * mm, 'Jo‘natuvchi (imzo)')
    c.drawCentredString(x1 - 35 * mm, y - 5 * mm, 'Qabul qiluvchi (imzo)')
    c.setFont('DejaVu', 7.5)
    c.setFillColor(colors.HexColor('#555555'))
    c.drawString(x0, 14 * mm, f'SURXON PAXTA HISOB TIZIMI · {wb["number"]} · {version}-versiya · yaratildi {generated_at} · {generated_by}')
    c.drawString(x0, 10 * mm, 'Raqam tizim tomonidan beriladi va takrorlanmaydi. Oldingi versiyalar arxivda saqlanadi.')
    c.showPage()
    c.save()
    return buf.getvalue()
