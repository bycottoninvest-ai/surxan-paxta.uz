"""BUXGALTER / MOLIYA: one place for the money of the whole season — and the two fixes the accountant needs most:

1. QARZDORLIKNI YOPISH — a picker the system still shows as owed was in fact paid (cash handed out before the system,
   by bank, …). Two different things, chosen every time so the kassa is never reduced twice:
     • “Pul HOZIR beriladi” → the ordinary pay (payment order + cash-out from the accountant's kassa, wallet.give_pay);
     • “Pul OLDIN berilgan / bank / boshqa” → a debt_closures row: the debt becomes 0, the kassa does not move.
   One person or many at once (checkboxes). Nothing is deleted: every closure is its own row with who, when, why
   (a mistaken one is cancelled with a reason), it is audited per person, and the director gets a Telegram note of every
   closure made without cash.

2. KG NI TUZATISH — a wrong weighing (120 kg that was 110) is never overwritten: the old row is cancelled (“KG tuzatish”)
   and a new one is written with the new kg at the SAME frozen rate, pointing to the old one (harvests.corrects_id).
   The pay follows by itself: unpaid → the debt changes; already paid → the card shows “N so‘m ortiqcha to‘langan”
   (or “qo‘shimcha to‘lanishi kerak”). A trailer's / waybill's own kg is not touched (the paper already went out).
"""
import json
from datetime import date, timedelta

from .db import doc_number, get_db, q, scalar, tx
from .security import audit
from .utils import UserError, clean_text, now_str, today_str

CLOSE_METHODS = {'oldin_naqd': 'Pul oldin naqd berilgan (kassadan chiqmaydi)', 'hozir_naqd': 'Hozir naqd beriladi (kassadan chiqadi)',
                 'bank': 'Bank orqali berilgan (kassadan chiqmaydi)', 'boshqa': 'Boshqa (kassadan chiqmaydi)'}
FIX_REASONS = ['Tarozida xato', 'Operator xatosi', 'Takroriy yozuv', 'Noto‘g‘ri talon', 'Qo‘lda tuzatish', 'Boshqa']
PERIODS = [('bugun', 'Bugun'), ('kecha', 'Kecha'), ('7kun', '7 kun'), ('oy', 'Shu oy'), ('hammasi', 'Barcha tarix'),
           ('oraliq', 'Sana oralig‘i')]


def _need(actor):
    if not actor or not actor.can('payouts.prepare'):
        raise UserError('Bu amal faqat buxgalter (yoki admin) uchun.')


def _som(v):
    return f'{(v or 0):,.0f}'.replace(',', ' ') + ' so‘m'


# ------------------------------------------------------------------ 1. closing debts

def close_debts(actor, items, *, method, note, cashbox_id=None, client_uuid=None):
    """items: [(worker_id, amount or None = the whole payable)]. Returns {n, total, cash, rows}."""
    from . import accounting as A
    _need(actor)
    if method not in CLOSE_METHODS:
        raise UserError('Pul qanday berilganini tanlang.')
    note = clean_text(note, 300)
    if len(note) < 3:
        raise UserError('Izoh majburiy: qachon, kim, qanday berganini qisqa yozing.')
    items = [(int(w), a) for w, a in items if w]
    if not items:
        raise UserError('Kamida bitta odamni tanlang.')
    if len(items) > 1000:
        raise UserError('Bir martada 1000 kishidan ko‘p emas.')
    year = A.season_of(get_db())
    if method == 'hozir_naqd':                     # real cash now: the ordinary pay, one order per person
        from .wallet import give_pay
        done, total = [], 0
        for wid, amount in items:
            row = next(iter(A.worker_balances(year, worker_id=wid)), None)
            amt = int(amount or (row['payable'] if row else 0))
            if amt <= 0:
                continue
            r = give_pay(actor, wid, amt, cashbox_id=cashbox_id, note=f'Qarzdorlikni yopish: {note}'[:200],
                         client_uuid=f'{client_uuid}:{wid}' if client_uuid else None)
            done.append({'worker_id': wid, 'amount': amt, 'doc_no': r['doc_no']})
            total += amt
        return {'n': len(done), 'total': total, 'cash': True, 'rows': done}
    batch = client_uuid or f'dc-{now_str()}-{actor.user_id}'
    rows, total = [], 0
    with tx() as db:
        if client_uuid and db.execute('SELECT 1 FROM debt_closures WHERE batch=?', (batch,)).fetchone():
            got = db.execute('SELECT worker_id, amount FROM debt_closures WHERE batch=?', (batch,)).fetchall()
            return {'n': len(got), 'total': sum(r['amount'] for r in got), 'cash': False, 'already': True,
                    'rows': [dict(r) for r in got]}
        for wid, amount in items:
            row = next(iter(A.worker_balances(year, worker_id=wid)), None)
            if not row:
                raise UserError(f'Ishchi №{wid} topilmadi.')
            room = row['payable']                   # what is owed and not already waiting at the cashier
            amt = int(amount or room)
            if amt <= 0:
                raise UserError(f'{row["full_name"]}: yopiladigan qarz yo‘q.')
            if amt > room:
                raise UserError(f'{row["full_name"]}: qarz {_som(room)} — {_som(amt)} yopib bo‘lmaydi.')
            cid = db.execute('''INSERT INTO debt_closures(season_year, worker_id, amount, method, note, batch, created_by, created_at)
                                VALUES (?,?,?,?,?,?,?,?)''', (year, wid, amt, method, note, batch, actor.user_id, now_str())).lastrowid
            audit(db, actor, 'DEBT_CLOSE', 'worker', wid,
                  old={'qarz': row['balance'], 'holat': row['status']},
                  new={'qarz': row['balance'] - amt, 'yopildi': amt, 'usul': CLOSE_METHODS[method], 'closure_id': cid,
                       'kassa': 'o‘zgarmadi'}, reason=note)
            A.mirror_worker(db, wid, year)
            rows.append({'worker_id': wid, 'name': row['full_name'], 'amount': amt})
            total += amt
        from .yordamchi import DIRECTOR_ROLES, _send
        lines = [f'• {r["name"]} — {_som(r["amount"])}' for r in rows[:15]]
        if len(rows) > 15:
            lines.append(f'… yana {len(rows) - 15} kishi')
        _send(db, 'moliya', f'debt-close:{batch}',
              '\n'.join(['SURXAN-PAXTA.UZ', f'🧾 QARZDORLIK KASSASIZ YOPILDI: {len(rows)} kishi · {_som(total)}',
                         f'Kim: {actor.name} · usul: {CLOSE_METHODS[method]}', f'Izoh: {note}', '', *lines,
                         '', 'Kassadan pul chiqmadi. Tarix: Buxgalteriya → Moliya.']), DIRECTOR_ROLES)
    return {'n': len(rows), 'total': total, 'cash': False, 'rows': rows}


def void_closure(actor, closure_id, reason):
    _need(actor)
    reason = clean_text(reason, 200)
    if len(reason) < 3:
        raise UserError('Bekor qilish sababini yozing.')
    with tx() as db:
        c = db.execute('SELECT * FROM debt_closures WHERE id=?', (closure_id,)).fetchone()
        if not c or c['voided_at']:
            raise UserError('Yozuv topilmadi yoki allaqachon bekor qilingan.')
        db.execute('UPDATE debt_closures SET voided_at=?, voided_by=?, void_reason=? WHERE id=?',
                   (now_str(), actor.user_id, reason, closure_id))
        audit(db, actor, 'DEBT_CLOSE_VOID', 'worker', c['worker_id'], old={'yopilgan': c['amount'], 'closure_id': closure_id},
              new={'qarz_qaytdi': c['amount']}, reason=reason)
        from .accounting import mirror_worker
        mirror_worker(db, c['worker_id'], c['season_year'])


# ------------------------------------------------------------------ 2. fixing a weighing's kg

def fix_kg(actor, harvest_id, new_kg, *, reason, note):
    """The old weighing is cancelled, a new one with the right kg (same rate) is written; returns the money effect."""
    _need(actor)
    if reason not in FIX_REASONS:
        raise UserError('Sababni tanlang.')
    note = clean_text(note, 300)
    if len(note) < 3:
        raise UserError('Izoh majburiy.')
    try:
        new_kg = round(float(str(new_kg).replace(',', '.').replace(' ', '')), 1)
    except ValueError:
        raise UserError('Yangi kg ni raqam bilan yozing.')
    if new_kg <= 0:
        raise UserError('Yangi kg 0 dan katta bo‘lishi kerak. Yozuv butunlay xato bo‘lsa — rahbar uni bekor qiladi.')
    from .accounting import mirror_worker, worker_balances
    with tx() as db:
        h = db.execute('SELECT * FROM harvests WHERE id=?', (harvest_id,)).fetchone()
        if not h or h['voided_at']:
            raise UserError('Yozuv topilmadi yoki allaqachon bekor qilingan (eng oxirgi tuzatilganini oching).')
        if h['method'] != 'hand' or not h['worker_id']:
            raise UserError('Faqat terimchining (qo‘l terimi) kg si shu yerda tuzatiladi.')
        if abs(new_kg - h['kg']) < 0.05:
            raise UserError('Yangi kg eskisi bilan bir xil.')
        bal = next(iter(worker_balances(h['season_year'], worker_id=h['worker_id'])), None)
        amount = round(new_kg * h['rate']) if h['rate'] else None
        ts = now_str()
        db.execute('UPDATE harvests SET voided_at=?, voided_by=?, void_reason=? WHERE id=?',
                   (ts, actor.user_id, f'KG tuzatish: {h["kg"]:g} → {new_kg:g} kg · {reason}: {note}'[:300], harvest_id))
        cols = [r['name'] for r in db.execute('PRAGMA table_info(harvests)')
                if r['name'] not in ('id', 'kg', 'amount', 'client_uuid', 'void_reason', 'voided_by', 'voided_at', 'created_at',
                                     'entered_by', 'corrects_id', 'fix_reason')]
        new_id = db.execute(f'''INSERT INTO harvests({", ".join(cols)}, kg, amount, created_at, entered_by, corrects_id, fix_reason)
                                SELECT {", ".join(cols)}, ?, ?, ?, ?, ?, ? FROM harvests WHERE id=?''',
                            (new_kg, amount, ts, actor.user_id, harvest_id, f'{reason}: {note}'[:300], harvest_id)).lastrowid
        t = db.execute('SELECT id, status, number FROM tezpul_talons WHERE harvest_id=?', (harvest_id,)).fetchone()
        if t:            # the talon follows its weighing; a paid talon keeps the sum that was handed out
            if t['status'] == 'TOLANDI':
                db.execute('UPDATE tezpul_talons SET harvest_id=?, kg=? WHERE id=?', (new_id, new_kg, t['id']))
            else:
                db.execute('UPDATE tezpul_talons SET harvest_id=?, kg=?, amount=? WHERE id=?', (new_id, new_kg, amount, t['id']))
        load = db.execute('SELECT * FROM trailer_loads WHERE id=?', (h['load_id'],)).fetchone()
        has_paper = db.execute("SELECT 1 FROM waybills WHERE load_id=? AND status<>'BEKOR'", (h['load_id'],)).fetchone()
        if load and load['status'] in ('OCHIQ', 'TOLDI') and not has_paper:
            from .services import _snapshot_load
            _snapshot_load(db, h['load_id'])
        diff_amount = (amount or 0) - (h['amount'] or 0)
        audit(db, actor, 'KG_FIX', 'harvest', new_id,
              old={'harvest_id': harvest_id, 'kg': h['kg'], 'summa': h['amount'], 'worker_id': h['worker_id']},
              new={'harvest_id': new_id, 'kg': new_kg, 'summa': amount, 'farq_kg': round(new_kg - h['kg'], 1),
                   'farq_som': diff_amount, 'narx': h['rate'], 'talon': t['number'] if t else None},
              reason=f'{reason}: {note}')
        audit(db, actor, 'KG_FIX', 'worker', h['worker_id'], old={'kg': h['kg'], 'summa': h['amount']},
              new={'kg': new_kg, 'summa': amount, 'harvest_id': new_id}, reason=f'{reason}: {note}')
        mirror_worker(db, h['worker_id'], h['season_year'])
    after = next(iter(worker_balances(h['season_year'], worker_id=h['worker_id'])), None)
    paid_before = bal and bal['balance'] <= 0
    effect = ('Qarzdorlik yangilandi' if not paid_before else
              (f'{_som(-after["balance"])} ortiqcha to‘langan' if after['balance'] < 0 else
               f'{_som(after["balance"])} qo‘shimcha to‘lanishi kerak' if after['balance'] > 0 else 'Hisob teng'))
    return {'harvest_id': new_id, 'old_kg': h['kg'], 'new_kg': new_kg, 'old_amount': h['amount'], 'new_amount': amount,
            'diff': diff_amount, 'balance': after['balance'] if after else 0, 'effect': effect}


# ------------------------------------------------------------------ the dashboard

def period(p, a=None, b=None):
    t = date.fromisoformat(today_str())
    if p == 'kecha':
        d = (t - timedelta(days=1)).isoformat()
        return d, d, 'Kecha'
    if p == '7kun':
        return (t - timedelta(days=6)).isoformat(), t.isoformat(), '7 kun'
    if p == 'oy':
        return t.replace(day=1).isoformat(), t.isoformat(), 'Shu oy'
    if p == 'hammasi':
        first = scalar('''SELECT MIN(d) FROM (SELECT MIN(entry_date) d FROM cash_entries UNION ALL
                          SELECT MIN(work_date) FROM harvests)''') or t.isoformat()
        return first, t.isoformat(), 'Barcha tarix'
    if p == 'oraliq' and a and b:
        try:
            a, b = sorted([date.fromisoformat(a).isoformat(), date.fromisoformat(b).isoformat()])
            return a, b, f'{a[8:10]}.{a[5:7]} – {b[8:10]}.{b[5:7]}.{b[:4]}'
        except ValueError:
            pass
    return t.isoformat(), t.isoformat(), 'Bugun'


def _cash(a, b):
    return q('''SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount END),0) i,
                       COALESCE(SUM(CASE WHEN direction='OUT' THEN amount END),0) o
                FROM cash_entries WHERE voided_at IS NULL AND entry_date BETWEEN ? AND ?''', (a, b), one=True)


def _exp(a, b, *names):
    marks = ','.join('?' * len(names))
    return scalar(f'''SELECT COALESCE(SUM(amount),0) FROM expenses WHERE voided_at IS NULL AND expense_date BETWEEN ? AND ?
                      AND category IN ({marks})''', (a, b, *names)) or 0


def dashboard(p='bugun', a=None, b=None):
    from .accounting import hand_rate, total_balance, worker_balances
    from .services import current_season
    db = get_db()
    a, b, label = period(p, a, b)
    year = current_season(db)
    c = _cash(a, b)
    earned = q('''SELECT COALESCE(SUM(amount),0) amt, COALESCE(SUM(kg),0) kg, COUNT(DISTINCT worker_id) people
                  FROM harvests WHERE method='hand' AND voided_at IS NULL AND work_date BETWEEN ? AND ?''', (a, b), one=True)
    paid = scalar('''SELECT COALESCE(SUM(CASE WHEN category IN ('worker_pay','advance') THEN amount
                                              WHEN category IN ('refund_worker_pay','refund_advance') THEN -amount END),0)
                     FROM cash_entries WHERE voided_at IS NULL AND worker_id IS NOT NULL AND entry_date BETWEEN ? AND ?''', (a, b))
    closed = scalar('''SELECT COALESCE(SUM(amount),0) FROM debt_closures WHERE voided_at IS NULL
                       AND substr(created_at,1,10) BETWEEN ? AND ?''', (a, b))
    rows = worker_balances(year)
    debt_now = sum(r['balance'] for r in rows if r['balance'] > 0)
    over_now = sum(-r['balance'] for r in rows if r['balance'] < 0)
    driver_paid = scalar('''SELECT COALESCE(SUM(amount),0) FROM cash_entries WHERE voided_at IS NULL AND category='driver_pay'
                            AND entry_date BETWEEN ? AND ?''', (a, b))
    driver_due = scalar('''SELECT COALESCE(SUM(t.driver_amount),0) FROM tezpul_talons t
                           WHERE t.driver_amount IS NOT NULL AND t.status IN ('TAYYOR','TOLANDI')
                           AND substr(t.entered_at,1,10) BETWEEN ? AND ?''', (a, b))
    transport = driver_due + _exp(a, b, 'Transport')
    starshi = _exp(a, b, 'Brigadir / starshi')
    gifts = _exp(a, b, 'Sovg‘a')
    fuel = _exp(a, b, 'Yoqilg‘i')
    parts = _exp(a, b, 'Ehtiyot qism', 'Ta‘mirlash')
    all_exp = scalar('SELECT COALESCE(SUM(amount),0) FROM expenses WHERE voided_at IS NULL AND expense_date BETWEEN ? AND ?', (a, b))
    other = all_exp - _exp(a, b, 'Transport', 'Brigadir / starshi', 'Sovg‘a', 'Yoqilg‘i', 'Ehtiyot qism', 'Ta‘mirlash')
    terim_total = earned['amt'] + transport + starshi + gifts
    t = date.fromisoformat(today_str())
    quick = []
    for key, (qa, qb) in (('Bugun', (t, t)), ('Kecha', (t - timedelta(days=1),) * 2),
                          ('Shu hafta', (t - timedelta(days=t.weekday()), t)), ('Shu oy', (t.replace(day=1), t))):
        x = _cash(qa.isoformat(), qb.isoformat())
        quick.append({'label': key, 'in': x['i'], 'out': x['o']})
    allc = _cash('2000-01-01', t.isoformat())
    quick.append({'label': 'Boshidan', 'in': allc['i'], 'out': allc['o']})
    debtors = sorted([r for r in rows if r['payable'] > 0], key=lambda r: -r['payable'])
    log = q('''SELECT a.*, u.full_name who, u.role FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id
               WHERE a.action IN ('DEBT_CLOSE','DEBT_CLOSE_VOID','KG_FIX') AND a.entity_type='worker'
               ORDER BY a.id DESC LIMIT 30''')
    names = {r['id']: r['full_name'] for r in q('SELECT id, full_name FROM workers WHERE id IN (%s)' % ','.join(
        str(int(x['entity_id'])) for x in log) if log else 'SELECT 1 id, NULL full_name WHERE 0')}
    history = [dict(x, name=names.get(int(x['entity_id'])), old=json.loads(x['old_json'] or '{}'),
                    new=json.loads(x['new_json'] or '{}')) for x in log]
    return {'p': p, 'a': a, 'b': b, 'label': label, 'periods': PERIODS,
            'cash_in': c['i'], 'cash_out': c['o'], 'balance': total_balance(db),
            'earned': earned['amt'], 'kg': earned['kg'], 'people': earned['people'], 'paid': paid, 'closed': closed,
            'debt_now': debt_now, 'over_now': over_now, 'debtors': debtors, 'debt_people': len(debtors),
            'transport': transport, 'driver_paid': driver_paid, 'starshi': starshi, 'gifts': gifts, 'fuel': fuel,
            'parts': parts, 'other': other, 'terim_total': terim_total,
            'rate': hand_rate(), 'real_per_kg': round(terim_total / earned['kg']) if earned['kg'] else None,
            'quick': quick, 'history': history, 'methods': CLOSE_METHODS, 'year': year}


def worker_log(worker_id):
    """Every closure and kg fix of one person, newest first (for the card)."""
    log = q('''SELECT a.*, u.full_name who, u.role FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id
               WHERE a.entity_type='worker' AND a.entity_id=? AND a.action IN ('DEBT_CLOSE','DEBT_CLOSE_VOID','KG_FIX')
               ORDER BY a.id DESC''', (str(worker_id),))
    closures = q('''SELECT d.*, u.full_name who FROM debt_closures d LEFT JOIN users u ON u.id=d.created_by
                    WHERE d.worker_id=? ORDER BY d.id DESC''', (worker_id,))
    return [dict(x, old=json.loads(x['old_json'] or '{}'), new=json.loads(x['new_json'] or '{}')) for x in log], closures
