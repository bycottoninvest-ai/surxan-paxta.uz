"""Dalalar atlasi: every field on A4 for printing and for the office wall.

Page 1 (landscape) — all fields on the satellite picture, coloured by brigadier, with the code on each contour and a
legend (area per brigadier). Page 2… — the table of fields. Then one portrait page per field: a large satellite
picture with the contour, its area, brigadier, this season's harvest and yield, the centre coordinates, a scale bar
and a north arrow.

The satellite picture is the same free Esri imagery the web maps use (tiles fetched by the server, kept on disk so the
next print is quick). Without internet the contours are drawn on a plain grid — the PDF is still made.
"""
import io
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import current_app
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from . import geo
from .pdfdoc import _date, _fonts, _num

ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
TILE = 256
MAX_TILES = 64                       # per picture
MAP_H = 196 * mm                     # the map on a field's page
BRAND = colors.HexColor('#0b3d91')
INK = colors.HexColor('#0f172a')
MUTED = colors.HexColor('#64748b')
LINE = colors.HexColor('#e2e8f0')
PALETTE = ['#facc15', '#22d3ee', '#f472b6', '#a3e635', '#fb923c', '#c084fc', '#f87171', '#34d399', '#60a5fa', '#fde68a']
_state = {'dir': None, 'offline': False}     # set per PDF; the tile threads have no app context


# ------------------------------------------------------------------ web mercator + tiles

def _px(lat, lon, z):
    s = math.sin(math.radians(max(min(lat, 85), -85)))
    n = TILE * 2 ** z
    return (lon + 180) / 360 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def _tile(z, x, y):
    """One Esri tile (bytes) from the disk cache or the internet; None when it cannot be had."""
    base = _state['dir'] / str(z) / str(x)
    path = base / f'{y}.jpg'
    if path.exists() and path.stat().st_size > 500:
        return path.read_bytes()
    if _state['offline']:
        return None
    try:
        import requests
        r = requests.get(ESRI.format(z=z, x=x, y=y), timeout=8, headers={'User-Agent': 'surxan-paxta.uz atlas'})
        if r.status_code != 200 or not r.content or len(r.content) < 500:
            return None
        base.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        return r.content
    except Exception:
        _state['offline'] = True         # no internet: stop trying for the rest of this PDF
        return None


def _blank(data):
    """Esri's grey “Map data not yet available” tile (no picture at this zoom here)."""
    from PIL import Image, ImageStat
    try:
        st = ImageStat.Stat(Image.open(io.BytesIO(data)).convert('L'))
    except Exception:
        return True
    return 185 <= st.mean[0] <= 220 and st.stddev[0] < 30


class Frame:
    """A picture of a lat/lon box: the zoom, the image (or None) and lat/lon → picture fraction (0..1)."""

    def __init__(self, box, w_pt, h_pt, max_zoom=17, max_tiles=MAX_TILES):
        self.box, self.aspect, self.max_tiles, self.image = box, w_pt / h_pt, max_tiles, None
        z = max_zoom
        while z > 3 and not self._fit(z):     # the largest zoom that keeps the picture within max_tiles
            z -= 1
        self._fit(z, force=True)

    def _fit(self, z, force=False):
        lat0, lon0, lat1, lon1 = self.box
        x0, y0 = _px(lat1, lon0, z)
        x1, y1 = _px(lat0, lon1, z)
        w, h = x1 - x0, y1 - y0
        if w / h < self.aspect:           # widen the box to the page's shape
            w = h * self.aspect
        else:
            h = w / self.aspect
        if not force and math.ceil(w / TILE + 1) * math.ceil(h / TILE + 1) > self.max_tiles:
            return False
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.z, self.x0, self.y0, self.w, self.h = z, cx - w / 2, cy - h / 2, w, h
        return True

    def frac(self, lat, lon):
        x, y = _px(lat, lon, self.z)
        return (x - self.x0) / self.w, (y - self.y0) / self.h

    def metres_per_frac(self, lat):
        """Ground metres across the whole picture width."""
        return self.w * 156543.03392 * math.cos(math.radians(lat)) / 2 ** self.z

    def tiles(self):
        tx0, ty0 = int(self.x0 // TILE), int(self.y0 // TILE)
        tx1, ty1 = int((self.x0 + self.w) // TILE), int((self.y0 + self.h) // TILE)
        return [(self.z, x, y) for x in range(tx0, tx1 + 1) for y in range(ty0, ty1 + 1)]

    def compose(self, got):
        """Build the picture from fetched tiles. Returns the share of blank tiles (no imagery at this zoom)."""
        from PIL import Image
        keys = self.tiles()
        data = [got.get(k) for k in keys]
        if not any(data):
            return 0.0
        blank = sum(1 for d in data if d and _blank(d)) / len(keys)
        tx0, ty0 = keys[0][1], keys[0][2]
        tx1, ty1 = keys[-1][1], keys[-1][2]
        img = Image.new('RGB', ((tx1 - tx0 + 1) * TILE, (ty1 - ty0 + 1) * TILE), (40, 48, 40))
        for (z, x, y), d in zip(keys, data):
            if d:
                try:
                    img.paste(Image.open(io.BytesIO(d)).convert('RGB'), ((x - tx0) * TILE, (y - ty0) * TILE))
                except Exception:
                    pass
        left, top = self.x0 - tx0 * TILE, self.y0 - ty0 * TILE
        img = img.crop((int(left), int(top), int(left + self.w), int(top + self.h)))
        out = io.BytesIO()
        img.save(out, 'JPEG', quality=86)
        out.seek(0)
        self.image = ImageReader(out)
        return blank


def load_all(frames, pool):
    """Fetch every picture's tiles at once (in parallel, each tile once); where the imagery has no picture at that
    zoom (grey tiles), step one zoom out and try again."""
    todo = list(frames)
    for _ in range(4):
        keys = sorted({k for fr in todo for k in fr.tiles()})
        got = dict(zip(keys, pool.map(lambda k: _tile(*k), keys)))
        again = []
        for fr in todo:
            if fr.compose(got) > 0.25 and fr.z > 13:
                fr.image = None
                fr._fit(fr.z - 1, force=True)
                again.append(fr)
        if not again or _state['offline']:
            break
        todo = again


def _box(polys, pad=0.18, min_m=250):
    lats = [p[0] for poly in polys for p in poly]
    lons = [p[1] for poly in polys for p in poly]
    lat0, lat1, lon0, lon1 = min(lats), max(lats), min(lons), max(lons)
    mid = (lat0 + lat1) / 2
    dlat = max(lat1 - lat0, min_m / 111320)
    dlon = max(lon1 - lon0, min_m / (111320 * math.cos(math.radians(mid))))
    clat, clon = (lat0 + lat1) / 2, (lon0 + lon1) / 2
    return (clat - dlat * (0.5 + pad), clon - dlon * (0.5 + pad), clat + dlat * (0.5 + pad), clon + dlon * (0.5 + pad))


# ------------------------------------------------------------------ drawing helpers

def _map(c, fr, x, y, w, h, shapes, labels=True):
    """The picture in (x, y, w, h) and each contour on it. shapes: [(poly, colour, label, strong)]."""
    c.saveState()
    p = c.beginPath()
    p.roundRect(x, y, w, h, 3 * mm)
    c.clipPath(p, stroke=0, fill=0)
    if fr.image:
        c.drawImage(fr.image, x, y, w, h)
    else:                                               # no internet: a light grid
        c.setFillColor(colors.HexColor('#f1f5f9'))
        c.rect(x, y, w, h, stroke=0, fill=1)
        c.setStrokeColor(colors.HexColor('#dbe3ec'))
        c.setLineWidth(0.4)
        for i in range(1, 12):
            c.line(x + w * i / 12, y, x + w * i / 12, y + h)
            c.line(x, y + h * i / 12, x + w, y + h * i / 12)

    def pt(lat, lon):
        fx, fy = fr.frac(lat, lon)
        return x + fx * w, y + h - fy * h

    for poly, col, label, strong in shapes:
        path = c.beginPath()
        for i, (lat, lon) in enumerate(poly):
            (path.moveTo if i == 0 else path.lineTo)(*pt(lat, lon))
        path.close()
        c.setFillColor(colors.HexColor(col))
        c.setFillAlpha(0.28 if strong else 0.16)
        c.setStrokeColor(colors.HexColor(col))
        c.setStrokeAlpha(1)
        c.setLineWidth(2.4 if strong else 1.1)
        c.drawPath(path, stroke=1, fill=1)
    c.setFillAlpha(1)
    if labels:
        for poly, col, label, strong in shapes:
            if not label:
                continue
            lat = sum(p[0] for p in poly) / len(poly)
            lon = sum(p[1] for p in poly) / len(poly)
            px, py = pt(lat, lon)
            size = 13 if strong else 6.5
            c.setFont('DejaVu-Bold', size)
            tw = c.stringWidth(label, 'DejaVu-Bold', size)
            c.setFillColor(colors.Color(0, 0, 0, alpha=0.55))
            c.roundRect(px - tw / 2 - 2, py - size * 0.35 - 1.5, tw + 4, size + 2, 2, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.drawCentredString(px, py - size * 0.3 + 0.5, label)
    c.restoreState()
    c.setStrokeColor(LINE)
    c.setLineWidth(0.8)
    c.roundRect(x, y, w, h, 3 * mm, stroke=1, fill=0)


def _scale_bar(c, fr, x, y, w, lat):
    """A scale bar (bottom-left on the map) and a north arrow (top-right is drawn by the caller)."""
    total_m = fr.metres_per_frac(lat)
    target = total_m * 0.22
    nice = next(v for v in (10, 20, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000, 20000) if v >= target * 0.6)
    bw = w * nice / total_m
    c.saveState()
    c.setFillColor(colors.Color(1, 1, 1, alpha=0.88))
    c.roundRect(x, y, bw + 16 * mm, 9 * mm, 1.5 * mm, stroke=0, fill=1)
    c.setFillColor(INK)
    c.rect(x + 3 * mm, y + 2.5 * mm, bw / 2, 1.6 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setStrokeColor(INK)
    c.setLineWidth(0.5)
    c.rect(x + 3 * mm + bw / 2, y + 2.5 * mm, bw / 2, 1.6 * mm, stroke=1, fill=1)
    c.rect(x + 3 * mm, y + 2.5 * mm, bw, 1.6 * mm, stroke=1, fill=0)
    c.setFillColor(INK)
    c.setFont('DejaVu', 6.5)
    c.drawString(x + 3 * mm, y + 5.3 * mm, '0')
    c.drawRightString(x + 3 * mm + bw + 9 * mm, y + 5.3 * mm, f'{nice:g} m' if nice < 1000 else f'{nice / 1000:g} km')
    c.restoreState()


def _north(c, x, y):
    c.saveState()
    c.setFillColor(colors.Color(1, 1, 1, alpha=0.88))
    c.circle(x, y, 6 * mm, stroke=0, fill=1)
    c.setFillColor(INK)
    p = c.beginPath()
    p.moveTo(x, y + 4 * mm)
    p.lineTo(x - 2.2 * mm, y - 2.5 * mm)
    p.lineTo(x, y - 1.2 * mm)
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    c.setFillColor(MUTED)
    p = c.beginPath()
    p.moveTo(x, y + 4 * mm)
    p.lineTo(x + 2.2 * mm, y - 2.5 * mm)
    p.lineTo(x, y - 1.2 * mm)
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    c.setFillColor(INK)
    c.setFont('DejaVu-Bold', 6.5)
    c.drawCentredString(x, y - 5 * mm, 'N')
    c.restoreState()


def _header(c, W, H, title, sub, company, logo):
    c.setFillColor(BRAND)
    c.rect(0, H - 22 * mm, W, 22 * mm, stroke=0, fill=1)
    x = 12 * mm
    if logo:
        try:
            c.drawImage(logo, x, H - 18 * mm, 34 * mm, 14 * mm, preserveAspectRatio=True, mask='auto', anchor='w')
            x += 40 * mm
        except Exception:
            pass
    c.setFillColor(colors.white)
    c.setFont('DejaVu-Bold', 15)
    c.drawString(x, H - 11 * mm, title)
    c.setFont('DejaVu', 8.5)
    c.setFillColor(colors.HexColor('#cfe0ff'))
    c.drawString(x, H - 16.5 * mm, sub)
    c.setFont('DejaVu-Bold', 8)
    c.setFillColor(colors.white)
    c.drawRightString(W - 12 * mm, H - 11 * mm, company)


def _footer(c, W, page, printed):
    c.setFont('DejaVu', 7)
    c.setFillColor(MUTED)
    c.drawString(12 * mm, 8 * mm, f'Chop etildi: {printed} · Sun’iy yo‘ldosh surati: © Esri World Imagery (sana noma’lum, jonli emas)')
    c.drawRightString(W - 12 * mm, 8 * mm, f'{page}-bet')


def _stat(c, x, y, w, label, value, unit='', big=False):
    c.setFillColor(colors.HexColor('#f8fafc'))
    c.setStrokeColor(LINE)
    c.roundRect(x, y, w, 17 * mm, 2 * mm, stroke=1, fill=1)
    c.setFillColor(MUTED)
    c.setFont('DejaVu', 7)
    c.drawString(x + 3 * mm, y + 12 * mm, label)
    c.setFillColor(INK)
    c.setFont('DejaVu-Bold', 15 if big else 12)
    c.drawString(x + 3 * mm, y + 4.5 * mm, value)
    if unit:
        c.setFont('DejaVu', 8)
        c.setFillColor(MUTED)
        c.drawString(x + 3 * mm + c.stringWidth(value, 'DejaVu-Bold', 15 if big else 12) + 1.5 * mm, y + 4.5 * mm, unit)


def _perimeter_m(poly):
    out = 0.0
    for (a, b), (c_, d) in zip(poly, poly[1:] + poly[:1]):
        dy = (c_ - a) * 111320
        dx = (d - b) * 111320 * math.cos(math.radians((a + c_) / 2))
        out += math.hypot(dx, dy)
    return out


# ------------------------------------------------------------------ the document

def build_atlas_pdf(fields, *, company, year, printed, title_extra=''):
    """fields: rows with id, code, name, area_ha, brigadier_name, polygon_json, net_kg, internal_kg."""
    _fonts()
    _state.update(dir=Path(current_app.config['SURXON'].UPLOAD_DIR) / 'tiles', offline=False)
    rows = []
    for f in fields:
        try:
            poly = json.loads(f['polygon_json']) if f['polygon_json'] else None
        except ValueError:
            poly = None
        if poly and len(poly) >= 3 and isinstance(poly[0], (list, tuple)):
            poly = [[float(p[0]), float(p[1])] for p in poly]
        else:
            poly = None
        rows.append(dict(f, poly=poly))
    brigs = []
    for r in rows:
        b = r['brigadier_name'] or 'Brigadirsiz'
        if b not in brigs:
            brigs.append(b)
    colour = {b: PALETTE[i % len(PALETTE)] for i, b in enumerate(brigs)}
    logo = None
    try:
        p = Path(current_app.static_folder) / 'img' / 'logo-light.png'
        if p.exists():
            logo = ImageReader(str(p))
    except Exception:
        pass

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(A4))
    c.setTitle(f'Dalalar xaritasi {year}')
    c.setAuthor(company)
    page = 0
    with_poly = [r for r in rows if r['poly']]
    total_ha = sum(r['area_ha'] or 0 for r in rows)
    total_kg = sum(r['net_kg'] or 0 for r in rows)

    with ThreadPoolExecutor(24) as pool:
        # 1. all fields together
        W, H = landscape(A4)
        page += 1
        _header(c, W, H, f'Dalalar xaritasi · {year}{title_extra}', f'{len(rows)} ta dala · {_num(total_ha, 2)} ga · '
                f'terim (tarozi netto) {_num(total_kg)} kg', company, logo)
        mx, my, mw, mh = 12 * mm, 16 * mm, W - 88 * mm, H - 44 * mm
        if with_poly:
            fr = Frame(_box([r['poly'] for r in with_poly], pad=0.06), mw, mh, max_zoom=17)
            load_all([fr], pool)
            _map(c, fr, mx, my, mw, mh, [(r['poly'], colour[r['brigadier_name'] or 'Brigadirsiz'], r['code'], False)
                                          for r in with_poly])
            mid = sum(p[0] for r in with_poly for p in r['poly']) / sum(len(r['poly']) for r in with_poly)
            _scale_bar(c, fr, mx + 4 * mm, my + 4 * mm, mw, mid)
            _north(c, mx + mw - 10 * mm, my + mh - 10 * mm)
        else:
            c.setFont('DejaVu', 11)
            c.setFillColor(MUTED)
            c.drawString(mx, my + mh / 2, 'Dalalarning xaritadagi chegarasi hali kiritilmagan.')
        # legend
        lx, ly = W - 72 * mm, H - 32 * mm
        c.setFillColor(INK)
        c.setFont('DejaVu-Bold', 10)
        c.drawString(lx, ly, 'Brigadirlar')
        ly -= 7 * mm
        for b in brigs:
            rs = [r for r in rows if (r['brigadier_name'] or 'Brigadirsiz') == b]
            c.setFillColor(colors.HexColor(colour[b]))
            c.roundRect(lx, ly - 1 * mm, 5 * mm, 5 * mm, 1 * mm, stroke=0, fill=1)
            c.setFillColor(INK)
            c.setFont('DejaVu-Bold', 9)
            c.drawString(lx + 7 * mm, ly + 1.8 * mm, b)
            c.setFont('DejaVu', 7.5)
            c.setFillColor(MUTED)
            c.drawString(lx + 7 * mm, ly - 1.8 * mm, f'{len(rs)} ta dala · {_num(sum(r["area_ha"] or 0 for r in rs), 2)} ga · '
                                                      f'{_num(sum(r["net_kg"] or 0 for r in rs))} kg')
            ly -= 11 * mm
        ly -= 3 * mm
        for label, value in (('Jami dalalar', f'{len(rows)} ta'), ('Jami maydon', f'{_num(total_ha, 2)} ga'),
                             ('Terim (netto)', f'{_num(total_kg)} kg'),
                             ('O‘rtacha hosil', f'{_num(total_kg / total_ha) if total_ha else "—"} kg/ga'),
                             ('Xaritasi yo‘q', f'{len(rows) - len(with_poly)} ta')):
            c.setFont('DejaVu', 8)
            c.setFillColor(MUTED)
            c.drawString(lx, ly, label)
            c.setFont('DejaVu-Bold', 9)
            c.setFillColor(INK)
            c.drawRightString(W - 12 * mm, ly, value)
            c.setStrokeColor(LINE)
            c.line(lx, ly - 2 * mm, W - 12 * mm, ly - 2 * mm)
            ly -= 7 * mm
        _footer(c, W, page, printed)
        c.showPage()

        # 2. the table
        c.setPageSize(A4)
        W, H = A4
        cols = [('Kod', 16), ('Nomi', 44), ('Brigadir', 34), ('Maydon, ga', 22), ('Terim netto, kg', 28), ('kg/ga', 18), ('Xarita', 14)]
        i = 0
        while i < len(rows) or i == 0:
            page += 1
            _header(c, W, H, 'Dalalar ro‘yxati', f'{year} mavsumi · {len(rows)} ta dala · {_num(total_ha, 2)} ga', company, logo)
            y = H - 32 * mm
            x = 12 * mm
            c.setFillColor(colors.HexColor('#eef3fb'))
            c.rect(x, y - 2.5 * mm, W - 24 * mm, 7 * mm, stroke=0, fill=1)
            c.setFont('DejaVu-Bold', 7.5)
            c.setFillColor(INK)
            cx = x + 3.5 * mm
            for name, wmm in cols:
                c.drawString(cx, y, name)
                cx += wmm * mm
            y -= 7 * mm
            while i < len(rows) and y > 22 * mm:
                r = rows[i]
                if i % 2:
                    c.setFillColor(colors.HexColor('#f8fafc'))
                    c.rect(x, y - 2.3 * mm, W - 24 * mm, 6.4 * mm, stroke=0, fill=1)
                c.setFillColor(colors.HexColor(colour[r['brigadier_name'] or 'Brigadirsiz']))
                c.circle(x + 1.4 * mm, y + 0.9 * mm, 0.9 * mm, stroke=0, fill=1)
                vals = [r['code'], (r['name'] or '')[:30], (r['brigadier_name'] or '—')[:22], _num(r['area_ha'], 2),
                        _num(r['net_kg']), _num((r['net_kg'] or 0) / r['area_ha']) if r['area_ha'] and r['net_kg'] else '—',
                        'bor' if r['poly'] else 'yo‘q']
                cx = x + 3.5 * mm
                c.setFont('DejaVu', 8)
                c.setFillColor(INK)
                for (name, wmm), v in zip(cols, vals):
                    c.drawString(cx, y, str(v))
                    cx += wmm * mm
                y -= 6.4 * mm
                i += 1
            if i >= len(rows):
                c.setStrokeColor(INK)
                c.line(x, y + 3.5 * mm, W - 12 * mm, y + 3.5 * mm)
                c.setFont('DejaVu-Bold', 8)
                c.drawString(x + 2 * mm, y - 1 * mm, 'JAMI')
                c.drawString(x + 2 * mm + 94 * mm, y - 1 * mm, _num(total_ha, 2))
                c.drawString(x + 2 * mm + 116 * mm, y - 1 * mm, _num(total_kg))
                c.drawString(x + 2 * mm + 144 * mm, y - 1 * mm, _num(total_kg / total_ha) if total_ha else '—')
            _footer(c, W, page, printed)
            c.showPage()
            if not rows:
                break

        # 3. one page per field
        frames = {}
        for r in with_poly:
            frames[r['id']] = Frame(_box([r['poly']]), W - 24 * mm, MAP_H, max_zoom=17, max_tiles=30)
        load_all(list(frames.values()), pool)
        for r in with_poly:
            page += 1
            col = colour[r['brigadier_name'] or 'Brigadirsiz']
            _header(c, W, H, f'{r["code"]} · {r["name"] or ""}', f'Brigadir: {r["brigadier_name"] or "—"} · {year} mavsumi',
                    company, logo)
            fr = frames[r['id']]
            mx, my, mw, mh = 12 * mm, H - 28 * mm - MAP_H, W - 24 * mm, MAP_H
            others = [(o['poly'], '#ffffff', o['code'], False) for o in with_poly if o['id'] != r['id']]
            _map(c, fr, mx, my, mw, mh, others + [(r['poly'], col, r['code'], True)])
            lat = sum(p[0] for p in r['poly']) / len(r['poly'])
            lon = sum(p[1] for p in r['poly']) / len(r['poly'])
            _scale_bar(c, fr, mx + 4 * mm, my + 4 * mm, mw, lat)
            _north(c, mx + mw - 10 * mm, my + mh - 10 * mm)
            # numbers
            sw = (W - 24 * mm - 9 * mm) / 4
            y = my - 21 * mm
            map_ha = geo.poly_area_ha(r['poly'])
            _stat(c, 12 * mm, y, sw, 'Maydon (hisobda)', _num(r['area_ha'], 2), 'ga', big=True)
            _stat(c, 12 * mm + (sw + 3 * mm), y, sw, 'Xarita bo‘yicha', _num(map_ha, 2), 'ga')
            _stat(c, 12 * mm + 2 * (sw + 3 * mm), y, sw, 'Terim (tarozi netto)', _num(r['net_kg']), 'kg')
            _stat(c, 12 * mm + 3 * (sw + 3 * mm), y, sw, 'Hosildorlik',
                  _num((r['net_kg'] or 0) / r['area_ha']) if r['area_ha'] and r['net_kg'] else '—', 'kg/ga')
            y -= 9 * mm
            c.setFont('DejaVu', 8)
            c.setFillColor(MUTED)
            c.drawString(12 * mm, y, f'Markaz: {lat:.6f}, {lon:.6f}   ·   Perimetr: {_num(_perimeter_m(r["poly"]))} m   ·   '
                                     f'Burchaklar: {len(r["poly"])} ta   ·   Dala ichidagi terim: {_num(r.get("internal_kg"))} kg')
            if map_ha and r['area_ha'] and abs(map_ha - r['area_ha']) / r['area_ha'] > 0.05:
                y -= 5 * mm
                c.setFillColor(colors.HexColor('#b45309'))
                c.drawString(12 * mm, y, f'Diqqat: xaritadagi maydon hisobdagidan {_num(map_ha - r["area_ha"], 2)} ga farq qiladi — '
                                         'kontur yoki maydonni tekshiring.')
            _footer(c, W, page, printed)
            c.showPage()
    c.save()
    return buf.getvalue()
