"""Yordamchi: the director's assistant and the accountant's invoice reminder.

- every day at `director_report_time` (23:00) the director (Rahbar / Admin) gets one Telegram message with the day's
  facts — cotton, punkt, PQ-17 from the cluster, combines, money — and what has to be done tomorrow;
- every day at the same time, and at once when a new PQ-17 arrives, the accountant gets the PQ-17s that still need our
  signature and the invoice (faktura) to the cluster in hosil-qabuli.uz: kg, price, sum with VAT, per cluster.

Only reads the same tables as every other screen; the message goes to the person's own chat (the bot they linked),
and when nobody has linked Telegram, to the report group.
"""
from datetime import date, timedelta

from .db import get_db, q, scalar, tx
from .outbox import enqueue
from .settings import get_setting
from .utils import now_str, today_str


def _n(v):
    return f'{int(round(v or 0)):,}'.replace(',', ' ')


def _d(day):
    return f'{day[8:10]}.{day[5:7]}.{day[:4]}'


def faktura_queue():
    """PQ-17s whose invoice is not signed yet, per cluster: [{name, inn, docs, kond, amount, vat, sign, invoice}]."""
    from .pq17 import stage
    out = {}
    for d in q("SELECT * FROM pq17_docs WHERE COALESCE(invoice_status,'')<>'imzolandi' ORDER BY doc_date, id"):
        key = d['cluster_inn'] or d['cluster_name'] or '?'
        c = out.setdefault(key, {'inn': key, 'name': d['cluster_name'] or 'Noma’lum klaster', 'docs': [], 'kond': 0,
                                 'netto': 0, 'amount': 0, 'vat': 0, 'sign': 0, 'invoice': 0})
        st = stage(d)
        c['docs'].append(dict(d, stage=st))
        c['kond'] += d['kond_kg']
        c['netto'] += d['netto']
        c['amount'] += d['amount']
        c['vat'] += d['vat'] or 0
        c['sign'] += st[0] == 'sign'
        c['invoice'] += st[0] in ('inv0', 'inv')
    return list(out.values())


def pq17_missing():
    """Trips (and umumiy yuklar) the punkt accepted that have no PQ-17 in the system yet: [(label, day, kg)]."""
    rows = [(f'{r["number"]} ({r["trip_no"]})', r['received_date'], r['accepted_kg']) for r in q(
        """SELECT wb.number, tl.trip_no, nr.received_date, nr.accepted_kg FROM nayman_receipts nr
           JOIN waybills wb ON wb.id=nr.waybill_id JOIN trailer_loads tl ON tl.id=wb.load_id
           WHERE wb.status<>'BEKOR' AND NOT EXISTS (SELECT 1 FROM load_group_items i WHERE i.waybill_id=wb.id)
           AND NOT EXISTS (SELECT 1 FROM pq17_docs p WHERE p.waybill_id=wb.id) ORDER BY nr.received_date, wb.id""")]
    rows += [(g['number'], (g['received_at'] or '')[:10], g['accepted_kg']) for g in q(
        """SELECT number, received_at, accepted_kg FROM load_groups WHERE status='QABUL'
           AND NOT EXISTS (SELECT 1 FROM pq17_docs p WHERE p.group_id=load_groups.id) ORDER BY received_at""")]
    return rows


def accountant_text():
    """The invoice list for the accountant ('' when nothing waits)."""
    lines = []
    miss = pq17_missing()
    if miss:
        lines += ['', f'📥 PQ-17 HALI YUKLANMAGAN: {len(miss)} ta (punkt qabul qilgan)']
        lines += [f'• {lab} · {_d(day) if day else "—"} · {_n(kg)} kg' for lab, day, kg in miss[:20]]
        if len(miss) > 20:
            lines.append(f'… yana {len(miss) - 20} ta')
        lines.append('   → hosil-qabuli.uz dan PQ-17 PDF ni oling va Telegram botga yuboring (yoki Agroklaster sverkasi sahifasiga yuklang).')
    for c in faktura_queue():
        docs = c['docs']
        kond, amount, vat = (sum(d[k] or 0 for d in docs) for k in ('kond_kg', 'amount', 'vat'))
        lines += ['', f'🏭 {c["name"]} (STIR {c["inn"]})',
                  f'{len(docs)} ta PQ-17 · {_n(kond)} kg · {_n(amount)} so‘m (QQS {_n(vat)})']
        for d in docs[:25]:
            what = 'Kombayn' if d['method'] == 'combine' else 'Qo‘l' if d['method'] == 'hand' else (d['harvest_raw'] or '')
            lines.append(f'• {d["code"]} · {_d(d["doc_date"]) if d["doc_date"] else "—"} · yuk xati {d["load_no"]} · {what} · '
                         f'{_n(d["kond_kg"])} kg × {d["price"]:,.2f} = {_n(d["amount"])}'.replace(',', ' ')
                         + f'\n   → {d["stage"][1]}')
        if len(docs) > 25:
            lines.append(f'… yana {len(docs) - 25} ta')
    if not lines:
        return ''
    return '\n'.join(['SURXAN-PAXTA.UZ', '🧾 BUXGALTER: PQ-17 VA FAKTURA', *lines, '',
                      'Qilish kerak: hosil-qabuli.uz → PQ-17 ni imzolang → klasterga faktura yarating va imzolang.',
                      'Keyin tizimda belgilang: Faktura sahifasi → “Faktura yaratildi / imzolandi”.'])


def director_text(day=None):
    """The director's evening report: exact facts of the day and what to do tomorrow."""
    from .accounting import combine_balances, cotton_totals, total_balance
    from .services import current_season
    db = get_db()
    day = day or today_str()
    year = current_season(db)
    h = q('''SELECT COALESCE(SUM(h.kg),0) kg, COALESCE(SUM(CASE WHEN h.method='hand' THEN h.kg END),0) hand,
                    COALESCE(SUM(CASE WHEN h.method='combine' THEN h.kg END),0) comb,
                    COUNT(DISTINCT CASE WHEN h.method='hand' THEN h.worker_id END) people
             FROM harvests h JOIN trailer_loads tl ON tl.id=h.load_id
             WHERE h.voided_at IS NULL AND tl.status<>'BEKOR' AND h.work_date=?''', (day,), one=True)
    sent = q('''SELECT COUNT(*) n, COALESCE(SUM(net_kg),0) kg FROM waybills WHERE status<>'BEKOR' AND document_date=?''', (day,), one=True)
    rec = q('''SELECT COUNT(*) n, COALESCE(SUM(nr.accepted_kg),0) acc, COALESCE(SUM(wb.net_kg),0) shipped
               FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id
               WHERE wb.status<>'BEKOR' AND nr.received_date=?''', (day,), one=True)
    road = scalar("SELECT COUNT(*) FROM waybills WHERE status='YARATILDI' AND season_year=?", (year,))
    open_trips = scalar("SELECT COUNT(*) FROM trailer_loads WHERE status='OCHIQ'")
    season = cotton_totals(year)
    lines = [f'SURXAN-PAXTA.UZ · {_d(day)}', '📋 DIREKTOR HISOBOTI', '',
             '🌱 PAXTA (bugun)',
             f'Terildi: {_n(h["kg"])} kg (qo‘l {_n(h["hand"])} kg, {h["people"]} kishi · kombayn {_n(h["comb"])} kg)',
             f'Punktga jo‘natildi: {sent["n"]} ta nakladnoy · {_n(sent["kg"])} kg',
             f'Punkt qabul qildi: {rec["n"]} ta · {_n(rec["acc"])} kg'
             + (f' (farq {rec["acc"] - rec["shipped"]:+,.0f} kg)'.replace(',', ' ') if rec['n'] else ''),
             f'Hozir yo‘lda: {road} ta · dalada ochiq telashka: {open_trips} ta',
             f'Mavsum: terildi {_n(season["field_kg"])} kg · punkt qabul {_n(season["punkt_kg"])} kg']

    pq = q('''SELECT COUNT(*) n, COALESCE(SUM(netto),0) netto, COALESCE(SUM(kond_kg),0) kond,
                     COALESCE(SUM(deduction_kg - COALESCE(bonus_kg,0)),0) ded, COALESCE(SUM(amount),0) amount,
                     SUM(netto * COALESCE(moist_pct,0)) / NULLIF(SUM(netto),0) moist, SUM(netto * COALESCE(dirt_pct,0)) / NULLIF(SUM(netto),0) dirt
              FROM pq17_docs WHERE substr(uploaded_at,1,10)=?''', (day,), one=True)      # arrived in the system today
    pq_all = q('SELECT COUNT(*) n, COALESCE(SUM(kond_kg),0) kond, COALESCE(SUM(amount),0) amount FROM pq17_docs', one=True)
    received = scalar('SELECT COALESCE(SUM(amount),0) FROM payments WHERE season_year=? AND voided_at IS NULL', (year,))
    lines += ['', '🏭 KLASTER (PQ-17)']
    if pq['n']:
        lines.append(f'Bugun: {pq["n"]} ta · topshirildi {_n(pq["netto"])} kg → to‘lanadi {_n(pq["kond"])} kg '
                     f'(chegirma {_n(pq["ded"])} kg; namlik {pq["moist"] or 0:.1f}%, ifloslik {pq["dirt"] or 0:.1f}%) · {_n(pq["amount"])} so‘m')
    else:
        lines.append('Bugun PQ-17 kelmadi')
    lines.append(f'Mavsum: {pq_all["n"]} ta · {_n(pq_all["kond"])} kg · {_n(pq_all["amount"])} so‘m · '
                 f'klaster to‘ladi {_n(received)} · qarzi {_n(pq_all["amount"] - received)} so‘m')

    combs = q('''SELECT e.code, SUM(h.kg) kg FROM harvests h JOIN equipment e ON e.id=h.combine_id JOIN trailer_loads tl ON tl.id=h.load_id
                 WHERE h.method='combine' AND h.voided_at IS NULL AND tl.status<>'BEKOR' AND h.work_date=? GROUP BY e.id ORDER BY kg DESC''', (day,))
    bal = combine_balances(year)
    lines += ['', '🚜 KOMBAYNLAR']
    lines.append('Bugun: ' + (', '.join(f'{c["code"]} {_n(c["kg"])} kg' for c in combs) if combs else 'ishlamadi'))
    if bal:
        earned, paid = sum(c['earned'] for c in bal), sum(c['paid'] for c in bal)
        prov = sum(c['provisional_amount'] or 0 for c in bal)
        lines.append(f'Mavsum: hisoblangan {_n(earned)} · to‘langan {_n(paid)} · qoldiq {_n(earned - paid)} so‘m'
                     + (f' (shundan {_n(prov)} taxminiy — PQ-17 kutilmoqda)' if prov else ''))

    cash = q('''SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount END),0) i, COALESCE(SUM(CASE WHEN direction='OUT' THEN amount END),0) o
                FROM cash_entries WHERE voided_at IS NULL AND entry_date=?''', (day,), one=True)
    exp = scalar('SELECT COALESCE(SUM(amount),0) FROM expenses WHERE voided_at IS NULL AND expense_date=?', (day,))
    lines += ['', '💰 PUL (bugun)', f'Kassa kirim {_n(cash["i"])} · chiqim {_n(cash["o"])} · xarajat {_n(exp)} so‘m',
              f'Kassa qoldig‘i: {_n(total_balance(db))} so‘m']

    todo = []
    from .nazorat import issues
    found = issues(year)
    reds = [i for i in found if i['level'] == 'red']
    for i in reds[:5]:
        todo.append(f'🔴 {i["title"]}')
    if len(reds) > 5:
        todo.append(f'🔴 … yana {len(reds) - 5} ta xato (Nazorat sahifasida)')
    yel = len(found) - len(reds)
    if yel:
        todo.append(f'🟡 {yel} ta kechikish (Nazorat sahifasida)')
    fq = faktura_queue()
    sign, inv = sum(c['sign'] for c in fq), sum(c['invoice'] for c in fq)
    if sign:
        todo.append(f'✍ {sign} ta PQ-17 ni imzolash kerak (hosil-qabuli.uz)')
    if inv:
        todo.append(f'🧾 {inv} ta PQ-17 ga faktura kerak · {_n(sum(c["amount"] for c in fq))} so‘m')
    try:
        from .loans import obligations
        for o in obligations(open_only=True, days=3, today=day):
            todo.append(f'📅 {_d(o["due_date"])} — {o["title"]}' + (f' ({o["party_name"]})' if o.get('party_name') else ''))
    except Exception:
        pass
    unchecked = scalar("SELECT COUNT(*) FROM expenses WHERE status='TEKSHIRILMAGAN' AND voided_at IS NULL")
    if unchecked:
        todo.append(f'🧾 {unchecked} ta xarajat tasdiqlanmagan')
    lines += ['', '✅ ERTAGA QILISH KERAK'] + (todo or ['Hammasi joyida — muammo yo‘q 👍'])
    return '\n'.join(lines)


def _people(db, roles):
    marks = ','.join('?' * len(roles))
    return db.execute(f'SELECT id, telegram_id FROM users WHERE active=1 AND telegram_id IS NOT NULL AND role IN ({marks})',
                      roles).fetchall()


def _send(db, kind, ref, text, roles, extra=None):
    """Personal chats of these roles; the report group when nobody of them linked Telegram."""
    people = _people(db, roles)
    for u in people:
        enqueue(db, 'telegram_report', kind, f'{ref}:u{u["id"]}',
                {'text': text, 'chat_id': u['telegram_id'], 'main_bot': True, **(extra or {})})
    if not people:
        enqueue(db, 'telegram_report', kind, ref, {'text': text, **(extra or {})})
    return len(people)


DIRECTOR_ROLES = ('admin', 'manager')
ACCOUNTANT_ROLES = ('accountant',)
AKT_ROLES = ('admin', 'manager', 'accountant', 'cashier')


def combines_worked(day):
    """Combines that picked (or have a day / hectare job) on that day."""
    return [r['id'] for r in q("""SELECT DISTINCT combine_id id FROM harvests h JOIN trailer_loads tl ON tl.id=h.load_id
                                  WHERE h.method='combine' AND h.voided_at IS NULL AND tl.status<>'BEKOR' AND h.work_date=?
                                  UNION SELECT combine_id FROM combine_work WHERE voided_at IS NULL AND work_date=?""", (day, day))]


def combine_day_akt(cid, day):
    """(pdf bytes, file name, caption) — one combine's akt-sverka for one day, built when it is sent (always fresh)."""
    from .accounting import TARIFF_TYPES, combine_statement
    from .pdfdoc import build_combine_statement_pdf
    from .services import current_season
    year = current_season(get_db())
    st = combine_statement(year, cid, day, day)
    c, season = st['combine'], st['season']
    pdf = build_combine_statement_pdf(st, company=get_setting('company_name') or 'SURXON', year=year,
                                      tariff_name=TARIFF_TYPES.get(c['tariff_type'] or '', ''), generated_at=now_str(),
                                      generated_by='Tizim (avtomatik)')
    sure = st['sof_kg']
    cap = [f'🚜 {c["code"]} · {c["owner"]} · {_d(day)} — KUNLIK AKT-SVERKA',
           f'Dala kg: {_n(st["kg"])} · punkt netto: {_n(st["punkt_kg"])} · PQ-17 bilan tasdiqlangan: {_n(sure)} kg']
    if st['waiting_kg']:
        cap.append(f'⏳ {_n(st["waiting_kg"])} kg hali punktga yetmagan — summaga kirmagan')
    if st['pq_wait_kg']:
        cap.append(f'🟡 {_n(st["pq_wait_kg"])} kg PQ-17 kutilmoqda — summa taxminiy')
    cap += [f'Bugun hisoblangan: {_n(c["earned"])} so‘m · to‘langan: {_n(c["paid"])} so‘m',
            f'Jami qoldiq: {_n(season["balance"])} so‘m · HOZIR TO‘LASH MUMKIN: {_n(season["payable"])} so‘m'
            + (f' ({_n(season["held"])} PQ-17 kelguncha ushlab turiladi)' if season['held'] > 0 else '')]
    return pdf, f'akt_{c["code"]}_{day}.pdf', '\n'.join(cap)


def send_evening(day=None, force=False):
    """Queue tonight's director report and the accountant's invoice list (once a day; force = again now)."""
    db = get_db()
    day = day or today_str()
    tag = now_str()[11:19].replace(':', '') if force else ''
    with tx(db):
        _send(db, 'director', f'director:{day}{tag}', director_text(day), DIRECTOR_ROLES)
        for cid in combines_worked(day):
            _send(db, 'akt', f'akt:{day}:{cid}{tag}', '', AKT_ROLES, extra={'combine_akt': {'cid': cid, 'day': day}})
        acc = accountant_text()
        if acc:
            _send(db, 'faktura', f'faktura:{day}{tag}', acc, ACCOUNTANT_ROLES)
        db.execute("INSERT INTO settings(key, value, updated_at) VALUES ('director_report_on', ?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (day, now_str()))
    return True


def remind_text():
    """Short daytime reminder for the accountant ('' when nothing is missing or waiting)."""
    miss = pq17_missing()
    fq = faktura_queue()
    sign, inv = sum(c['sign'] for c in fq), sum(c['invoice'] for c in fq)
    if not (miss or sign or inv):
        return ''
    lines = ['SURXAN-PAXTA.UZ', '⏰ ESLATMA — BUXGALTER']
    if miss:
        lines.append(f'📥 {len(miss)} ta yukning PQ-17 si hali yuklanmagan ({_n(sum(kg or 0 for _l, _d_, kg in miss))} kg):')
        lines += [f'• {lab} · {_d(day) if day else "—"} · {_n(kg)} kg' for lab, day, kg in miss[:10]]
        if len(miss) > 10:
            lines.append(f'… yana {len(miss) - 10} ta')
        lines.append('→ hosil-qabuli.uz dan PDF ni oling va shu botga yuboring.')
    if sign:
        lines.append(f'✍ {sign} ta PQ-17 ni hosil-qabuli.uz da imzolash kerak')
    if inv:
        lines.append(f'🧾 {inv} ta PQ-17 ga faktura yaratish / imzolash kerak')
    return '\n'.join(lines)


def maybe_remind():
    """From the outbox worker: at each pq17_remind_times (10:00, 15:00) remind the accountant once, if anything waits."""
    db = get_db()
    day, now = today_str(), now_str()[11:16]
    passed = sorted(x.strip() for x in (get_setting('pq17_remind_times') or '').split(',') if x.strip() and x.strip() <= now)
    if not passed:
        return False
    key = f'pq17remind:{day}:{passed[-1]}'          # only the latest time that has come (a late worker sends one, not all)
    if (get_setting('pq17_reminded') or '') >= key:
        return False
    text = remind_text()
    with tx(db):
        if text:
            _send(db, 'remind', key, text, ACCOUNTANT_ROLES)
        db.execute("INSERT INTO settings(key, value, updated_at) VALUES ('pq17_reminded', ?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, now_str()))
    return bool(text)


def maybe_send_evening():
    """From the outbox worker: after director_report_time (23:00), once a day."""
    at = (get_setting('director_report_time') or '23:00').strip()
    if now_str()[11:16] < at or (get_setting('director_report_on') or '') == today_str():
        return False
    return send_evening()


def pq17_arrived(db, doc_ids):
    """Inside the import transaction: tell the accountant at once that new PQ-17s wait for signature and invoice."""
    ids = [i for i in doc_ids if i]
    if not ids:
        return
    from .pq17 import stage
    rows = {r['id']: r for r in q(f'SELECT * FROM pq17_docs WHERE id IN ({",".join("?" * len(ids))})', ids)}
    lines = []
    for i in ids:
        d = rows.get(i)
        if not d:
            continue
        what = 'Kombayn' if d['method'] == 'combine' else 'Qo‘l' if d['method'] == 'hand' else (d['harvest_raw'] or '')
        lines.append(f'• {d["code"]} · {_d(d["doc_date"]) if d["doc_date"] else "—"} · yuk xati {d["load_no"]} · {what}\n'
                     f'   {_n(d["netto"])} kg → to‘lanadi {_n(d["kond_kg"])} kg × {d["price"]:,.2f} = {_n(d["amount"])} so‘m '
                     f'(QQS {_n(d["vat"])})'.replace(',', ' ') + f'\n   {d["cluster_name"] or ""} · → {stage(d)[1]}')
    if not lines:
        return
    text = '\n'.join(['SURXAN-PAXTA.UZ', '🧾 YANGI PQ-17 KELDI — faktura kerak', *lines, '',
                      'hosil-qabuli.uz → PQ-17 ni imzolang → klasterga faktura yarating.',
                      'Keyin tizimda: Faktura sahifasi → “Faktura yaratildi / imzolandi”.'])
    _send(db, 'faktura', 'pq17new:' + '-'.join(str(i) for i in ids), text, ACCOUNTANT_ROLES)
