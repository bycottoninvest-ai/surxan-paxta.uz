"""SURXON TEZ-PUL: hand picking paid in cash by numbered paper talons.

1. The notebook: “talon № — name” (names never go into the phone).
2. Field (terim hisobchisi, two steps): scan the talon → type its kg → SAQLASH. The kg is written as an ordinary
   hand-picking weighing (Qo‘l terimi) of the open trailer trip, priced at the trip's rate, so the punkt check, the
   trip documents and the reports see it like any other weighing. The date is the day it was scanned — talons are
   printed once, without a date. Each talon is its own card (“Talon 000274”, hidden from the name lists).
3. Kassa (two steps): scan → the amount owed on that talon appears → PUL BERILDI. A starshi brings his team's talons:
   the cashier scans them one after another and pays the total with one PUL BERILDI. The money leaves the existing
   kassa as ordinary worker payments (payment order + PAY- cash entry per talon), so the worker balances stay exact.
One talon is paid once, ever: the status flips in one guarded UPDATE inside an IMMEDIATE transaction (a second scan,
a photocopy or two cashiers at the same second pay once). The QR holds only the number and a random code stored with
the talon (no kg, no money): a home-made QR with a real number but a wrong code is refused.
"""
import json
import secrets

from flask import current_app

from .db import get_db, q, tx
from .security import audit
from .settings import get_float, get_setting
from .utils import UserError, fmt_money, fmt_num, name_key, now_str, today_str

MAX_BATCH = 2000
PREFIX = 'SPX-T'
STATUS = {
    'BOSH': ('Kg yozilmagan', 'b-gray'),
    'KUTILMOQDA': ('Eski tartib — bekor qiling', 'b-orange'),
    'TAYYOR': ('To‘lovga tayyor', 'b-blue'),
    'TOLANDI': ('To‘landi', 'b-green'),
    'BEKOR': ('Bekor qilingan', 'b-red'),
}


class Refused(UserError):
    """A scan that must show a big coloured screen (already paid, fake, cancelled, no kg yet) — carries what to show."""
    def __init__(self, kind, message, talon=None):
        super().__init__(message)
        self.kind, self.talon = kind, talon


def _need(actor, *perms):
    if not actor or not any(actor.can(p) for p in perms):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')


def number_text(n):
    return f'{int(n):06d}'


def qr_text(t):
    return f'{PREFIX}:{number_text(t["number"])}:{t["code"]}'


def in_use():
    return bool(q('SELECT 1 FROM tezpul_talons LIMIT 1', one=True))


# ------------------------------------------------------------------ issuing (admin)

def create_batch(actor, count, brigadier_id=None):
    """New talons numbered after the last one (000001 …). Returns (batch, first, last)."""
    _need(actor, 'tezpul.manage')
    try:
        count = int(str(count).replace(' ', ''))
    except ValueError:
        raise UserError('Nechta talon kerakligini raqam bilan yozing (masalan 1000).')
    if not 1 <= count <= MAX_BATCH:
        raise UserError(f'Bir martada 1 dan {MAX_BATCH} tagacha talon chiqarish mumkin.')
    with tx() as db:
        if brigadier_id and not db.execute('SELECT 1 FROM brigadiers WHERE id=?', (brigadier_id,)).fetchone():
            raise UserError('Brigada topilmadi.')
        last = db.execute('SELECT MAX(number) m, MAX(batch) b FROM tezpul_talons').fetchone()
        start, batch, now = (last['m'] or 0) + 1, (last['b'] or 0) + 1, now_str()
        db.executemany('INSERT INTO tezpul_talons(number, code, batch, brigadier_id, created_by, created_at) VALUES (?,?,?,?,?,?)',
                       [(n, secrets.token_hex(4).upper(), batch, brigadier_id or None, actor.user_id, now)
                        for n in range(start, start + count)])
        audit(db, actor, 'CREATE', 'tezpul_talons', batch, new={'count': count, 'from': number_text(start),
                                                                 'to': number_text(start + count - 1), 'brigadier_id': brigadier_id})
    return batch, number_text(start), number_text(start + count - 1)


def void(actor, number, reason):
    """Lost / torn / stolen blank talon: never usable again."""
    _need(actor, 'tezpul.manage')
    reason = (reason or '').strip()
    if not reason:
        raise UserError('Bekor qilish sababini yozing.')
    with tx() as db:
        t = db.execute('SELECT * FROM tezpul_talons WHERE number=?', (_num(number),)).fetchone()
        if not t:
            raise UserError('Bunday talon yo‘q.')
        if t['status'] == 'TOLANDI':
            raise UserError(f'№{number_text(t["number"])} allaqachon to‘langan — bekor qilib bo‘lmaydi.')
        if t['status'] == 'TAYYOR' and _live_harvest(db, t):
            raise UserError(f'№{number_text(t["number"])} ga kg yozilgan, puli hali berilmagan. Talon yo‘qolgan bo‘lsa — '
                            f'pulni daftar bo‘yicha “Mening kassam → Ishchiga pul berish” da “Talon {number_text(t["number"])}” '
                            'ni topib bering; kg xato yozilgan bo‘lsa — avval o‘sha tortishni bekor qiling.')
        db.execute("UPDATE tezpul_talons SET status='BEKOR', void_reason=?, voided_by=?, voided_at=? WHERE id=?",
                   (reason[:200], actor.user_id, now_str(), t['id']))
        audit(db, actor, 'VOID', 'tezpul_talon', t['id'], old={'status': t['status']}, new={'reason': reason[:200]})


# ------------------------------------------------------------------ scanning

def _num(text):
    digits = ''.join(ch for ch in str(text or '') if ch.isdigit())
    return int(digits) if digits and len(digits) <= 9 else None


def parse(code, manual_code=''):
    """(number, code) from a scanned QR “SPX-T:000274:7C41E0B9” or from the number + code typed by hand."""
    text = (code or '').strip().upper()
    if text.startswith(PREFIX + ':'):
        parts = text.split(':')
        if len(parts) == 3:
            return _num(parts[1]), parts[2].strip()
        return None, None
    return _num(text), (manual_code or '').strip().upper().replace(' ', '').replace('-', '')


def _log(db, actor, kind, code, talon_id=None, note=''):
    db.execute('INSERT INTO tezpul_events(created_at, user_id, code, kind, talon_id, note) VALUES (?,?,?,?,?,?)',
               (now_str(), actor.user_id if actor else None, (code or '')[:60], kind, talon_id, note[:200]))


def _live_harvest(db, t):
    if not t['harvest_id']:
        return None
    h = db.execute('SELECT * FROM harvests WHERE id=?', (t['harvest_id'],)).fetchone()
    return h if h and not h['voided_at'] else None


def _payable(db, t):
    """What the kassa gives for this talon now: what its card is still owed (0 once paid in any way)."""
    from .accounting import season_of
    from .wallet import worker_figures
    if not t['worker_id']:
        return 0
    return worker_figures(db, season_of(db), t['worker_id'])['payable']


def _view(db, t):
    names = {}
    for col in ('paid_by', 'entered_by'):
        if t[col]:
            u = db.execute('SELECT full_name FROM users WHERE id=?', (t[col],)).fetchone()
            names[col] = u['full_name'] if u else None
    b = db.execute('SELECT name FROM brigadiers WHERE id=?', (t['brigadier_id'],)).fetchone() if t['brigadier_id'] else None
    amount = _payable(db, t) if t['status'] == 'TAYYOR' else t['amount']
    return {'number': number_text(t['number']), 'status': t['status'], 'status_label': STATUS[t['status']][0],
            'kind': t['kind'] or 'odam', 'kg': t['kg'], 'rate': t['rate'], 'amount': amount,
            'brigade': b['name'] if b else None, 'paid_at': t['paid_at'], 'paid_by': names.get('paid_by'),
            'entered_at': t['entered_at'], 'entered_by': names.get('entered_by'), 'void_reason': t['void_reason']}


def _find(db, code, manual_code=''):
    number, sig = parse(code, manual_code)
    t = db.execute('SELECT * FROM tezpul_talons WHERE number=?', (number,)).fetchone() if number else None
    if not t or not sig or not secrets.compare_digest(sig, t['code']):
        raise Refused('soxta', 'TALON HAQIQIY EMAS — soxta yoki bizniki emas.' if number and sig else
                      'Talon tanilmadi. QR ni qayta skanerlang yoki raqam va kodni to‘g‘ri yozing.',
                      {'id': t['id']} if t else None)
    if t['status'] == 'BEKOR':
        raise Refused('bekor', f'№{number_text(t["number"])} BEKOR QILINGAN: {t["void_reason"] or ""}.',
                      {**_view(db, t), 'id': t['id']})
    if t['status'] == 'TOLANDI':
        raise Refused('tolangan', f'№{number_text(t["number"])} ALLAQACHON TO‘LANGAN.', {**_view(db, t), 'id': t['id']})
    if t['status'] == 'KUTILMOQDA' or (t['status'] == 'TAYYOR' and not t['worker_id']):
        raise UserError(f'№{number_text(t["number"])} eski tartibda yozilgan — rahbar uni bekor qilsin.')
    return t


def _logged(actor, code, fn):
    """Run fn; a refused scan (fake, paid, cancelled, written twice) goes to the rahbar's error list after the rollback."""
    try:
        return fn()
    except Refused as err:
        if err.kind in ('soxta', 'bekor', 'tolangan', 'kiritilgan'):
            with tx() as db:
                _log(db, actor, {'tolangan': 'takror'}.get(err.kind, err.kind), code, (err.talon or {}).get('id'))
        raise


def _kg(value):
    try:
        kg = round(float(str(value or '').replace(' ', '').replace(',', '.')), 1)
    except ValueError:
        raise UserError('Kg ni raqam bilan yozing (masalan 37,5).')
    if kg <= 0:
        raise UserError('Talondagi kg ni yozing.')
    return kg


# ------------------------------------------------------------------ the field: scan → kg → SAQLASH

def _entered_check(db, t):
    if t['status'] == 'TAYYOR' and _live_harvest(db, t):
        v = _view(db, t)
        raise Refused('kiritilgan', f'№{v["number"]} ga {t["kg"]:g} kg allaqachon yozilgan '
                                    f'({(t["entered_at"] or "")[11:16]}, {v["entered_by"] or ""}). Bir talon — bir marta.',
                      {**v, 'id': t['id']})


def field_lookup(actor, code, manual_code=''):
    _need(actor, 'harvest.write')

    def go():
        db = get_db()
        t = _find(db, code, manual_code)
        _entered_check(db, t)
        return _view(db, t)
    return _logged(actor, code, go)


# rows on a starshi talon sheet (pdfdoc.build_tezpul_starshi_pdf): the first sheet has the header, the others more rows
STARSHI_ROWS_FIRST, STARSHI_ROWS_NEXT = 19, 21


def sheets_needed(people):
    """How many sheets a starshi's list of `people` takes (30 people → 2 sheets)."""
    people = int(people or 0)
    return 1 if people <= STARSHI_ROWS_FIRST else 1 + -(-(people - STARSHI_ROWS_FIRST) // STARSHI_ROWS_NEXT)


def _clean_sheets(db, talon_id, sheets):
    """The photographed sheets as the clerk confirmed them: [{sheet, photo_id, kg, rows:[{row, before, after, total}]}]."""
    out = []
    for s in (sheets or [])[:10]:
        try:
            no, kg = int(s.get('sheet')), round(float(s.get('kg')), 1)
        except (TypeError, ValueError, AttributeError):
            continue
        pid = s.get('photo_id')
        if pid and not db.execute("SELECT 1 FROM photos WHERE id=? AND entity_type='tezpul_talon' AND entity_id=?",
                                  (pid, talon_id)).fetchone():
            pid = None
        rows = []
        for r in (s.get('rows') or [])[:40]:
            try:
                rows.append({'row': int(r['row']), 'before': r.get('before'), 'after': r.get('after'),
                             'total': round(float(r['total']), 1)})
            except (TypeError, ValueError, KeyError):
                continue
        if no > 0 and kg >= 0 and no not in {x['sheet'] for x in out}:
            out.append({'sheet': no, 'photo_id': pid, 'kg': kg, 'rows': rows})
    return out


def enter(actor, load_id, code, kg, *, manual_code='', client_uuid=None, group=False, sheets=None, people=None, driver=None):
    """The terim hisobchisi saved one talon: its kg becomes a hand weighing of the open trip (Qo‘l terimi).
    group: a starshi's talon — one talon for his whole team's kg (over the one-person limit, up to the group limit).
    sheets / people: the starshi's photographed sheets and head count — kept with the talon, sent as a PDF."""
    _need(actor, 'harvest.write')
    return _logged(actor, code, lambda: _enter(actor, load_id, code, kg, manual_code, client_uuid, group, sheets, people,
                                              driver=driver))


def evening_brigades(day=None):
    """Every active brigade for the evening count — those with a hand trailer today first. The starshi's kg goes to the
    brigade's last trailer of the day; a brigade with none entered today (the punkt writes the papers later) still
    gets its talons saved — on a pay-only holder trip of the day (see _pay_holder)."""
    return q('''SELECT b.id, b.name,
                       (SELECT MAX(tl.id) FROM trailer_loads tl WHERE tl.brigadier_id=b.id AND COALESCE(tl.method,'hand')='hand'
                        AND tl.status<>'BEKOR' AND substr(tl.opened_at,1,10)=?) last_load,
                       (SELECT COUNT(*) FROM trailer_loads tl WHERE tl.brigadier_id=b.id AND COALESCE(tl.method,'hand')='hand'
                        AND tl.status<>'BEKOR' AND COALESCE(tl.pay_only,0)=0 AND substr(tl.opened_at,1,10)=?) trips
                FROM brigadiers b WHERE b.active=1 ORDER BY trips=0, b.name COLLATE NOCASE''', (day or today_str(),) * 2)


def _pay_holder(actor, brigadier_id):
    """No hand trailer of this brigade today: a closed, pay-only trip of the day carries the evening talons (no kg of its
    own — the sverka does not count it as a trip). Its field / trailer are the brigade's last ones."""
    from .db import trip_number
    with tx() as db:
        prev = db.execute("""SELECT * FROM trailer_loads WHERE brigadier_id=? AND status<>'BEKOR' AND COALESCE(method,'hand')='hand'
                             ORDER BY id DESC LIMIT 1""", (brigadier_id,)).fetchone()
        field = prev['field_id'] if prev else (db.execute(
            'SELECT id FROM fields WHERE brigadier_id=? ORDER BY active DESC, id LIMIT 1', (brigadier_id,)).fetchone() or [None])[0]
        trailer = prev['trailer_id'] if prev else (db.execute(
            "SELECT id FROM equipment WHERE kind='telashka' ORDER BY active DESC, id LIMIT 1").fetchone() or [None])[0]
        if not field or not trailer:
            raise UserError('Bu brigadaga dala biriktirilmagan — Admin: Dalalar.')
        rate = (prev['rate'] if prev else None) or get_setting('worker_rate_hand') or None
        from .services import current_season
        year = current_season(db)
        ts = now_str()
        lid = db.execute("""INSERT INTO trailer_loads(season_year, load_date, trailer_id, tractor_id, field_id, brigadier_id, status,
                                note, opened_by, opened_at, full_by, full_at, weighed_at, trip_no, station_id, method, rate,
                                manual_kg, pay_only)
                            VALUES (?,?,?,?,?,?,'TORTILDI',?,?,?,?,?,?,?,?,'hand',?,0,1)""",
                         (year, today_str(), trailer, prev['tractor_id'] if prev else None, field, brigadier_id,
                          'Kechki hisob: bugun telashka kiritilmagan — faqat talonlar (pul)', actor.user_id, ts,
                          actor.user_id, ts, ts, trip_number(db, year), prev['station_id'] if prev else None,
                          int(rate) if rate else None)).lastrowid
        audit(db, actor, 'CREATE', 'trailer_load', lid, new={'pay_only': 1, 'brigadier_id': brigadier_id})
    return lid


def evening_lookup(actor, code, manual_code=''):
    _need(actor, 'harvest.write', 'tezpul.count')

    def go():
        db = get_db()
        t = _find(db, code, manual_code)
        _entered_check(db, t)
        return _view(db, t)
    return _logged(actor, code, go)


def enter_evening(actor, brigadier_id, code, kg, *, manual_code='', client_uuid=None, sheets=None, people=None, driver=None):
    """The evening count (hisobchi or kassir, at the kassa): a starshi's talon — the day's kg of his whole team —
    written as that brigade's hand work of the day, to its last trailer of the day even if it is closed (the trailers
    were closed with their own kg; this is the pay). Then the kassir pays it as any talon."""
    _need(actor, 'harvest.write', 'tezpul.count')
    b = next((r for r in evening_brigades() if r['id'] == brigadier_id), None)
    if not b:
        raise UserError('Brigadani tanlang.')

    def go():
        lid = b['last_load'] or _pay_holder(actor, brigadier_id)
        return _enter(actor, lid, code, kg, manual_code, client_uuid, True, sheets, people, late=True, driver=driver)
    return _logged(actor, code, go)


def _enter(actor, load_id, code, kg, manual_code, client_uuid, group=False, sheets=None, people=None, late=False, driver=None):
    from .services import _new_worker, add_harvest
    with tx() as db:
        if client_uuid:
            dup = db.execute('''SELECT t.* FROM tezpul_talons t JOIN harvests h ON h.id=t.harvest_id
                                WHERE h.client_uuid=?''', (client_uuid,)).fetchone()
            if dup:          # the same save sent twice (bad network): already written
                return {'already': True, **_view(db, dup)}
        t = _find(db, code, manual_code)
        _entered_check(db, t)
        kg = _kg(kg)
        load = db.execute('SELECT * FROM trailer_loads WHERE id=?', (load_id,)).fetchone()
        if not load:
            raise UserError('Telashka topilmadi.')
        name = f'Talon {number_text(t["number"])}'
        wid = t['worker_id']
        if wid:
            db.execute('UPDATE workers SET full_name=?, name_key=?, active=1 WHERE id=?', (name, name_key(name), wid))
        else:
            wid = _new_worker(db, actor, name, load['brigadier_id'])
            db.execute('UPDATE workers SET talon_no=? WHERE id=?', (t['number'], wid))
        sheets = _clean_sheets(db, t['id'], sheets)
        try:
            people = int(people) if people not in (None, '') else None
        except (TypeError, ValueError):
            people = None
        if kg > get_float('max_hand_kg', 250, db) or sheets or (people or 0) > 1:
            group = True     # more than one person picks: the starshi's talon — his whole team's kg, names on the paper
        if group:            # the card takes the group limit (max_group_kg) instead of one person's
            db.execute('UPDATE workers SET group_size=1 WHERE id=?', (wid,))
        try:
            hid, _ = add_harvest(actor, load_id=load_id, method='hand', kg=kg, worker_id=wid, client_uuid=client_uuid,
                                 confirm_duplicate=True, late=late)
        except UserError as err:
            if 'chegara' in str(err):
                raise UserError(f'{kg:g} kg — bitta talonga juda ko‘p ({str(err).split("(")[1].split(")")[0]}). '
                                'Talondagi raqamni tekshiring.')
            raise
        h = db.execute('SELECT rate, amount FROM harvests WHERE id=?', (hid,)).fetchone()
        db.execute('''UPDATE tezpul_talons SET status='TAYYOR', kind=?, worker_id=?, harvest_id=?, load_id=?, brigadier_id=?,
                          kg=?, rate=?, amount=?, entered_by=?, entered_at=? WHERE id=?''',
                   ('guruh' if group else 'odam', wid, hid, load_id, load['brigadier_id'], kg, h['rate'], h['amount'],
                    actor.user_id, now_str(), t['id']))
        if people:
            db.execute('UPDATE tezpul_talons SET people=? WHERE id=?', (people, t['id']))
        _set_driver(db, t['id'], driver, sheets, people, ask=bool(sheets))
        for s in sheets:
            db.execute('''INSERT INTO tezpul_sheets(talon_id, sheet, photo_id, kg, rows_json, created_at, created_by)
                          VALUES (?,?,?,?,?,?,?) ON CONFLICT(talon_id, sheet) DO UPDATE SET photo_id=excluded.photo_id,
                          kg=excluded.kg, rows_json=excluded.rows_json, created_at=excluded.created_at, created_by=excluded.created_by''',
                       (t['id'], s['sheet'], s['photo_id'], s['kg'], json.dumps(s['rows']), now_str(), actor.user_id))
        audit(db, actor, 'TEZPUL_KG', 'tezpul_talon', t['id'],
              new={'number': t['number'], 'kg': kg, 'load_id': load_id, 'harvest_id': hid, 'group': bool(group),
                   'people': people, 'sheets': [(s['sheet'], s['kg']) for s in sheets]})
        if sheets or people:          # the starshi's talon goes to the director and the accountant as a PDF
            from .yordamchi import _send
            _send(db, 'tezpul_starshi', f'tezpul:{t["id"]}', '', ('admin', 'manager', 'accountant'),
                  {'tezpul_starshi': {'talon_id': t['id']}})
        return {'already': False, **_view(db, db.execute('SELECT * FROM tezpul_talons WHERE id=?', (t['id'],)).fetchone())}


def attach_photo(actor, code, manual_code, data, sheet):
    """A photo of the starshi's filled talon sheet, kept with the talon as evidence (finance photos: the names stay
    out of the field archive). Taken before the talon's kg is written. Returns the talon's view."""
    _need(actor, 'harvest.write', 'tezpul.count')
    from .photos import store_photo
    if not data:
        raise UserError('Varaqni rasmga oling.')

    def go():
        with tx() as db:
            t = _find(db, code, manual_code)
            _entered_check(db, t)
            pid = store_photo(db, actor, data, category='payment', entity_type='tezpul_talon', entity_id=t['id'],
                              caption=f'Starshi taloni № {number_text(t["number"])} · {sheet}-varaq')
            audit(db, actor, 'TEZPUL_PHOTO', 'tezpul_talon', t['id'], new={'photo_id': pid, 'sheet': sheet})
            return {**_view(db, t), 'photo_id': pid}
    return _logged(actor, code, go)


def starshi_info(talon_id):
    """Everything the starshi talon's PDF shows: the talon, its trip, its sheets (rows and photos)."""
    t = q('''SELECT t.*, b.name brigade, e.full_name clerk, u.full_name cashier, tl.trip_no, f.name field_name, eq.code trailer,
                    d.name driver, d.vehicle driver_vehicle
             FROM tezpul_talons t LEFT JOIN brigadiers b ON b.id=t.brigadier_id LEFT JOIN users e ON e.id=t.entered_by
             LEFT JOIN tezpul_drivers d ON d.id=t.driver_id
             LEFT JOIN users u ON u.id=t.paid_by LEFT JOIN trailer_loads tl ON tl.id=t.load_id
             LEFT JOIN fields f ON f.id=tl.field_id LEFT JOIN equipment eq ON eq.id=tl.trailer_id WHERE t.id=?''',
          (talon_id,), one=True)
    if not t:
        return None
    sheets = [dict(s, rows=json.loads(s['rows_json'] or '[]')) for s in
              q('''SELECT s.*, p.path FROM tezpul_sheets s LEFT JOIN photos p ON p.id=s.photo_id
                   WHERE s.talon_id=? ORDER BY s.sheet''', (talon_id,))]
    return {'talon': dict(t), 'sheets': sheets, 'number': number_text(t['number']),
            'rows': sum(len(s['rows']) for s in sheets)}


def starshi_report(talon_id):
    """(pdf bytes, file name, caption) — the starshi talon's report, built when it is sent or opened (always fresh)."""
    from .pdfdoc import build_tezpul_starshi_report
    from .settings import get_setting
    info = starshi_info(talon_id)
    if not info:
        raise UserError('Talon topilmadi.')
    t = info['talon']
    pdf = build_tezpul_starshi_report(info, company=get_setting('company_name'), upload_dir=current_app.config['SURXON'].UPLOAD_DIR)
    paid = (f'💵 PUL BERILDI: {fmt_money(t["amount"])} · kassir {t["cashier"] or "—"} · {(t["paid_at"] or "")[:16]}\n'
            if t['status'] == 'TOLANDI' else '')
    caption = (paid + f'👥 Starshi taloni № {info["number"]} · {t["brigade"] or ""}\n'
               f'{t["people"] or "—"} odam · {len(info["sheets"])} varaq · {fmt_num(t["kg"] or 0, 1)} kg · {fmt_money(t["amount"])}\n'
               f'Hisobchi: {t["clerk"] or "—"} · {(t["entered_at"] or "")[:16]}')
    return pdf, f'starshi_talon_{info["number"]}.pdf', caption


def trip_counter(load_id):
    r = q('''SELECT COUNT(*) n, COALESCE(SUM(t.kg),0) kg FROM tezpul_talons t JOIN harvests h ON h.id=t.harvest_id
             WHERE t.load_id=? AND h.voided_at IS NULL''', (load_id,), one=True)
    return dict(r)


# ------------------------------------------------------------------ the kassa: scan → PUL BERILDI

def _ready(db, t):
    if t['status'] == 'BOSH' or not _live_harvest(db, t):
        raise Refused('kgsiz', f'№{number_text(t["number"])}: KG HALI YOZILMAGAN — terim hisobchisi talonni skanerlab '
                               'kg ni kiritsin. Pul berilmaydi.', {**_view(db, t), 'id': t['id']})


def lookup(actor, code, manual_code=''):
    """What the cashier sees after the scan."""
    _need(actor, 'tezpul.pay')

    def go():
        db = get_db()
        t = _find(db, code, manual_code)
        _ready(db, t)
        v = _view(db, t)
        if not v['amount']:
            raise Refused('tolangan', f'№{v["number"]} bo‘yicha to‘lanadigan pul qolmagan.', {**v, 'id': t['id']})
        return {**v, **_detail(db, t)}
    return _logged(actor, code, go)


def _detail(db, t):
    """A starshi talon on the kassa: every row of its sheets (kg before / after lunch, total kg and its money) and the
    sheet photos — the cashier sees the whole sheet before paying. A one-person talon: nothing extra."""
    if (t['kind'] or 'odam') != 'guruh':
        return {}
    from flask import url_for
    rate = float(t['rate'] or 0)
    sheets = []
    for sh in db.execute('''SELECT s.sheet, s.kg, s.rows_json, p.path FROM tezpul_sheets s
                            LEFT JOIN photos p ON p.id=s.photo_id AND p.voided_at IS NULL
                            WHERE s.talon_id=? ORDER BY s.sheet''', (t['id'],)).fetchall():
        rows = [{**r, 'amount': round((r.get('total') or 0) * rate)} for r in json.loads(sh['rows_json'] or '[]')]
        sheets.append({'sheet': sh['sheet'], 'kg': sh['kg'], 'amount': round((sh['kg'] or 0) * rate), 'rows': rows,
                       'photo': url_for('main.media', path=sh['path']) if sh['path'] else None})
    return {'people': t['people'], 'sheets': sheets}


def pay(actor, codes, *, manual_codes=None, client_uuid=None):
    """“PUL BERILDI” for one talon or many at once (a starshi brings his team's 50–60 talons): every talon gets the
    whole amount owed on it, all in one transaction — all are paid or none. Same tap sent twice = paid once."""
    _need(actor, 'tezpul.pay')
    codes = [codes] if isinstance(codes, str) else list(codes or [])
    manual_codes = list(manual_codes or []) + [''] * len(codes)
    if not codes:
        raise UserError('Talonni skanerlang.')
    if len(codes) > 300:
        raise UserError('Bir to‘lovda 300 tadan ko‘p talon bo‘lmaydi.')
    return _logged(actor, codes[0] if len(codes) == 1 else f'{len(codes)} talon',
                   lambda: _pay(actor, codes, manual_codes, client_uuid))


def _pay(actor, codes, manual_codes, client_uuid):
    from . import accounting as A
    from .db import doc_number
    from .wallet import worker_figures
    with tx() as db:
        if client_uuid:
            done_rows = db.execute('SELECT * FROM tezpul_talons WHERE pay_batch=? ORDER BY number', (client_uuid,)).fetchall()
            if done_rows:          # the same tap sent twice (bad network): the first one already did it
                return _paid_result(db, done_rows, already=True)
        year = A.season_of(db)
        box = A._cashbox(db, actor, None)
        items, seen = [], set()
        for code, manual in zip(codes, manual_codes):
            t = _find(db, code, manual)
            if t['id'] in seen:
                continue
            seen.add(t['id'])
            _ready(db, t)
            f = worker_figures(db, year, t['worker_id'])
            if f['payable'] <= 0:
                if f['uncalc_kg']:
                    raise UserError(f'№{number_text(t["number"])}: telashkada qo‘l terimi narxi yozilmagan — summa hisoblanmagan. '
                                    'Buxgalterga ayting.')
                raise Refused('tolangan', f'№{number_text(t["number"])} bo‘yicha to‘lanadigan pul qolmagan.',
                              {'id': t['id'], 'number': number_text(t['number'])})
            items.append((t, f))
        total = sum(f['payable'] for _, f in items)
        bal = A.box_balance(db, box)
        if total > bal:
            raise UserError(f'Kassada yetarli pul yo‘q: qoldiq {A.fmt_som(bal)}, berilmoqchi {A.fmt_som(total)}.')
        ts, batch = now_str(), client_uuid or secrets.token_hex(8)
        for k, (t, f) in enumerate(items):
            amount = f['payable']
            puid = batch if len(items) == 1 else f'{batch}#{k + 1}'
            # the guard: only one transaction can move this talon to TOLANDI
            n = db.execute('''UPDATE tezpul_talons SET status='TOLANDI', amount=?, paid_by=?, paid_at=?, pay_date=?, cashbox_id=?,
                                  pay_uuid=?, pay_batch=? WHERE id=? AND status='TAYYOR' ''',
                           (amount, actor.user_id, ts, today_str(), box, puid, batch, t['id'])).rowcount
            if n != 1:
                raise Refused('tolangan', f'№{number_text(t["number"])} ALLAQACHON TO‘LANGAN.',
                              {'id': t['id'], 'number': number_text(t['number'])})
            no = doc_number(db, 'PAY', year)
            note = f'TEZ-PUL talon №{number_text(t["number"])} · {t["kg"]:g} kg' + (f' · {len(items)} talon birga' if len(items) > 1 else '')
            pid = db.execute('''INSERT INTO payouts(doc_no, season_year, kind, purpose, worker_id, amount, note, client_uuid,
                                    cashbox_id, prepared_by, prepared_at, status, paid_by, paid_at)
                                VALUES (?,?,'worker','pay',?,?,?,?,?,?,?,'BERILDI',?,?)''',
                             (no, year, t['worker_id'], amount, note, puid, box, actor.user_id, ts, actor.user_id, ts)).lastrowid
            cid, _ = A.book_cash(db, actor, direction='OUT', category='worker_pay', amount=amount, cashbox_id=box,
                                 worker_id=t['worker_id'], payout_id=pid, counterparty=f['name'], note=note, doc_no=no, feed=False)
            db.execute('UPDATE payouts SET cash_entry_id=? WHERE id=?', (cid, pid))
            db.execute('UPDATE tezpul_talons SET cash_entry_id=? WHERE id=?', (cid, t['id']))
            A.mirror_payout(db, pid)
            A.mirror_worker(db, t['worker_id'], year)
            if (t['kind'] or 'odam') == 'guruh':   # a starshi's talon: “PUL BERILDI” with its PDF to the director
                from .yordamchi import _send
                _send(db, 'tezpul_paid', f'tezpul-paid:{t["id"]}', '', ('admin', 'manager', 'accountant'),
                      {'tezpul_starshi': {'talon_id': t['id']}})
        audit(db, actor, 'TEZPUL_PAID', 'tezpul_talons', None,
              new={'batch': batch, 'talons': [t['number'] for t, _ in items], 'amount': total, 'cashbox_id': box})
        rows = db.execute('SELECT * FROM tezpul_talons WHERE pay_batch=? ORDER BY number', (batch,)).fetchall()
        return _paid_result(db, rows, already=False)


def _paid_result(db, rows, already):
    return {**_view(db, rows[0]), 'paid': True, 'already': already, 'count': len(rows),
            'kg': sum(r['kg'] or 0 for r in rows), 'amount': sum(r['amount'] or 0 for r in rows),
            'numbers': [number_text(r['number']) for r in rows]}


# ------------------------------------------------------------------ drivers: paid per person brought

def driver_rate(db=None):
    return get_float('driver_rate_person', 10000, db)


def set_driver_rate(actor, value):
    """The rate per person brought (setting driver_rate_person); talons already counted keep their own rate."""
    _need(actor, 'tezpul.manage')
    try:
        rate = round(float(str(value or '').replace(' ', '').replace(',', '.')))
    except ValueError:
        raise UserError('Narxni raqam bilan yozing, masalan 10000.')
    if not 0 <= rate <= 1_000_000:
        raise UserError('Narx 0 dan 1 000 000 so‘mgacha bo‘lsin.')
    with tx() as db:
        old = get_float('driver_rate_person', 10000, db)
        db.execute('INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) ON CONFLICT(key) '
                   'DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by',
                   ('driver_rate_person', str(rate), now_str(), actor.user_id))
        audit(db, actor, 'UPDATE', 'setting', 'driver_rate_person', old={'driver_rate_person': old},
              new={'driver_rate_person': rate})
    return rate


def drivers(active=True):
    return q('SELECT * FROM tezpul_drivers' + (' WHERE active=1' if active else '') + ' ORDER BY active DESC, name')


def _set_driver(db, talon_id, driver, sheets, people, ask):
    """Who brought the starshi's people. He is paid for the people who have kg on the talon's sheets — not for the
    number anybody says (the starshi's head count only when no row was read). '' = not chosen, '0' = nobody."""
    if driver is None:            # not asked on this screen (an old page, a one-person talon)
        return
    if driver == '':
        if ask and db.execute('SELECT 1 FROM tezpul_drivers WHERE active=1').fetchone():
            raise UserError('Odamlarni qaysi shofyor olib keldi? Tanlang (yoki “Shofyorsiz”).')
        return
    try:
        did = int(driver)
    except (TypeError, ValueError):
        raise UserError('Shofyor tanlanmadi.')
    if not did:
        return
    d = db.execute('SELECT * FROM tezpul_drivers WHERE id=? AND active=1', (did,)).fetchone()
    if not d:
        raise UserError('Bu shofyor ro‘yxatda yo‘q.')
    rows = sum(1 for s in sheets or [] for r in s['rows'] if (r.get('total') or 0) > 0)
    n = rows or int(people or 0)
    if not n:
        raise UserError('Shofyor puli uchun odam soni kerak — varaqni rasmga oling yoki odam sonini yozing.')
    rate = driver_rate(db)
    db.execute('UPDATE tezpul_talons SET driver_id=?, driver_people=?, driver_rate=?, driver_amount=? WHERE id=?',
               (did, n, rate, round(n * rate), talon_id))


def save_driver(actor, *, name, vehicle='', phone='', driver_id=None, active=True):
    _need(actor, 'tezpul.manage', 'tezpul.panel')
    from .utils import clean_text
    name, vehicle, phone = clean_text(name, 60), clean_text(vehicle, 40), clean_text(phone, 30)
    if not name:
        raise UserError('Shofyorning ismini yozing.')
    with tx() as db:
        if driver_id:
            old = db.execute('SELECT * FROM tezpul_drivers WHERE id=?', (driver_id,)).fetchone()
            if not old:
                raise UserError('Shofyor topilmadi.')
            db.execute('UPDATE tezpul_drivers SET name=?, vehicle=?, phone=?, active=? WHERE id=?',
                       (name, vehicle, phone, 1 if active else 0, driver_id))
            audit(db, actor, 'UPDATE', 'tezpul_driver', driver_id, old=dict(old),
                  new={'name': name, 'vehicle': vehicle, 'phone': phone, 'active': bool(active)})
            return driver_id
        did = db.execute('INSERT INTO tezpul_drivers(name, vehicle, phone, created_by, created_at) VALUES (?,?,?,?,?)',
                         (name, vehicle, phone, actor.user_id, now_str())).lastrowid
        audit(db, actor, 'CREATE', 'tezpul_driver', did, new={'name': name, 'vehicle': vehicle, 'phone': phone})
        return did


def drivers_due():
    """Every driver still owed: the starshi talons he brought people for (kg written, not cancelled), not yet paid."""
    return [dict(r) for r in q(f'''SELECT d.id, d.name, d.vehicle, COUNT(*) talons, SUM(t.driver_people) people,
                                       SUM(t.driver_amount) amount, MIN(substr(t.entered_at,1,10)) since
                                FROM tezpul_talons t JOIN tezpul_drivers d ON d.id=t.driver_id
                                WHERE t.driver_pay_id IS NULL AND t.status IN ('TAYYOR','TOLANDI') AND {LIVE}
                                GROUP BY d.id ORDER BY amount DESC''')]


def pay_driver(actor, driver_id, *, client_uuid=None):
    """PUL BERILDI to a driver: everything he is owed, in one cash entry (Shofyor — odam tashish). Same tap twice = once."""
    _need(actor, 'payouts.pay')
    from . import accounting as A
    with tx() as db:
        if client_uuid:
            done = db.execute('SELECT * FROM tezpul_driver_pays WHERE client_uuid=?', (client_uuid,)).fetchone()
            if done:
                return {'already': True, **dict(done)}
        d = db.execute('SELECT * FROM tezpul_drivers WHERE id=?', (driver_id,)).fetchone()
        if not d:
            raise UserError('Shofyor topilmadi.')
        rows = db.execute(f'''SELECT t.* FROM tezpul_talons t WHERE t.driver_id=? AND t.driver_pay_id IS NULL
                              AND t.status IN ('TAYYOR','TOLANDI') AND {LIVE}''', (driver_id,)).fetchall()
        if not rows:
            raise UserError(f'{d["name"]} ga to‘lanadigan pul qolmagan.')
        amount = sum(r['driver_amount'] or 0 for r in rows)
        people = sum(r['driver_people'] or 0 for r in rows)
        box = A._cashbox(db, actor, None)
        bal = A.box_balance(db, box)
        if amount > bal:
            raise UserError(f'Kassada yetarli pul yo‘q: qoldiq {A.fmt_som(bal)}, berilmoqchi {A.fmt_som(amount)}.')
        nums = ', '.join(number_text(r['number']) for r in rows)
        note = f'Shofyor {d["name"]}{" · " + d["vehicle"] if d["vehicle"] else ""} · {people} odam · talonlar {nums}'
        cid, doc = A.book_cash(db, actor, direction='OUT', category='driver_pay', amount=amount, cashbox_id=box,
                               counterparty=d['name'], note=note, feed=False)
        ts = now_str()
        pid = db.execute('''INSERT INTO tezpul_driver_pays(driver_id, amount, people, talons, cashbox_id, cash_entry_id,
                                client_uuid, paid_by, paid_at) VALUES (?,?,?,?,?,?,?,?,?)''',
                         (driver_id, amount, people, len(rows), box, cid, client_uuid, actor.user_id, ts)).lastrowid
        n = db.execute(f'''UPDATE tezpul_talons SET driver_pay_id=? WHERE driver_id=? AND driver_pay_id IS NULL
                           AND id IN ({",".join("?" * len(rows))})''', (pid, driver_id, *[r['id'] for r in rows])).rowcount
        if n != len(rows):
            raise UserError('Shu payt boshqa kassir ham to‘ladi — qaytadan oching.')
        audit(db, actor, 'DRIVER_PAID', 'tezpul_driver', driver_id,
              new={'pay_id': pid, 'amount': amount, 'people': people, 'talons': [r['number'] for r in rows], 'doc_no': doc})
        from .yordamchi import _send
        who = db.execute('SELECT full_name FROM users WHERE id=?', (actor.user_id,)).fetchone()
        rates = sorted({r['driver_rate'] for r in rows})
        _send(db, 'tezpul_driver', f'driver-pay:{pid}',
              f'🚐 Shofyorga pul berildi: {d["name"]}{" (" + d["vehicle"] + ")" if d["vehicle"] else ""}\n'
              + (f'{people} odam × {fmt_money(rates[0])} = {fmt_money(amount)}\n' if len(rates) == 1
                 else f'{people} odam = {fmt_money(amount)}\n')
              + f'Starshi talonlari: {nums}\nKassir: {who["full_name"] if who else "—"} · {ts[:16]}',
              ('admin', 'manager', 'accountant'))
        return {'already': False, 'id': pid, 'name': d['name'], 'amount': amount, 'people': people, 'talons': len(rows),
                'doc_no': doc}


# ------------------------------------------------------------------ the rahbar

LIVE = "t.harvest_id IS NOT NULL AND EXISTS (SELECT 1 FROM harvests h WHERE h.id=t.harvest_id AND h.voided_at IS NULL)"


def panel(day=None):
    """The rahbar's figures for one day (default today) + the whole season."""
    day = day or today_str()
    db = get_db()
    entered = db.execute(f'''SELECT COUNT(*) n, COALESCE(SUM(t.kg),0) kg, COALESCE(SUM(t.amount),0) amount FROM tezpul_talons t
                             WHERE substr(t.entered_at,1,10)=? AND {LIVE}''', (day,)).fetchone()
    paid = db.execute('''SELECT COUNT(*) n, COALESCE(SUM(kg),0) kg, COALESCE(SUM(amount),0) amount FROM tezpul_talons
                         WHERE status='TOLANDI' AND pay_date=?''', (day,)).fetchone()
    unpaid = db.execute(f'''SELECT COUNT(*) n, COALESCE(SUM(t.kg),0) kg, COALESCE(SUM(t.amount),0) amount FROM tezpul_talons t
                            WHERE t.status='TAYYOR' AND {LIVE}''').fetchone()
    season = db.execute('''SELECT COUNT(*) n, COALESCE(SUM(kg),0) kg, COALESCE(SUM(amount),0) amount FROM tezpul_talons
                           WHERE status='TOLANDI' ''').fetchone()
    counts = {r['status']: r['n'] for r in db.execute('SELECT status, COUNT(*) n FROM tezpul_talons GROUP BY status')}
    punkt = {r['b']: r['kg'] for r in db.execute(
        '''SELECT tl.brigadier_id b, SUM(nr.accepted_kg) kg FROM nayman_receipts nr JOIN waybills wb ON wb.id=nr.waybill_id
           JOIN trailer_loads tl ON tl.id=wb.load_id
           WHERE wb.status='QABUL' AND tl.method='hand' AND nr.received_date=? GROUP BY tl.brigadier_id''', (day,))}
    # the brigade's hand trailers closed that day — their own kg (dala), the third number next to talons and punkt
    trailers = {r['b']: r['kg'] for r in db.execute(
        '''SELECT brigadier_id b, SUM(internal_kg) kg FROM trailer_loads WHERE COALESCE(method,'hand')='hand'
           AND status IN ('TOLDI','TORTILDI') AND substr(opened_at,1,10)=? GROUP BY brigadier_id''', (day,))}
    brigades = [dict(r, punkt_kg=punkt.get(r['bid']), trailer_kg=trailers.get(r['bid'])) for r in db.execute(
        f'''SELECT t.brigadier_id bid, COALESCE(b.name, 'Brigadasiz') name, COUNT(*) n, SUM(t.kg) kg, SUM(t.amount) amount,
                   SUM(CASE WHEN t.status='TOLANDI' THEN t.amount ELSE 0 END) paid
            FROM tezpul_talons t LEFT JOIN brigadiers b ON b.id=t.brigadier_id
            WHERE substr(t.entered_at,1,10)=? AND {LIVE} GROUP BY t.brigadier_id ORDER BY kg DESC''', (day,))]
    from .accounting import box_balance
    cashiers = [dict(r, balance=box_balance(db, r['cashbox_id'])) for r in db.execute(
        '''SELECT t.paid_by, u.full_name name, t.cashbox_id, cb.name box, COUNT(*) n, SUM(t.kg) kg, SUM(t.amount) amount,
                  MAX(t.paid_at) last_at
           FROM tezpul_talons t LEFT JOIN users u ON u.id=t.paid_by LEFT JOIN cashboxes cb ON cb.id=t.cashbox_id
           WHERE t.status='TOLANDI' AND t.pay_date=? GROUP BY t.paid_by, t.cashbox_id ORDER BY amount DESC''', (day,))]
    clerks = db.execute(f'''SELECT u.full_name name, COUNT(*) n, SUM(t.kg) kg FROM tezpul_talons t
                            LEFT JOIN users u ON u.id=t.entered_by
                            WHERE substr(t.entered_at,1,10)=? AND {LIVE} GROUP BY t.entered_by ORDER BY n DESC''', (day,)).fetchall()
    errors = db.execute('''SELECT e.*, u.full_name name FROM tezpul_events e LEFT JOIN users u ON u.id=e.user_id
                           WHERE substr(e.created_at,1,10)=? ORDER BY e.id DESC LIMIT 50''', (day,)).fetchall()
    recent = db.execute('''SELECT MIN(t.number) a, MAX(t.number) b, COUNT(*) n, SUM(t.kg) kg, SUM(t.amount) amount, MAX(t.paid_at) paid_at,
                                  MAX(b.name) brigade, MAX(u.full_name) paid_name
                           FROM tezpul_talons t LEFT JOIN brigadiers b ON b.id=t.brigadier_id LEFT JOIN users u ON u.id=t.paid_by
                           WHERE t.status='TOLANDI' AND t.pay_date=? GROUP BY COALESCE(t.pay_batch, t.id)
                           ORDER BY paid_at DESC LIMIT 30''', (day,)).fetchall()
    drivers_day = [dict(r) for r in db.execute(f'''SELECT d.name, d.vehicle, COUNT(*) talons, SUM(t.driver_people) people,
                                   SUM(t.driver_amount) amount,
                                   SUM(CASE WHEN t.driver_pay_id IS NOT NULL THEN t.driver_amount ELSE 0 END) paid,
                                   SUM(t.people) said
                            FROM tezpul_talons t JOIN tezpul_drivers d ON d.id=t.driver_id
                            WHERE substr(t.entered_at,1,10)=? AND {LIVE} GROUP BY d.id ORDER BY amount DESC''', (day,))]
    # one person both wrote the kg and paid the money (an o‘rinbosar day) — shown in red for the rahbar to check
    self_paid = db.execute('''SELECT u.full_name name, COUNT(*) n, SUM(t.kg) kg, SUM(t.amount) amount,
                                     GROUP_CONCAT(t.number, ',') numbers, MAX(COALESCE(u.pays_cash, 0)) allowed
                              FROM tezpul_talons t LEFT JOIN users u ON u.id=t.paid_by
                              WHERE t.status='TOLANDI' AND t.pay_date=? AND t.paid_by=t.entered_by
                              GROUP BY t.paid_by ORDER BY n DESC''', (day,)).fetchall()
    # the starshi talons' sheets photographed today — the evidence behind each total kg
    photos = {}
    for r in db.execute('''SELECT t.number, t.kg, t.status, p.path, p.thumb_path, p.caption FROM photos p
                           JOIN tezpul_talons t ON t.id=p.entity_id
                           WHERE p.entity_type='tezpul_talon' AND p.voided_at IS NULL AND substr(p.uploaded_at,1,10)=?
                           ORDER BY t.number, p.id''', (day,)).fetchall():
        photos.setdefault(r['number'], {'number': r['number'], 'kg': r['kg'], 'status': r['status'], 'sheets': []})['sheets'].append(dict(r))
    return {'day': day, 'entered': dict(entered), 'paid': dict(paid), 'unpaid': dict(unpaid), 'season': dict(season),
            'photos': list(photos.values()),
            'counts': counts, 'brigades': brigades, 'cashiers': cashiers, 'clerks': clerks, 'errors': errors,
            'self_paid': self_paid, 'drivers': drivers_day, 'clerk_cash': _clerk_cash(day),
            'recent': recent, 'issued': sum(counts.values()), 'blank': counts.get('BOSH', 0),
            'legacy': counts.get('KUTILMOQDA', 0)}


def _clerk_cash(day):
    from .hkassa import overview
    return overview(day)


def today_counter(actor):
    """The small line under the cashier's camera: how many talons this cashier paid today."""
    r = q('''SELECT COUNT(*) n, COALESCE(SUM(kg),0) kg, COALESCE(SUM(amount),0) amount FROM tezpul_talons
             WHERE status='TOLANDI' AND pay_date=? AND paid_by=?''', (today_str(), actor.user_id), one=True)
    return dict(r)


def talons_for_print(batch=None, first=None, last=None):
    if batch:
        return q('SELECT * FROM tezpul_talons WHERE batch=? ORDER BY number', (batch,))
    return q('SELECT * FROM tezpul_talons WHERE number BETWEEN ? AND ? ORDER BY number', (_num(first) or 0, _num(last) or 0))


def batches():
    return q('''SELECT t.batch, MIN(t.number) a, MAX(t.number) b, COUNT(*) n, MIN(t.created_at) created_at,
                       SUM(t.status='TOLANDI') paid, SUM(t.status='BEKOR') void, SUM(t.status='BOSH') blank
                FROM tezpul_talons t GROUP BY t.batch ORDER BY t.batch DESC''')
