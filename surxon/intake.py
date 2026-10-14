"""DALA NAKLADNOYI (PB v3) → trips. Nobody uses the website in the field: the paper is filled by hand there, and at the
punkt Yunus photographs it inside the UY (umumiy yuk) list — the system reads it (talon_ai.read_blank), he checks it,
and every row of the paper (a trailer filled in up to 3 places: a brigadier, a field, kg) becomes a closed trip with its
own waybill, added to that UY; then the UY is received as before (yuk xati, brutto / tara → netto split by kg).

The trips are opened and closed through services.open_load / add_harvest / mark_full exactly like in the field, so the
same checks hold (field ↔ brigadier, a busy trailer, limits); the punkt operator gets the field permissions only for
this (an _Intake actor) and every record carries his name.
"""
import dataclasses
import re

from flask import current_app

from .db import q, tx
from .security import Actor, audit
from .settings import get_float, get_setting
from .utils import UserError, now_str

ROWS = 3


@dataclasses.dataclass
class _Intake(Actor):
    """The punkt operator registering a field waybill: may open / fill / close the trips it describes."""
    def can(self, perm):
        return perm in ('load.open', 'load.full', 'harvest.write') or super().can(perm)


def _as_intake(actor):
    return _Intake(**{f.name: getattr(actor, f.name) for f in dataclasses.fields(Actor)})


def _need(actor):
    if not actor or not actor.can('station.receive'):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')


def _digits(text):
    d = ''.join(ch for ch in str(text or '') if ch.isdigit())
    return int(d) if d else None


def _norm(text):
    t = (text or '').lower().replace('ʻ', "'").replace('‘', "'").replace('’', "'").replace('`', "'").replace('ʼ', "'")
    return re.sub(r'\s+', ' ', t).strip()


def choices():
    """What the paper may name: trailers, fields (with their brigadier), brigadiers, combines."""
    return {
        'trailers': [dict(r) for r in q("SELECT id, code FROM equipment WHERE kind='telashka' AND active=1 ORDER BY code")],
        'combines': [dict(r) for r in q("SELECT id, code FROM equipment WHERE kind='kombayn' AND active=1 ORDER BY code")],
        'brigadiers': [dict(r) for r in q('SELECT id, name FROM brigadiers WHERE active=1 ORDER BY name')],
        'fields': [dict(r) for r in q('''SELECT f.id, f.code, f.name, f.brigadier_id, b.name brig FROM fields f
                                          LEFT JOIN brigadiers b ON b.id=f.brigadier_id WHERE f.active=1 ORDER BY f.code''')],
    }


def _by_number(items, text):
    n = _digits(text)
    if n is None:
        return None
    hits = [i for i in items if _digits(i['code']) == n]
    return hits[0]['id'] if len(hits) == 1 else None


def _brigadier(items, text):
    t = _norm(text)
    if not t:
        return None
    exact = [i for i in items if _norm(i['name']) == t]
    if exact:
        return exact[0]['id']
    first = [i for i in items if _norm(i['name']).split(' ')[0] == t.split(' ')[0]]
    return first[0]['id'] if len(first) == 1 else None


def match(raw, ch=None):
    """The paper as read, tied to our records; every row flagged when something is missing or does not add up."""
    ch = ch or choices()
    fields = {f['id']: f for f in ch['fields']}
    rows = []
    for r in sorted(raw.get('rows') or [], key=lambda x: x.get('row') or 0)[:ROWS]:
        kg = r.get('kg')
        try:
            kg = None if kg is None else round(float(kg), 1)
        except (TypeError, ValueError):
            kg = None
        bid = _brigadier(ch['brigadiers'], r.get('brigadier'))
        fid = None
        code = (r.get('field') or '').strip()
        if code:
            exact = [f for f in ch['fields'] if f['code'].lower() == code.lower()]
            fid = exact[0]['id'] if exact else _by_number(ch['fields'], code)
        if not bid and fid and fields[fid]['brigadier_id']:
            bid = fields[fid]['brigadier_id']
        if not (kg or bid or fid):
            continue
        flag = ''
        if r.get('unclear'):
            flag = 'aniq o‘qilmadi'
        if fid and bid and fields[fid]['brigadier_id'] and fields[fid]['brigadier_id'] != bid:
            flag = f'{fields[fid]["code"]} — {fields[fid]["brig"]} dalasi, belgilangan brigadir boshqa'
        if not bid or not fid or not kg:
            flag = flag or ('brigadir' if not bid else 'dala' if not fid else 'kg') + ' o‘qilmadi — tanlang'
        rows.append({'row': r.get('row'), 'brigadier_id': bid, 'brigadier_text': r.get('brigadier') or '',
                     'field_id': fid, 'field_text': code, 'kg': kg, 'flag': flag})
    total = raw.get('total_kg')
    try:
        total = None if total is None else round(float(total), 1)
    except (TypeError, ValueError):
        total = None
    s = round(sum(r['kg'] or 0 for r in rows), 1)
    method = raw.get('method') if raw.get('method') in ('hand', 'combine') else None
    return {'blank_no': raw.get('blank_no'), 'trailer_id': _by_number(ch['trailers'], raw.get('trailer')),
            'trailer_text': raw.get('trailer') or '', 'method': method,
            'combine_id': _by_number(ch['combines'], raw.get('combine_no')) if method == 'combine' else None,
            'rows': rows, 'total_kg': total, 'sum_kg': s, 'sum_ok': total is None or abs(total - s) <= 0.5,
            'date': raw.get('date'), 'note': (raw.get('note') or '')[:300]}


def _group(actor, gid):
    from . import groups as G
    g = G.get(gid)
    if not g:
        raise UserError('Umumiy nakladnoy (UY) topilmadi.')
    if actor.role == 'station' and g['station_id'] and g['station_id'] != actor.station_id:
        raise UserError('Bu UY boshqa punktniki.')
    if g['status'] != 'OCHIQ':
        raise UserError(f'{g["number"]} ro‘yxati yopilgan — avval “Qayta ochish”.')
    return g


def read(actor, gid, data, code=''):
    """The photo of one field waybill: kept as evidence and read. The blank comes from its QR (decoded on the phone)
    or from the number the reader saw. A blank whose trips exist already (registered before) just joins the list."""
    _need(actor)
    from . import blanks as BL, talon_ai
    from .photos import store_photo
    g = _group(actor, gid)
    if not data:
        raise UserError('Nakladnoyni rasmga oling.')
    jpeg = talon_ai.photo_jpeg(data)
    raw, ai_error = None, None
    ch = choices()
    if talon_ai.enabled():
        try:
            raw = talon_ai.read_blank(jpeg, [b['name'] for b in ch['brigadiers']])
        except UserError as err:
            ai_error = str(err)
    b = BL.find(code) if code else None
    if not b and raw and raw.get('blank_no'):
        b = BL.find(raw['blank_no'])
    if not b:
        raise UserError('Nakladnoy raqami o‘qilmadi — QR ham, PB raqami ham ko‘rinadigan qilib qaytadan rasmga oling.')
    if b['spoiled_reason']:
        raise UserError(f'{b["number"]} buzilgan deb belgilangan.')
    if b['waybill_id']:
        from . import groups as G
        trips = [r['wid'] for r in q('''SELECT wb.id wid FROM trailer_loads tl JOIN waybills wb ON wb.load_id=tl.id
                                         WHERE tl.pb_blank_id=? AND wb.status<>'BEKOR' ''', (b['id'],))] or [b['waybill_id']]
        added = []
        for wid in trips:
            try:
                added.append(G.add_trip(actor, gid, wid))
            except UserError as err:
                if 'allaqachon shu ro‘yxatda' not in str(err):
                    raise
        return {'already': True, 'blank': b['number'], 'added': added}
    with tx() as db:
        pid = store_photo(db, actor, jpeg, category='nayman', entity_type='punkt_blank', entity_id=b['id'],
                          caption=f'Dala nakladnoyi {b["number"]} · {g["number"]}')
    out = match(raw, ch) if raw else {'blank_no': b['number'], 'trailer_id': None, 'trailer_text': '', 'method': None,
                                       'combine_id': None, 'rows': [], 'total_kg': None, 'sum_kg': 0, 'sum_ok': True,
                                       'date': None, 'note': ''}
    return {'already': False, 'blank': b['number'], 'token': b['token'], 'photo_id': pid, 'read': out,
            'ai_on': talon_ai.enabled(), 'ai_error': ai_error}


def register(actor, gid, *, code, photo_id, trailer_id, method, combine_id, rows, total_kg=None):
    """The checked paper becomes trips: one closed trip (its own waybill, the paper's kg) per row, all added to the UY.
    Safe to send twice: every step is keyed by the blank and the row."""
    _need(actor)
    from . import blanks as BL, groups as G
    from .services import add_harvest, mark_full, open_load
    g = _group(actor, gid)
    b = BL.find(code)
    if not b:
        raise UserError('Nakladnoy topilmadi.')
    if b['spoiled_reason']:
        raise UserError(f'{b["number"]} buzilgan deb belgilangan.')
    if method not in ('hand', 'combine'):
        raise UserError('Qo‘l terimi yoki kombayn — belgilang.')
    if not trailer_id:
        raise UserError('Telashkani tanlang.')
    if method == 'combine' and not combine_id:
        raise UserError('Kombaynni tanlang.')
    max_kg = get_float('max_trailer_kg', 15000)
    clean = []
    for r in (rows or [])[:ROWS]:
        try:
            kg = round(float(str(r.get('kg') or '').replace(',', '.').replace(' ', '')), 1)
        except ValueError:
            kg = 0
        if not kg and not r.get('field_id') and not r.get('brigadier_id'):
            continue
        if not (0 < kg <= max_kg):
            raise UserError(f'{len(clean) + 1}-qator: kg noto‘g‘ri.')
        if not r.get('field_id') or not r.get('brigadier_id'):
            raise UserError(f'{len(clean) + 1}-qator: dala va brigadirni tanlang.')
        clean.append({'field_id': int(r['field_id']), 'brigadier_id': int(r['brigadier_id']), 'kg': kg})
    if not clean:
        raise UserError('Kamida bitta qator: brigadir, dala, kg.')
    if sum(r['kg'] for r in clean) > max_kg:
        raise UserError('Jami kg bitta telashkaga juda ko‘p — raqamlarni tekshiring.')
    if b['waybill_id']:
        done_trips = q('''SELECT wb.id wid, tl.manual_kg kg FROM trailer_loads tl JOIN waybills wb ON wb.load_id=tl.id
                          WHERE tl.pb_blank_id=? AND wb.status<>'BEKOR' ORDER BY tl.pb_row''', (b['id'],))
        if not done_trips:
            raise UserError(f'{b["number"]} boshqa reysga ishlatilgan.')
        added = []                     # registered already (the answer was lost): just make sure they are in the list
        for t in done_trips:
            try:
                added.append(G.add_trip(actor, gid, t['wid']))
            except UserError as err:
                if 'allaqachon shu ro‘yxatda' not in str(err):
                    raise
        return {'blank': b['number'], 'trips': added, 'kg': round(sum(t['kg'] or 0 for t in done_trips), 1), 'already': True}
    photo = q('SELECT path FROM photos WHERE id=? AND entity_type=? AND entity_id=?', (photo_id, 'punkt_blank', b['id']), one=True)
    data = (current_app.config['SURXON'].UPLOAD_DIR / photo['path']).read_bytes() if photo else None
    if not data:
        raise UserError('Nakladnoy rasmi topilmadi — qaytadan rasmga oling.')
    fa = _as_intake(actor)
    rate = (get_setting('worker_rate_hand') if method == 'hand'
            else get_setting('combine_rate_standard') or get_setting('combine_rate_kg')) or None
    if not rate:
        raise UserError('Terim narxi sozlanmagan — Admin: Sozlamalar.')
    station = g['station_id'] or actor.station_id
    waybills = []
    for k, r in enumerate(clean, 1):
        key = f'pb:{b["id"]}:{k}'
        lid = open_load(fa, trailer_id=int(trailer_id), field_id=r['field_id'], brigadier_id=r['brigadier_id'],
                        method=method, rate=rate, station_id=station, client_uuid=key)
        with tx() as db:
            db.execute('UPDATE trailer_loads SET pb_blank_id=?, pb_row=? WHERE id=?', (b['id'], k, lid))
        if method == 'combine':
            add_harvest(fa, load_id=lid, method='combine', kg=r['kg'], combine_id=int(combine_id), client_uuid=key + ':h')
        res = mark_full(fa, lid, photos=[data], manual_kg=r['kg'] if method == 'hand' else None)
        if not res.get('waybill_id'):
            raise UserError('Nakladnoy avtomatik chiqmadi — Sozlamalar: auto_waybill_hand = 1.')
        waybills.append(res['waybill_id'])
    BL.attach(actor, waybills[0], b['number'], photo_later=True)
    with tx() as db:
        db.execute('UPDATE punkt_blanks SET photo_id=? WHERE id=?', (photo_id, b['id']))
        audit(db, actor, 'PB_INTAKE', 'punkt_blank', b['id'],
              new={'number': b['number'], 'group': g['number'], 'trailer_id': trailer_id, 'method': method,
                   'rows': clean, 'total_kg': total_kg, 'waybills': waybills})
    added = []
    for wid in waybills:
        try:
            added.append(G.add_trip(actor, gid, wid))
        except UserError as err:
            if 'allaqachon shu ro‘yxatda' not in str(err):
                raise
    return {'blank': b['number'], 'trips': added, 'kg': round(sum(r['kg'] for r in clean), 1)}
