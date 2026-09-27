"""Credits, leasing and other contracts: who we owe, how much is left, what falls due when.

Each counterparty (bank, leasing company, cluster, supplier) holds its contracts, their files, the payment schedule
(principal + interest per due date) and the money that really moved (paid out / loan received), entered by hand or from
a bank statement. Paid money is laid over the schedule oldest-first, so every due line is paid / partly paid / overdue,
and the office sees the next payments and the whole debt in one place — next to the cotton money that pays for it.
"""
import hashlib
import io
import re
from datetime import date, datetime
from pathlib import Path

from flask import current_app

from .db import q, tx
from .security import audit
from .utils import UserError, clean_text, now_str, today_str

KINDS = {'bank': 'Bank', 'lizing': 'Lizing kompaniyasi', 'sugurta': 'Sug‘urta kompaniyasi', 'klaster': 'Klaster', 'yetkazuvchi': 'Yetkazib beruvchi',
         'boshqa': 'Boshqa'}
CONTRACT_KINDS = {'kredit': 'Kredit', 'lizing': 'Lizing', 'sugurta': 'Sug‘urta', 'xarid': 'Xarid shartnomasi', 'boshqa': 'Boshqa'}


def _need(actor):
    if not actor or not actor.can('loans.write'):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')


def _money(v, label, allow_empty=False):
    s = str(v or '').replace(' ', '').replace('\xa0', '').replace(',', '.').strip()
    if not s:
        if allow_empty:
            return None
        raise UserError(f'{label} kiriting.')
    try:
        n = float(s)
    except ValueError:
        raise UserError(f'{label}: raqam yozing.')
    if n < 0:
        raise UserError(f'{label} manfiy bo‘lmaydi.')
    return n


def _date(v, label, allow_empty=False):
    s = (v or '').strip()
    if not s:
        if allow_empty:
            return None
        raise UserError(f'{label} kiriting.')
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise UserError(f'{label}: sana noto‘g‘ri (masalan 30.09.2026).')


# ------------------------------------------------------------------ parties / contracts

def save_party(actor, pid, *, name, inn='', kind='boshqa', note=''):
    _need(actor)
    name = clean_text(name, 120)
    if len(name) < 2:
        raise UserError('Firma nomini yozing.')
    inn = re.sub(r'\D', '', inn or '')[:14] or None
    if kind not in KINDS:
        kind = 'boshqa'
    with tx() as db:
        if inn:
            other = db.execute('SELECT id, name FROM parties WHERE inn=? AND id<>?', (inn, pid or 0)).fetchone()
            if other:
                raise UserError(f'STIR {inn} — “{other["name"]}” da allaqachon bor.')
        if pid:
            db.execute('UPDATE parties SET name=?, inn=?, kind=?, note=? WHERE id=?', (name, inn, kind, clean_text(note, 300), pid))
            audit(db, actor, 'UPDATE', 'party', pid, new={'name': name, 'inn': inn, 'kind': kind})
            return pid
        cur = db.execute('INSERT INTO parties(name, inn, kind, note, created_by, created_at) VALUES (?,?,?,?,?,?)',
                         (name, inn, kind, clean_text(note, 300), actor.user_id, now_str()))
        audit(db, actor, 'CREATE', 'party', cur.lastrowid, new={'name': name, 'inn': inn, 'kind': kind})
        return cur.lastrowid


def save_contract(actor, cid, *, party_id, kind, title, number='', sign_date='', start_date='', amount='', advance='',
                  rate_pct='', months='', equipment_id=None, note='', status='FAOL', end_date='', terms=''):
    _need(actor)
    if kind not in CONTRACT_KINDS:
        raise UserError('Shartnoma turini tanlang.')
    title = clean_text(title, 160)
    if len(title) < 2:
        raise UserError('Shartnoma nomini yozing (masalan: TZST CE-220 kombayn lizingi).')
    vals = (party_id, kind, clean_text(number, 60) or None, title, _date(sign_date, 'Imzolangan sana', True),
            _date(start_date, 'Boshlanish sanasi', True), _money(amount, 'Summa', True), _money(advance, 'Avans', True),
            _money(rate_pct, 'Foiz', True), int(_money(months, 'Muddat', True) or 0) or None, equipment_id or None,
            clean_text(note, 500), status if status in ('FAOL', 'YOPILGAN', 'BEKOR') else 'FAOL',
            _date(end_date, 'Tugash sanasi', True), (terms or '').strip()[:4000] or None)
    with tx() as db:
        if not db.execute('SELECT 1 FROM parties WHERE id=?', (party_id,)).fetchone():
            raise UserError('Firma topilmadi.')
        if cid:
            db.execute('''UPDATE contracts SET party_id=?, kind=?, number=?, title=?, sign_date=?, start_date=?, amount=?, advance=?,
                          rate_pct=?, months=?, equipment_id=?, note=?, status=?, end_date=?, terms=? WHERE id=?''', vals + (cid,))
            audit(db, actor, 'UPDATE', 'contract', cid, new={'title': title, 'amount': vals[6]})
            return cid
        cur = db.execute('''INSERT INTO contracts(party_id, kind, number, title, sign_date, start_date, amount, advance, rate_pct,
                            months, equipment_id, note, status, end_date, terms, created_by, created_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                         vals + (actor.user_id, now_str()))
        audit(db, actor, 'CREATE', 'contract', cur.lastrowid, new={'title': title, 'amount': vals[6]})
        return cur.lastrowid


def add_file(actor, party_id, name, data, contract_id=None, kind='boshqa'):
    _need(actor)
    if not data:
        raise UserError('Fayl tanlang.')
    if len(data) > 25 * 1024 * 1024:
        raise UserError('Fayl juda katta (25 MB dan ko‘p).')
    ext = (Path(name or '').suffix.lower() or '.bin')[:6]
    if ext not in ('.pdf', '.jpg', '.jpeg', '.png', '.webp', '.xlsx', '.xls', '.doc', '.docx', '.zip', '.csv', '.txt'):
        raise UserError('Bu turdagi faylni saqlab bo‘lmaydi (PDF, rasm, Excel, Word, ZIP).')
    sha = hashlib.sha256(data).hexdigest()
    rel = f'shartnomalar/{party_id}/{sha[:16]}{ext}'
    path = Path(current_app.config['SURXON'].UPLOAD_DIR) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    with tx() as db:
        dup = db.execute('SELECT id FROM contract_files WHERE party_id=? AND sha256=?', (party_id, sha)).fetchone()
        if dup:
            return dup['id']
        cur = db.execute('''INSERT INTO contract_files(party_id, contract_id, kind, name, path, sha256, uploaded_by, uploaded_at)
                            VALUES (?,?,?,?,?,?,?,?)''', (party_id, contract_id, kind, clean_text(name, 160) or 'fayl', rel, sha,
                                                          actor.user_id, now_str()))
        audit(db, actor, 'CREATE', 'contract_file', cur.lastrowid, new={'name': name, 'contract_id': contract_id})
        return cur.lastrowid


# ------------------------------------------------------------------ schedule

def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace('\xa0', '').replace(' ', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


def _cell_date(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v or '').strip()
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse_schedule_xlsx(data):
    """A leasing / loan schedule from Excel. Understands the Agrosanoat lizing layout (numbered quarter rows: № ·
    residual · due date · days · principal · interest · total; amounts “минг сўм”) and plain tables with a date column
    and principal / interest / total columns. Returns {'rows': [...], 'info': {...}} — amounts in so‘m."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception:
        raise UserError('Excel faylni o‘qib bo‘lmadi.')
    ws = wb.active
    grid = [list(r) for r in ws.iter_rows(values_only=True)]
    text = ' '.join(str(c) for r in grid for c in r if c is not None).lower()
    mult = 1000 if ('минг' in text or 'ming so' in text) else 1
    info = {}
    for r in grid:
        cells = [c for c in r if c is not None]
        if len(cells) >= 2 and isinstance(cells[0], str):
            k, v = cells[0].lower(), cells[1]
            if 'нархи' in k or 'narxi' in k:
                info['amount'] = (_num(v) or 0) * mult
            elif 'аванс' in k and 'миқдори' in k or 'avans' in k and 'miqdori' in k:
                info['advance'] = (_num(v) or 0) * mult
            elif 'ставка' in k or 'foizi' in k or 'маржа' in k:
                info['rate_pct'] = _num(v)
            elif 'муддати' in k or 'muddati' in k:
                info['months'] = int(_num(v) or 0) or None
            elif 'берилган сана' in k or 'berilgan sana' in k:
                info['start_date'] = _cell_date(v)
            elif 'русумли' in k or 'rusumli' in k:
                info['title'] = re.sub(r'[“”"]', '', cells[0]).strip()
    if 'title' not in info:
        for r in grid[:4]:
            for c in r:
                if isinstance(c, str) and ('русумли' in c or 'rusumli' in c or 'машина' in c.lower()):
                    info['title'] = re.sub(r'[“”"]', '', c).strip()
    rows = []
    for r in grid:
        cells = list(r)
        # numbered payment rows: first non-empty cell is a small integer and one cell holds a date
        firsts = [c for c in cells if c is not None]
        if len(firsts) < 5:
            continue
        no = str(firsts[0]).strip()
        if not (no.isdigit() and len(no) <= 3):
            continue
        d_idx = next((i for i, c in enumerate(firsts) if _cell_date(c)), None)
        if d_idx is None:
            continue
        nums = [_num(c) for c in firsts[d_idx + 1:]]
        nums = [n for n in nums if n is not None]
        if len(nums) >= 4:          # days · principal · interest · total
            principal, interest, total = nums[1], nums[2], nums[3]
        elif len(nums) == 3:        # principal · interest · total
            principal, interest, total = nums
        elif len(nums) == 1:
            principal, interest, total = nums[0], 0, nums[0]
        else:
            continue
        rows.append({'due_date': _cell_date(firsts[d_idx]), 'principal': round(principal * mult, 2),
                     'interest': round(interest * mult, 2), 'amount': round(total * mult, 2)})
    if not rows:
        raise UserError('Grafikdagi to‘lov qatorlarini topib bo‘lmadi (№, sana, summa ustunlari kerak).')
    return {'rows': rows, 'info': info}


def set_schedule(actor, contract_id, rows, replace=True):
    _need(actor)
    with tx() as db:
        if not db.execute('SELECT 1 FROM contracts WHERE id=?', (contract_id,)).fetchone():
            raise UserError('Shartnoma topilmadi.')
        if replace:
            db.execute('DELETE FROM contract_schedule WHERE contract_id=?', (contract_id,))
        for r in rows:
            db.execute('INSERT INTO contract_schedule(contract_id, due_date, principal, interest, amount, note) VALUES (?,?,?,?,?,?)',
                       (contract_id, r['due_date'], r.get('principal') or 0, r.get('interest') or 0, r['amount'], r.get('note')))
        audit(db, actor, 'UPDATE', 'contract_schedule', contract_id,
              new={'rows': len(rows), 'total': round(sum(r['amount'] for r in rows))})
    return len(rows)


def annuity_schedule(amount, rate_pct, months, start, every=1):
    """A plain schedule when the paper has none: equal principal every `every` months, interest on the remainder."""
    from calendar import monthrange
    amount, months = float(amount), int(months)
    n = max(1, months // every)
    part = amount / n
    y, m = start.year, start.month
    rest, out = amount, []
    for _ in range(n):
        m += every
        while m > 12:
            m, y = m - 12, y + 1
        due = date(y, m, monthrange(y, m)[1])
        interest = rest * (rate_pct or 0) / 100 * every / 12
        out.append({'due_date': due.isoformat(), 'principal': round(part, 2), 'interest': round(interest, 2),
                    'amount': round(part + interest, 2)})
        rest -= part
    return out


# ------------------------------------------------------------------ money that moved

def add_move(actor, *, party_id, contract_id=None, move_date, direction, amount, purpose='', source='qo‘lda', bank_ref=None):
    _need(actor)
    if direction not in ('IN', 'OUT'):
        raise UserError('Yo‘nalish noto‘g‘ri.')
    amt = _money(amount, 'Summa')
    if amt <= 0:
        raise UserError('Summa 0 dan katta bo‘lsin.')
    d = _date(move_date, 'Sana')
    with tx() as db:
        if bank_ref and db.execute('SELECT 1 FROM contract_moves WHERE bank_ref=?', (bank_ref,)).fetchone():
            return None
        cur = db.execute('''INSERT INTO contract_moves(contract_id, party_id, move_date, direction, amount, purpose, source, bank_ref,
                            created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                         (contract_id or None, party_id, d, direction, amt, clean_text(purpose, 300), source, bank_ref,
                          actor.user_id, now_str()))
        audit(db, actor, 'CREATE', 'contract_move', cur.lastrowid, new={'amount': amt, 'direction': direction, 'contract_id': contract_id})
        return cur.lastrowid


def void_move(actor, move_id):
    _need(actor)
    with tx() as db:
        db.execute('UPDATE contract_moves SET voided_at=?, voided_by=? WHERE id=? AND voided_at IS NULL',
                   (now_str(), actor.user_id, move_id))
        audit(db, actor, 'VOID', 'contract_move', move_id)


# ------------------------------------------------------------------ important dates (not payments)

def save_obligation(actor, *, due_date, title, party_id=None, contract_id=None, equipment_id=None, note='', remind_days=7):
    _need(actor)
    title = clean_text(title, 160)
    if len(title) < 3:
        raise UserError('Nima qilish kerakligini yozing (masalan: texnik ko‘rikdan o‘tkazish).')
    with tx() as db:
        cur = db.execute('''INSERT INTO obligations(party_id, contract_id, equipment_id, due_date, title, note, remind_days, created_by, created_at)
                            VALUES (?,?,?,?,?,?,?,?,?)''', (party_id, contract_id, equipment_id, _date(due_date, 'Sana'), title,
                                                            clean_text(note, 500), int(remind_days or 7), actor.user_id, now_str()))
        audit(db, actor, 'CREATE', 'obligation', cur.lastrowid, new={'title': title, 'due_date': due_date})
        return cur.lastrowid


def done_obligation(actor, oid, undo=False):
    _need(actor)
    with tx() as db:
        db.execute('UPDATE obligations SET done_at=?, done_by=? WHERE id=?', (None if undo else now_str(), actor.user_id, oid))
        audit(db, actor, 'UPDATE', 'obligation', oid, new={'done': not undo})


def obligations(party_id=None, contract_id=None, open_only=False, days=None, today=None):
    from datetime import timedelta
    today = today or today_str()
    where, params = ['1=1'], []
    if party_id:
        where.append('(o.party_id=? OR c.party_id=?)'); params += [party_id, party_id]
    if contract_id:
        where.append('o.contract_id=?'); params.append(contract_id)
    if open_only:
        where.append('o.done_at IS NULL')
    if days is not None:
        where.append('o.due_date<=?'); params.append((date.fromisoformat(today) + timedelta(days=days)).isoformat())
    rows = q(f'''SELECT o.*, c.title contract_title, COALESCE(o.party_id, c.party_id) pid, p.name party_name, e.code equipment_code
                 FROM obligations o LEFT JOIN contracts c ON c.id=o.contract_id LEFT JOIN parties p ON p.id=COALESCE(o.party_id, c.party_id)
                 LEFT JOIN equipment e ON e.id=o.equipment_id WHERE {' AND '.join(where)} ORDER BY o.done_at IS NOT NULL, o.due_date''', params)
    out = []
    for r in rows:
        left = (date.fromisoformat(r['due_date']) - date.fromisoformat(today)).days
        out.append(dict(r, days_left=left, state='done' if r['done_at'] else 'overdue' if left < 0 else 'soon' if left <= r['remind_days'] else 'later'))
    return out


def remind_due(today=None):
    """Once a day: payments and important dates coming within their reminder window go to the report channel."""
    from .settings import get_setting
    today = today or today_str()
    if (get_setting('loans_reminded_on') or '') == today:
        return 0
    lines = [f'⚠ {o["due_date"][8:10]}.{o["due_date"][5:7]} — {o["title"]}' + (f' ({o["party_name"]})' if o['party_name'] else '')
             for o in obligations(open_only=True, days=30, today=today) if o['state'] in ('overdue', 'soon')]
    lines += [f'💸 {u["date"][8:10]}.{u["date"][5:7]} — {u["party"]}: {u["amount"]:,.0f} so‘m ({u["contract"]})'.replace(',', ' ')
              for u in upcoming(7, today)]
    with tx() as db:
        db.execute('''INSERT INTO settings(key, value, updated_at) VALUES ('loans_reminded_on', ?, ?)
                      ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at''', (today, now_str()))
    if not lines:
        return 0
    try:
        from .outbox import enqueue
        with tx() as db:
            enqueue(db, 'telegram_report', 'alert', f'loans:{today}', {'text': '📅 To‘lov va muhim muddatlar:\n' + '\n'.join(lines[:30])})
    except Exception as exc:
        current_app.logger.warning('loan reminder: %s', exc)
    return len(lines)


# ------------------------------------------------------------------ the picture

def contract_state(c, today=None):
    """Schedule lines with paid / partly / overdue / ahead, laid over the real payments (oldest first)."""
    today = today or today_str()
    sched = q('SELECT * FROM contract_schedule WHERE contract_id=? ORDER BY due_date, id', (c['id'],))
    paid = q("SELECT COALESCE(SUM(amount),0) s FROM contract_moves WHERE contract_id=? AND direction='OUT' AND voided_at IS NULL",
             (c['id'],), one=True)['s']
    got = q("SELECT COALESCE(SUM(amount),0) s FROM contract_moves WHERE contract_id=? AND direction='IN' AND voided_at IS NULL",
            (c['id'],), one=True)['s']
    pool = paid - (c['advance'] or 0) if c['kind'] == 'lizing' and c['advance'] else paid
    pool = max(pool, 0) if c['kind'] == 'lizing' else paid
    lines, overdue, overdue_sum, nxt = [], 0, 0.0, None
    for s in sched:
        cover = min(pool, s['amount'])
        pool -= cover
        left = round(s['amount'] - cover, 2)
        st = 'paid' if left <= 0.5 else ('partly' if cover > 0 else 'due')
        if st != 'paid' and s['due_date'] < today:
            st = 'overdue'
            overdue += 1
            overdue_sum += left
        if st != 'paid' and nxt is None and s['due_date'] >= today:
            nxt = {'date': s['due_date'], 'amount': left}
        lines.append(dict(s, left=left, state=st))
    total = sum(s['amount'] for s in sched)
    advance_paid = min(paid, c['advance'] or 0) if c['kind'] == 'lizing' else 0
    debt = (total + (c['advance'] or 0 if c['kind'] == 'lizing' else 0)) - paid if sched else \
        ((c['amount'] or got) - paid if (c['amount'] or got) else None)
    return {'lines': lines, 'total': total, 'paid': paid, 'received': got, 'debt': round(debt, 2) if debt is not None else None,
            'overdue': overdue, 'overdue_sum': round(overdue_sum, 2), 'next': nxt, 'advance_paid': advance_paid,
            'principal_left': round(sum(l['principal'] for l in lines if l['state'] != 'paid'), 2)}


def contracts(party_id=None, today=None):
    rows = q('''SELECT c.*, p.name party_name, p.kind party_kind, e.code equipment_code FROM contracts c
                JOIN parties p ON p.id=c.party_id LEFT JOIN equipment e ON e.id=c.equipment_id
                WHERE c.status<>'BEKOR' ''' + (' AND c.party_id=?' if party_id else '') + ' ORDER BY c.start_date, c.id',
             (party_id,) if party_id else ())
    return [dict(r, st=contract_state(r, today)) for r in rows]


def parties_overview():
    out = []
    for p in q('SELECT * FROM parties ORDER BY name'):
        cs = contracts(p['id'])
        loose = q("""SELECT COALESCE(SUM(CASE WHEN direction='OUT' THEN amount END),0) paid,
                            COALESCE(SUM(CASE WHEN direction='IN' THEN amount END),0) got
                     FROM contract_moves WHERE party_id=? AND contract_id IS NULL AND voided_at IS NULL""", (p['id'],), one=True)
        nexts = [c['st']['next'] for c in cs if c['st']['next']]
        out.append(dict(p, contracts=cs, n=len(cs), debt=sum(c['st']['debt'] or 0 for c in cs),
                        overdue=sum(c['st']['overdue'] for c in cs), overdue_sum=sum(c['st']['overdue_sum'] for c in cs),
                        next=min(nexts, key=lambda x: x['date']) if nexts else None,
                        files=q('SELECT COUNT(*) n FROM contract_files WHERE party_id=?', (p['id'],), one=True)['n'],
                        photo=q("SELECT id FROM contract_files WHERE party_id=? AND kind='rasm' ORDER BY id LIMIT 1", (p['id'],), one=True),
                        loose_paid=loose['paid'], loose_got=loose['got']))
    return out


def upcoming(days=90, today=None):
    """Every unpaid due line up to `days` ahead (overdue first) — “when and how much to pay”."""
    from datetime import timedelta
    today = today or today_str()
    until = (date.fromisoformat(today) + timedelta(days=days)).isoformat()
    out = []
    for c in contracts(today=today):
        for ln in c['st']['lines']:
            if ln['state'] != 'paid' and ln['due_date'] <= until:
                out.append({'date': ln['due_date'], 'amount': ln['left'], 'state': ln['state'], 'contract': c['title'],
                            'contract_id': c['id'], 'party': c['party_name'], 'party_id': c['party_id']})
    return sorted(out, key=lambda x: x['date'])


def summary(today=None):
    cs = contracts(today=today)
    up = upcoming(30, today)
    obl = obligations(open_only=True, days=30, today=today)
    return {'debt': sum(c['st']['debt'] or 0 for c in cs), 'n': len(cs),
            'duties': [o for o in obl if o['state'] in ('overdue', 'soon')],
            'overdue': sum(c['st']['overdue'] for c in cs), 'overdue_sum': sum(c['st']['overdue_sum'] for c in cs),
            'month': sum(u['amount'] for u in up), 'next': up[0] if up else None}


def akt(party_id, dan=None, gacha=None, today=None):
    """Akt-sverka with one firm for a period: what fell due by the contracts (schedule lines, the leasing advance) against
    what we really paid. Saldo > 0 — we owe; < 0 — we paid ahead. Loan money received is listed apart (the schedule
    already holds its repayment). Lines after the period are summed as “not yet due”."""
    today = today or today_str()
    gacha = gacha or today
    rows, pre = [], 0.0
    for c in contracts(party_id, today=today):
        if c['kind'] == 'lizing' and c['advance']:
            d = c['start_date'] or c['sign_date'] or (c['created_at'] or '')[:10]
            rows.append({'date': d, 'text': f'{c["title"]}: avans', 'due': c['advance'], 'paid': 0, 'got': 0})
        for ln in c['st']['lines']:
            rows.append({'date': ln['due_date'], 'text': f'{c["title"]}: grafik bo‘yicha to‘lov', 'due': ln['amount'], 'paid': 0, 'got': 0})
    no_sched = {c['id'] for c in contracts(party_id, today=today) if c['kind'] == 'kredit' and not c['st']['lines']}
    for m in q('''SELECT m.*, c.title ct FROM contract_moves m LEFT JOIN contracts c ON c.id=m.contract_id
                  WHERE m.party_id=? AND m.voided_at IS NULL''', (party_id,)):
        text = ' · '.join(x for x in (m['ct'], m['purpose']) if x) or ('to‘lov' if m['direction'] == 'OUT' else 'pul tushdi')
        if m['direction'] == 'IN' and m['contract_id'] in no_sched:      # loan used, repayment schedule not loaded yet
            rows.append({'date': m['move_date'], 'text': 'Kreditdan to‘landi: ' + (m['purpose'] or text), 'due': m['amount'],
                         'paid': 0, 'got': 0})
            continue
        rows.append({'date': m['move_date'], 'text': text, 'due': 0, 'paid': m['amount'] if m['direction'] == 'OUT' else 0,
                     'got': m['amount'] if m['direction'] == 'IN' else 0})
    rows.sort(key=lambda r: (r['date'], r['due'] == 0))
    later = sum(r['due'] for r in rows if r['date'] > gacha)
    body = []
    for r in rows:
        if r['date'] > gacha:
            continue
        if dan and r['date'] < dan:
            pre += r['due'] - r['paid']
            continue
        body.append(r)
    saldo = pre
    for r in body:
        saldo += r['due'] - r['paid']
        r['saldo'] = round(saldo, 2)
    return {'rows': body, 'opening': round(pre, 2), 'closing': round(saldo, 2), 'due': sum(r['due'] for r in body),
            'paid': sum(r['paid'] for r in body), 'got': sum(r['got'] for r in body), 'later': round(later, 2),
            'dan': dan, 'gacha': gacha}


def photos(party_id=None, contract_id=None):
    """Machine pictures (files of kind “rasm”) — shown on the contract, the firm and the Buxgalteriya cards."""
    if contract_id:
        return q("SELECT * FROM contract_files WHERE contract_id=? AND kind='rasm' ORDER BY id", (contract_id,))
    return q("SELECT * FROM contract_files WHERE party_id=? AND kind='rasm' ORDER BY id", (party_id,))


# ------------------------------------------------------------------ credit-account spending (Agrobank “To‘lovlar hisoboti”)

CREDIT_EXPORT_HEADERS = ('qabul qiluvchi', 'maqsad nomi', 'summa')


def parse_credit_export(data):
    """The bank's export of payments made straight from the credit account (Agrobank farmer platform): one row per
    payment — date, amount, recipient, purpose. The export repeats every payment several times: identical rows are one
    payment. Amounts come in tiyin (1/100 so‘m) although the column says so‘m. Returns rows sorted by date, in so‘m."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception:
        raise UserError('Excel faylni o‘qib bo‘lmadi.')
    ws = wb.active
    grid = [list(r) for r in ws.iter_rows(values_only=True)]
    head_i = next((i for i, r in enumerate(grid[:10])
                   if all(any(h in str(c or '').lower() for c in r) for h in CREDIT_EXPORT_HEADERS)), None)
    if head_i is None:
        raise UserError('Bu bank to‘lovlar hisoboti emas (“Qabul qiluvchi”, “Maqsad nomi”, “Summa” ustunlari kerak).')
    head = [str(c or '').strip().lower() for c in grid[head_i]]

    def col(*names):
        return next((i for i, h in enumerate(head) if any(n == h or n in h for n in names)), None)
    ci = {'date': col('yaratilgan sana', 'sana'), 'amount': col('summa'), 'payer_acc': col('to‘lovchi hisob', "to'lovchi hisob"),
          'to': col('qabul qiluvchi'), 'inn': col('qabul qiluvchi inn'), 'acc': col('qabul qiluvchi hisob'),
          'cat': col('maqsad nomi'), 'crop': col('ekin turi'), 'note': col('izoh'), 'bank': col('bank nomi')}
    seen, out = set(), []
    for r in grid[head_i + 1:]:
        if not r or ci['amount'] is None or r[ci['amount']] in (None, ''):
            continue
        key = tuple(str(c or '') for c in r[1:])            # the row number differs between the copies
        if key in seen:
            continue
        seen.add(key)
        raw = str(r[ci['date']] or '').strip()
        when = None
        for fmt in ('%d/%m/%Y, %H:%M:%S', '%d.%m.%Y %H:%M:%S', '%d/%m/%Y', '%d.%m.%Y', '%Y-%m-%d %H:%M:%S'):
            try:
                when = datetime.strptime(raw, fmt)
                break
            except ValueError:
                pass
        if isinstance(r[ci['date']], datetime):
            when = r[ci['date']]
        amt = _num(r[ci['amount']])
        if not when or not amt:
            continue
        g = lambda k: str(r[ci[k]] or '').strip() if ci[k] is not None else ''
        mask = lambda t: re.sub(r'\b(\d{4})[ -]?\d{4}[ -]?\d{4}[ -]?(\d{4})\b', r'\1 **** **** \2', t)   # card numbers
        ref = hashlib.sha1('|'.join(key).encode()).hexdigest()[:20]
        out.append({'date': when.date().isoformat(), 'time': when.strftime('%H:%M'), 'amount': round(amt / 100, 2),
                    'to': g('to'), 'inn': re.sub(r'\D', '', g('inn')).strip('0') and re.sub(r'\D', '', g('inn')),
                    'acc': g('acc'), 'category': g('cat') or 'Boshqa', 'crop': g('crop'), 'note': mask(g('note')), 'ref': ref})
    if not out:
        raise UserError('Hisobotda to‘lov qatori topilmadi.')
    return sorted(out, key=lambda x: (x['date'], x['time']))


def _norm_name(s):
    s = (s or '').lower().replace('ʻ', "'").replace('‘', "'").replace('’', "'")
    s = re.sub(r'\b(mchj|aj|atb|xk|ooo|ao|llc|kampaniyasi|kompaniyasi)\b|[«»"“”`\']', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def import_credit_spend(actor, contract_id, rows):
    """Each payment from the credit account = loan money used (IN on the credit contract). A payment to a firm we keep
    (e.g. the leasing company) is also written as our payment (OUT) to that firm. Re-importing never doubles anything."""
    _need(actor)
    c = q('SELECT * FROM contracts WHERE id=?', (contract_id,), one=True)
    if not c:
        raise UserError('Shartnoma topilmadi.')
    parties = [p for p in q('SELECT id, name, inn FROM parties') if p['id'] != c['party_id']]
    new = dup = linked = 0
    with tx() as db:
        for r in rows:
            purpose = ' · '.join(x for x in (r['category'], r['to'], r['note']) if x)[:300]
            if db.execute('SELECT 1 FROM contract_moves WHERE bank_ref=?', ('KR:' + r['ref'],)).fetchone():
                dup += 1
                continue
            db.execute('''INSERT INTO contract_moves(contract_id, party_id, move_date, direction, amount, purpose, source, bank_ref,
                          category, counterparty, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (contract_id, c['party_id'], r['date'], 'IN', r['amount'], purpose, 'kredit hisobi', 'KR:' + r['ref'],
                        r['category'], r['to'], actor.user_id, now_str()))
            new += 1
            to = _norm_name(r['to'])
            hit = [p for p in parties if (r['inn'] and p['inn'] == r['inn']) or (len(_norm_name(p['name'])) >= 4 and
                                                                                 (_norm_name(p['name']) in to or to in _norm_name(p['name'])))]
            if len(hit) == 1:
                cs = db.execute("SELECT id FROM contracts WHERE party_id=? AND status<>'BEKOR'", (hit[0]['id'],)).fetchall()
                db.execute('''INSERT INTO contract_moves(contract_id, party_id, move_date, direction, amount, purpose, source, bank_ref,
                              category, counterparty, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                           (cs[0]['id'] if len(cs) == 1 else None, hit[0]['id'], r['date'], 'OUT', r['amount'],
                            f'{r["note"] or r["category"]} (kredit hisobidan)'[:300], 'kredit hisobi', 'KRO:' + r['ref'],
                            r['category'], r['to'], actor.user_id, now_str()))
                linked += 1
        audit(db, actor, 'CREATE', 'credit_import', contract_id, new={'new': new, 'dup': dup, 'linked': linked})
    return {'new': new, 'dup': dup, 'linked': linked, 'total': sum(r['amount'] for r in rows)}


def credit_use(contract_id):
    """Where the loan went: money used from the credit account by purpose, largest first."""
    rows = q('''SELECT COALESCE(category, 'Boshqa') category, COUNT(*) n, SUM(amount) s, MIN(move_date) first, MAX(move_date) last
                FROM contract_moves WHERE contract_id=? AND direction='IN' AND voided_at IS NULL GROUP BY 1 ORDER BY s DESC''',
             (contract_id,))
    total = sum(r['s'] for r in rows)
    return {'rows': [dict(r, pct=r['s'] / total * 100 if total else 0) for r in rows], 'total': total}
