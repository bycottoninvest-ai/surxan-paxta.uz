"""PQ-17 (paxta qabul varaqasi) from docs.agro.uz / hosil-qabuli.uz: the state's receipt for every load the cluster
(FAYZ AGROKLASTER) accepted — natural netto, dirt and moisture, the conditioned (kondition) weight that is paid for,
the real price (contract price × grade coefficient, different for hand and combine cotton) and the sum with VAT.

The PDF carries real text, so it is read here (no OCR), stored once per document code (XH…), and matched to our trip:
by the load number (yuk xati) the punkt wrote on the receipt, otherwise by date and netto. From then on the money the
punkt owes for that trip is the PQ-17 sum, never an estimate; trips without a PQ-17 stay “taxminiy”.
"""
import hashlib
import io
import re
from datetime import date, timedelta
from pathlib import Path

from flask import current_app

from .db import get_db, q, tx
from .security import audit
from .settings import get_setting
from .utils import UserError, now_str, today_str

HAND, COMBINE = 'hand', 'combine'


def _num(s):
    return float(str(s).replace(' ', '').replace(',', '.'))


def parse(data):
    """PDF bytes → dict. Raises UserError when it is not a PQ-17 or a number is missing."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = '\n'.join((p.extract_text() or '') for p in reader.pages)
    except Exception:
        raise UserError('Faylni o‘qib bo‘lmadi — hosil-qabuli.uz dan yuklab olingan PQ-17 PDF faylini yuboring.')
    flat = re.sub(r'\s+', ' ', text)
    m = re.search(r'ВАРАҚАСИ\s*№\s*([A-Z]{1,3}\d{6,})', flat) or re.search(r'Hujjat kodi:\s*([A-Z]{1,3}\d{6,})', flat)
    if not m:
        raise UserError('Bu PQ-17 (paxta qabul varaqasi) emas.')
    out = {'code': m.group(1)}
    # I. load line: nakladnoy (yuk xati) № · terim turi · tuda · variety … sort class netto
    m = re.search(r'Соф табиий вазни\s+(\d{3,9})\s+([^\d]+?)\s+(\d+)\s+(.+?)\s+(\d)\s+(\d)\s+(\d+(?:\.\d+)?)\s+II\.', flat)
    if not m:
        raise UserError('PQ-17 dagi yuk qatorini o‘qib bo‘lmadi (nakladnoy raqami / netto).')
    out.update(load_no=m.group(1), harvest_raw=m.group(2), lot=m.group(3), variety=m.group(4).strip(),
               grade=int(m.group(5)), klass=int(m.group(6)), netto=_num(m.group(7)))
    raw = out['harvest_raw'].lower()
    out['method'] = HAND if ('qo' in raw or 'қўл' in raw or 'qul' in raw) else \
        COMBINE if ('mash' in raw or 'komb' in raw or 'машин' in raw or 'комб' in raw) else None
    m = re.search(r'Харид нархи:\s*([\d.]+)\s*\(.*?\)\s*\*\s*([\d.]+)\s*\((.*?)\)\s*=\s*([\d.]+)', flat)
    if not m:
        raise UserError('PQ-17 dagi narx qatorini o‘qib bo‘lmadi.')
    out.update(base_price=_num(m.group(1)), coef=_num(m.group(2)), grade_text=m.group(3), price=_num(m.group(4)))
    m = re.search(r'чегирма\s+миқдори\s+([\d.]+)\s*кг', flat)
    out['deduction_kg'] = _num(m.group(1)) if m else 0.0
    m = re.search(r'устама\s+миқдори\s+([\d.]+)\s*кг', flat)
    out['bonus_kg'] = _num(m.group(1)) if m else 0.0
    out['kond_kg'] = round(out['netto'] - out['deduction_kg'] + out['bonus_kg'], 1)
    m = re.search(r'қиймати,\s*ҚҚС билан\s+31\s+([\d.]+)', flat)
    if not m:
        raise UserError('PQ-17 dagi summani o‘qib bo‘lmadi.')
    out['amount'] = _num(m.group(1))
    m = re.search(r'шундан ҚҚС.*?34\s+([\d.]+)', flat)
    out['vat'] = _num(m.group(1)) if m else None
    # dirt % and moisture %: … netto dirt dirt-weight moisture kondition price sum (pypdf may split “2.9” as “2. 9”)
    m = re.search(r'терим\s+\d\s+\d\s+[\d.]+\s+(\d+\.?\s?\d*)\s+[\d.]+\s+(\d+(?:\.\s?\d+)?)\s+[\d.]+\s+[\d.]+\s+[\d.]+', flat)
    out['dirt_pct'] = _num(m.group(1)) if m else None
    out['moist_pct'] = _num(m.group(2)) if m else None
    # the farmer (us) and the cluster (buyer) — our STIR must be on it
    m = re.search(r'([A-Z][A-Z0-9 "\'.-]{3,80}?(?:MCHJ|MChJ|XK|FX|QK|AJ))\s+(\d{9})\s+Жамоа', flat)
    out['farmer_name'], out['farmer_inn'] = (m.group(1).strip(), m.group(2)) if m else (None, None)
    m = re.search(r'Туман\s+([A-Z][A-Z0-9 "\'.-]{3,80}?(?:MCHJ|MChJ|XK|AJ|QK))\s+Ташкилот\s+\S*\s*(\d{9})', flat)
    out['cluster_name'], out['cluster_inn'] = (m.group(1).strip(), m.group(2)) if m else (None, None)
    m = re.search(r'(\d{2})(\d{2})(\d{3})\s?(\d)\s+кг\s+ҚАБУЛ', flat)
    out['doc_date'] = f'{m.group(3)}{m.group(4)}-{m.group(2)}-{m.group(1)}' if m else None
    if abs(out['netto'] * out['price'] - out['amount']) > max(50, out['amount'] * 0.002) and \
            abs(out['kond_kg'] * out['price'] - out['amount']) > max(50, out['amount'] * 0.002):
        raise UserError('PQ-17 dagi summa narx × og‘irlikka to‘g‘ri kelmadi — faylni tekshiring.')
    return out


# ------------------------------------------------------------------ store + match

def _save_file(data, code):
    rel = f'pq17/{code}.pdf'
    path = Path(current_app.config['SURXON'].UPLOAD_DIR) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return rel


def find_trip(db, d):
    """Our received trip — or umumiy yuk (several trailers weighed once) — for this PQ-17: the load number the punkt
    typed, else one with the same netto within a day of the document date. Returns (waybill_id, how, group_id)."""
    g = db.execute("SELECT id FROM load_groups WHERE load_no=? AND status='QABUL'", (d['load_no'],)).fetchone()
    if g:
        return None, 'yuk xati (umumiy yuk)', g['id']
    r = db.execute("""SELECT wb.id FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id
                      WHERE nr.load_no=? AND wb.status<>'BEKOR'
                        AND NOT EXISTS (SELECT 1 FROM load_group_items i WHERE i.waybill_id=wb.id)""", (d['load_no'],)).fetchone()
    if r:
        return r['id'], 'yuk xati', None
    if d.get('doc_date'):
        day = date.fromisoformat(d['doc_date'])
        lo, hi = (day - timedelta(days=1)).isoformat(), (day + timedelta(days=1)).isoformat()
    else:                       # the date was not read from the PDF: the week before it was uploaded (still one match only)
        day = date.fromisoformat((d.get('uploaded_at') or today_str())[:10])
        lo, hi = (day - timedelta(days=7)).isoformat(), day.isoformat()
    rows = db.execute("""SELECT wb.id FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id
                         WHERE wb.status<>'BEKOR' AND ABS(nr.accepted_kg - ?) < 0.5 AND nr.received_date BETWEEN ? AND ?
                           AND NOT EXISTS (SELECT 1 FROM load_group_items i WHERE i.waybill_id=wb.id)
                           AND NOT EXISTS (SELECT 1 FROM pq17_docs p WHERE p.waybill_id=wb.id)""", (d['netto'], lo, hi)).fetchall()
    groups = db.execute("""SELECT g.id FROM load_groups g WHERE g.status='QABUL' AND ABS(g.accepted_kg - ?) < 0.5
                             AND substr(g.received_at,1,10) BETWEEN ? AND ?
                             AND NOT EXISTS (SELECT 1 FROM pq17_docs p WHERE p.group_id=g.id)""", (d['netto'], lo, hi)).fetchall()
    if len(rows) + len(groups) == 1:
        return (rows[0]['id'], 'netto va sana', None) if rows else (None, 'netto va sana (umumiy yuk)', groups[0]['id'])
    return None, None, None


def import_pdf(actor, data, source='web'):
    """Read, check, store and match one PQ-17. Returns (doc row id, parsed dict, already, match text)."""
    if not actor or not (actor.can('nayman.write') or actor.can('reports.finance')):
        raise UserError('PQ-17 ni faqat buxgalter, rahbar yoki admin yuklaydi.')
    if not data or data[:4] != b'%PDF':
        raise UserError('PDF fayl yuboring (hosil-qabuli.uz → PQ-17 → PDF yuklash).')
    d = parse(data)
    inn = (get_setting('company_inn') or '').strip()
    if inn and d['farmer_inn'] and d['farmer_inn'] != inn:
        raise UserError(f'Bu PQ-17 boshqa xo‘jalikniki (STIR {d["farmer_inn"]}) — bizniki {inn}.')
    with tx() as db:
        old = db.execute('SELECT * FROM pq17_docs WHERE code=?', (d['code'],)).fetchone()
        if old:
            return old['id'], d, True, None
        wid, how, gid = find_trip(db, d)
        cur = db.execute("""INSERT INTO pq17_docs(code, doc_date, load_no, method, harvest_raw, lot, variety, grade, klass,
                              netto, dirt_pct, moist_pct, deduction_kg, bonus_kg, kond_kg, base_price, coef, price, amount, vat,
                              farmer_name, farmer_inn, cluster_name, cluster_inn, file_path, sha256, source, waybill_id,
                              match_how, uploaded_by, uploaded_at, group_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (d['code'], d['doc_date'], d['load_no'], d['method'], d['harvest_raw'], d['lot'], d['variety'],
                          d['grade'], d['klass'], d['netto'], d['dirt_pct'], d['moist_pct'], d['deduction_kg'], d['bonus_kg'],
                          d['kond_kg'], d['base_price'], d['coef'], d['price'], d['amount'], d['vat'], d['farmer_name'],
                          d['farmer_inn'], d['cluster_name'], d['cluster_inn'], _save_file(data, d['code']),
                          hashlib.sha256(data).hexdigest(), source, wid, how, actor.user_id, now_str(), gid))
        if wid:
            db.execute('UPDATE nayman_receipts SET load_no=COALESCE(load_no, ?) WHERE waybill_id=?', (d['load_no'], wid))
        audit(db, actor, 'IMPORT', 'pq17', cur.lastrowid, new={'code': d['code'], 'load_no': d['load_no'], 'netto': d['netto'],
                                                               'amount': d['amount'], 'waybill_id': wid, 'group_id': gid})
    return cur.lastrowid, d, False, how


def link(actor, doc_id, waybill_id):
    """The office ties an unmatched PQ-17 to a trip by hand (or unties it: waybill_id None)."""
    if not actor or not actor.can('nayman.write'):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')
    with tx() as db:
        doc = db.execute('SELECT * FROM pq17_docs WHERE id=?', (doc_id,)).fetchone()
        if not doc:
            raise UserError('PQ-17 topilmadi.')
        grp = db.execute("SELECT g.id FROM load_groups g JOIN load_group_items i ON i.group_id=g.id "
                         "WHERE i.waybill_id=? AND g.status='QABUL'", (waybill_id,)).fetchone() if waybill_id else None
        if grp:
            busy = db.execute('SELECT code FROM pq17_docs WHERE group_id=? AND id<>?', (grp['id'], doc_id)).fetchone()
            if busy:
                raise UserError(f'Bu umumiy yukka {busy["code"]} allaqachon biriktirilgan.')
            db.execute("UPDATE pq17_docs SET waybill_id=NULL, group_id=?, match_how='qo‘lda (umumiy yuk)' WHERE id=?", (grp['id'], doc_id))
            audit(db, actor, 'UPDATE', 'pq17', doc_id, new={'group_id': grp['id']})
            return
        if waybill_id:
            wb = db.execute("SELECT wb.id FROM waybills wb JOIN nayman_receipts nr ON nr.waybill_id=wb.id "
                            "WHERE wb.id=? AND wb.status<>'BEKOR'", (waybill_id,)).fetchone()
            if not wb:
                raise UserError('Bu reys hali punktda qabul qilinmagan.')
            busy = db.execute('SELECT code FROM pq17_docs WHERE waybill_id=? AND id<>?', (waybill_id, doc_id)).fetchone()
            if busy:
                raise UserError(f'Bu reysga {busy["code"]} allaqachon biriktirilgan.')
            db.execute('UPDATE nayman_receipts SET load_no=? WHERE waybill_id=?', (doc['load_no'], waybill_id))
        db.execute("UPDATE pq17_docs SET waybill_id=?, group_id=NULL, match_how=? WHERE id=?",
                   (waybill_id, 'qo‘lda' if waybill_id else None, doc_id))
        audit(db, actor, 'UPDATE', 'pq17', doc_id, old={'waybill_id': doc['waybill_id']}, new={'waybill_id': waybill_id})


def set_status(actor, doc_ids, *, signed=None, invoice=None, invoice_no=None):
    """Mark PQ-17s as signed by us (farmer) and the invoice as made / signed — what hosil-qabuli.uz shows."""
    if not actor or not actor.can('nayman.write'):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')
    if invoice not in (None, '', 'yaratildi', 'imzolandi'):
        raise UserError('Faktura holati noto‘g‘ri.')
    ids = [int(i) for i in doc_ids if str(i).isdigit()]
    if not ids:
        raise UserError('Kamida bitta PQ-17 ni belgilang.')
    with tx() as db:
        for i in ids:
            d = db.execute('SELECT * FROM pq17_docs WHERE id=?', (i,)).fetchone()
            if not d:
                continue
            new = {}
            if signed is not None:
                new['farmer_signed_at'] = now_str() if signed else None
            if invoice is not None:
                new['invoice_status'] = invoice or None
                if invoice and not (d['farmer_signed_at'] or new.get('farmer_signed_at')):
                    new['farmer_signed_at'] = now_str()          # an invoice exists only after our PQ-17 signature
            if invoice_no is not None:
                new['invoice_no'] = (invoice_no or '').strip()[:40] or None
            if not new:
                continue
            sets = ', '.join(f'{k}=?' for k in new)
            db.execute(f'UPDATE pq17_docs SET {sets}, status_by=?, status_at=? WHERE id=?',
                       list(new.values()) + [actor.user_id, now_str(), i])
            audit(db, actor, 'UPDATE', 'pq17', i, old={k: d[k] for k in new}, new=new)
    return len(ids)


def stage(d):
    """Where a PQ-17 is (as in hosil-qabuli.uz): our signature → invoice → invoice signed."""
    if d['invoice_status'] == 'imzolandi':
        return ('done', 'Faktura imzolangan')
    if d['invoice_status'] == 'yaratildi':
        return ('inv', 'Faktura imzo kutilmoqda')
    if d['farmer_signed_at']:
        return ('inv0', 'Faktura yaratish kerak')
    return ('sign', 'SURXON imzosi kutilmoqda')


def clusters():
    """One card per buyer (cluster / punkt): what they accepted, what they owe, what is still to sign."""
    rows = q('SELECT * FROM pq17_docs ORDER BY doc_date, id')
    out = {}
    for d in rows:
        key = d['cluster_inn'] or d['cluster_name'] or '?'
        c = out.setdefault(key, {'inn': key, 'name': d['cluster_name'] or 'Noma’lum klaster', 'n': 0, 'netto': 0, 'kond': 0,
                                 'deduction': 0, 'amount': 0, 'to_sign': 0, 'to_invoice': 0, 'invoices': 0, 'farq': 0,
                                 'unmatched': 0, 'first': d['doc_date'], 'last': d['doc_date']})
        c['n'] += 1
        c['netto'] += d['netto']
        c['kond'] += d['kond_kg']
        c['deduction'] += d['deduction_kg'] - d['bonus_kg']
        c['amount'] += d['amount']
        st = stage(d)[0]
        c['to_sign'] += st == 'sign'
        c['to_invoice'] += st in ('inv0', 'inv')
        c['invoices'] += st == 'done'
        c['unmatched'] += d['waybill_id'] is None and d['group_id'] is None
        c['last'] = d['doc_date'] or c['last']
    return list(out.values())


def rematch():
    """Match stored PQ-17s that arrived before their trip was received (called after each punkt receipt)."""
    n = 0
    with tx() as db:
        for d in db.execute('SELECT * FROM pq17_docs WHERE waybill_id IS NULL AND group_id IS NULL').fetchall():
            wid, how, gid = find_trip(db, dict(d))
            if wid and not db.execute('SELECT 1 FROM pq17_docs WHERE waybill_id=?', (wid,)).fetchone():
                db.execute('UPDATE pq17_docs SET waybill_id=?, match_how=? WHERE id=?', (wid, how, d['id']))
                n += 1
            elif gid and not db.execute('SELECT 1 FROM pq17_docs WHERE group_id=?', (gid,)).fetchone():
                db.execute('UPDATE pq17_docs SET group_id=?, match_how=? WHERE id=?', (gid, how, d['id']))
                n += 1
    return n


SHARED = ('netto', 'kond_kg', 'deduction_kg', 'bonus_kg', 'amount', 'vat')


def by_waybill(year=None):
    """{waybill_id: PQ-17 row}. A group's PQ-17 is shared over its trailers by their punkt share (kg and sum),
    so every trip — and every combine / brigade behind it — gets its part of the one document."""
    out = {r['waybill_id']: dict(r) for r in q('SELECT * FROM pq17_docs WHERE waybill_id IS NOT NULL')}
    for d in q('SELECT p.*, g.accepted_kg g_kg, g.number g_number FROM pq17_docs p JOIN load_groups g ON g.id=p.group_id'):
        items = q('''SELECT i.waybill_id, nr.accepted_kg FROM load_group_items i JOIN nayman_receipts nr ON nr.waybill_id=i.waybill_id
                     WHERE i.group_id=? ORDER BY i.added_at, i.waybill_id''', (d['group_id'],))
        tot = sum(i['accepted_kg'] for i in items) or 1
        for i in items:
            k = i['accepted_kg'] / tot
            out[i['waybill_id']] = dict(d, **{f: (d[f] or 0) * k for f in SHARED}, waybill_id=i['waybill_id'], share=k)
    return out


def overview(year):
    """The check page: every received trip with its PQ-17 (or the lack of one), every PQ-17 without a trip, totals."""
    trips = q("""SELECT wb.id, wb.number, tl.trip_no, tl.method, nr.accepted_kg, nr.received_date, nr.load_no, f.code field_code,
                        p.id pq_id, p.code, p.load_no pq_load, p.netto, p.kond_kg, p.deduction_kg, p.dirt_pct, p.moist_pct,
                        p.price, p.amount, p.method pq_method, p.grade, p.klass, p.match_how
                 FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                 LEFT JOIN fields f ON f.id=tl.field_id LEFT JOIN pq17_docs p ON p.waybill_id=wb.id
                 WHERE wb.status<>'BEKOR' AND tl.status<>'BEKOR' AND wb.season_year=?
                 ORDER BY nr.received_date DESC, wb.id DESC""", (year,))
    grp = {}
    for d in q('''SELECT p.*, g.accepted_kg g_kg, g.number g_number, i.waybill_id wid FROM pq17_docs p
                  JOIN load_groups g ON g.id=p.group_id JOIN load_group_items i ON i.group_id=g.id'''):
        grp[d['wid']] = d
    rows = []
    for t in trips:
        r = dict(t)
        gd = grp.get(r['id'])
        if gd and not r['pq_id']:      # a trailer of an umumiy yuk: the group's PQ-17, checked on the group's total
            r.update(pq_id=gd['id'], code=gd['code'], pq_load=gd['load_no'], netto=gd['netto'], kond_kg=gd['kond_kg'],
                     deduction_kg=gd['deduction_kg'], dirt_pct=gd['dirt_pct'], moist_pct=gd['moist_pct'], price=gd['price'],
                     amount=gd['amount'], pq_method=None, grade=gd['grade'], klass=gd['klass'], match_how=gd['match_how'],
                     group=gd['g_number'], accepted_kg_row=r['accepted_kg'], accepted_kg=gd['g_kg'])
        if not r['pq_id']:
            r['state'] = 'kutilmoqda'
        else:
            problems = []
            if abs((r['accepted_kg'] or 0) - r['netto']) >= 0.5:
                problems.append(f'kg: bizda {r["accepted_kg"]:g}, PQ-17 da {r["netto"]:g}')
            if r['pq_method'] and r['method'] and r['pq_method'] != r['method']:
                problems.append('terim turi: bizda ' + ('kombayn' if r['method'] == COMBINE else 'qo‘l')
                                + ', PQ-17 da ' + ('kombayn' if r['pq_method'] == COMBINE else 'qo‘l'))
            r['problems'] = problems
            r['state'] = 'farq' if problems else 'mos'
        rows.append(r)
    orphans = q('SELECT * FROM pq17_docs WHERE waybill_id IS NULL AND group_id IS NULL ORDER BY doc_date DESC, id DESC')
    docs = q('SELECT * FROM pq17_docs')
    tot = {'n': len(docs), 'netto': sum(d['netto'] for d in docs), 'kond': sum(d['kond_kg'] for d in docs),
           'deduction': sum(d['deduction_kg'] - d['bonus_kg'] for d in docs), 'amount': sum(d['amount'] for d in docs),
           'mos': sum(1 for r in rows if r['state'] == 'mos'), 'farq': sum(1 for r in rows if r['state'] == 'farq'),
           'kutilmoqda': sum(1 for r in rows if r['state'] == 'kutilmoqda'), 'orphans': len(orphans),
           'our_kg': sum((r['accepted_kg_row'] if r.get('group') else r['accepted_kg']) or 0 for r in rows)}
    for m in (HAND, COMBINE):
        ds = [d for d in docs if d['method'] == m]
        tot[m] = {'n': len(ds), 'kond': sum(d['kond_kg'] for d in ds), 'amount': sum(d['amount'] for d in ds),
                  'price': round(sum(d['amount'] for d in ds) / sum(d['kond_kg'] for d in ds)) if ds and sum(d['kond_kg'] for d in ds) else None}
    return {'rows': rows, 'orphans': orphans, 'tot': tot}


def latest_price(method):
    """The price of the newest PQ-17 of that kind (contract price × grade) — the best estimate for trips still waiting."""
    r = q('SELECT price FROM pq17_docs WHERE method=? ORDER BY doc_date DESC, id DESC LIMIT 1', (method,), one=True)
    return r['price'] if r else None


def period(dan, gacha):
    """What the clusters accepted by PQ-17 in a period (by the document date) beside our punkt kg for the same trips —
    for the dashboard strip. Only kg (no money). None when no PQ-17 falls in the period."""
    r = q('''SELECT COUNT(*) n, COALESCE(SUM(p.netto),0) netto, COALESCE(SUM(p.kond_kg),0) kond,
                    COALESCE(SUM(p.deduction_kg - COALESCE(p.bonus_kg,0)),0) deduction,
                    SUM(p.netto * COALESCE(p.moist_pct,0)) / NULLIF(SUM(p.netto),0) moist,
                    SUM(p.netto * COALESCE(p.dirt_pct,0)) / NULLIF(SUM(p.netto),0) dirt,
                    SUM(p.waybill_id IS NULL AND p.group_id IS NULL) unmatched,
                    COALESCE(SUM(CASE WHEN p.waybill_id IS NOT NULL OR p.group_id IS NOT NULL
                                      THEN COALESCE(g.accepted_kg, nr.accepted_kg) END),0) ours,
                    COALESCE(SUM(CASE WHEN p.waybill_id IS NOT NULL OR p.group_id IS NOT NULL THEN p.netto END),0) matched_netto,
                    SUM(CASE WHEN (p.waybill_id IS NOT NULL OR p.group_id IS NOT NULL)
                              AND ABS(COALESCE(g.accepted_kg, nr.accepted_kg, 0) - p.netto) >= 0.5 THEN 1 ELSE 0 END) farq_n
             FROM pq17_docs p LEFT JOIN nayman_receipts nr ON nr.waybill_id=p.waybill_id LEFT JOIN load_groups g ON g.id=p.group_id
             WHERE COALESCE(p.doc_date, substr(p.uploaded_at,1,10)) BETWEEN ? AND ?''', (dan, gacha), one=True)
    if not r or not r['n']:
        return None
    return dict(r, diff=round(r['ours'] - r['matched_netto'], 1))
