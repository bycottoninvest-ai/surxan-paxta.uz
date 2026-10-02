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


def build_blanks_pdf(blanks, *, company, domain, receiver='', copies=2):
    """Field blanks (dala yorlig‘i), TWO per A4 page — cut along the dashed line. The clerk writes only the trailer,
    field, kg and ticks hand / combine; its QR ties it to the trip (and the combine / brigade) in the system. It goes
    with the trailer; only the UY (umumiy nakladnoy) goes into the cluster, so it has no receiver part and one copy is
    enough (`receiver` / `copies` are kept for the callers and not used)."""
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    c.setTitle('Dala blanklari')
    c.setAuthor(company)
    logo = Path(current_app.static_folder) / 'img' / 'logo-dark.png'
    x0, x1 = 16 * mm, W - 16 * mm
    half = H / 2

    def line_field(y, label, width=None, x=None, big=False):
        x = x or x0
        font, size = ('DejaVu-Bold', 14) if big else ('DejaVu', 12)
        c.setFont(font, size)
        c.drawString(x, y, label)
        lw = c.stringWidth(label, font, size)
        c.setLineWidth(0.8)
        c.line(x + lw + 3 * mm, y - 1.2 * mm, (x + width) if width else x1, y - 1.2 * mm)

    def tick(x, y, label):
        c.setLineWidth(1.4)
        c.rect(x, y - 1.2 * mm, 7 * mm, 7 * mm)
        c.setFont('DejaVu-Bold', 14)
        c.drawString(x + 10 * mm, y, label)

    def one(b, top):
        url = f'https://{domain}/punkt/blanka/{b["token"]}'
        y = top - 12 * mm
        if logo.exists():
            c.drawImage(str(logo), x0, y - 15 * mm, width=48 * mm, height=15 * mm, mask='auto', preserveAspectRatio=True, anchor='sw')
        c.setFont('DejaVu-Bold', 16)
        c.drawRightString(x1 - 40 * mm, y - 9 * mm, 'DALA NAKLADNOYI')
        _qr(c, url, x1 - 34 * mm, y - 30 * mm, 34 * mm)
        c.setFont('DejaVu', 8)                  # small: only if the QR gets dirty and has to be typed in
        c.drawCentredString(x1 - 17 * mm, y - 33 * mm, b['number'])
        y -= 44 * mm
        line_field(y, 'Telashka:', width=70 * mm)
        line_field(y, 'Dala:', x=x0 + 80 * mm)
        y -= 18 * mm
        c.setLineWidth(1.8)
        c.rect(x0, y - 5 * mm, x1 - x0, 15 * mm)
        c.setFont('DejaVu-Bold', 17)
        c.drawString(x0 + 4 * mm, y + 0.5 * mm, 'PAXTA, kg:')
        y -= 19 * mm
        tick(x0, y, 'QO‘L TERIMI')
        tick(x0 + 70 * mm, y, 'KOMBAYN')
        y -= 16 * mm
        line_field(y, 'Sana:', width=60 * mm)
        line_field(y, 'Hisobchi imzosi:', x=x0 + 70 * mm)
        c.setFont('DejaVu', 8)
        c.drawString(x0, top - half + 8 * mm, f'{company} · QR ni hisobchi yopishda, punktda Yunus skanerlaydi.')

    for i, b in enumerate(blanks):
        if i % 2 == 0:
            one(b, H)
            c.setDash(4, 3)
            c.setLineWidth(0.6)
            c.line(8 * mm, half, W - 8 * mm, half)
            c.setDash()
            c.setFont('DejaVu', 7)
            c.drawCentredString(W / 2, half + 1.5 * mm, '✂  shu yerdan qirqing')
        else:
            one(b, half)
            c.showPage()
    if len(blanks) % 2:
        c.showPage()
    c.save()
    return buf.getvalue()


def build_group_blanks_pdf(groups, *, company, domain, receiver='', copies=2):
    """UMUMIY NAKLADNOY (UY) papers, one A4 each (`copies` per number): the single paper that goes into the punkt with
    a tractor's several trailers. TOP — ours: tractor, date, number of trailers, TOTAL sent kg, who made it; the list of
    trailers lives in the system behind the QR. BOTTOM — the receiver: brutto, tara, ACCEPTED netto, load number, name,
    signature; the rest of the page is left empty for the stamps."""
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    c.setTitle('Umumiy nakladnoy blanklari')
    c.setAuthor(company)
    logo = Path(current_app.static_folder) / 'img' / 'logo-dark.png'
    x0, x1 = 16 * mm, W - 16 * mm
    copy_names = {1: '1-NUSXA — QABUL QILUVCHIDA QOLADI', 2: '2-NUSXA — JO‘NATUVCHIDA QOLADI'}

    def line_field(y, label, width=None, x=None, big=False):
        x = x or x0
        f = 'DejaVu-Bold' if big else 'DejaVu'
        c.setFont(f, 12 if big else 11)
        c.drawString(x, y, label)
        c.setLineWidth(0.8)
        c.line(x + c.stringWidth(label, f, 12 if big else 11) + 3 * mm, y - 1.2 * mm, (x + width) if width else x1, y - 1.2 * mm)

    def banner(y, text, rgb):
        c.setFillColorRGB(*rgb)
        c.rect(x0, y, x1 - x0, 8 * mm, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        size = 12
        while size > 8 and c.stringWidth(text, 'DejaVu-Bold', size) > x1 - x0 - 6 * mm:
            size -= 0.5
        c.setFont('DejaVu-Bold', size)
        c.drawString(x0 + 3 * mm, y + 2.4 * mm, text)
        c.setFillColorRGB(0, 0, 0)

    def big_box(y, text):
        c.setLineWidth(1.6)
        c.rect(x0, y - 4 * mm, x1 - x0, 13 * mm)
        c.setFont('DejaVu-Bold', 15)
        c.drawString(x0 + 3 * mm, y + 1.5 * mm, text)

    for g in groups:
        url = f'https://{domain}/punkt/uy/{g["token"]}'
        for copy in range(1, copies + 1):
            y = H - 14 * mm
            if copies > 1:
                c.setFont('DejaVu-Bold', 9)
                c.drawString(x0, H - 8 * mm, copy_names.get(copy, f'{copy}-NUSXA'))
            if logo.exists():
                c.drawImage(str(logo), x0, y - 16 * mm, width=52 * mm, height=16 * mm, mask='auto', preserveAspectRatio=True, anchor='sw')
            c.setFont('DejaVu-Bold', 13)
            c.drawRightString(x1 - 40 * mm, y - 4 * mm, 'UMUMIY NAKLADNOY')
            c.setFont('DejaVu', 9)
            c.drawRightString(x1 - 40 * mm, y - 8.5 * mm, '(punktga kiradigan yagona hujjat)')
            c.setFont('DejaVu-Bold', 26)
            c.drawRightString(x1 - 40 * mm, y - 18 * mm, '№ ' + g['number'])
            _qr(c, url, x1 - 36 * mm, y - 34 * mm, 36 * mm)
            y -= 46 * mm
            banner(y, f'1. JO‘NATUVCHI: {company}', (0.04, 0.23, 0.43))
            y -= 12 * mm
            line_field(y, 'Traktor:', width=80 * mm)
            line_field(y, 'Sana:', x=x0 + 90 * mm)
            y -= 12 * mm
            line_field(y, 'Telashkalar soni:', width=80 * mm)
            y -= 16 * mm
            big_box(y, 'JAMI JO‘NATILGAN PAXTA, kg:')
            y -= 14 * mm
            line_field(y, 'Tuzdi (F.I.Sh.):', width=100 * mm)
            line_field(y, 'Imzo:', x=x0 + 110 * mm)
            y -= 6 * mm
            c.setFont('DejaVu', 8)
            c.drawString(x0, y, 'Ichidagi telashkalar (qaysi kombayn / qo‘l terimi, kimniki, necha kg) tizimda saqlanadi — QR orqali bog‘langan.')
            y -= 10 * mm
            c.setDash(4, 3)
            c.setLineWidth(0.6)
            c.line(x0, y, x1, y)
            c.setDash()
            y -= 12 * mm
            banner(y, '2. QABUL QILUVCHI' + (f': {receiver}' if receiver else ''), (0.09, 0.45, 0.24))
            y -= 11 * mm
            line_field(y, 'Punkt:')
            y -= 12 * mm
            line_field(y, 'Brutto, kg:', width=80 * mm, big=True)
            line_field(y, 'Tara, kg:', x=x0 + 90 * mm, big=True)
            y -= 16 * mm
            big_box(y, 'QABUL QILINGAN PAXTA (NETTO), kg:')
            y -= 16 * mm
            line_field(y, 'Yuk xati №:', width=80 * mm)
            line_field(y, 'Qabul sanasi:', x=x0 + 90 * mm)
            y -= 12 * mm
            line_field(y, 'Qabul qiluvchi F.I.Sh.:', width=120 * mm)
            line_field(y, 'Imzo:', x=x0 + 126 * mm)
            c.setFont('DejaVu', 8)
            c.drawString(x0, 10 * mm, f'№ {g["number"]} · ikki nusxada · ikki tomon imzosi va muhri bilan haqiqiy.')
            c.showPage()
    c.save()
    return buf.getvalue()


KIND_TITLES = {'zapravka': 'ZAPRAVKA (SOLYARKA)', 'traktor': 'TRAKTOR', 'kombayn': 'KOMBAYN', 'telashka': 'PRITSEP (TELASHKA)', 'mashina': 'MASHINA'}
KIND_HINTS = {
    'zapravka': 'Solyarka olishda shu QR skanerlanadi (Solyarka → Olish).',
    'traktor': 'Solyarka berishda shu QR skanerlanadi (Solyarka → Berish).',
    'kombayn': 'Solyarka berishda shu QR skanerlanadi (Solyarka → Berish).',
    'mashina': 'Solyarka berishda shu QR skanerlanadi (Solyarka → Berish).',
    'telashka': 'Telefon kamerasi bilan skanerlang — shu pritsepning joriy reysi ochiladi.',
}


def build_equipment_qr_pdf(items, *, company):
    """One A4 page per machine, to stick on it: logo, what it is, a big QR and a big code readable from afar.
    items: (qr_text, kind, code, subtitle)."""
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    c.setTitle('Texnika QR kodlari')
    c.setAuthor(company)
    logo = Path(current_app.static_folder) / 'img' / 'logo-dark.png'
    for text, kind, code, sub in items:
        c.setLineWidth(1.5)
        c.roundRect(10 * mm, 10 * mm, W - 20 * mm, H - 20 * mm, 6 * mm)
        y = H - 20 * mm
        if logo.exists():
            c.drawImage(str(logo), W / 2 - 38 * mm, y - 22 * mm, width=76 * mm, height=22 * mm, mask='auto',
                        preserveAspectRatio=True, anchor='c')
        y -= 34 * mm
        c.setFont('DejaVu-Bold', 26)
        c.drawCentredString(W / 2, y, KIND_TITLES.get(kind, kind.upper()))
        size = 150 * mm
        _qr(c, text, (W - size) / 2, y - 8 * mm - size, size)
        y -= 8 * mm + size + 30 * mm
        c.setFont('DejaVu-Bold', 76)
        c.drawCentredString(W / 2, y, code)
        y -= 12 * mm
        c.setFont('DejaVu', 14)
        c.drawCentredString(W / 2, y, (sub or '')[:60])
        c.setFont('DejaVu', 10)
        c.drawCentredString(W / 2, 17 * mm, KIND_HINTS.get(kind, ''))
        c.setFont('DejaVu', 8)
        c.drawCentredString(W / 2, 13 * mm, f'{company} · shu varaqni texnikaga yopishtiring (shaffof skotch bilan yoping)')
        c.showPage()
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


def _short_trip(t):
    """“TL-2026-000026 (UY-0001)” → “26 (UY-0001)” — the number the clerk says."""
    n, _, rest = t.partition(' ')
    return (n.rsplit('-', 1)[-1].lstrip('0') or n) + (f' {rest}' if rest else '')


def _photo_reader(cfg, rel, max_px=1100):
    """A stored photo, downscaled for the PDF (the full 2400 px originals would make a 10 MB file)."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    path = Path(cfg.UPLOAD_DIR) / rel
    if not path.exists():
        return None
    try:
        img = Image.open(path)
        img = img.convert('RGB')
        img.thumbnail((max_px, max_px))
        out = io.BytesIO()
        img.save(out, 'JPEG', quality=72, optimize=True)
        out.seek(0)
        return ImageReader(out), img.size
    except Exception:
        return None


def build_combine_statement_pdf(st, *, company, year, tariff_name='', generated_at='', generated_by=''):
    """KOMBAYN AKT-SVERKA for the season — given to the combine's owner (ours or a hired one). One line per trip:
    the field clerk's kg (information), the punkt netto, the cluster's PQ-17 sof kg and the sum on it; then the
    payments, what is left to pay, both signatures — and, as an annex, the photos of the field blanks and the punkt's
    stamped papers, so the owner sees where every kilogram comes from."""
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    x0, x1 = 14 * mm, W - 14 * mm
    cb = st['combine']
    navy, blue50, green50, orange50, gray50, line_c = (colors.HexColor(h) for h in
                                                       ('#0d2a4a', '#eaf1fb', '#e6f5ec', '#fff2e0', '#f1f3f6', '#d9e2ee'))
    c.setTitle(f'Kombayn akt-sverka {cb["code"]} {year}')
    c.setAuthor(company)
    logo = Path(current_app.static_folder) / 'img' / 'logo-dark.png'
    std = None
    if cb['tariff_type'] == 'tonna' and cb['tariff_rate']:
        rate_txt = f'{_num(cb["tariff_rate"] / 1000)} so‘m/kg'
    elif cb['tariff_type']:
        rate_txt = f'{tariff_name}: {_num(cb["tariff_rate"])} so‘m'
    else:
        from .accounting import combine_standard
        std = combine_standard()
        rate_txt = f'{_num(std)} so‘m/kg (standart)' if std else 'tarif yo‘q'
    page = [1]

    def footer():
        c.setFont('DejaVu', 7.5)
        c.setFillColor(colors.HexColor('#7a8aa0'))
        c.drawString(x0, 9 * mm, f'{company} · Kombayn akt-sverka · {cb["code"]} · {year}')
        c.drawRightString(x1, 9 * mm, f'{page[0]}-bet')
        c.setFillColor(colors.black)

    def new_page():
        footer()
        c.showPage()
        page[0] += 1

    def header(first=True):
        y = H - 14 * mm
        if logo.exists():
            c.drawImage(str(logo), x0, y - 11 * mm, width=38 * mm, height=12 * mm, mask='auto', preserveAspectRatio=True,
                        anchor='sw')
        else:
            c.setFont('DejaVu-Bold', 13)
            c.drawString(x0, y - 7 * mm, company)
        c.setFont('DejaVu', 8)
        c.setFillColor(colors.HexColor('#5d6f86'))
        c.drawRightString(x1, y - 3 * mm, company)
        c.drawRightString(x1, y - 7 * mm, f'Chop: {_date(generated_at)} · {generated_by or ""}')
        c.setFillColor(colors.black)
        y -= 15 * mm
        if not first:
            c.setFont('DejaVu-Bold', 10)
            c.drawString(x0, y, f'{cb["code"]} · {cb["owner"]} — davomi')
            return y - 5 * mm
        c.setFillColor(navy)
        c.roundRect(x0, y - 10 * mm, x1 - x0, 10 * mm, 2 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont('DejaVu-Bold', 13)
        per = st.get('period')
        title = (f'KOMBAYN AKT-SVERKA · {_date(per["dan"])}' + (f' – {_date(per["gacha"])}' if per['gacha'] != per['dan'] else '')
                 if per else f'KOMBAYN AKT-SVERKA · {year} MAVSUM')
        c.drawCentredString(W / 2, y - 6.6 * mm, title)
        c.setFillColor(colors.black)
        y -= 16 * mm
        c.setFont('DejaVu-Bold', 15)
        c.drawString(x0, y, f'{cb["code"]} · {cb["owner"]}')
        c.setFont('DejaVu', 9)
        c.drawRightString(x1, y, f'{"O‘zimizniki" if cb["own"] else "Tashqi (xizmat)"} · narx: {rate_txt}')
        y -= 5 * mm
        c.setFillColor(blue50)
        c.roundRect(x0, y - 11 * mm, x1 - x0, 11 * mm, 1.5 * mm, stroke=0, fill=1)
        c.setFillColor(navy)
        c.setFont('DejaVu-Bold', 8.5)
        c.drawString(x0 + 3 * mm, y - 4.3 * mm, 'Summa = hisob kg × narx. Pul punkt qabul qilgan paxtaga hisoblanadi, dala kg ga emas.')
        c.setFont('DejaVu', 8)
        c.drawString(x0 + 3 * mm, y - 8.5 * mm, 'Hisob kg — PQ-17 sof kg (namlik, ifloslik ayirilgan); PQ-17 kelmagan bo‘lsa punkt netto '
                                                '(taxminiy, sariq).')
        c.setFillColor(colors.black)
        return y - 15 * mm

    def boxes(y, items, h=15 * mm):
        n = len(items)
        gap = 3 * mm
        bw = (x1 - x0 - gap * (n - 1)) / n
        for i, (label, value, sub, col) in enumerate(items):
            bx = x0 + i * (bw + gap)
            c.setFillColor(col)
            c.roundRect(bx, y - h, bw, h, 2 * mm, stroke=0, fill=1)
            c.setFillColor(colors.HexColor('#5d6f86'))
            c.setFont('DejaVu', 7.5)
            c.drawString(bx + 3 * mm, y - 4.5 * mm, label)
            c.setFillColor(navy)
            c.setFont('DejaVu-Bold', 12.5)
            c.drawString(bx + 3 * mm, y - 10 * mm, value)
            if sub:
                c.setFillColor(colors.HexColor('#5d6f86'))
                c.setFont('DejaVu', 7)
                c.drawString(bx + 3 * mm, y - 13.4 * mm, sub)
            c.setFillColor(colors.black)
        return y - h - 4 * mm

    cols = [('Sana', 14, 'l'), ('Reys / nakladnoy', 40, 'l'), ('Dala', 20, 'l'), ('Dala kg', 17, 'r'),
            ('Punkt netto', 20, 'r'), ('PQ-17', 19, 'l'), ('Hisob kg', 22, 'r'), ('Summa, so‘m', 30, 'r')]
    xs, acc = [], x0
    for _, w, _a in cols:
        xs.append((acc, acc + w * mm))
        acc += w * mm

    def cell(i, y, text, bold=False, size=8, color=None):
        a = cols[i][2]
        lx, rx = xs[i]
        c.setFont('DejaVu-Bold' if bold else 'DejaVu', size)
        if color is not None:
            c.setFillColor(color)
        if a == 'l':
            c.drawString(lx + 1.5 * mm, y, text)
        else:
            c.drawRightString(rx - 1.5 * mm, y, text)
        c.setFillColor(colors.black)

    def table_head(y):
        c.setFillColor(navy)
        c.rect(x0, y - 7 * mm, x1 - x0, 7 * mm, stroke=0, fill=1)
        for i, (t, _w, _a) in enumerate(cols):
            cell(i, y - 4.8 * mm, t, bold=True, size=7.8, color=colors.white)
        return y - 7 * mm

    def row(y, vals, fill=None, bold=False, h=6.8 * mm, sub=None):
        if fill is not None:
            c.setFillColor(fill)
            c.rect(x0, y - h, x1 - x0, h, stroke=0, fill=1)
            c.setFillColor(colors.black)
        for i, v in enumerate(vals):
            cell(i, y - (4.2 if sub else 4.6) * mm, v, bold=bold)
        if sub:
            c.setFont('DejaVu', 6.5)
            c.setFillColor(colors.HexColor('#7a8aa0'))
            c.drawString(xs[1][0] + 1.5 * mm, y - 7.4 * mm, sub)
            c.setFillColor(colors.black)
        c.setStrokeColor(line_c)
        c.line(x0, y - h, x1, y - h)
        c.setStrokeColor(colors.black)
        return y - h

    def room(y, need, first_head=True):
        if y < need + 16 * mm:
            new_page()
            y = header(first=False)
            return table_head(y) if first_head else y
        return y

    def kg(v):
        return _num(v, 1 if v % 1 else 0) if v is not None else '—'

    y = header()
    y = boxes(y, [('Dala kg (ma’lumot)', kg(st['kg']), 'terim hisobchi yozgani', gray50),
                  ('Punkt netto', kg(st['punkt_kg']), f'{kg(st["waiting_kg"])} kg punktga yetmagan' if st['waiting_kg'] else 'punkt tarozisi', blue50),
                  ('Hisob kg', kg(st['pay_kg']), f'{kg(st["pq_wait_kg"])} kg PQ-17 kutilmoqda' if st['pq_wait_kg'] else 'PQ-17 sof kg', green50),
                  ('Hisoblangan', f'{_num(cb["earned"])} so‘m',
                   f'shundan taxminiy {_num(st["provisional_amount"])}' if st['provisional_amount'] else 'aniq', green50)])
    y = table_head(y)
    trips = st.get('trips') or []
    for t in trips:
        tall = bool(t['uy'])
        y = room(y, 9 * mm)
        waiting = t['basis'] == 'kutilmoqda'
        prov = t['basis'] == 'punkt'
        fill = gray50 if waiting else orange50 if prov else None
        ref = f'{_short_trip(t["trip_no"] or "")} · {t["waybill_no"] or "—"}'
        y = row(y, [_date(t['date'])[:5], ref, (t['field'] or '')[:14], kg(t['kg']),
                    'yo‘lda' if waiting else kg(t['punkt_kg']),
                    '—' if waiting else (t['pq_code'] or 'kutilmoqda'),
                    '—' if waiting else kg(t['pay_kg']) + ('*' if prov else ''),
                    '—' if waiting else (_num(t['tonnage_amount']) + ('*' if prov else '') if not t['uncalc_kg'] else 'tarifsiz')],
                fill=fill, h=9 * mm if tall else 6.8 * mm, sub=f'umumiy yuk {t["uy"]}' if tall else None)
    work_days = [d for d in st['days'] if d.get('work')]
    for d in work_days:
        y = room(y, 9 * mm)
        y = row(y, [_date(d['date'])[:5], f'ish hajmi: {d["work"]}', '', '', '', '', '', _num(d['amount'] - d['tonnage_amount'])])
    if not trips and not work_days:
        y = row(y, ['', 'Bu mavsumda terim yozilmagan', '', '', '', '', '', ''])
    y = room(y, 9 * mm)
    y = row(y, [f'JAMI', f'{len(trips)} ta reys', '', kg(st['kg']), kg(st['punkt_kg']), '', kg(st['pay_kg']), _num(cb['earned'])],
            fill=blue50, bold=True, h=7.5 * mm)

    # payments
    y -= 6 * mm
    y = room(y, 40 * mm, first_head=False)
    c.setFont('DejaVu-Bold', 10.5)
    c.drawString(x0, y, 'TO‘LOVLAR')
    y -= 3 * mm
    c.setStrokeColor(line_c)
    c.line(x0, y, x1, y)
    c.setStrokeColor(colors.black)
    c.setFont('DejaVu', 8.5)
    if st['payments']:
        for p in st['payments']:
            y -= 5.5 * mm
            y = room(y, 10 * mm, first_head=False)
            c.drawString(x0 + 1.5 * mm, y, _date(p['entry_date']))
            c.drawString(x0 + 28 * mm, y, p['doc_no'] or '')
            c.drawString(x0 + 62 * mm, y, (p['note'] or ('Qaytarildi' if p['amount'] < 0 else ''))[:60])
            c.drawRightString(x1 - 1.5 * mm, y, f'{_num(p["amount"])} so‘m')
    else:
        y -= 5.5 * mm
        c.setFillColor(colors.HexColor('#7a8aa0'))
        c.drawString(x0 + 1.5 * mm, y, 'To‘lov hali yo‘q.')
        c.setFillColor(colors.black)
    y -= 6 * mm
    y = room(y, 50 * mm, first_head=False)
    bal = cb['balance']
    per = st.get('period')
    items = [('Hisoblangan' + (' (davrda)' if per else ''), f'{_num(cb["earned"])} so‘m', None, blue50),
             ('To‘langan' + (' (davrda)' if per else ''), f'{_num(cb["paid"])} so‘m', None, gray50),
             (('Qoldiq (davr oxiriga)' if per else 'Qoldiq (to‘lanadi)') if bal >= 0 else 'Ortiqcha to‘langan',
              f'{_num(abs(bal))} so‘m', None, green50 if bal >= 0 else orange50)]
    if per:
        items.insert(0, ('Davr boshiga qoldiq', f'{_num(per["opening"])} so‘m', None, gray50))
    y = boxes(y, items, h=13 * mm)
    c.setFont('DejaVu', 7.5)
    c.setFillColor(colors.HexColor('#5d6f86'))
    notes = ['Umumiy yukda (UY) bir nechta telashka birga tortiladi: punkt nettosi va PQ-17 sof kg telashkalarga dala kg ulushiga qarab bo‘linadi.']
    held = (st.get('season') or cb).get('held') or 0
    if held > 0:
        notes.append(f'Hozir to‘lanadigan: {_num((st.get("season") or cb)["payable"])} so‘m. {_num(held)} so‘m PQ-17 kelguncha ushlab turiladi.')
    if st['provisional_amount']:
        notes.append(f'* PQ-17 hali kelmagan: {_num(st["provisional_amount"])} so‘m punkt netto bo‘yicha taxminiy, PQ-17 kelgach aniqlanadi.')
    if st['waiting_kg']:
        notes.append(f'Punktga hali yetmagan {kg(st["waiting_kg"])} kg — summaga kirmagan, qabul qilingach qo‘shiladi.')
    if st['uncalc_kg']:
        notes.append(f'{kg(st["uncalc_kg"])} kg uchun narx belgilanmagan — summaga kirmagan.')
    if st.get('photos'):
        notes.append('Ilova: har bir reysning dala blanki va punkt muhrlagan qog‘oz rasmlari (keyingi betlarda).')
    for n in notes:
        c.drawString(x0, y, n[:150])
        y -= 4 * mm
    c.setFillColor(colors.black)
    y -= 12 * mm
    y = room(y, 20 * mm, first_head=False)
    c.setFont('DejaVu', 9.5)
    c.drawString(x0, y, f'{company}:')
    c.drawString(x0 + 100 * mm, y, f'{cb["owner"]}:')
    y -= 10 * mm
    c.line(x0, y, x0 + 75 * mm, y)
    c.line(x0 + 100 * mm, y, x1, y)
    c.setFont('DejaVu', 7)
    c.drawString(x0, y - 3.5 * mm, 'imzo, F.I.Sh.')
    c.drawString(x0 + 100 * mm, y - 3.5 * mm, 'imzo, F.I.Sh.')

    # annex: the photos behind every kilogram
    photos = st.get('photos') or []
    if photos:
        cfg = current_app.config['SURXON']
        per_row, per_page = 3, 6
        gw = (x1 - x0 - 2 * 4 * mm) / per_row
        gh = 95 * mm
        for i, p in enumerate(photos):
            if i % per_page == 0:
                new_page()
                y = header(first=False)
                c.setFont('DejaVu-Bold', 11)
                c.drawString(x0, y, 'ILOVA: HUJJAT RASMLARI')
                y -= 6 * mm
                top = y
            k = i % per_page
            gx = x0 + (k % per_row) * (gw + 4 * mm)
            gy = top - (k // per_row) * (gh + 8 * mm)
            c.setStrokeColor(line_c)
            c.roundRect(gx, gy - gh, gw, gh, 1.5 * mm, stroke=1, fill=0)
            c.setStrokeColor(colors.black)
            got = _photo_reader(cfg, p['path']) or _photo_reader(cfg, p['thumb_path'])
            if got:
                img, (iw, ih) = got
                box_w, box_h = gw - 4 * mm, gh - 14 * mm
                s = min(box_w / iw, box_h / ih)
                dw, dh = iw * s, ih * s
                c.drawImage(img, gx + (gw - dw) / 2, gy - 2 * mm - box_h + (box_h - dh) / 2, width=dw, height=dh)
            else:
                c.setFont('DejaVu', 8)
                c.drawCentredString(gx + gw / 2, gy - gh / 2, 'rasm topilmadi')
            c.setFont('DejaVu-Bold', 7.8)
            c.drawString(gx + 2 * mm, gy - gh + 7 * mm,
                         f'{_short_trip(p["trip_no"] or "")} · {p["waybill_no"] or ""}' + (f' · {p["uy"]}' if p['uy'] else ''))
            c.setFont('DejaVu', 7.2)
            c.setFillColor(colors.HexColor('#5d6f86'))
            c.drawString(gx + 2 * mm, gy - gh + 3 * mm,
                         ('Punkt muhrlagan qog‘oz' if p['kind'] == 'punkt' else 'Dala blanki / telashka') + f' · {_date(p["uploaded_at"])[:10]}')
            c.setFillColor(colors.black)
    footer()
    c.save()
    return buf.getvalue()
