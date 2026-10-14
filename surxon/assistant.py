"""YORDAMCHI (AI) — a colleague inside the system: the director, the punkt operator (Yunus) and the accountant talk to it
(voice or text) on the phone; it answers from the live database, says what is left to do today, and teaches the system
step by step (its book: docs/YORDAMCHI_BILIM.md + the detailed docs).

The brain is Claude (the key on Admin → Integratsiyalar, as for the talon reading); everything else stays here: the
knowledge, the read-only tools, who may see what (the user's own permissions), the conversation memory (our database,
never Claude's) and the monthly limit.

It never writes: no kg, no money, no status. To change anything it explains the steps and gives a button to the screen
where the person does it with their own name. Workers' (terimchilar) names are never sent — only brigades, trips and kg.
"""
import json
from datetime import date, timedelta
from pathlib import Path

from flask import current_app, url_for

from .db import get_db, q, scalar, tx
from .security import has_perm
from .settings import get_bool, get_setting
from .utils import UserError, now_str, today_str

ROLES = ('admin', 'manager', 'accountant', 'station')
ROLE_NAMES = {'admin': 'admin (tizim egasi)', 'manager': 'direktor / rahbar', 'accountant': 'buxgalter',
              'station': 'punkt operatori'}
MEMORY = 12                     # last messages kept in the conversation
MAX_STEPS = 6                   # tool rounds per answer
DOCS = {'punkt': 'PUNKT.md', 'tezpul': 'TEZPUL.md', 'buxgalteriya': 'BUXGALTERIYA.md', 'kuzatuv': 'KUZATUV.md',
        'vakolatlar': 'VAKOLATLAR.md'}

# the screens it may offer as a button: key → (endpoint, title, permission)
PAGES = {
    'sverka': ('rahbar.sverka', 'Kunlik sverka', 'reports.finance'),
    'direktor': ('rahbar.home', 'Direktor paneli', 'reports.finance'),
    'yolda': ('rahbar.moving', 'Yo‘ldagi telashkalar', 'reports.finance'),
    'tekshirish': ('rahbar.checks', 'Tekshirish (qizillar)', 'reports.finance'),
    'nazorat': ('acct.nazorat', 'Nazorat', 'acct.view'),
    'buxgalteriya': ('acct.home', 'Buxgalteriya', 'acct.view'),
    'faktura': ('acct.faktura', 'Faktura (PQ-17)', 'nayman.write'),
    'xarajatlar': ('acct.expenses', 'Xarajatlar', 'acct.view'),
    'kun_yopish': ('acct.day_close', 'Kunni yopish', 'acct.view'),
    'tezpul_panel': ('tezpul.panel', 'TEZ-PUL panel', 'tezpul.panel'),
    'tezpul_kassa': ('tezpul.kassa', 'TEZ-PUL kassa', 'payouts.pay'),
    'shofyorlar': ('tezpul.drivers', 'Shofyorlar', 'tezpul.panel'),
    'kassir': ('acct.cashier', 'Pul beriladiganlar', 'payouts.pay'),
    'telashkalar': ('ops.loads', 'Telashkalar', 'dashboard'),
    'punkt': ('punkt.home', 'Punkt (umumiy yuk)', 'station.view'),
    'punkt_tarix': ('punkt.history', 'Bugungi tarix (punkt)', 'station.view'),
    'blankalar': ('admin.blanks', 'Punkt blanklari (PB chop etish)', 'masterdata.write'),
}


# ------------------------------------------------------------------ switches

def enabled():
    from . import talon_ai
    return get_bool('ai_yordamchi_on') and talon_ai.enabled()


def allowed(user):
    return bool(user) and user['role'] in ROLES and enabled()


def model():
    from . import talon_ai
    m = (get_setting('ai_yordamchi_model') or '').strip()
    return m if m in talon_ai.MODELS else talon_ai.model()


def month_usage():
    r = q("""SELECT COUNT(*) n, COALESCE(SUM(tokens_in),0) tin, COALESCE(SUM(tokens_out),0) tout FROM yordamchi_messages
             WHERE role='user' AND substr(created_at,1,7)=?""", (today_str()[:7],), one=True)
    t = q("""SELECT COALESCE(SUM(tokens_in),0) tin, COALESCE(SUM(tokens_out),0) tout FROM yordamchi_messages
             WHERE role='assistant' AND substr(created_at,1,7)=?""", (today_str()[:7],), one=True)
    try:
        limit = int(float(get_setting('ai_yordamchi_limit') or 0))
    except ValueError:
        limit = 0
    return {'questions': r['n'], 'tokens_in': t['tin'], 'tokens_out': t['tout'], 'limit': limit}


# ------------------------------------------------------------------ tools (read-only, the asker's own permissions)

def _n(v):
    return f'{(v or 0):,.0f}'.replace(',', ' ')


def _day(arg):
    s = (arg or '').strip().lower()
    t = date.fromisoformat(today_str())
    if s in ('', 'bugun', 'today'):
        return t.isoformat()
    if s in ('kecha', 'yesterday'):
        return (t - timedelta(days=1)).isoformat()
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return t.isoformat()


def _station(user):
    return user['station_id'] if user['role'] == 'station' else None


def t_bugungi_holat(user, sana=''):
    """The day's cotton: picked, sent, received, on the road, open; for the punkt operator — only his punkt."""
    day = _day(sana)
    st = _station(user)
    sw = ' AND tl.station_id=?' if st else ''
    args = (day, st) if st else (day,)
    trips = q(f'''SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN COALESCE(tl.method,'hand')='hand' THEN COALESCE(wb.net_kg, tl.internal_kg) END),0) hand,
                         COALESCE(SUM(CASE WHEN tl.method='combine' THEN COALESCE(wb.net_kg, tl.internal_kg) END),0) comb
                  FROM trailer_loads tl LEFT JOIN waybills wb ON wb.load_id=tl.id AND wb.status<>'BEKOR'
                  WHERE tl.status<>'BEKOR' AND tl.load_date=?{sw}''', args, one=True)
    rec = q(f'''SELECT COUNT(*) n, COALESCE(SUM(nr.accepted_kg),0) acc, COALESCE(SUM(wb.net_kg),0) sent
                FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id JOIN trailer_loads tl ON tl.id=wb.load_id
                WHERE wb.status<>'BEKOR' AND nr.received_date=?{sw}''', args, one=True)
    road = q(f'''SELECT COUNT(*) n, COALESCE(SUM(wb.net_kg),0) kg FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                 WHERE wb.status='YARATILDI'{sw}''', (st,) if st else (), one=True)
    out = {'sana': day, 'reyslar': trips['n'], 'qol_terimi_kg': trips['hand'], 'kombayn_kg': trips['comb'],
           'punkt_qabul': {'reys': rec['n'], 'netto_kg': rec['acc'], 'nakladnoy_kg': rec['sent'],
                           'farq_kg': round(rec['acc'] - rec['sent'], 1) if rec['n'] else None},
           'hozir_punktga_kelmagan_yoki_navbatda': {'reys': road['n'], 'kg': road['kg']},
           'dalada_ochiq_telashka': scalar("SELECT COUNT(*) FROM trailer_loads WHERE status='OCHIQ'")}
    if st:
        from . import groups as G
        out['ochiq_umumiy_yuklar'] = [{'UY': g['number'], 'holat': g['status'], 'telashkalar': g['n'], 'kg': g['kg']}
                                      for g in G.active(st)]
    return out


def t_sverka(user, sana=''):
    from . import sverka as S
    c = S.check(_day(sana))
    rows = [{'brigada': r['name'], 'tur': 'kombayn' if r['method'] == 'combine' else 'qo‘l', 'telashka': r['trips'],
             'dala_kg': r['dala'], 'punkt_kg': r['punkt'], 'pul_talon_kg': r['talon_kg'], 'talon_summa': r['amount'],
             'berilgan': r['paid'], 'punkt_kutilmoqda_kg': r['waiting_kg'], 'holat': r['level'], 'izoh': r['why'],
             'mavsum': r.get('season')} for r in c['rows']]
    return {'sana': c['day'], 'umumiy_holat': c['level'], 'jami': c['totals'], 'brigadalar': rows,
            'qizil_chegara_foiz': c['alert_pct']}


def t_nazorat(user):
    from .nazorat import issues
    found = issues()
    if user['role'] == 'station':
        found = [i for i in found if 'punkt' in (i['title'] + i['detail']).lower() or 'UY' in i['title']]
    return {'muammolar': [{'daraja': 'qizil' if i['level'] == 'red' else 'sariq', 'nima': i['title'], 'batafsil': i['detail']}
                          for i in found[:25]], 'jami': len(found)}


def t_kassa(user, sana=''):
    from . import director as D
    day = _day(sana)
    c = D.cash(day, day)
    return {'sana': day, 'kassalar': [{'kassa': b['name'], 'hozir_qoldiq': b['now'], 'kirim': b['inflow'], 'chiqim': b['outflow']}
                                      for b in c['boxes'] if b['active']],
            'jami_hozir': c['total']['now'], 'chiqim_turlari': c['out_groups'], 'xarajatlar': c['expenses']}


def t_telashkalar(user, holat='yolda'):
    """Trips now: yolda (sent, not received), ochiq (open in the field), bugun (today's)."""
    st = _station(user)
    where = {'ochiq': "tl.status='OCHIQ'", 'bugun': f"tl.status<>'BEKOR' AND tl.load_date='{today_str()}'"}.get(
        holat, "wb.status='YARATILDI'")
    if st:
        where += f' AND tl.station_id={int(st)}'
    rows = q(f'''SELECT tl.trip_no, t.code telashka, f.code dala, b.name brigada, COALESCE(tl.method,'hand') tur,
                        COALESCE(wb.net_kg, tl.internal_kg) kg, tl.status, wb.created_at yuborilgan, wb.arrived_at keldi,
                        tl.opened_at ochilgan
                 FROM trailer_loads tl JOIN equipment t ON t.id=tl.trailer_id LEFT JOIN fields f ON f.id=tl.field_id
                 LEFT JOIN brigadiers b ON b.id=tl.brigadier_id
                 LEFT JOIN waybills wb ON wb.load_id=tl.id AND wb.status<>'BEKOR'
                 WHERE {where} ORDER BY tl.id DESC LIMIT 40''')
    return {'holat': holat, 'soni': len(rows), 'reyslar': [dict(r) for r in rows]}


def t_vazifalar(user):
    """What this person should do today — from the system's own state."""
    role, todo = user['role'], []
    if role in ('admin', 'manager'):
        from . import sverka as S
        c = S.check(today_str())
        for r in c['rows']:
            if r['level'] in ('red', 'yellow'):
                todo.append(f'Sverka: {r["name"]} — {r["why"]}')
        from .nazorat import issues
        reds = [i for i in issues() if i['level'] == 'red']
        todo += [f'Nazorat: {i["title"]}' for i in reds[:8]]
    if role in ('admin', 'accountant'):
        from .yordamchi import faktura_queue
        fq = faktura_queue()
        sign, inv = sum(c['sign'] for c in fq), sum(c['invoice'] for c in fq)
        if sign:
            todo.append(f'{sign} ta PQ-17 ni hosil-qabuli.uz da imzolash')
        if inv:
            todo.append(f'{inv} ta PQ-17 ga faktura yaratish')
        from .yordamchi import pq17_missing
        miss = pq17_missing()
        if miss:
            todo.append(f'{len(miss)} ta yukning PQ-17 si yuklanmagan ({_n(sum(m[2] or 0 for m in miss))} kg) — hosil-qabuli.uz dan yuklash '
                        '(sahifa: faktura)')
        un = scalar("SELECT COUNT(*) FROM expenses WHERE status='TEKSHIRILMAGAN' AND voided_at IS NULL")
        if un:
            todo.append(f'{un} ta xarajatni tasdiqlash (kassir yozgan) — tekshirilmagan_xarajatlar, keyin taklif')
        closed = scalar('SELECT COUNT(*) FROM cash_days WHERE day=?', (today_str(),))
        if not closed:
            todo.append('Kun oxirida kassani yopish (real pulni sanab kiritish) — kassa_kun, keyin taklif')
        from .accounting import season_of, worker_balances
        owed = [r for r in worker_balances(season_of(get_db())) if r['payable'] > 0]
        if owed:
            todo.append(f'{len(owed)} kishiga {_n(sum(r["payable"] for r in owed))} so‘m to‘lanmagan — oldin naqd berilganlari bo‘lsa, '
                        'yopish kerak (sahifa: moliya)')
        unpaid = q("SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM tezpul_talons WHERE status='TAYYOR'", one=True)
        if unpaid['n']:
            todo.append(f'{unpaid["n"]} ta talon to‘lanmagan ({_n(unpaid["s"])} so‘m) — kassada pul yetarlimi')
    if role == 'station':
        from . import groups as G
        for g in G.active(_station(user)):
            todo.append(f'{g["number"]}: {"ochiq — telashkalar qo‘shilmoqda" if g["status"] == "OCHIQ" else "yopilgan — punkt tortishi (brutto/tara) kutilmoqda"}'
                        f' ({g["n"]} telashka, {_n(g["kg"])} kg)')
        road = scalar("""SELECT COUNT(*) FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id
                         WHERE wb.status='YARATILDI' AND tl.station_id=?""", (_station(user),))
        if road:
            todo.append(f'{road} ta telashka hali qabul qilinmagan')
    return {'vazifalar': todo or ['Hozircha shoshilinch ish yo‘q 👍']}


def t_mavsum(user):
    from .accounting import cotton_totals
    from .services import current_season
    year = current_season(get_db())
    c = cotton_totals(year)
    pq = q('SELECT COUNT(*) n, COALESCE(SUM(kond_kg),0) kond, COALESCE(SUM(amount),0) amount FROM pq17_docs', one=True)
    got = scalar('SELECT COALESCE(SUM(amount),0) FROM payments WHERE season_year=? AND voided_at IS NULL', (year,))
    out = {'mavsum': year, 'dala_kg': c['field_kg'], 'punkt_kg': c['punkt_kg'], 'pq17_soni': pq['n'],
           'pq17_tolanadigan_kg': pq['kond'], 'pq17_summa': pq['amount'], 'klaster_tolagan': got,
           'klaster_qarzi': pq['amount'] - got}
    t = q("SELECT COALESCE(SUM(kg),0) kg, COALESCE(SUM(amount),0) s FROM tezpul_talons WHERE status='TOLANDI'", one=True)
    out['talon_berilgan'] = {'kg': t['kg'], 'som': t['s']}
    return out


def t_yoriqnoma(user, mavzu=''):
    """The detailed instructions on a topic (the short book is already in the conversation)."""
    key = (mavzu or '').strip().lower()
    name = next((v for k, v in DOCS.items() if k in key or key in k), None) if key else None
    if not name:
        return {'mavzular': list(DOCS), 'izoh': 'Mavzuni tanlang.'}
    path = Path(current_app.root_path).parent / 'docs' / name
    try:
        return {'mavzu': name, 'matn': path.read_text(encoding='utf-8')[:9000]}
    except OSError:
        return {'xato': 'yo‘riqnoma topilmadi'}


def t_sahifa(user, nom=''):
    p = PAGES.get((nom or '').strip())
    if not p or not has_perm(user, p[2]):
        return {'xato': 'Bu sahifa yo‘q yoki sizga yopiq.', 'mavjud': [k for k, v in PAGES.items() if has_perm(user, v[2])]}
    return {'tugma': {'title': p[1], 'url': url_for(p[0])}, 'izoh': 'Tugma suhbatda ko‘rinadi.'}


def t_xarajatlar(user):
    rows = q("""SELECT e.id, e.expense_date, e.category, e.amount, e.payer, e.note, u.full_name yozgan,
                      (SELECT COUNT(*) FROM photos p WHERE p.entity_type='expense' AND p.entity_id=e.id AND p.voided_at IS NULL) rasm
               FROM expenses e LEFT JOIN users u ON u.id=e.created_by
               WHERE e.status='TEKSHIRILMAGAN' AND e.voided_at IS NULL ORDER BY e.id LIMIT 30""")
    return {'tekshirilmagan': [dict(r) for r in rows], 'izoh': 'Har biri: chek rasmi va summa to‘g‘rimi — keyin taklif(xarajat_tasdiq).'}


def t_qarzdorlar(user):
    """Who is still owed (ids only — the names stay here; the card shows them to the person)."""
    from .accounting import season_of, worker_balances
    rows = sorted([r for r in worker_balances(season_of(get_db())) if r['payable'] > 0], key=lambda r: -r['payable'])
    return {'soni': len(rows), 'jami': sum(r['payable'] for r in rows),
            'qarzdorlar': [{'worker_id': r['id'], 'brigada': r['brigadier_name'], 'kg': r['kg'], 'hisoblangan': r['earned'],
                            'tolangan': r['paid'] + r['advances'] + r['closed'], 'qarz': r['payable']} for r in rows[:30]],
            'izoh': 'Ismlar sizga berilmaydi — odamga “№ worker_id” yoki kartadagi ism bilan ko‘rinadi.'}


def t_kassa_kun(user):
    from .accounting import day_figures
    db, day = get_db(), today_str()
    out = []
    for b in q('SELECT id, name FROM cashboxes WHERE active=1 ORDER BY id'):
        closed = q('SELECT counted, diff FROM cash_days WHERE cashbox_id=? AND day=?', (b['id'], day), one=True)
        out.append({'cashbox_id': b['id'], 'kassa': b['name'], **day_figures(db, b['id'], day),
                    'yopilgan': bool(closed), 'sanalgan': closed['counted'] if closed else None})
    return {'sana': day, 'kassalar': out, 'izoh': 'system = tizim bo‘yicha bo‘lishi kerak bo‘lgan pul; odam real pulni sanaydi.'}


ACTIONS = {
    'xarajat_tasdiq': ('expenses.approve', 'Xarajatni tasdiqlash'),
    'qarz_yopish': ('payouts.prepare', 'Qarzdorlikni yopish (pul oldin berilgan)'),
    'kun_yopish': ('dayclose', 'Kassani (kunni) yopish'),
}


def t_taklif(user, tur='', expense_id=None, worker_id=None, amount=None, cashbox_id=None, sabab=''):
    """Prepare one change as a card; the person checks it and taps the button (nothing changes until then)."""
    spec = ACTIONS.get(tur)
    if not spec or not has_perm(user, spec[0]):
        return {'xato': 'Bunday taklif yo‘q yoki bu odamga yopiq.'}
    params = {}
    if tur == 'xarajat_tasdiq':
        e = q("SELECT id FROM expenses WHERE id=? AND status='TEKSHIRILMAGAN' AND voided_at IS NULL", (_int(expense_id),), one=True)
        if not e:
            return {'xato': 'Bu xarajat topilmadi yoki tasdiqlangan.'}
        params = {'expense_id': e['id']}
    elif tur == 'qarz_yopish':
        from .accounting import season_of, worker_balances
        r = next(iter(worker_balances(season_of(get_db()), worker_id=_int(worker_id))), None) if _int(worker_id) else None
        if not r or r['payable'] <= 0:
            return {'xato': 'Bu odamda yopiladigan qarz yo‘q.'}
        amt = min(_int(amount) or r['payable'], r['payable'])
        params = {'worker_id': r['id'], 'amount': amt}
    elif tur == 'kun_yopish':
        b = q('SELECT id FROM cashboxes WHERE id=? AND active=1', (_int(cashbox_id),), one=True)
        if not b:
            return {'xato': 'Kassa topilmadi.'}
        if q('SELECT 1 FROM cash_days WHERE cashbox_id=? AND day=?', (b['id'], today_str()), one=True):
            return {'xato': 'Bugun bu kassa allaqachon yopilgan.'}
        params = {'cashbox_id': b['id'], 'day': today_str()}
    with tx() as db:
        aid = db.execute('INSERT INTO yordamchi_actions(user_id, kind, params_json, why, created_at) VALUES (?,?,?,?,?)',
                         (user['id'], tur, json.dumps(params), (sabab or '')[:300], now_str())).lastrowid
    return {'taklif_id': aid, 'izoh': 'Karta odamga ko‘rsatildi: u tekshirib, tugmani o‘zi bosadi. Natijani keyingi gapda bilasiz.'}


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def action_card(a):
    """What the person sees on the card (names come from here, never from the AI)."""
    p = json.loads(a['params_json'])
    card = {'id': a['id'], 'kind': a['kind'], 'title': ACTIONS.get(a['kind'], ('', a['kind']))[1], 'why': a['why'] or '',
            'status': a['status'], 'result': a['result'] or '', 'lines': [], 'fields': []}
    if a['kind'] == 'xarajat_tasdiq':
        e = q("""SELECT e.*, u.full_name yozgan FROM expenses e LEFT JOIN users u ON u.id=e.created_by WHERE e.id=?""",
              (p['expense_id'],), one=True)
        if e:
            card['lines'] = [f'{e["expense_date"][8:10]}.{e["expense_date"][5:7]} · {e["category"]} · {_n(e["amount"])} so‘m',
                             f'Yozgan: {e["yozgan"] or "—"}' + (f' · {e["payer"]}' if e['payer'] else '') + (f' · {e["note"]}' if e['note'] else '')]
            card['link'] = {'title': 'Chek rasmini ko‘rish', 'url': url_for('acct.expenses')}
    elif a['kind'] == 'qarz_yopish':
        w = q('SELECT full_name FROM workers WHERE id=?', (p['worker_id'],), one=True)
        card['lines'] = [f'{w["full_name"] if w else "№" + str(p["worker_id"])} — {_n(p["amount"])} so‘m',
                         'Kassadan pul chiqmaydi: faqat “oldin naqd berilgan” deb yopiladi.']
        card['fields'] = [{'name': 'note', 'label': 'Kim, qachon bergan? (majburiy)', 'type': 'text', 'value': ''}]
        card['link'] = {'title': 'Kartochkasi', 'url': url_for('acct.worker', worker_id=p['worker_id'])}
    elif a['kind'] == 'kun_yopish':
        from .accounting import DAY_DIFF_REASONS, day_figures
        b = q('SELECT name FROM cashboxes WHERE id=?', (p['cashbox_id'],), one=True)
        f = day_figures(get_db(), p['cashbox_id'], p['day'])
        card['lines'] = [f'{b["name"] if b else "Kassa"} · {p["day"][8:10]}.{p["day"][5:7]}',
                         f'Tizim bo‘yicha: {_n(f["system"])} so‘m (boshida {_n(f["opening"])} + kirim {_n(f["inflow"])} − chiqim {_n(f["outflow"])})']
        card['fields'] = [{'name': 'counted', 'label': 'Kassadagi real pul (sanab yozing)', 'type': 'number', 'value': ''},
                          {'name': 'reason', 'label': 'Farq bo‘lsa sababi', 'type': 'select', 'options': [''] + DAY_DIFF_REASONS, 'value': ''}]
    return card


def act(user, actor, action_id, form):
    """The person tapped “Bajarish”: the prepared change runs in their name, through the ordinary service."""
    a = q('SELECT * FROM yordamchi_actions WHERE id=? AND user_id=?', (action_id, user['id']), one=True)
    if not a:
        raise UserError('Taklif topilmadi.')
    if a['status'] == 'BAJARILDI':
        return {'text': a['result'], 'already': True}
    if form.get('bekor'):
        with tx() as db:
            db.execute("UPDATE yordamchi_actions SET status='BEKOR', done_at=? WHERE id=?", (now_str(), action_id))
        return {'text': 'Bekor qilindi.'}
    p = json.loads(a['params_json'])
    if a['kind'] == 'xarajat_tasdiq':
        from .accounting import approve_expense
        approve_expense(actor, p['expense_id'])
        text = 'Xarajat tasdiqlandi.'
    elif a['kind'] == 'qarz_yopish':
        from .moliya import close_debts
        note = (form.get('note') or '').strip()
        r = close_debts(actor, [(p['worker_id'], p['amount'])], method='oldin_naqd',
                        note=f'{note} (yordamchi taklifi)' if note else '', client_uuid=f'ai-{action_id}')
        text = f'Qarzdorlik yopildi: {_n(r["total"])} so‘m (kassadan pul chiqmadi).'
    elif a['kind'] == 'kun_yopish':
        from .accounting import close_day
        try:
            counted = int(str(form.get('counted') or '').replace(' ', ''))
        except ValueError:
            raise UserError('Kassadagi real pulni raqam bilan yozing.')
        r = close_day(actor, cashbox_id=p['cashbox_id'], day=p['day'], counted=counted, reason=form.get('reason') or '',
                      note='yordamchi bilan')
        text = 'Kassa yopildi.' + (f' Farq {_n(r["diff"])} so‘m.' if r['diff'] else ' Farq yo‘q 👍')
    else:
        raise UserError('Noma’lum taklif.')
    with tx() as db:
        db.execute("UPDATE yordamchi_actions SET status='BAJARILDI', result=?, done_at=? WHERE id=?", (text, now_str(), action_id))
    return {'text': text}


_SANA = {'type': 'object', 'properties': {'sana': {'type': 'string', 'description': '“bugun”, “kecha” yoki YYYY-MM-DD'}}}
TOOLS = {
    't_vazifalar': (None, {'name': 'vazifalarim', 'description': 'Shu odam bugun nima qilishi kerak — tizim holatidan (sverka, '
                           'Nazorat, PQ-17, xarajatlar, kassa, punkt UY). Suhbat boshida va “nima qilay?” deganda chaqiring.',
                           'input_schema': {'type': 'object', 'properties': {}}}),
    't_bugungi_holat': (None, {'name': 'kun_holati', 'description': 'Bir kunlik paxta: terilgan (qo‘l/kombayn), punkt qabul, '
                               'farq, yo‘lda, ochiq telashkalar. Punkt operatoriga faqat o‘z punkti.', 'input_schema': _SANA}),
    't_telashkalar': (None, {'name': 'telashkalar', 'description': 'Reyslar ro‘yxati: yolda (punkt qabul qilmagan), ochiq '
                             '(dalada), bugun.', 'input_schema': {'type': 'object', 'properties': {
                                 'holat': {'type': 'string', 'enum': ['yolda', 'ochiq', 'bugun']}}}}),
    't_sverka': ('reports.finance', {'name': 'sverka', 'description': 'Kunlik sverka: har brigada dala nakladnoy kg · punkt netto · '
                                     'talonga yozilgan (pul) kg, holat va izoh, mavsum farqi.', 'input_schema': _SANA}),
    't_nazorat': ('acct.view', {'name': 'nazorat', 'description': 'Nazorat: tizim o‘zi topgan ochiq muammolar (qizil/sariq).',
                                'input_schema': {'type': 'object', 'properties': {}}}),
    't_kassa': ('cash.view', {'name': 'kassa', 'description': 'Kassalar: hozirgi qoldiq, kunlik kirim/chiqim, xarajat turlari.',
                              'input_schema': _SANA}),
    't_mavsum': ('reports.finance', {'name': 'mavsum', 'description': 'Mavsum jami: dala va punkt kg, PQ-17, klaster qarzi, '
                                     'talon bo‘yicha berilgan pul.', 'input_schema': {'type': 'object', 'properties': {}}}),
    't_yoriqnoma': (None, {'name': 'yoriqnoma', 'description': 'Batafsil yo‘riqnoma: ' + ', '.join(DOCS) + '. Tizimni '
                           'o‘rgatishda, aniq qadamlar kerak bo‘lsa.', 'input_schema': {'type': 'object', 'properties': {
                               'mavzu': {'type': 'string', 'enum': list(DOCS)}}, 'required': ['mavzu']}}),
    't_xarajatlar': ('expenses.approve', {'name': 'tekshirilmagan_xarajatlar', 'description': 'Kassir yozgan, hali tasdiqlanmagan '
                     'xarajatlar (id, sana, tur, summa, kim yozgan, chek rasmi bormi).', 'input_schema': {'type': 'object', 'properties': {}}}),
    't_qarzdorlar': ('payouts.prepare', {'name': 'qarzdorlar', 'description': 'Terimchilar ichida qarzi borlar (worker_id, brigada, kg, '
                     'hisoblangan, to‘langan, qarz). Ismlar berilmaydi.', 'input_schema': {'type': 'object', 'properties': {}}}),
    't_kassa_kun': ('dayclose', {'name': 'kassa_kun', 'description': 'Bugungi kassa: har kassa uchun boshidagi qoldiq, kirim, chiqim, '
                    'tizim bo‘yicha qoldiq, yopilganmi.', 'input_schema': {'type': 'object', 'properties': {}}}),
    't_taklif': (None, {'name': 'taklif', 'description': 'Bitta o‘zgarishni TAYYORLASH (karta): odam tekshiradi va “Bajarish”ni o‘zi '
                 'bosadi. tur: xarajat_tasdiq (expense_id) · qarz_yopish (worker_id, amount — FAQAT odam “oldin naqd berilgan” desa) · '
                 'kun_yopish (cashbox_id — kechqurun). sabab: odamga qisqa tushuntirish.',
                 'input_schema': {'type': 'object', 'properties': {
                     'tur': {'type': 'string', 'enum': list(ACTIONS)}, 'expense_id': {'type': 'integer'},
                     'worker_id': {'type': 'integer'}, 'amount': {'type': 'integer'}, 'cashbox_id': {'type': 'integer'},
                     'sabab': {'type': 'string'}}, 'required': ['tur']}}),
    't_sahifa': (None, {'name': 'sahifa_tugmasi', 'description': 'Odamga kerakli ekranni ochadigan tugma berish. nom: '
                        + ', '.join(PAGES), 'input_schema': {'type': 'object', 'properties': {
                            'nom': {'type': 'string', 'enum': list(PAGES)}}, 'required': ['nom']}}),
}
FUNCS = {spec['name']: globals()[fn] for fn, (_, spec) in TOOLS.items()}


def tools_for(user):
    return [spec for _, (perm, spec) in TOOLS.items() if perm is None or has_perm(user, perm)]


def run_tool(user, name, args):
    spec = next((s for s in tools_for(user) if s['name'] == name), None)
    if not spec:
        return {'xato': 'Bu ma’lumot sizga yopiq.'}
    try:
        return FUNCS[name](user, **{k: v for k, v in (args or {}).items() if isinstance(v, (str, int, float))})
    except UserError as err:
        return {'xato': str(err)}
    except Exception as exc:                 # a broken tool never breaks the conversation
        print(f'yordamchi tool {name} failed: {exc}', flush=True)
        return {'xato': 'Ma’lumotni olib bo‘lmadi.'}


# ------------------------------------------------------------------ the conversation

PERSONA = """Sen — SURXAN-PAXTA.UZ tizimining ichidagi yordamchisan: SURXON (Taxiatosh Textile) paxta korxonasi xodimlarining \
mehribon, aqlli hamkasbisan. Odamlar bilan telefonda, ko‘pincha ovoz orqali gaplashasan.

Qanday gaplashasan:
- Faqat o‘zbek tilida (lotin yozuvi), oddiy, iliq, samimiy — xuddi yaxshi hamkasb kabi. Odamni ismi bilan “siz” deb chaqir.
- Qisqa gapir: 1–4 jumla, kerak bo‘lsa qisqa ro‘yxat. Javobing ovozda o‘qiladi — jadval, uzun ro‘yxat, markdown belgilarisiz.
- Raqamlarni aniq ayt (kg, so‘m), bo‘sh joy bilan: 12 450 kg.
- Gap noaniq bo‘lsa (qaysi kun? qaysi brigada?) — bitta qisqa savol ber.
- Ovozdan yozilgan gapda xato bo‘lishi mumkin (o‘zbekcha so‘z ruscha/qozoqcha yozilib qolishi) — ma’nosini tushunishga harakat qil.
- Odam charchagan yoki asabiy bo‘lsa — xotirjam, hurmat bilan.

Nima qilasan:
- Savolga faqat tizimdagi ma’lumot bilan javob ber: avval asbobni (tool) chaqir, keyin javob ber. Raqam to‘qima. Ma’lumot yo‘q bo‘lsa —
  “tizimda bu yo‘q” de va kim kiritishi kerakligini ayt.
- Ustoz bo‘l: “qanday qilinadi?” deyilsa — qadamma-qadam, 2–5 qadam, kerakli ekran tugmasini ber (sahifa_tugmasi).
- Bugungi ishlarni eslat (vazifalarim). Muammo bo‘lsa — kim nima qilishi kerakligini ayt.
- Ma’lumot yetishmasa, odamdan so‘ra (masalan: “PB-0123 rasmini olib, Qo‘shishni bosing — keyin men tekshiraman”).

Ustoz bo‘lib ishlash (ayniqsa buxgalter va punkt operatori bilan):
- Odam “bugungi ishlar”, “boshlaymiz” desa yoki suhbat endi boshlansa — vazifalarim ni chaqir va ismi bilan boshla:
  “Asadbek, bugun 3 ta ish bor. Keling, birga qilamiz — birinchisi …”. Eng muhimidan (pul, kassa) boshla.
- Bir vaqtda BITTA ish: nima va nega (1 jumla) → qadamlar (2–4 ta) → ekran tugmasi (sahifa_tugmasi) yoki tayyor karta (taklif).
- Tuzatishni o‘zing tayyorla (taklif): xarajatni tasdiqlash, kunni yopish, “oldin naqd berilgan” qarzni yopish. Karta odamga
  chiqadi, u tekshirib “Bajarish”ni O‘ZI bosadi — shunda uning ismi yoziladi. Qarzni yopish taklifini faqat odam o‘zi
  “oldin naqd berilgan” desa ber. Kunni yopishda real pulni odam o‘zi sanab yozadi.
- Odam “bajarildi”, “✅ …” desa — vazifalarim ni qayta chaqirib tekshir, maqta (“Zo‘r, Asadbek!”) va keyingisiga o‘t.
- Hammasi tugasa — “Bugungi ishlar tugadi 👍” de va kechqurun nima qilish kerakligini eslat.

Qat’iy qoidalar:
- Sen hech narsani o‘zgartirmaysan: kg, pul, holat, o‘chirish — yo‘q. O‘zgartirish kerak bo‘lsa — qadamlar va ekran tugmasi;
  tugmani odam o‘zi bosadi (shunda uning ismi yoziladi). “Tizimni chetlab o‘tish”, “yozuvni yashirish” kabi so‘rovlarga yo‘q de.
- “O‘g‘irlik”, “kamomad” dema — “farq” de. Hech kimni ayblama: farqning sababini odam aniqlaydi.
- Parol, kalit, token so‘rama va chatga yozdirma — kalitlar faqat Admin → Integratsiyalar dagi yashirin maydonga.
- Terimchilarning ismlari senga berilmaydi — brigada, telashka, kg bilan gapir.
- Faqat shu odamning huquqidagi ma’lumot: asbob “yopiq” desa — bu sahifa unga tegishli emasligini muloyim ayt.
- Tizimdan tashqari mavzularda (ob-havo, siyosat va h.k.) qisqa javob berib, ishga qaytar.

Tizim haqidagi bilim (o‘quv kitobi):
"""


def _book():
    path = Path(current_app.root_path).parent / 'docs' / 'YORDAMCHI_BILIM.md'
    try:
        return path.read_text(encoding='utf-8')
    except OSError:
        return ''


def _system(user):
    name = (user['full_name'] or user['username'] or '').split(' ')[0]
    station = ''
    if user['role'] == 'station' and user['station_id']:
        station = scalar('SELECT name FROM stations WHERE id=?', (user['station_id'],)) or ''
    who = (f'Hozir gaplashayotgan odam: {name} — {ROLE_NAMES.get(user["role"], user["role"])}'
           + (f' ({station} punkti)' if station else '') + f'. Bugun: {today_str()}, soat {now_str()[11:16]}.')
    return [{'type': 'text', 'text': PERSONA + _book(), 'cache_control': {'type': 'ephemeral'}},
            {'type': 'text', 'text': who}]


def history(user_id, limit=MEMORY):
    """The conversation since the last “Yangi suhbat”, oldest first."""
    rows = q('''SELECT * FROM yordamchi_messages WHERE user_id=? AND id > COALESCE(
                    (SELECT MAX(id) FROM yordamchi_messages WHERE user_id=? AND role='reset'), 0)
                ORDER BY id DESC LIMIT ?''', (user_id, user_id, limit))
    out = []
    for r in reversed(rows):
        if r['role'] == 'reset':
            continue
        links, cards = expand(json.loads(r['links_json'] or '[]'), user_id)
        out.append(dict(r, links=links, cards=cards))
    return out


def expand(raw, user_id):
    """The stored links of an answer → (screen buttons, action cards with their current state)."""
    links = [x for x in raw if 'url' in x]
    ids = [x['action'] for x in raw if 'action' in x]
    cards = [action_card(a) for a in q(f'SELECT * FROM yordamchi_actions WHERE user_id=? AND id IN ({",".join("?" * len(ids))})',
                                       (user_id, *ids))] if ids else []
    return links, cards


def reset(user_id):
    with tx() as db:
        db.execute("INSERT INTO yordamchi_messages(user_id, role, text, created_at) VALUES (?, 'reset', '', ?)",
                   (user_id, now_str()))


def _client():
    import anthropic
    from . import talon_ai
    return anthropic.Anthropic(api_key=talon_ai.api_key(), timeout=60, max_retries=2)


def _call(client, **kw):
    import anthropic
    try:
        return client.messages.create(**kw)
    except anthropic.AuthenticationError:
        raise UserError('AI kaliti noto‘g‘ri — admin Integratsiyalar sahifasida tekshirsin.')
    except anthropic.RateLimitError:
        raise UserError('Hozir band ekanman — bir daqiqadan keyin yana so‘rang.')
    except anthropic.APIConnectionError:
        raise UserError('Internetga ulanib bo‘lmadi — birozdan keyin qayta urinib ko‘ring.')
    except anthropic.APIStatusError as err:
        raise UserError(f'AI xatosi ({err.status_code}) — birozdan keyin qayta urinib ko‘ring.')


def ask(user, text):
    """One turn: the question → (tools …) → the answer. Returns {text, links}. Both sides are kept in our database."""
    text = (text or '').strip()[:1500]
    if not text:
        raise UserError('Savolni yozing yoki ayting.')
    if not allowed(user):
        raise UserError('Yordamchi siz uchun yoqilmagan.')
    u = month_usage()
    if u['limit'] and u['questions'] >= u['limit']:
        raise UserError(f'Bu oyning chegarasi tugadi ({u["limit"]} ta savol). Admin Sozlamalarda oshirishi mumkin.')
    past = history(user['id'])
    messages = [{'role': m['role'], 'content': m['text']} for m in past if m['text']]
    while messages and messages[0]['role'] != 'user':
        messages.pop(0)
    messages.append({'role': 'user', 'content': text})
    with tx() as db:
        db.execute("INSERT INTO yordamchi_messages(user_id, role, text, created_at) VALUES (?, 'user', ?, ?)",
                   (user['id'], text, now_str()))
    client, tools, system = _client(), tools_for(user), _system(user)
    links, tin, tout, resp = [], 0, 0, None
    for _ in range(MAX_STEPS):
        resp = _call(client, model=model(), max_tokens=4000, system=system, tools=tools, messages=messages,
                     output_config={'effort': 'low'})
        tin += (resp.usage.input_tokens or 0) + (getattr(resp.usage, 'cache_read_input_tokens', 0) or 0)
        tout += resp.usage.output_tokens or 0
        if resp.stop_reason == 'pause_turn':
            messages.append({'role': 'assistant', 'content': resp.content})
            continue
        uses = [b for b in resp.content if b.type == 'tool_use']
        if resp.stop_reason != 'tool_use' or not uses:
            break
        messages.append({'role': 'assistant', 'content': resp.content})
        results = []
        for b in uses:
            out = run_tool(user, b.name, b.input)
            if isinstance(out, dict) and out.get('tugma') and out['tugma'] not in links:
                links.append(out['tugma'])
            if isinstance(out, dict) and out.get('taklif_id'):
                links.append({'action': out['taklif_id']})
            results.append({'type': 'tool_result', 'tool_use_id': b.id,
                            'content': json.dumps(out, ensure_ascii=False, default=str)[:12000]})
        messages.append({'role': 'user', 'content': results})
    answer = '\n'.join(b.text for b in (resp.content if resp else []) if b.type == 'text').strip()
    if resp is not None and resp.stop_reason == 'refusal':
        answer = 'Bu savolga javob bera olmayman. Ishga oid boshqa savol bering.'
    import re
    answer = re.sub(r'(?<=\d) (?=\d{3}\b)', '\u00a0', answer)      # 12 450 kg never breaks across lines
    if not answer:
        answer = 'Kechirasiz, javobni tayyorlay olmadim — savolni boshqacha aytib ko‘ring.'
    with tx() as db:
        db.execute('''INSERT INTO yordamchi_messages(user_id, role, text, links_json, tokens_in, tokens_out, created_at)
                      VALUES (?, 'assistant', ?, ?, ?, ?, ?)''',
                   (user['id'], answer, json.dumps(links, ensure_ascii=False), tin, tout, now_str()))
    buttons, cards = expand(links, user['id'])
    return {'text': answer, 'links': buttons, 'cards': cards}


def suggestions(user):
    """The chips under the greeting — by role."""
    common = ['Tizimni menga o‘rgating']
    return {'manager': ['Bugun nima bo‘ldi?', 'Sverkada farq bormi?', 'Qaysi telashka punktga kelmadi?', 'Kassada qancha pul bor?'],
            'admin': ['Bugun nima bo‘ldi?', 'Sverkada farq bormi?', 'Klaster qancha qarz?'],
            'accountant': ['🚀 Bugungi ishlarni birga qilamiz', 'Tekshirilmagan xarajatlar', 'Kassani yopishga yordam bering',
                           'Faktura kerak PQ-17 bormi?'],
            'station': ['🚀 Bugungi ishlarni birga qilamiz', 'Qaysi telashkalar kelishi kerak?', 'Nakladnoyni qanday qo‘shaman?', 'UY ni qanday yopaman?'],
            }.get(user['role'], []) + common


def todo_count(user):
    """The small red number on the assistant's round button: today's open tasks (accountant and punkt only — cheap)."""
    if user['role'] not in ('accountant', 'station') or not allowed(user):
        return 0
    try:
        t = t_vazifalar(user)['vazifalar']
        return 0 if t == ['Hozircha shoshilinch ish yo‘q 👍'] else len(t)
    except Exception:
        return 0
