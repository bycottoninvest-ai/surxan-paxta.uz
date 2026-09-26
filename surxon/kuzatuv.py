"""Kuzatuv: the system asks people for photos/videos through the Telegram bot and shows the answers to the manager.

People ("members") are tractor drivers, brigadiers, agronomists... — they need no login, only Telegram.
They get into the list three ways:
  1. admin/manager adds a name and sends the person a link (t.me/<bot>?start=K-XXXXXX);
  2. someone joins the work group (or writes in it) — registered automatically;
  3. a system user links their Telegram account — becomes a member with their role.

A request (media_requests) goes to the person privately if they have opened the bot, otherwise into the work group
with a mention. The photo/video they send back (reply, or simply the next one while a request is open) is stored
on the server (media_items) and shown on the /kuzatuv page. Scheduled rules (media_rules) create requests
automatically, e.g. every day at 09:00 and 16:00. Unanswered requests turn KECHIKDI after the deadline.
"""
import json
import secrets
from datetime import datetime, timedelta

from flask import current_app

from .db import get_db, q, tx
from .photos import process_image
from .security import audit
from .settings import get_bool, get_setting
from .utils import UserError, clean_text, now, now_str

STATUSES = {'KUTILMOQDA': 'Kutilmoqda', 'JAVOB': 'Javob keldi', 'KECHIKDI': 'Kechikdi', 'BEKOR': 'Bekor qilindi'}
KINDS = {'any': 'Rasm yoki video', 'photo': 'Rasm', 'video': 'Video'}
TEMPLATES = [
    'traktor ishlayotgan rasm yoki videoni yuboring',
    'paxtaning hozirgi holati (dala) rasmini yuboring',
    'dalaning umumiy ko‘rinishini video qilib yuboring',
    'terimchilar ishlayotgan joyni rasmga olib yuboring',
    'telashka yuklanishini rasmga olib yuboring',
    'kombayn ishlayotgan videoni yuboring',
]
VIDEO_TYPES = {'video/mp4': 'mp4', 'video/quicktime': 'mov', 'video/webm': 'webm', 'video/3gpp': '3gp'}
MAX_DOWNLOAD = 20 * 1024 * 1024       # Telegram Bot API getFile limit
OPEN = ('KUTILMOQDA', 'KECHIKDI')


def row_dict(row):
    return dict(row) if row is not None else None


def _bot():
    # resolved at call time so tests (and the webhook) share the one patched Telegram client
    from . import telegram_bot
    return telegram_bot


def _code():
    return 'K-' + ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))


def tg_name(frm):
    name = ' '.join(x for x in (frm.get('first_name'), frm.get('last_name')) if x).strip()
    return clean_text(name or frm.get('username') or f'Telegram {frm.get("id")}', 80)


def invite_link(member):
    user = current_app.config['SURXON'].TELEGRAM_BOT_USERNAME
    if not member['link_code']:
        return None
    return f'https://t.me/{user}?start={member["link_code"]}' if user else None


# ------------------------------------------------------------------ members

def save_member(actor, mid, *, full_name, role_label='', status=None, equipment_id=None, field_id=None, user_id=None):
    full_name = clean_text(full_name, 80)
    if len(full_name) < 2:
        raise UserError('Ism kiritilishi shart.')
    role_label = clean_text(role_label, 40)
    if status and status not in ('YANGI', 'FAOL', 'NOFAOL'):
        raise UserError('Holat noto‘g‘ri.')
    import sqlite3
    with tx() as db:
        try:
            if mid:
                old = db.execute('SELECT * FROM tg_members WHERE id=?', (mid,)).fetchone()
                if not old:
                    raise UserError('Odam topilmadi.')
                db.execute('''UPDATE tg_members SET full_name=?, role_label=?, status=?, equipment_id=?, field_id=?, user_id=?
                              WHERE id=?''', (full_name, role_label, status or old['status'], equipment_id, field_id,
                                             user_id, mid))
                audit(db, actor, 'UPDATE', 'tg_member', mid, old=row_dict(old),
                      new={'full_name': full_name, 'role_label': role_label, 'status': status or old['status']})
                return mid
            cur = db.execute('''INSERT INTO tg_members(full_name, role_label, status, source, link_code, equipment_id,
                                    field_id, user_id, created_at) VALUES (?,?,'FAOL','admin',?,?,?,?,?)''',
                             (full_name, role_label, _code(), equipment_id, field_id, user_id, now_str()))
            audit(db, actor, 'CREATE', 'tg_member', cur.lastrowid, new={'full_name': full_name, 'role_label': role_label})
            return cur.lastrowid
        except sqlite3.IntegrityError:
            raise UserError('Bu tizim foydalanuvchisi boshqa odamga bog‘langan.')


def new_link_code(actor, mid):
    with tx() as db:
        code = _code()
        db.execute('UPDATE tg_members SET link_code=? WHERE id=?', (code, mid))
        audit(db, actor, 'UPDATE', 'tg_member', mid, new={'link_code': 'yangi'})
    return code


def seen(db, frm, chat=None, *, private=False):
    """Register (or refresh) whoever the bot sees. Returns the member row, or None for bots."""
    if not frm or frm.get('is_bot'):
        return None
    tid = str(frm['id'])
    m = db.execute('SELECT * FROM tg_members WHERE telegram_id=?', (tid,)).fetchone()
    work = None
    if chat and chat.get('type') in ('group', 'supergroup'):
        work = db.execute('SELECT is_work FROM tg_chats WHERE chat_id=?', (str(chat['id']),)).fetchone()
    if m:
        sets, params = ['last_seen_at=?', 'username=COALESCE(?, username)'], [now_str(), frm.get('username')]
        if private:
            sets.append('dm_ok=1')
        if work is not None:
            sets.append('group_chat_id=?')
            params.append(str(chat['id']))
            if work['is_work'] and m['status'] == 'YANGI' and m['source'] == 'guruh':
                sets.append("status='FAOL'")
        db.execute(f'UPDATE tg_members SET {", ".join(sets)} WHERE id=?', (*params, m['id']))
        return db.execute('SELECT * FROM tg_members WHERE id=?', (m['id'],)).fetchone()
    user = db.execute('SELECT id, full_name, role FROM users WHERE telegram_id=? AND active=1', (tid,)).fetchone()
    if user:
        from .security import ROLES
        status, source, name, role = 'FAOL', 'tizim', user['full_name'], ROLES.get(user['role'], user['role'])
    elif work is not None:
        # in the work group everyone is staff; a group the admin has not confirmed yet waits for approval
        status, source, name, role = ('FAOL' if work['is_work'] else 'YANGI'), 'guruh', tg_name(frm), ''
    else:
        status, source, name, role = 'YANGI', 'bot', tg_name(frm), ''
    cur = db.execute('''INSERT INTO tg_members(full_name, role_label, telegram_id, username, user_id, status, source, dm_ok,
                            group_chat_id, created_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                     (name, role, tid, frm.get('username'), user['id'] if user else None, status, source,
                      1 if private else 0, str(chat['id']) if work is not None else None, now_str(), now_str()))
    audit(db, None, 'CREATE', 'tg_member', cur.lastrowid, new={'full_name': name, 'status': status, 'source': source})
    return db.execute('SELECT * FROM tg_members WHERE id=?', (cur.lastrowid,)).fetchone()


def link_by_code(db, code, frm):
    """/start K-XXXXXX from the invite link: the named member row gets this Telegram account."""
    m = db.execute('SELECT * FROM tg_members WHERE link_code=?', (code.strip().upper(),)).fetchone()
    if not m:
        return None
    tid = str(frm['id'])
    other = db.execute('SELECT * FROM tg_members WHERE telegram_id=? AND id<>?', (tid, m['id'])).fetchone()
    if other:
        # the person wrote to the bot / group before using the link: keep their history on the named row
        db.execute('UPDATE tg_members SET telegram_id=NULL WHERE id=?', (other['id'],))
        db.execute('UPDATE media_items SET member_id=? WHERE member_id=?', (m['id'], other['id']))
        db.execute('UPDATE media_requests SET member_id=? WHERE member_id=?', (m['id'], other['id']))
        if other['status'] != 'NOFAOL':
            db.execute("UPDATE tg_members SET status='NOFAOL' WHERE id=?", (other['id'],))
    db.execute('''UPDATE tg_members SET telegram_id=?, username=?, link_code=NULL, dm_ok=1, last_seen_at=?,
                  status=CASE WHEN status='NOFAOL' THEN 'NOFAOL' ELSE 'FAOL' END WHERE id=?''',
               (tid, frm.get('username'), now_str(), m['id']))
    audit(db, None, 'TG_LINK', 'tg_member', m['id'], new={'telegram_id': tid})
    return db.execute('SELECT * FROM tg_members WHERE id=?', (m['id'],)).fetchone()


def link_system_user(db, user):
    """A system user who linked Telegram (/start CODE) is also a kuzatuv member."""
    from .security import ROLES
    tid = str(user['telegram_id'])
    m = db.execute('SELECT * FROM tg_members WHERE user_id=? OR telegram_id=? ORDER BY user_id IS NULL LIMIT 1',
                   (user['id'], tid)).fetchone()
    if m:
        db.execute('UPDATE tg_members SET telegram_id=NULL WHERE telegram_id=? AND id<>?', (tid, m['id']))
        db.execute('''UPDATE tg_members SET telegram_id=?, user_id=?, dm_ok=1, last_seen_at=?,
                      status=CASE WHEN status='YANGI' THEN 'FAOL' ELSE status END,
                      role_label=COALESCE(NULLIF(role_label,''), ?) WHERE id=?''',
                   (tid, user['id'], now_str(), ROLES.get(user['role'], ''), m['id']))
        return
    db.execute('''INSERT INTO tg_members(full_name, role_label, telegram_id, user_id, status, source, dm_ok, created_at,
                      last_seen_at) VALUES (?,?,?,?, 'FAOL', 'tizim', 1, ?, ?)''',
               (user['full_name'], ROLES.get(user['role'], ''), tid, user['id'], now_str(), now_str()))


def chat_seen(db, chat, bot_status=None):
    """Remember a group or channel by id and title only. Channels are recorded so the admin can pick the archive and
    report channel on the Integrations page; nothing posted in them is stored."""
    if chat.get('type') not in ('group', 'supergroup', 'channel'):
        return None
    cid = str(chat['id'])
    db.execute('''INSERT INTO tg_chats(chat_id, title, type, first_seen_at, last_seen_at) VALUES (?,?,?,?,?)
                  ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, type=excluded.type, last_seen_at=excluded.last_seen_at''',
               (cid, clean_text(chat.get('title') or '', 120), chat.get('type'), now_str(), now_str()))
    if bot_status is not None:
        db.execute('UPDATE tg_chats SET bot_status=? WHERE chat_id=?', (bot_status, cid))
    return db.execute('SELECT * FROM tg_chats WHERE chat_id=?', (cid,)).fetchone()


def set_work_group(actor, chat_id, on):
    with tx() as db:
        chat = db.execute('SELECT * FROM tg_chats WHERE chat_id=?', (str(chat_id),)).fetchone()
        if not chat:
            raise UserError('Guruh topilmadi.')
        db.execute('UPDATE tg_chats SET is_work=? WHERE chat_id=?', (1 if on else 0, chat['chat_id']))
        n = 0
        if on:
            n = db.execute("UPDATE tg_members SET status='FAOL' WHERE status='YANGI' AND source='guruh' AND group_chat_id=?",
                           (chat['chat_id'],)).rowcount
        audit(db, actor, 'UPDATE', 'tg_chat', chat['chat_id'], new={'is_work': bool(on), 'approved_members': n})
        return n


def work_chat_ids(db=None):
    return [r['chat_id'] for r in (db or get_db()).execute('SELECT chat_id FROM tg_chats WHERE is_work=1')]


# ------------------------------------------------------------------ requests

def _deadline(minutes):
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = int(get_setting('kuzatuv_deadline_min') or 120)
    return max(10, min(minutes, 24 * 60))


def create_requests(actor, member_ids, text, *, kind='any', deadline_min=None, rule_id=None, slot=None, db=None,
                    context=None, event=None, batch=None):
    """Insert one request per member (inside a transaction) — sending happens after commit (deliver)."""
    text = clean_text(text, 300)
    if len(text) < 3:
        raise UserError('So‘rov matnini yozing (masalan: “traktor ishlayotgan rasmni yuboring”).')
    if kind not in KINDS:
        kind = 'any'
    ids = sorted({int(m) for m in member_ids if str(m).isdigit()})
    if not ids:
        raise UserError('Kamida bitta odamni tanlang.')
    mins = _deadline(deadline_min)
    created = []

    def run(db):
        for mid in ids:
            m = db.execute("SELECT * FROM tg_members WHERE id=? AND status='FAOL'", (mid,)).fetchone()
            if not m:
                continue
            ts = now()
            cur = db.execute('''INSERT OR IGNORE INTO media_requests(member_id, text, kind, rule_id, slot, requested_by,
                                    created_at, due_at, context, event, batch) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                             (mid, text, kind, rule_id, slot, actor.user_id if actor else None,
                              ts.strftime('%Y-%m-%d %H:%M:%S'), (ts + timedelta(minutes=mins)).strftime('%Y-%m-%d %H:%M:%S'),
                              context, clean_text(event or '', 80) or None, batch))
            if cur.rowcount:
                created.append(cur.lastrowid)
                audit(db, actor, 'CREATE', 'media_request', cur.lastrowid,
                      new={'member_id': mid, 'text': text, 'kind': kind, 'rule_id': rule_id, 'slot': slot})
    if db is not None:
        run(db)
    else:
        with tx() as d:
            run(d)
    if not created and not rule_id:
        raise UserError('Tanlangan odamlar faol emas (ro‘yxatda “FAOL” holatida bo‘lishi kerak).')
    return created


def _mention(m):
    from html import escape
    if m['telegram_id']:
        return f'<a href="tg://user?id={int(m["telegram_id"])}">{escape(m["full_name"])}</a>'
    return escape(m['full_name'])


def request_message(r, m, reminder=False):
    from html import escape
    icon = {'video': '🎥', 'photo': '📷'}.get(r['kind'], '📸')
    head = '⏰ ESLATMA · ' if reminder else ''
    return (f'{head}{icon} {_mention(m)}, {escape(r["text"])}\n'
            f'<i>So‘rov №{r["id"]} · {r["due_at"][11:16]} gacha. Shu xabarga javob qilib (reply) yuboring.</i>')


def deliver(req_id, reminder=False):
    """Send one request: privately if possible, else into the work group with a mention. Records where it went."""
    db = get_db()
    r = db.execute('SELECT * FROM media_requests WHERE id=?', (req_id,)).fetchone()
    if not r or r['status'] not in OPEN:
        return False
    m = db.execute('SELECT * FROM tg_members WHERE id=?', (r['member_id'],)).fetchone()
    bot = _bot()
    if not current_app.config['SURXON'].TELEGRAM_BOT_TOKEN:
        err = 'Telegram bot ulanmagan (TELEGRAM_BOT_TOKEN yo‘q)'
        with tx(db):
            db.execute('UPDATE media_requests SET send_error=? WHERE id=?', (err, req_id))
        return False
    text = request_message(r, m, reminder)
    targets = []
    if m['telegram_id'] and m['dm_ok']:
        targets.append(('dm', m['telegram_id']))
    works = work_chat_ids(db)
    group = m['group_chat_id'] if m['group_chat_id'] in works else (works[0] if works else None)
    if group and m['telegram_id']:
        targets.append(('group', group))
    if not targets:
        err = ('Odam hali botni ochmagan va ishchi guruhda ko‘rinmagan' if m['telegram_id']
               else 'Odam Telegramga ulanmagan — unga taklif havolasini yuboring')
        with tx(db):
            db.execute('UPDATE media_requests SET send_attempts=send_attempts+1, send_error=? WHERE id=?', (err, req_id))
        return False
    last_err = None
    for via, chat in targets:
        try:
            res = bot.tg_api('sendMessage', {'chat_id': chat, 'text': text, 'parse_mode': 'HTML',
                                             'disable_web_page_preview': True})
            if not res.get('ok', True):
                raise RuntimeError(res.get('description') or 'Telegram xatosi')
        except Exception as exc:  # blocked the bot, left the group, network — try the next way
            last_err = str(exc)[:300]
            continue
        msg_id = (res.get('result') or {}).get('message_id')
        with tx(db):
            if reminder:
                db.execute('UPDATE media_requests SET reminded_at=?, sent_via=?, chat_id=?, tg_message_id=?, send_error=NULL '
                           'WHERE id=?', (now_str(), via, str(chat), msg_id, req_id))
            else:
                db.execute('''UPDATE media_requests SET sent_at=?, sent_via=?, chat_id=?, tg_message_id=?, send_error=NULL,
                              send_attempts=send_attempts+1 WHERE id=?''', (now_str(), via, str(chat), msg_id, req_id))
        return True
    with tx(db):
        db.execute('UPDATE media_requests SET send_attempts=send_attempts+1, send_error=? WHERE id=?', (last_err, req_id))
    return False


def remind(actor, req_id):
    r = q('SELECT * FROM media_requests WHERE id=?', (req_id,), one=True)
    if not r or r['status'] not in OPEN:
        raise UserError('So‘rov yopilgan.')
    if not deliver(req_id, reminder=True):
        r = q('SELECT send_error FROM media_requests WHERE id=?', (req_id,), one=True)
        raise UserError(f'Eslatma yuborilmadi: {r["send_error"] or "Telegram xatosi"}')
    with tx() as db:
        audit(db, actor, 'REMIND', 'media_request', req_id)


def cancel(actor, req_id):
    with tx() as db:
        r = db.execute('SELECT * FROM media_requests WHERE id=?', (req_id,)).fetchone()
        if not r or r['status'] not in OPEN:
            raise UserError('So‘rov allaqachon yopilgan.')
        db.execute("UPDATE media_requests SET status='BEKOR' WHERE id=?", (req_id,))
        audit(db, actor, 'CANCEL', 'media_request', req_id, old={'status': r['status']})


def deliver_pending(limit=30, member_id=None):
    """Send requests that were not delivered yet (bot was down, person not reachable yet). Retries back off:
    1, 2, 4 … minutes after the request was made, up to 10 tries (~17 hours); linking the person sends at once."""
    where, params = '', []
    if member_id:
        where, params = ' AND member_id=?', [member_id]
    rows = q(f"""SELECT id, created_at, send_attempts FROM media_requests WHERE status IN ('KUTILMOQDA','KECHIKDI')
                 AND sent_at IS NULL AND send_attempts < 10{where} ORDER BY id LIMIT ?""", (*params, limit))
    cur = now().replace(tzinfo=None)
    sent = 0
    for r in rows:
        age = (cur - datetime.strptime(r['created_at'], '%Y-%m-%d %H:%M:%S')).total_seconds() / 60
        if member_id or age >= 2 ** r['send_attempts'] - 1:
            sent += bool(deliver(r['id']))
    return sent


def mark_late():
    """Past the deadline without an answer → KECHIKDI, and the bot reminds the person once. If there is still nothing
    after the grace time, ONE grouped alert goes to the report channel and the managers: “3 kishi hali yubormadi”."""
    with tx() as db:
        rows = db.execute("""SELECT * FROM media_requests WHERE status='KUTILMOQDA' AND due_at < ?""", (now_str(),)).fetchall()
        if rows:
            db.execute("UPDATE media_requests SET status='KECHIKDI' WHERE status='KUTILMOQDA' AND due_at < ?", (now_str(),))
    if rows and get_bool('kuzatuv_auto_remind'):
        for r in rows:
            if not r['reminded_at'] and r['sent_at']:
                deliver(r['id'], reminder=True)
    grace = int(float(get_setting('kuzatuv_grace_min') or 30))
    limit = (now().replace(tzinfo=None) - timedelta(minutes=grace)).strftime('%Y-%m-%d %H:%M:%S')
    text = None
    with tx() as db:
        due = db.execute("""SELECT r.*, m.full_name, m.role_label FROM media_requests r JOIN tg_members m ON m.id=r.member_id
                            WHERE r.status='KECHIKDI' AND r.late_alerted=0 AND COALESCE(r.reminded_at, r.due_at) < ?
                            ORDER BY r.id""", (limit,)).fetchall()
        if due:
            db.execute(f"UPDATE media_requests SET late_alerted=1 WHERE id IN ({','.join('?' * len(due))})", [r['id'] for r in due])
            if get_bool('kuzatuv_late_alert', db):
                from .reporting import feed
                lines = [f'• {r["full_name"]}' + (f' ({r["role_label"]})' if r['role_label'] else '') + f' — “{r["text"]}”'
                         f' · {r["created_at"][11:16]} da so‘ralgan' for r in due[:15]]
                text = (f'⏰ Kuzatuv: {len(due)} kishi hali rasm/video yubormadi (eslatmadan keyin ham)\n' + '\n'.join(lines)
                        + (f'\n… va yana {len(due) - 15} kishi' if len(due) > 15 else ''))
                feed(db, f'late:{due[0]["id"]}-{due[-1]["id"]}', text)
    if text:
        _bot().notify_async(text)
    return len(rows)


def auto_idle_requests():
    """A machine standing long in working hours → its driver is asked for a video once per stop (setting kuzatuv_auto_idle)."""
    if not get_bool('kuzatuv_auto_idle'):
        return 0
    from . import fleet
    made = []
    for m in fleet.live():
        if not m['alert'] or m['state'] not in ('idle', 'parked') or not m.get('since'):
            continue
        ctx = f'idle:{m["id"]}:{m["since"]}'
        if q('SELECT 1 FROM media_requests WHERE context=?', (ctx,), one=True):
            continue
        people = [r['id'] for r in q("SELECT id FROM tg_members WHERE status='FAOL' AND equipment_id=?", (m['id'],))]
        if not people:
            continue
        with tx() as db:
            made += create_requests(None, people, f'{m["code"]} {m["still_min"]} daqiqadan beri bir joyda turibdi. '
                                                  'Texnika va joyni ko‘rsatib qisqa video yuboring.',
                                    kind='video', deadline_min=30, db=db, context=ctx, event='Texnika uzoq turibdi')
    for rid in made:
        deliver(rid)
    return len(made)


# ------------------------------------------------------------------ rules (schedule)

def parse_times(raw):
    out = []
    for part in (raw or '').replace(';', ',').replace(' ', ',').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            t = datetime.strptime(part, '%H:%M')
        except ValueError:
            raise UserError(f'Vaqt noto‘g‘ri: “{part}”. Masalan: 09:00, 16:30')
        out.append(t.strftime('%H:%M'))
    if not out:
        raise UserError('Kamida bitta vaqt kiriting (masalan 09:00).')
    return ','.join(sorted(set(out)))


def save_rule(actor, rid, *, title, text, kind, times, weekdays, member_ids, deadline_min, active=True):
    title = clean_text(title, 60) or clean_text(text, 60)
    text = clean_text(text, 300)
    if len(text) < 3:
        raise UserError('So‘rov matnini yozing.')
    times = parse_times(times)
    weekdays = ''.join(sorted({d for d in (weekdays or '') if d in '1234567'})) or '1234567'
    ids = sorted({int(m) for m in member_ids if str(m).isdigit()})
    if not ids:
        raise UserError('Kamida bitta odamni tanlang.')
    kind = kind if kind in KINDS else 'any'
    mins = _deadline(deadline_min)
    with tx() as db:
        vals = (title, text, kind, times, weekdays, json.dumps(ids), mins, 1 if active else 0)
        if rid:
            old = db.execute('SELECT * FROM media_rules WHERE id=?', (rid,)).fetchone()
            if not old:
                raise UserError('Jadval topilmadi.')
            db.execute('''UPDATE media_rules SET title=?, text=?, kind=?, times=?, weekdays=?, members_json=?, deadline_min=?,
                          active=? WHERE id=?''', (*vals, rid))
            audit(db, actor, 'UPDATE', 'media_rule', rid, old=row_dict(old), new=dict(zip(
                ('title', 'text', 'kind', 'times', 'weekdays', 'members', 'deadline_min', 'active'), vals)))
            return rid
        cur = db.execute('''INSERT INTO media_rules(title, text, kind, times, weekdays, members_json, deadline_min, active,
                                created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)''', (*vals, actor.user_id, now_str()))
        audit(db, actor, 'CREATE', 'media_rule', cur.lastrowid, new={'title': title, 'times': times, 'members': ids})
        return cur.lastrowid


CATCH_UP_MIN = 90   # after a restart, a missed time is still asked if it is at most this old (never old days)


def run_rules(at=None):
    """Worker: for each active rule and each of its times that has come today, create the requests once."""
    at = at or now()
    day, hm, wd = at.strftime('%Y-%m-%d'), at.strftime('%H:%M'), str(at.isoweekday())
    new = []
    for rule in q('SELECT * FROM media_rules WHERE active=1'):
        if wd not in rule['weekdays']:
            continue
        for t in rule['times'].split(','):
            if not (t <= hm):
                continue
            fire = datetime.strptime(f'{day} {t}', '%Y-%m-%d %H:%M').replace(tzinfo=at.tzinfo)
            if (at - fire).total_seconds() > CATCH_UP_MIN * 60:
                continue
            with tx() as db:
                new += create_requests(None, json.loads(rule['members_json']), rule['text'], kind=rule['kind'],
                                       deadline_min=rule['deadline_min'], rule_id=rule['id'], slot=f'{day} {t}', db=db)
    for rid in new:
        deliver(rid)
    return len(new)


def tick():
    """Called from the outbox worker loop every ~30 s."""
    made = run_rules()
    try:
        made += auto_idle_requests()
    except Exception as exc:      # GPS part must never stop the kuzatuv loop
        current_app.logger.warning('auto idle requests: %s', exc)
    sent = deliver_pending()
    late = mark_late()
    return {'rule_requests': made, 'delivered': sent, 'late': late}


# ------------------------------------------------------------------ incoming media

def open_request_for(db, member, chat_id=None, reply_to=None):
    if reply_to and chat_id is not None:
        r = db.execute('SELECT * FROM media_requests WHERE chat_id=? AND tg_message_id=?', (str(chat_id), reply_to)).fetchone()
        if r and r['member_id'] == member['id'] and r['status'] != 'BEKOR':
            return r
    return db.execute(f"""SELECT * FROM media_requests WHERE member_id=? AND status IN ('KUTILMOQDA','KECHIKDI')
                          ORDER BY id LIMIT 1""", (member['id'],)).fetchone()


def media_of(message):
    """('photo'|'video', file dict, thumb dict|None, mime) or None."""
    if message.get('photo'):
        return 'photo', message['photo'][-1], None, 'image/jpeg'
    for key in ('video', 'video_note'):
        if message.get(key):
            v = message[key]
            return 'video', v, v.get('thumbnail') or v.get('thumb'), v.get('mime_type') or 'video/mp4'
    doc = message.get('document')
    if doc:
        mime = doc.get('mime_type') or ''
        if mime.startswith('video/'):
            return 'video', doc, doc.get('thumbnail') or doc.get('thumb'), mime
        if mime in ('image/jpeg', 'image/png', 'image/webp'):
            return 'photo', doc, None, mime
    return None


def _write(sub, name, data):
    cfg = current_app.config['SURXON']
    folder = cfg.UPLOAD_DIR / sub
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return f'{sub}/{name}'


def save_incoming(member, message, request=None):
    """Download and store one photo/video from Telegram. Idempotent on Telegram's file_unique_id."""
    got = media_of(message)
    if not got:
        return None
    kind, f, thumb, mime = got
    uniq = f.get('file_unique_id')
    db = get_db()
    if uniq:
        dup = db.execute('SELECT id FROM media_items WHERE tg_file_unique_id=?', (uniq,)).fetchone()
        if dup:
            return dup['id']
    bot = _bot()
    sub = 'kuzatuv/' + now().strftime('%Y/%m')
    base = f"{now().strftime('%Y%m%d_%H%M%S')}_{kind}_{secrets.token_hex(5)}"
    path = thumb_path = note = None
    size = f.get('file_size')
    if kind == 'photo':
        main, small, meta = process_image(bot.tg_download(f['file_id']))
        path = _write(sub, f'{base}.jpg', main)
        thumb_path = _write(sub, f'{base}_t.jpg', small)
        size = len(main)
    else:
        if size and size > MAX_DOWNLOAD:
            note = f'Video {size / 1048576:.0f} MB — 20 MB dan katta, serverga yuklab bo‘lmadi; asli Telegram arxivida.'
        else:
            try:
                data = bot.tg_download(f['file_id'])
                path = _write(sub, f'{base}.{VIDEO_TYPES.get(mime, "mp4")}', data)
                size = len(data)
            except UserError as exc:
                note = str(exc)
        if thumb:
            try:
                _main, small, _meta = process_image(bot.tg_download(thumb['file_id']))
                thumb_path = _write(sub, f'{base}_t.jpg', small)
            except Exception:
                thumb_path = None
    loc = message.get('location') or {}
    field_id, equipment_id, lat, lon = place_of(db, member, request, loc)
    with tx(db):
        cur = db.execute('''INSERT OR IGNORE INTO media_items(request_id, member_id, kind, path, thumb_path, size_bytes, duration_s,
                                caption, lat, lon, chat_id, tg_message_id, tg_file_id, tg_file_unique_id, note, created_at,
                                field_id, equipment_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (request['id'] if request else None, member['id'], kind, path, thumb_path, size, f.get('duration'),
                          clean_text(message.get('caption') or '', 300), lat, lon,
                          str(message['chat']['id']), message.get('message_id'), f.get('file_id'), uniq, note, now_str(),
                          field_id, equipment_id))
        if not cur.rowcount:
            return None
        mid = cur.lastrowid
        if request and request['status'] in OPEN:
            db.execute("UPDATE media_requests SET status='JAVOB', answered_at=? WHERE id=?", (now_str(), request['id']))
        label = f'{member["full_name"]}' + (f' ({member["role_label"]})' if member['role_label'] else '')
        caption = (f'{"🎥" if kind == "video" else "📷"} Kuzatuv · {label} · {now_str()[:16]}'
                   + (f'\nSo‘rov: {request["text"]}' if request else '') + (f'\n{message.get("caption")}' if message.get('caption') else ''))
        from .outbox import enqueue
        if path:
            enqueue(db, 'telegram_archive', kind, f'kuzatuv:{mid}', {'path': path, 'caption': caption})
        else:
            enqueue(db, 'telegram_archive', 'copy', f'kuzatuv:{mid}',
                    {'from_chat_id': message['chat']['id'], 'message_id': message.get('message_id'), 'caption': caption})
    return mid


def place_of(db, member, request, loc):
    """Where a photo/video belongs: the request's field/machine, else the person's own, else where their live location
    (Telegram / app, last 30 min) puts them. GPS: from the message, else that live location."""
    field_id = equipment_id = None
    ctx = (request['context'] if request and 'context' in request.keys() else None) or ''
    kind, _, val = ctx.partition(':')
    if kind == 'field' and val.isdigit():
        field_id = int(val)
    elif kind in ('equipment', 'idle') and val.split(':')[0].isdigit():
        equipment_id = int(val.split(':')[0])
    equipment_id = equipment_id or member['equipment_id']
    lat, lon = loc.get('latitude'), loc.get('longitude')
    if lat is None:
        since = (now().replace(tzinfo=None) - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
        pos = db.execute('''SELECT lat, lon FROM staff_positions WHERE (member_id=? OR (user_id IS NOT NULL AND user_id=?))
                             AND at>=? ORDER BY id DESC LIMIT 1''', (member['id'], member['user_id'] or -1, since)).fetchone()
        if pos:
            lat, lon = pos['lat'], pos['lon']
    if not field_id and lat is not None:
        from .fleet import field_at
        f = field_at(lat, lon)
        field_id = f[0] if f else None
    return field_id or member['field_id'], equipment_id, lat, lon


# ------------------------------------------------------------------ asking a whole group at once

GROUPS = {   # key: (label, words in the role label, system roles, equipment kind)
    'agronom': ('Barcha agronomlar', ('agronom',), (), None),
    'brigadir': ('Barcha brigadirlar', ('brigadir',), ('brigadier',), None),
    'traktor': ('Barcha traktor haydovchilari', ('traktor', 'haydovchi', 'mexanizator'), ('driver',), 'traktor'),
    'kombayn': ('Barcha kombaynchilar', ('kombayn',), (), 'kombayn'),
    'hisobchi': ('Terim hisobchilari', ('hisobchi',), ('tally',), None),
    'tarozi': ('Tarozi xodimlari', ('tarozi',), ('scale',), None),
    'punkt': ('Punkt xodimlari', ('punkt',), ('station',), None),
    'yoqilgi': ('Yoqilg‘i mas’ullari', ('yoqilg', 'solyarka'), ('fuel',), None),
}
GROUP_TEXT = {
    'agronom': 'Hozir dalada nima bo‘layapti? 20–30 soniyalik video yuboring.',
    'brigadir': 'Terim jarayonidan rasm yoki video yuboring.',
    'traktor': 'Texnika va bajarilayotgan ishni ko‘rsatib video yuboring.',
    'kombayn': 'Kombaynning hozirgi ish holatini video qilib yuboring.',
    'hisobchi': 'Terim va telashkaning hozirgi holatini rasmga olib yuboring.',
    'tarozi': 'Tarozi va kelgan telashkani rasmga olib yuboring.',
    'punkt': 'Qabul jarayonidan rasm yoki video yuboring.',
    'yoqilgi': 'Solyarka berilayotgan texnikani rasmga olib yuboring.',
}
QUICK = {
    'holat': 'Hozirgi holatdan rasm yoki qisqa video yuboring.',
    'terim': 'Terim jarayonini ko‘rsatib rasm yoki video yuboring.',
    'texnika': 'Texnika va bajarilayotgan ishni ko‘rsatib video yuboring.',
    'muammo': 'Muammo bo‘lsa, uni ko‘rsatib rasm yoki video yuboring.',
    'video30': '20–30 soniyalik video yuboring.',
}
QUICK_LABELS = {'holat': 'Hozirgi holatni yuboring', 'terim': 'Terimni ko‘rsating', 'texnika': 'Texnika ishini ko‘rsating',
                'muammo': 'Muammoni ko‘rsating', 'video30': '30 soniyalik video yuboring'}


def _in_group(m, key, user_roles, eq_kinds):
    label, words, roles, eq_kind = GROUPS[key]
    rl = (m['role_label'] or '').lower()
    return (any(w in rl for w in words) or (m['user_id'] and user_roles.get(m['user_id']) in roles)
            or (eq_kind and m['equipment_id'] and eq_kinds.get(m['equipment_id']) == eq_kind))


def group_members(key):
    user_roles = {r['id']: r['role'] for r in q('SELECT id, role FROM users')}
    eq_kinds = {r['id']: r['kind'] for r in q('SELECT id, kind FROM equipment')}
    return [m for m in q("SELECT * FROM tg_members WHERE status='FAOL' ORDER BY full_name") if _in_group(m, key, user_roles, eq_kinds)]


def field_members(field_id):
    """People of a field: assigned to it, its brigadier, or standing in it now (live location, last 30 min)."""
    f = q('SELECT id, brigadier_id FROM fields WHERE id=?', (field_id,), one=True)
    if not f:
        return []
    out = {m['id']: m for m in q("SELECT * FROM tg_members WHERE status='FAOL' AND field_id=?", (field_id,))}
    if f['brigadier_id']:
        for m in q('''SELECT m.* FROM tg_members m JOIN users u ON u.id=m.user_id WHERE m.status='FAOL'
                      AND u.role='brigadier' AND u.brigadier_id=?''', (f['brigadier_id'],)):
            out[m['id']] = m
    from . import staffmap
    for p in staffmap.people(hours=1):
        if not p['stale'] and p['field']:
            code = q('SELECT code FROM fields WHERE id=?', (field_id,), one=True)['code']
            if p['field'] == code:
                col, val = ('member_id', int(p['key'][1:])) if p['key'].startswith('m') else ('user_id', int(p['key'][1:]))
                for m in q(f"SELECT * FROM tg_members WHERE status='FAOL' AND {'id' if col == 'member_id' else 'user_id'}=?", (val,)):
                    out[m['id']] = m
    return sorted(out.values(), key=lambda m: m['full_name'])


def brigade_members(brigadier_id):
    ids = {}
    for f in q('SELECT id FROM fields WHERE brigadier_id=? AND active=1', (brigadier_id,)):
        for m in q("SELECT * FROM tg_members WHERE status='FAOL' AND field_id=?", (f['id'],)):
            ids[m['id']] = m
    for m in q('''SELECT m.* FROM tg_members m JOIN users u ON u.id=m.user_id WHERE m.status='FAOL'
                  AND u.brigadier_id=?''', (brigadier_id,)):
        ids[m['id']] = m
    return sorted(ids.values(), key=lambda m: m['full_name'])


def resolve_target(target):
    """'group:agronom' / 'field:3' / 'brigade:2' / 'member:7' → (members, label, context)."""
    kind, _, val = (target or '').partition(':')
    if kind == 'group' and val in GROUPS:
        return group_members(val), GROUPS[val][0], f'group:{val}'
    if kind in ('field', 'brigade', 'member') and val.isdigit():
        v = int(val)
        if kind == 'field':
            f = q('SELECT code, name FROM fields WHERE id=?', (v,), one=True)
            return field_members(v), f'{f["code"]} dala' if f else 'Dala', f'field:{v}'
        if kind == 'brigade':
            b = q('SELECT name FROM brigadiers WHERE id=?', (v,), one=True)
            return brigade_members(v), f'{b["name"]} brigadasi' if b else 'Brigada', f'brigade:{v}'
        m = q("SELECT * FROM tg_members WHERE id=? AND status='FAOL'", (v,), one=True)
        return ([m] if m else []), (m['full_name'] if m else 'Xodim'), (f'equipment:{m["equipment_id"]}' if m and m['equipment_id'] else f'member:{v}')
    raise UserError('Kimdan so‘rashni tanlang.')


def ask(actor, target, quick='holat', text='', kind='any', deadline_min=None):
    """The director's one-tap “ask”: every person of the target gets the request privately from the bot."""
    members_, label, context = resolve_target(target)
    if not members_:
        raise UserError(f'{label}: Telegramga ulangan faol odam topilmadi. Kuzatuv → Odamlar bo‘limida qo‘shing.')
    if not text:
        grp = context.split(':')[1] if context.startswith('group:') else None
        text = GROUP_TEXT.get(grp) if quick == 'holat' and grp else QUICK.get(quick, QUICK['holat'])
    batch = secrets.token_hex(6)
    ids = create_requests(actor, [m['id'] for m in members_], text, kind=kind, deadline_min=deadline_min,
                          context=context, event=label, batch=batch)
    sent = sum(1 for rid in ids if deliver(rid))
    return {'label': label, 'asked': len(ids), 'sent': sent, 'batch': batch}


def target_choices():
    """Everything the “ask” form offers, with how many connected people each has."""
    groups = [(f'group:{k}', v[0], len(group_members(k))) for k, v in GROUPS.items()]
    return {'groups': groups,
            'fields': q("SELECT id, code, name FROM fields WHERE active=1 ORDER BY code"),
            'brigades': q('SELECT id, name FROM brigadiers WHERE active=1 ORDER BY name'),
            'members': q("SELECT id, full_name, role_label FROM tg_members WHERE status='FAOL' ORDER BY full_name")}


def void_item(actor, item_id, reason):
    reason = clean_text(reason, 200)
    if len(reason) < 3:
        raise UserError('Yashirish sababini yozing.')
    with tx() as db:
        it = db.execute('SELECT * FROM media_items WHERE id=?', (item_id,)).fetchone()
        if not it or it['voided_at']:
            raise UserError('Topilmadi.')
        db.execute('UPDATE media_items SET voided_at=?, voided_by=?, void_reason=? WHERE id=?',
                   (now_str(), actor.user_id, reason, item_id))
        audit(db, actor, 'VOID', 'media_item', item_id, old=row_dict(it), reason=reason)


# ------------------------------------------------------------------ read side

def day_stats(day):
    r = q('''SELECT COUNT(*) n, SUM(status='JAVOB') answered, SUM(status='KUTILMOQDA') waiting, SUM(status='KECHIKDI') late,
                    SUM(status='BEKOR') cancelled
             FROM media_requests WHERE substr(created_at,1,10)=?''', (day,), one=True)
    items = q('''SELECT COUNT(*) n, SUM(kind='photo') photos, SUM(kind='video') videos FROM media_items
                 WHERE substr(created_at,1,10)=? AND voided_at IS NULL''', (day,), one=True)
    return {'requests': r['n'] or 0, 'answered': r['answered'] or 0, 'waiting': r['waiting'] or 0, 'late': r['late'] or 0,
            'items': items['n'] or 0, 'photos': items['photos'] or 0, 'videos': items['videos'] or 0,
            'people': q("SELECT COUNT(*) c FROM tg_members WHERE status='FAOL'", one=True)['c'],
            'pending_people': q("SELECT COUNT(*) c FROM tg_members WHERE status='YANGI'", one=True)['c']}


ITEM_SQL = '''SELECT i.*, m.full_name, m.role_label, r.text request_text, r.created_at requested_at
              FROM media_items i JOIN tg_members m ON m.id=i.member_id LEFT JOIN media_requests r ON r.id=i.request_id'''


def items(day=None, member_id=None, limit=60, offset=0):
    where, params = ['i.voided_at IS NULL'], []
    if day:
        where.append('substr(i.created_at,1,10)=?')
        params.append(day)
    if member_id:
        where.append('i.member_id=?')
        params.append(member_id)
    return q(f'{ITEM_SQL} WHERE {" AND ".join(where)} ORDER BY i.id DESC LIMIT ? OFFSET ?', (*params, limit, offset))


def latest(limit=6):
    return q(f'{ITEM_SQL} WHERE i.voided_at IS NULL ORDER BY i.id DESC LIMIT ?', (limit,))


def open_requests():
    return q('''SELECT r.*, m.full_name, m.role_label, m.telegram_id, m.dm_ok FROM media_requests r
                JOIN tg_members m ON m.id=r.member_id
                WHERE r.status IN ('KUTILMOQDA','KECHIKDI') ORDER BY r.status='KECHIKDI' DESC, r.id DESC LIMIT 100''')


def recent_requests(day):
    return q('''SELECT r.*, m.full_name, m.role_label, (SELECT COUNT(*) FROM media_items i WHERE i.request_id=r.id
                AND i.voided_at IS NULL) n_items FROM media_requests r JOIN tg_members m ON m.id=r.member_id
                WHERE substr(r.created_at,1,10)=? ORDER BY r.id DESC''', (day,))


def members(status=None):
    where = 'WHERE m.status=?' if status else ''
    return q(f'''SELECT m.*, u.username sys_username, e.code equipment_code, f.name field_name,
                        (SELECT COUNT(*) FROM media_items i WHERE i.member_id=m.id AND i.voided_at IS NULL) n_items,
                        (SELECT MAX(created_at) FROM media_items i WHERE i.member_id=m.id) last_media_at
                 FROM tg_members m LEFT JOIN users u ON u.id=m.user_id LEFT JOIN equipment e ON e.id=m.equipment_id
                 LEFT JOIN fields f ON f.id=m.field_id {where}
                 ORDER BY CASE m.status WHEN 'YANGI' THEN 0 WHEN 'FAOL' THEN 1 ELSE 2 END, m.full_name''',
             (status,) if status else ())
