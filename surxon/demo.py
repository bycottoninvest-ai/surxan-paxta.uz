"""Sample data for training / demonstration on an EMPTY database. Never run on production data."""
import random
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from .db import get_db, next_counter, tx
from .utils import name_key, now_str, today_str

WORKERS = ['Gulbahor opa', 'Sadoqat opa', 'Mirjalol', 'Asadbek', 'Aliyev Anvar', 'Karimov Bek', 'To‘xtayev Karim',
           'Saidov Sanjar', 'Normatov Akmal', 'Yusupov Diyor', 'Usmonov Valijon', 'Sodiqov Jamshid', 'Abdullayev Anvar',
           'Rahimova Dilnoza', 'Qodirova Malika', 'Ergashev Bobur', 'Tursunova Zulfiya', 'Xolmatov Sherzod']


def fill_demo(days=8, seed=7):
    db = get_db()
    if db.execute('SELECT 1 FROM trailer_loads LIMIT 1').fetchone():
        return 'Bazada allaqachon ish ma’lumotlari bor — demo yozilmadi.'
    rnd = random.Random(seed)
    today = date.fromisoformat(today_str())
    year = today.year
    with tx() as db:
        admin = db.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()['id']
        brig = {r['name']: r['id'] for r in db.execute('SELECT id, name FROM brigadiers')}
        demo_fields = [('D-01', 'Dala 1', 94.0, 'Nurim ota', [[42.335, 59.585], [42.341, 59.598], [42.333, 59.604], [42.328, 59.592]]),
                       ('D-02', 'Dala 2', 90.0, 'Bayram ota', [[42.322, 59.583], [42.328, 59.592], [42.321, 59.599], [42.315, 59.590]]),
                       ('D-03', 'Dala 3', 60.0, 'Juma ota', [[42.328, 59.604], [42.334, 59.615], [42.325, 59.620], [42.320, 59.609]]),
                       ('D-04', 'Dala 4', 66.2, 'Juma ota', [[42.313, 59.600], [42.320, 59.609], [42.312, 59.616], [42.306, 59.606]])]
        import json
        fids = {}
        for code, name, area, bname, poly in demo_fields:
            cur = db.execute('INSERT INTO fields(code, name, area_ha, brigadier_id, polygon_json, notes, created_at) VALUES (?,?,?,?,?,?,?)',
                             (code, name, area, brig[bname], json.dumps(poly), 'DEMO', now_str()))
            fids[code] = (cur.lastrowid, brig[bname])
        wids = []
        for n in WORKERS:
            cur = db.execute('INSERT INTO workers(full_name, name_key, created_at) VALUES (?,?,?)', (n, name_key(n), now_str()))
            wids.append(cur.lastrowid)
        eq = {r['code']: r['id'] for r in db.execute('SELECT id, code FROM equipment')}
        trailers = ['TL-01', 'TL-02', 'TL-03', 'TL-04']
        tractors = ['T-01', 'T-02', 'T-03']
        for code, uname, role, b in (('tarozi01', 'Tarozi xodimi', 'scale', None), ('juma', 'Juma ota', 'brigadier', 'Juma ota'),
                                     ('buxgalter', 'Buxgalter', 'accountant', None), ('asadbek', 'Asadbek', 'cashier', None),
                                     ('rahbar', 'Rahbar', 'manager', None)):
            db.execute('INSERT OR IGNORE INTO users(username, password_hash, full_name, role, brigadier_id, created_at) '
                       'VALUES (?,?,?,?,?,?)', (code, generate_password_hash('Demo2026!'), uname, role,
                                                brig.get(b) if b else None, now_str()))
        for back in range(days - 1, -1, -1):
            d = (today - timedelta(days=back)).isoformat()
            trips = rnd.randint(3, 5) if back else 3
            for t in range(trips):
                tcode = trailers[t % 4]
                fcode = rnd.choice(list(fids))
                fid, bid = fids[fcode]
                hour = 9 + t * 2
                stamp = f'{d} {hour:02d}:{rnd.randint(0, 20):02d}:00'   # TOLDI + brutto, always before tara (:45)
                last_today = back == 0 and t == trips - 1
                status = 'OCHIQ' if last_today else 'TORTILDI'
                cur = db.execute('''INSERT INTO trailer_loads(season_year, load_date, trailer_id, tractor_id, field_id, brigadier_id,
                                        vehicle_plate, driver_name, status, opened_by, opened_at, full_by, full_at)
                                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                                 (year, d, eq[tcode], eq[tractors[t % 3]], fid, bid, f'01 {rnd.randint(100, 999)} AAA',
                                  rnd.choice(['Karimov B.', 'Alimov A.', 'Saidov S.']), status, admin, stamp,
                                  None if last_today else admin, None if last_today else stamp))
                lid = cur.lastrowid
                hand = comb = 0
                for w in rnd.sample(wids, rnd.randint(6, 10)):
                    kg = float(rnd.randint(45, 115))
                    hand += kg
                    db.execute('''INSERT INTO harvests(season_year, work_date, load_id, worker_id, field_id, brigadier_id, trailer_id,
                                      tractor_id, method, kg, source, entered_by, created_at)
                                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                               (year, d, lid, w, fid, bid, eq[tcode], eq[tractors[t % 3]], 'hand', kg, 'demo', admin,
                                f'{d} {hour - 1:02d}:{rnd.randint(0, 59):02d}:00'))
                if rnd.random() < 0.6:
                    kg = float(rnd.randint(1500, 3200))
                    comb += kg
                    db.execute('''INSERT INTO harvests(season_year, work_date, load_id, field_id, brigadier_id, trailer_id, tractor_id,
                                      combine_id, method, kg, source, entered_by, created_at)
                                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                               (year, d, lid, fid, bid, eq[tcode], eq[tractors[t % 3]], eq[rnd.choice(['K-01', 'K-02'])],
                                'combine', kg, 'demo', admin, f'{d} {hour - 1:02d}:30:00'))
                db.execute('UPDATE trailer_loads SET hand_kg=?, combine_kg=?, internal_kg=? WHERE id=?', (hand, comb, hand + comb, lid))
                if back <= 1:
                    for act, et, eid, new in (('OPEN', 'trailer_load', lid, {'trailer': tcode}),
                                              ('TOLDI', 'trailer_load', lid, {'internal_kg': hand + comb})):
                        db.execute('INSERT INTO audit_logs(user_id, action, entity_type, entity_id, new_json, source, created_at) '
                                   'VALUES (?,?,?,?,?,?,?)', (admin, act, et, str(eid), json.dumps(new), 'demo', stamp))
                if last_today:
                    continue
                net = round((hand + comb) * rnd.uniform(0.985, 1.02))
                tare = float(rnd.randint(2400, 2800))
                wt = f'{d} {hour:02d}:45:00'
                db.execute('''INSERT INTO weighings(load_id, gross_kg, gross_at, gross_by, tare_kg, tare_at, tare_by, net_kg,
                                  internal_kg, diff_kg, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                           (lid, net + tare, stamp, admin, tare, wt, admin, net, hand + comb, net - hand - comb, 'YAKUNLANDI', stamp))
                db.execute('UPDATE trailer_loads SET weighed_at=? WHERE id=?', (wt, lid))
                seq = next_counter(db, 'waybill')
                wcur = db.execute('''INSERT INTO waybills(seq, number, season_year, load_id, net_kg, document_date, destination,
                                         status, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                                  (seq, f'PA-{seq:06d}', year, lid, net, d, 'Nayman paxta qabul punkti',
                                   'QABUL' if back else 'YARATILDI', admin, wt))
                if back <= 1:
                    wid_ = db.execute('SELECT id FROM weighings WHERE load_id=?', (lid,)).fetchone()[0]
                    for act, et, eid, new in (('TARE', 'weighing', wid_, {'net_kg': net}),
                                              ('CREATE', 'waybill', wcur.lastrowid, {'number': f'PA-{seq:06d}', 'net_kg': net})):
                        db.execute('INSERT INTO audit_logs(user_id, action, entity_type, entity_id, new_json, source, created_at) '
                                   'VALUES (?,?,?,?,?,?,?)', (admin, act, et, str(eid), json.dumps(new), 'demo', wt))
                if back:
                    acc = net - rnd.choice([0, 10, 20, 30])
                    db.execute('''INSERT INTO nayman_receipts(waybill_id, accepted_kg, diff_kg, diff_reason, received_date,
                                      receiver_name, price_per_kg, amount, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                               (wcur.lastrowid, acc, acc - net, 'Namlik / tabiiy kamayish' if acc != net else None, d,
                                'Nayman mas’uli', 7800, int(acc * 7800), admin, wt))
        # load waiting at the scale today
        d = today.isoformat()
        fid, bid = fids['D-03']
        cur = db.execute('''INSERT INTO trailer_loads(season_year, load_date, trailer_id, tractor_id, field_id, brigadier_id, status,
                                hand_kg, internal_kg, opened_by, opened_at, full_by, full_at)
                            VALUES (?,?,?,?,?,?,'TOLDI',?,?,?,?,?,?)''',
                         (year, d, eq['TL-04'], eq['T-03'], fid, bid, 0, 0, admin, f'{d} 13:10:00', admin, f'{d} 14:20:00'))
        db.execute("INSERT INTO cash_entries(season_year, entry_date, direction, category, amount, note, created_by, created_at) "
                   "VALUES (?,?,?,?,?,?,?,?)", (year, (today - timedelta(days=days)).isoformat(), 'IN', 'opening', 25_000_000,
                                                'DEMO boshlang‘ich qoldiq', admin, now_str()))
        # a few sample photos (cut from the approved design images) so the archive isn't empty
        from pathlib import Path
        from flask import current_app
        from .photos import store_photo
        from .security import Actor
        img = Path(current_app.static_folder) / 'img'
        recent = db.execute("SELECT id, field_id, brigadier_id, season_year FROM trailer_loads ORDER BY id DESC LIMIT 4").fetchall()
        for (lid, fid, bid, yr), (fname, cat) in zip(recent, [('trailer.jpg', 'trailer'), ('hero-field.jpg', 'field'),
                                                             ('sidebar-cotton.jpg', 'cotton'), ('field-thumb.jpg', 'field')]):
            store_photo(db, Actor(admin, 'admin', source='demo'), (img / fname).read_bytes(), category=cat, entity_type='trailer_load',
                        entity_id=lid, caption='DEMO', source='demo',
                        links={'season_year': yr, 'load_id': lid, 'field_id': fid, 'brigadier_id': bid})
        for key, val in (('price_per_kg', '7800'), ('worker_rate_hand', '1500'), ('combine_rate', '1500')):
            db.execute("INSERT INTO settings(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO NOTHING",
                       (key, val, now_str()))
    return 'Demo ma’lumotlar yozildi. Demo loginlar paroli: Demo2026!'
