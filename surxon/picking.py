"""Which part of which field(s) each trip picked, per harvest round (1st / 2nd / 3rd picking).

Every field is split into ~60 grid cells (geo.field_grid). When a trip is closed the tally marks the cells that were
picked — tap on the map or walk with the phone; the cells of the trip's weighing positions are pre-marked. Cells of
neighbouring fields are shown too: a trailer is often filled across two adjacent fields, and then the trip's kg and
pay are split between the fields in proportion to the hectares marked in each (e.g. D-16 80 %, D-17 20 %).
Cell keys are stored as "field_id|i:j".
  - hectares of a field part = marked cells × (field area / number of cells of that field)
  - c/ha of a trip           = trip kg / 100 / marked hectares
  - per field and round      = kg shares of the round's trips, distinct marked cells → hectares, c/ha
"""
import json
import math

from . import geo
from .db import get_db, q, tx
from .security import audit
from .utils import UserError

ROUNDS = (1, 2, 3)
NEAR_M = 400          # neighbouring fields shown on the marking map


def _season():
    from .services import current_season
    return current_season(get_db())


def field_row(field_id, year=None):
    year = year or _season()
    return q('''SELECT f.id, f.code, f.name, f.polygon_json, COALESCE(fs.area_ha, f.area_ha) area_ha
                FROM fields f LEFT JOIN field_seasons fs ON fs.field_id=f.id AND fs.year=? WHERE f.id=?''',
             (year, field_id), one=True)


_grid_cache = {}


def grid(field):
    """(cells, cell_ha) for a field row; cells=[] when the field has no contour."""
    if not field or not field['polygon_json']:
        return [], 0
    key = (field['id'], field['polygon_json'][:64], len(field['polygon_json']))
    if key not in _grid_cache:
        _grid_cache[key] = geo.field_grid(json.loads(field['polygon_json']))[0]
    cells = _grid_cache[key]
    return cells, ((field['area_ha'] or 0) / len(cells) if cells else 0)


def _key(fid, cid):
    return f'{fid}|{cid}'


def parse_key(key, main_fid):
    """"12|3:4" → (12, "3:4"); an old plain "3:4" belongs to the trip's own field."""
    if '|' in key:
        f, c = key.split('|', 1)
        return int(f), c
    return main_fid, key


def neighbours(field, year):
    """Fields whose contour lies within NEAR_M metres of this one (bounding boxes), the field itself first."""
    poly = json.loads(field['polygon_json'])
    lat0 = sum(p[0] for p in poly) / len(poly)
    dy, dx = NEAR_M / 110540, NEAR_M / (111320 * math.cos(math.radians(lat0)))
    box = (min(p[0] for p in poly) - dy, max(p[0] for p in poly) + dy, min(p[1] for p in poly) - dx, max(p[1] for p in poly) + dx)
    out = [field]
    for f in q('''SELECT f.id, f.code, f.name, f.polygon_json, COALESCE(fs.area_ha, f.area_ha) area_ha
                  FROM fields f LEFT JOIN field_seasons fs ON fs.field_id=f.id AND fs.year=?
                  WHERE f.active=1 AND f.polygon_json IS NOT NULL AND f.id<>?''', (year, field['id'])):
        pp = json.loads(f['polygon_json'])
        if any(box[0] <= p[0] <= box[1] and box[2] <= p[1] <= box[3] for p in pp):
            out.append(f)
    return out


def default_round(field_id, year):
    r = q('''SELECT MAX(harvest_round) FROM trailer_loads WHERE field_id=? AND season_year=? AND status<>'BEKOR'
             AND harvest_round IS NOT NULL''', (field_id, year), one=True)
    return (r[0] if r and r[0] else 1)


def _marked(year, rnd=None, exclude_load=None):
    """{field_id: set(cell ids)} marked by not-cancelled trips of the season (optionally one round)."""
    out = {}
    sql = '''SELECT id, field_id, picked_cells FROM trailer_loads WHERE season_year=? AND status<>'BEKOR'
             AND picked_cells IS NOT NULL'''
    params = [year]
    if rnd:
        sql += ' AND COALESCE(harvest_round,1)=?'
        params.append(rnd)
    for r in q(sql, params):
        if r['id'] == exclude_load:
            continue
        for k in json.loads(r['picked_cells']):
            fid, cid = parse_key(k, r['field_id'])
            out.setdefault(fid, set()).add(cid)
    return out


def suggested_cells(load_id, fields):
    """Cells (keys) where this trip's weighings were recorded, in any of the given fields."""
    pts = q('SELECT DISTINCT ROUND(lat,5) lat, ROUND(lon,5) lon FROM harvests WHERE load_id=? AND voided_at IS NULL '
            'AND lat IS NOT NULL', (load_id,))
    out = []
    for f in fields:
        poly = json.loads(f['polygon_json']) if f['polygon_json'] else None
        if not poly:
            continue
        for h in pts:
            c = geo.cell_of(poly, h['lat'], h['lon'])
            if c and _key(f['id'], c) not in out:
                out.append(_key(f['id'], c))
    return out


def save_cells(actor, load_id, keys, rnd=None):
    """Store the marked cells of one trip, the hectares they cover and the split between fields."""
    with tx() as db:
        ld = db.execute('SELECT * FROM trailer_loads WHERE id=?', (load_id,)).fetchone()
        if not ld or ld['status'] == 'BEKOR':
            raise UserError('Reys topilmadi yoki bekor qilingan.')
        if rnd is not None and rnd not in ROUNDS:
            raise UserError('Terim 1, 2 yoki 3 bo‘lishi mumkin.')
        grids, chosen, split = {}, [], {}
        for k in keys:
            fid, cid = parse_key(k, ld['field_id'])
            if fid not in grids:
                grids[fid] = grid(field_row(fid, ld['season_year']))
            cells, cell_ha = grids[fid]
            if cid in {c['id'] for c in cells} and _key(fid, cid) not in chosen:
                chosen.append(_key(fid, cid))
                split[fid] = split.get(fid, 0) + cell_ha
        ha = round(sum(split.values()), 3) if chosen else None
        split = {str(f): round(v, 4) for f, v in split.items()} if len(split) > 1 else None
        db.execute('''UPDATE trailer_loads SET picked_cells=?, picked_ha=?, picked_split=?, harvest_round=COALESCE(?, harvest_round, 1)
                      WHERE id=?''', (json.dumps(sorted(chosen)) if chosen else None, ha, json.dumps(split) if split else None,
                                      rnd, load_id))
        audit(db, actor, 'UPDATE', 'trailer_load', load_id,
              old={'picked_ha': ld['picked_ha'], 'harvest_round': ld['harvest_round']},
              new={'picked_ha': ha, 'cells': len(chosen), 'split': split, 'harvest_round': rnd or ld['harvest_round'] or 1})
        return ha


def shares(trip):
    """{field_id: share 0..1} of a trip row (picked_split / field_id)."""
    if trip['picked_split']:
        sp = {int(k): v for k, v in json.loads(trip['picked_split']).items()}
        tot = sum(sp.values())
        if tot:
            return {f: v / tot for f, v in sp.items()}
    return {trip['field_id']: 1.0}


def trip_yield(load_id):
    """kg, hectares, c/ha of one trip and its split between fields."""
    r = q('''SELECT tl.id, tl.field_id, tl.picked_ha, tl.picked_split,
                    (SELECT COALESCE(SUM(kg),0) FROM harvests h WHERE h.load_id=tl.id AND h.voided_at IS NULL) kg
             FROM trailer_loads tl WHERE tl.id=?''', (load_id,), one=True)
    if not r:
        return None
    parts = []
    if r['picked_split']:
        for fid, sh in sorted(shares(r).items(), key=lambda x: -x[1]):
            f = field_row(fid)
            parts.append({'code': f['code'] if f else '?', 'share': round(sh * 100), 'kg': round(r['kg'] * sh)})
    cha = round(r['kg'] / 100 / r['picked_ha'], 1) if r['picked_ha'] else None
    return {'kg': r['kg'], 'ha': r['picked_ha'], 'cha': cha, 'parts': parts}


def season_report(year, with_money=False):
    """Per field: area; per round kg (trip kg split by marked hectares), hectares (distinct marked cells), c/ha;
    season total kg, c/ha of the whole field and (optionally) the pickers' pay attributed to the field."""
    fields = q('''SELECT f.id, f.code, f.name, f.polygon_json, COALESCE(fs.area_ha, f.area_ha) area_ha, b.name brigadier
                  FROM fields f LEFT JOIN field_seasons fs ON fs.field_id=f.id AND fs.year=?
                  LEFT JOIN brigadiers b ON b.id=f.brigadier_id
                  WHERE f.active=1 OR fs.field_id IS NOT NULL ORDER BY f.code''', (year,))
    kg, trips, pay = {}, {}, {}
    for t in q('''SELECT tl.id, tl.field_id, tl.picked_split, COALESCE(tl.harvest_round,1) rnd,
                         COALESCE(SUM(h.kg),0) kg, COALESCE(SUM(h.amount),0) amount
                  FROM trailer_loads tl JOIN harvests h ON h.load_id=tl.id AND h.voided_at IS NULL
                  WHERE tl.season_year=? AND tl.status<>'BEKOR' GROUP BY tl.id''', (year,)):
        for fid, sh in shares(t).items():
            kg[(fid, t['rnd'])] = kg.get((fid, t['rnd']), 0) + t['kg'] * sh
            trips[(fid, t['rnd'])] = trips.get((fid, t['rnd']), 0) + 1
            pay[fid] = pay.get(fid, 0) + t['amount'] * sh
    by_round = {r: _marked(year, r) for r in ROUNDS}
    out = []
    for f in fields:
        cells, cell_ha = grid(f)
        rounds, total = [], 0
        for rnd in ROUNDS:
            k = round(kg.get((f['id'], rnd), 0))
            marked = by_round[rnd].get(f['id'], set())
            ha = round(len(marked) * cell_ha, 2) if marked else None
            total += k
            rounds.append({'round': rnd, 'kg': k, 'trips': trips.get((f['id'], rnd), 0), 'ha': ha,
                           'cover': round(len(marked) / len(cells) * 100) if cells and marked else None,
                           'cha': round(k / 100 / ha, 1) if ha and k else None})
        if not total and not any(r['ha'] for r in rounds):
            continue
        row = {'id': f['id'], 'code': f['code'], 'name': f['name'], 'brigadier': f['brigadier'], 'area_ha': f['area_ha'],
               'rounds': rounds, 'kg': total, 'cha': round(total / 100 / f['area_ha'], 1) if f['area_ha'] and total else None}
        if with_money:
            row['pay'] = round(pay.get(f['id'], 0))
        out.append(row)
    return out


def season_cells(year, day=None):
    """Map layer: every marked cell with its round (and whether it was marked today)."""
    out, grids = [], {}
    for r in q('''SELECT tl.id, tl.field_id, tl.load_date, COALESCE(tl.harvest_round,1) rnd, tl.picked_cells, tl.trip_no
                  FROM trailer_loads tl WHERE tl.season_year=? AND tl.status<>'BEKOR' AND tl.picked_cells IS NOT NULL''', (year,)):
        for k in json.loads(r['picked_cells']):
            fid, cid = parse_key(k, r['field_id'])
            if fid not in grids:
                grids[fid] = {c['id']: c['poly'] for c in grid(field_row(fid, year))[0]}
            if cid in grids[fid]:
                out.append({'poly': grids[fid][cid], 'round': r['rnd'], 'today': r['load_date'] == day,
                            'trip': r['trip_no'], 'lid': r['id']})
    return out


def pick_context(ld, trip_kg):
    """Everything the marking map needs: the trip's field and its neighbours (cells keyed "fid|id"),
    cells other trips of this round already marked, and the current / suggested marks."""
    field = field_row(ld['field_id'], ld['season_year'])
    if not field or not field['polygon_json']:
        return None
    row = q('SELECT harvest_round, picked_cells FROM trailer_loads WHERE id=?', (ld['id'],), one=True)
    rnd = row['harvest_round'] or 1
    fields = neighbours(field, ld['season_year'])
    done = _marked(ld['season_year'], rnd, exclude_load=ld['id'])
    data = []
    for f in fields:
        cells, cell_ha = grid(f)
        if not cells:
            continue
        data.append({'id': f['id'], 'code': f['code'], 'label': f"{f['code']} · {f['name']}", 'main': f['id'] == field['id'],
                     'poly': json.loads(f['polygon_json']), 'cell_ha': cell_ha, 'area': f['area_ha'], 'n': len(cells),
                     'cells': [{'k': _key(f['id'], c['id']), 'poly': c['poly']} for c in cells],
                     'done': [_key(f['id'], c) for c in done.get(f['id'], ())]})
    if row['picked_cells']:
        chosen = [_key(*parse_key(k, field['id'])) for k in json.loads(row['picked_cells'])]
    else:
        chosen = suggested_cells(ld['id'], fields)
    return {'fields': data, 'round': rnd, 'chosen': chosen, 'kg': trip_kg, 'label': f"{field['code']} · {field['name']}"}
