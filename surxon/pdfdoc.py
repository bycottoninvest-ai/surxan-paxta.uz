"""Server-side waybill PDF (no browser needed).

Two documents are produced for every waybill version:
- 'nayman' — PUNKT UCHUN NAKLADNOY: trip number, field, brigade, time, field weight, transport and a QR code.
             NO price, NO amount, NO worker names (the punkt needs the load, not the payroll).
- 'ichki'  — ICHKI TERIM HISOBOTI: the same header plus every worker's kg (and combines), the total, and
             price/amount when a price is known. For the company only.
"""
import io
from pathlib import Path

from flask import current_app
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
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


def qr_payload(trip_no, domain):
    """What the QR holds: a link that opens the trip on the punkt screen (plain number on a local test run)."""
    if not trip_no:
        return ''
    if not domain or domain in ('localhost', '127.0.0.1'):
        return trip_no
    return f'https://{domain}/punkt/q/{trip_no}'


def _qr(c, text, x, y, size):
    widget = QrCodeWidget(text, barLevel='M')
    b = widget.getBounds()
    w, h = b[2] - b[0], b[3] - b[1]
    d = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    d.add(widget)
    renderPDF.draw(d, c, x, y)


def build_waybill_pdf(wb, *, copy, company, version, generated_at, generated_by, workers_count, lines=None, domain=''):
    """Return PDF bytes for one waybill row (queries.waybill) — copy is 'nayman' or 'ichki'.
    lines: [(name, kg)] per worker/combine for the internal harvest report."""
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    trip = wb['trip_no'] if 'trip_no' in wb.keys() else None
    c.setTitle(f'{"Nakladnoy" if copy == "nayman" else "Ichki terim hisoboti"} {trip or wb["number"]}')
    c.setAuthor(company)
    c.setSubject('Punkt (Nayman) nusxasi' if copy == 'nayman' else 'Ichki terim hisoboti')
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
    c.drawCentredString(W / 2, y, 'PUNKT UCHUN NAKLADNOY' if copy == 'nayman' else 'ICHKI TERIM HISOBOTI')
    y -= 7 * mm
    c.setFont('DejaVu-Bold', 12)
    c.drawCentredString(W / 2, y, f'Telashka: {trip}   ·   Nakladnoy № {wb["number"]}' if trip else f'NAKLADNOY № {wb["number"]}')
    y -= 6 * mm
    c.setFont('DejaVu', 10)
    label = 'Punkt / Nayman uchun nusxa' if copy == 'nayman' else 'Ichki hisob uchun (korxona)'
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

    top = y
    col = 52 * mm if copy == 'nayman' and trip else 62 * mm
    right = x1 - 52 * mm if copy == 'nayman' and trip else x1
    sent = wb['sent_at'] if 'sent_at' in wb.keys() else None

    def table2(rows, y, row_h=8 * mm):
        for k, v in rows:
            c.rect(x0, y - row_h, col, row_h)
            c.rect(x0 + col, y - row_h, right - x0 - col, row_h)
            c.setFont('DejaVu', 10)
            c.drawString(x0 + 2.5 * mm, y - row_h + 2.6 * mm, k)
            c.setFont('DejaVu-Bold', 10.5)
            c.drawString(x0 + col + 2.5 * mm, y - row_h + 2.6 * mm, v)
            y -= row_h
        return y

    y = table2([
        ('Jo‘natuvchi', company),
        ('Qabul qiluvchi (punkt)', wb['destination'] or 'Nayman'),
        ('Telashka raqami', trip or '—'),
        ('Dala', f'{wb["field_code"] or ""} · {wb["field_name"] or ""}'),
        ('Brigada', wb['brigadier_name'] or '—'),
        ('Jo‘natilgan vaqt', _date(sent) if sent else '—'),
        ('Transport', f'{wb["tractor_code"] or "—"} · {wb["trailer_code"]}' + (f' · {wb["vehicle_plate"]}' if wb['vehicle_plate'] else '')),
        ('Haydovchi', wb['driver_name'] or '—'),
        ('Ishchilar soni', str(workers_count)),
    ], y)
    if copy == 'nayman' and trip:
        payload = qr_payload(trip, domain)
        _qr(c, payload, x1 - 46 * mm, top - 50 * mm, 46 * mm)
        c.setFont('DejaVu', 7.5)
        c.drawCentredString(x1 - 23 * mm, top - 54 * mm, 'QR: punktda skaner qiling')
        c.setFont('DejaVu-Bold', 9)
        c.drawCentredString(x1 - 23 * mm, top - 58 * mm, trip)
    y -= 6 * mm
    field_sum = wb['basis'] == 'dala'
    if field_sum:
        weights = [('Og‘irlik manbai', 'Dala tarozisi yig‘indisi'), ('Dala vazni (netto)', f'{_num(wb["net_kg"])} kg')]
    else:
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
    c.drawString(x0, y, f'Dala tarozisida bittalab tortilgan kg yig‘indisi · {_date(wb["tare_at"])}' if field_sum else
                 f'Brutto: {_date(wb["gross_at"])}   ·   Tara: {_date(wb["tare_at"])}')
    if copy == 'ichki' and lines:
        # every worker's kg — the basis of the pickers' pay
        y -= 8 * mm
        c.setFont('DejaVu-Bold', 11)
        c.drawString(x0, y, 'Terimchilar (dala tarozisi)')
        y -= 3 * mm
        row_h = 6.2 * mm
        total = 0
        for i, (name, kg) in enumerate(lines, 1):
            if y - row_h < 30 * mm:
                c.setFont('DejaVu', 8)
                c.drawString(x0, 14 * mm, f'{trip or wb["number"]} · davomi keyingi sahifada')
                c.showPage()
                y = H - 20 * mm
            c.rect(x0, y - row_h, 12 * mm, row_h)
            c.rect(x0 + 12 * mm, y - row_h, x1 - x0 - 52 * mm, row_h)
            c.rect(x1 - 40 * mm, y - row_h, 40 * mm, row_h)
            c.setFont('DejaVu', 9.5)
            c.drawRightString(x0 + 10 * mm, y - row_h + 2 * mm, str(i))
            c.drawString(x0 + 14 * mm, y - row_h + 2 * mm, name[:60])
            c.drawRightString(x1 - 3 * mm, y - row_h + 2 * mm, f'{_num(kg, 1 if kg % 1 else 0)} kg')
            total += kg
            y -= row_h
        c.setFont('DejaVu-Bold', 11)
        c.drawRightString(x1 - 3 * mm, y - 6 * mm, f'JAMI: {_num(total, 1 if total % 1 else 0)} kg · {len(lines)} yozuv')
        y -= 10 * mm
    y -= 16 * mm if lines and copy == 'ichki' else 22 * mm
    if y < 24 * mm:                     # signatures need ~10 mm above the footer
        c.showPage()
        y = H - 40 * mm
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
