"""Telegram bot on the same backend/database as the web app.

Webhook only (no polling process). Every action calls the same service
functions as the web UI, so validation, permissions and the audit trail are
identical. Telegram photos are downloaded and stored in the photo archive.
"""
import json
import secrets
import threading
import urllib.request
import uuid

from flask import Blueprint, abort, current_app, request, url_for

from . import queries
from .db import get_db, q, tx
from .security import Actor, has_perm
from .services import (add_harvest, attach_load_photos, current_season, mark_full, open_load, record_gross,
                       record_tare, upload_archive_photo)
from .utils import UserError, fmt_num, now_str, today_str

bp = Blueprint('telegram', __name__)
MAX_TEXT = 3500


# ------------------------------------------------------------------ Telegram HTTP

def tg_api(method, payload=None):
    token = current_app.config['SURXON'].TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError('TELEGRAM_BOT_TOKEN sozlanmagan')
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}',
                                 data=json.dumps(payload or {}).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def tg_download(file_id):
    token = current_app.config['SURXON'].TELEGRAM_BOT_TOKEN
    info = tg_api('getFile', {'file_id': file_id})['result']
    if info.get('file_size', 0) > 20 * 1024 * 1024:
        raise UserError('Rasm juda katta (20 MB dan ortiq).')
    with urllib.request.urlopen(f'https://api.telegram.org/file/bot{token}/{info["file_path"]}', timeout=60) as resp:
        return resp.read()


def send(chat_id, text, buttons=None, reply_to=None):
    payload = {'chat_id': chat_id, 'text': text[:MAX_TEXT], 'disable_web_page_preview': True}
    if reply_to:
        payload['reply_to_message_id'] = reply_to
        payload['allow_sending_without_reply'] = True
    if buttons:
        payload['reply_markup'] = {'inline_keyboard': buttons}
    try:
        return tg_api('sendMessage', payload)
    except Exception as exc:  # never let a failed reply break the operation that already committed
        current_app.logger.warning('Telegram send failed: %s', exc)


def notify_async(text, roles=('admin', 'manager'), station_id=None):
    """Best-effort alert to linked managers (and, with station_id, that punkt's operators).
    Runs after commit, off the request thread."""
    try:
        from .settings import get_bool
        cfg = current_app.config['SURXON']
        if not cfg.TELEGRAM_BOT_TOKEN or not get_bool('notify_telegram'):
            return
        chats = [r['telegram_id'] for r in q(
            'SELECT telegram_id FROM users WHERE active=1 AND telegram_id IS NOT NULL AND role IN (%s)' % ','.join('?' * len(roles)),
            tuple(roles))]
        if station_id:
            chats += [r['telegram_id'] for r in q("SELECT telegram_id FROM users WHERE active=1 AND telegram_id IS NOT NULL "
                                                  "AND role='station' AND station_id=?", (station_id,))]
    except Exception:
        return
    if not chats:
        return
    app = current_app._get_current_object()

    def run():
        with app.app_context():
            for chat in chats:
                send(chat, text)
    threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------ session state

def get_state(tid):
    row = q('SELECT * FROM telegram_sessions WHERE telegram_id=?', (tid,), one=True)
    return (row['step'], json.loads(row['data_json'] or '{}')) if row else ('menu', {})


def set_state(tid, step, **data):
    get_db().execute('INSERT INTO telegram_sessions(telegram_id, step, data_json, updated_at) VALUES (?,?,?,?) '
                     'ON CONFLICT(telegram_id) DO UPDATE SET step=excluded.step, data_json=excluded.data_json, '
                     'updated_at=excluded.updated_at', (tid, step, json.dumps(data), now_str()))


def clear_state(tid):
    get_db().execute('DELETE FROM telegram_sessions WHERE telegram_id=?', (tid,))


def actor_for(user):
    return Actor.from_user(user, source='telegram')


def op_uuid(*parts):
    """Deterministic id so a re-delivered Telegram update can never create a second record."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'tg:' + ':'.join(str(p) for p in parts)))


# ------------------------------------------------------------------ menus

def menu_buttons(user):
    rows = []
    if has_perm(user, 'harvest.write'):
        rows.append([{'text': '🌿 Terim kiritish', 'callback_data': 'm:harvest'}])
    if has_perm(user, 'load.full'):
        rows.append([{'text': '🚛 Telashka to‘ldi (TOLDI)', 'callback_data': 'm:toldi'}])
    if has_perm(user, 'load.open'):
        rows.append([{'text': '➕ Yangi telashka ochish', 'callback_data': 'm:open'}])
    if has_perm(user, 'weigh.write'):
        rows.append([{'text': '⚖️ Tarozi', 'callback_data': 'm:scale'}])
    if has_perm(user, 'photos.upload'):
        rows.append([{'text': '📷 Rasm yuborish (arxiv)', 'callback_data': 'm:photo'}])
    rows.append([{'text': '📊 Bugun', 'callback_data': 'm:today'}])
    return rows


def show_menu(chat, user, text=None):
    clear_state(str(user['telegram_id']))
    send(chat, text or f'SURXON PAXTA · {user["full_name"]}\nKerakli bo‘limni tanlang:', menu_buttons(user))


def cancel_row():
    return [{'text': '❌ Bekor qilish', 'callback_data': 'cancel'}]


def scoped_open_loads(user, statuses=('OCHIQ',)):
    brig = user['brigadier_id'] if user['role'] == 'brigadier' else None
    year = current_season(get_db())
    return queries.loads(year, statuses=statuses, brig=brig)


# ------------------------------------------------------------------ webhook

@bp.post('/telegram/webhook/<secret>')
def webhook(secret):
    cfg = current_app.config['SURXON']
    expected = cfg.TELEGRAM_WEBHOOK_SECRET
    if not expected or not secrets.compare_digest(secret, expected):
        abort(404)
    header = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if header is not None and not secrets.compare_digest(header, expected):
        abort(403)
    update = request.get_json(silent=True) or {}
    uid = update.get('update_id')
    if uid is not None:
        cur = get_db().execute('INSERT OR IGNORE INTO telegram_updates(update_id, received_at) VALUES (?,?)', (uid, now_str()))
        if cur.rowcount == 0:
            return {'ok': True, 'duplicate': True}
    try:
        process_update(update)
    except Exception as exc:
        current_app.logger.exception('Telegram update failed: %s', exc)
        chat = _chat_of(update)
        if chat:
            send(chat, '⚠️ Xatolik yuz berdi, amal saqlanmadi. Qayta urinib ko‘ring.')
    return {'ok': True}


def _chat_of(update):
    if update.get('message'):
        return update['message']['chat']['id']
    if update.get('callback_query'):
        return update['callback_query']['message']['chat']['id']
    return None


def process_update(update):
    if update.get('channel_post'):
        # a post in a channel where the bot is admin: record the channel (id + title), ignore the content
        from . import kuzatuv
        with tx() as db:
            kuzatuv.chat_seen(db, update['channel_post'].get('chat') or {}, bot_status='administrator')
        return
    if update.get('my_chat_member') or update.get('chat_member'):
        return _on_member_update(update.get('my_chat_member') or update['chat_member'])
    if update.get('callback_query'):
        cb = update['callback_query']
        try:
            tg_api('answerCallbackQuery', {'callback_query_id': cb['id']})
        except Exception:
            pass
        _dispatch(cb['message']['chat']['id'], cb['from'], data=cb.get('data', ''), update_id=update.get('update_id'))
    elif update.get('message'):
        m = update['message']
        ctype = m.get('chat', {}).get('type')
        if ctype in ('group', 'supergroup'):
            return _on_group_message(m)
        if ctype != 'private':
            return
        _dispatch(m['chat']['id'], m['from'], message=m, update_id=update.get('update_id'))


# ------------------------------------------------------------------ kuzatuv (work group + photo/video requests)

def _on_member_update(upd):
    """Bot added to / removed from a group, or (bot is admin) someone joined the group."""
    from . import kuzatuv
    chat = upd.get('chat') or {}
    new = upd.get('new_chat_member') or {}
    who = new.get('user') or {}
    with tx() as db:
        if chat.get('type') == 'channel':
            # only the bot's own membership in a channel is of interest (to offer it as archive/report channel)
            if who.get('is_bot'):
                kuzatuv.chat_seen(db, chat, bot_status=new.get('status'))
            return
        kuzatuv.chat_seen(db, chat)
        if who and not who.get('is_bot') and new.get('status') in ('member', 'administrator', 'creator', 'restricted'):
            kuzatuv.seen(db, who, chat)
        elif who and not who.get('is_bot') and new.get('status') in ('left', 'kicked'):
            _left(db, who, chat)


def _left(db, who, chat):
    work = db.execute('SELECT 1 FROM tg_chats WHERE chat_id=? AND is_work=1', (str(chat.get('id')),)).fetchone()
    if work:
        db.execute("UPDATE tg_members SET status='NOFAOL' WHERE telegram_id=? AND source='guruh' AND user_id IS NULL",
                   (str(who['id']),))


def _on_group_message(m):
    """Work group: register everyone the bot sees; photos/videos that answer a request are stored.
    Other chatter in the group is ignored (the bot never answers ordinary messages there)."""
    from . import kuzatuv
    chat = m['chat']
    with tx() as db:
        info = kuzatuv.chat_seen(db, chat)
        for who in m.get('new_chat_members') or []:
            kuzatuv.seen(db, who, chat)
        if m.get('left_chat_member'):
            _left(db, m['left_chat_member'], chat)
        member = kuzatuv.seen(db, m.get('from'), chat) if m.get('from') else None
    if not info or not info['is_work'] or not member or member['status'] != 'FAOL' or not kuzatuv.media_of(m):
        return
    reply_to = (m.get('reply_to_message') or {}).get('message_id')
    req = kuzatuv.open_request_for(get_db(), member, chat['id'], reply_to)
    if not req:
        return  # a photo not asked for, in a busy group: not stored (people send it privately to the bot instead)
    kuzatuv.save_incoming(member, m, req)
    send(chat['id'], f'✅ Qabul qilindi — {member["full_name"]}, rahmat.', reply_to=m.get('message_id'))


def _kuzatuv_private(chat, sender, message):
    """Private message from someone who is not (or not only) a system user: kuzatuv member flow."""
    from . import kuzatuv
    with tx() as db:
        member = kuzatuv.seen(db, sender, private=True)
    if not member:
        return
    if member['status'] == 'YANGI':
        return send(chat, '🔒 Bu Telegram akkaunt tizimga ulanmagan.\n'
                          f'Siz kuzatuv ro‘yxatiga yozildingiz ({member["full_name"]}). Rahbar tasdiqlagach, '
                          'bot sizdan ish joyidan rasm/video so‘raydi.\n'
                          f'(Sizning ID: {sender["id"]})')
    if member['status'] != 'FAOL':
        return send(chat, 'Siz kuzatuv ro‘yxatida faol emassiz. Rahbarga murojaat qiling.')
    if kuzatuv.media_of(message):
        return _kuzatuv_media(chat, member, message)
    open_n = q("SELECT COUNT(*) c FROM media_requests WHERE member_id=? AND status IN ('KUTILMOQDA','KECHIKDI')",
               (member['id'],), one=True)['c']
    send(chat, f'👋 {member["full_name"]}, siz SURXAN-PAXTA kuzatuv ro‘yxatidasiz.\n'
               + (f'Sizdan {open_n} ta rasm/video so‘ralgan — shu yerga yuboring.' if open_n else
                  'Hozir so‘rov yo‘q. Rahbar so‘raganda shu yerga xabar keladi; ish joyidan rasm/video yuborsangiz ham saqlanadi.'))


def _kuzatuv_media(chat, member, message):
    from . import kuzatuv
    reply_to = (message.get('reply_to_message') or {}).get('message_id')
    req = kuzatuv.open_request_for(get_db(), member, chat, reply_to)
    item = kuzatuv.save_incoming(member, message, req)
    if item is None:
        return send(chat, 'Bu fayl allaqachon qabul qilingan.')
    row = q('SELECT kind, path, note FROM media_items WHERE id=?', (item,), one=True)
    what = 'Video' if row['kind'] == 'video' else 'Rasm'
    extra = f'\n⚠️ {row["note"]}' if row['note'] else ''
    left = q("SELECT COUNT(*) c FROM media_requests WHERE member_id=? AND status IN ('KUTILMOQDA','KECHIKDI')",
             (member['id'],), one=True)['c']
    return send(chat, f'✅ {what} qabul qilindi va rahbarga ko‘rinadi.' + (f'\nSo‘rov: {req["text"]}' if req else '')
                + extra + (f'\nYana {left} ta so‘rov ochiq.' if left else ''))


def _link_account(chat, tid, code):
    code = code.strip().upper()
    with tx() as db:
        user = db.execute("SELECT * FROM users WHERE tg_link_code=? AND active=1 AND tg_link_expires >= ?",
                          (code, now_str())).fetchone()
        if not user:
            send(chat, '❌ Kod noto‘g‘ri yoki muddati o‘tgan. Admin yangi kod bersin.')
            return
        db.execute('UPDATE users SET telegram_id=NULL WHERE telegram_id=?', (tid,))
        db.execute('UPDATE users SET telegram_id=?, tg_link_code=NULL, tg_link_expires=NULL WHERE id=?', (tid, user['id']))
        from .security import audit
        audit(db, Actor.from_user(user, source='telegram'), 'TG_LINK', 'user', user['id'], new={'telegram_id': tid})
        from .kuzatuv import link_system_user
        link_system_user(db, db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone())
    user = q('SELECT * FROM users WHERE id=?', (user['id'],), one=True)
    show_menu(chat, user, f'✅ Akkaunt ulandi: {user["full_name"]}.\nEndi botdan foydalanishingiz mumkin.')


def _link_member(chat, sender, code):
    from . import kuzatuv
    with tx() as db:
        member = kuzatuv.link_by_code(db, code, sender)
    if not member:
        return send(chat, '❌ Havola eskirgan yoki noto‘g‘ri. Rahbardan yangi havola so‘rang.')
    send(chat, f'✅ Ulandi: {member["full_name"]}' + (f' ({member["role_label"]})' if member['role_label'] else '')
         + '.\nRahbar ish joyidan rasm yoki video so‘raganda shu yerga xabar keladi. Javobni shu yerga yuborasiz.')
    kuzatuv.deliver_pending(member_id=member['id'])   # anything asked before they joined arrives now


def _goes_to_kuzatuv(user, message):
    """A linked system user's photo/video goes to kuzatuv when it answers a request (reply or open request) and they
    are not in the middle of a TOLDI / scale / archive photo step. Videos always go to kuzatuv."""
    from . import kuzatuv
    got = kuzatuv.media_of(message)
    if not got:
        return False
    step, _st = get_state(str(user['telegram_id']))
    if step in ('toldi_photos', 'scale_gross', 'scale_tare', 'archive_photos'):
        return False
    if got[0] == 'video':
        return True
    m = q('SELECT * FROM tg_members WHERE telegram_id=? AND status=?', (str(user['telegram_id']), 'FAOL'), one=True)
    if not m:
        return False
    reply_to = (message.get('reply_to_message') or {}).get('message_id')
    return kuzatuv.open_request_for(get_db(), m, message['chat']['id'], reply_to) is not None


def _dispatch(chat, sender, data=None, message=None, update_id=None):
    tid = str(sender['id'])
    text = ((message or {}).get('text') or '').strip()
    if text.startswith('/start'):
        parts = text.split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip().upper().startswith('K-'):
            return _link_member(chat, sender, parts[1])
        if len(parts) == 2:
            return _link_account(chat, tid, parts[1])
    user = q('SELECT * FROM users WHERE telegram_id=? AND active=1', (tid,), one=True)
    if not user:
        return _kuzatuv_private(chat, sender, message or {})
    if message and _goes_to_kuzatuv(user, message):
        from . import kuzatuv
        with tx() as db:
            member = kuzatuv.seen(db, sender, private=True)
        return _kuzatuv_media(chat, member, message)
    try:
        if data:
            return _on_callback(chat, user, data, update_id)
        if text in ('/start', '/menu', 'Menu', 'menu'):
            return show_menu(chat, user)
        if text == '/cancel':
            return show_menu(chat, user, 'Bekor qilindi.')
        return _on_message(chat, user, message, update_id)
    except UserError as e:
        step, st = get_state(tid)
        send(chat, f'⚠️ {e}', [cancel_row()] if step != 'menu' else menu_buttons(user))


# ------------------------------------------------------------------ callbacks

def _on_callback(chat, user, data, update_id):
    tid = str(user['telegram_id'])
    step, st = get_state(tid)
    if data == 'cancel':
        return show_menu(chat, user, 'Bekor qilindi.')
    if data == 'm:today':
        return _today(chat, user)
    if data == 'm:harvest':
        loads = scoped_open_loads(user)
        if not loads:
            return send(chat, 'Ochiq telashka yo‘q. Avval “➕ Yangi telashka ochish”.', menu_buttons(user))
        set_state(tid, 'pick_harvest_load')
        return send(chat, '🌿 Qaysi telashka yonida terim tortilyapti?',
                    [[{'text': f'{r["trailer_code"]} · {r["field_name"]} · {fmt_num(r["live_kg"])} kg',
                       'callback_data': f'hl:{r["id"]}'}] for r in loads] + [cancel_row()])
    if data.startswith('hl:'):
        lid = int(data[3:])
        ld = queries.load(lid)
        set_state(tid, 'harvest_lines', load_id=lid)
        return send(chat, f'🚛 {ld["trailer_code"]} · {ld["field_name"]}\n\nHar bir qatorga “Ism Familiya kg” yozing:\n'
                          'Gulbahor opa 85\nMirjalol 95,5\n\nKombayn uchun: “K-01 1850”.\nBir xabarda bir nechta qator bo‘lishi mumkin.',
                    [[{'text': '✅ Tugatish', 'callback_data': 'cancel'}]])
    if data == 'm:toldi':
        loads = scoped_open_loads(user)
        if not loads:
            return send(chat, 'TOLDI qilinadigan ochiq telashka yo‘q.', menu_buttons(user))
        return send(chat, '🚛 Qaysi telashka to‘ldi?',
                    [[{'text': f'{r["trailer_code"]} · {r["field_name"]} · {fmt_num(r["live_kg"])} kg',
                       'callback_data': f'tf:{r["id"]}'}] for r in loads] + [cancel_row()])
    if data.startswith('tf:'):
        lid = int(data[3:])
        set_state(tid, 'toldi_photos', load_id=lid)
        return send(chat, '📷 Telashkaning to‘la holatdagi rasm(lar)ini yuboring. Keyin “✅ TOLDI — SAQLASH” ni bosing.',
                    [[{'text': '✅ TOLDI — SAQLASH', 'callback_data': 'tsave'}], cancel_row()])
    if data == 'tsave':
        if step != 'toldi_photos':
            return show_menu(chat, user)
        res = mark_full(actor_for(user), st['load_id'], source='telegram')
        ld = queries.load(st['load_id'])
        clear_state(tid)
        if not res['already'] and res.get('waybill_id'):
            from .services import after_waybill_change
            after_waybill_change(actor_for(user), res['waybill_id'], 'yaratildi')
            notify_async(f'📄 {res["number"]} · {ld["trailer_code"]} tugatildi (Telegram, {user["full_name"]})\n'
                         f'Qo‘l terimi: {fmt_num(res["net_kg"])} kg', roles=('admin', 'manager', 'accountant'))
            return send(chat, f'✅ Tugatildi.\n🚛 {ld["trailer_code"]} · {ld["field_name"]} · {ld["brigadier_name"]}\n'
                              f'⚖️ Dala tarozisi yig‘indisi: {fmt_num(res["net_kg"])} kg\n📄 Nakladnoy: {res["number"]}',
                        menu_buttons(user))
        if not res['already']:
            notify_async(f'🚛 {ld["trailer_code"]} TOLDI (Telegram, {user["full_name"]}) · {ld["field_name"]}\n'
                         f'Ichki hisob: {fmt_num(res["internal_kg"])} kg', roles=('admin', 'manager', 'scale'))
        return send(chat, f'✅ TOLDI saqlandi.\n🚛 {ld["trailer_code"]} · {ld["field_name"]} · {ld["brigadier_name"]}\n'
                          f'⚖️ Ichki hisob: {fmt_num(res["internal_kg"])} kg\n🕒 {now_str()[:16]}\n\nEndi umumiy taroziga olib boring.',
                    menu_buttons(user))
    if data == 'm:open':
        busy = {r['trailer_id'] for r in q("SELECT trailer_id FROM trailer_loads WHERE status IN ('OCHIQ','TOLDI')")}
        free = [t for t in q("SELECT * FROM equipment WHERE kind='telashka' AND active=1 ORDER BY code") if t['id'] not in busy]
        if not free:
            return send(chat, 'Bo‘sh telashka yo‘q — hammasida tugallanmagan yuk bor.', menu_buttons(user))
        set_state(tid, 'open_trailer')
        return send(chat, '🚛 Qaysi telashka?', [[{'text': t['code'], 'callback_data': f'ot:{t["id"]}'}] for t in free] + [cancel_row()])
    if data.startswith('ot:'):
        brig = user['brigadier_id'] if user['role'] == 'brigadier' else None
        fields = q('SELECT * FROM fields WHERE active=1' + (' AND brigadier_id=?' if brig else '') + ' ORDER BY code',
                   (brig,) if brig else ())
        if not fields:
            return send(chat, 'Dala topilmadi. Admin dalalarni kiritishi kerak.', menu_buttons(user))
        set_state(tid, 'open_field', trailer_id=int(data[3:]))
        return send(chat, '🌾 Qaysi dala?', [[{'text': f'{f["code"]} · {f["name"]}', 'callback_data': f'of:{f["id"]}'}]
                                           for f in fields] + [cancel_row()])
    if data.startswith('of:'):
        tractors = q("SELECT * FROM equipment WHERE kind='traktor' AND active=1 ORDER BY code")
        set_state(tid, 'open_tractor', trailer_id=st.get('trailer_id'), field_id=int(data[3:]))
        return send(chat, '🚜 Qaysi traktor?', [[{'text': t['code'], 'callback_data': f'otr:{t["id"]}'}] for t in tractors]
                    + [[{'text': 'Traktorsiz', 'callback_data': 'otr:0'}], cancel_row()])
    if data.startswith('otr:'):
        tractor = int(data[4:]) or None
        lid = open_load(actor_for(user), trailer_id=st['trailer_id'], field_id=st['field_id'], brigadier_id=None,
                        tractor_id=tractor, client_uuid=op_uuid('open', update_id))
        ld = queries.load(lid)
        set_state(tid, 'harvest_lines', load_id=lid)
        return send(chat, f'✅ Ochildi: {ld["trailer_code"]} · {ld["field_name"]} · {ld["brigadier_name"]}\n\n'
                          'Endi terimni yozing: “Ism Familiya kg” (har qatorga bittadan).',
                    [[{'text': '✅ Tugatish', 'callback_data': 'cancel'}]])
    if data == 'm:scale':
        year = current_season(get_db())
        waiting = queries.loads(year, statuses=('TOLDI',))
        if not waiting:
            return send(chat, 'Tarozini kutayotgan telashka yo‘q.', menu_buttons(user))
        rows = []
        for r in waiting:
            label = 'tara kerak' if r['weigh_status'] == 'BRUTTO' else 'brutto kerak'
            rows.append([{'text': f'{r["trailer_code"]} · {r["field_name"]} · {label}', 'callback_data': f'sc:{r["id"]}'}])
        return send(chat, '⚖️ Qaysi telashka?', rows + [cancel_row()])
    if data.startswith('sc:'):
        lid = int(data[3:])
        ld = queries.load(lid)
        if ld['weigh_status'] == 'BRUTTO':
            set_state(tid, 'scale_tare', load_id=lid)
            return send(chat, f'⚖️ {ld["trailer_code"]}: brutto {fmt_num(ld["gross_kg"])} kg.\nTARA (bo‘sh) kg ni yozing. '
                              'Tarozi rasmini ham yuborishingiz mumkin.', [cancel_row()])
        set_state(tid, 'scale_gross', load_id=lid)
        return send(chat, f'⚖️ {ld["trailer_code"]} · {ld["field_name"]}\nIchki hisob: {fmt_num(ld["live_kg"])} kg\n\n'
                          'BRUTTO (yuk bilan) kg ni yozing:', [cancel_row()])
    if data == 'm:photo':
        cats = [('trailer', 'Telashka'), ('cotton', 'Paxta'), ('field', 'Dala'), ('combine', 'Kombayn'),
                ('nayman', 'Nayman'), ('waybill', 'Nakladnoy'), ('payment', 'To‘lov'), ('expense', 'Xarajat')]
        return send(chat, '📷 Rasm turi?', [[{'text': b, 'callback_data': f'pc:{a}'}] for a, b in cats] + [cancel_row()])
    if data.startswith('pc:'):
        set_state(tid, 'archive_photos', category=data[3:])
        return send(chat, 'Rasm(lar)ni yuboring (izoh yozish mumkin). Tugagach “✅ Tugatish”.',
                    [[{'text': '✅ Tugatish', 'callback_data': 'cancel'}]])
    return show_menu(chat, user)


# ------------------------------------------------------------------ messages

def _parse_line(line):
    parts = line.replace('\t', ' ').rsplit(maxsplit=1)
    if len(parts) != 2:
        raise UserError(f'“{line}” — format: Ism Familiya kg')
    name, raw = parts[0].strip(' -:;,'), parts[1].replace(',', '.').lower().rstrip('kg')
    try:
        kg = float(raw)
    except ValueError:
        raise UserError(f'“{line}” — kg son bo‘lishi kerak')
    return name, kg


def _on_message(chat, user, message, update_id):
    tid = str(user['telegram_id'])
    step, st = get_state(tid)
    text = (message.get('text') or '').strip()
    photo = message.get('photo')
    actor = actor_for(user)

    if photo:
        best = photo[-1]
        data = tg_download(best['file_id'])
        if step == 'toldi_photos':
            attach_load_photos(actor, st['load_id'], [data], category='trailer', source='telegram',
                               tg_ids=[best.get('file_unique_id')])
            n = q("SELECT COUNT(*) c FROM photos WHERE load_id=? AND voided_at IS NULL", (st['load_id'],), one=True)['c']
            return send(chat, f'📷 Rasm saqlandi ({n} ta). Yana yuboring yoki “✅ TOLDI — SAQLASH”.',
                        [[{'text': '✅ TOLDI — SAQLASH', 'callback_data': 'tsave'}], cancel_row()])
        if step in ('scale_gross', 'scale_tare'):
            cat = 'weigh_gross' if step == 'scale_gross' else 'weigh_tare'
            attach_load_photos(actor, st['load_id'], [data], category=cat, source='telegram',
                               tg_ids=[best.get('file_unique_id')])
            return send(chat, '📷 Tarozi rasmi saqlandi. Endi kg ni yozing.', [cancel_row()])
        category = st.get('category', 'other') if step == 'archive_photos' else 'other'
        upload_archive_photo(actor, data, category=category, caption=message.get('caption', ''), source='telegram',
                             tg_file_unique_id=best.get('file_unique_id'))
        return send(chat, '📷 Rasm arxivga saqlandi va Dashboard’da ko‘rinadi.',
                    [[{'text': '✅ Tugatish', 'callback_data': 'cancel'}]] if step == 'archive_photos' else menu_buttons(user))

    if step == 'harvest_lines' and text:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        combines = {r['code'].upper(): r['id'] for r in q("SELECT id, code FROM equipment WHERE kind='kombayn' AND active=1")}
        ok, errors, total = [], [], None
        for i, line in enumerate(lines[:40]):
            try:
                name, kg = _parse_line(line)
                if name.upper() in combines:
                    _hid, info = add_harvest(actor, load_id=st['load_id'], method='combine', kg=kg,
                                             combine_id=combines[name.upper()], source='telegram',
                                             client_uuid=op_uuid('h', update_id, i), confirm_duplicate=True)
                else:
                    _hid, info = add_harvest(actor, load_id=st['load_id'], method='hand', kg=kg, worker_name=name,
                                             source='telegram', client_uuid=op_uuid('h', update_id, i), confirm_duplicate=True)
                total = info.get('load_total', total)
                ok.append(f'✅ {name} — {fmt_num(kg, 1 if kg % 1 else 0)} kg' + (' (yangi ishchi)' if info.get('created_worker') else ''))
            except UserError as e:
                errors.append(f'❌ {line}: {e}')
        msg = '\n'.join(ok + errors)
        if total is not None:
            msg += f'\n\n🚛 Telashkada jami: {fmt_num(total)} kg'
        return send(chat, msg or 'Hech narsa saqlanmadi.', [[{'text': '✅ Tugatish', 'callback_data': 'cancel'}],
                                                            [{'text': '🚛 TOLDI qilish', 'callback_data': f'tf:{st["load_id"]}'}]])

    if step in ('scale_gross', 'scale_tare') and text:
        try:
            kg = float(text.replace(' ', '').replace(',', '.').lower().rstrip('kg'))
        except ValueError:
            if step == 'scale_tare' and st.get('tare') is not None:
                return _finish_tare(chat, user, st, st['tare'], text)
            raise UserError('Faqat son yozing (kg).')
        if step == 'scale_gross':
            record_gross(actor, st['load_id'], kg, source='telegram')
            set_state(tid, 'scale_tare', load_id=st['load_id'])
            return send(chat, f'✅ Brutto {fmt_num(kg)} kg saqlandi ({now_str()[11:16]}).\n'
                              'Mashina yukni topshirgach, bo‘sh holatda tortib TARA kg ni yozing.', [cancel_row()])
        return _finish_tare(chat, user, st, kg, '')
    return show_menu(chat, user)


def _finish_tare(chat, user, st, tare, reason):
    tid = str(user['telegram_id'])
    try:
        res = record_tare(actor_for(user), st['load_id'], tare, diff_reason=reason, source='telegram')
    except UserError as e:
        if 'sabab' in str(e).lower():
            set_state(tid, 'scale_tare', load_id=st['load_id'], tare=tare)
            return send(chat, f'⚠️ {e}\n\nFarq sababini matn bilan yozing:', [cancel_row()])
        raise
    clear_state(tid)
    ld = queries.load(st['load_id'])
    if not res['already']:
        from .services import after_waybill_change
        after_waybill_change(actor_for(user), res['waybill_id'], 'yaratildi')
        notify_async(f'⚖️ {ld["trailer_code"]} tortildi · Netto {fmt_num(res["net_kg"])} kg\n📄 {res["number"]}',
                     roles=('admin', 'manager', 'accountant'))
    return send(chat, f'✅ Tortish yakunlandi\n🚛 {ld["trailer_code"]} · {ld["field_name"]}\n'
                      f'Brutto: {fmt_num(ld["gross_kg"])} kg\nTara: {fmt_num(ld["tare_kg"])} kg\n'
                      f'Netto: {fmt_num(res["net_kg"])} kg\n📄 Nakladnoy: {res["number"]}', menu_buttons(user))


def _today(chat, user):
    year = current_season(get_db())
    brig = user['brigadier_id'] if user['role'] == 'brigadier' else None
    k = queries.day_kpis(year, today_str(), brig)
    send(chat, f'📊 BUGUN ({today_str()})\n🌿 Terim: {fmt_num(k["harvest"])} kg '
               f'(qo‘l {fmt_num(k["hand"])}, kombayn {fmt_num(k["combine"])})\n'
               f'⚖️ Tarozi netto: {fmt_num(k["net"])} kg\n🚛 Naymanga: {fmt_num(k["sent"])} kg\n'
               f'🏭 Qabul: {fmt_num(k["accepted"])} kg\n📦 Band telashkalar: {k["trailers_busy"]}/{k["trailers_total"]}',
         menu_buttons(user))
