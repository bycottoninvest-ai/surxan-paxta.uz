"""hosil-qabuli.uz → “Yuklab olish” (Excel): every load the cluster weighed for us — yuk xati №, time, brutto / tara /
netto, kondition weight, dirt, moisture, hand / combine, PQ-17 №. Uploaded here it is kept per yuk xati and set beside
our punkt receipts the same day, long before the akt-sverka:

- mos            — our trip has this yuk xati № and the same netto;
- kg farq        — our trip has the number, the kg differ (our punkt kg was typed wrong — fix the receipt);
- raqamsiz       — no trip has the number, but one received that day has exactly this netto → one tap writes the
                   number on it (then the PQ-17 ties itself);
- bizda yo‘q     — nothing of ours matches: a load not entered at the punkt.

The table also gives every yuk xati its date, so a PQ-17 whose form hides the date gets the right day.
"""
import io
import re
from datetime import date, datetime, timedelta

from .db import q, tx
from .security import audit
from .utils import UserError, clean_text, now_str

COLS = {          # our field: words that must be in the column's (multi-row) header, lower case
    'load_no': (('yuk', 'raqam'), ('nakladnoy', 'raqam'), ('yuk xati',)),
    'dt': (('yuk', 'sana'), ('sana',)),
    'brutto': (('brutto',),),
    'tara': (('tara',),),
    'netto': (('netto',),),
    'kond': (('kondits',), ('кондиц',)),
    'dirt': (('iflos',), ('ифлос',)),
    'moist': (('namlik',), ('намлик',)),
    'term': (('terim',), ('терим',)),
    'pq_no': (('pq-17', 'raqam'), ('pq17', 'raqam'), ('pq-17',)),
}


def _num(v):
    if v is None or v == '' or v == '-':
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace('\xa0', '').replace(' ', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


def _dt(v):
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M')
    if isinstance(v, date):
        return v.isoformat()
    m = re.search(r'(\d{2})[./-](\d{2})[./-](\d{4})(?:\D+(\d{1,2}):(\d{2}))?', str(v or ''))
    if not m:
        return None
    d = f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    return d + (f' {int(m.group(4)):02d}:{m.group(5)}' if m.group(4) else '')


def _pq_no(v):
    """“PQ-3141\nyuklab olish” (the cell also holds the download link's text) → “PQ-3141”; “-” / empty → None."""
    m = re.search(r'PQ\s*-?\s*(\d+)', str(v or ''), re.I)
    return f'PQ-{m.group(1)}' if m else None


def parse(data, filename=''):
    """Excel (or CSV) bytes → [row dict]. The header may span several rows with merged cells (as on the site)."""
    rows = _read(data, filename)
    if not rows:
        raise UserError('Fayl bo‘sh yoki o‘qib bo‘lmadi.')
    width = max(len(r) for r in rows[:15])
    head_n = next((i for i, r in enumerate(rows[:15]) if any('netto' in str(c or '').lower() for c in r)), None)
    if head_n is None:
        raise UserError('Bu hosil-qabuli.uz jadvali emas — “NETTO” ustuni topilmadi. Saytdagi “Yuklab olish” Excel faylini yuboring.')
    # header text per column: the rows above the first data row, merged cells carried to the right
    first_data = head_n + 1
    while first_data < min(len(rows), head_n + 4) and not any(_num(c) is not None for c in rows[first_data]):
        first_data += 1
    heads = [''] * width
    for r in rows[max(0, head_n - 2):first_data]:
        carry = ''
        for j in range(width):
            c = str(r[j]).strip() if j < len(r) and r[j] not in (None, '') else ''
            carry = c or (carry if r is not rows[first_data - 1] else '')
            heads[j] += ' ' + carry.lower()
    col = {}
    for key, options in COLS.items():
        for words in options:
            j = next((j for j, h in enumerate(heads) if j not in col.values() and all(w in h for w in words)
                      and not (key == 'load_no' and 'pq' in h) and not (key == 'dt' and ('qayt' in h or 'kirish' in h))), None)
            if j is not None:
                col[key] = j
                break
    if 'load_no' not in col or 'netto' not in col:
        raise UserError('Jadvalda yuk xati raqami yoki netto ustuni topilmadi.')
    out = []
    for r in rows[first_data:]:
        get = lambda k: r[col[k]] if k in col and col[k] < len(r) else None
        no = re.sub(r'\D', '', str(get('load_no') or '').split('.')[0])
        if not 4 <= len(no) <= 10:
            continue
        term = str(get('term') or '').lower()
        out.append({'load_no': no, 'dt': _dt(get('dt')), 'brutto': _num(get('brutto')), 'tara': _num(get('tara')),
                    'netto': _num(get('netto')), 'kond': _num(get('kond')), 'dirt': _num(get('dirt')),
                    'moist': _num(get('moist')),
                    'method': 'hand' if ('qo' in term or 'қўл' in term) else 'combine' if ('mash' in term or 'komb' in term) else None,
                    'pq_no': _pq_no(get('pq_no'))})
    if not out:
        raise UserError('Jadvalda yuk qatorlari topilmadi.')
    return out


def _read(data, filename):
    if data[:2] == b'PK':
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    import csv
    text = data.decode('utf-8-sig', errors='replace')
    dialect = csv.Sniffer().sniff(text[:2000], delimiters=',;\t') if text.strip() else csv.excel
    return [list(r) for r in csv.reader(io.StringIO(text), dialect)]


def import_file(actor, data, filename=''):
    """Store / refresh every yuk xati of the file. Returns (new, updated)."""
    if not actor or not (actor.can('nayman.write') or actor.can('reports.finance')):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')
    rows = parse(data, filename)
    new = upd = 0
    ts = now_str()
    with tx() as db:
        for r in rows:
            old = db.execute('SELECT load_no FROM hq_loads WHERE load_no=?', (r['load_no'],)).fetchone()
            db.execute('''INSERT INTO hq_loads(load_no, dt, brutto, tara, netto, kond, dirt, moist, method, pq_no, imported_at, imported_by)
                          VALUES (:load_no, :dt, :brutto, :tara, :netto, :kond, :dirt, :moist, :method, :pq_no, :ts, :by)
                          ON CONFLICT(load_no) DO UPDATE SET dt=COALESCE(excluded.dt, dt), brutto=excluded.brutto,
                            tara=excluded.tara, netto=excluded.netto, kond=excluded.kond, dirt=excluded.dirt,
                            moist=excluded.moist, method=COALESCE(excluded.method, method),
                            pq_no=COALESCE(excluded.pq_no, pq_no), imported_at=excluded.imported_at''',
                       dict(r, ts=ts, by=actor.user_id))
            new += not old
            upd += bool(old)
        # PQ-17s that came without a readable date: the yuk xati's day from the table
        db.execute('''UPDATE pq17_docs SET doc_date=(SELECT substr(h.dt,1,10) FROM hq_loads h WHERE h.load_no=pq17_docs.load_no)
                      WHERE doc_date IS NULL AND EXISTS (SELECT 1 FROM hq_loads h WHERE h.load_no=pq17_docs.load_no AND h.dt IS NOT NULL)''')
        audit(db, actor, 'IMPORT', 'hq_loads', 0, new={'rows': len(rows), 'new': new, 'updated': upd})
    return new, upd


def compare():
    """Every yuk xati of the table beside our receipt for it (by number, else a same-day exact-netto candidate)."""
    ours = {}
    for r in q('''SELECT nr.load_no, nr.waybill_id, nr.accepted_kg, nr.received_date, tl.trip_no, tl.method, g.number grp,
                         g.accepted_kg grp_kg, g.id gid, nr.receiver_name, nr.created_at, nr.station_gross_kg, nr.station_tare_kg,
                         nr.created_by
                  FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id JOIN trailer_loads tl ON tl.id=wb.load_id
                  LEFT JOIN load_group_items i ON i.waybill_id=wb.id LEFT JOIN load_groups g ON g.id=i.group_id AND g.status='QABUL'
                  WHERE wb.status<>'BEKOR' AND nr.load_no IS NOT NULL'''):
        o = ours.setdefault(r['load_no'], {'label': r['grp'] or r['trip_no'], 'kg': 0.0, 'wid': r['waybill_id'], 'gid': r['gid'],
                                           'who': r['receiver_name'], 'at': r['created_at'], 'by': r['created_by'],
                                           'copied': r['station_gross_kg'] is None})
        o['kg'] = r['grp_kg'] if r['grp'] else o['kg'] + (r['accepted_kg'] or 0)
    for p in q('''SELECT p.load_no, COALESCE(g.number, tl.trip_no) label, COALESCE(g.accepted_kg, nr.accepted_kg) kg,
                         COALESCE(p.waybill_id, (SELECT MIN(i.waybill_id) FROM load_group_items i WHERE i.group_id=p.group_id)) wid,
                         p.group_id gid, nr.receiver_name, nr.created_at, nr.station_gross_kg, nr.created_by
                  FROM pq17_docs p LEFT JOIN waybills wb ON wb.id=p.waybill_id LEFT JOIN trailer_loads tl ON tl.id=wb.load_id
                  LEFT JOIN load_groups g ON g.id=p.group_id
                  LEFT JOIN nayman_receipts nr ON nr.waybill_id=COALESCE(p.waybill_id,
                        (SELECT MIN(i.waybill_id) FROM load_group_items i WHERE i.group_id=p.group_id))
                  WHERE p.waybill_id IS NOT NULL OR p.group_id IS NOT NULL'''):
        if p['load_no'] not in ours:
            ours[p['load_no']] = {'label': p['label'], 'kg': p['kg'], 'wid': p['wid'], 'gid': p['gid'], 'who': p['receiver_name'],
                                  'at': p['created_at'], 'by': p['created_by'], 'copied': p['station_gross_kg'] is None}
    taken_w = {o['wid'] for o in ours.values()}
    taken_g = {o['gid'] for o in ours.values() if o['gid']}
    free = q('''SELECT wb.id, tl.trip_no, tl.method, nr.accepted_kg, nr.received_date, g.number grp, g.accepted_kg grp_kg, g.id gid,
                       nr.receiver_name, nr.created_at, wb.net_kg, nr.created_by
                FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id JOIN trailer_loads tl ON tl.id=wb.load_id
                LEFT JOIN load_group_items i ON i.waybill_id=wb.id LEFT JOIN load_groups g ON g.id=i.group_id AND g.status='QABUL'
                WHERE wb.status<>'BEKOR' AND nr.load_no IS NULL ORDER BY wb.id''')
    cands, seen = [], set()
    for w in free:
        if w['id'] in taken_w or (w['gid'] and w['gid'] in taken_g):
            continue
        if w['grp']:
            if w['grp'] in seen:
                continue
            seen.add(w['grp'])
        cands.append({'wid': w['id'], 'label': w['grp'] or w['trip_no'], 'kg': w['grp_kg'] if w['grp'] else w['accepted_kg'],
                      'day': w['received_date'], 'method': w['method'], 'who': w['receiver_name'], 'at': w['created_at'],
                      'by': w['created_by']})
    pq = {r['load_no']: r for r in q('SELECT load_no, code, netto FROM pq17_docs')}
    rows = []
    for h in q('SELECT * FROM hq_loads ORDER BY dt DESC, load_no DESC'):
        r = dict(h, pq=pq.get(h['load_no']), ours=ours.get(h['load_no']), cand=None)
        if r['netto'] is None:
            r['state'] = 'tarozida'                  # still on the scale (no tara yet)
        elif r['ours']:
            r['state'] = 'mos' if abs((r['ours']['kg'] or 0) - r['netto']) < 0.5 else 'farq'
        else:
            day = (r['dt'] or '')[:10]
            near = [c for c in cands if c['kg'] is not None and abs(c['kg'] - r['netto']) < 0.5 and day and c['day']
                    and abs((date.fromisoformat(c['day']) - date.fromisoformat(day)).days) <= 1]
            if len(near) == 1:
                r['cand'] = near[0]
                r['state'] = 'raqamsiz'
            else:
                r['state'] = 'yoq'
        rows.append(r)
    used = {r['cand']['wid'] for r in rows if r['cand']}
    orphans = [c for c in cands if c['wid'] not in used]          # our receipts no yuk xati of the table explains
    tot = {k: sum(1 for r in rows if r['state'] == k) for k in ('mos', 'farq', 'raqamsiz', 'yoq', 'tarozida')}
    tot['orphans'] = len(orphans)
    tot['n'] = len(rows)
    tot['last'] = q('SELECT MAX(imported_at) m FROM hq_loads', one=True)['m']
    onroad = [dict(r) for r in q('''SELECT wb.id wid, wb.number, tl.trip_no, e.code trailer, wb.net_kg kg,
                                          substr(wb.created_at,1,10) day, g.number grp
                                   FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                                   LEFT JOIN equipment e ON e.id=tl.trailer_id
                                   LEFT JOIN load_group_items i ON i.waybill_id=wb.id LEFT JOIN load_groups g ON g.id=i.group_id
                                   WHERE wb.status='YARATILDI' AND NOT EXISTS (SELECT 1 FROM nayman_receipts nr WHERE nr.waybill_id=wb.id)
                                   ORDER BY wb.created_at, wb.id''')]
    tot['onroad'] = len(onroad)
    return {'rows': rows, 'tot': tot, 'orphans': orphans, 'onroad': onroad}


def assign(actor, load_no, waybill_id):
    """Write a yuk xati № on our receipt (all trailers of its umumiy yuk), then let the PQ-17 tie itself."""
    if not actor or not actor.can('nayman.write'):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')
    from .pq17 import clean_load_no, rematch
    h = q('SELECT * FROM hq_loads WHERE load_no=?', (load_no,), one=True)
    if not h:
        raise UserError('Bu yuk xati jadvalda yo‘q.')
    g = q('''SELECT g.id FROM load_groups g JOIN load_group_items i ON i.group_id=g.id
             WHERE i.waybill_id=? AND g.status='QABUL' ''', (waybill_id,), one=True)
    ids = [r['waybill_id'] for r in q('SELECT waybill_id FROM load_group_items WHERE group_id=?', (g['id'],))] if g else [waybill_id]
    clean_load_no(load_no, waybill_ids=ids, group_id=g['id'] if g else None)
    with tx() as db:
        marks = ','.join('?' * len(ids))
        db.execute(f'UPDATE nayman_receipts SET load_no=? WHERE waybill_id IN ({marks})', [load_no] + ids)
        if g:
            db.execute('UPDATE load_groups SET load_no=? WHERE id=?', (load_no, g['id']))
        audit(db, actor, 'UPDATE', 'nayman_receipt', waybill_id, new={'load_no': load_no, 'from': 'hosil-qabuli.uz'})
    rematch()


def correct(actor, load_no, waybill_ids):
    """The cluster's weighing is the truth: put this yuk xati's netto on the trip(s) the office picked — the punkt kg
    becomes the table's netto (shared over several trailers by their field kg), the number is written, the PQ-17 ties
    itself. Several trips received one by one (or a received UY plus trips) become one umumiy yuk. Only the office
    (nayman.write) may do it; old values stay in the audit with the reason, nothing is deleted."""
    if not actor or not actor.can('nayman.write'):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')
    import secrets
    from .groups import split
    from .pq17 import rematch
    h = q('SELECT * FROM hq_loads WHERE load_no=?', (load_no,), one=True)
    if not h or not h['netto']:
        raise UserError('Bu yuk xati jadvalda yo‘q yoki hali tortib bo‘linmagan.')
    ids = sorted({int(i) for i in waybill_ids if str(i).isdigit()})
    if not ids:
        raise UserError('Reysni belgilang.')
    reason = f'hosil-qabuli.uz yuk xati {load_no} bo‘yicha tuzatildi (klaster netto {h["netto"]:g} kg)'
    ts = now_str()
    trip_sql = """SELECT wb.id, wb.net_kg, tl.trip_no, nr.id rid, nr.accepted_kg, nr.load_no,
                         (SELECT group_id FROM load_group_items i WHERE i.waybill_id=wb.id) gid,
                         (SELECT code FROM pq17_docs p WHERE p.waybill_id=wb.id) pq
                  FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                  LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id WHERE wb.id=? AND wb.status<>'BEKOR'"""
    with tx() as db:
        trips = []
        for wid in ids:
            r = db.execute(trip_sql, (wid,)).fetchone()
            if not r:
                raise UserError('Belgilangan reyslardan biri topilmadi yoki bekor qilingan.')
            trips.append(dict(r))
        # a trip still on the road (its umumiy yuk was never closed / received at the punkt): the cluster's weighing
        # is its receipt — it leaves that open UY (an emptied UY is cancelled, nothing was received on it)
        emptied = set()
        for t in trips:
            if t['rid']:
                continue
            if t['gid']:
                og = db.execute('SELECT * FROM load_groups WHERE id=?', (t['gid'],)).fetchone()
                if og['status'] == 'QABUL':
                    raise UserError(f'{t["trip_no"]}: {og["number"]} qabul qilingan, lekin reysning qabuli yo‘q — rahbarga ayting.')
                db.execute('DELETE FROM load_group_items WHERE group_id=? AND waybill_id=?', (t['gid'], t['id']))
                emptied.add(t['gid'])
                t['gid'] = None
            st = db.execute('''SELECT s.name FROM trailer_loads tl JOIN waybills wb ON wb.load_id=tl.id
                               LEFT JOIN stations s ON s.id=tl.station_id WHERE wb.id=?''', (t['id'],)).fetchone()
            t['rid'] = db.execute('''INSERT INTO nayman_receipts(accepted_kg, diff_kg, diff_reason, received_date, receiver_name,
                                       waybill_id, created_by, created_at, load_no) VALUES (?,?,?,?,?,?,?,?,?)''',
                                  (t['net_kg'], 0, None, (h['dt'] or ts)[:10],
                                   clean_text(f'{actor.name} (klaster jadvali{", " + st["name"] if st and st["name"] else ""})', 80),
                                   t['id'], actor.user_id, ts, None)).lastrowid
            t['accepted_kg'] = None
            db.execute("UPDATE waybills SET status='QABUL', arrived_at=COALESCE(arrived_at, ?), updated_at=? WHERE id=?",
                       (ts, ts, t['id']))
        for og in emptied:
            if not db.execute('SELECT 1 FROM load_group_items WHERE group_id=?', (og,)).fetchone():
                db.execute("UPDATE load_groups SET status='BEKOR', spoiled_reason=? WHERE id=?",
                           (f'telashkalari yuk xati {load_no} ga o‘tkazildi (hosil-qabuli.uz)', og))
        gids = {t['gid'] for t in trips if t['gid']}
        if len(gids) > 1:
            raise UserError('Ikki xil umumiy yuk belgilangan — bittasini tanlang.')
        gid = next(iter(gids), None)
        if gid:                                 # a picked UY comes whole — all its trailers
            g = db.execute('SELECT * FROM load_groups WHERE id=?', (gid,)).fetchone()
            if g['status'] != 'QABUL':
                raise UserError(f'{g["number"]} hali qabul qilinmagan.')
            if db.execute('SELECT 1 FROM pq17_docs WHERE group_id=?', (gid,)).fetchone():
                raise UserError(f'{g["number"]} ga PQ-17 biriktirilgan — tegilmadi.')
            for i in db.execute('SELECT waybill_id FROM load_group_items WHERE group_id=? ORDER BY added_at, waybill_id', (gid,)):
                if i['waybill_id'] not in ids:
                    ids.append(i['waybill_id'])
                    trips.append(dict(db.execute(trip_sql, (i['waybill_id'],)).fetchone()))
        for t in trips:
            if t['load_no'] and t['load_no'] != load_no:
                raise UserError(f'{t["trip_no"]} ga boshqa yuk xati ({t["load_no"]}) yozilgan — tegilmadi.')
            if t['pq']:
                raise UserError(f'{t["trip_no"]} ga {t["pq"]} biriktirilgan — tegilmadi.')
        clash = db.execute(f"""SELECT tl.trip_no FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id
                               JOIN trailer_loads tl ON tl.id=wb.load_id WHERE nr.load_no=? AND wb.status<>'BEKOR'
                               AND nr.waybill_id NOT IN ({','.join('?' * len(ids))})""", [load_no] + ids).fetchone()
        if clash:
            raise UserError(f'Yuk xati {load_no} allaqachon {clash["trip_no"]} ga yozilgan.')
        old = {t['trip_no']: t['accepted_kg'] for t in trips}
        shares = split(h['netto'], [t['net_kg'] or 0 for t in trips])
        if len(trips) > 1 and not gid:          # trips received one by one → one umumiy yuk now
            batch = (db.execute('SELECT MAX(batch) b FROM load_groups').fetchone()['b'] or 0) + 1
            gid = db.execute("""INSERT INTO load_groups(number, token, batch, status, created_by, created_at, received_by, received_at)
                                VALUES (?,?,?,'QABUL',?,?,?,?)""", (f'YX-{load_no}', secrets.token_urlsafe(9), batch,
                                                                  actor.user_id, ts, actor.user_id, ts)).lastrowid
        for t, kg in zip(trips, shares):
            if gid and not t['gid']:
                db.execute('INSERT INTO load_group_items(group_id, waybill_id, added_by, added_at) VALUES (?,?,?,?)',
                           (gid, t['id'], actor.user_id, ts))
            db.execute('''UPDATE nayman_receipts SET accepted_kg=?, diff_kg=?, load_no=?,
                            diff_reason=COALESCE(diff_reason || ' · ', '') || ?, updated_at=? WHERE id=?''',
                       (kg, round(kg - (t['net_kg'] or 0), 1), load_no, reason, ts, t['rid']))
        if gid:
            sent = round(sum(t['net_kg'] or 0 for t in trips), 1)
            db.execute('''UPDATE load_groups SET accepted_kg=?, sent_kg=?, gross_kg=?, tare_kg=?, load_no=?, diff_kg=?,
                            diff_reason=COALESCE(diff_reason || ' · ', '') || ? WHERE id=?''',
                       (h['netto'], sent, h['brutto'], h['tara'], load_no, round(h['netto'] - sent, 1), reason, gid))
        audit(db, actor, 'CORRECT', 'nayman_receipt', ids[0], old={'kg': old},
              new={'load_no': load_no, 'netto': h['netto'], 'group_id': gid,
                   'shares': dict(zip([t['trip_no'] for t in trips], shares))}, reason=reason)
    rematch()
    return [t['trip_no'] for t in trips], h['netto']


def _kg(v):
    return f'{v:,.0f}'.replace(',', ' ') if v is not None else '—'


def notices(cmp=None):
    """What to tell whom: {user_id: [line]} for the people who entered a wrong / unexplained receipt, and the
    director's list of every problem (who, where, how much)."""
    cmp = cmp or compare()
    person, boss = {}, []
    for r in cmp['rows']:
        day = (r['dt'] or '')[:16]
        if r['state'] == 'farq':
            o = r['ours']
            line = (f'⚠ Yuk xati {r["load_no"]} ({day}): klaster tarozisida {_kg(r["netto"])} kg, tizimda {o["label"]} '
                    f'{_kg(o["kg"])} kg yozilgan (farq {_kg(o["kg"] - r["netto"])} kg).')
            person.setdefault(o['by'], []).append(line + ' Chekdagi brutto/tarani qayta tekshirib, buxgalterga yozing.')
            boss.append(line + f' Kiritgan: {o["who"] or "—"}.')
        elif r['state'] == 'yoq':
            boss.append(f'✗ Yuk xati {r["load_no"]} ({day}, {_kg(r["netto"])} kg): klaster tortgan, bizning tizimda yo‘q.')
    for c in cmp['orphans']:
        line = (f'❓ {c["label"]} ({c["day"]}, {_kg(c["kg"])} kg): klaster jadvalida bunday kg li yuk topilmadi.')
        person.setdefault(c['by'], []).append(
            line + ' Bu yuk qaysi yuk xati raqami bilan tortilgan, chekda brutto va tara qancha edi? '
                   'Shu UY qog‘ozida yana qaysi telashkalar bor edi? Javobni buxgalterga yozing.')
        boss.append(line + f' Kiritgan: {c["who"] or "—"}.')
    return person, boss


def send_notices(actor):
    """Queue the questions on Telegram: each person gets their own lines, the director the whole list.
    Returns (people reached, problems)."""
    if not actor or not actor.can('nayman.write'):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')
    from .outbox import enqueue
    from .yordamchi import DIRECTOR_ROLES, _send
    person, boss = notices()
    if not boss:
        return 0, 0
    ts = now_str()
    reached = 0
    with tx() as db:
        for uid, lines in person.items():
            u = db.execute('SELECT id, full_name, telegram_id FROM users WHERE id=? AND active=1', (uid,)).fetchone() if uid else None
            if u and u['telegram_id']:
                enqueue(db, 'telegram_report', 'sverka', f'sverka:{ts}:u{u["id"]}',
                        {'text': '📋 hosil-qabuli.uz bilan sverka — sizning qabullaringiz:\n\n' + '\n\n'.join(lines),
                         'chat_id': u['telegram_id'], 'main_bot': True})
                reached += 1
            else:
                boss.append(f'(Telegram ulanmagan: {u["full_name"] if u else "noma’lum xodim"} — savollar unga bormadi)')
        _send(db, 'sverka', f'sverka:{ts}', '📋 hosil-qabuli.uz bilan sverka — muammolar:\n\n' + '\n'.join(boss), DIRECTOR_ROLES)
        audit(db, actor, 'NOTIFY', 'hq_loads', 0, new={'people': reached, 'problems': len(boss)})
    return reached, len(boss)
