"""“Jonli kuzatuv”: the day's real photos and videos as cards — never the Telegram chat itself.

Two sources, both real:
  • answers to photo/video requests sent through the bot (media_items), grouped per request (or per person within
    10 minutes), with the place they belong to (field / machine) and the event that asked for them;
  • photos the system already takes at real events: a trailer filled (trip close), the weighbridge, the punkt receipt,
    diesel given to a machine. Cancelled trips never appear.
Videos are shown with their thumbnail; the video itself loads only when someone presses play.
"""
from .db import q
from .utils import today_str

EVENT_PHOTOS = {   # photo category → (title, where it happened)
    'trailer': 'Telashka to‘ldi', 'cotton': 'Telashka to‘ldi', 'field': 'Dala rasmi',
    'weigh_gross': 'Tarozi (brutto)', 'weigh_tare': 'Tarozi (tara)', 'nayman': 'Punkt qabul qildi',
    'fuel': 'Solyarka berildi', 'combine': 'Kombayn',
}


def _bucket(ts):
    return ts[:15]      # "YYYY-MM-DD HH:M" → 10-minute window


def telegram_cards(day, limit=40):
    rows = q('''SELECT i.*, m.full_name, m.role_label, r.text request_text, r.event, r.context, f.code field_code,
                       e.code equipment_code
                FROM media_items i JOIN tg_members m ON m.id=i.member_id LEFT JOIN media_requests r ON r.id=i.request_id
                LEFT JOIN fields f ON f.id=i.field_id LEFT JOIN equipment e ON e.id=i.equipment_id
                WHERE i.voided_at IS NULL AND substr(i.created_at,1,10)=? ORDER BY i.id DESC LIMIT ?''', (day, limit * 3))
    cards = {}
    for r in rows:
        key = ('r', r['request_id']) if r['request_id'] else ('m', r['member_id'], _bucket(r['created_at']))
        c = cards.get(key)
        if not c:
            place = r['field_code'] and f'{r["field_code"]} dala' or r['equipment_code'] or ''
            # a group ask (“Barcha agronomlar”) is not an event — the card then shows what was asked
            title = r['event'] if r['event'] and not (r['context'] or '').startswith(('group:', 'member:')) else None
            c = cards[key] = {'src': 'telegram', 'at': r['created_at'], 'time': r['created_at'][11:16], 'place': place,
                              'title': title or (r['caption'] or r['request_text'] or 'Kuzatuv')[:60],
                              'who': r['full_name'] + (f' · {r["role_label"]}' if r['role_label'] else ''),
                              'photos': 0, 'videos': 0, 'media': []}
        c['photos' if r['kind'] == 'photo' else 'videos'] += 1
        c['media'].append({'kind': r['kind'], 'thumb': r['thumb_path'], 'path': r['path'], 'id': r['id'],
                           'note': r['note'], 'duration': r['duration_s']})
    return list(cards.values())


def event_cards(day, limit=40):
    rows = q(f'''SELECT p.*, tl.trip_no, t.code trailer, f.code field_code, eq.code fuel_machine, u.full_name who
                 FROM photos p LEFT JOIN trailer_loads tl ON tl.id=p.load_id LEFT JOIN equipment t ON t.id=tl.trailer_id
                 LEFT JOIN fields f ON f.id=COALESCE(p.field_id, tl.field_id)
                 LEFT JOIN fuel_ops o ON p.entity_type='fuel_op' AND o.id=p.entity_id
                 LEFT JOIN equipment eq ON eq.id=o.equipment_id
                 LEFT JOIN users u ON u.id=p.uploaded_by
                 WHERE p.voided_at IS NULL AND substr(p.uploaded_at,1,10)=?
                   AND p.category IN ({",".join("?" * len(EVENT_PHOTOS))})
                   AND COALESCE(tl.status,'')<>'BEKOR' AND (o.id IS NULL OR o.voided_at IS NULL)
                 ORDER BY p.id DESC LIMIT ?''', (day, *EVENT_PHOTOS, limit * 3))
    cards = {}
    for r in rows:
        grp = ('toldi' if r['category'] in ('trailer', 'cotton', 'field') else
               'weigh' if r['category'].startswith('weigh') else r['category'])
        key = ('load', r['load_id'], grp) if r['load_id'] else (r['entity_type'], r['entity_id'], grp)
        c = cards.get(key)
        if not c:
            if r['category'] == 'fuel':
                place = r['fuel_machine'] or ''
            elif r['category'] in ('nayman',):
                place = 'Punkt' + (f' · {r["trailer"]}' if r['trailer'] else '')
            elif r['category'].startswith('weigh'):
                place = 'Tarozi' + (f' · {r["trailer"]}' if r['trailer'] else '')
            else:
                place = ' · '.join(x for x in (r['field_code'] and f'{r["field_code"]} dala', r['trailer']) if x)
            c = cards[key] = {'src': 'event', 'at': r['uploaded_at'], 'time': r['uploaded_at'][11:16], 'place': place,
                              'title': EVENT_PHOTOS[r['category']] + (f' · {r["trip_no"]}' if r['trip_no'] else ''),
                              'who': r['who'] or '', 'photos': 0, 'videos': 0, 'media': []}
        c['photos'] += 1
        c['media'].append({'kind': 'photo', 'thumb': r['thumb_path'], 'path': r['path'], 'id': r['id'], 'photo_id': r['id']})
    return list(cards.values())


def cards(day=None, limit=12, sources=('telegram', 'event')):
    day = day or today_str()
    out = []
    if 'telegram' in sources:
        out += telegram_cards(day, limit)
    if 'event' in sources:
        out += event_cards(day, limit)
    out.sort(key=lambda c: c['at'], reverse=True)
    return out[:limit]


def status(day=None):
    """So‘raldi / Javob berdi / Kutilmoqda (and who is late) for the day's requests."""
    day = day or today_str()
    r = q('''SELECT COUNT(*) n, SUM(status='JAVOB') ans, SUM(status='KUTILMOQDA') wait, SUM(status='KECHIKDI') late
             FROM media_requests WHERE substr(created_at,1,10)=? AND status<>'BEKOR' ''', (day,), one=True)
    late = q('''SELECT m.full_name, m.role_label, r.text FROM media_requests r JOIN tg_members m ON m.id=r.member_id
                WHERE substr(r.created_at,1,10)=? AND r.status='KECHIKDI' ORDER BY r.id DESC LIMIT 10''', (day,))
    return {'asked': r['n'] or 0, 'answered': r['ans'] or 0, 'waiting': (r['wait'] or 0) + (r['late'] or 0),
            'late': r['late'] or 0, 'late_people': [dict(x) for x in late]}
