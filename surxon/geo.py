"""Field contours: read KML / GeoJSON, check them, and compute their area on the WGS84 ellipsoid.

Coordinates are kept exactly as given (no rounding, no simplification). GeoJSON / KML order is [longitude, latitude];
the map (Leaflet) and the fields table use [latitude, longitude] — the swap happens only here.
The area is geodesic (local equal-area projection per contour, WGS84); it is labelled “xaritadan hisoblangan”, never
presented as the confirmed working area.
"""
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET

A, F = 6378137.0, 1 / 298.257223563
E2 = F * (2 - F)


def ring_area_m2(ring_lonlat):
    """Area of a closed ring (lon, lat in degrees) in m², on the WGS84 ellipsoid.
    Sinusoidal-type equal-area projection around the ring's mean latitude, with the ellipsoid's radii of curvature."""
    pts = ring_lonlat[:-1] if ring_lonlat[0] == ring_lonlat[-1] else ring_lonlat
    if len(pts) < 3:
        return 0.0
    lat0 = math.radians(sum(p[1] for p in pts) / len(pts))
    lon0 = math.radians(sum(p[0] for p in pts) / len(pts))
    m0 = A * (1 - E2) / (1 - E2 * math.sin(lat0) ** 2) ** 1.5          # meridian radius of curvature

    def xy(p):
        lat, lon = math.radians(p[1]), math.radians(p[0])
        n = A / math.sqrt(1 - E2 * math.sin(lat) ** 2)                  # prime-vertical radius at this point
        return (lon - lon0) * n * math.cos(lat), (lat - lat0) * m0
    xs = [xy(p) for p in pts]
    s = 0.0
    for i in range(len(xs)):
        x1, y1 = xs[i]
        x2, y2 = xs[(i + 1) % len(xs)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def _segments_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-18 else (1 if v > 0 else -1)
    o1, o2, o3, o4 = orient(p1, p2, p3), orient(p1, p2, p4), orient(p3, p4, p1), orient(p3, p4, p2)
    return o1 != o2 and o3 != o4 and 0 not in (o1, o2, o3, o4)


def check_ring(ring):
    """Problems with one outer ring ([] = fine)."""
    out = []
    if len(ring) < 4:
        out.append('kamida 3 ta nuqta kerak')
        return out
    if ring[0] != ring[-1]:
        out.append('kontur yopilmagan (birinchi va oxirgi nuqta bir xil emas)')
    for lon, lat in ring:
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            out.append('koordinata chegaradan tashqari (uzunlik/kenglik almashganmi?)')
            break
    pts = ring if ring[0] == ring[-1] else ring + [ring[0]]
    n = len(pts) - 1
    if n <= 400:
        for i in range(n):
            for j in range(i + 2, n):
                if i == 0 and j == n - 1:
                    continue
                if _segments_cross(pts[i], pts[i + 1], pts[j], pts[j + 1]):
                    out.append('kontur o‘zini kesib o‘tadi')
                    return out
    return out


def _num(text):
    m = re.search(r'\d+', text or '')
    return int(m.group(0)) if m else None


def _feature(i, name, source_id, rings, props=None):
    outer = [[float(c[0]), float(c[1])] for c in rings[0]] if rings else []
    problems = check_ring(outer) if outer else ['kontur topilmadi']
    if len(rings) > 1:
        problems.append(f'{len(rings) - 1} ta ichki teshik bor — maydondan ayrilgan, xaritada faqat tashqi chegara')
    area = ring_area_m2(outer) if outer and not any('yopilmagan' in p or 'nuqta' in p for p in problems) else 0.0
    for inner in rings[1:]:
        area -= ring_area_m2([[float(c[0]), float(c[1])] for c in inner])
    n = _num(name)
    return {'i': i, 'source_id': source_id or f'IMP-{i:03d}', 'name': (name or f'Kontur {i}').strip(),
            'code': f'D-{n:02d}' if n is not None else f'D-{i:02d}',
            'map_ha': round(area / 10000, 4), 'points': len(outer), 'poly': [[c[1], c[0]] for c in outer],
            'problems': [p for p in problems if 'ichki teshik' not in p], 'notes': [p for p in problems if 'ichki teshik' in p],
            'stated_ha': (props or {}).get('kml_ga')}


def parse(data, filename=''):
    """KML or GeoJSON bytes → list of contours. Raises ValueError with a readable reason."""
    text = data.decode('utf-8-sig', errors='replace').strip()
    if text.startswith('{'):
        try:
            gj = json.loads(text)
        except ValueError:
            raise ValueError('GeoJSON faylni o‘qib bo‘lmadi.')
        feats = gj.get('features') if gj.get('type') == 'FeatureCollection' else [gj]
        out = []
        for i, f in enumerate(feats or [], 1):
            g, p = f.get('geometry') or {}, f.get('properties') or {}
            name = p.get('nomi') or p.get('name') or p.get('Name')
            if g.get('type') != 'Polygon':
                out.append({'i': i, 'source_id': p.get('source_id') or f'IMP-{i:03d}', 'name': name or f'Kontur {i}',
                            'code': '', 'map_ha': 0, 'points': 0, 'poly': [], 'notes': [], 'stated_ha': None,
                            'problems': [f'{g.get("type") or "geometriya yo‘q"} — faqat Polygon qabul qilinadi (alohida ko‘rib chiqing)']})
                continue
            out.append(_feature(i, name, p.get('source_id'), g.get('coordinates') or [], p))
        return out
    if '<kml' in text[:500].lower() or text.startswith('<?xml'):
        try:
            root = ET.fromstring(text.encode('utf-8'))
        except ET.ParseError:
            raise ValueError('KML faylni o‘qib bo‘lmadi.')
        ns = {'k': root.tag.split('}')[0].strip('{')} if root.tag.startswith('{') else {'k': ''}
        pre = 'k:' if ns['k'] else ''
        out = []
        for i, pm in enumerate(root.iter(f'{{{ns["k"]}}}Placemark' if ns['k'] else 'Placemark'), 1):
            name_el = pm.find(f'{pre}name', ns)
            name = name_el.text if name_el is not None else None
            polys = pm.findall(f'.//{pre}Polygon', ns)
            if len(polys) != 1:
                out.append({'i': i, 'source_id': f'KML-{i:03d}', 'name': name or f'Kontur {i}', 'code': '', 'map_ha': 0,
                            'points': 0, 'poly': [], 'notes': [], 'stated_ha': None,
                            'problems': ['Polygon topilmadi' if not polys else f'{len(polys)} ta poligon (MultiGeometry) — alohida ko‘rib chiqing']})
                continue

            def ring(el):
                coords = (el.text or '').split() if el is not None else []
                return [[float(c.split(',')[0]), float(c.split(',')[1])] for c in coords if ',' in c]
            outer = ring(polys[0].find(f'{pre}outerBoundaryIs/{pre}LinearRing/{pre}coordinates', ns))
            inners = [ring(x) for x in polys[0].findall(f'{pre}innerBoundaryIs/{pre}LinearRing/{pre}coordinates', ns)]
            out.append(_feature(i, name, f'KML-{i:03d}', [outer] + inners))
        return out
    raise ValueError('Fayl KML yoki GeoJSON emas.')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def poly_area_ha(poly_latlon):
    """Area of a stored [lat, lon] contour (the fields table order)."""
    if not poly_latlon or len(poly_latlon) < 3:
        return None
    ring = [[p[1], p[0]] for p in poly_latlon]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return round(ring_area_m2(ring) / 10000, 4)


def field_grid(poly_latlon):
    """Split a stored [lat, lon] contour into ~60 square cells (20–60 m) used to mark which part of the field a trip
    picked. Cell ids ("i:j") depend only on the contour, so marks from different trips / rounds line up.
    Returns (cells, cell_size_m): cells = [{'id', 'poly': [[lat, lon] x4]}] whose centre lies inside the field."""
    if not poly_latlon or len(poly_latlon) < 3:
        return [], 0
    lat0 = sum(p[0] for p in poly_latlon) / len(poly_latlon)
    kx, ky = 111320 * math.cos(math.radians(lat0)), 110540.0
    xs = [p[1] * kx for p in poly_latlon]
    ys = [p[0] * ky for p in poly_latlon]
    ring = [[p[1], p[0]] for p in poly_latlon]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    area = ring_area_m2(ring)
    size = max(20.0, min(60.0, math.sqrt(area / 60))) if area else 50.0
    size = round(size / 5) * 5
    x0, y0 = math.floor(min(xs) / size) * size, math.floor(min(ys) / size) * size
    poly_xy = list(zip(xs, ys))

    def inside(x, y):
        c = False
        for k in range(len(poly_xy)):
            (xi, yi), (xj, yj) = poly_xy[k], poly_xy[k - 1]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                c = not c
        return c
    cells = []
    nx, ny = int((max(xs) - x0) // size) + 1, int((max(ys) - y0) // size) + 1
    for i in range(nx):
        for j in range(ny):
            cx, cy = x0 + (i + .5) * size, y0 + (j + .5) * size
            if inside(cx, cy):
                a, b = x0 + i * size, y0 + j * size
                cells.append({'id': f'{i}:{j}', 'poly': [[round(y / ky, 7), round(x / kx, 7)] for x, y in
                                                         ((a, b), (a + size, b), (a + size, b + size), (a, b + size))]})
    if not cells:   # a very thin field: one cell = the whole field
        cells = [{'id': '0:0', 'poly': [[p[0], p[1]] for p in poly_latlon]}]
    return cells, size


def cell_of(poly_latlon, lat, lon):
    """The grid cell id a point falls in (or None when outside every cell)."""
    cells, _ = field_grid(poly_latlon)
    for c in cells:
        p = c['poly']
        if len(p) == 4 and min(x[0] for x in p) <= lat <= max(x[0] for x in p) and min(x[1] for x in p) <= lon <= max(x[1] for x in p):
            return c['id']
    return None


def grid_locator(poly_latlon):
    """Fast point → grid cell id for field_grid's cells (same frame: size, origin), or None outside the grid."""
    cells, size = field_grid(poly_latlon)
    if not cells:
        return lambda lat, lon: None
    lat0 = sum(p[0] for p in poly_latlon) / len(poly_latlon)
    kx, ky = 111320 * math.cos(math.radians(lat0)), 110540.0
    if len(cells) == 1 and cells[0]['id'] == '0:0' and size:
        ys = [p[0] for p in poly_latlon]
        xs = [p[1] for p in poly_latlon]
        return lambda lat, lon: '0:0' if min(ys) <= lat <= max(ys) and min(xs) <= lon <= max(xs) else None
    x0 = math.floor(min(p[1] * kx for p in poly_latlon) / size) * size
    y0 = math.floor(min(p[0] * ky for p in poly_latlon) / size) * size
    ids = {c['id'] for c in cells}

    def locate(lat, lon):
        cid = f'{int((lon * kx - x0) // size)}:{int((lat * ky - y0) // size)}'
        return cid if cid in ids else None
    return locate
