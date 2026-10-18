"""Dala nakladnoyi rasmi — guruhdan punktga.

When a trailer is full the field sends a photo of its field waybill (PB) to the work group. The bot keeps it at once
(take), and the worker reads it in the background (tick: the same reader as the punkt's — PB number, trailer, the
brigades' rows, total kg) and answers in the group under the photo. At the punkt Yunus sees “🚜 Yo‘lda” — every paper
still on its way, with its photo; when the trailer comes he taps “KELDI” and the paper opens in the open UY already
read (no second photo, no second reading). A photo that is not a field waybill is set aside silently.
"""
import json

from flask import current_app

from .db import get_db, q, tx
from .security import audit
from .settings import get_setting
from .utils import UserError, clean_text, now_str

LIVE = ('YANGI', 'YOLDA')


def on():
    return (get_setting('field_photo_group') or '1') != '0'      # on unless switched off


def take(message, sender):
    """A photo in the work group nobody asked for: kept as a field waybill on its way. Returns the row id or None."""
    from . import kuzatuv
    from .photos import store_photo
    from .telegram_bot import tg_download
    got = kuzatuv.media_of(message)
    if not got or got[0] != 'photo':
        return None
    f = got[1]
    uniq = f.get('file_unique_id')
    db = get_db()
    if uniq and db.execute('SELECT 1 FROM field_photos WHERE tg_file_unique_id=?', (uniq,)).fetchone():
        return None
    data = tg_download(f['file_id'])
    name = clean_text(' '.join(x for x in (sender.get('first_name'), sender.get('last_name')) if x)
                      or sender.get('username') or 'Guruh', 80)
    with tx(db):
        cur = db.execute('''INSERT OR IGNORE INTO field_photos(chat_id, tg_message_id, tg_file_unique_id, sender, sender_tg_id,
                                created_at) VALUES (?,?,?,?,?,?)''',
                         (str(message['chat']['id']), message.get('message_id'), uniq, name, str(sender.get('id') or ''),
                          now_str()))
        if not cur.rowcount:
            return None
        fid = cur.lastrowid
        pid = store_photo(db, None, data, category='nayman', entity_type='field_photo', entity_id=fid, source='telegram',
                          caption=clean_text(f'Guruhdan · {name}' + (f' · {message.get("caption")}' if message.get('caption')
                                                                      else ''), 300), tg_file_unique_id=uniq)
        db.execute('UPDATE field_photos SET photo_id=? WHERE id=?', (pid, fid))
    return fid


def _summary(m, ch):
    names = {b['id']: b['name'] for b in ch['brigadiers']}
    trailers = {t['id']: t['code'] for t in ch['trailers']}
    kg_ = lambda v: f'{v:,.0f}'.replace(',', ' ')
    parts = [m.get('blank_no') or '']
    if m.get('trailer_id') or m.get('trailer_text'):
        parts.append(trailers.get(m.get('trailer_id')) or m['trailer_text'])
    rows = [f'{names.get(r["brigadier_id"], r["brigadier_text"] or "?")} {kg_(r["kg"])}' for r in m['rows'] if r.get('kg')]
    if rows:
        parts.append(', '.join(rows))
    kg = m.get('total_kg') or m.get('sum_kg')
    if kg:
        parts.append(f'jami {kg_(kg)} kg')
    return ' · '.join(p for p in parts if p)


def read_one(fid):
    """Read one kept photo (the punkt's reader) and answer in the group under it."""
    from . import blanks as BL, intake, talon_ai
    from .telegram_bot import send
    fp = q('SELECT * FROM field_photos WHERE id=?', (fid,), one=True)
    if not fp or fp['status'] != 'YANGI':
        return None
    raw, err = None, None
    if talon_ai.enabled():
        try:
            raw = talon_ai.read_blank(photo_bytes(fp), [b['name'] for b in intake.choices()['brigadiers']])
        except Exception as exc:            # a reader failure never loses the photo — Yunus still sees it
            err = clean_text(str(exc), 200)
    ch = intake.choices()
    m = intake.match(raw, ch) if raw else None
    b = BL.find(m['blank_no']) if m and m.get('blank_no') else None
    looks = bool(m and (m.get('blank_no') or m.get('total_kg') or any(r.get('kg') for r in m['rows'])))
    status, note, reply = 'YOLDA', err, None
    if raw is not None and not looks:
        status, reply = 'BOSHQA', None                       # not a field waybill: kept, not shown, no answer
    elif b and b['waybill_id']:
        status, note = 'KELDI', f'{b["number"]} punktda allaqachon kiritilgan'
        reply = f'ℹ️ {b["number"]} punktda allaqachon kiritilgan.'
    elif m:
        reply = f'✅ {_summary(m, ch)} — punktga yuborildi (Yunus ko‘radi).'
        if not m.get('blank_no'):
            reply = f'⚠️ PB raqami o‘qilmadi — Yunus rasmni ko‘radi. {_summary(m, ch)}'
    else:
        reply = '📷 Rasm punktga yuborildi (Yunus ko‘radi).'
    with tx() as db:
        db.execute('''UPDATE field_photos SET status=?, read_json=?, blank_no=?, blank_id=?, trailer_text=?, total_kg=?,
                        summary=?, note=?, read_at=? WHERE id=? AND status='YANGI' ''',
                   (status, json.dumps(raw, ensure_ascii=False) if raw else None, (b['number'] if b else (m or {}).get('blank_no')),
                    b['id'] if b else None, (m or {}).get('trailer_text'), (m or {}).get('total_kg') or (m or {}).get('sum_kg'),
                    _summary(m, ch) if m else None, note, now_str(), fid))
    if reply:
        try:
            send(int(fp['chat_id']) if fp['chat_id'].lstrip('-').isdigit() else fp['chat_id'], reply,
                 reply_to=fp['tg_message_id'])
        except Exception:
            pass
    return status


def tick(limit=10):
    """From the outbox worker: read the photos still unread (oldest first)."""
    n = 0
    for r in q("SELECT id FROM field_photos WHERE status='YANGI' ORDER BY id LIMIT ?", (limit,)):
        read_one(r['id'])
        n += 1
    return n


def photo_bytes(fp):
    p = q('SELECT path FROM photos WHERE id=?', (fp['photo_id'],), one=True) if fp['photo_id'] else None
    if not p:
        raise UserError('Rasm topilmadi.')
    return (current_app.config['SURXON'].UPLOAD_DIR / p['path']).read_bytes()


def get(fid):
    return q('''SELECT fp.*, p.path, p.thumb_path FROM field_photos fp LEFT JOIN photos p ON p.id=fp.photo_id
                WHERE fp.id=?''', (fid,), one=True)


def waiting(days=3):
    """Papers on their way (newest first): read or still being read."""
    return [dict(r) for r in q(f'''SELECT fp.*, p.path, p.thumb_path FROM field_photos fp LEFT JOIN photos p ON p.id=fp.photo_id
                                   WHERE fp.status IN ('YANGI','YOLDA') AND fp.created_at >= datetime(?, '-{int(days)} days')
                                   ORDER BY fp.id DESC''', (now_str(),))]


def raw_of(fp):
    try:
        return json.loads(fp['read_json']) if fp['read_json'] else None
    except ValueError:
        return None


def arrived(actor, fid, group_id=None):
    """The trailer came: the paper went into the UY (KELDI). Called after the punkt saved it."""
    with tx() as db:
        n = db.execute("""UPDATE field_photos SET status='KELDI', arrived_at=?, arrived_by=?, group_id=?
                          WHERE id=? AND status IN ('YANGI','YOLDA')""", (now_str(), actor.user_id, group_id, fid)).rowcount
        if n:
            audit(db, actor, 'UPDATE', 'field_photo', fid, new={'status': 'KELDI', 'group_id': group_id})


def void(actor, fid, reason):
    """Not a waybill / sent twice: taken off the punkt's list (kept with the reason)."""
    if not actor or not actor.can('station.receive'):
        raise UserError('Bu amal uchun huquqingiz yo‘q.')
    reason = clean_text(reason, 200) or 'kerak emas'
    with tx() as db:
        n = db.execute("""UPDATE field_photos SET status='BEKOR', voided_by=?, void_reason=? WHERE id=? AND status IN ('YANGI','YOLDA')""",
                       (actor.user_id, reason, fid)).rowcount
        if not n:
            raise UserError('Bu rasm ro‘yxatda yo‘q.')
        audit(db, actor, 'VOID', 'field_photo', fid, reason=reason)


def announce(actor, text):
    """“📢 Guruhga xabar”: the bot posts this text to every work group. Returns how many groups got it."""
    from .kuzatuv import work_chat_ids
    from .telegram_bot import send
    text = (text or '').strip()
    if len(text) < 5:
        raise UserError('Xabar matnini yozing.')
    sent = 0
    for chat in work_chat_ids():
        if send(int(chat) if str(chat).lstrip('-').isdigit() else chat, text[:3500]):
            sent += 1
    with tx() as db:
        audit(db, actor, 'CREATE', 'group_announce', None, new={'groups': sent, 'text': text[:500]})
    if not sent:
        raise UserError('Ish guruhi topilmadi yoki bot yubora olmadi (Kuzatuv → Odamlar → guruhni “ish guruhi” qiling).')
    return sent


ANNOUNCE_TEXT = """📢 DIQQAT, HAMMAGA!

Har bir telashka to‘lgandan keyin dala nakladnoyi (PB) RASMini shu guruhga yuboring:
• PB raqami, telashka raqami, brigada va kg aniq ko‘rinsin
• Rasm tiniq bo‘lsin, nakladnoy to‘liq tushsin
• Rasm yuborilmagan telashka punktda qabul qilinmaydi

Rasm Yunusning punkt ekraniga darrov chiqadi — telashka yo‘lda ekanini ko‘rib turadi.
Bu juda muhim, hammadan iltimos!"""
