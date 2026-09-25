"""Director's phone panel — read-only figures, all taken from saved operations (no second set of books).

Rules kept here so the phone and the computer agree:
- Field kg (harvest rows), shipped netto (waybills) and punkt kg (receipts) are three separate numbers. The
  difference is counted only on trips that were received, against those same trips' shipped kg; what is not received
  yet is shown as its own number, never as a "loss".
- Trip states (full in the field / on the way / waiting at the punkt) come from saved events and each trip is in
  exactly one of them.
- Cash: opening (before the period) + in − out = closing, from the cash book; the current balance is separate.
- Wages: earned and paid in the period are shown side by side and never subtracted from each other (a payment may
  settle an earlier debt); the debt is the current, season-wide figure.
- Fuel: liters taken / given in the period, liters the keepers hold now (all history), ticket money left — liters
  and so‘m never mixed. Before any fuel ticket exists the card says “Ulanmagan”, never 0.
"""
import json
from datetime import date, timedelta

from . import accounting as A
from .db import get_db, q, scalar
from .reporting import period_range
from .settings import get_float
from .utils import today_str

PERIODS = [('bugun', 'Bugun'), ('hafta', 'Hafta'), ('mavsum', 'Mavsum')]


def period(p, year):
    if p not in dict(PERIODS):
        p = 'bugun'
    a, b, label = period_range(p, year=year)
    return p, a, b, label


def stamp():
    """Changes whenever any operation is saved (every write is audited) — the phone polls this."""
    return scalar('SELECT COALESCE(MAX(id),0) FROM audit_logs')


# ------------------------------------------------------------------ cotton

def cotton(year, a, b):
    field = q('''SELECT COALESCE(SUM(kg),0) kg, COALESCE(SUM(CASE WHEN method='hand' THEN kg END),0) hand,
                        COALESCE(SUM(CASE WHEN method='combine' THEN kg END),0) comb, COUNT(DISTINCT worker_id) people
                 FROM harvests WHERE season_year=? AND voided_at IS NULL AND work_date BETWEEN ? AND ?''',
              (year, a, b), one=True)
    shipped = q('''SELECT COUNT(*) n, COALESCE(SUM(net_kg),0) kg FROM waybills
                   WHERE season_year=? AND status<>'BEKOR' AND document_date BETWEEN ? AND ?''', (year, a, b), one=True)
    rec = q('''SELECT COUNT(*) n, COALESCE(SUM(wb.net_kg),0) sent, COALESCE(SUM(nr.accepted_kg),0) got
               FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id
               WHERE wb.season_year=? AND wb.status='QABUL' AND substr(nr.created_at,1,10) BETWEEN ? AND ?''',
            (year, a, b), one=True)
    diff = rec['got'] - rec['sent']
    return {'field_kg': field['kg'], 'hand_kg': field['hand'], 'combine_kg': field['comb'], 'people': field['people'],
            'shipped_n': shipped['n'], 'shipped_kg': shipped['kg'],
            'received_n': rec['n'], 'received_sent_kg': rec['sent'], 'received_kg': rec['got'],
            'diff_kg': diff, 'diff_pct': round(diff / rec['sent'] * 100, 2) if rec['sent'] else None}


def trips_now():
    """Current state (“Hozir”): each trip in exactly one state."""
    dalada = q('''SELECT COUNT(*) n, COALESCE(SUM(tl.internal_kg),0) kg FROM trailer_loads tl WHERE tl.status='TOLDI'
                  AND NOT EXISTS (SELECT 1 FROM waybills wb WHERE wb.load_id=tl.id AND wb.status<>'BEKOR')''', one=True)
    r = q('''SELECT SUM(CASE WHEN arrived_at IS NULL THEN 1 ELSE 0 END) yolda,
                    COALESCE(SUM(CASE WHEN arrived_at IS NULL THEN net_kg END),0) yolda_kg,
                    SUM(CASE WHEN arrived_at IS NOT NULL THEN 1 ELSE 0 END) navbat,
                    COALESCE(SUM(CASE WHEN arrived_at IS NOT NULL THEN net_kg END),0) navbat_kg
             FROM waybills WHERE status='YARATILDI' ''', one=True)
    ochiq = scalar("SELECT COUNT(*) FROM trailer_loads WHERE status='OCHIQ'")
    return {'dalada': dalada['n'], 'dalada_kg': dalada['kg'], 'yolda': r['yolda'] or 0, 'yolda_kg': r['yolda_kg'],
            'navbat': r['navbat'] or 0, 'navbat_kg': r['navbat_kg'], 'ochiq': ochiq}


TRIP_SELECT = '''SELECT tl.id, tl.trip_no, tl.status, tl.internal_kg, tl.full_at, tl.opened_at, t.code trailer_code,
                        f.name field_name, f.code field_code, b.name brigadier_name, st.name station_name,
                        wb.id wb_id, wb.number wb_number, wb.net_kg sent_kg, wb.created_at sent_at, wb.arrived_at,
                        wb.status wb_status, nr.accepted_kg, nr.diff_kg, nr.diff_reason, nr.created_at received_at,
                        w.basis
                 FROM trailer_loads tl JOIN equipment t ON t.id=tl.trailer_id LEFT JOIN fields f ON f.id=tl.field_id
                 LEFT JOIN brigadiers b ON b.id=tl.brigadier_id LEFT JOIN stations st ON st.id=tl.station_id
                 LEFT JOIN waybills wb ON wb.load_id=tl.id AND wb.status<>'BEKOR'
                 LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id LEFT JOIN weighings w ON w.load_id=tl.id'''


def trip_state(t):
    if t['accepted_kg'] is not None:
        return 'Qabul qilindi', 'b-green'
    if t['wb_id'] and t['arrived_at']:
        return 'Punktda navbatda', 'b-purple'
    if t['wb_id']:
        return 'Yo‘lda', 'b-orange'
    if t['status'] == 'TOLDI':
        return 'Dalada to‘lgan', 'b-blue'
    if t['status'] == 'OCHIQ':
        return 'Terilmoqda', 'b-gray'
    return t['status'], 'b-gray'


def _trips(where, params, limit=200):
    out = []
    for r in q(f'{TRIP_SELECT} WHERE {where} ORDER BY tl.id DESC LIMIT ?', params + [limit]):
        d = dict(r)
        d['state'], d['badge'] = trip_state(r)
        out.append(d)
    return out


def moving_trips():
    return _trips("(tl.status='TOLDI' AND wb.id IS NULL) OR wb.status='YARATILDI'", [])


def period_trips(year, a, b):
    return _trips("tl.season_year=? AND tl.status<>'BEKOR' AND tl.load_date BETWEEN ? AND ?", [year, a, b])


def trip(load_id):
    rows = _trips('tl.id=?', [load_id], 1)
    return rows[0] if rows else None


def by_field(year, a, b):
    rows = {r['id']: dict(r, field_kg=0, received_kg=0, received_sent_kg=0, trips=0) for r in
            q('SELECT f.id, f.code, f.name, b.name brigadier_name FROM fields f LEFT JOIN brigadiers b ON b.id=f.brigadier_id')}
    for r in q('''SELECT field_id, SUM(kg) kg FROM harvests WHERE season_year=? AND voided_at IS NULL
                  AND work_date BETWEEN ? AND ? GROUP BY field_id''', (year, a, b)):
        if r['field_id'] in rows:
            rows[r['field_id']]['field_kg'] = r['kg']
    for r in q('''SELECT tl.field_id, COUNT(*) n, SUM(wb.net_kg) sent, SUM(nr.accepted_kg) got
                  FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id JOIN trailer_loads tl ON tl.id=wb.load_id
                  WHERE wb.season_year=? AND wb.status='QABUL' AND substr(nr.created_at,1,10) BETWEEN ? AND ?
                  GROUP BY tl.field_id''', (year, a, b)):
        if r['field_id'] in rows:
            rows[r['field_id']].update(received_kg=r['got'], received_sent_kg=r['sent'], trips=r['n'])
    out = [x for x in rows.values() if x['field_kg'] or x['received_kg']]
    for x in out:
        x['diff_kg'] = x['received_kg'] - x['received_sent_kg'] if x['trips'] else None
    return sorted(out, key=lambda x: -x['field_kg'])


def by_worker(year, a, b, limit=50):
    rows = q('''SELECT w.id, w.full_name, w.note, SUM(h.kg) kg, COUNT(*) n,
                       GROUP_CONCAT(DISTINCT br.name) brigades
                FROM harvests h JOIN workers w ON w.id=h.worker_id LEFT JOIN brigadiers br ON br.id=h.brigadier_id
                WHERE h.season_year=? AND h.voided_at IS NULL AND h.method='hand' AND h.work_date BETWEEN ? AND ?
                GROUP BY w.id ORDER BY kg DESC LIMIT ?''', (year, a, b, limit))
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ cash

def cash(a, b):
    db = get_db()
    boxes = []
    for bx in q('SELECT id, name, active FROM cashboxes ORDER BY id'):
        r = q('''SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount END),0) i,
                        COALESCE(SUM(CASE WHEN direction='OUT' THEN amount END),0) o
                 FROM cash_entries WHERE cashbox_id=? AND voided_at IS NULL AND entry_date BETWEEN ? AND ?''',
              (bx['id'], a, b), one=True)
        opening = A.box_balance(db, bx['id'], before_day=a)
        boxes.append({'id': bx['id'], 'name': bx['name'], 'active': bx['active'], 'opening': opening, 'inflow': r['i'],
                      'outflow': r['o'], 'closing': opening + r['i'] - r['o'], 'now': A.box_balance(db, bx['id'])})
    tot = {k: sum(x[k] for x in boxes) for k in ('opening', 'inflow', 'outflow', 'closing', 'now')}
    out = q('''SELECT category, SUM(amount) amount FROM cash_entries WHERE direction='OUT' AND voided_at IS NULL
               AND entry_date BETWEEN ? AND ? GROUP BY category ORDER BY amount DESC''', (a, b))
    from .services import CASH_CATEGORIES
    groups = [{'label': CASH_CATEGORIES.get(r['category'], ('', r['category']))[1], 'amount': r['amount']} for r in out]
    exp = q('''SELECT category, SUM(amount) amount FROM expenses WHERE voided_at IS NULL AND expense_date BETWEEN ? AND ?
               GROUP BY category ORDER BY amount DESC''', (a, b))
    return {'boxes': boxes, 'total': tot, 'out_groups': groups, 'expenses': [dict(r) for r in exp]}


def cash_ops(a, b, limit=30):
    from .wallet import ENTRY_SELECT, describe
    return [describe(r) for r in q(f'''{ENTRY_SELECT} WHERE ce.entry_date BETWEEN ? AND ?
                                        ORDER BY ce.id DESC LIMIT ?''', (a, b, limit))]


# ------------------------------------------------------------------ wages

def wages(year, a, b):
    rows = A.worker_balances(year)
    earned = sum(r['earned'] for r in rows)
    given = sum(r['paid'] + r['advances'] for r in rows)
    debt = sum(r['balance'] for r in rows if r['balance'] > 0)
    over = sum(-r['balance'] for r in rows if r['balance'] < 0)
    per = q('''SELECT COALESCE(SUM(amount),0) amt, COALESCE(SUM(CASE WHEN amount IS NULL THEN kg END),0) uncalc,
                      COUNT(DISTINCT worker_id) people
               FROM harvests WHERE season_year=? AND method='hand' AND voided_at IS NULL AND work_date BETWEEN ? AND ?''',
            (year, a, b), one=True)
    paid = q('''SELECT COALESCE(SUM(CASE WHEN category IN ('worker_pay','advance') THEN amount END),0)
                     - COALESCE(SUM(CASE WHEN category IN ('refund_worker_pay','refund_advance') THEN amount END),0) paid,
                       COUNT(DISTINCT CASE WHEN category IN ('worker_pay','advance') THEN worker_id END) people
                FROM cash_entries WHERE voided_at IS NULL AND worker_id IS NOT NULL AND entry_date BETWEEN ? AND ?''',
             (a, b), one=True)
    return {'earned_all': earned, 'given_all': given, 'debt_now': debt, 'over_now': over, 'people': len(rows),
            'owed_people': sum(1 for r in rows if r['balance'] > 0),
            'earned_period': per['amt'], 'uncalc_kg': per['uncalc'], 'worked_people': per['people'],
            'paid_period': paid['paid'], 'paid_people': paid['people'], 'rows': rows}


# ------------------------------------------------------------------ fuel

def fuel(a, b):
    from . import fuel as F
    if not scalar('SELECT COUNT(*) FROM fuel_tickets') and not scalar('SELECT COUNT(*) FROM fuel_ops'):
        return None                                    # not set up yet → “Ulanmagan”
    t = F.totals(a, b)
    t['keepers'] = [k for k in F.keepers() if k['liters'] or k['active']]
    t['tickets'] = [x for x in F.tickets_overview() if x['status'] == 'AKTIV']
    return t


# ------------------------------------------------------------------ what needs a look

def checks(year, a, b):
    """Things that need a look — worded as “tekshirish kerak”, never as a conclusion."""
    items = []
    warn = get_float('punkt_alert_pct', 3) or 3
    for t in _trips("wb.status='QABUL' AND substr(nr.created_at,1,10) BETWEEN ? AND ?", [a, b]):
        if t['sent_kg'] and abs(t['diff_kg']) / t['sent_kg'] * 100 > warn:
            pct = t['diff_kg'] / t['sent_kg'] * 100
            items.append({'level': 'red', 'title': f'{t["trip_no"]}: punktda farq {t["diff_kg"]:+,.0f} kg ({pct:+.1f}%)'.replace(',', ' '),
                          'sub': f'{t["field_name"] or ""} · jo‘natilgan {t["sent_kg"]:,.0f} kg · qabul {t["accepted_kg"]:,.0f} kg'.replace(',', ' ')
                                 + (f' · sabab: {t["diff_reason"]}' if t['diff_reason'] else ''),
                          'time': (t['received_at'] or '')[:16], 'link': ('rahbar.trip', {'load_id': t['id']})})
    for d in q('''SELECT cd.*, cb.name box FROM cash_days cd JOIN cashboxes cb ON cb.id=cd.cashbox_id
                  WHERE cd.day BETWEEN ? AND ? AND cd.diff<>0 ORDER BY cd.day DESC''', (a, b)):
        items.append({'level': 'red', 'title': f'{d["box"]}: kun yopishda sanalgan pul hisobdan {d["diff"]:+,} so‘m farq qildi'.replace(',', ' '),
                      'sub': f'{d["day"][8:10]}.{d["day"][5:7]} · sabab: {d["reason"] or "—"}', 'time': d['closed_at'][:16],
                      'link': ('rahbar.cash', {})})
    n = scalar("SELECT COUNT(*) FROM cash_corrections WHERE status='KUTILMOQDA'")
    if n:
        items.append({'level': 'yellow', 'title': f'{n} ta kassa tuzatish so‘rovi buxgalter qarorini kutmoqda', 'sub': '',
                      'time': '', 'link': ('hamyon.fixes', {})})
    n = scalar("SELECT COUNT(*) FROM cash_corrections WHERE status='BAJARILDI' AND substr(decided_at,1,10) BETWEEN ? AND ?", (a, b))
    if n:
        items.append({'level': 'yellow', 'title': f'{n} ta kassa yozuvi tuzatildi', 'sub': 'Kim, qachon, nima sababdan — ro‘yxatda',
                      'time': '', 'link': ('hamyon.fixes', {})})
    n = scalar("SELECT COUNT(*) FROM expenses WHERE status='TEKSHIRILMAGAN' AND voided_at IS NULL")
    if n:
        items.append({'level': 'yellow', 'title': f'{n} ta xarajat buxgalter tekshiruvini kutmoqda', 'sub': '', 'time': '',
                      'link': None})
    n = scalar("SELECT COUNT(*) FROM harvests WHERE season_year=? AND method='hand' AND amount IS NULL AND voided_at IS NULL", (year,))
    if n:
        items.append({'level': 'yellow', 'title': f'{n} ta tortish narxsiz — ish haqi hisoblanmagan', 'sub': '', 'time': '',
                      'link': None})
    y = (date.fromisoformat(today_str()) - timedelta(days=1)).isoformat()
    for bx in q('SELECT id, name FROM cashboxes WHERE active=1'):
        had = scalar('SELECT COUNT(*) FROM cash_entries WHERE cashbox_id=? AND entry_date<=? AND voided_at IS NULL', (bx['id'], y))
        closed = scalar('SELECT MAX(day) FROM cash_days WHERE cashbox_id=?', (bx['id'],))
        if had and (not closed or closed < y):
            items.append({'level': 'yellow', 'title': f'{bx["name"]}: kecha kun yopilmagan (pul sanab solishtirilmagan)',
                          'sub': 'Bu kamomad degani emas — faqat sanab tekshirilmagan', 'time': '', 'link': None})
    for o in q('''SELECT o.id, o.doc_no, o.liters, o.flag, o.reason, o.reason_note, o.created_at, e.code
                  FROM fuel_ops o JOIN equipment e ON e.id=o.equipment_id WHERE o.kind='BERISH' AND o.flag IS NOT NULL
                  AND o.voided_at IS NULL AND substr(o.created_at,1,10) BETWEEN ? AND ? ORDER BY o.id DESC''', (a, b)):
        items.append({'level': 'red', 'title': f'Solyarka — tekshirish kerak: {o["code"]} {o["liters"]:g} L',
                      'sub': f'{o["flag"]} · sabab: {o["reason"]}' + (f' — {o["reason_note"]}' if o['reason_note'] else ''),
                      'time': o['created_at'][:16], 'link': ('yoqilgi.op_view', {'op_id': o['id']})})
    failed = scalar("SELECT COUNT(*) FROM outbox WHERE status='failed'")
    if failed:
        items.append({'level': 'yellow', 'title': f'Tizim: {failed} ta xabar (Telegram / Sheets) yuborilmadi',
                      'sub': 'Ma’lumot bazada saqlangan; faqat nusxa yetmagan', 'time': '', 'link': None})
    items.sort(key=lambda i: 0 if i['level'] == 'red' else 1)
    return items


# ------------------------------------------------------------------ feed

FEED_ACTIONS = [('TOLDI', 'trailer_load'), ('CREATE', 'waybill'), ('ARRIVED', 'waybill'), ('RECEIVE', 'waybill'),
                ('CREATE', 'nayman_receipt'), ('CREATE', 'cash_entry'), ('CORRECT', 'cash_entry'),
                ('OLISH', 'fuel_op'), ('BERISH', 'fuel_op')]


def feed(limit=8):
    cond = ' OR '.join('(a.action=? AND a.entity_type=?)' for _ in FEED_ACTIONS)
    params = [x for pair in FEED_ACTIONS for x in pair]
    out = []
    from .services import CASH_CATEGORIES
    for r in q(f'''SELECT a.*, u.full_name FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id WHERE {cond}
                   ORDER BY a.id DESC LIMIT ?''', params + [limit * 2]):
        new = json.loads(r['new_json'] or '{}')
        et, act = r['entity_type'], r['action']
        text, icon, link = '', 'doc', None
        if et == 'trailer_load':
            t = trip(int(r['entity_id']))
            if not t:
                continue
            text, icon, link = f'{t["trailer_code"]} to‘ldi · {t["field_name"] or ""}', 'trailer', ('rahbar.trip', {'load_id': t['id']})
        elif et == 'waybill':
            wb = q('SELECT wb.number, wb.load_id, t.code FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id '
                   'JOIN equipment t ON t.id=tl.trailer_id WHERE wb.id=?', (int(r['entity_id']),), one=True)
            if not wb:
                continue
            verb = {'CREATE': 'punktga jo‘nadi', 'ARRIVED': 'punktga yetib keldi', 'RECEIVE': 'punktda qabul qilindi'}[act]
            text, icon, link = f'{wb["code"]} {verb} · {wb["number"]}', 'truck', ('rahbar.trip', {'load_id': wb['load_id']})
        elif et == 'nayman_receipt':
            text, icon = f'Punkt qabul: {new.get("accepted_kg", 0):,.0f} kg'.replace(',', ' '), 'factory'
        elif et == 'cash_entry':
            if act == 'CORRECT':
                text, icon = 'Kassa yozuvi tuzatildi', 'edit'
            else:
                lab = CASH_CATEGORIES.get(new.get('category'), ('', new.get('category') or ''))[1]
                sign = '+' if new.get('direction') == 'IN' else '−'
                text, icon = f'{lab}: {sign}{new.get("amount", 0):,} so‘m'.replace(',', ' '), 'cash'
            link = ('hamyon.entry', {'cash_id': int(r['entity_id']), 'ro': 1})
        elif et == 'fuel_op':
            o = q('SELECT o.*, s.code sc, e.code ec FROM fuel_ops o LEFT JOIN fuel_stations s ON s.id=o.station_id '
                  'LEFT JOIN equipment e ON e.id=o.equipment_id WHERE o.id=?', (int(r['entity_id']),), one=True)
            if not o:
                continue
            text = (f'{o["sc"]}: solyarka olindi +{o["liters"]:g} L' if act == 'OLISH'
                    else f'{o["ec"]} ga solyarka berildi −{o["liters"]:g} L')
            icon, link = 'fuel', ('yoqilgi.op_view', {'op_id': o['id']})
        out.append({'time': r['created_at'][11:16], 'date': r['created_at'][:10], 'text': text, 'icon': icon, 'link': link})
        if len(out) >= limit:
            break
    return out
