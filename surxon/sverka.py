"""KUNLIK SVERKA — the day's ready reckoning, made by itself from what people already entered (nobody fills anything):

    🌾 DALA      the field nakladnoy kg of every trip of the day (the paper Yunus photographed / the trailer's own kg)
    🏭 PUNKT     the punkt netto share of those same trips (after the UY weighing)
    💵 PUL       the starshi / worker talons counted in the evening for those trips (the kg the workers are paid for)

Per brigade (hand picking) and per brigade's combine trips (field → punkt only, combines have no talons). The talons are
tied to the trip they were written to (talon.load_id), so a day is the trips' day — never mixed with another day's
cotton. Trips the punkt has not weighed yet are “kutilmoqda”: the pay is then compared with the field kg only.

The season columns add every day up: a trailer that reached the punkt the next morning makes one day look off, the
season line does not lie.
"""
from .db import get_db
from .settings import get_float

LIVE = "t.harvest_id IS NOT NULL AND EXISTS (SELECT 1 FROM harvests h WHERE h.id=t.harvest_id AND h.voided_at IS NULL)"
MIN_KG = 20          # smaller differences are scale / rounding noise, never red
SEASON_MIN_KG = 100


def _trips(db, where, args):
    return db.execute(f'''
        SELECT tl.brigadier_id bid, COALESCE(b.name, 'Brigadasiz') name, COALESCE(tl.method, 'hand') method,
               COUNT(*) trips,
               COALESCE(SUM(COALESCE(wb.net_kg, NULLIF(tl.manual_kg, 0), tl.internal_kg)), 0) dala,
               COALESCE(SUM(CASE WHEN nr.id IS NOT NULL THEN COALESCE(wb.net_kg, tl.internal_kg) END), 0) dala_recv,
               COALESCE(SUM(nr.accepted_kg), 0) punkt,
               SUM(CASE WHEN nr.id IS NULL THEN 1 ELSE 0 END) waiting,
               COALESCE(SUM(CASE WHEN nr.id IS NULL THEN COALESCE(wb.net_kg, NULLIF(tl.manual_kg, 0), tl.internal_kg) END), 0) waiting_kg
        FROM trailer_loads tl
        LEFT JOIN brigadiers b ON b.id=tl.brigadier_id
        LEFT JOIN waybills wb ON wb.load_id=tl.id AND wb.status<>'BEKOR'
        LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
        WHERE tl.status<>'BEKOR' AND {where}
        GROUP BY tl.brigadier_id, COALESCE(tl.method, 'hand')''', args).fetchall()


def _talons(db, where, args):
    return {r['bid']: dict(r) for r in db.execute(f'''
        SELECT tl.brigadier_id bid, COUNT(*) n, COALESCE(SUM(t.kg), 0) kg, COALESCE(SUM(t.amount), 0) amount,
               COALESCE(SUM(CASE WHEN t.status='TOLANDI' THEN t.amount END), 0) paid,
               COALESCE(SUM(t.people), 0) people
        FROM tezpul_talons t JOIN trailer_loads tl ON tl.id=t.load_id
        WHERE t.status IN ('TAYYOR', 'TOLANDI') AND {LIVE} AND tl.status<>'BEKOR' AND {where}
        GROUP BY tl.brigadier_id''', args)}


def _pct(a, b):
    return round((a - b) / b * 100, 1) if b else None


def _judge(row, alert):
    """One brigade's verdict: (level, short words). Levels: red, yellow, wait, green."""
    talon, dala, punkt = row['talon_kg'], row['dala'], row['punkt']
    if row['method'] == 'combine':
        if row['waiting'] and not row['punkt']:
            return 'wait', 'Punktda hali tortilmagan'
        d = punkt - row['dala_recv']
        if abs(d) > MIN_KG and row['dala_recv'] and abs(d) / row['dala_recv'] * 100 > alert:
            return 'red', f'Punkt dala nakladnoyidan {abs(d):,.0f} kg {"kam" if d < 0 else "ko‘p"}'.replace(',', ' ')
        return ('wait', 'Bir qismi punktda kutilmoqda') if row['waiting'] else ('green', 'Dala va punkt mos')
    if not talon:
        return 'yellow', 'Talon hali yozilmagan (kechki hisob)'
    if row['waiting']:             # the punkt has not weighed everything: the pay against the field paper for now
        base = dala
        d = talon - base
        if d > MIN_KG and d / base * 100 > alert:
            return 'red', f'Pul dala nakladnoyidan {d:,.0f} kg ko‘pga yozilgan'.replace(',', ' ')
        return 'wait', 'Punkt tortishi kutilmoqda'
    d = talon - punkt
    if d > MIN_KG and punkt and d / punkt * 100 > alert:
        return 'red', f'Pul punktdan {d:,.0f} kg ko‘pga yozilgan'.replace(',', ' ')
    dp = punkt - row['dala_recv']
    if abs(dp) > MIN_KG and row['dala_recv'] and abs(dp) / row['dala_recv'] * 100 > alert:
        return 'yellow', f'Punkt dala nakladnoyidan {abs(dp):,.0f} kg {"kam" if dp < 0 else "ko‘p"}'.replace(',', ' ')
    return 'green', 'Dala, punkt va pul mos'


def check(day, year=None):
    """{day, rows: [brigade × method with dala / punkt / talon kg, verdict, season], totals, level, counts}."""
    from .services import current_season
    db = get_db()
    year = year or current_season(db)
    alert = get_float('punkt_alert_pct', 3, db) or 3
    day_trips = _trips(db, 'tl.load_date=?', (day,))
    season_trips = {(r['bid'], r['method']): r for r in _trips(db, 'tl.season_year=?', (year,))}
    tal = _talons(db, 'tl.load_date=?', (day,))
    tal_season = _talons(db, 'tl.season_year=?', (year,))
    rows = []
    for r in day_trips:
        row = dict(r)
        t = tal.get(r['bid']) if r['method'] == 'hand' else None
        row.update(talon_kg=(t or {}).get('kg', 0), talon_n=(t or {}).get('n', 0), amount=(t or {}).get('amount', 0),
                   paid=(t or {}).get('paid', 0), people=(t or {}).get('people', 0))
        row['level'], row['why'] = _judge(row, alert)
        s = season_trips.get((r['bid'], r['method']))
        if s:
            ts = tal_season.get(r['bid'], {}).get('kg', 0) if r['method'] == 'hand' else 0
            base = s['punkt'] + s['waiting_kg']           # what reached the punkt + what is still on its way
            row['season'] = {'dala': s['dala'], 'punkt': s['punkt'], 'waiting_kg': s['waiting_kg'], 'talon': ts,
                             'diff': round(ts - base, 1) if r['method'] == 'hand' else round(s['punkt'] - s['dala_recv'], 1),
                             'pct': _pct(ts, base) if r['method'] == 'hand' else _pct(s['punkt'], s['dala_recv'])}
            sd = row['season']
            if (r['method'] == 'hand' and sd['diff'] > SEASON_MIN_KG and (sd['pct'] or 0) > alert
                    and row['level'] in ('green', 'wait')):
                row['level'], row['why'] = 'red', f'Mavsum bo‘yicha pul {sd["diff"]:,.0f} kg ko‘pga yozilgan'.replace(',', ' ')
        top = max(row['dala'], row['punkt'], row['talon_kg']) or 1
        row['bars'] = {k: round(v / top * 100) for k, v in (('dala', row['dala']), ('punkt', row['punkt']), ('talon', row['talon_kg']))}
        rows.append(row)
    order = {'red': 0, 'yellow': 1, 'wait': 2, 'green': 3}
    rows.sort(key=lambda x: (order[x['level']], x['method'] != 'hand', -x['dala']))
    hand = [r for r in rows if r['method'] == 'hand']
    totals = {'dala': round(sum(r['dala'] for r in rows), 1), 'punkt': round(sum(r['punkt'] for r in rows), 1),
              'waiting_kg': round(sum(r['waiting_kg'] for r in rows), 1), 'waiting': sum(r['waiting'] for r in rows),
              'trips': sum(r['trips'] for r in rows), 'talon': round(sum(r['talon_kg'] for r in hand), 1),
              'amount': sum(r['amount'] for r in hand), 'paid': sum(r['paid'] for r in hand),
              'hand_dala': round(sum(r['dala'] for r in hand), 1)}
    counts = {k: sum(1 for r in rows if r['level'] == k) for k in order}
    level = 'red' if counts['red'] else 'yellow' if counts['yellow'] else 'wait' if counts['wait'] else 'green' if rows else 'empty'
    return {'day': day, 'year': year, 'alert_pct': alert, 'rows': rows, 'totals': totals, 'counts': counts, 'level': level}


ICON = {'red': '🔴', 'yellow': '🟡', 'wait': '⏳', 'green': '✅'}


def text(day):
    """The lines for the director's evening Telegram report ([] when nothing was picked that day)."""
    c = check(day)
    if not c['rows']:
        return []
    t = c['totals']
    n = lambda v: f'{v:,.0f}'.replace(',', ' ')
    head = {'red': f'🔴 {c["counts"]["red"]} ta brigadada farq', 'yellow': '🟡 Tekshirish kerak',
            'wait': '⏳ Punkt tortishi kutilmoqda', 'green': '✅ Hammasi mos'}[c['level']]
    lines = ['⚖️ SVERKA: dala → punkt → pul', head,
             f'Dala {n(t["dala"])} kg → punkt {n(t["punkt"])} kg' + (f' (+{n(t["waiting_kg"])} kg kutilmoqda)' if t['waiting'] else '')
             + f' → talon {n(t["talon"])} kg']
    for r in c['rows'][:12]:
        what = 'kombayn' if r['method'] == 'combine' else f'talon {n(r["talon_kg"])}'
        lines.append(f'{ICON[r["level"]]} {r["name"]}: dala {n(r["dala"])} · punkt {n(r["punkt"])} · {what} — {r["why"]}')
    return lines
