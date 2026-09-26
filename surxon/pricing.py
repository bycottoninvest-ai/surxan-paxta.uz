"""What the punkt pays for our cotton: one price for hand-picked, one for combine-picked (so‘m/kg).

The prices live in Settings and the accountant or admin may change them at any time — even at the end of the season,
when the final price is agreed. Nothing is frozen: every sum (punkt owes, received, remaining) is computed with the
prices in force now. A received trip is priced by what was picked into it: its live hand kg and combine kg share the
punkt's accepted kg proportionally (a trip that is all hand-picked simply gets the hand price).
"""
from .db import q, tx
from .settings import get_float
from .utils import UserError, now_str

KEYS = {'hand': 'price_hand_kg', 'combine': 'price_combine_kg'}


def prices(db=None):
    base = get_float('price_per_kg', None, db)
    return {'hand': get_float(KEYS['hand'], None, db) or base, 'combine': get_float(KEYS['combine'], None, db) or base}


def money(year=None, waybill_ids=None):
    """{waybill_id: {accepted_kg, hand_kg, combine_kg, price, amount}} for received waybills (never cancelled ones).
    price/amount are None when a price the trip needs is not set yet (“narx kiritilmagan”, never a made-up 0)."""
    p = prices()
    where, params = ["wb.status<>'BEKOR'", "tl.status<>'BEKOR'"], []
    if year:
        where.append('wb.season_year=?')
        params.append(year)
    if waybill_ids is not None:
        ids = [int(i) for i in waybill_ids] or [0]
        where.append(f'wb.id IN ({",".join("?" * len(ids))})')
        params += ids
    rows = q(f'''SELECT wb.id, nr.accepted_kg, tl.method,
                        COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.load_id=wb.load_id AND h.voided_at IS NULL
                                  AND h.method='hand'),0) hand,
                        COALESCE((SELECT SUM(kg) FROM harvests h WHERE h.load_id=wb.load_id AND h.voided_at IS NULL
                                  AND h.method='combine'),0) comb
                 FROM waybills wb JOIN nayman_receipts nr ON nr.waybill_id=wb.id JOIN trailer_loads tl ON tl.id=wb.load_id
                 WHERE {" AND ".join(where)}''', params)
    out = {}
    for r in rows:
        hand, comb = r['hand'], r['comb']
        if not hand and not comb:                        # weighbridge-only trip: its method decides
            hand, comb = (0, 1) if r['method'] == 'combine' else (1, 0)
        tot = hand + comb
        acc = r['accepted_kg'] or 0
        missing = (hand and not p['hand']) or (comb and not p['combine'])
        price = None if missing else (hand * (p['hand'] or 0) + comb * (p['combine'] or 0)) / tot
        out[r['id']] = {'accepted_kg': acc, 'hand_kg': round(acc * hand / tot), 'combine_kg': round(acc * comb / tot),
                        'price': round(price) if price is not None else None,
                        'amount': int(round(acc * price)) if price is not None else None}
    return out


def totals(year):
    m = money(year)
    priced = [v for v in m.values() if v['amount'] is not None]
    return {'accepted_kg': sum(v['accepted_kg'] for v in m.values()),
            'hand_kg': sum(v['hand_kg'] for v in m.values()), 'combine_kg': sum(v['combine_kg'] for v in m.values()),
            'amount': sum(v['amount'] for v in priced) if priced else None,
            'unpriced': len(m) - len(priced), 'n': len(m)}


def set_prices(actor, *, hand, combine):
    """Accountant / admin: change the two prices (blank = not agreed yet). Audited; every sum follows at once."""
    from .security import audit
    if not actor or not (actor.role in ('admin', 'manager', 'accountant')):
        raise UserError('Narxni faqat buxgalter, rahbar yoki admin o‘zgartiradi.')
    vals = {}
    for k, v in (('hand', hand), ('combine', combine)):
        v = (str(v or '')).replace(' ', '').replace(' ', '').replace(',', '.').strip()
        if v:
            try:
                n = float(v)
            except ValueError:
                raise UserError('Narx raqam bo‘lsin (masalan 7800).')
            if not 100 <= n <= 1_000_000:
                raise UserError('Narx 100 dan 1 000 000 so‘m/kg gacha bo‘lsin.')
            v = str(int(round(n)))
        vals[KEYS[k]] = v
    with tx() as db:
        old = {k: (db.execute('SELECT value FROM settings WHERE key=?', (k,)).fetchone() or [None])[0] for k in vals}
        for k, v in vals.items():
            db.execute('''INSERT INTO settings(key, value, updated_at) VALUES (?,?,?)
                          ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at''', (k, v, now_str()))
        audit(db, actor, 'UPDATE', 'setting', None, old=old, new=vals, reason='paxta narxi (punkt)')
    return vals
