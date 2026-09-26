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
    parts = (['1-NUSXA — punktda qoladi', '2-NUSXA — punkt kg yozib, imzo va muhr bilan korxonaga qaytariladi']
             if copy == 'nayman' else [None])
    for part in parts:
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
        if part:
            c.setFont('DejaVu-Bold', 9)
            c.setFillColor(colors.HexColor('#1d4ed8'))
            c.drawString(x0, y - 8 * mm, part)
            c.setFillColor(colors.black)
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
        if copy == 'nayman':
            y = _punkt_form(c, x0, x1, y - 6 * mm)
        y -= 16 * mm if lines and copy == 'ichki' else (12 * mm if copy == 'nayman' else 22 * mm)
        if copy != 'nayman':                # the punkt copy has signature + stamp boxes in its form instead
            if y < 24 * mm:                 # signatures need ~10 mm above the footer
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


def _punkt_form(c, x0, x1, y):
    """Empty boxes the punkt fills in by hand on the printed waybill: its scale weight, the accepted kg, date, who,
    signature and the punkt's stamp — so the company keeps a waybill confirmed by the punkt."""
    h = 9 * mm
    c.setFont('DejaVu-Bold', 10.5)
    c.drawString(x0, y, 'PUNKT TOMONIDAN TO‘LDIRILADI (qo‘lda)')
    y -= 2.5 * mm
    mid = (x0 + x1) / 2
    rows = [(('Punkt tarozisi — brutto', 'kg'), ('Tara', 'kg')),
            (('QABUL QILINGAN KG (netto)', 'kg'), None),
            (('Qabul sanasi va vaqti', ''), None),
            (('Qabul qildi (F.I.Sh.)', ''), ('Imzo', ''))]
    for left, right in rows:
        cells = [(x0, mid if right else x1, left)] + ([(mid, x1, right)] if right else [])
        for a, b, (label, unit) in cells:
            c.rect(a, y - h, b - a, h)
            c.setFont('DejaVu-Bold' if 'QABUL' in label else 'DejaVu', 9.5 if 'QABUL' in label else 8.5)
            c.drawString(a + 2 * mm, y - h + 3 * mm, label + ':')
            if unit:
                c.setFont('DejaVu', 9)
                c.drawRightString(b - 2.5 * mm, y - h + 3 * mm, unit)
        y -= h
    # stamp places
    y -= 3 * mm
    box = 30 * mm
    for a, text in ((x0, 'PUNKT MUHRI'), (x1 - 62 * mm, 'JO‘NATUVCHI MUHRI VA IMZOSI')):
        c.setDash(3, 2)
        c.roundRect(a, y - box, 62 * mm, box, 3 * mm)
        c.setDash()
        c.setFont('DejaVu', 8)
        c.setFillColor(colors.HexColor('#777777'))
        c.drawCentredString(a + 31 * mm, y - box / 2 - 1 * mm, text + ' (M.O.)')
        c.setFillColor(colors.black)
    return y - box


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


def _stamp(c, cx, cy, r, ring, lines, color='#1d4ed8'):
    """A round electronic stamp: text around the ring, a few lines in the middle (slightly tilted like a real one)."""
    import math
    c.saveState()
    c.translate(cx, cy)
    c.rotate(-8)
    col = colors.HexColor(color)
    c.setStrokeColor(col)
    c.setFillColor(col)
    c.setLineWidth(1.6)
    c.circle(0, 0, r)
    c.setLineWidth(0.8)
    c.circle(0, 0, r - 2.2 * mm)
    c.circle(0, 0, r - 7.2 * mm)
    c.setFont('DejaVu-Bold', 7.2)
    ring = (ring + ' • ') * 3
    rr = r - 5.6 * mm
    step = 360 / max(len(ring), 1) if len(ring) > 60 else 6.2
    angle = 90
    for ch in ring[:int(360 / step)]:
        rad = math.radians(angle)
        c.saveState()
        c.translate(rr * math.cos(rad), rr * math.sin(rad))
        c.rotate(angle - 90)
        c.drawCentredString(0, -1.2 * mm, ch)
        c.restoreState()
        angle -= step
    y = (len(lines) - 1) * 2.3 * mm
    for i, (txt, size) in enumerate(lines):
        c.setFont('DejaVu-Bold', size)
        c.drawCentredString(0, y - 1.2 * mm, txt)
        y -= 4.6 * mm
    c.restoreState()


def receipt_code(wb, secret):
    """Short check code of a punkt receipt: anyone can verify the stamp on /tekshir/<id>/<code>."""
    import hashlib
    import hmac
    msg = f'{wb["id"]}:{wb["receipt_id"]}:{wb["accepted_kg"]}:{wb["received_at"]}'.encode()
    return hmac.new((secret or 'surxon').encode(), msg, hashlib.sha256).hexdigest()[:10].upper()


def build_receipt_pdf(wb, *, company, code='', domain='', stamp_photo=False, by_punkt=True):
    """TASDIQLANGAN NAKLADNOY: the waybill as the punkt accepted it — what was sent, what the punkt scale showed,
    the difference, who accepted and when, an electronic stamp and a QR to verify it. No price, wages or names."""
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    x0, x1 = 18 * mm, W - 18 * mm
    trip = wb['trip_no'] or wb['number']
    c.setTitle(f'Tasdiqlangan nakladnoy {trip}')
    c.setAuthor(company)
    y = H - 16 * mm
    logo = Path(current_app.static_folder) / 'img' / 'logo-dark.png'
    if logo.exists():
        c.drawImage(str(logo), W / 2 - 36 * mm, y - 20 * mm, width=72 * mm, height=22 * mm, mask='auto',
                    preserveAspectRatio=True, anchor='c')
    y -= 27 * mm
    c.setLineWidth(1.2)
    c.line(x0, y, x1, y)
    y -= 10 * mm
    c.setFont('DejaVu-Bold', 17)
    c.drawCentredString(W / 2, y, 'TASDIQLANGAN NAKLADNOY')
    y -= 6.5 * mm
    c.setFont('DejaVu-Bold', 11.5)
    c.drawCentredString(W / 2, y, f'Nakladnoy № {wb["number"]}   ·   Telashka: {trip}')
    y -= 5.5 * mm
    c.setFont('DejaVu', 9.5)
    c.drawCentredString(W / 2, y, (f'Punkt qabul qildi va tizimda tasdiqladi · {wb["station_name"] or ""}' if by_punkt else
                                   f'Punkt qog‘oz nakladnoyda tasdiqlagan kg (korxona kiritdi) · {wb["station_name"] or ""}'))
    y -= 9 * mm

    def rows(items, y, col=62 * mm, big_last=False, fill=None):
        for i, (k, v) in enumerate(items):
            last = big_last and i == len(items) - 1
            h = 10 * mm if last else 8 * mm
            if last and fill:
                c.setFillColor(colors.HexColor(fill))
                c.rect(x0, y - h, x1 - x0, h, stroke=0, fill=1)
                c.setFillColor(colors.black)
            c.rect(x0, y - h, col, h)
            c.rect(x0 + col, y - h, x1 - x0 - col, h)
            c.setFont('DejaVu', 9.5)
            c.drawString(x0 + 2.5 * mm, y - h + 2.8 * mm, k)
            c.setFont('DejaVu-Bold', 15 if last else 10.5)
            c.drawString(x0 + col + 2.5 * mm, y - h + 2.8 * mm, str(v)[:64])
            y -= h
        return y

    src = 'dala tarozisi yig‘indisi' if wb['basis'] == 'dala' else 'jo‘natish tarozisi netto'
    c.setFont('DejaVu-Bold', 10.5)
    c.drawString(x0, y, 'JO‘NATILDI')
    y -= 2.5 * mm
    y = rows([('Jo‘natuvchi', company), ('Dala', f'{wb["field_code"] or ""} · {wb["field_name"] or ""}'),
              ('Brigada', wb['brigadier_name'] or '—'),
              ('Transport', f'{wb["tractor_code"] or "—"} · {wb["trailer_code"]}' + (f' · {wb["vehicle_plate"]}' if wb['vehicle_plate'] else '')),
              ('Haydovchi', wb['driver_name'] or '—'), ('Nakladnoy sanasi', _date(wb['document_date'])),
              ('Jo‘natilgan kg', f'{_num(wb["net_kg"])} kg ({src})')], y)
    y -= 7 * mm
    c.setFont('DejaVu-Bold', 10.5)
    c.drawString(x0, y, 'PUNKT QABULI (punkt tarozisi)')
    y -= 2.5 * mm
    items = []
    if wb['station_gross_kg'] is not None:
        items += [('Brutto', f'{_num(wb["station_gross_kg"])} kg'), ('Tara', f'{_num(wb["station_tare_kg"])} kg')]
    diff = wb['nayman_diff_kg'] or 0
    pct = (diff / wb['net_kg'] * 100) if wb['net_kg'] else 0
    items += [('Farq (qabul − jo‘natilgan)', f'{diff:+,.0f} kg ({pct:+.2f}%)'.replace(',', ' ')),
              ('Farq sababi', wb['nayman_diff_reason'] or '—'),
              ('Qabul qildi', f'{wb["receiver_name"] or "—"} · {_date(wb["received_at"])}'),
              ('TASDIQLANGAN KG (netto)', f'{_num(wb["accepted_kg"])} kg')]
    y = rows(items, y, big_last=True, fill='#e8f6ec')
    # the stamp and the verification QR
    sy = y - 34 * mm
    station = (wb['station_name'] or 'PAXTA QABUL PUNKTI').upper()
    _stamp(c, x0 + 36 * mm, sy, 26 * mm, f'{station} · ' + ('ELEKTRON MUHR' if by_punkt else 'QOG‘OZDAN KIRITILDI'),
           [('QABUL QILINDI', 10), (f'{_num(wb["accepted_kg"])} kg', 12), (_date(wb['received_at']), 7.5),
            (f'№ {wb["number"]}', 7.5)])
    if code:
        url = f'https://{domain}/tekshir/{wb["id"]}/{code}' if domain and domain not in ('localhost', '127.0.0.1') \
            else f'/tekshir/{wb["id"]}/{code}'
        _qr(c, url, x1 - 42 * mm, sy - 21 * mm, 42 * mm)
        c.setFont('DejaVu', 7.5)
        c.drawCentredString(x1 - 21 * mm, sy - 24 * mm, 'Muhrni tekshirish: QR ni skaner qiling')
        c.setFont('DejaVu-Bold', 9)
        c.drawCentredString(x1 - 21 * mm, sy - 28 * mm, f'Tekshiruv kodi: {code}')
    y = sy - 40 * mm
    c.setLineWidth(0.8)
    c.line(x0, y, x0 + 70 * mm, y)
    c.line(x1 - 70 * mm, y, x1, y)
    c.setFont('DejaVu', 9.5)
    c.drawCentredString(x0 + 35 * mm, y - 5 * mm, 'Qabul qildi (imzo, muhr)')
    c.drawCentredString(x1 - 35 * mm, y - 5 * mm, 'Topshirdi (haydovchi, imzo)')
    c.setFont('DejaVu', 7.5)
    c.setFillColor(colors.HexColor('#555555'))
    c.drawString(x0, 16 * mm, ('Elektron muhr: punkt operatori tizimda “Qabulni tasdiqlash” ni bosganda qo‘yiladi. ' if by_punkt else
                               'Kg punktning pechatli qog‘oz nakladnoyidan kiritilgan. ') + 'Saqlangan qabul o‘zgartirilmaydi.')
    c.drawString(x0, 12 * mm, f'SURXON PAXTA HISOB TIZIMI · {wb["number"]} · {trip}'
                 + (' · pechatli qog‘oz rasmi tizimda saqlangan' if stamp_photo else ''))
    c.showPage()
    c.save()
    return buf.getvalue()


def build_cash_pdf(e, corrections, *, company, printed_by, printed_at):
    """KASSA HUJJATI: one cash operation (payment / expense / income) with its corrections, for sharing from the phone."""
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    x0, x1 = 18 * mm, W - 18 * mm
    c.setTitle(f'Kassa {e["doc_no"]}')
    y = H - 20 * mm
    c.setFont('DejaVu-Bold', 15)
    c.drawString(x0, y, company)
    y -= 10 * mm
    title = {'pay': 'CHIQIM ORDERI — ISHCHIGA TO‘LOV', 'expense': 'CHIQIM ORDERI — XARAJAT',
             'income': 'KIRIM ORDERI'}.get(e['kind'], 'KASSA HUJJATI')
    c.setFont('DejaVu-Bold', 16)
    c.drawCentredString(W / 2, y, title)
    y -= 6 * mm
    c.setFont('DejaVu', 10)
    c.drawCentredString(W / 2, y, f'{e["doc_no"]} · {_date(e["entry_date"])} {e["created_at"][11:16]} · {e["box_name"] or ""}')
    y -= 10 * mm
    rows = [('Amal', e['what']), ('Kimga / kimdan', e['who'] or '—'),
            ('Summa', f'{_num(e["amount"])} so‘m')]
    if e['kind'] == 'expense':
        if e['field_name']:
            rows.append(('Dala', e['field_name']))
        if e['equipment_code']:
            rows.append(('Texnika', e['equipment_code']))
    if e['note']:
        rows.append(('Izoh', e['note']))
    rows += [('Kiritdi', f'{e["by_name"] or "—"} · {e["created_at"][:16]}'), ('Holat', e['status_label'])]
    if e['voided_at']:
        rows.append(('Bekor/tuzatildi', f'{e["voided_name"] or ""} · {e["voided_at"][:16]}'))
    for k, v in rows:
        c.rect(x0, y - 9 * mm, 55 * mm, 9 * mm)
        c.rect(x0 + 55 * mm, y - 9 * mm, x1 - x0 - 55 * mm, 9 * mm)
        c.setFont('DejaVu', 10)
        c.drawString(x0 + 2.5 * mm, y - 6 * mm, k)
        c.setFont('DejaVu-Bold', 11)
        c.drawString(x0 + 57.5 * mm, y - 6 * mm, str(v)[:62])
        y -= 9 * mm
    if corrections:
        y -= 8 * mm
        c.setFont('DejaVu-Bold', 11)
        c.drawString(x0, y, 'Tuzatishlar tarixi')
        c.setFont('DejaVu', 9)
        for t in corrections:
            y -= 6 * mm
            status = {'KUTILMOQDA': 'kutilmoqda', 'BAJARILDI': 'bajarildi', 'RAD': 'rad etildi'}[t['status']]
            c.drawString(x0, y, (f'#{t["id"]} {t["reason"]}' + (f' — {t["note"]}' if t['note'] else '') +
                                 f' · {t["requested_name"] or ""} {t["requested_at"][:16]} · {status}'
                                 + (f' ({t["decided_name"]})' if t['decided_name'] else '')
                                 + (f' · yangi: {t["new_doc_no"]}' if t['new_doc_no'] else ''))[:110])
    y -= 16 * mm
    c.setFont('DejaVu', 9)
    c.drawString(x0, y, 'Berdi: ____________________        Oldi: ____________________')
    y -= 8 * mm
    c.setFont('DejaVu', 8)
    c.drawString(x0, y, f'Chop etildi: {printed_at[:16]} · {printed_by}')
    c.save()
    return buf.getvalue()


def build_qr_labels_pdf(items, *, company):
    """A4 sheet of QR labels (6 per page): (qr_text, code, subtitle). Stick them on the pump / the machine."""
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    c.setTitle('Solyarka QR')
    cols, rows = 2, 3
    cw, ch = (W - 20 * mm) / cols, (H - 20 * mm) / rows
    for i, (text, code, sub) in enumerate(items):
        if i and i % (cols * rows) == 0:
            c.showPage()
        k = i % (cols * rows)
        x0, y0 = 10 * mm + (k % cols) * cw, H - 10 * mm - (k // cols + 1) * ch
        c.setDash(3, 3)
        c.rect(x0 + 2 * mm, y0 + 2 * mm, cw - 4 * mm, ch - 4 * mm)
        c.setDash()
        size = min(cw, ch) - 36 * mm
        _qr(c, text, x0 + (cw - size) / 2, y0 + 22 * mm, size)
        c.setFont('DejaVu-Bold', 20)
        c.drawCentredString(x0 + cw / 2, y0 + 14 * mm, code)
        c.setFont('DejaVu', 9)
        c.drawCentredString(x0 + cw / 2, y0 + 8.5 * mm, (sub or '')[:48])
        c.setFont('DejaVu', 7)
        c.drawCentredString(x0 + cw / 2, y0 + ch - 7 * mm, f'{company} · solyarka')
    c.save()
    return buf.getvalue()
