"""Nazorat: the system checks itself, so nobody pays a combine owner on a mistake.

Every few minutes (from the outbox worker) every check below is run; a problem seen for the first time is written to
the Telegram report group and to the personal chat of every admin / director / accountant who linked Telegram. The
“Nazorat” page lists what is open now with a link to fix it. What the system can fix by itself (tying a PQ-17 to its
trip) it does first.

Red  — money or data that does not add up: fix before paying.
Yellow — something is late (no PQ-17 yet, a trailer long on the road or left open).
"""
from datetime import datetime, timedelta

from .db import get_db, q, tx
from .settings import get_float, get_setting
from .utils import now_str, today_str

EVERY_MIN = 15


def _ago(hours):
    return (datetime.strptime(now_str()[:19], '%Y-%m-%d %H:%M:%S') - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')


def _u(endpoint, **kw):
    """A link for the page; '' where there is no web request (the worker only writes text)."""
    try:
        from flask import url_for
        return url_for(endpoint, **kw)
    except Exception:
        return ''


def _kg(v):
    return f'{v:,.0f}'.replace(',', ' ')


def evidence(waybill_id=None, group_id=None, load_id=None, limit=2):
    """The pictures that show what really happened: the punkt's stamped paper (the umumiy yuk's paper for a group),
    then the field photo of the trailer. Paths under UPLOAD_DIR."""
    rows = []
    if group_id:
        rows += q("""SELECT path FROM photos WHERE voided_at IS NULL AND entity_type='load_group' AND entity_id=?
                     ORDER BY id DESC LIMIT 1""", (group_id,))
    if waybill_id and not load_id:
        w = q('SELECT load_id FROM waybills WHERE id=?', (waybill_id,), one=True)
        load_id = w['load_id'] if w else None
    if waybill_id or load_id:
        rows += q("""SELECT path FROM photos WHERE voided_at IS NULL AND ((waybill_id IS NOT NULL AND waybill_id=?) OR load_id=?)
                     ORDER BY category='nayman' DESC, id DESC LIMIT 3""", (waybill_id or -1, load_id or -1))
    out = []
    for r in rows:
        if r['path'] not in out:
            out.append(r['path'])
    return out[:limit]


def issues(year=None):
    """Everything wrong right now: [{key, level, title, detail, url}] — reds first."""
    from .accounting import combine_balances
    from .services import current_season
    year = year or current_season(get_db())
    out = []

    def add(key, level, title, detail, url, who=None, photos=None, ask=None, steps=None, link=None):
        out.append({'key': key, 'level': level, 'title': title, 'detail': detail, 'url': url,
                    'who': who, 'photos': photos or [], 'ask': ask, 'steps': steps or [], 'link': link})

    fix_receipt = ['Rasmdagi punkt chekiga qarang: brutto, tara va yuk xati raqami.',
                   'Qabul BUGUN bo‘lgan bo‘lsa: havolani oching → “↩ Qabulni bekor qilish” → qaytadan to‘g‘ri kg va '
                   'yuk xati № bilan qabul qiling.',
                   'Eski kun bo‘lsa: chek rasmini buxgalterga Telegram’da yuboring — u tizimda tuzatadi.']

    punkt = [r['id'] for r in q("SELECT id FROM users WHERE active=1 AND role='station'")]   # who receives at the punkt

    # 1. a PQ-17 the system could not tie to a trip — its kg and money belong to nobody yet
    for p in q('''SELECT id, code, load_no, netto, doc_date, method FROM pq17_docs WHERE waybill_id IS NULL AND group_id IS NULL'''):
        what = 'kombayn' if p['method'] == 'combine' else 'qo‘l terimi' if p['method'] == 'hand' else 'paxta'
        add(f'pq:unmatched:{p["id"]}', 'red', f'PQ-17 {p["code"]} hech bir reysga bog‘lanmagan',
            f'yuk xati {p["load_no"] or "—"} · {_kg(p["netto"] or 0)} kg · {p["doc_date"] or ""} — qaysi reys ekanini tanlang',
            _u('acct.pq17'), who=punkt, link='/punkt',
            ask=f'Klaster yuk xati {p["load_no"] or "—"} ni tortgan ({what}, {_kg(p["netto"] or 0)} kg), lekin tizimda bu yuk QABUL QILINMAGAN.',
            steps=['Havolani bosing — punktdagi telashkalar ro‘yxati ochiladi.',
                   f'Shu yukni toping ({what}, taxminan {_kg(p["netto"] or 0)} kg) va oching.',
                   f'“Yuk xati №” katagiga {p["load_no"] or ""} ni yozing va “Qabulni tasdiqlash” ni bosing.',
                   'Bir nechta telashka birga tortilgan bo‘lsa — “Umumiy yuk (UY)” qilib qabul qiling.'])

    # 2. the cluster's netto and our punkt kg differ
    lim_pct = get_float('nazorat_pq_pct', 1.0) or 1.0
    for p in q('''SELECT p.id, p.code, p.netto, COALESCE(g.accepted_kg, nr.accepted_kg) ours, COALESCE(g.number, wb.number) what,
                         COALESCE(g.received_by, nr.created_by) who, p.waybill_id, p.group_id
                  FROM pq17_docs p LEFT JOIN waybills wb ON wb.id=p.waybill_id LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                  LEFT JOIN load_groups g ON g.id=p.group_id
                  WHERE (p.waybill_id IS NOT NULL OR p.group_id IS NOT NULL) AND p.netto > 0'''):
        if p['ours'] is None:
            continue
        d = round(p['netto'] - p['ours'], 1)
        if abs(d) >= max(20, p['netto'] * lim_pct / 100):
            add(f'pq:diff:{p["id"]}:{d}', 'red', f'{p["what"]}: punkt kg PQ-17 dan farq qiladi',
                f'bizda {_kg(p["ours"])} kg, PQ-17 {p["code"]} da {_kg(p["netto"])} kg (farq {d:+,.0f} kg)'.replace(',', ' '),
                _u('acct.pq17'), who=p['who'], photos=evidence(p['waybill_id'], p['group_id']),
                ask='Klaster (PQ-17) kg i bilan punkt kg i bir xil emas.', steps=fix_receipt,
                link=f'/punkt/yuk/{p["waybill_id"]}' if p['waybill_id'] else '/punkt/uy')

    # 3. received long ago and still no PQ-17 — the combine's sum is only an estimate
    days = int(get_float('nazorat_pq17_days', 3) or 3)
    since = (datetime.strptime(today_str(), '%Y-%m-%d') - timedelta(days=days)).strftime('%Y-%m-%d')
    for r in q('''SELECT wb.id, wb.number, tl.trip_no, nr.received_date FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id
                  JOIN trailer_loads tl ON tl.id=wb.load_id
                  WHERE wb.season_year=? AND wb.status<>'BEKOR' AND nr.received_date <= ?
                  AND NOT EXISTS (SELECT 1 FROM load_group_items i WHERE i.waybill_id=wb.id)
                  AND NOT EXISTS (SELECT 1 FROM pq17_docs p WHERE p.waybill_id=wb.id)''', (year, since)):
        add(f'pq:missing:wb:{r["id"]}', 'yellow', f'{r["number"]}: PQ-17 kelmadi ({days} kundan ko‘p)',
            f'{r["trip_no"]} · punkt qabul {r["received_date"]} — klasterdan PQ-17 ni oling va yuklang',
            _u('ops.waybill_detail', waybill_id=r['id']))
    for g in q('''SELECT id, number, token, received_at FROM load_groups WHERE status='QABUL' AND substr(received_at,1,10) <= ?
                  AND NOT EXISTS (SELECT 1 FROM pq17_docs p WHERE p.group_id=load_groups.id)''', (since,)):
        add(f'pq:missing:uy:{g["id"]}', 'yellow', f'{g["number"]}: PQ-17 kelmadi ({days} kundan ko‘p)',
            f'umumiy yuk, qabul {g["received_at"][:10]} — klasterdan PQ-17 ni oling va yuklang',
            _u('punkt.group', token=g['token']))

    # 4. a trailer long on the road, a trip left open
    hours = int(get_float('nazorat_hours', 24) or 24)
    for r in q('''SELECT wb.id, wb.number, tl.trip_no, wb.created_at, wb.created_by, wb.load_id FROM waybills wb
                  JOIN trailer_loads tl ON tl.id=wb.load_id
                  WHERE wb.status='YARATILDI' AND wb.arrived_at IS NULL AND wb.created_at < ?''', (_ago(hours),)):
        add(f'road:{r["id"]}', 'yellow', f'{r["number"]}: {hours} soatdan ko‘p yo‘lda',
            f'{r["trip_no"]} · jo‘natilgan {r["created_at"][:16]} — punktga yetdimi?', _u('ops.waybill_detail', waybill_id=r['id']),
            who=[r['created_by']] + punkt, photos=evidence(r['id'], load_id=r['load_id']),
            ask='Bu telashka punktga yetib bordimi?', link=f'/punkt/yuk/{r["id"]}',
            steps=['Yetib borgan bo‘lsa (punkt xodimi): havolani bosing → kg va yuk xati № ni yozing → “Qabulni tasdiqlash”.',
                   'Boshqa telashkalar bilan birga tortilgan bo‘lsa — “Umumiy yuk (UY)” ga qo‘shing.',
                   'Yetib bormagan yoki bekor bo‘lgan bo‘lsa — rahbarga ayting.'])
    for r in q('''SELECT tl.id, tl.trip_no, tl.opened_at, tl.opened_by, e.code FROM trailer_loads tl JOIN equipment e ON e.id=tl.trailer_id
                  WHERE tl.status='OCHIQ' AND tl.opened_at < ?''', (_ago(hours),)):
        add(f'open:{r["id"]}', 'yellow', f'{r["code"]}: reys {hours} soatdan ko‘p ochiq turibdi',
            f'{r["trip_no"]} · ochilgan {r["opened_at"][:16]} — yopilmagan yoki keraksiz bo‘lsa bekor qiling',
            _u('ops.load_detail', load_id=r['id']), who=r['opened_by'],
            ask='Bu reys hali ochiq turibdi.', link=f'/dala/reys/{r["id"]}',
            steps=['Havolani bosing — reys ochiladi.', 'Telashka to‘lgan bo‘lsa — “Tugatish” ni bosing.',
                   'Keraksiz (adashib ochilgan) bo‘lsa — rahbarga ayting, u bekor qiladi.'])

    # 5. the trip's waybill must equal its field weighings; an umumiy yuk's shares must equal its punkt kg
    for r in q('''SELECT wb.id, wb.number, wb.net_kg, wb.created_by, wb.load_id, (SELECT COALESCE(SUM(h.kg),0) FROM harvests h WHERE h.load_id=wb.load_id
                                                       AND h.voided_at IS NULL) field
                  FROM waybills wb JOIN weighings w ON w.load_id=wb.load_id
                  WHERE wb.season_year=? AND wb.status<>'BEKOR' AND w.basis='dala' ''', (year,)):
        if abs((r['net_kg'] or 0) - r['field']) > 0.5:
            add(f'wb:sum:{r["id"]}:{r["field"]}', 'red', f'{r["number"]}: nakladnoy kg tortishlar yig‘indisiga teng emas',
                f'nakladnoyda {_kg(r["net_kg"])} kg, tortishlar {_kg(r["field"])} kg', _u('ops.waybill_detail', waybill_id=r['id']),
                who=r['created_by'], photos=evidence(r['id'], load_id=r['load_id']),
                ask='Nakladnoydagi kg tortishlar yig‘indisiga teng emas.', link=f'/dala/reys/{r["load_id"]}',
                steps=['Havolani bosing — reysdagi barcha tortishlar chiqadi.', 'Dala daftaringiz bilan bittalab solishtiring.',
                       'Xato tortishni rahbarga ayting — tuzatishni rahbar qiladi.'])
    for g in q('''SELECT g.id, g.number, g.token, g.accepted_kg, g.received_by, (SELECT COALESCE(SUM(nr.accepted_kg),0) FROM load_group_items i
                         JOIN nayman_receipts nr ON nr.waybill_id=i.waybill_id WHERE i.group_id=g.id) shared
                  FROM load_groups g WHERE g.status='QABUL' '''):
        if abs((g['accepted_kg'] or 0) - g['shared']) > 0.5:
            add(f'uy:sum:{g["id"]}', 'red', f'{g["number"]}: telashkalarga bo‘lingan kg jamiga teng emas',
                f'jami {_kg(g["accepted_kg"])} kg, bo‘lingani {_kg(g["shared"])} kg', _u('punkt.group', token=g['token']),
                who=g['received_by'], photos=evidence(None, g['id']),
                ask='Umumiy yukdagi telashkalarga bo‘lingan kg jamiga teng emas.', link=f'/punkt/uy/{g["token"]}',
                steps=['Rasmdagi umumiy yuk qog‘ozini tekshiring: qaysi telashkalar birga tortilgan, jami netto qancha.',
                       'Havolani bosing — umumiy yuk ochiladi. Telashkalar ro‘yxati qog‘oz bilan bir xilmi?',
                       'Farq bo‘lsa — qog‘oz rasmini buxgalterga yuboring.'])

    # 6. the cluster's own table (hosil-qabuli.uz): a receipt whose kg differs, or one the table does not know
    try:
        from .hosil import compare
        cmp = compare() if q('SELECT 1 FROM hq_loads LIMIT 1') else None
    except Exception:
        cmp = None
    if cmp:
        first = min(((r['dt'] or '')[:10] for r in cmp['rows'] if r['dt']), default='')
        for r in cmp['rows']:
            o = r['ours']
            if r['state'] == 'farq' and o:
                add(f'hq:farq:{r["load_no"]}:{o["kg"]}', 'red', f'Yuk xati {r["load_no"]}: klaster {_kg(r["netto"])} kg, bizda {_kg(o["kg"])} kg',
                    f'{o["label"]} · kiritgan {o["who"] or "—"} · farq {_kg(o["kg"] - r["netto"])} kg', _u('acct.hosil_qabuli'),
                    who=o['by'], photos=evidence(o['wid'], o['gid']),
                    ask='Klaster tarozisi bilan bizdagi kg bir xil emas.', steps=fix_receipt,
                    link=f'/punkt/yuk/{o["wid"]}' if o.get('wid') else '/punkt')
            elif r['state'] == 'yoq':
                add(f'hq:yoq:{r["load_no"]}', 'red', f'Yuk xati {r["load_no"]}: klaster tortgan ({_kg(r["netto"])} kg), bizda yo‘q',
                    f'{(r["dt"] or "")[:16]} — bu yuk punktda qabul qilinmagan yoki raqami yozilmagan',
                    _u('acct.hosil_qabuli'), who=punkt,
                    ask=f'Klaster tarozisida {(r["dt"] or "")[:16]} da yuk xati {r["load_no"]} ({_kg(r["netto"])} kg) tortilgan, '
                        'tizimda bu raqamli qabul yo‘q.', link='/punkt',
                    steps=['Havolani bosing — punktdagi telashkalar ro‘yxati ochiladi.',
                           f'Shu vaqtda kelgan telashkani toping va “Yuk xati №” ga {r["load_no"]} ni yozib qabul qiling.',
                           'Allaqachon raqamsiz qabul qilingan bo‘lsa — chek rasmini buxgalterga yuboring.'])
        for c in cmp['orphans']:
            if first and (c['day'] or '') >= first:
                add(f'hq:orphan:{c["wid"]}', 'yellow', f'{c["label"]}: klaster jadvalida topilmadi ({_kg(c["kg"])} kg)',
                    f'{c["day"]} · kiritgan {c["who"] or "—"} — yuk xati № yozilmagan yoki kg boshqacha',
                    _u('acct.hosil_qabuli'), who=c['by'], photos=evidence(c['wid']),
                    ask='Bu yuk klaster jadvalida topilmadi — yuk xati raqami yozilmagan yoki kg boshqacha.', steps=fix_receipt,
                    link=f'/punkt/yuk/{c["wid"]}')

    # the office computer's daily PQ-17 task: is hosil-qabuli.uz signed in, and is the task running at all?
    st, at = get_setting('hq_session_state') or '', get_setting('hq_session_at') or ''
    if st == 'yopiq':
        add(f'hq:yopiq:{at}', 'red', 'hosil-qabuli.uz yopilgan — ERI bilan qayta kiring',
            f'{at[:16]} dan beri PQ-17 avtomatik yuklanmayapti. Kompyuterda Chrome’da hosil-qabuli.uz ni oching va ERI kalit bilan kiring.',
            _u('acct.hosil_holat'))
    elif at and at < _ago(int(get_float('hq_task_hours', 14) or 14)):
        add(f'hq:jim:{at}', 'yellow', 'PQ-17 avtomatik yuklash ishlamayapti',
            f'kompyuterdagi vazifa oxirgi marta {at[:16]} da ishlagan — kompyuter yoki Chrome yopiq bo‘lishi mumkin',
            _u('acct.hosil_holat'))

    # 7. the combine owners' money
    for c in combine_balances(year):
        url = _u('acct.combine_statement', cid=c['id'])
        if c['balance'] < 0:
            add(f'comb:over:{c["id"]}', 'red', f'{c["code"]}: hisoblangandan ko‘p to‘langan',
                f'hisoblangan {_kg(c["earned"])}, to‘langan {_kg(c["paid"])} so‘m (ortiqcha {_kg(-c["balance"])} so‘m)', url)
        elif c['provisional_amount'] and c['paid'] > c['earned'] - c['provisional_amount']:
            add(f'comb:prov:{c["id"]}', 'yellow', f'{c["code"]}: PQ-17 kelmagan (taxminiy) summadan to‘langan',
                f'tasdiqlangan {_kg(c["earned"] - c["provisional_amount"])}, to‘langan {_kg(c["paid"])} so‘m — PQ-17 kelganda farq chiqishi mumkin', url)
        if c['uncalc_kg']:
            add(f'comb:uncalc:{c["id"]}', 'red', f'{c["code"]}: {_kg(c["uncalc_kg"])} kg narxsiz',
                'tarif qo‘yilmagan — summaga kirmayapti', url)
    out.sort(key=lambda i: i['level'] != 'red')
    return out


def tick(force=False):
    """From the outbox worker: tie PQ-17s first (the one thing fixed by itself), then check; tell people about what is
    new. Runs at most every EVERY_MIN minutes."""
    db = get_db()
    last = get_setting('nazorat_last_run') or ''
    if not force and last and last > _ago(EVERY_MIN / 60):
        return None
    with tx(db):
        db.execute("INSERT INTO settings(key, value, updated_at) VALUES ('nazorat_last_run', ?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (now_str(), now_str()))
    try:
        from .pq17 import rematch
        rematch()
    except Exception:
        pass
    found = issues()
    keys = {i['key'] for i in found}
    open_ = {r['key'] for r in q('SELECT key FROM nazorat_alerts WHERE resolved_at IS NULL')}
    new = [i for i in found if i['key'] not in open_]
    with tx(db):
        for i in new:
            db.execute('''INSERT INTO nazorat_alerts(key, level, title, first_at) VALUES (?,?,?,?)
                          ON CONFLICT(key) DO UPDATE SET resolved_at=NULL, level=excluded.level, title=excluded.title,
                          first_at=excluded.first_at''', (i['key'], i['level'], i['title'], now_str()))
        for k in open_ - keys:
            db.execute('UPDATE nazorat_alerts SET resolved_at=? WHERE key=?', (now_str(), k))
        if new:
            _tell(db, new)
        _tell_people(db, found)
    return {'found': len(found), 'new': len(new)}


def _tell(db, new):
    from .outbox import enqueue
    reds = [i for i in new if i['level'] == 'red']
    head = (f'🔴 NAZORAT: {len(reds)} ta xato' if reds else '🟡 NAZORAT') + f' · {len(new)} ta yangi'
    lines = [f'{"🔴" if i["level"] == "red" else "🟡"} {i["title"]}\n   {i["detail"]}' for i in new[:12]]
    if len(new) > 12:
        lines.append(f'… yana {len(new) - 12} ta')
    domain = get_setting('public_domain') or ''
    text = '\n'.join([head, *lines, '', 'Hammasi: surxan-paxta.uz/nazorat' if not domain else f'Hammasi: {domain}/nazorat'])
    stamp = now_str()[:16]
    enqueue(db, 'telegram_report', 'alert', f'nazorat:{stamp}', {'text': f'SURXAN-PAXTA.UZ\n{text}'})
    for u in db.execute("""SELECT id, telegram_id FROM users WHERE active=1 AND telegram_id IS NOT NULL
                           AND role IN ('admin','manager','accountant')""").fetchall():
        enqueue(db, 'telegram_report', 'alert', f'nazorat:{stamp}:u{u["id"]}',
                {'text': f'SURXAN-PAXTA.UZ\n{text}', 'chat_id': u['telegram_id'], 'main_bot': True})


NUM = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣']


def _abs(path):
    try:
        from flask import current_app
        domain = current_app.config['SURXON'].DOMAIN
    except Exception:
        domain = 'surxan-paxta.uz'
    return f'https://{domain}{path}'


def person_text(i):
    """What one worker reads on Telegram: what is wrong, then numbered simple steps, a link that opens the exact page,
    and the photos under it."""
    lines = ['SURXAN-PAXTA.UZ', f'{"🔴" if i["level"] == "red" else "🟡"} SIZDAN HARAKAT KERAK', '', f'📌 {i["title"]}', i['detail']]
    if i.get('ask'):
        lines += ['', i['ask']]
    if i.get('steps'):
        lines += ['', 'Nima qilish kerak:'] + [f'{NUM[n] if n < len(NUM) else f"{n + 1}."} {t}' for n, t in enumerate(i['steps'])]
    if i.get('link'):
        lines += ['', f'👉 Ochish: {_abs(i["link"])}']
    if i.get('photos'):
        lines += ['', '📷 Rasm(lar) shu xabarda.']
    return '\n'.join(lines)


def _tell_people(db, found):
    """Each open problem to the person who must act on it — with the photos and what to check / fix. Once per problem
    and person (the outbox key), also for problems that were open before this was switched on."""
    from .outbox import enqueue
    for i in found:
        who = i.get('who')
        for uid in dict.fromkeys(who if isinstance(who, (list, tuple)) else [who]):
            u = db.execute('SELECT id, telegram_id FROM users WHERE id=? AND active=1', (uid,)).fetchone() if uid else None
            if not u or not u['telegram_id']:
                continue
            msg = person_text(i)
            enqueue(db, 'telegram_report', 'xodim', f'nazorat:{i["key"]}:u{u["id"]}',
                    {'text': msg.strip(), 'chat_id': u['telegram_id'], 'main_bot': True, 'photos': i.get('photos') or []})
