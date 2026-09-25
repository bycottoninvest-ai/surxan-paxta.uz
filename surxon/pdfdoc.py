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
        # not a weighbridge netto: the sum of the field-scale weighings (the punkt weighs it again)
        weights = [('Og‘irlik manbai', 'Dala tarozisi yig‘indisi (tarozi netto emas)'),
                   ('Dala hisobidagi kg', f'{_num(wb["net_kg"])} kg')]
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


def build_workers_report_pdf(ld, lines, *, company, generated_at, generated_by):
    """ICHKI: ISHCHILAR HISOBOTI for one trip — every picker (or combine) with kg, the rate frozen on those weighings,
    the amount, and the totals. Internal only (never given to the punkt)."""
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    x0, x1 = 16 * mm, W - 16 * mm
    trip = ld['trip_no'] or f'№{ld["id"]}'
    c.setTitle(f'Ishchilar hisoboti {trip}')

    def header():
        y = H - 18 * mm
        c.setFont('DejaVu-Bold', 15)
        c.drawString(x0, y, company)
        c.setFont('DejaVu', 9)
        c.drawRightString(x1, y, f'Chop: {_date(generated_at)} · {generated_by or ""}')
        y -= 9 * mm
        c.setFont('DejaVu-Bold', 16)
        c.drawCentredString(W / 2, y, 'ISHCHILAR HISOBOTI (ichki)')
        y -= 6 * mm
        c.setFont('DejaVu', 9.5)
        kind = 'Kombayn terimi' if ld['method'] == 'combine' else 'Qo‘l terimi'
        c.drawCentredString(W / 2, y, f'{trip} · {_date(ld["load_date"])} · {ld["field_code"] or ""} {ld["field_name"] or ""} · '
                                      f'{ld["brigadier_name"] or ""} · {ld["trailer_code"]} · {kind}')
        return y - 8 * mm

    cols = [('Ism / kombayn', x0 + 2 * mm, 'l'), ('Tortish', x0 + 92 * mm, 'r'), ('Kg', x0 + 115 * mm, 'r'),
            ('Narx, so‘m/kg', x0 + 145 * mm, 'r'), ('Summa, so‘m', x1 - 2 * mm, 'r')]

    def head_row(y):
        c.setFillColor(colors.HexColor('#eef3fa'))
        c.rect(x0, y - 7 * mm, x1 - x0, 7 * mm, stroke=0, fill=1)
        c.setFillColor(colors.black)
        c.setFont('DejaVu-Bold', 9.5)
        for t, x, a in cols:
            (c.drawString if a == 'l' else c.drawRightString)(x, y - 5 * mm, t)
        return y - 7 * mm

    y = head_row(header())
    tot_kg = tot_amt = 0
    uncalc = False
    people = set()
    for i, ln in enumerate(lines):
        if y < 30 * mm:
            c.showPage()
            y = head_row(header())
        c.setFont('DejaVu', 10)
        name = ln['name'][:44]
        vals = [name, str(ln['n']), _num(ln['kg'], 1 if ln['kg'] % 1 else 0),
                _num(ln['rate']) if ln['rate'] else 'hisoblanmagan',
                _num(ln['amount']) if ln['amount'] is not None else '—']
        for (t, x, a), v in zip(cols, vals):
            (c.drawString if a == 'l' else c.drawRightString)(x, y - 5.5 * mm, v)
        c.setStrokeColor(colors.HexColor('#dfe6ef'))
        c.line(x0, y - 7.5 * mm, x1, y - 7.5 * mm)
        c.setStrokeColor(colors.black)
        y -= 7.5 * mm
        tot_kg += ln['kg']
        people.add(ln['key'])
        if ln['amount'] is None:
            uncalc = True
        else:
            tot_amt += ln['amount']
    y -= 3 * mm
    c.setFillColor(colors.HexColor('#e8f6ec'))
    c.rect(x0, y - 9 * mm, x1 - x0, 9 * mm, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont('DejaVu-Bold', 11)
    c.drawString(x0 + 2 * mm, y - 6.2 * mm, f'JAMI: {len(people)} ' + ('kombayn' if ld['method'] == 'combine' else 'kishi'))
    c.drawRightString(x0 + 115 * mm, y - 6.2 * mm, f'{_num(tot_kg, 1 if tot_kg % 1 else 0)} kg')
    c.drawRightString(x1 - 2 * mm, y - 6.2 * mm, f'{_num(tot_amt)} so‘m' + (' + hisoblanmagan' if uncalc else ''))
    y -= 16 * mm
    c.setFont('DejaVu', 8.5)
    c.drawString(x0, y, 'Kg — dala tarozisida har tortish. Narx — o‘sha tortish paytida qotirilgan narx (keyin o‘zgarmaydi).')
    c.drawString(x0, y - 4.5 * mm, 'Punkt/tarozi farqi ishchi haqini o‘z-o‘zidan kamaytirmaydi. Bu hujjat punktga berilmaydi.')
    c.save()
    return buf.getvalue()


def build_receipt_pdf(wb, *, company):
    """PUNKT QABUL HUJJATI: one received trip — no price, wages or worker names."""
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    x0, x1 = 18 * mm, W - 18 * mm
    trip = wb['trip_no'] or wb['number']
    c.setTitle(f'Qabul {trip}')
    y = H - 20 * mm
    c.setFont('DejaVu-Bold', 15)
    c.drawString(x0, y, company)
    y -= 10 * mm
    c.setFont('DejaVu-Bold', 17)
    c.drawCentredString(W / 2, y, 'PUNKT QABUL HUJJATI')
    y -= 6 * mm
    c.setFont('DejaVu', 10)
    c.drawCentredString(W / 2, y, f'{wb["number"]} · {trip} · {wb["station_name"] or ""}')
    y -= 10 * mm
    src = 'dala tarozisi yig‘indisi' if wb['basis'] == 'dala' else 'jo‘natish tarozisi netto'
    rows = [('Dala', f'{wb["field_code"] or ""} · {wb["field_name"] or ""}'), ('Brigada', wb['brigadier_name'] or '—'),
            ('Telashka', f'{wb["trailer_code"]}' + (f' · {wb["tractor_code"]}' if wb['tractor_code'] else '')),
            ('Jo‘natilgan', f'{_num(wb["net_kg"])} kg ({src})')]
    if wb['station_gross_kg'] is not None:
        rows += [('Punkt brutto', f'{_num(wb["station_gross_kg"])} kg'), ('Punkt tara', f'{_num(wb["station_tare_kg"])} kg')]
    rows += [('Punkt netto (qabul)', f'{_num(wb["accepted_kg"])} kg'),
             ('Farq (qabul − jo‘natilgan)', f'{wb["nayman_diff_kg"]:+,.0f} kg'.replace(',', ' ')),
             ('Sabab', wb['nayman_diff_reason'] or '—'),
             ('Qabul qildi', f'{wb["receiver_name"] or "—"} · {_date(wb["received_at"])}')]
    for k, v in rows:
        c.rect(x0, y - 9 * mm, 62 * mm, 9 * mm)
        c.rect(x0 + 62 * mm, y - 9 * mm, x1 - x0 - 62 * mm, 9 * mm)
        c.setFont('DejaVu', 10)
        c.drawString(x0 + 2.5 * mm, y - 6 * mm, k)
        c.setFont('DejaVu-Bold', 11)
        c.drawString(x0 + 64.5 * mm, y - 6 * mm, str(v)[:60])
        y -= 9 * mm
    y -= 12 * mm
    c.setFont('DejaVu', 9)
    c.drawString(x0, y, 'Qabul qildi: ____________________        Topshirdi (haydovchi): ____________________')
    c.save()
    return buf.getvalue()
