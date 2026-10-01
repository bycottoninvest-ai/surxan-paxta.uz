"""Buxgalteriya va kassa: pay-rate history, worker/combine balances, payment orders, cash boxes, day close, debts.

Rules (all enforced here, whichever screen or channel calls them):
- Money is whole so‘m (INTEGER). Nothing is recalculated backwards: the rate in force when a kg was written is
  frozen on that row (harvests.rate / amount); a later rate change only affects new rows.
- "Hisoblandi" (earned) and "pul berildi" (cash out) are different records. A payment order (payout) is prepared by
  the accountant and handed out by the cashier exactly once (status TAYYOR → BERILDI, checked in one UPDATE).
- Kassa: KIRIM − CHIQIM = QOLDIQ per cash box. A closed day is locked; corrections are new, dated entries.
- Nothing is deleted: mistakes are voided with a reason (audit keeps who/what/when/old/new/why).
- Every operation has a unique id (INC-/EXP-/PAY-/ADJ-/DEB-YYYY-NNNNNN) that the Google Sheets mirror upserts on.
"""
import time
from datetime import date, timedelta

from .db import cash_prefix, doc_number, get_db, q, scalar, tx
from .outbox import enqueue
from .photos import store_photo
from .security import audit
from .settings import get_float, get_setting
from .utils import UserError, clean_text, now_str, today_str

TARIFF_TYPES = {'tonna': 'Tonnaga', 'gektar': 'Gektarga', 'kunlik': 'Kunlik'}
DAY_DIFF_REASONS = ['Sanashda xato', 'Yozilmagan xarajat', 'Yozilmagan kirim', 'Qaytim / mayda pul', 'Boshqa']
INCOME_SOURCES_DEFAULT = 'Direktor, Nayman (paxta puli), Boshqa'


def _need(actor, perm):
    if not actor.can(perm):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')


def _reason(text, what):
    text = clean_text(text, 300)
    if len(text) < 3:
        raise UserError(f'{what}: sabab yozilishi shart.')
    return text


def season_of(db):
    from .services import current_season
    return current_season(db)


def fmt_som(v):
    return f'{int(v):,}'.replace(',', ' ') + ' so‘m' if v is not None else 'hisoblanmagan'


# ------------------------------------------------------------------ rates (frozen per row)

def hand_rate(db=None):
    r = get_float('worker_rate_hand', None, db)
    return int(round(r)) if r and r > 0 else None


def combine_standard(db=None):
    """The one combine rate the owner set for everybody, so‘m/kg (None = not set)."""
    r = get_float('combine_rate_standard', None, db)
    return int(round(r)) if r and r > 0 else None


def check_combine_rate(actor, rate_kg, db=None):
    """Only an Admin may write a combine rate other than the standard one (in a trip or a combine's tariff)."""
    std = combine_standard(db)
    if std and rate_kg is not None and int(round(rate_kg)) != std and actor.role != 'admin':
        raise UserError(f'Kombayn narxi — {std:,} so‘m/kg (standart). Boshqa narx faqat Admin ruxsati bilan: '
                        f'Admin o‘zi yozadi.'.replace(',', ' '))


def price_harvest(db, method, kg, combine_id, load=None):
    """(rate, unit, amount) to freeze on a new harvest row. None amount = no rate set yet ("hisoblanmagan").
    A trip opened with its own rate (so‘m/kg, hand or combine) prices every weighing of that trip at that rate —
    except a combine with its own tariff per tonne: the accountant's tariff wins over the rate typed in the field.
    (A combine's money is not this amount: see combine_rows — it is paid on the punkt kg.)"""
    keys = load.keys() if load is not None else ()
    if method == 'combine':
        eq = db.execute('SELECT tariff_type, tariff_rate FROM equipment WHERE id=?', (combine_id,)).fetchone()
        if eq and eq['tariff_type'] == 'tonna' and eq['tariff_rate']:
            return eq['tariff_rate'], 'tonna', int(round(kg * eq['tariff_rate'] / 1000))
    if load is not None and 'rate' in keys and load['rate'] and load['method'] == method:
        return load['rate'], 'kg', int(round(kg * load['rate']))
    if method == 'hand':
        rate = hand_rate(db)
        return (rate, 'kg', int(round(kg * rate))) if rate else (None, None, None)
    return None, None, None


def after_combine_harvest(db, actor, season, combine_id, work_date, field_id):
    """Day-rate combines: the first weighing of the day books one working day at today's tariff (once per day)."""
    eq = db.execute('SELECT tariff_type, tariff_rate FROM equipment WHERE id=?', (combine_id,)).fetchone()
    if not eq or eq['tariff_type'] != 'kunlik' or not eq['tariff_rate']:
        return
    db.execute('''INSERT OR IGNORE INTO combine_work(season_year, combine_id, work_date, unit, qty, rate, amount, field_id,
                      source, created_by, created_at) VALUES (?,?,?,'kunlik',1,?,?,?,'auto',?,?)''',
               (season, combine_id, work_date, eq['tariff_rate'], eq['tariff_rate'], field_id, actor.user_id, now_str()))


# ------------------------------------------------------------------ cash boxes

def cashboxes(active=True):
    return q('SELECT * FROM cashboxes' + (' WHERE active=1' if active else '') + ' ORDER BY id')


def default_cashbox(db):
    row = db.execute('SELECT id FROM cashboxes WHERE active=1 ORDER BY id LIMIT 1').fetchone()
    if not row:
        raise UserError('Kassa yaratilmagan. Admin → Kassalar.')
    return row['id']


def _cashbox(db, actor, cashbox_id):
    """Cashier: always their own box. Others: the chosen one or the default."""
    if actor.role == 'cashier':
        own = db.execute('SELECT cashbox_id FROM users WHERE id=?', (actor.user_id,)).fetchone()
        if own and own['cashbox_id']:
            return own['cashbox_id']
    if cashbox_id:
        if not db.execute('SELECT 1 FROM cashboxes WHERE id=? AND active=1', (cashbox_id,)).fetchone():
            raise UserError('Kassa topilmadi yoki faol emas.')
        return int(cashbox_id)
    return default_cashbox(db)


def box_balance(db, cashbox_id, before_day=None, upto_day=None):
    where, params = ['voided_at IS NULL', 'cashbox_id=?'], [cashbox_id]
    if before_day:
        where.append('entry_date < ?'); params.append(before_day)
    if upto_day:
        where.append('entry_date <= ?'); params.append(upto_day)
    row = db.execute(f'''SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount END),0) i,
                                COALESCE(SUM(CASE WHEN direction='OUT' THEN amount END),0) o
                         FROM cash_entries WHERE {' AND '.join(where)}''', params).fetchone()
    return row['i'] - row['o']


def assert_day_open(db, cashbox_id, day):
    closed = db.execute('SELECT MAX(day) d FROM cash_days WHERE cashbox_id=?', (cashbox_id,)).fetchone()['d']
    if closed and day <= closed:
        raise UserError(f'{closed[8:10]}.{closed[5:7]} gacha kassa kuni yopilgan — bu sanaga yozib/o‘zgartirib bo‘lmaydi. '
                        'Tuzatishni bugungi sana bilan alohida yozing.')


def _assert_cash(db, cashbox_id, amount):
    bal = box_balance(db, cashbox_id)
    if amount > bal:
        raise UserError(f'Kassada yetarli pul yo‘q: qoldiq {fmt_som(bal)}, chiqim {fmt_som(amount)}.')


def book_cash(db, actor, *, direction, category, amount, entry_date=None, cashbox_id=None, worker_id=None,
              combine_id=None, payout_id=None, debt_id=None, expense_id=None, counterparty='', source='', note='',
              doc_no=None, client_uuid=None, check_balance=True):
    """Insert one cash movement with its document number (caller holds the transaction)."""
    season = season_of(db)
    entry_date = entry_date or today_str()
    cashbox_id = cashbox_id or default_cashbox(db)
    assert_day_open(db, cashbox_id, entry_date)
    if direction == 'OUT' and check_balance:
        _assert_cash(db, cashbox_id, amount)
    doc_no = doc_no or doc_number(db, cash_prefix(direction, category), season)
    cur = db.execute('''INSERT INTO cash_entries(season_year, entry_date, direction, category, amount, worker_id, expense_id,
                            counterparty, note, client_uuid, created_by, created_at, doc_no, cashbox_id, source, combine_id,
                            payout_id, debt_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                     (season, entry_date, direction, category, amount, worker_id, expense_id, clean_text(counterparty, 80),
                      clean_text(note, 300), client_uuid, actor.user_id, now_str(), doc_no, cashbox_id,
                      clean_text(source, 80), combine_id, payout_id, debt_id))
    cid = cur.lastrowid
    audit(db, actor, 'CREATE', 'cash_entry', cid,
          new={'doc_no': doc_no, 'direction': direction, 'category': category, 'amount': amount, 'cashbox_id': cashbox_id,
               'worker_id': worker_id, 'combine_id': combine_id, 'payout_id': payout_id, 'source': source})
    mirror_cash(db, cid)
    _feed_cash(db, cid)
    return cid, doc_no


def _feed_cash(db, cid):
    from .reporting import feed
    from .services import CASH_CATEGORIES
    e = db.execute('''SELECT ce.*, w.full_name worker, eq.code combine, cb.name box, u.full_name by_name
                      FROM cash_entries ce LEFT JOIN workers w ON w.id=ce.worker_id
                      LEFT JOIN equipment eq ON eq.id=ce.combine_id LEFT JOIN cashboxes cb ON cb.id=ce.cashbox_id
                      LEFT JOIN users u ON u.id=ce.created_by WHERE ce.id=?''', (cid,)).fetchone()
    label = CASH_CATEGORIES.get(e['category'], ('', e['category']))[1]
    who = e['worker'] or e['combine'] or e['counterparty'] or ''
    sign = '🟢 KIRIM' if e['direction'] == 'IN' else '🔴 CHIQIM'
    feed(db, f'cash:{cid}', f'{sign} {fmt_som(e["amount"])} · {label}' + (f' · {who}' if who else '')
         + f'\n{e["doc_no"]} · {e["box"] or "kassa"}' + (f' · {e["by_name"]}' if e['by_name'] else '')
         + (f'\n{e["note"]}' if e['note'] else ''))


def add_income(actor, *, amount, source, cashbox_id=None, note='', photo=None, client_uuid=None, entry_date=None, check=None):
    """PUL KIRIMI: a new entry every time (earlier income is never edited). check(db, box): optional in-transaction guard."""
    _need(actor, 'cash.write')
    if not amount or amount <= 0:
        raise UserError('Summa 0 dan katta bo‘lishi kerak.')
    source = clean_text(source, 80)
    if not source:
        raise UserError('Pul qayerdan kelgani (manba) tanlanishi shart.')
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT id, doc_no FROM cash_entries WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return dup['id'], dup['doc_no']
        box = _cashbox(db, actor, cashbox_id)
        if check:
            check(db, box)
        cid, no = book_cash(db, actor, direction='IN', category='income', amount=amount, entry_date=entry_date,
                            cashbox_id=box, source=source, counterparty=source, note=note, client_uuid=client_uuid)
        if photo:
            store_photo(db, actor, photo, category='cash', entity_type='cash_entry', entity_id=cid,
                        caption=f'{no} kirim {fmt_som(amount)}', links={'season_year': season_of(db)})
        return cid, no


def income_sources():
    raw = get_setting('income_sources') or INCOME_SOURCES_DEFAULT
    return [s.strip() for s in raw.split(',') if s.strip()]


# ------------------------------------------------------------------ workers: earned / paid / balance

WORKER_STATUS = {'HISOBLANMAGAN': 'b-gray', 'HISOBLANDI': 'b-blue', 'TO‘LOVGA TAYYOR': 'b-orange',
                 'QISMAN TO‘LANDI': 'b-purple', 'TO‘LANDI': 'b-green', 'AVANS ORTIQCHA': 'b-red'}


def _worker_status(r):
    if r['earned'] == 0 and r['uncalc_kg'] > 0:
        return 'HISOBLANMAGAN'
    if r['pending'] > 0:
        return 'TO‘LOVGA TAYYOR'
    if r['balance'] < 0:
        return 'AVANS ORTIQCHA'
    if r['earned'] > 0 and r['balance'] == 0:
        return 'TO‘LANDI'
    if r['paid'] + r['advances'] > 0:
        return 'QISMAN TO‘LANDI'
    return 'HISOBLANDI'


def worker_balances(year, *, worker_id=None, search='', status=None, brigadier_id=None, field_id=None, day=None):
    """One row per worker: kg, earned (frozen rates), paid, advances, pending order, balance, status."""
    where, params = ['1=1'], []
    if worker_id:
        where.append('w.id=?'); params.append(worker_id)
    if search:
        where.append('(w.full_name LIKE ? OR IFNULL(w.phone,\'\') LIKE ?)'); params += [f'%{search}%'] * 2
    if brigadier_id:
        where.append('EXISTS (SELECT 1 FROM harvests h WHERE h.worker_id=w.id AND h.brigadier_id=? AND h.season_year=?)')
        params += [brigadier_id, year]
    if field_id:
        where.append('EXISTS (SELECT 1 FROM harvests h WHERE h.worker_id=w.id AND h.field_id=? AND h.season_year=?)')
        params += [field_id, year]
    if day:
        where.append('EXISTS (SELECT 1 FROM harvests h WHERE h.worker_id=w.id AND h.work_date=? AND h.voided_at IS NULL)')
        params.append(day)
    rows = q(f'''SELECT w.id, w.full_name, w.phone, b.name brigadier_name,
                   COALESCE(h.kg,0) kg, COALESCE(h.earned,0) earned, COALESCE(h.uncalc_kg,0) uncalc_kg, h.last_day, h.days,
                   COALESCE(c.paid,0) paid, COALESCE(c.advances,0) advances, COALESCE(p.pending,0) pending, p.pending_id,
                   p.pending_doc
                FROM workers w LEFT JOIN brigadiers b ON b.id=w.brigadier_id
                LEFT JOIN (SELECT worker_id, SUM(kg) kg, SUM(amount) earned, SUM(CASE WHEN amount IS NULL THEN kg END) uncalc_kg,
                                  MAX(work_date) last_day, COUNT(DISTINCT work_date) days
                           FROM harvests WHERE season_year=? AND method='hand' AND voided_at IS NULL GROUP BY worker_id) h
                       ON h.worker_id=w.id
                LEFT JOIN (SELECT worker_id,
                                  SUM(CASE WHEN category='worker_pay' THEN amount WHEN category='refund_worker_pay' THEN -amount END) paid,
                                  SUM(CASE WHEN category='advance' THEN amount WHEN category='refund_advance' THEN -amount END) advances
                           FROM cash_entries WHERE season_year=? AND voided_at IS NULL AND worker_id IS NOT NULL
                           GROUP BY worker_id) c ON c.worker_id=w.id
                LEFT JOIN (SELECT worker_id, SUM(amount) pending, MAX(id) pending_id, MAX(doc_no) pending_doc FROM payouts
                           WHERE season_year=? AND kind='worker' AND status='TAYYOR' GROUP BY worker_id) p ON p.worker_id=w.id
                WHERE (h.worker_id IS NOT NULL OR c.worker_id IS NOT NULL OR p.worker_id IS NOT NULL) AND {' AND '.join(where)}
                ORDER BY h.last_day DESC, w.full_name''', [year, year, year] + params)
    out = []
    for r in rows:
        d = dict(r)
        d['balance'] = d['earned'] - d['paid'] - d['advances']
        d['payable'] = max(0, d['balance'] - d['pending'])
        d['status'] = _worker_status(d)
        d['badge'] = WORKER_STATUS[d['status']]
        if status and d['status'] != status:
            continue
        out.append(d)
    return out


def worker_history(worker_id, year):
    """Every weighing with the rate it was paid at, plus every advance/payment/order."""
    harvests = q('''SELECT h.work_date, h.kg, h.rate, h.rate_unit, h.amount, h.created_at, f.name field_name,
                           tl.trip_no, b.name brigadier_name, h.voided_at
                    FROM harvests h LEFT JOIN fields f ON f.id=h.field_id LEFT JOIN trailer_loads tl ON tl.id=h.load_id
                    LEFT JOIN brigadiers b ON b.id=h.brigadier_id
                    WHERE h.worker_id=? AND h.season_year=? AND h.method='hand' ORDER BY h.id DESC''', (worker_id, year))
    money = q('''SELECT c.*, u.full_name created_name, cb.name cashbox_name FROM cash_entries c
                 LEFT JOIN users u ON u.id=c.created_by LEFT JOIN cashboxes cb ON cb.id=c.cashbox_id
                 WHERE c.worker_id=? AND c.season_year=? ORDER BY c.id DESC''', (worker_id, year))
    orders = q('''SELECT p.*, u.full_name prepared_name, pu.full_name paid_name FROM payouts p
                  LEFT JOIN users u ON u.id=p.prepared_by LEFT JOIN users pu ON pu.id=p.paid_by
                  WHERE p.worker_id=? AND p.season_year=? ORDER BY p.id DESC''', (worker_id, year))
    return harvests, money, orders


# ------------------------------------------------------------------ combines

def combine_owner(c):
    """Whose combine: a hired one carries its owner's name (“Farhod aka (xizmat)”), ours the company's short name."""
    if c['ownership'] == 'external':
        return f"{c['operator_name'] or 'Tashqi'} (xizmat)"
    from .settings import get_setting
    return (get_setting('company_name') or 'SURXON').split()[0]


def combine_rows(year, combine_id=None):
    """Every combine weighing of the season with the kg it is PAID on. The field clerk's kg (dala kg) is kept for
    information only: the money is the combine's share of what the punkt accepted — the cluster's sof (kondition) kg,
    after dirt and moisture, once its PQ-17 is in; until then the punkt netto — times the tariff per tonne.
    In an umumiy yuk the punkt kg is shared over the trailers (and inside a trailer over its combines) by field kg.
    Tariff: the one frozen on the row when it was written per tonne, else the combine's tariff per tonne in force now,
    else the standard combine rate (setting combine_rate_standard). The so‘m/kg typed for a field trip is not used.
    A weighing not yet received at the punkt is “kutilmoqda”: it has no sum yet (never priced on the field kg)."""
    from .pq17 import by_waybill
    pq = by_waybill()
    std = combine_standard()
    hs = q('''SELECT h.id, h.combine_id, h.work_date, h.load_id, h.kg, h.rate, h.rate_unit, f.name field_name, tl.trip_no,
                     wb.id waybill_id, wb.number waybill_no, nr.accepted_kg, g.id group_id, g.number uy, e.tariff_type, e.tariff_rate,
                     (SELECT SUM(x.kg) FROM harvests x WHERE x.load_id=h.load_id AND x.voided_at IS NULL) load_kg
              FROM harvests h JOIN trailer_loads tl ON tl.id=h.load_id JOIN equipment e ON e.id=h.combine_id
              LEFT JOIN fields f ON f.id=h.field_id
              LEFT JOIN waybills wb ON wb.load_id=h.load_id AND wb.status<>'BEKOR' LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
              LEFT JOIN load_group_items gi ON gi.waybill_id=wb.id LEFT JOIN load_groups g ON g.id=gi.group_id
              WHERE h.season_year=? AND h.method='combine' AND h.voided_at IS NULL''' + (' AND h.combine_id=?' if combine_id else '')
           + ' ORDER BY h.work_date, h.id', (year,) + ((combine_id,) if combine_id else ()))
    out = []
    for h in hs:
        r = dict(h)
        share = h['kg'] / h['load_kg'] if h['load_kg'] else 0
        r['punkt_kg'] = h['accepted_kg'] * share if h['accepted_kg'] is not None else None
        doc = pq.get(h['waybill_id']) if r['punkt_kg'] is not None else None
        r['sof_kg'] = doc['kond_kg'] * share if doc else None
        r['pq_code'] = doc['load_no'] if doc else None
        if r['punkt_kg'] is None:
            r['basis'], r['pay_kg'] = 'kutilmoqda', None
        elif r['sof_kg'] is not None:
            r['basis'], r['pay_kg'] = 'pq17', r['sof_kg']
        else:
            r['basis'], r['pay_kg'] = 'punkt', r['punkt_kg']
        if h['rate_unit'] == 'tonna' and h['rate']:
            r['tonne_rate'] = h['rate']
        elif h['tariff_type'] == 'tonna' and h['tariff_rate']:
            r['tonne_rate'] = h['tariff_rate']
        elif not h['tariff_type'] and std:                  # no own tariff: the standard rate for every combine
            r['tonne_rate'] = std * 1000
        else:
            r['tonne_rate'] = None
        r['amount'] = int(round(r['pay_kg'] * r['tonne_rate'] / 1000)) if r['pay_kg'] is not None and r['tonne_rate'] else None
        # a day / hectare tariff pays by combine_work, not by kg — such kg is not “tarifsiz”
        r['uncalc'] = r['pay_kg'] is not None and not r['tonne_rate'] and h['tariff_type'] not in ('kunlik', 'gektar')
        out.append(r)
    return out


def _kg_totals(rows):
    """Dala / punkt / sof / paid-on kg and the sum of a set of combine_rows."""
    t = {'kg': 0, 'punkt_kg': 0, 'sof_kg': 0, 'pay_kg': 0, 'waiting_kg': 0, 'pq_wait_kg': 0, 'uncalc_kg': 0,
         'tonnage_amount': 0, 'provisional_amount': 0}
    for r in rows:
        t['kg'] += r['kg']
        if r['basis'] == 'kutilmoqda':
            t['waiting_kg'] += r['kg']
            continue
        t['punkt_kg'] += r['punkt_kg']
        t['pay_kg'] += r['pay_kg']
        if r['basis'] == 'pq17':
            t['sof_kg'] += r['sof_kg']
        else:
            t['pq_wait_kg'] += r['pay_kg']
        if r['uncalc']:
            t['uncalc_kg'] += r['pay_kg']
        if r['amount'] is not None:
            t['tonnage_amount'] += r['amount']
            if r['basis'] == 'punkt':
                t['provisional_amount'] += r['amount']
    for k in ('punkt_kg', 'sof_kg', 'pay_kg', 'pq_wait_kg', 'uncalc_kg'):
        t[k] = round(t[k], 1)
    return t


def combine_balances(year, combine_id=None):
    rows = q('''SELECT e.id, e.code, e.operator_name, e.ownership, e.tariff_type, e.tariff_rate,
                       COALESCE(w.days_amount,0) days_amount, COALESCE(w.work_days,0) work_days,
                       COALESCE(w.ha,0) hectares, COALESCE(w.ha_amount,0) ha_amount,
                       COALESCE(c.paid,0) paid, COALESCE(p.pending,0) pending
                FROM equipment e
                LEFT JOIN (SELECT combine_id, SUM(CASE WHEN unit='kunlik' THEN amount END) days_amount,
                                  SUM(CASE WHEN unit='kunlik' THEN qty END) work_days,
                                  SUM(CASE WHEN unit='gektar' THEN qty END) ha, SUM(CASE WHEN unit='gektar' THEN amount END) ha_amount
                           FROM combine_work WHERE season_year=? AND voided_at IS NULL GROUP BY combine_id) w ON w.combine_id=e.id
                LEFT JOIN (SELECT combine_id, SUM(CASE WHEN category='combine_pay' THEN amount ELSE -amount END) paid
                           FROM cash_entries WHERE season_year=? AND voided_at IS NULL
                           AND category IN ('combine_pay','refund_combine_pay') GROUP BY combine_id) c ON c.combine_id=e.id
                LEFT JOIN (SELECT combine_id, SUM(amount) pending FROM payouts WHERE season_year=? AND kind='combine'
                           AND status='TAYYOR' GROUP BY combine_id) p ON p.combine_id=e.id
                WHERE e.kind='kombayn' ''' + (' AND e.id=?' if combine_id else '') + ' ORDER BY e.code',
             (year, year, year) + ((combine_id,) if combine_id else ()))
    by_combine = {}
    for r in combine_rows(year, combine_id):
        by_combine.setdefault(r['combine_id'], []).append(r)
    out = []
    for r in rows:
        d = dict(r)
        hs = by_combine.get(d['id'], [])
        d.update(_kg_totals(hs))
        d['days'] = len({h['work_date'] for h in hs}) or None
        d['fields'] = ', '.join(dict.fromkeys(h['field_name'] for h in hs if h['field_name'])) or None
        d['earned'] = d['tonnage_amount'] + d['days_amount'] + d['ha_amount']
        d['own'] = d['ownership'] != 'external'        # shown for information; the combine's pay is counted either way
        d['balance'] = d['earned'] - d['paid']
        d['payable'] = max(0, d['balance'] - d['pending'])
        d['tonnes'] = round(d['kg'] / 1000, 2)
        d['pay_tonnes'] = round(d['pay_kg'] / 1000, 2)
        d['diff_kg'] = round(d['pay_kg'] - (d['kg'] - d['waiting_kg']), 1)     # what the punkt took off the received kg
        out.append(d)
    return out


def trip_photos(trip_list, per_trip=2):
    """For the owner's akt-sverka: each trip's evidence — the field blank / trailer photo and the punkt's stamped paper
    (the umumiy yuk's paper for a trailer weighed together with others). Newest first, at most per_trip each."""
    out = []
    seen_groups = set()
    for t in trip_list:
        rows = q('''SELECT id, path, thumb_path, category, uploaded_at FROM photos WHERE voided_at IS NULL
                    AND (load_id=? OR (waybill_id IS NOT NULL AND waybill_id=?)) ORDER BY category='nayman' DESC, id DESC''',
                 (t['load_id'], t['waybill_id']))
        if t['group_id'] and t['group_id'] not in seen_groups:
            seen_groups.add(t['group_id'])
            rows = list(q('''SELECT id, path, thumb_path, category, uploaded_at FROM photos WHERE voided_at IS NULL
                             AND entity_type='load_group' AND entity_id=? ORDER BY id DESC LIMIT 1''', (t['group_id'],))) + list(rows)
        picked, kinds = [], set()
        for p in rows:                       # one of each kind first: the punkt paper, then the field photo
            kind = 'punkt' if p['category'] == 'nayman' else 'dala'
            if kind not in kinds:
                kinds.add(kind)
                picked.append(dict(p, kind=kind))
            if len(picked) >= per_trip:
                break
        for p in picked:
            out.append(dict(p, trip_no=t['trip_no'], waybill_no=t['waybill_no'], uy=t['uy'], date=t['date']))
    return out


def combine_statement(year, combine_id):
    """Season hisob-kitob of one combine for its owner: day by day — fields, trips, dala kg, its share of the punkt kg
    and of the PQ-17 sof kg (in an umumiy yuk shared by field kg), the sum on that kg — then every payment, and what
    is left to pay."""
    bal = combine_balances(year, combine_id)
    if not bal:
        raise UserError('Kombayn topilmadi.')
    c = bal[0]
    c['owner'] = combine_owner(c)
    days, trips = {}, {}

    def day(key):
        return days.setdefault(key, {'date': key, 'fields': [], 'trips': [], 'rows': [], 'amount': 0, 'work': ''})

    hs = combine_rows(year, combine_id)
    for h in hs:
        t = trips.setdefault(h['load_id'], {'date': h['work_date'], 'load_id': h['load_id'], 'trip_no': h['trip_no'],
                                            'waybill_id': h['waybill_id'], 'waybill_no': h['waybill_no'], 'uy': h['uy'],
                                            'group_id': h['group_id'], 'field': h['field_name'], 'pq_code': h['pq_code'],
                                            'rows': []})
        t['rows'].append(h)
    trip_list = []
    for t in sorted(trips.values(), key=lambda t: (t['date'], t['trip_no'] or '')):
        t.update(_kg_totals(t.pop('rows')))
        t['basis'] = 'kutilmoqda' if t['waiting_kg'] else 'punkt' if t['pq_wait_kg'] else 'pq17'
        trip_list.append(t)
    for h in hs:
        d = day(h['work_date'])
        d['rows'].append(h)
        if h['field_name'] and h['field_name'] not in d['fields']:
            d['fields'].append(h['field_name'])
        trip = (h['trip_no'] or '') + (f' ({h["uy"]})' if h['uy'] else '')
        if trip and trip not in d['trips']:
            d['trips'].append(trip)
    for w in q('''SELECT work_date, unit, qty, amount FROM combine_work WHERE season_year=? AND combine_id=? AND voided_at IS NULL
                  ORDER BY work_date''', (year, combine_id)):
        d = day(w['work_date'])
        d['amount'] += w['amount']
        d['work'] = (d['work'] + ' ' if d['work'] else '') + (f'{w["qty"]:g} ga' if w['unit'] == 'gektar' else f'{w["qty"]:g} kun')
    rows = []
    for k in sorted(days):
        d = days[k]
        t = _kg_totals(d.pop('rows'))
        work_amount = d['amount']
        d.update(t)
        d['amount'] = t['tonnage_amount'] + work_amount
        d['basis'] = ('kutilmoqda' if t['kg'] and t['waiting_kg'] == t['kg'] else
                      'punkt' if t['pq_wait_kg'] else 'pq17' if t['sof_kg'] else '')
        rows.append(d)
    pays = q('''SELECT entry_date, doc_no, category, amount, note FROM cash_entries WHERE season_year=? AND combine_id=?
                AND voided_at IS NULL AND category IN ('combine_pay','refund_combine_pay') ORDER BY entry_date, id''',
             (year, combine_id))
    return {'combine': c, 'days': rows, 'trips': trip_list, 'photos': trip_photos(trip_list),
            'payments': [dict(p, amount=p['amount'] if p['category'] == 'combine_pay' else -p['amount']) for p in pays],
            **{k: c[k] for k in ('kg', 'punkt_kg', 'sof_kg', 'pay_kg', 'waiting_kg', 'pq_wait_kg', 'uncalc_kg', 'provisional_amount')}}


def set_combine_tariff(actor, combine_id, *, tariff_type, tariff_rate, operator_name=None, ownership=None, whole_season=False):
    """New tariff applies to work written from now on; already booked work keeps its own rate — unless the accountant
    asks to apply a per-tonne tariff to the whole season (the tariff was agreed late or typed wrong). That re-freezes
    every combine weighing of the current season at the new rate, with an audit record of how many rows changed."""
    _need(actor, 'combine.finance')
    if tariff_type and tariff_type not in TARIFF_TYPES:
        raise UserError('Hisoblash turi noto‘g‘ri.')
    if tariff_type and (not tariff_rate or tariff_rate <= 0):
        raise UserError('Tarif (so‘m) kiritilishi shart.')
    if whole_season and tariff_type != 'tonna':
        raise UserError('Butun mavsumga faqat “Tonnaga” tarifini qo‘llash mumkin.')
    if tariff_type == 'tonna':
        check_combine_rate(actor, tariff_rate / 1000)
    with tx() as db:
        old = db.execute("SELECT * FROM equipment WHERE id=? AND kind='kombayn'", (combine_id,)).fetchone()
        if not old:
            raise UserError('Kombayn topilmadi.')
        if ownership not in (None, 'own', 'external'):
            raise UserError('Egalik noto‘g‘ri.')
        db.execute('''UPDATE equipment SET tariff_type=?, tariff_rate=?, operator_name=COALESCE(?, operator_name),
                      ownership=COALESCE(?, ownership) WHERE id=?''',
                   (tariff_type or None, tariff_rate if tariff_type else None,
                    clean_text(operator_name, 80) if operator_name is not None else None, ownership, combine_id))
        changed = 0
        if whole_season:
            changed = db.execute('''UPDATE harvests SET rate=?, rate_unit='tonna', amount=CAST(ROUND(kg * ? / 1000.0) AS INTEGER)
                                    WHERE combine_id=? AND method='combine' AND season_year=? AND voided_at IS NULL''',
                                 (tariff_rate, tariff_rate, combine_id, season_of(db))).rowcount
        audit(db, actor, 'TARIFF', 'equipment', combine_id,
              old={'tariff_type': old['tariff_type'], 'tariff_rate': old['tariff_rate'], 'ownership': old['ownership']},
              new={'tariff_type': tariff_type, 'tariff_rate': tariff_rate, 'ownership': ownership or old['ownership'],
                   **({'whole_season_rows': changed} if whole_season else {})})
    return changed


def add_combine_work(actor, combine_id, *, work_date, unit, qty, field_id=None, note=''):
    """Manual work record for hectare (or day) tariffs; the rate in force today is frozen on the row."""
    _need(actor, 'combine.finance')
    if unit not in ('gektar', 'kunlik'):
        raise UserError('Birlik noto‘g‘ri.')
    if not qty or qty <= 0:
        raise UserError('Miqdor 0 dan katta bo‘lishi kerak.')
    with tx() as db:
        eq = db.execute("SELECT * FROM equipment WHERE id=? AND kind='kombayn'", (combine_id,)).fetchone()
        if not eq:
            raise UserError('Kombayn topilmadi.')
        if eq['tariff_type'] != unit or not eq['tariff_rate']:
            raise UserError(f'{eq["code"]} uchun “{TARIFF_TYPES[unit]}” tarifi o‘rnatilmagan.')
        amount = int(round(qty * eq['tariff_rate']))
        try:
            cur = db.execute('''INSERT INTO combine_work(season_year, combine_id, work_date, unit, qty, rate, amount, field_id,
                                    source, note, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,'manual',?,?,?)''',
                             (season_of(db), combine_id, work_date, unit, qty, eq['tariff_rate'], amount, field_id,
                              clean_text(note, 200), actor.user_id, now_str()))
        except Exception:
            raise UserError('Bu kun uchun kombayn ish kuni allaqachon yozilgan.')
        audit(db, actor, 'CREATE', 'combine_work', cur.lastrowid,
              new={'combine': eq['code'], 'unit': unit, 'qty': qty, 'rate': eq['tariff_rate'], 'amount': amount})
        return cur.lastrowid


# ------------------------------------------------------------------ payment orders (to‘lov buyrug‘i)

def prepare_payout(actor, *, kind, target_id, amount=None, purpose='pay', note='', client_uuid=None, cashbox_id=None):
    """Accountant: "X SO‘M TO‘LASH" → ready order for the cashier. Never more than what is owed (advances aside)."""
    _need(actor, 'payouts.prepare')
    if kind not in ('worker', 'combine') or purpose not in ('pay', 'advance'):
        raise UserError('To‘lov turi noto‘g‘ri.')
    if purpose == 'advance' and kind != 'worker':
        raise UserError('Avans faqat ishchiga beriladi.')
    import sqlite3
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT id, doc_no FROM payouts WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return dup['id'], dup['doc_no']
        year = season_of(db)
        if kind == 'worker':
            row = next(iter(worker_balances(year, worker_id=target_id)), None)
            if not db.execute('SELECT 1 FROM workers WHERE id=?', (target_id,)).fetchone():
                raise UserError('Ishchi topilmadi.')
            payable = row['payable'] if row else 0
            name = db.execute('SELECT full_name FROM workers WHERE id=?', (target_id,)).fetchone()['full_name']
        else:
            row = next(iter(combine_balances(year, target_id)), None)
            if not row:
                raise UserError('Kombayn topilmadi.')
            payable, name = row['payable'], row['code']
        if purpose == 'pay':
            amount = amount or payable
            if not amount or amount <= 0:
                raise UserError(f'{name}: to‘lanadigan qoldiq yo‘q.')
            if amount > payable:
                raise UserError(f'{name}: to‘lanadigan qoldiq {fmt_som(payable)} — {fmt_som(amount)} dan ko‘p to‘lab bo‘lmaydi.')
        elif not amount or amount <= 0:
            raise UserError('Avans summasi kiritilishi shart.')
        no = doc_number(db, 'PAY', year)
        try:
            cur = db.execute('''INSERT INTO payouts(doc_no, season_year, kind, purpose, worker_id, combine_id, amount, note,
                                    client_uuid, cashbox_id, prepared_by, prepared_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                             (no, year, kind, purpose, target_id if kind == 'worker' else None,
                              target_id if kind == 'combine' else None, amount, clean_text(note, 200), client_uuid,
                              _cashbox(db, actor, cashbox_id), actor.user_id, now_str()))
        except sqlite3.IntegrityError:
            raise UserError(f'{name}: tayyor (hali berilmagan) to‘lov allaqachon bor. Avval uni kassir bersin yoki bekor qiling.')
        pid = cur.lastrowid
        audit(db, actor, 'PREPARE', 'payout', pid, new={'doc_no': no, 'kind': kind, 'purpose': purpose, 'target': name,
                                                          'amount': amount, 'status': 'TAYYOR'})
        mirror_payout(db, pid)
        return pid, no


def pay_payout(actor, payout_id, check=None):
    """Cashier: "339 000 SO‘M BERILDI". The status flips in one guarded UPDATE, so a double tap pays once.
    check(db, payout, cashbox_id): optional guard run inside the transaction before the money moves."""
    _need(actor, 'payouts.pay')
    with tx() as db:
        p = db.execute('SELECT * FROM payouts WHERE id=?', (payout_id,)).fetchone()
        if not p:
            raise UserError('To‘lov topilmadi.')
        if p['status'] == 'BERILDI':
            return {'already': True, 'doc_no': p['doc_no'], 'amount': p['amount']}
        if p['status'] != 'TAYYOR':
            raise UserError('Bu to‘lov bekor qilingan.')
        box = _cashbox(db, actor, p['cashbox_id'])
        if actor.role == 'cashier' and box != p['cashbox_id'] and p['cashbox_id']:
            raise UserError('Bu to‘lov boshqa kassadan beriladi.')
        if check:
            check(db, p, box)
        category = 'combine_pay' if p['kind'] == 'combine' else ('advance' if p['purpose'] == 'advance' else 'worker_pay')
        who = (db.execute('SELECT full_name FROM workers WHERE id=?', (p['worker_id'],)).fetchone()['full_name']
               if p['kind'] == 'worker' else db.execute('SELECT code FROM equipment WHERE id=?', (p['combine_id'],)).fetchone()['code'])
        ts = now_str()
        n = db.execute("UPDATE payouts SET status='BERILDI', paid_by=?, paid_at=?, cashbox_id=? WHERE id=? AND status='TAYYOR'",
                       (actor.user_id, ts, box, payout_id)).rowcount
        if n != 1:
            return {'already': True, 'doc_no': p['doc_no'], 'amount': p['amount']}
        cid, _ = book_cash(db, actor, direction='OUT', category=category, amount=p['amount'], cashbox_id=box,
                           worker_id=p['worker_id'], combine_id=p['combine_id'], payout_id=payout_id, counterparty=who,
                           note=p['note'] or '', doc_no=p['doc_no'])
        db.execute('UPDATE payouts SET cash_entry_id=? WHERE id=?', (cid, payout_id))
        audit(db, actor, 'PAID', 'payout', payout_id, old={'status': 'TAYYOR'},
              new={'status': 'BERILDI', 'amount': p['amount'], 'to': who, 'cashbox_id': box, 'cash_entry_id': cid})
        mirror_payout(db, payout_id)
        if p['worker_id']:
            mirror_worker(db, p['worker_id'], p['season_year'])
        return {'already': False, 'doc_no': p['doc_no'], 'amount': p['amount'], 'to': who}


def cancel_payout(actor, payout_id, reason):
    """TAYYOR → BEKOR (money never left). A handed-out payment is reversed only by a new, dated correction."""
    _need(actor, 'payouts.prepare')
    reason = _reason(reason, 'To‘lovni bekor qilish')
    with tx() as db:
        p = db.execute('SELECT * FROM payouts WHERE id=?', (payout_id,)).fetchone()
        if not p or p['status'] != 'TAYYOR':
            raise UserError('Faqat hali berilmagan (TAYYOR) to‘lovni bekor qilish mumkin. Berilgan pul — tuzatish yozuvi bilan.')
        db.execute("UPDATE payouts SET status='BEKOR', voided_by=?, voided_at=?, void_reason=? WHERE id=? AND status='TAYYOR'",
                   (actor.user_id, now_str(), reason, payout_id))
        audit(db, actor, 'VOID', 'payout', payout_id, old={'status': 'TAYYOR'}, new={'status': 'BEKOR'}, reason=reason)
        mirror_payout(db, payout_id)


def refund_payout(actor, payout_id, reason):
    """Money that was handed out came back (mistake): a separate ADJ income entry today; the original stays as it was."""
    _need(actor, 'payouts.prepare')
    reason = _reason(reason, 'Qaytarish')
    with tx() as db:
        p = db.execute('SELECT * FROM payouts WHERE id=?', (payout_id,)).fetchone()
        if not p or p['status'] != 'BERILDI':
            raise UserError('Faqat berilgan to‘lov qaytariladi.')
        if db.execute("SELECT 1 FROM cash_entries WHERE payout_id=? AND category LIKE 'refund%' AND voided_at IS NULL",
                      (payout_id,)).fetchone():
            raise UserError('Bu to‘lov allaqachon qaytarilgan.')
        orig = db.execute('SELECT * FROM cash_entries WHERE id=?', (p['cash_entry_id'],)).fetchone()
        # money comes back into the box, and the person's "paid" total goes down by the same amount
        cid, no = book_cash(db, actor, direction='IN', category='refund_' + orig['category'], amount=p['amount'],
                            cashbox_id=p['cashbox_id'], payout_id=payout_id, worker_id=p['worker_id'],
                            combine_id=p['combine_id'], counterparty=orig['counterparty'] or '',
                            note=f'{p["doc_no"]} qaytarildi: {reason}', check_balance=False)
        audit(db, actor, 'REFUND', 'payout', payout_id, new={'refund_doc': no, 'amount': p['amount']}, reason=reason)
        return no


# ------------------------------------------------------------------ expenses

EXPENSE_BUTTONS = [
    ('Yoqilg‘i', 'truck'), ('Ovqat', 'cotton'), ('Transport', 'trailer'), ('Ta‘mirlash', 'wrench'), ('Ehtiyot qism', 'gear'),
    ('Punkt xarajati', 'factory'), ('Boshqa', 'receipt'),
]
# these three are not ordinary expenses — they open their own flows (money owed to a person or combine)
EXPENSE_SHORTCUTS = [('Kombayn', 'combine', 'acct.combines'), ('Ish haqi', 'users', 'acct.workers'),
                     ('Avans', 'wallet', 'acct.workers')]


def approve_expense(actor, expense_id):
    _need(actor, 'expenses.approve')
    with tx() as db:
        e = db.execute('SELECT * FROM expenses WHERE id=?', (expense_id,)).fetchone()
        if not e or e['voided_at']:
            raise UserError('Xarajat topilmadi yoki bekor qilingan.')
        if e['status'] == 'TASDIQLANGAN':
            return False
        db.execute("UPDATE expenses SET status='TASDIQLANGAN', checked_by=?, checked_at=? WHERE id=?",
                   (actor.user_id, now_str(), expense_id))
        audit(db, actor, 'APPROVE', 'expense', expense_id, old={'status': e['status']}, new={'status': 'TASDIQLANGAN'})
        mirror_expense(db, expense_id)
        return True


# ------------------------------------------------------------------ day close

def day_figures(db, cashbox_id, day):
    opening = box_balance(db, cashbox_id, before_day=day)
    row = db.execute('''SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount END),0) i,
                               COALESCE(SUM(CASE WHEN direction='OUT' THEN amount END),0) o
                        FROM cash_entries WHERE voided_at IS NULL AND cashbox_id=? AND entry_date=?''',
                     (cashbox_id, day)).fetchone()
    return {'opening': opening, 'inflow': row['i'], 'outflow': row['o'], 'system': opening + row['i'] - row['o']}


def close_day(actor, *, cashbox_id, day, counted, reason='', note=''):
    """KUNNI YOPISH: counted cash vs system; a difference is booked (ADJ) with its reason and the day is locked."""
    _need(actor, 'dayclose')
    if counted is None or counted < 0:
        raise UserError('Kassadagi real pul kiritilishi shart.')
    if day > today_str():
        raise UserError('Kelajakdagi kunni yopib bo‘lmaydi.')
    with tx() as db:
        box = _cashbox(db, actor, cashbox_id)
        if db.execute('SELECT 1 FROM cash_days WHERE cashbox_id=? AND day=?', (box, day)).fetchone():
            raise UserError('Bu kun allaqachon yopilgan.')
        assert_day_open(db, box, day)
        f = day_figures(db, box, day)
        diff = counted - f['system']
        reason = clean_text(reason, 60)
        note = clean_text(note, 300)
        adjust = None
        if diff:
            if reason not in DAY_DIFF_REASONS:
                raise UserError(f'Farq {fmt_som(diff)}: sababini tanlang.')
            if reason == 'Boshqa' and len(note) < 3:
                raise UserError('“Boshqa” tanlansa izoh yozing.')
            adjust, _ = book_cash(db, actor, direction='IN' if diff > 0 else 'OUT',
                                  category='adjust_in' if diff > 0 else 'adjust_out', amount=abs(diff), entry_date=day,
                                  cashbox_id=box, note=f'Kun yopish farqi: {reason}{": " + note if note else ""}',
                                  check_balance=False)
        cur = db.execute('''INSERT INTO cash_days(cashbox_id, day, opening, inflow, outflow, system_balance, counted, diff, reason,
                                note, adjust_entry_id, closed_by, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (box, day, f['opening'], f['inflow'], f['outflow'], f['system'], counted, diff, reason or None,
                          note or None, adjust, actor.user_id, now_str()))
        audit(db, actor, 'CLOSE_DAY', 'cash_day', cur.lastrowid,
              new={'cashbox_id': box, 'day': day, **f, 'counted': counted, 'diff': diff, 'reason': reason})
        mirror_day(db, cur.lastrowid)
        from .reporting import enqueue_daily_report, enqueue_alert
        if diff:
            enqueue_alert(db, f'close:{cur.lastrowid}', f'🔴 Kassa farqi {day[8:10]}.{day[5:7]}: {fmt_som(diff)} ({reason})')
        enqueue_daily_report(db, day)
        return {'id': cur.lastrowid, 'diff': diff, **f, 'counted': counted}


# ------------------------------------------------------------------ debts (debitor / kreditor)

def add_debt(actor, *, direction, counterparty, amount, reason='', debt_date=None, due_date=None, note='', client_uuid=None):
    _need(actor, 'debts.write')
    if direction not in ('OLISH', 'BERISH'):
        raise UserError('Yo‘nalish noto‘g‘ri.')
    counterparty = clean_text(counterparty, 80)
    if not counterparty or not amount or amount <= 0:
        raise UserError('Kim va summa kiritilishi shart.')
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT id FROM debts WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return dup['id']
        year = season_of(db)
        no = doc_number(db, 'DEB', year)
        cur = db.execute('''INSERT INTO debts(doc_no, season_year, direction, counterparty, amount, reason, debt_date, due_date,
                                note, client_uuid, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (no, year, direction, counterparty, amount, clean_text(reason, 200), debt_date or today_str(),
                          due_date or None, clean_text(note, 300), client_uuid, actor.user_id, now_str()))
        audit(db, actor, 'CREATE', 'debt', cur.lastrowid, new={'doc_no': no, 'direction': direction,
                                                                 'counterparty': counterparty, 'amount': amount})
        mirror_debt(db, cur.lastrowid)
        return cur.lastrowid


def debts(year, direction=None):
    rows = q('''SELECT d.*, COALESCE((SELECT SUM(amount) FROM cash_entries c WHERE c.debt_id=d.id AND c.voided_at IS NULL),0) settled
                FROM debts d WHERE d.season_year=? AND d.voided_at IS NULL''' + (' AND d.direction=?' if direction else '') +
             ' ORDER BY d.due_date IS NULL, d.due_date, d.id DESC', (year,) + ((direction,) if direction else ()))
    out = []
    for r in rows:
        d = dict(r)
        d['remaining'] = d['amount'] - d['settled']
        d['overdue'] = bool(d['due_date'] and d['due_date'] < today_str() and d['remaining'] > 0)
        out.append(d)
    return out


def settle_debt(actor, debt_id, *, amount, cashbox_id=None, note=''):
    """Receivable paid to us → cash IN; payable paid by us → cash OUT. Never more than what is left."""
    _need(actor, 'debts.write')
    with tx() as db:
        d = db.execute('SELECT * FROM debts WHERE id=? AND voided_at IS NULL', (debt_id,)).fetchone()
        if not d:
            raise UserError('Qarz yozuvi topilmadi.')
        settled = db.execute('SELECT COALESCE(SUM(amount),0) FROM cash_entries WHERE debt_id=? AND voided_at IS NULL',
                             (debt_id,)).fetchone()[0]
        left = d['amount'] - settled
        amount = amount or left
        if amount <= 0 or amount > left:
            raise UserError(f'Qolgan summa {fmt_som(left)}.')
        box = _cashbox(db, actor, cashbox_id)
        cid, no = book_cash(db, actor, direction='IN' if d['direction'] == 'OLISH' else 'OUT',
                            category='debt_in' if d['direction'] == 'OLISH' else 'debt_out', amount=amount, cashbox_id=box,
                            debt_id=debt_id, counterparty=d['counterparty'], note=note or d['doc_no'])
        mirror_debt(db, debt_id)
        return no


# ------------------------------------------------------------------ dashboard figures

def cash_totals(db=None, cashbox_id=None, date_from=None, date_to=None):
    db = db or get_db()
    where, params = ['voided_at IS NULL'], []
    if cashbox_id:
        where.append('cashbox_id=?'); params.append(cashbox_id)
    if date_from:
        where.append('entry_date>=?'); params.append(date_from)
    if date_to:
        where.append('entry_date<=?'); params.append(date_to)
    r = db.execute(f'''SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount END),0) i,
                              COALESCE(SUM(CASE WHEN direction='OUT' THEN amount END),0) o
                       FROM cash_entries WHERE {' AND '.join(where)}''', params).fetchone()
    return {'in': r['i'], 'out': r['o']}


def total_balance(db=None):
    db = db or get_db()
    return sum(box_balance(db, b['id']) for b in db.execute('SELECT id FROM cashboxes').fetchall())


def cotton_totals(year, date_from=None, date_to=None):
    """Field kg (harvest rows) vs punkt kg — taken from the field and punkt screens, never typed again."""
    hw, hp = ['h.season_year=?', 'h.voided_at IS NULL'], [year]
    rw, rp = ["wb.status='QABUL'", 'wb.season_year=?'], [year]
    if date_from:
        hw.append('h.work_date>=?'); hp.append(date_from); rw.append('substr(nr.created_at,1,10)>=?'); rp.append(date_from)
    if date_to:
        hw.append('h.work_date<=?'); hp.append(date_to); rw.append('substr(nr.created_at,1,10)<=?'); rp.append(date_to)
    field = scalar(f'''SELECT COALESCE(SUM(h.kg),0) FROM harvests h JOIN trailer_loads tl ON tl.id=h.load_id
                       WHERE tl.status<>'BEKOR' AND {" AND ".join(hw)}''', hp)
    r = q(f'''SELECT COALESCE(SUM(wb.net_kg),0) sent, COALESCE(SUM(nr.accepted_kg),0) got, COUNT(*) n
              FROM waybills wb JOIN nayman_receipts nr ON nr.waybill_id=wb.id WHERE {" AND ".join(rw)}''', rp, one=True)
    return {'field_kg': field, 'sent_kg': r['sent'], 'punkt_kg': r['got'], 'diff_kg': r['got'] - r['sent'],
            'diff_pct': round((r['got'] - r['sent']) / r['sent'] * 100, 2) if r['sent'] else None, 'received': r['n']}


def trip_ledger(year, date_from, date_to):
    """Every trip with picking in the period, as the field clerk wrote it: hand kg / people / pay and combine kg / pay.
    Only live (not cancelled) weighings count; the number of cancelled trips is returned apart, never added."""
    rows = q('''SELECT tl.id, tl.trip_no, tl.status, MIN(h.work_date) day, f.code field, t.code trailer, b.name brigadier,
                       wb.number waybill, wb.status wb_status, nr.accepted_kg,
                       COALESCE(SUM(CASE WHEN h.method='hand' THEN h.kg END),0) hand_kg,
                       COUNT(DISTINCT CASE WHEN h.method='hand' THEN h.worker_id END) people,
                       COALESCE(SUM(CASE WHEN h.method='hand' THEN h.amount END),0) hand_pay,
                       COALESCE(SUM(CASE WHEN h.method='combine' THEN h.kg END),0) combine_kg,
                       COALESCE(SUM(CASE WHEN h.method='combine' THEN h.amount END),0) combine_pay,
                       SUM(CASE WHEN h.amount IS NULL THEN h.kg ELSE 0 END) unpriced_kg
                FROM harvests h JOIN trailer_loads tl ON tl.id=h.load_id
                LEFT JOIN fields f ON f.id=tl.field_id LEFT JOIN equipment t ON t.id=tl.trailer_id
                LEFT JOIN brigadiers b ON b.id=tl.brigadier_id
                LEFT JOIN waybills wb ON wb.load_id=tl.id AND wb.status<>'BEKOR'
                LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                WHERE h.season_year=? AND h.voided_at IS NULL AND tl.status<>'BEKOR' AND h.work_date BETWEEN ? AND ?
                GROUP BY tl.id ORDER BY day, tl.id''', (year, date_from, date_to))
    out = [dict(r) for r in rows]
    keys = ('hand_kg', 'people', 'hand_pay', 'combine_kg', 'combine_pay', 'unpriced_kg')
    total = {k: sum(r[k] or 0 for r in out) for k in keys}
    total['people'] = scalar('''SELECT COUNT(DISTINCT h.worker_id) FROM harvests h JOIN trailer_loads tl ON tl.id=h.load_id
                                WHERE h.season_year=? AND h.voided_at IS NULL AND tl.status<>'BEKOR' AND h.method='hand'
                                AND h.work_date BETWEEN ? AND ?''', (year, date_from, date_to))
    cancelled = scalar('''SELECT COUNT(*) FROM trailer_loads WHERE season_year=? AND status='BEKOR' AND load_date BETWEEN ? AND ?''',
                       (year, date_from, date_to))
    return {'rows': out, 'total': total, 'cancelled': cancelled}


def todo(year, today=None):
    """"Bugun sizni kutayotgan ishlar" — each line links straight to the work."""
    today = today or today_str()
    items = []
    n_ready = scalar("SELECT COUNT(*) FROM payouts WHERE status='TAYYOR' AND season_year=?", (year,))
    if n_ready:
        s = scalar("SELECT SUM(amount) FROM payouts WHERE status='TAYYOR' AND season_year=?", (year,))
        items.append(('red', f'{n_ready} ta to‘lov kassirda berilishini kutmoqda · {fmt_som(s)}', 'acct.payouts', {'status': 'TAYYOR'}))
    owed = [w for w in worker_balances(year) if w['payable'] > 0]
    if owed:
        items.append(('red', f'{len(owed)} ishchining qoldig‘i bor · {fmt_som(sum(w["payable"] for w in owed))}',
                      'acct.workers', {'tab': 'qoldiq'}))
    uncalc = scalar("SELECT COUNT(*) FROM harvests WHERE season_year=? AND method='hand' AND amount IS NULL AND voided_at IS NULL",
                    (year,))
    if uncalc:
        items.append(('red', f'{uncalc} ta tortish narxsiz (terim narxi kiritilmagan) — pul hisoblanmagan', 'admin.settings', {}))
    n_exp = scalar("SELECT COUNT(*) FROM expenses WHERE status='TEKSHIRILMAGAN' AND voided_at IS NULL AND season_year=?", (year,))
    if n_exp:
        items.append(('yellow', f'{n_exp} ta xarajat tekshirilmagan', 'acct.expenses', {'status': 'TEKSHIRILMAGAN'}))
    warn = get_float('punkt_alert_pct', 3) or 3
    rec = q('''SELECT wb.net_kg, nr.accepted_kg FROM waybills wb JOIN nayman_receipts nr ON nr.waybill_id=wb.id
               WHERE substr(nr.created_at,1,10)=? AND wb.status='QABUL' ''', (today,))
    big = sum(1 for r in rec if r['net_kg'] and abs(r['accepted_kg'] - r['net_kg']) / r['net_kg'] * 100 > warn)
    if big:
        items.append(('red', f'{big} ta punkt yukida katta kg farqi', 'acct.cotton', {'period': 'bugun'}))
    if rec and len(rec) - big:
        items.append(('green', f'{len(rec) - big} ta yuk muammosiz qabul qilindi', 'acct.cotton', {'period': 'bugun'}))
    y = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    for b in q('SELECT id, name FROM cashboxes WHERE active=1'):
        had = scalar('SELECT COUNT(*) FROM cash_entries WHERE cashbox_id=? AND entry_date<=? AND voided_at IS NULL', (b['id'], y))
        closed = scalar('SELECT MAX(day) FROM cash_days WHERE cashbox_id=?', (b['id'],))
        if had and (not closed or closed < y):
            items.append(('yellow', f'{b["name"]}: kecha ({y[8:10]}.{y[5:7]}) kun yopilmagan', 'acct.day_close', {'day': y}))
    return items


# ------------------------------------------------------------------ Google Sheets mirror (source of truth stays here)

def _mirror(db, sheet, row_id, header, row):
    """Upsert-by-id job: a retried or repeated job updates the same sheet row, never adds a duplicate."""
    enqueue(db, 'sheets', 'upsert', f'{sheet}:{row_id}:{time.time_ns()}',
            {'sheet': sheet, 'id': row_id, 'header': header, 'row': [row_id] + list(row)})


CASH_HEADER = ['ID', 'Sana', 'Kassa', 'Yo‘nalish', 'Turi', 'Summa', 'Kimga/kimdan', 'Izoh', 'Kiritdi', 'Holat']


def mirror_cash(db, cash_id):
    c = db.execute('''SELECT c.*, cb.name box, u.full_name who FROM cash_entries c LEFT JOIN cashboxes cb ON cb.id=c.cashbox_id
                      LEFT JOIN users u ON u.id=c.created_by WHERE c.id=?''', (cash_id,)).fetchone()
    if not c:
        return
    from .services import CASH_CATEGORIES
    label = CASH_CATEGORIES.get(c['category'], (None, c['category']))[1]
    _mirror(db, 'KASSA KIRIM-CHIQIM', c['doc_no'], CASH_HEADER,
            [c['entry_date'], c['box'], 'KIRIM' if c['direction'] == 'IN' else 'CHIQIM', label,
             c['amount'] if c['direction'] == 'IN' else -c['amount'], c['counterparty'] or c['source'] or '', c['note'] or '',
             c['who'] or '', 'BEKOR: ' + (c['void_reason'] or '') if c['voided_at'] else 'OK'])
    if c['category'] == 'advance':
        _mirror(db, 'AVANSLAR', c['doc_no'], ['ID', 'Sana', 'Ishchi', 'Summa', 'Holat'],
                [c['entry_date'], c['counterparty'] or '', c['amount'], 'BEKOR' if c['voided_at'] else 'BERILDI'])


def mirror_payout(db, payout_id):
    p = db.execute('''SELECT p.*, w.full_name worker, e.code combine, u.full_name prep, pu.full_name payer FROM payouts p
                      LEFT JOIN workers w ON w.id=p.worker_id LEFT JOIN equipment e ON e.id=p.combine_id
                      LEFT JOIN users u ON u.id=p.prepared_by LEFT JOIN users pu ON pu.id=p.paid_by WHERE p.id=?''',
                   (payout_id,)).fetchone()
    _mirror(db, 'TO‘LOVLAR', p['doc_no'], ['ID', 'Turi', 'Kimga', 'Summa', 'Holat', 'Tayyorladi', 'Tayyorlangan', 'Berdi', 'Berilgan'],
            ['Avans' if p['purpose'] == 'advance' else ('Kombayn' if p['kind'] == 'combine' else 'Ish haqi'),
             p['worker'] or p['combine'], p['amount'], p['status'], p['prep'] or '', p['prepared_at'], p['payer'] or '',
             p['paid_at'] or ''])


def mirror_expense(db, expense_id):
    e = db.execute('''SELECT e.*, f.name field, st.name station, eq.code equip FROM expenses e LEFT JOIN fields f ON f.id=e.field_id
                      LEFT JOIN stations st ON st.id=e.station_id LEFT JOIN equipment eq ON eq.id=e.equipment_id
                      WHERE e.id=?''', (expense_id,)).fetchone()
    _mirror(db, 'XARAJATLAR', e['doc_no'], ['ID', 'Sana', 'Turi', 'Summa', 'Dala/Punkt/Texnika', 'Izoh', 'Holat'],
            [e['expense_date'], e['category'], e['amount'], e['field'] or e['station'] or e['equip'] or '', e['note'] or '',
             'BEKOR' if e['voided_at'] else e['status']])


def mirror_day(db, day_id):
    d = db.execute('SELECT cd.*, cb.name box FROM cash_days cd JOIN cashboxes cb ON cb.id=cd.cashbox_id WHERE cd.id=?',
                   (day_id,)).fetchone()
    _mirror(db, 'KUNLIK-YOPILISH', f'DAY-{d["day"]}-{d["cashbox_id"]}',
            ['ID', 'Sana', 'Kassa', 'Boshlang‘ich', 'Kirim', 'Chiqim', 'Tizim', 'Real', 'Farq', 'Sabab'],
            [d['day'], d['box'], d['opening'], d['inflow'], d['outflow'], d['system_balance'], d['counted'], d['diff'],
             d['reason'] or ''])


def mirror_debt(db, debt_id):
    d = db.execute('''SELECT d.*, COALESCE((SELECT SUM(amount) FROM cash_entries c WHERE c.debt_id=d.id AND c.voided_at IS NULL),0) s
                      FROM debts d WHERE d.id=?''', (debt_id,)).fetchone()
    _mirror(db, 'DEBITOR-KREDITOR', d['doc_no'], ['ID', 'Turi', 'Kim', 'Summa', 'To‘langan', 'Qolgan', 'Sabab', 'Sana', 'Muddat'],
            ['Biz olamiz' if d['direction'] == 'OLISH' else 'Biz beramiz', d['counterparty'], d['amount'], d['s'],
             d['amount'] - d['s'], d['reason'] or '', d['debt_date'], d['due_date'] or ''])


def mirror_worker(db, worker_id, year):
    r = next(iter(worker_balances(year, worker_id=worker_id)), None)
    if r:
        from .reporting import WORKERS_HEADER
        _mirror(db, 'TERIMCHILAR', f'W-{worker_id}', WORKERS_HEADER,
                [r['full_name'], r['brigadier_name'] or '', r['kg'], r['earned'], r['advances'], r['paid'], r['balance'],
                 r['status'], r['uncalc_kg']])
