"""Business operations. Every function here runs inside a single transaction,
writes its own audit record, and validates everything it is given.

Web views and the Telegram bot both call these, so the rules are identical
whichever channel the operator uses.
"""
import json
import secrets
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from .db import get_db, next_counter, trip_number, tx
from .outbox import enqueue
from .photos import store_photo
from .security import ROLES, audit
from .settings import get_bool, get_float, get_setting
from .utils import UserError, clean_text, name_key, now_str, today_str

WAYBILL_PREFIX = 'PA-'


def row_dict(row):
    return dict(row) if row is not None else None


# ------------------------------------------------------------------ seasons

def current_season(db):
    raw = (get_setting('current_season', db) or '').strip()
    year = int(raw) if raw.isdigit() else int(today_str()[:4])
    ensure_season(db, year)
    return year


def ensure_season(db, year):
    db.execute('INSERT OR IGNORE INTO seasons(year, status) VALUES (?, ?)', (year, 'OCHIQ'))


def assert_season_open(db, year):
    ensure_season(db, year)
    row = db.execute('SELECT status FROM seasons WHERE year=?', (year,)).fetchone()
    if row['status'] != 'OCHIQ':
        raise UserError(f'{year}-mavsum yopilgan (arxiv). O‘zgartirish uchun Admin mavsumni qayta ochishi kerak.')


def close_season(actor, year, reason):
    _need(actor, 'seasons.manage')
    with tx() as db:
        ensure_season(db, year)
        open_loads = db.execute("SELECT COUNT(*) FROM trailer_loads WHERE season_year=? AND status IN ('OCHIQ','TOLDI')",
                                (year,)).fetchone()[0]
        if open_loads:
            raise UserError(f'Mavsumni yopib bo‘lmaydi: {open_loads} ta telashka hali tortilmagan.')
        db.execute('DELETE FROM field_seasons WHERE year=?', (year,))
        db.execute('INSERT INTO field_seasons(year, field_id, area_ha, brigadier_id) '
                   'SELECT ?, id, area_ha, brigadier_id FROM fields WHERE active=1', (year,))
        db.execute("UPDATE seasons SET status='YOPILGAN', closed_at=?, closed_by=? WHERE year=?",
                   (now_str(), actor.user_id, year))
        audit(db, actor, 'SEASON_CLOSE', 'season', year, new={'status': 'YOPILGAN'}, reason=reason)


def reopen_season(actor, year, reason):
    _need(actor, 'seasons.manage')
    if not reason:
        raise UserError('Mavsumni qayta ochish sababi majburiy.')
    with tx() as db:
        db.execute("UPDATE seasons SET status='OCHIQ', closed_at=NULL, closed_by=NULL WHERE year=?", (year,))
        audit(db, actor, 'SEASON_REOPEN', 'season', year, new={'status': 'OCHIQ'}, reason=reason)


# ------------------------------------------------------------------ helpers

def _equipment(db, eid, kind, label):
    if not eid:
        return None
    row = db.execute('SELECT * FROM equipment WHERE id=?', (eid,)).fetchone()
    if not row or row['kind'] != kind or not row['active']:
        raise UserError(f'{label} topilmadi yoki faol emas.')
    return row


def _field(db, fid):
    row = db.execute('SELECT * FROM fields WHERE id=? AND active=1', (fid,)).fetchone()
    if not row:
        raise UserError('Dala topilmadi yoki faol emas.')
    return row


def _station(db, sid):
    """The chosen receiving point, or the first active one (None only if no punkt exists yet)."""
    if sid:
        row = db.execute('SELECT * FROM stations WHERE id=? AND active=1', (sid,)).fetchone()
        if not row:
            raise UserError('Punkt topilmadi yoki faol emas.')
        return row
    return db.execute('SELECT * FROM stations WHERE active=1 ORDER BY id LIMIT 1').fetchone()


def _brigadier(db, bid):
    row = db.execute('SELECT * FROM brigadiers WHERE id=? AND active=1', (bid,)).fetchone()
    if not row:
        raise UserError('Brigadir topilmadi yoki faol emas.')
    return row


def _need(actor, *perms):
    """Role check inside the service layer, so web and Telegram enforce the same rules."""
    if actor is None or not any(actor.can(p) for p in perms):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')


def _check_scope(actor, brigadier_id):
    if actor.brigadier_id and brigadier_id and int(brigadier_id) != int(actor.brigadier_id):
        raise UserError('Siz faqat o‘z brigadangiz uchun yozuv kirita olasiz.')


def _require_reason(reason, what='O‘zgartirish'):
    reason = clean_text(reason, 300)
    if len(reason) < 3:
        raise UserError(f'{what} sababi majburiy (kamida 3 harf).')
    return reason


def load_row(db, load_id):
    row = db.execute('SELECT * FROM trailer_loads WHERE id=?', (load_id,)).fetchone()
    if not row:
        raise UserError('Telashka yuki topilmadi.')
    return row


# ------------------------------------------------------------------ workers

def find_or_create_worker(db, actor, full_name, phone='', brigadier_id=None, allow_create=True):
    full_name = clean_text(full_name, 120)
    if len(full_name) < 2:
        raise UserError('Ishchi F.I.Sh. kiritilishi shart.')
    key = name_key(full_name)
    row = db.execute('SELECT * FROM workers WHERE name_key=? AND active=1 ORDER BY id LIMIT 1', (key,)).fetchone()
    if row:
        return row['id'], False
    if not allow_create:
        raise UserError(f'“{full_name}” ishchisi topilmadi.')
    cur = db.execute('INSERT INTO workers(full_name, name_key, phone, brigadier_id, created_by, created_at) VALUES (?,?,?,?,?,?)',
                     (full_name, key, clean_text(phone, 30), brigadier_id or actor.brigadier_id, actor.user_id, now_str()))
    audit(db, actor, 'CREATE', 'worker', cur.lastrowid, new={'full_name': full_name, 'phone': phone})
    return cur.lastrowid, True


def _new_worker(db, actor, full_name, brigadier_id=None):
    full_name = clean_text(full_name, 120)
    if len(full_name) < 2:
        raise UserError('Ishchi F.I.Sh. kiritilishi shart.')
    cur = db.execute('INSERT INTO workers(full_name, name_key, brigadier_id, created_by, created_at) VALUES (?,?,?,?,?)',
                     (full_name, name_key(full_name), brigadier_id or actor.brigadier_id, actor.user_id, now_str()))
    audit(db, actor, 'CREATE', 'worker', cur.lastrowid, new={'full_name': full_name, 'same_name_allowed': True})
    return cur.lastrowid


def create_worker(actor, full_name, phone='', brigadier_id=None, photo=None):
    _need(actor, 'workers.write')
    with tx() as db:
        key = name_key(full_name)
        if db.execute('SELECT 1 FROM workers WHERE name_key=? AND active=1', (key,)).fetchone():
            raise UserError(f'“{full_name}” nomli ishchi allaqachon bor. Ro‘yxatdan tanlang (ikki marta yozilmasin).')
        wid, _ = find_or_create_worker(db, actor, full_name, phone, brigadier_id)
        if photo:
            pid = store_photo(db, actor, photo, category='worker', entity_type='worker', entity_id=wid,
                              caption=full_name, links={'brigadier_id': brigadier_id})
            db.execute('UPDATE workers SET photo_id=? WHERE id=?', (pid, wid))
        return wid


def update_worker(actor, worker_id, full_name, phone, active, photo=None):
    _need(actor, 'workers.write')
    with tx() as db:
        old = db.execute('SELECT * FROM workers WHERE id=?', (worker_id,)).fetchone()
        if not old:
            raise UserError('Ishchi topilmadi.')
        full_name = clean_text(full_name, 120)
        if len(full_name) < 2:
            raise UserError('F.I.Sh. kiritilishi shart.')
        key = name_key(full_name)
        clash = db.execute('SELECT id FROM workers WHERE name_key=? AND active=1 AND id<>?', (key, worker_id)).fetchone()
        if clash and active:
            raise UserError('Shu nomli boshqa faol ishchi bor.')
        db.execute('UPDATE workers SET full_name=?, name_key=?, phone=?, active=? WHERE id=?',
                   (full_name, key, clean_text(phone, 30), 1 if active else 0, worker_id))
        if photo:
            pid = store_photo(db, actor, photo, category='worker', entity_type='worker', entity_id=worker_id, caption=full_name)
            db.execute('UPDATE workers SET photo_id=? WHERE id=?', (pid, worker_id))
        audit(db, actor, 'UPDATE', 'worker', worker_id, old=row_dict(old),
              new={'full_name': full_name, 'phone': phone, 'active': bool(active)})


# ------------------------------------------------------------------ trailer loads

def open_load(actor, *, trailer_id, field_id, brigadier_id, tractor_id=None, vehicle_plate='', driver_name='',
              note='', client_uuid=None, load_date=None, station_id=None, method=None, rate=None):
    """method + rate (so‘m/kg) are set once for the whole trip: every weighing is priced at that rate and the rate
    becomes the default for the next trip. A later rate never changes this trip or earlier ones."""
    _need(actor, 'load.open')
    if method is not None:
        if method not in ('hand', 'combine'):
            raise UserError('Terim turini tanlang: qo‘l terimi yoki kombayn.')
        try:
            rate = int(round(float(str(rate).replace(' ', '').replace(',', '.'))))
        except (TypeError, ValueError):
            raise UserError('Terim narxini kiriting (so‘m/kg).')
        if rate <= 0 or rate > 100000:
            raise UserError('Terim narxi so‘m/kg da bo‘lishi kerak (masalan 1500).')
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT id FROM trailer_loads WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return dup['id']
        season = current_season(db)
        assert_season_open(db, season)
        trailer = _equipment(db, trailer_id, 'telashka', 'Telashka')
        _equipment(db, tractor_id, 'traktor', 'Traktor')
        field = _field(db, field_id)
        brigadier_id = brigadier_id or field['brigadier_id']
        if not brigadier_id:
            raise UserError('Brigadir tanlanishi shart.')
        _brigadier(db, brigadier_id)
        _check_scope(actor, brigadier_id)
        if field['brigadier_id'] and int(field['brigadier_id']) != int(brigadier_id):
            raise UserError('Bu dala boshqa brigadirga biriktirilgan.')
        busy = db.execute("SELECT id, status FROM trailer_loads WHERE trailer_id=? AND status IN ('OCHIQ','TOLDI')",
                          (trailer_id,)).fetchone()
        if busy:
            raise UserError(f'{trailer["code"]} telashkada tugallanmagan yuk bor (№{busy["id"]}, {busy["status"]}). '
                            'Avval u tortilishi kerak.')
        station = _station(db, station_id)
        trip_no = trip_number(db, season)          # server-side, inside this transaction: never duplicated
        cur = db.execute(
            '''INSERT INTO trailer_loads(season_year, load_date, trailer_id, tractor_id, field_id, brigadier_id,
                   vehicle_plate, driver_name, status, note, client_uuid, opened_by, opened_at, trip_no, station_id)
               VALUES (?,?,?,?,?,?,?,?, 'OCHIQ', ?,?,?,?,?,?)''',
            (season, load_date or today_str(), trailer_id, tractor_id or None, field_id, brigadier_id,
             clean_text(vehicle_plate, 30), clean_text(driver_name, 80), clean_text(note), client_uuid,
             actor.user_id, now_str(), trip_no, station['id'] if station else None))
        if method is not None:
            db.execute("UPDATE trailer_loads SET method=?, rate=?, rate_unit='kg' WHERE id=?", (method, rate, cur.lastrowid))
            # the last rate used is offered on the next trip (no retyping); it never touches existing records
            key = 'worker_rate_hand' if method == 'hand' else 'combine_rate_kg'
            db.execute('INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE '
                       'SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by',
                       (key, str(rate), now_str(), actor.user_id))
        audit(db, actor, 'OPEN', 'trailer_load', cur.lastrowid,
              new={'trip_no': trip_no, 'trailer': trailer['code'], 'field_id': field_id, 'brigadier_id': brigadier_id,
                   'tractor_id': tractor_id, 'station': station['name'] if station else None,
                   'method': method, 'rate': rate, 'rate_unit': 'so‘m/kg' if method else None})
        return cur.lastrowid


def add_harvest(actor, *, load_id, method, kg, worker_id=None, worker_name=None, combine_id=None, note='',
                client_uuid=None, source='web', confirm_duplicate=False, new_worker=False):
    """Record one weighing next to the trailer. Returns (harvest_id, info dict)."""
    _need(actor, 'harvest.write')
    if method not in ('hand', 'combine'):
        raise UserError('Terim turi noto‘g‘ri.')
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT id FROM harvests WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return dup['id'], {'duplicate': True}
        load = load_row(db, load_id)
        if load['status'] != 'OCHIQ':
            raise UserError('Bu telashka yopilgan (TOLDI yoki tortilgan). Yangi yuk oching.')
        assert_season_open(db, load['season_year'])
        _check_scope(actor, load['brigadier_id'])
        max_kg = get_float('max_hand_kg' if method == 'hand' else 'max_combine_kg', 250 if method == 'hand' else 15000, db)
        if kg is None or kg <= 0:
            raise UserError('Kg 0 dan katta bo‘lishi kerak.')
        if kg > max_kg:
            raise UserError(f'{kg:g} kg juda katta (chegara {max_kg:g} kg). Raqamni tekshiring.')
        created_worker = False
        if method == 'hand':
            if not worker_id and worker_name and new_worker:
                # "+ Yangi odam": a different person, even if someone with the same name already exists
                worker_id, created_worker = _new_worker(db, actor, worker_name, load['brigadier_id']), True
            elif not worker_id and worker_name:
                worker_id, created_worker = find_or_create_worker(db, actor, worker_name, brigadier_id=load['brigadier_id'])
            if not worker_id:
                raise UserError('Ishchi tanlanishi shart.')
            w = db.execute('SELECT * FROM workers WHERE id=?', (worker_id,)).fetchone()
            if not w or not w['active']:
                raise UserError('Ishchi topilmadi yoki faol emas.')
            combine_id = None
        else:
            _equipment(db, combine_id, 'kombayn', 'Kombayn')
            if not combine_id:
                raise UserError('Kombayn tanlanishi shart.')
            if worker_id:
                if not db.execute('SELECT 1 FROM workers WHERE id=? AND active=1', (worker_id,)).fetchone():
                    raise UserError('Ishchi topilmadi.')
        if not confirm_duplicate:
            recent = db.execute(
                '''SELECT id FROM harvests WHERE load_id=? AND method=? AND IFNULL(worker_id,0)=IFNULL(?,0)
                   AND IFNULL(combine_id,0)=IFNULL(?,0) AND kg=? AND voided_at IS NULL
                   AND created_at >= datetime(?, '-3 minutes')''',
                (load_id, method, worker_id, combine_id, kg, now_str())).fetchone()
            if recent:
                raise UserError('Bu odamga xuddi shu kg hozirgina yozilgan (takror bo‘lishi mumkin). '
                                'Rostdan ikkinchi tortish bo‘lsa, “Ha, bu ikkinchi tortish” belgisini qo‘yib qayta saqlang.')
        from .accounting import after_combine_harvest, price_harvest
        if load['method'] and load['method'] != method:
            raise UserError('Bu telashka ' + ('qo‘l terimi' if load['method'] == 'hand' else 'kombayn') +
                            ' uchun ochilgan. Boshqa turdagi terim uchun alohida telashka oching.')
        rate, rate_unit, amount = price_harvest(db, method, kg, combine_id, load)     # frozen: never recalculated later
        cur = db.execute(
            '''INSERT INTO harvests(season_year, work_date, load_id, worker_id, field_id, brigadier_id, trailer_id,
                   tractor_id, combine_id, method, kg, note, source, client_uuid, entered_by, created_at, rate, rate_unit, amount)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (load['season_year'], today_str(), load_id, worker_id, load['field_id'], load['brigadier_id'],
             load['trailer_id'], load['tractor_id'], combine_id, method, kg, clean_text(note), source, client_uuid,
             actor.user_id, now_str(), rate, rate_unit, amount))
        hid = cur.lastrowid
        if method == 'combine' and not load['rate']:   # a per-kg trip rate replaces the machine's day tariff
            after_combine_harvest(db, actor, load['season_year'], combine_id, today_str(), load['field_id'])
        audit(db, actor, 'CREATE', 'harvest', hid,
              new={'load_id': load_id, 'method': method, 'kg': kg, 'worker_id': worker_id, 'combine_id': combine_id,
                   'rate': rate, 'rate_unit': rate_unit, 'amount': amount})
        total = db.execute('SELECT COALESCE(SUM(kg),0) FROM harvests WHERE load_id=? AND voided_at IS NULL',
                           (load_id,)).fetchone()[0]
        return hid, {'created_worker': created_worker, 'load_total': total, 'worker_id': worker_id,
                     'rate': rate, 'amount': amount}


def void_harvest(actor, harvest_id, reason):
    _need(actor, 'harvest.write')
    reason = _require_reason(reason, 'Bekor qilish')
    with tx() as db:
        h = db.execute('SELECT * FROM harvests WHERE id=?', (harvest_id,)).fetchone()
        if not h or h['voided_at']:
            raise UserError('Yozuv topilmadi yoki allaqachon bekor qilingan.')
        load = load_row(db, h['load_id'])
        _check_scope(actor, h['brigadier_id'])
        if load['status'] != 'OCHIQ':
            if not actor.can('load.reopen'):
                raise UserError('Telashka TOLDI bo‘lgan — yozuvni faqat Rahbar/Admin bekor qila oladi.')
            if load['status'] == 'TORTILDI':
                raise UserError('Telashka tortilib nakladnoy chiqqan. Yozuvni bekor qilib bo‘lmaydi; tuzatish uchun Rahbar bilan gaplashing.')
        assert_season_open(db, h['season_year'])
        db.execute('UPDATE harvests SET voided_at=?, voided_by=?, void_reason=? WHERE id=?',
                   (now_str(), actor.user_id, reason, harvest_id))
        if load['status'] == 'TOLDI':
            _snapshot_load(db, load['id'])
        audit(db, actor, 'VOID', 'harvest', harvest_id, old=row_dict(h), reason=reason)


def _snapshot_load(db, load_id):
    t = db.execute('''SELECT COALESCE(SUM(CASE WHEN method='hand' THEN kg END),0) hand,
                             COALESCE(SUM(CASE WHEN method='combine' THEN kg END),0) comb
                      FROM harvests WHERE load_id=? AND voided_at IS NULL''', (load_id,)).fetchone()
    db.execute('UPDATE trailer_loads SET hand_kg=?, combine_kg=?, internal_kg=? WHERE id=?',
               (t['hand'], t['comb'], t['hand'] + t['comb'], load_id))
    return t['hand'], t['comb']


def attach_load_photos(actor, load_id, photos, category='trailer', source='web', tg_ids=None):
    _need(actor, 'photos.upload')
    with tx() as db:
        load = load_row(db, load_id)
        ids = []
        for i, data in enumerate(photos):
            ids.append(store_photo(db, actor, data, category=category, entity_type='trailer_load', entity_id=load_id,
                                   caption=f'Telashka yuki №{load_id}', source=source,
                                   tg_file_unique_id=(tg_ids or [None] * len(photos))[i],
                                   links={'season_year': load['season_year'], 'load_id': load_id,
                                          'field_id': load['field_id'], 'brigadier_id': load['brigadier_id']}))
        if ids:
            audit(db, actor, 'PHOTO', 'trailer_load', load_id, new={'photo_ids': ids, 'category': category})
        return ids


def mark_full(actor, load_id, note='', photos=None, source='web'):
    """TOLDI: the trailer is full. Freezes the internal kg snapshot."""
    _need(actor, 'load.full')
    photos = photos or []
    with tx() as db:
        load = load_row(db, load_id)
        if load['status'] == 'TOLDI':
            return {'already': True, 'internal_kg': load['internal_kg']}
        if load['status'] == 'TORTILDI':
            # repeat tap / offline replay of "Tugatish" on a trip that closed with the field sum
            wb = db.execute("SELECT wb.id, wb.number FROM waybills wb JOIN weighings w ON w.load_id=wb.load_id "
                            "WHERE wb.load_id=? AND w.basis='dala'", (load_id,)).fetchone()
            if wb:
                return {'already': True, 'internal_kg': load['internal_kg'], 'waybill_id': wb['id'], 'number': wb['number'],
                        'trip_no': load['trip_no']}
        if load['status'] != 'OCHIQ':
            raise UserError('Bu yuk allaqachon tortilgan yoki bekor qilingan.')
        _check_scope(actor, load['brigadier_id'])
        assert_season_open(db, load['season_year'])
        for data in photos:
            store_photo(db, actor, data, category='trailer', entity_type='trailer_load', entity_id=load_id,
                        caption='Telashka to‘ldi', source=source,
                        links={'season_year': load['season_year'], 'load_id': load_id,
                               'field_id': load['field_id'], 'brigadier_id': load['brigadier_id']})
        need = int(get_float('toldi_min_photos', 1, db) or 0)
        have = db.execute("SELECT COUNT(*) FROM photos WHERE entity_type='trailer_load' AND entity_id=? AND voided_at IS NULL",
                          (load_id,)).fetchone()[0]
        if have < need:
            raise UserError(f'TOLDI uchun kamida {need} ta rasm kerak (telashka/paxta). Hozir: {have} ta.')
        live = db.execute('SELECT COUNT(*) FROM harvests WHERE load_id=? AND voided_at IS NULL', (load_id,)).fetchone()[0]
        if not live:
            raise UserError('Bu telashkada hali birorta terim yozilmagan — bo‘sh telashkani tugatib bo‘lmaydi.')
        hand, comb = _snapshot_load(db, load_id)
        db.execute("UPDATE trailer_loads SET status='TOLDI', full_by=?, full_at=?, note=COALESCE(NULLIF(?,''), note) WHERE id=?",
                   (actor.user_id, now_str(), clean_text(note), load_id))
        audit(db, actor, 'TOLDI', 'trailer_load', load_id, old={'status': 'OCHIQ'},
              new={'status': 'TOLDI', 'hand_kg': hand, 'combine_kg': comb, 'internal_kg': hand + comb})
        res = {'already': False, 'internal_kg': hand + comb, 'hand_kg': hand, 'combine_kg': comb, 'waybill_id': None}
        field_kg = round(hand + comb, 1)
        if field_kg > 0 and get_bool('auto_waybill_hand', db):
            # "TUGATISH": the trip is locked, the waybill carries the field weight (every hand kg weighed per person;
            # combine kg as entered) and the trip is on its way to the receiving point, where it is weighed again.
            ts = now_str()
            cur = db.execute('''INSERT INTO weighings(load_id, gross_kg, gross_at, gross_by, tare_kg, tare_at, tare_by, net_kg,
                                    internal_kg, diff_kg, status, created_at, basis)
                                VALUES (?,?,?,?,0,?,?,?,?,0,'YAKUNLANDI',?,'dala')''',
                             (load_id, field_kg, ts, actor.user_id, ts, actor.user_id, field_kg, field_kg, ts))
            db.execute("UPDATE trailer_loads SET status='TORTILDI', weighed_at=? WHERE id=?", (ts, load_id))
            audit(db, actor, 'FIELD_SUM', 'weighing', cur.lastrowid,
                  new={'load_id': load_id, 'trip_no': load['trip_no'], 'net_kg': field_kg, 'basis': 'dala',
                       'status': 'PUNKTGA YO‘LDA'})
            res['waybill_id'], res['number'] = _issue_waybill(db, actor, load_row(db, load_id), field_kg)
            res['net_kg'] = field_kg
        res['trip_no'] = load['trip_no']
        return res


def reopen_load(actor, load_id, reason):
    _need(actor, 'load.reopen')
    reason = _require_reason(reason, 'Qayta ochish')
    with tx() as db:
        load = load_row(db, load_id)
        if load['status'] != 'TOLDI':
            raise UserError('Faqat TOLDI holatidagi (hali tortilmagan) yukni qayta ochish mumkin.')
        if db.execute('SELECT 1 FROM weighings WHERE load_id=?', (load_id,)).fetchone():
            raise UserError('Brutto allaqachon tortilgan — qayta ochib bo‘lmaydi.')
        db.execute("UPDATE trailer_loads SET status='OCHIQ', full_at=NULL, full_by=NULL WHERE id=?", (load_id,))
        audit(db, actor, 'REOPEN', 'trailer_load', load_id, old={'status': 'TOLDI'}, new={'status': 'OCHIQ'}, reason=reason)


def void_load(actor, load_id, reason):
    _need(actor, 'records.void')
    reason = _require_reason(reason, 'Bekor qilish')
    with tx() as db:
        load = load_row(db, load_id)
        if load['status'] not in ('OCHIQ', 'TOLDI'):
            raise UserError('Tortilgan yukni bekor qilib bo‘lmaydi — nakladnoyni bekor qiling.')
        live = db.execute('SELECT COUNT(*) FROM harvests WHERE load_id=? AND voided_at IS NULL', (load_id,)).fetchone()[0]
        if live:
            raise UserError(f'Yukda {live} ta faol terim yozuvi bor. Avval ularni boshqa yo‘l bilan hal qiling '
                            '(bekor qiling) — kg yo‘qolib ketmasligi uchun.')
        if db.execute('SELECT 1 FROM weighings WHERE load_id=?', (load_id,)).fetchone():
            raise UserError('Brutto tortilgan yukni bekor qilib bo‘lmaydi.')
        db.execute("UPDATE trailer_loads SET status='BEKOR', voided_at=?, voided_by=?, void_reason=? WHERE id=?",
                   (now_str(), actor.user_id, reason, load_id))
        audit(db, actor, 'VOID', 'trailer_load', load_id, old={'status': load['status']}, new={'status': 'BEKOR'}, reason=reason)


# ------------------------------------------------------------------ weighbridge

def record_gross(actor, load_id, gross_kg, *, vehicle_plate='', driver_name='', scale_no='', photo=None, source='web'):
    _need(actor, 'weigh.write')
    with tx() as db:
        load = load_row(db, load_id)
        if load['status'] != 'TOLDI':
            raise UserError('Taroziga faqat TOLDI holatidagi telashka qo‘yiladi.')
        assert_season_open(db, load['season_year'])
        max_g = get_float('max_gross_kg', 40000, db)
        if gross_kg is None or gross_kg <= 0 or gross_kg > max_g:
            raise UserError(f'Brutto 0 va {max_g:g} kg oralig‘ida bo‘lishi kerak.')
        existing = db.execute('SELECT * FROM weighings WHERE load_id=?', (load_id,)).fetchone()
        if existing:
            raise UserError('Brutto allaqachon kiritilgan. Tuzatish kerak bo‘lsa Rahbar “Tuzatish” orqali qiladi.')
        cur = db.execute('''INSERT INTO weighings(load_id, gross_kg, gross_at, gross_by, scale_no, status, created_at)
                            VALUES (?,?,?,?,?, 'BRUTTO', ?)''',
                         (load_id, gross_kg, now_str(), actor.user_id, clean_text(scale_no, 20), now_str()))
        if vehicle_plate or driver_name:
            db.execute("UPDATE trailer_loads SET vehicle_plate=COALESCE(NULLIF(?,''),vehicle_plate), "
                       "driver_name=COALESCE(NULLIF(?,''),driver_name) WHERE id=?",
                       (clean_text(vehicle_plate, 30), clean_text(driver_name, 80), load_id))
        if photo:
            store_photo(db, actor, photo, category='weigh_gross', entity_type='weighing', entity_id=cur.lastrowid,
                        caption=f'Brutto {gross_kg:g} kg', source=source,
                        links={'season_year': load['season_year'], 'load_id': load_id,
                               'field_id': load['field_id'], 'brigadier_id': load['brigadier_id']})
        audit(db, actor, 'GROSS', 'weighing', cur.lastrowid, new={'load_id': load_id, 'gross_kg': gross_kg})
        return cur.lastrowid


def diff_needs_reason(db, internal_kg, net_kg):
    if not internal_kg:
        return False
    pct = get_float('diff_threshold_pct', 2, db) or 0
    return abs(net_kg - internal_kg) / max(net_kg, 1) * 100 > pct


def record_tare(actor, load_id, tare_kg, *, diff_reason='', photo=None, source='web'):
    """Second weighing; computes net and issues the waybill atomically."""
    _need(actor, 'weigh.write')
    with tx() as db:
        load = load_row(db, load_id)
        w = db.execute('SELECT * FROM weighings WHERE load_id=?', (load_id,)).fetchone()
        if not w:
            raise UserError('Avval brutto tortilishi kerak.')
        if w['status'] == 'YAKUNLANDI':
            wb = db.execute('SELECT * FROM waybills WHERE load_id=?', (load_id,)).fetchone()
            return {'already': True, 'waybill_id': wb['id'] if wb else None, 'number': wb['number'] if wb else None,
                    'net_kg': w['net_kg']}
        assert_season_open(db, load['season_year'])
        if tare_kg is None or tare_kg < 0 or tare_kg >= w['gross_kg']:
            raise UserError(f'Tara 0 dan katta va bruttodan ({w["gross_kg"]:g} kg) kichik bo‘lishi kerak.')
        net = round(w['gross_kg'] - tare_kg, 1)
        internal = load['internal_kg'] or 0
        diff = round(net - internal, 1) if internal else None
        diff_reason = clean_text(diff_reason, 300)
        if diff is not None and diff_needs_reason(db, internal, net) and len(diff_reason) < 3:
            pct = get_float('diff_threshold_pct', 2, db)
            raise UserError(f'Ichki hisob ({internal:g} kg) va tarozi netto ({net:g} kg) farqi {diff:+g} kg — '
                            f'{pct:g}% dan katta. Farq sababini yozing.')
        db.execute('''UPDATE weighings SET tare_kg=?, tare_at=?, tare_by=?, net_kg=?, internal_kg=?, diff_kg=?,
                          diff_reason=?, status='YAKUNLANDI', updated_at=? WHERE id=?''',
                   (tare_kg, now_str(), actor.user_id, net, internal, diff, diff_reason or None, now_str(), w['id']))
        db.execute("UPDATE trailer_loads SET status='TORTILDI', weighed_at=? WHERE id=?", (now_str(), load_id))
        if photo:
            store_photo(db, actor, photo, category='weigh_tare', entity_type='weighing', entity_id=w['id'],
                        caption=f'Tara {tare_kg:g} kg', source=source,
                        links={'season_year': load['season_year'], 'load_id': load_id,
                               'field_id': load['field_id'], 'brigadier_id': load['brigadier_id']})
        audit(db, actor, 'TARE', 'weighing', w['id'],
              new={'tare_kg': tare_kg, 'net_kg': net, 'internal_kg': internal, 'diff_kg': diff, 'diff_reason': diff_reason})
        wid, number = _issue_waybill(db, actor, load, net)
        return {'already': False, 'waybill_id': wid, 'number': number, 'net_kg': net,
                'internal_kg': internal, 'diff_kg': diff}


def _issue_waybill(db, actor, load, net):
    """Next PA-number inside the caller's transaction (never reused, never skipped on rollback)."""
    seq = next_counter(db, 'waybill')
    number = f'{WAYBILL_PREFIX}{seq:06d}'
    price = get_float('price_per_kg', None, db)
    station = db.execute('SELECT name FROM stations WHERE id=?', (load['station_id'],)).fetchone() if load['station_id'] else None
    cur = db.execute('''INSERT INTO waybills(seq, number, season_year, load_id, net_kg, document_date, destination,
                            price_per_kg, status, created_by, created_at)
                        VALUES (?,?,?,?,?,?,?,?, 'YARATILDI', ?,?)''',
                     (seq, number, load['season_year'], load['id'], net, today_str(),
                      station['name'] if station else get_setting('destination_name', db),
                      int(price) if price else None, actor.user_id, now_str()))
    db.execute('UPDATE photos SET waybill_id=? WHERE load_id=?', (cur.lastrowid, load['id']))
    audit(db, actor, 'CREATE', 'waybill', cur.lastrowid, new={'number': number, 'load_id': load['id'], 'net_kg': net})
    _sheet_waybill(db, cur.lastrowid, 'yaratildi')
    from .reporting import feed
    info = db.execute('''SELECT tl.trip_no, f.name field, b.name brig FROM trailer_loads tl LEFT JOIN fields f ON f.id=tl.field_id
                         LEFT JOIN brigadiers b ON b.id=tl.brigadier_id WHERE tl.id=?''', (load['id'],)).fetchone()
    feed(db, f'wb:{cur.lastrowid}', f'🚛 Reys tugadi: {info["trip_no"] or ""} · {number}\n'
                                   f'{info["field"] or ""} · {info["brig"] or ""} · {net:,.0f} kg'.replace(',', ' ')
         + (f'\n→ {station["name"]}' if station else ''))
    return cur.lastrowid, number


def correct_weighing(actor, load_id, gross_kg, tare_kg, reason):
    """Supervisor correction after the fact; keeps old values in the audit trail."""
    _need(actor, 'weigh.correct')
    reason = _require_reason(reason, 'Tuzatish')
    with tx() as db:
        w = db.execute('SELECT * FROM weighings WHERE load_id=?', (load_id,)).fetchone()
        if not w:
            raise UserError('Tortish topilmadi.')
        load = load_row(db, load_id)
        assert_season_open(db, load['season_year'])
        if gross_kg is None or gross_kg <= 0:
            raise UserError('Brutto noto‘g‘ri.')
        if w['status'] == 'YAKUNLANDI':
            if tare_kg is None or tare_kg < 0 or tare_kg >= gross_kg:
                raise UserError('Tara bruttodan kichik bo‘lishi kerak.')
            net = round(gross_kg - tare_kg, 1)
            internal = w['internal_kg'] or 0
            diff = round(net - internal, 1) if internal else None
            # a correction is always a real weighbridge / Nayman figure, even for a trip closed from the field sum
            db.execute("UPDATE weighings SET gross_kg=?, tare_kg=?, net_kg=?, diff_kg=?, updated_at=?, basis='tarozi' WHERE id=?",
                       (gross_kg, tare_kg, net, diff, now_str(), w['id']))
            wb = db.execute('SELECT * FROM waybills WHERE load_id=?', (load_id,)).fetchone()
            if wb:
                db.execute('UPDATE waybills SET net_kg=?, updated_at=? WHERE id=?', (net, now_str(), wb['id']))
                rec = db.execute('SELECT * FROM nayman_receipts WHERE waybill_id=?', (wb['id'],)).fetchone()
                if rec:
                    db.execute('UPDATE nayman_receipts SET diff_kg=?, updated_at=? WHERE id=?',
                               (round(rec['accepted_kg'] - net, 1), now_str(), rec['id']))
            new = {'gross_kg': gross_kg, 'tare_kg': tare_kg, 'net_kg': net, 'basis': 'tarozi'}
        else:
            db.execute('UPDATE weighings SET gross_kg=?, updated_at=? WHERE id=?', (gross_kg, now_str(), w['id']))
            new = {'gross_kg': gross_kg}
        audit(db, actor, 'CORRECT', 'weighing', w['id'], old=row_dict(w), new=new, reason=reason)
        wb_row = db.execute('SELECT id FROM waybills WHERE load_id=?', (load_id,)).fetchone()
        if wb_row:
            _sheet_waybill(db, wb_row['id'], 'tarozi tuzatildi')
        corrected_waybill = wb_row['id'] if wb_row else None
    if corrected_waybill:
        after_waybill_change(actor, corrected_waybill, f'tuzatish: {reason}')


# ------------------------------------------------------------------ waybills / Nayman

def void_waybill(actor, waybill_id, reason):
    _need(actor, 'waybill.void')
    reason = _require_reason(reason, 'Nakladnoyni bekor qilish')
    with tx() as db:
        wb = db.execute('SELECT * FROM waybills WHERE id=?', (waybill_id,)).fetchone()
        if not wb or wb['status'] == 'BEKOR':
            raise UserError('Nakladnoy topilmadi yoki allaqachon bekor qilingan.')
        if db.execute('SELECT 1 FROM nayman_receipts WHERE waybill_id=?', (waybill_id,)).fetchone():
            raise UserError('Nayman qabul qilgan nakladnoyni bekor qilib bo‘lmaydi.')
        if db.execute('SELECT 1 FROM payments WHERE waybill_id=? AND voided_at IS NULL', (waybill_id,)).fetchone():
            raise UserError('Bu nakladnoyga to‘lov bog‘langan. Avval to‘lovni bekor qiling.')
        db.execute("UPDATE waybills SET status='BEKOR', voided_at=?, voided_by=?, void_reason=?, updated_at=? WHERE id=?",
                   (now_str(), actor.user_id, reason, now_str(), waybill_id))
        audit(db, actor, 'VOID', 'waybill', waybill_id, old={'status': wb['status']}, new={'status': 'BEKOR'}, reason=reason)
        _sheet_waybill(db, waybill_id, 'bekor qilindi')
    after_waybill_change(actor, waybill_id, f'bekor: {reason}')


NAYMAN_DIFF_REASONS = ['Namlik / tabiiy kamayish', 'Ifloslik (chiqindi)', 'Tarozi farqi', 'Yo‘lda to‘kilgan', 'Boshqa']


def record_nayman(actor, waybill_id, *, accepted_kg, received_date, receiver_name='', diff_reason='', note='',
                  price_per_kg=None, photo=None, edit_reason=''):
    _need(actor, 'nayman.write')
    with tx() as db:
        wb = db.execute('SELECT * FROM waybills WHERE id=?', (waybill_id,)).fetchone()
        if not wb or wb['status'] == 'BEKOR':
            raise UserError('Nakladnoy topilmadi yoki bekor qilingan.')
        assert_season_open(db, wb['season_year'])
        if accepted_kg is None or accepted_kg < 0:
            raise UserError('Qabul qilingan kg noto‘g‘ri.')
        if accepted_kg > wb['net_kg'] * 1.2:
            raise UserError(f'Qabul qilingan kg ({accepted_kg:g}) jo‘natilgandan ({wb["net_kg"]:g}) ancha katta. Tekshiring.')
        diff = round(accepted_kg - wb['net_kg'], 1)
        diff_reason = clean_text(diff_reason, 200)
        if diff != 0 and get_bool('nayman_diff_reason_required', db) and not diff_reason:
            raise UserError(f'Farq {diff:+g} kg. Farq sababini tanlang.')
        amount = int(round(accepted_kg * price_per_kg)) if price_per_kg else None
        existing = db.execute('SELECT * FROM nayman_receipts WHERE waybill_id=?', (waybill_id,)).fetchone()
        vals = (accepted_kg, diff, diff_reason or None, received_date, clean_text(receiver_name, 80),
                price_per_kg, amount, clean_text(note))
        if existing:
            edit_reason = _require_reason(edit_reason, 'Nayman qabulini o‘zgartirish')
            db.execute('''UPDATE nayman_receipts SET accepted_kg=?, diff_kg=?, diff_reason=?, received_date=?, receiver_name=?,
                              price_per_kg=?, amount=?, note=?, updated_at=? WHERE id=?''', vals + (now_str(), existing['id']))
            rid = existing['id']
            audit(db, actor, 'UPDATE', 'nayman_receipt', rid, old=row_dict(existing),
                  new={'accepted_kg': accepted_kg, 'diff_kg': diff, 'diff_reason': diff_reason, 'amount': amount},
                  reason=edit_reason)
        else:
            cur = db.execute('''INSERT INTO nayman_receipts(accepted_kg, diff_kg, diff_reason, received_date, receiver_name,
                                    price_per_kg, amount, note, waybill_id, created_by, created_at)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?)''', vals + (waybill_id, actor.user_id, now_str()))
            rid = cur.lastrowid
            audit(db, actor, 'CREATE', 'nayman_receipt', rid,
                  new={'waybill': wb['number'], 'accepted_kg': accepted_kg, 'diff_kg': diff, 'diff_reason': diff_reason})
        db.execute("UPDATE waybills SET status='QABUL', updated_at=? WHERE id=?", (now_str(), waybill_id))
        _sheet_waybill(db, waybill_id, 'Nayman qabuli' if not existing else 'Nayman qabuli tuzatildi')
        if photo:
            load = load_row(db, wb['load_id'])
            store_photo(db, actor, photo, category='nayman', entity_type='nayman_receipt', entity_id=rid,
                        caption=f'Nayman qabul {wb["number"]}',
                        links={'season_year': wb['season_year'], 'load_id': wb['load_id'], 'waybill_id': waybill_id,
                               'field_id': load['field_id'], 'brigadier_id': load['brigadier_id']})
        return rid, diff


# ------------------------------------------------------------------ receiving point (punkt)

# the operator picks one; nothing is preselected or guessed. Only “Boshqa sabab” needs a written note.
# (older receipts keep whatever reason text they were saved with)
STATION_DIFF_REASONS = ['Tarozilar farqi', 'Tara farqi', 'Paxta to‘kilgan', 'Namlik o‘zgarishi', 'Aralashma ajratilgan',
                        'Qisman tushirilgan', 'Sabab aniqlanmagan', 'Boshqa sabab']
OTHER_REASON = 'Boshqa sabab'


def _station_trip(db, actor, waybill_id):
    wb = db.execute('''SELECT wb.*, tl.station_id, tl.trip_no, tl.field_id, tl.brigadier_id FROM waybills wb
                       JOIN trailer_loads tl ON tl.id=wb.load_id WHERE wb.id=?''', (waybill_id,)).fetchone()
    if not wb or wb['status'] == 'BEKOR':
        raise UserError('Yuk topilmadi yoki nakladnoy bekor qilingan.')
    if actor.role == 'station' and (not actor.station_id or wb['station_id'] != actor.station_id):
        raise UserError('Bu yuk sizning punktingizga jo‘natilmagan.')
    return wb


def diff_level(field_kg, station_kg, db=None):
    """(diff_kg, diff_pct, level): level is ok / warn / alert by the punkt thresholds."""
    diff = round(station_kg - field_kg, 1)
    pct = round(diff / field_kg * 100, 2) if field_kg else 0.0
    warn = get_float('punkt_warn_pct', 1, db) or 0
    alert = get_float('punkt_alert_pct', 3, db) or 0
    level = 'ok' if abs(pct) <= warn else ('warn' if abs(pct) <= alert else 'alert')
    return diff, pct, level


def mark_arrived(actor, waybill_id):
    """KELDI: the trailer reached the punkt (idempotent)."""
    _need(actor, 'station.receive')
    with tx() as db:
        wb = _station_trip(db, actor, waybill_id)
        if wb['arrived_at']:
            return False
        db.execute('UPDATE waybills SET arrived_at=?, arrived_by=? WHERE id=?', (now_str(), actor.user_id, waybill_id))
        audit(db, actor, 'ARRIVED', 'waybill', waybill_id, new={'trip_no': wb['trip_no'], 'status': 'KELDI'})
        _sheet_waybill(db, waybill_id, 'punktga keldi')
        return True


def receive_at_station(actor, waybill_id, *, station_kg=None, reason='', note='', photo=None, gross_kg=None, tare_kg=None):
    """Punkt scale weight → difference → QABUL QILINDI. A second tap never makes a second receipt.
    Either brutto + tara (netto = brutto − tara, both kept) or a ready netto from the scale."""
    _need(actor, 'station.receive')
    if gross_kg is not None or tare_kg is not None:
        if gross_kg is None or tare_kg is None:
            raise UserError('Brutto va tarani ikkalasini ham kiriting (yoki tayyor nettoni).')
        if tare_kg < 0 or gross_kg <= 0:
            raise UserError('Brutto va tara musbat son bo‘lishi kerak.')
        if gross_kg <= tare_kg:
            raise UserError(f'Brutto ({gross_kg:g} kg) taradan ({tare_kg:g} kg) katta bo‘lishi kerak.')
        station_kg = round(gross_kg - tare_kg, 1)
    with tx() as db:
        wb = _station_trip(db, actor, waybill_id)
        existing = db.execute('SELECT * FROM nayman_receipts WHERE waybill_id=?', (waybill_id,)).fetchone()
        if existing:
            return {'already': True, 'receipt_id': existing['id'], 'diff_kg': existing['diff_kg'],
                    'station_kg': existing['accepted_kg']}
        assert_season_open(db, wb['season_year'])
        max_g = get_float('max_gross_kg', 40000, db)
        if station_kg is None or station_kg <= 0 or station_kg > max_g:
            raise UserError(f'Punkt tarozisi kg 0 va {max_g:g} oralig‘ida bo‘lishi kerak.')
        if station_kg > wb['net_kg'] * 1.5:
            raise UserError(f'Punkt vazni ({station_kg:g} kg) daladagidan ({wb["net_kg"]:g} kg) juda katta. Tekshiring.')
        diff, pct, level = diff_level(wb['net_kg'], station_kg, db)
        reason = clean_text(reason, 60)
        note = clean_text(note, 300)
        if level != 'ok' and reason not in STATION_DIFF_REASONS:
            raise UserError(f'Farq {diff:+g} kg ({pct:+.2f}%). Farq sababini tanlang.')
        if reason and reason not in STATION_DIFF_REASONS:
            raise UserError('Sababni ro‘yxatdan tanlang.')
        if reason == OTHER_REASON and len(note) < 3:
            raise UserError('“Boshqa sabab” tanlansa, sababni qisqa yozing.')
        full_reason = (f'{reason}: {note}' if reason and note else reason or note) or None
        ts = now_str()
        if not wb['arrived_at']:
            db.execute('UPDATE waybills SET arrived_at=?, arrived_by=? WHERE id=?', (ts, actor.user_id, waybill_id))
        station = db.execute('SELECT name FROM stations WHERE id=?', (wb['station_id'],)).fetchone()
        cur = db.execute('''INSERT INTO nayman_receipts(accepted_kg, diff_kg, diff_reason, received_date, receiver_name,
                                note, waybill_id, created_by, created_at, station_gross_kg, station_tare_kg) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                         (station_kg, diff, full_reason, today_str(),
                          clean_text(f'{actor.name} ({station["name"]})' if station else actor.name, 80),
                          note or None, waybill_id, actor.user_id, ts, gross_kg, tare_kg))
        db.execute("UPDATE waybills SET status='QABUL', updated_at=? WHERE id=?", (ts, waybill_id))
        audit(db, actor, 'RECEIVE', 'waybill', waybill_id,
              new={'trip_no': wb['trip_no'], 'field_kg': wb['net_kg'], 'station_kg': station_kg, 'diff_kg': diff,
                   'station_gross_kg': gross_kg, 'station_tare_kg': tare_kg,
                   'diff_pct': pct, 'level': level, 'reason': full_reason, 'status': 'QABUL QILINDI'})
        if photo:
            store_photo(db, actor, photo, category='nayman', entity_type='nayman_receipt', entity_id=cur.lastrowid,
                        caption=f'Punkt tarozisi {wb["trip_no"]}: {station_kg:g} kg',
                        links={'season_year': wb['season_year'], 'load_id': wb['load_id'], 'waybill_id': waybill_id,
                               'field_id': wb['field_id'], 'brigadier_id': wb['brigadier_id']})
        _sheet_waybill(db, waybill_id, 'punktda qabul qilindi')
        from .reporting import feed
        feed(db, f'rcv:{waybill_id}', f'🏭 Punktda qabul: {wb["trip_no"]} · {station["name"] if station else ""}\n'
                                     f'Dala {wb["net_kg"]:,.0f} kg → punkt {station_kg:,.0f} kg · farq {diff:+,.0f} kg ({pct:+.2f}%)'
                                     .replace(',', ' ') + (f'\nSabab: {full_reason}' if full_reason else ''))
        if level == 'alert':
            from .reporting import enqueue_alert
            enqueue_alert(db, f'kg:{waybill_id}', f'🔴 Katta kg farqi: {wb["trip_no"]} · dala {wb["net_kg"]:g} kg, '
                                                 f'punkt {station_kg:g} kg, farq {diff:+g} kg ({pct:+.2f}%) · {full_reason}')
        return {'already': False, 'receipt_id': cur.lastrowid, 'diff_kg': diff, 'diff_pct': pct, 'level': level,
                'station_kg': station_kg}


def save_cashbox(actor, cid, *, name, active=True):
    _need(actor, 'users.manage')
    import sqlite3
    name = clean_text(name, 60)
    if len(name) < 2:
        raise UserError('Kassa nomi kiritilishi shart.')
    with tx() as db:
        try:
            if cid:
                old = db.execute('SELECT * FROM cashboxes WHERE id=?', (cid,)).fetchone()
                db.execute('UPDATE cashboxes SET name=?, active=? WHERE id=?', (name, 1 if active else 0, cid))
                audit(db, actor, 'UPDATE', 'cashbox', cid, old=row_dict(old), new={'name': name, 'active': bool(active)})
                return cid
            cur = db.execute('INSERT INTO cashboxes(name, created_at) VALUES (?,?)', (name, now_str()))
            audit(db, actor, 'CREATE', 'cashbox', cur.lastrowid, new={'name': name})
            return cur.lastrowid
        except sqlite3.IntegrityError as e:
            _unique_error(e, 'Bu kassa')


def save_station(actor, sid, *, name, address='', active=True):
    _need(actor, 'masterdata.write')
    import sqlite3
    name = clean_text(name, 60)
    if len(name) < 2:
        raise UserError('Punkt nomi kiritilishi shart.')
    with tx() as db:
        try:
            if sid:
                old = db.execute('SELECT * FROM stations WHERE id=?', (sid,)).fetchone()
                db.execute('UPDATE stations SET name=?, address=?, active=? WHERE id=?',
                           (name, clean_text(address, 200), 1 if active else 0, sid))
                audit(db, actor, 'UPDATE', 'station', sid, old=row_dict(old), new={'name': name, 'active': bool(active)})
                return sid
            cur = db.execute('INSERT INTO stations(name, address, created_at) VALUES (?,?,?)',
                             (name, clean_text(address, 200), now_str()))
            audit(db, actor, 'CREATE', 'station', cur.lastrowid, new={'name': name})
            return cur.lastrowid
        except sqlite3.IntegrityError as e:
            _unique_error(e, 'Bu punkt')


# ------------------------------------------------------------------ money

def add_payment(actor, *, amount, payment_date, waybill_id=None, method='', payer='Nayman', note='', client_uuid=None,
                to_cash=False, photo=None):
    _need(actor, 'payments.write')
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT id FROM payments WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return dup['id']
        season = current_season(db)
        if waybill_id:
            wb = db.execute('SELECT * FROM waybills WHERE id=?', (waybill_id,)).fetchone()
            if not wb or wb['status'] == 'BEKOR':
                raise UserError('Nakladnoy topilmadi yoki bekor qilingan.')
            season = wb['season_year']
        assert_season_open(db, season)
        cur = db.execute('''INSERT INTO payments(season_year, waybill_id, payment_date, amount, method, payer, note,
                                client_uuid, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                         (season, waybill_id, payment_date, amount, clean_text(method, 40), clean_text(payer, 80) or 'Nayman',
                          clean_text(note), client_uuid, actor.user_id, now_str()))
        pid = cur.lastrowid
        if to_cash:
            from .accounting import _cashbox, book_cash
            cid, _no = book_cash(db, actor, direction='IN', category='nayman', amount=amount, entry_date=payment_date,
                                 cashbox_id=_cashbox(db, actor, None), counterparty=payer, source=payer or 'Nayman',
                                 note=f'To‘lov #{pid}')
            db.execute('UPDATE cash_entries SET payment_id=? WHERE id=?', (pid, cid))
        if photo:
            store_photo(db, actor, photo, category='payment', entity_type='payment', entity_id=pid,
                        caption=f'To‘lov {amount:,} so‘m', links={'season_year': season, 'waybill_id': waybill_id})
        audit(db, actor, 'CREATE', 'payment', pid, new={'amount': amount, 'waybill_id': waybill_id, 'method': method,
                                                        'to_cash': bool(to_cash)})
        _sheet_payment(db, pid, 'kiritildi')
        return pid


def void_payment(actor, payment_id, reason):
    _need(actor, 'payments.write')
    reason = _require_reason(reason, 'To‘lovni bekor qilish')
    with tx() as db:
        p = db.execute('SELECT * FROM payments WHERE id=?', (payment_id,)).fetchone()
        if not p or p['voided_at']:
            raise UserError('To‘lov topilmadi yoki bekor qilingan.')
        assert_season_open(db, p['season_year'])
        from .accounting import assert_day_open
        for c in db.execute('SELECT cashbox_id, entry_date FROM cash_entries WHERE payment_id=? AND voided_at IS NULL',
                            (payment_id,)).fetchall():
            if c['cashbox_id']:
                assert_day_open(db, c['cashbox_id'], c['entry_date'])
        db.execute('UPDATE payments SET voided_at=?, voided_by=?, void_reason=? WHERE id=?',
                   (now_str(), actor.user_id, reason, payment_id))
        db.execute('UPDATE cash_entries SET voided_at=?, voided_by=?, void_reason=? WHERE payment_id=? AND voided_at IS NULL',
                   (now_str(), actor.user_id, f'To‘lov #{payment_id} bekor qilindi: {reason}', payment_id))
        _sheet_payment(db, payment_id, 'bekor qilindi')
        audit(db, actor, 'VOID', 'payment', payment_id, old=row_dict(p), reason=reason)


EXPENSE_CATEGORIES = ['Yoqilg‘i', 'Ovqat', 'Transport', 'Remont', 'Punkt xarajati', 'O‘g‘it / kimyo', 'Suv', 'Ijara',
                      'Boshqa']

CASH_CATEGORIES = {
    'opening': ('IN', 'Boshlang‘ich qoldiq'),
    'nayman': ('IN', 'Naymandan tushum'),
    'other_in': ('IN', 'Boshqa kirim'),
    'worker_pay': ('OUT', 'Ishchiga to‘lov'),
    'advance': ('OUT', 'Avans'),
    'expense': ('OUT', 'Xarajat'),
    'other_out': ('OUT', 'Boshqa chiqim'),
    'income': ('IN', 'Kirim'),
    'combine_pay': ('OUT', 'Kombayn to‘lovi'),
    'adjust_in': ('IN', 'Kassa farqi (ortiqcha)'),
    'adjust_out': ('OUT', 'Kassa farqi (kam)'),
    'debt_in': ('IN', 'Qarz qaytdi'),
    'debt_out': ('OUT', 'Qarz to‘landi'),
    'refund_worker_pay': ('IN', 'To‘lov qaytarildi'),
    'refund_advance': ('IN', 'Avans qaytarildi'),
    'refund_combine_pay': ('IN', 'Kombayn to‘lovi qaytarildi'),
}
# categories only created by their own flows (payment orders, day close, debts), never typed on the kassa form
SYSTEM_CASH_CATEGORIES = {'combine_pay', 'adjust_in', 'adjust_out', 'debt_in', 'debt_out', 'refund_worker_pay',
                          'refund_advance', 'refund_combine_pay'}


def add_expense(actor, *, amount, expense_date, category, field_id=None, brigadier_id=None, equipment_id=None, payer='',
                note='', from_cash=False, client_uuid=None, photo=None, station_id=None, cashbox_id=None):
    _need(actor, 'expenses.write')
    from .accounting import _cashbox, assert_day_open, book_cash, mirror_expense
    from .db import doc_number
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT id FROM expenses WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return dup['id']
        season = current_season(db)
        assert_season_open(db, season)
        category = clean_text(category, 60)
        if not category:
            raise UserError('Xarajat turi tanlanishi shart.')
        if not amount or amount <= 0:
            raise UserError('Summa 0 dan katta bo‘lishi kerak.')
        box = _cashbox(db, actor, cashbox_id)
        assert_day_open(db, box, expense_date)
        if field_id and not brigadier_id:   # a field expense belongs to that field's brigade (for per-brigade cost)
            row = db.execute('SELECT brigadier_id FROM fields WHERE id=?', (field_id,)).fetchone()
            brigadier_id = row['brigadier_id'] if row else None
        # entered by the accountant (or admin) = checked; by anyone else it waits for the accountant
        status = 'TASDIQLANGAN' if actor.can('expenses.approve') else 'TEKSHIRILMAGAN'
        no = doc_number(db, 'EXP', season)
        cur = db.execute('''INSERT INTO expenses(season_year, expense_date, category, amount, field_id, brigadier_id,
                                equipment_id, payer, note, client_uuid, created_by, created_at, doc_no, cashbox_id,
                                station_id, status, checked_by, checked_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (season, expense_date, category, amount, field_id, brigadier_id, equipment_id,
                          clean_text(payer, 80), clean_text(note), client_uuid, actor.user_id, now_str(), no, box,
                          station_id, status, actor.user_id if status == 'TASDIQLANGAN' else None,
                          now_str() if status == 'TASDIQLANGAN' else None))
        eid = cur.lastrowid
        if from_cash:
            cid, _ = book_cash(db, actor, direction='OUT', category='expense', amount=amount, entry_date=expense_date,
                               cashbox_id=box, expense_id=eid, counterparty=payer, note=f'{category}: {note}'[:200], doc_no=no)
            db.execute('UPDATE expenses SET cash_entry_id=? WHERE id=?', (cid, eid))
        mirror_expense(db, eid)
        if photo:
            store_photo(db, actor, photo, category='expense', entity_type='expense', entity_id=eid,
                        caption=f'{category} {amount:,} so‘m', links={'season_year': season, 'field_id': field_id})
        audit(db, actor, 'CREATE', 'expense', eid, new={'doc_no': no, 'amount': amount, 'category': category,
                                                         'from_cash': bool(from_cash), 'status': status, 'cashbox_id': box})
        return eid


def void_expense(actor, expense_id, reason):
    _need(actor, 'expenses.approve')
    reason = _require_reason(reason, 'Xarajatni bekor qilish')
    with tx() as db:
        e = db.execute('SELECT * FROM expenses WHERE id=?', (expense_id,)).fetchone()
        if not e or e['voided_at']:
            raise UserError('Xarajat topilmadi yoki bekor qilingan.')
        assert_season_open(db, e['season_year'])
        from .accounting import assert_day_open, mirror_cash, mirror_expense
        if e['cashbox_id']:
            assert_day_open(db, e['cashbox_id'], e['expense_date'])
        db.execute('UPDATE expenses SET voided_at=?, voided_by=?, void_reason=? WHERE id=?',
                   (now_str(), actor.user_id, reason, expense_id))
        db.execute('UPDATE cash_entries SET voided_at=?, voided_by=?, void_reason=? WHERE expense_id=? AND voided_at IS NULL',
                   (now_str(), actor.user_id, f'Xarajat #{expense_id} bekor qilindi: {reason}', expense_id))
        audit(db, actor, 'VOID', 'expense', expense_id, old=row_dict(e), reason=reason)
        mirror_expense(db, expense_id)
        if e['cash_entry_id']:
            mirror_cash(db, e['cash_entry_id'])


def cash_balance(db, season):
    row = db.execute('''SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount END),0) inflow,
                               COALESCE(SUM(CASE WHEN direction='OUT' THEN amount END),0) outflow
                        FROM cash_entries WHERE season_year=? AND voided_at IS NULL''', (season,)).fetchone()
    return row['inflow'] - row['outflow']


def _assert_cash_available(db, season, amount):
    bal = cash_balance(db, season)
    if amount > bal:
        raise UserError(f'Kassada yetarli pul yo‘q: qoldiq {bal:,} so‘m, chiqim {amount:,} so‘m.'.replace(',', ' '))


def add_cash_entry(actor, *, category, amount, entry_date, worker_id=None, counterparty='', note='', client_uuid=None,
                   photo=None, cashbox_id=None, source=''):
    _need(actor, 'cash.write')
    if category not in CASH_CATEGORIES or category in SYSTEM_CASH_CATEGORIES:
        raise UserError('Kassa operatsiyasi turi noto‘g‘ri.')
    if not amount or amount <= 0:
        raise UserError('Summa 0 dan katta bo‘lishi kerak.')
    direction = CASH_CATEGORIES[category][0]
    with tx() as db:
        if client_uuid:
            dup = db.execute('SELECT id FROM cash_entries WHERE client_uuid=?', (client_uuid,)).fetchone()
            if dup:
                return dup['id']
        season = current_season(db)
        assert_season_open(db, season)
        if category in ('worker_pay', 'advance'):
            if not worker_id or not db.execute('SELECT 1 FROM workers WHERE id=?', (worker_id,)).fetchone():
                raise UserError('Ishchi tanlanishi shart.')
        else:
            worker_id = None
        if category == 'opening' and db.execute(
                "SELECT 1 FROM cash_entries WHERE season_year=? AND category='opening' AND voided_at IS NULL", (season,)).fetchone():
            raise UserError('Bu mavsum uchun boshlang‘ich qoldiq allaqachon kiritilgan.')
        from .accounting import _cashbox, book_cash
        cid, _no = book_cash(db, actor, direction=direction, category=category, amount=amount, entry_date=entry_date,
                             cashbox_id=_cashbox(db, actor, cashbox_id), worker_id=worker_id, counterparty=counterparty,
                             source=source or counterparty, note=note, client_uuid=client_uuid)
        if photo:
            store_photo(db, actor, photo, category='cash', entity_type='cash_entry', entity_id=cid,
                        caption=f'{CASH_CATEGORIES[category][1]} {amount:,}', links={'season_year': season})
        return cid


def void_cash_entry(actor, entry_id, reason):
    _need(actor, 'cash.write')
    reason = _require_reason(reason, 'Kassa yozuvini bekor qilish')
    with tx() as db:
        c = db.execute('SELECT * FROM cash_entries WHERE id=?', (entry_id,)).fetchone()
        if not c or c['voided_at']:
            raise UserError('Yozuv topilmadi yoki bekor qilingan.')
        if c['expense_id'] or c['payment_id'] or c['payout_id'] or c['debt_id'] or c['category'] in SYSTEM_CASH_CATEGORIES:
            raise UserError('Bu yozuv xarajat / to‘lov / qarz / kun yopishga bog‘langan — o‘sha joydan tuzatiladi.')
        assert_season_open(db, c['season_year'])
        from .accounting import assert_day_open, box_balance, mirror_cash
        if c['cashbox_id']:
            assert_day_open(db, c['cashbox_id'], c['entry_date'])
        if c['direction'] == 'IN' and c['cashbox_id']:
            if box_balance(db, c['cashbox_id']) - c['amount'] < 0:
                raise UserError('Bu kirimni bekor qilsangiz kassa qoldig‘i manfiy bo‘ladi.')
        db.execute('UPDATE cash_entries SET voided_at=?, voided_by=?, void_reason=? WHERE id=?',
                   (now_str(), actor.user_id, reason, entry_id))
        audit(db, actor, 'VOID', 'cash_entry', entry_id, old=row_dict(c), reason=reason)
        mirror_cash(db, entry_id)


def payment_rule():
    """Configured expected-payment rule, or None if not confirmed/enabled."""
    if not get_bool('payment_rule_enabled'):
        return None
    pct = get_float('payment_rule_percent', None)
    days = get_float('payment_rule_days', None)
    if not pct or days is None:
        return None
    return {'percent': pct, 'days': int(days)}


def expected_payment(receipt_date, amount):
    rule = payment_rule()
    if not rule or not amount:
        return None
    due = date.fromisoformat(receipt_date) + timedelta(days=rule['days'])
    return {'amount': int(round(amount * rule['percent'] / 100)), 'due_date': due.isoformat(), **rule}


# ------------------------------------------------------------------ master data

def _unique_error(exc, label):
    if 'UNIQUE' in str(exc):
        raise UserError(f'{label} allaqachon mavjud.')
    raise exc


def save_brigadier(actor, bid, name, full_name='', phone='', active=True):
    _need(actor, 'masterdata.write')
    import sqlite3
    name = clean_text(name, 60)
    if len(name) < 2:
        raise UserError('Brigadir nomi kiritilishi shart.')
    with tx() as db:
        try:
            if bid:
                old = db.execute('SELECT * FROM brigadiers WHERE id=?', (bid,)).fetchone()
                db.execute('UPDATE brigadiers SET name=?, full_name=?, phone=?, active=? WHERE id=?',
                           (name, clean_text(full_name, 120), clean_text(phone, 30), 1 if active else 0, bid))
                audit(db, actor, 'UPDATE', 'brigadier', bid, old=row_dict(old), new={'name': name, 'full_name': full_name})
                return bid
            cur = db.execute('INSERT INTO brigadiers(name, full_name, phone, created_at) VALUES (?,?,?,?)',
                             (name, clean_text(full_name, 120), clean_text(phone, 30), now_str()))
            audit(db, actor, 'CREATE', 'brigadier', cur.lastrowid, new={'name': name})
            return cur.lastrowid
        except sqlite3.IntegrityError as e:
            _unique_error(e, 'Bu brigadir')


def save_field(actor, fid, *, code, name, area_ha, brigadier_id=None, notes='', polygon_json=None, active=True):
    _need(actor, 'masterdata.write')
    import sqlite3
    code, name = clean_text(code, 20), clean_text(name, 80)
    if not code or not name:
        raise UserError('Dala kodi va nomi majburiy.')
    if polygon_json:
        try:
            pts = json.loads(polygon_json)
            if not (isinstance(pts, list) and len(pts) >= 3 and all(len(p) == 2 for p in pts)):
                raise ValueError
            polygon_json = json.dumps([[round(float(a), 6), round(float(b), 6)] for a, b in pts])
        except (ValueError, TypeError):
            raise UserError('Xarita chegarasi noto‘g‘ri (kamida 3 nuqta kerak).')
    with tx() as db:
        try:
            if fid:
                old = db.execute('SELECT * FROM fields WHERE id=?', (fid,)).fetchone()
                db.execute('''UPDATE fields SET code=?, name=?, area_ha=?, brigadier_id=?, notes=?, polygon_json=?, active=?
                              WHERE id=?''', (code, name, area_ha, brigadier_id, clean_text(notes), polygon_json,
                                              1 if active else 0, fid))
                audit(db, actor, 'UPDATE', 'field', fid, old=row_dict(old),
                      new={'code': code, 'name': name, 'area_ha': area_ha, 'brigadier_id': brigadier_id,
                           'polygon': bool(polygon_json), 'active': bool(active)})
                return fid
            cur = db.execute('''INSERT INTO fields(code, name, area_ha, brigadier_id, notes, polygon_json, created_at)
                                VALUES (?,?,?,?,?,?,?)''', (code, name, area_ha, brigadier_id, clean_text(notes),
                                                            polygon_json, now_str()))
            audit(db, actor, 'CREATE', 'field', cur.lastrowid, new={'code': code, 'name': name, 'area_ha': area_ha})
            return cur.lastrowid
        except sqlite3.IntegrityError as e:
            _unique_error(e, 'Bu dala kodi')


def save_equipment(actor, eid, *, kind, code, plate='', operator_name='', ownership='own', notes='', active=True):
    _need(actor, 'masterdata.write')
    import sqlite3
    if kind not in ('traktor', 'telashka', 'kombayn', 'mashina'):
        raise UserError('Texnika turi noto‘g‘ri.')
    code = clean_text(code, 20).upper()
    if not code:
        raise UserError('Texnika raqami/kodi majburiy.')
    with tx() as db:
        try:
            if eid:
                old = db.execute('SELECT * FROM equipment WHERE id=?', (eid,)).fetchone()
                if not active and kind == 'telashka' and db.execute(
                        "SELECT 1 FROM trailer_loads WHERE trailer_id=? AND status IN ('OCHIQ','TOLDI')", (eid,)).fetchone():
                    raise UserError('Telashkada tugallanmagan yuk bor — hozir o‘chirib bo‘lmaydi.')
                db.execute('''UPDATE equipment SET kind=?, code=?, plate=?, operator_name=?, ownership=?, notes=?, active=?
                              WHERE id=?''', (kind, code, clean_text(plate, 30), clean_text(operator_name, 80),
                                              ownership, clean_text(notes), 1 if active else 0, eid))
                audit(db, actor, 'UPDATE', 'equipment', eid, old=row_dict(old),
                      new={'code': code, 'plate': plate, 'operator_name': operator_name, 'active': bool(active)})
                return eid
            cur = db.execute('''INSERT INTO equipment(kind, code, plate, operator_name, ownership, notes, created_at)
                                VALUES (?,?,?,?,?,?,?)''', (kind, code, clean_text(plate, 30), clean_text(operator_name, 80),
                                                            ownership, clean_text(notes), now_str()))
            audit(db, actor, 'CREATE', 'equipment', cur.lastrowid, new={'kind': kind, 'code': code})
            return cur.lastrowid
        except sqlite3.IntegrityError as e:
            _unique_error(e, 'Bu texnika kodi')


def validate_password(pw):
    if len(pw or '') < 8:
        raise UserError('Parol kamida 8 belgidan iborat bo‘lsin.')
    if pw.isdigit() or pw.isalpha():
        raise UserError('Parolda harf va raqam aralash bo‘lsin.')


def save_user(actor, uid, *, username, full_name, role, password='', brigadier_id=None, phone='', active=True,
              station_id=None, cashbox_id=None):
    _need(actor, 'users.manage')
    import sqlite3
    username = clean_text(username, 40).lower()
    if not username or not username.replace('_', '').replace('.', '').isalnum():
        raise UserError('Login faqat lotin harf, raqam, “.” va “_” dan iborat bo‘lsin.')
    if role not in ROLES:
        raise UserError('Rol noto‘g‘ri.')
    if role == 'brigadier' and not brigadier_id:
        raise UserError('Brigadir roli uchun qaysi brigada ekanini tanlang.')
    if role == 'station' and not station_id:
        raise UserError('Punkt operatori uchun qaysi punkt ekanini tanlang.')
    station_id = station_id if role == 'station' else None
    cashbox_id = cashbox_id if role == 'cashier' else None
    with tx() as db:
        try:
            if uid:
                old = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
                if uid == actor.user_id and (not active or role != 'admin') and old['role'] == 'admin':
                    raise UserError('O‘zingizni admin rolidan chiqara olmaysiz.')
                db.execute('UPDATE users SET username=?, full_name=?, role=?, brigadier_id=?, phone=?, active=? WHERE id=?',
                           (username, clean_text(full_name, 120), role, brigadier_id if role == 'brigadier' else None,
                            clean_text(phone, 30), 1 if active else 0, uid))
                if password:
                    validate_password(password)
                    db.execute('UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?',
                               (generate_password_hash(password), uid))
                db.execute('UPDATE users SET station_id=?, cashbox_id=? WHERE id=?', (station_id, cashbox_id, uid))
                audit(db, actor, 'UPDATE', 'user', uid,
                      old={k: old[k] for k in ('username', 'full_name', 'role', 'brigadier_id', 'active', 'station_id')},
                      new={'username': username, 'full_name': full_name, 'role': role, 'brigadier_id': brigadier_id,
                           'station_id': station_id, 'active': bool(active), 'password_reset': bool(password)})
                return uid
            validate_password(password)
            cur = db.execute('''INSERT INTO users(username, password_hash, full_name, role, brigadier_id, phone,
                                    must_change_password, created_at, station_id) VALUES (?,?,?,?,?,?,1,?,?)''',
                             (username, generate_password_hash(password), clean_text(full_name, 120), role,
                              brigadier_id if role == 'brigadier' else None, clean_text(phone, 30), now_str(), station_id))
            db.execute('UPDATE users SET cashbox_id=? WHERE id=?', (cashbox_id, cur.lastrowid))
            audit(db, actor, 'CREATE', 'user', cur.lastrowid, new={'username': username, 'role': role,
                                                                   'station_id': station_id, 'cashbox_id': cashbox_id})
            return cur.lastrowid
        except sqlite3.IntegrityError as e:
            _unique_error(e, 'Bu login')


STAFF_ROLE_ALIASES = {'hisobchi': 'tally', 'terim': 'tally', 'punkt': 'station', 'buxgalter': 'accountant',
                      'kassir': 'cashier', 'kassa': 'cashier', 'rahbar': 'manager', 'tarozi': 'scale',
                      'haydovchi': 'driver', 'brigadir': 'brigadier', 'admin': 'admin'}


def random_password():
    """8 easy-to-read characters (no 0/o/1/l), letters + digits — to be written on paper and changed at first login."""
    import secrets
    return (''.join(secrets.choice('abcdefghjkmnpqrstuvwxyz') for _ in range(4))
            + ''.join(secrets.choice('23456789') for _ in range(4)))


def _username_for(db, full_name):
    tr = str.maketrans({'‘': '', '’': '', "'": '', 'ʻ': '', 'ʼ': '', '`': ''})
    words = full_name.split()
    base = ''.join(ch for ch in (words[0] if words else '').lower().translate(tr) if ch.isascii() and ch.isalnum())
    base = base or 'xodim'
    name, n = base, 1
    while db.execute('SELECT 1 FROM users WHERE username=?', (name,)).fetchone():
        n += 1
        name = f'{base}{n}'
    return name


def quick_add_user(actor, full_name, role):
    """Name + role → login and a generated password (shown once). Punkt/kassa get the first active one."""
    _need(actor, 'users.manage')
    role = STAFF_ROLE_ALIASES.get((role or '').strip().lower(), (role or '').strip().lower())
    full_name = clean_text(full_name, 120)
    if len(full_name) < 2:
        raise UserError('Ismni yozing.')
    if role not in ROLES:
        raise UserError('Rol noto‘g‘ri.')
    db = get_db()
    brig = station = box = None
    if role == 'brigadier':
        row = db.execute('SELECT id FROM brigadiers WHERE active=1 AND lower(name)=lower(?)', (full_name,)).fetchone()
        brig = row['id'] if row else None
        if not brig:
            raise UserError('Brigadir uchun “Brigadirlar” bo‘limida shu nomli brigada bo‘lishi kerak.')
    if role == 'station':
        row = db.execute('SELECT id FROM stations WHERE active=1 ORDER BY id LIMIT 1').fetchone()
        if not row:
            raise UserError('Avval “Punktlar” bo‘limida punkt qo‘shing.')
        station = row['id']
    if role == 'cashier':
        row = db.execute('SELECT id FROM cashboxes WHERE active=1 ORDER BY id LIMIT 1').fetchone()
        box = row['id'] if row else None
    password = random_password()
    uid = save_user(actor, None, username=_username_for(db, full_name), full_name=full_name, role=role,
                    password=password, brigadier_id=brig, station_id=station, cashbox_id=box)
    user = db.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone()
    return uid, user['username'], password


def reset_password_random(actor, uid):
    _need(actor, 'users.manage')
    password = random_password()
    with tx() as db:
        u = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not u:
            raise UserError('Xodim topilmadi.')
        db.execute('UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?',
                   (generate_password_hash(password), uid))
        audit(db, actor, 'UPDATE', 'user', uid, new={'password_reset': True})
    return u['full_name'], u['username'], password


def set_user_active(actor, uid, active):
    _need(actor, 'users.manage')
    with tx() as db:
        u = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not u:
            raise UserError('Xodim topilmadi.')
        if uid == actor.user_id and not active:
            raise UserError('O‘zingizni o‘chira olmaysiz.')
        db.execute('UPDATE users SET active=? WHERE id=?', (1 if active else 0, uid))
        if not active:  # an ex-employee's Telegram must stop working too
            db.execute('UPDATE users SET telegram_id=NULL WHERE id=?', (uid,))
            db.execute("UPDATE tg_members SET status='NOFAOL' WHERE user_id=?", (uid,))
        audit(db, actor, 'UPDATE', 'user', uid, old={'active': bool(u['active'])}, new={'active': bool(active)})
    return u['full_name']


def change_password(actor, uid, new_password):
    validate_password(new_password)
    with tx() as db:
        db.execute('UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?',
                   (generate_password_hash(new_password), uid))
        audit(db, actor, 'PASSWORD', 'user', uid, new={'changed': True})


def telegram_link_code(actor, uid):
    _need(actor, 'users.manage')
    code = secrets.token_hex(4).upper()
    with tx() as db:
        db.execute("UPDATE users SET tg_link_code=?, tg_link_expires=datetime(?, '+2 days') WHERE id=?",
                   (code, now_str(), uid))
        audit(db, actor, 'TG_LINK_CODE', 'user', uid, new={'issued': True})
    return code


def unlink_telegram(actor, uid):
    _need(actor, 'users.manage')
    with tx() as db:
        db.execute('UPDATE users SET telegram_id=NULL, tg_link_code=NULL WHERE id=?', (uid,))
        audit(db, actor, 'TG_UNLINK', 'user', uid)


def save_settings(actor, values: dict):
    _need(actor, 'settings.manage')
    from .settings import DEFAULTS
    with tx() as db:
        changed = {}
        for key, val in values.items():
            if key not in DEFAULTS:
                continue
            val = clean_text(val, 200)
            old = get_setting(key, db)
            if old != val:
                db.execute('INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) '
                           'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, '
                           'updated_by=excluded.updated_by', (key, val, now_str(), actor.user_id))
                changed[key] = {'old': old, 'new': val}
        if changed:
            audit(db, actor, 'UPDATE', 'settings', None, new=changed)
        return changed


def void_photo(actor, photo_id, reason):
    _need(actor, 'photos.void')
    reason = _require_reason(reason, 'Rasmni yashirish')
    with tx() as db:
        p = db.execute('SELECT * FROM photos WHERE id=?', (photo_id,)).fetchone()
        if not p or p['voided_at']:
            raise UserError('Rasm topilmadi.')
        db.execute('UPDATE photos SET voided_at=?, voided_by=?, void_reason=? WHERE id=?',
                   (now_str(), actor.user_id, reason, photo_id))
        audit(db, actor, 'VOID', 'photo', photo_id, old={'path': p['path'], 'category': p['category']}, reason=reason)


def upload_archive_photo(actor, data, *, category, caption='', load_id=None, waybill_id=None, field_id=None,
                         lat=None, lon=None, source='web', tg_file_unique_id=None):
    _need(actor, 'photos.upload')
    with tx() as db:
        links = {'season_year': current_season(db), 'field_id': field_id}
        entity_type, entity_id = 'archive', None
        if load_id:
            load = load_row(db, load_id)
            links.update(load_id=load_id, field_id=load['field_id'], brigadier_id=load['brigadier_id'],
                         season_year=load['season_year'])
            entity_type, entity_id = 'trailer_load', load_id
            wb = db.execute('SELECT id FROM waybills WHERE load_id=?', (load_id,)).fetchone()
            if wb:
                links['waybill_id'] = wb['id']
        if waybill_id:
            links['waybill_id'] = waybill_id
        pid = store_photo(db, actor, data, category=category, entity_type=entity_type, entity_id=entity_id,
                          caption=clean_text(caption, 200), lat=lat, lon=lon, source=source,
                          tg_file_unique_id=tg_file_unique_id, links=links)
        audit(db, actor, 'PHOTO', entity_type, entity_id or pid, new={'photo_id': pid, 'category': category})
        return pid


# ------------------------------------------------------------------ waybill PDF documents

def create_waybill_documents(actor, waybill_id, reason='yaratildi'):
    """Render the Nayman copy (no price/amount) and the internal copy, store both as a new version,
    and queue them for the Telegram archive channel. Earlier versions are kept (marked superseded)."""
    import hashlib
    from flask import current_app
    from . import queries
    from .pdfdoc import build_waybill_pdf
    wb = queries.waybill(waybill_id)
    if not wb:
        raise UserError('Nakladnoy topilmadi.')
    cfg = current_app.config['SURXON']
    with tx() as db:
        workers = db.execute('SELECT COUNT(DISTINCT worker_id) FROM harvests WHERE load_id=? AND voided_at IS NULL AND worker_id IS NOT NULL',
                             (wb['load_id'],)).fetchone()[0]
        lines = [(r[0], r[1]) for r in db.execute(
            '''SELECT COALESCE(w.full_name, 'Kombayn ' || e.code), SUM(h.kg) FROM harvests h
               LEFT JOIN workers w ON w.id=h.worker_id LEFT JOIN equipment e ON e.id=h.combine_id
               WHERE h.load_id=? AND h.voided_at IS NULL
               GROUP BY h.method, h.worker_id, h.combine_id ORDER BY MIN(h.id)''', (wb['load_id'],))]
        version = (db.execute('SELECT MAX(version) FROM documents WHERE waybill_id=?', (waybill_id,)).fetchone()[0] or 0) + 1
        company = get_setting('company_name', db)
        folder = cfg.UPLOAD_DIR / 'hujjatlar' / str(wb['season_year'])
        folder.mkdir(parents=True, exist_ok=True)
        db.execute('UPDATE documents SET superseded=1 WHERE waybill_id=?', (waybill_id,))
        ids = []
        for kind in ('nayman', 'ichki'):
            pdf = build_waybill_pdf(wb, copy=kind, company=company, version=version, generated_at=now_str()[:16],
                                    generated_by=actor.name or 'tizim', workers_count=workers,
                                    lines=lines if kind == 'ichki' else None, domain=cfg.DOMAIN)
            name = f'{wb["number"]}_v{version}_{kind}.pdf'
            (folder / name).write_bytes(pdf)
            rel = f'hujjatlar/{wb["season_year"]}/{name}'
            cur = db.execute('''INSERT INTO documents(waybill_id, kind, version, reason, path, sha256, size, created_by, created_at)
                                VALUES (?,?,?,?,?,?,?,?,?)''', (waybill_id, kind, version, reason, rel,
                                                                 hashlib.sha256(pdf).hexdigest(), len(pdf), actor.user_id, now_str()))
            ids.append(cur.lastrowid)
            enqueue(db, 'telegram_archive', 'document', f'doc:{cur.lastrowid}',
                    {'path': rel, 'filename': name,
                     'caption': f'📄 {wb["trip_no"] or ""} · {wb["number"]} · {version}-versiya ({reason}) · '
                                f'{"Punkt nakladnoyi" if kind == "nayman" else "Ichki terim hisoboti"}\n'
                                f'{wb["trailer_code"]} · {wb["field_name"]} · {wb["brigadier_name"]} · {wb["net_kg"]:g} kg'})
        audit(db, actor, 'PDF', 'waybill', waybill_id, new={'version': version, 'reason': reason, 'document_ids': ids})
        return version


def ensure_missing_documents():
    """Safety net: any waybill without a PDF (e.g. rendering failed after the weighing committed) gets one."""
    from .db import q
    from .security import Actor
    missing = q('''SELECT wb.id FROM waybills wb WHERE NOT EXISTS (SELECT 1 FROM documents d WHERE d.waybill_id=wb.id)
                   ORDER BY wb.id LIMIT 20''')
    for r in missing:
        try:
            create_waybill_documents(Actor(None, 'system', source='system', name='tizim'), r['id'], 'avtomatik tiklandi')
        except Exception:
            from flask import current_app
            current_app.logger.exception('PDF generation failed for waybill %s', r['id'])


def after_waybill_change(actor, waybill_id, reason):
    """Called after the committing transaction; a PDF failure never undoes the weighing (retried by the worker)."""
    try:
        return create_waybill_documents(actor, waybill_id, reason)
    except Exception:
        from flask import current_app
        current_app.logger.exception('PDF generation failed for waybill %s', waybill_id)
        return None


def _sheet_waybill(db, waybill_id, event):
    """Current state of one trip/waybill in the PAXTA-PUNKT sheet (upsert on the waybill number)."""
    r = db.execute('''SELECT wb.number, wb.document_date, wb.status, wb.net_kg, wb.arrived_at, tl.trip_no, t.code trailer,
                             f.name field, b.name brigadier, st.name station, w.basis, w.gross_kg, w.tare_kg, tl.internal_kg,
                             nr.accepted_kg, nr.diff_kg, nr.diff_reason, nr.created_at received_at
                      FROM waybills wb JOIN trailer_loads tl ON tl.id=wb.load_id JOIN equipment t ON t.id=tl.trailer_id
                      LEFT JOIN fields f ON f.id=tl.field_id LEFT JOIN brigadiers b ON b.id=tl.brigadier_id
                      LEFT JOIN stations st ON st.id=tl.station_id
                      LEFT JOIN weighings w ON w.load_id=tl.id LEFT JOIN nayman_receipts nr ON nr.waybill_id=wb.id
                      WHERE wb.id=?''', (waybill_id,)).fetchone()
    state = {'YARATILDI': 'KELDI' if r['arrived_at'] else 'YO‘LDA', 'QABUL': 'QABUL QILINDI', 'BEKOR': 'BEKOR'}[r['status']]
    pct = round(r['diff_kg'] / r['net_kg'] * 100, 2) if r['diff_kg'] is not None and r['net_kg'] else ''
    enqueue(db, 'sheets', 'upsert', f'wb:{waybill_id}:{secrets.token_hex(6)}',
            {'sheet': 'PAXTA-PUNKT', 'id': r['number'],
             'header': ['Nakladnoy', 'Telashka', 'Sana', 'Dala', 'Brigada', 'Punkt', 'Dala kg', 'Punkt kg', 'Farq kg',
                        'Farq %', 'Sabab', 'Holat', 'Oxirgi o‘zgarish'],
             'row': [r['number'], r['trip_no'] or r['trailer'], r['document_date'], r['field'], r['brigadier'],
                     r['station'] or '', r['net_kg'], r['accepted_kg'] if r['accepted_kg'] is not None else '',
                     r['diff_kg'] if r['diff_kg'] is not None else '', pct, r['diff_reason'] or '', state,
                     f'{now_str()} {event}']})


def _sheet_payment(db, payment_id, event):
    p = db.execute('''SELECT p.*, wb.number FROM payments p LEFT JOIN waybills wb ON wb.id=p.waybill_id WHERE p.id=?''',
                   (payment_id,)).fetchone()
    enqueue(db, 'sheets', 'upsert', f'pay:{payment_id}:{secrets.token_hex(6)}',
            {'sheet': 'NAYMAN TO‘LOVLARI', 'id': f'NAY-{p["season_year"]}-{p["id"]:06d}',
             'header': ['ID', 'Sana', 'Summa', 'Usul', 'To‘lovchi', 'Nakladnoy', 'Holat'],
             'row': [f'NAY-{p["season_year"]}-{p["id"]:06d}', p['payment_date'], p['amount'], p['method'] or '', p['payer'],
                     p['number'] or '', 'BEKOR: ' + p['void_reason'] if p['voided_at'] else 'OK']})
