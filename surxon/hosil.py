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
from .utils import UserError, now_str

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
    while first_data < min(len(rows), head_n + 4) and not any(isinstance(c, (int, float)) for c in rows[first_data]):
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
                    'pq_no': (str(get('pq_no') or '').strip() or None) if str(get('pq_no') or '').strip() not in ('-', '') else None})
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
                         g.accepted_kg grp_kg, g.id gid, nr.receiver_name, nr.created_at, nr.station_gross_kg, nr.station_tare_kg
                  FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id JOIN trailer_loads tl ON tl.id=wb.load_id
                  LEFT JOIN load_group_items i ON i.waybill_id=wb.id LEFT JOIN load_groups g ON g.id=i.group_id AND g.status='QABUL'
                  WHERE wb.status<>'BEKOR' AND nr.load_no IS NOT NULL'''):
        o = ours.setdefault(r['load_no'], {'label': r['grp'] or r['trip_no'], 'kg': 0.0, 'wid': r['waybill_id'], 'gid': r['gid'],
                                           'who': r['receiver_name'], 'at': r['created_at'],
                                           'copied': r['station_gross_kg'] is None})
        o['kg'] = r['grp_kg'] if r['grp'] else o['kg'] + (r['accepted_kg'] or 0)
    free = q('''SELECT wb.id, tl.trip_no, tl.method, nr.accepted_kg, nr.received_date, g.number grp, g.accepted_kg grp_kg,
                       nr.receiver_name, nr.created_at, wb.net_kg
                FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id JOIN trailer_loads tl ON tl.id=wb.load_id
                LEFT JOIN load_group_items i ON i.waybill_id=wb.id LEFT JOIN load_groups g ON g.id=i.group_id AND g.status='QABUL'
                WHERE wb.status<>'BEKOR' AND nr.load_no IS NULL ORDER BY wb.id''')
    cands, seen = [], set()
    for w in free:
        if w['grp']:
            if w['grp'] in seen:
                continue
            seen.add(w['grp'])
        cands.append({'wid': w['id'], 'label': w['grp'] or w['trip_no'], 'kg': w['grp_kg'] if w['grp'] else w['accepted_kg'],
                      'day': w['received_date'], 'method': w['method'], 'who': w['receiver_name'], 'at': w['created_at']})
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
    return {'rows': rows, 'tot': tot, 'orphans': orphans}


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
