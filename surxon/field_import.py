"""Import field contours (KML / GeoJSON): upload → review on the map → names, brigades, areas → confirm.

Nothing reaches the fields table before the confirm. Re-importing the same contours finds them by source_id and
updates them (no duplicates); a field keeps its id, so trips and harvests written on it stay linked when it is renamed.
A computed map area is stored apart (map_area_ha) and marked “xarita” until a person confirms a working area; an area
someone typed earlier is never overwritten by the map. The brigade change is recorded with its date; past trips keep
the brigade they were written with.
"""
import json

from . import geo
from .db import get_db, q, tx
from .security import audit
from .utils import UserError, clean_text, now_str, today_str

CROPS = ['Paxta', 'Bug‘doy', 'Boshqa']


def _need(actor):
    if not actor.can('masterdata.write'):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')


def _match(db, row):
    """Which existing field this contour is (by source id, else a same-code field that has no contour yet)."""
    f = db.execute('SELECT * FROM fields WHERE source_id=?', (row['source_id'],)).fetchone()
    if f:
        return f, 'update'
    f = db.execute('SELECT * FROM fields WHERE code=? COLLATE NOCASE', (row['code'],)).fetchone()
    if f and not f['source_id'] and not f['polygon_json']:
        return f, 'link'
    if f:
        return f, 'code_taken'
    return None, 'new'


def create_import(actor, data, filename=''):
    _need(actor)
    if not data:
        raise UserError('Fayl tanlang (KML yoki GeoJSON).')
    try:
        rows = geo.parse(data, filename)
    except ValueError as e:
        raise UserError(str(e))
    if not rows:
        raise UserError('Faylda kontur topilmadi.')
    with tx() as db:
        seen = {}
        for r in rows:
            f, how = _match(db, r)
            r['match'] = how
            r['match_id'] = f['id'] if f else None
            r['match_code'] = f['code'] if f else None
            r['match_name'] = f['name'] if f else None
            r['current_brigadier_id'] = f['brigadier_id'] if f else None
            r['current_area'] = f['area_ha'] if f else None
            r['current_area_source'] = (f['area_source'] or 'qo‘lda') if f else None
            if how == 'code_taken':
                r['code'] = ''
                r['notes'].append(f'{f["code"]} kodi boshqa dalada band — yangi kod yozing')
            if r['source_id'] in seen:
                r['problems'].append(f'manba raqami takrorlangan ({r["source_id"]})')
            seen[r['source_id']] = True
        total = round(sum(r['map_ha'] for r in rows if not r['problems']), 4)
        iid = db.execute('''INSERT INTO field_imports(filename, sha256, count, map_total_ha, data_json, created_by, created_at)
                            VALUES (?,?,?,?,?,?,?)''', (clean_text(filename, 120), geo.sha256(data), len(rows), total,
                                                        json.dumps(rows, ensure_ascii=False), actor.user_id, now_str())).lastrowid
        audit(db, actor, 'UPLOAD', 'field_import', iid, new={'file': filename, 'count': len(rows), 'map_total_ha': total})
        return iid


def get_import(iid):
    r = q('SELECT i.*, u.full_name by_name FROM field_imports i LEFT JOIN users u ON u.id=i.created_by WHERE i.id=?',
          (iid,), one=True)
    if not r:
        return None, []
    return r, json.loads(r['data_json'])


def _num(v, label):
    v = (v or '').strip().replace(' ', '').replace(',', '.')
    if not v:
        return None
    try:
        x = float(v)
    except ValueError:
        raise UserError(f'{label}: son kiriting.')
    if x <= 0 or x > 5000:
        raise UserError(f'{label}: 0 dan katta va real bo‘lsin.')
    return round(x, 4)


def confirm_import(actor, iid, choices, year):
    """choices: {i: {'take', 'code', 'name', 'brigadier_id', 'confirmed_ha', 'crop', 'active'}}. One transaction."""
    _need(actor)
    with tx() as db:
        imp = db.execute('SELECT * FROM field_imports WHERE id=?', (iid,)).fetchone()
        if not imp:
            raise UserError('Import topilmadi.')
        if imp['status'] == 'SAQLANDI':
            return json.loads(imp['result_json'] or '{}')
        if imp['status'] != 'KORIB_CHIQISH':
            raise UserError('Bu import bekor qilingan.')
        rows = {r['i']: r for r in json.loads(imp['data_json'])}
        picked = [(i, c) for i, c in choices.items() if c.get('take') and i in rows]
        if not picked:
            raise UserError('Hech bir dala tanlanmagan.')
        codes = {}
        errors = []
        for i, c in picked:
            r = rows[i]
            if r['problems']:
                errors.append(f'{r["name"]}: {"; ".join(r["problems"])}')
                continue
            code, name = clean_text(c.get('code'), 20).upper(), clean_text(c.get('name'), 80)
            if not code or not name:
                errors.append(f'{r["source_id"]}: kod va nom majburiy')
            if code in codes:
                errors.append(f'{code}: bir importda ikki marta ({codes[code]} va {r["source_id"]})')
            codes[code] = r['source_id']
        if errors:
            raise UserError('Saqlanmadi — tuzating: ' + ' | '.join(errors[:8]) + (' …' if len(errors) > 8 else ''))
        created = updated = 0
        ts, today = now_str(), today_str()
        for i, c in picked:
            r = rows[i]
            code, name = clean_text(c['code'], 20).upper(), clean_text(c['name'], 80)
            brig = int(c['brigadier_id']) if c.get('brigadier_id') else None
            confirmed = _num(c.get('confirmed_ha'), f'{code} tasdiqlangan maydon')
            crop = c.get('crop') if c.get('crop') in CROPS else None
            active = 1 if c.get('active') else 0
            poly = json.dumps(r['poly'])
            f, how = _match(db, r)
            other = db.execute('SELECT id FROM fields WHERE code=? COLLATE NOCASE', (code,)).fetchone()
            if other and (not f or other['id'] != f['id'] or how == 'code_taken'):
                raise UserError(f'{code} kodi boshqa dalada band — boshqa kod yozing.')
            if f and how in ('update', 'link'):
                if confirmed:
                    area, source = confirmed, 'tasdiqlangan'
                elif f['area_source'] in (None, 'qo‘lda', 'tasdiqlangan'):
                    area, source = f['area_ha'], f['area_source'] or 'qo‘lda'     # a typed / confirmed area stays
                else:
                    area, source = r['map_ha'], 'xarita'
                db.execute('''UPDATE fields SET code=?, name=?, polygon_json=?, map_area_ha=?, source_id=?, area_ha=?, area_source=?,
                              brigadier_id=?, active=? WHERE id=?''',
                           (code, name, poly, r['map_ha'], r['source_id'], area, source, brig, active, f['id']))
                fid = f['id']
                if brig != f['brigadier_id']:
                    db.execute('INSERT INTO field_assignments(field_id, brigadier_id, from_date, set_by, set_at) VALUES (?,?,?,?,?)',
                               (fid, brig, today, actor.user_id, ts))
                audit(db, actor, 'IMPORT', 'field', fid, old={'code': f['code'], 'name': f['name'], 'area_ha': f['area_ha'],
                                                             'area_source': f['area_source'], 'brigadier_id': f['brigadier_id']},
                      new={'code': code, 'name': name, 'area_ha': area, 'area_source': source, 'map_area_ha': r['map_ha'],
                           'brigadier_id': brig, 'source_id': r['source_id'], 'import': iid})
                updated += 1
            else:
                area, source = (confirmed, 'tasdiqlangan') if confirmed else (r['map_ha'], 'xarita')
                fid = db.execute('''INSERT INTO fields(code, name, area_ha, brigadier_id, polygon_json, notes, active, created_at,
                                        source_id, map_area_ha, area_source) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                                 (code, name, area, brig, poly, f'Importdan: {imp["filename"] or ""} ({r["source_id"]})', active, ts,
                                  r['source_id'], r['map_ha'], source)).lastrowid
                if brig:
                    db.execute('INSERT INTO field_assignments(field_id, brigadier_id, from_date, set_by, set_at) VALUES (?,?,?,?,?)',
                               (fid, brig, today, actor.user_id, ts))
                audit(db, actor, 'IMPORT', 'field', fid, new={'code': code, 'name': name, 'area_ha': area, 'area_source': source,
                                                            'map_area_ha': r['map_ha'], 'brigadier_id': brig,
                                                            'source_id': r['source_id'], 'import': iid})
                created += 1
            if crop:
                row = db.execute('SELECT 1 FROM field_seasons WHERE year=? AND field_id=?', (year, fid)).fetchone()
                if row:
                    db.execute('UPDATE field_seasons SET crop=? WHERE year=? AND field_id=?', (crop, year, fid))
                else:
                    db.execute('INSERT INTO field_seasons(year, field_id, area_ha, brigadier_id, crop) VALUES (?,?,?,?,?)',
                               (year, fid, area, brig, crop))
        result = {'created': created, 'updated': updated, 'skipped': len(rows) - len(picked)}
        db.execute("UPDATE field_imports SET status='SAQLANDI', saved_by=?, saved_at=?, result_json=? WHERE id=?",
                   (actor.user_id, ts, json.dumps(result), iid))
        audit(db, actor, 'SAVE', 'field_import', iid, new=result)
        return result


def cancel_import(actor, iid):
    _need(actor)
    with tx() as db:
        db.execute("UPDATE field_imports SET status='BEKOR' WHERE id=? AND status='KORIB_CHIQISH'", (iid,))
        audit(db, actor, 'CANCEL', 'field_import', iid)


def confirm_area(actor, field_id, area_ha):
    """A person confirms the working area of a field (the map area stays stored next to it)."""
    _need(actor)
    area = _num(str(area_ha), 'Maydon')
    if not area:
        raise UserError('Maydonni kiriting.')
    with tx() as db:
        f = db.execute('SELECT * FROM fields WHERE id=?', (field_id,)).fetchone()
        if not f:
            raise UserError('Dala topilmadi.')
        db.execute("UPDATE fields SET area_ha=?, area_source='tasdiqlangan' WHERE id=?", (area, field_id))
        audit(db, actor, 'CONFIRM_AREA', 'field', field_id, old={'area_ha': f['area_ha'], 'area_source': f['area_source']},
              new={'area_ha': area, 'area_source': 'tasdiqlangan', 'map_area_ha': f['map_area_ha']})


def field_stats(field_id, year):
    """What the director sees for one field: field kg, shipped netto, punkt kg (received trips only), trips by state."""
    db = get_db()
    h = db.execute('''SELECT COALESCE(SUM(kg),0) kg, COUNT(DISTINCT worker_id) people, MIN(work_date) first, MAX(work_date) last
                      FROM harvests WHERE field_id=? AND season_year=? AND voided_at IS NULL''', (field_id, year)).fetchone()
    t = db.execute('''SELECT COUNT(*) trips,
                             SUM(CASE WHEN wb.id IS NOT NULL THEN 1 ELSE 0 END) sent_n, COALESCE(SUM(wb.net_kg),0) sent_kg,
                             SUM(CASE WHEN nr.id IS NOT NULL THEN 1 ELSE 0 END) rec_n,
                             COALESCE(SUM(CASE WHEN nr.id IS NOT NULL THEN wb.net_kg END),0) rec_sent_kg,
                             COALESCE(SUM(nr.accepted_kg),0) rec_kg,
                             SUM(CASE WHEN wb.status='YARATILDI' THEN 1 ELSE 0 END) moving_n,
                             COALESCE(SUM(CASE WHEN wb.status='YARATILDI' THEN wb.net_kg END),0) moving_kg
                      FROM trailer_loads tl LEFT JOIN waybills wb ON wb.load_id=tl.id AND wb.status<>'BEKOR'
                      LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                      WHERE tl.field_id=? AND tl.season_year=? AND tl.status<>'BEKOR' ''', (field_id, year)).fetchone()
    return {'field_kg': h['kg'], 'people': h['people'], 'first': h['first'], 'last': h['last'], 'trips': t['trips'],
            'sent_n': t['sent_n'] or 0, 'sent_kg': t['sent_kg'], 'rec_n': t['rec_n'] or 0, 'rec_kg': t['rec_kg'],
            'rec_sent_kg': t['rec_sent_kg'], 'diff_kg': t['rec_kg'] - t['rec_sent_kg'] if t['rec_n'] else None,
            'moving_n': t['moving_n'] or 0, 'moving_kg': t['moving_kg']}


def next_code(db=None):
    db = db or get_db()
    nums = [int(r[0][2:]) for r in db.execute("SELECT code FROM fields WHERE code LIKE 'D-%'") if r[0][2:].isdigit()]
    return f'D-{(max(nums) + 1) if nums else 1:02d}'


def create_drawn_field(actor, *, code, name, polygon, brigadier_id=None, confirmed_ha=None, crop=None, year=None):
    """A field drawn on the map by hand (e.g. 10 new hectares taken on later): area computed from the contour and marked
    “xarita” until confirmed. The brigade assignment is recorded with today’s date."""
    _need(actor)
    try:
        pts = json.loads(polygon) if isinstance(polygon, str) else polygon
        pts = [[float(a), float(b)] for a, b in pts]
    except (TypeError, ValueError):
        raise UserError('Chegara noto‘g‘ri.')
    if len(pts) < 3:
        raise UserError('Kamida 3 ta nuqta qo‘ying.')
    ring = [[p[1], p[0]] for p in pts] + [[pts[0][1], pts[0][0]]]
    problems = geo.check_ring(ring)
    if problems:
        raise UserError('Chegara: ' + '; '.join(problems))
    map_ha = geo.poly_area_ha(pts)
    if not map_ha or map_ha < 0.01:
        raise UserError('Maydon juda kichik — chegarani tekshiring.')
    code, name = clean_text(code, 20).upper(), clean_text(name, 80)
    if not code or not name:
        raise UserError('Kod va nom majburiy.')
    confirmed = _num(str(confirmed_ha or ''), 'Tasdiqlangan maydon')
    import sqlite3
    with tx() as db:
        try:
            fid = db.execute('''INSERT INTO fields(code, name, area_ha, brigadier_id, polygon_json, notes, created_at, map_area_ha,
                                    area_source) VALUES (?,?,?,?,?,?,?,?,?)''',
                             (code, name, confirmed or map_ha, brigadier_id, json.dumps(pts), 'Xaritada chizilgan', now_str(),
                              map_ha, 'tasdiqlangan' if confirmed else 'xarita')).lastrowid
        except sqlite3.IntegrityError:
            raise UserError(f'{code} kodi band — boshqa kod yozing.')
        if brigadier_id:
            db.execute('INSERT INTO field_assignments(field_id, brigadier_id, from_date, set_by, set_at) VALUES (?,?,?,?,?)',
                       (fid, brigadier_id, today_str(), actor.user_id, now_str()))
        if crop in CROPS and year:
            db.execute('INSERT OR REPLACE INTO field_seasons(year, field_id, area_ha, brigadier_id, crop) VALUES (?,?,?,?,?)',
                       (year, fid, confirmed or map_ha, brigadier_id, crop))
        audit(db, actor, 'DRAW', 'field', fid, new={'code': code, 'name': name, 'map_area_ha': map_ha, 'area_ha': confirmed or map_ha,
                                                   'brigadier_id': brigadier_id, 'points': len(pts)})
        return fid, map_ha
