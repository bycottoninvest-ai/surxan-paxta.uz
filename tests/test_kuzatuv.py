"""Kuzatuv: people are asked for photos/videos through the bot; answers are stored and shown to the manager.
Also: the read-only report channel gets one line per important event."""
import json
from datetime import timedelta

import pytest

from conftest import jpeg, make_user
from surxon.db import get_db, q, scalar

SENT = []
VIDEO = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 2000


@pytest.fixture(autouse=True)
def fake_telegram(monkeypatch):
    import surxon.telegram_bot as tb
    SENT.clear()
    counter = {'n': 500}

    def api(method, payload=None):
        SENT.append((method, payload))
        counter['n'] += 1
        return {'ok': True, 'result': {'message_id': counter['n']}}

    def download(file_id):
        return VIDEO if file_id.startswith('vid') else jpeg((30, 140, 60))
    monkeypatch.setattr(tb, 'tg_api', api)
    monkeypatch.setattr(tb, 'tg_download', download)
    monkeypatch.setattr(tb, 'notify_async', lambda *a, **k: None)
    yield


class TG:
    """Simulates Telegram webhook deliveries."""

    def __init__(self, app):
        self.c = app.test_client()
        self.uid = 90000

    def post(self, upd, resend=False):
        if not resend:
            self.uid += 1
        upd['update_id'] = self.uid
        r = self.c.post('/telegram/webhook/hooksecret', json=upd)
        assert r.status_code == 200
        return r.get_json()

    def dm(self, tid, first='Mirjalol', resend=False, **msg):
        m = {'message_id': self.uid + 1, 'chat': {'id': tid, 'type': 'private'},
             'from': {'id': tid, 'first_name': first, 'is_bot': False}, **msg}
        return self.post({'message': m}, resend)

    def group(self, chat, tid, first='Ali', **msg):
        m = {'message_id': self.uid + 1, 'chat': {'id': chat, 'type': 'supergroup', 'title': 'Surxon ishchilar'},
             'from': {'id': tid, 'first_name': first, 'is_bot': False}, **msg}
        return self.post({'message': m})


def last_text(chat=None):
    msgs = [p for m, p in SENT if m == 'sendMessage' and (chat is None or p['chat_id'] == chat)]
    return msgs[-1]['text'] if msgs else ''


def photo(uid='ph1'):
    return {'photo': [{'file_id': 'small', 'file_unique_id': uid + 's'}, {'file_id': 'big', 'file_unique_id': uid}]}


def add_member(admin, name='Mirjalol', role='Traktorchi'):
    r = admin.post('/kuzatuv/odamlar', {'full_name': name, 'role_label': role})
    assert r.get_json()['ok'], r.get_data(as_text=True)
    return q('SELECT * FROM tg_members WHERE full_name=?', (name,), one=True)


def test_invite_link_request_and_photo_answer(app, world):
    admin, tg = world['admin'], TG(app)
    with app.app_context():
        m = add_member(admin)
        assert m['status'] == 'FAOL' and m['link_code'].startswith('K-') and m['telegram_id'] is None
    # the person opens the invite link
    tg.dm(900, text=f'/start {m["link_code"]}')
    assert 'Ulandi: Mirjalol (Traktorchi)' in last_text(900)
    with app.app_context():
        m = q('SELECT * FROM tg_members WHERE id=?', (m['id'],), one=True)
        assert m['telegram_id'] == '900' and m['dm_ok'] == 1 and m['link_code'] is None
    # the manager asks for a photo
    r = admin.post('/kuzatuv', {'member_ids': [m['id']], 'text': 'traktor ishlayotgan rasm yoki videoni yuboring',
                                'kind': 'any', 'deadline_min': '60'})
    assert r.get_json()['ok'] and '1 tasi Telegramga yuborildi' in r.get_json()['message']
    req_msg = [p for meth, p in SENT if meth == 'sendMessage' and p['chat_id'] == '900'][-1]
    assert 'tg://user?id=900' in req_msg['text'] and 'traktor ishlayotgan' in req_msg['text'] and req_msg['parse_mode'] == 'HTML'
    with app.app_context():
        req = q('SELECT * FROM media_requests', one=True)
        assert req['status'] == 'KUTILMOQDA' and req['sent_via'] == 'dm' and req['tg_message_id']
    # answer: a photo as a reply to the request
    tg.dm(900, reply_to_message={'message_id': req['tg_message_id']}, caption='1-dala', **photo())
    assert 'Rasm qabul qilindi' in last_text(900)
    with app.app_context():
        item = q('SELECT * FROM media_items', one=True)
        assert item['request_id'] == req['id'] and item['kind'] == 'photo' and item['caption'] == '1-dala'
        assert q('SELECT status FROM media_requests WHERE id=?', (req['id'],), one=True)['status'] == 'JAVOB'
        assert scalar("SELECT COUNT(*) FROM outbox WHERE channel='telegram_archive' AND ref=?", (f'kuzatuv:{item["id"]}',)) == 1
    # the same update delivered twice, and the same file sent again: never a second row
    tg.dm(900, resend=True, reply_to_message={'message_id': req['tg_message_id']}, **photo())
    tg.dm(900, **photo())
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM media_items') == 1
    # the manager sees it; the media file is served to them and hidden from other roles
    page = admin.get('/kuzatuv').get_data(as_text=True)
    assert 'Mirjalol' in page and 'traktor ishlayotgan' in page and 'Javob keldi' in page
    assert admin.get(f'/media/{item["path"]}').status_code == 200
    assert admin.get(f'/media/{item["thumb_path"]}').status_code == 200
    assert world['juma'].get(f'/media/{item["path"]}').status_code == 404
    assert world['bux'].get('/kuzatuv').status_code in (302, 403)          # no access: sent back
    dash = world['rahbar'].get('/?view=full').get_data(as_text=True)
    assert 'Kuzatuv (bugun)' in dash and item['thumb_path'] in dash


def test_video_small_and_too_big(app, world):
    admin, tg = world['admin'], TG(app)
    with app.app_context():
        m = add_member(admin, 'Sadokat', 'Agronom')
    tg.dm(901, first='Sadokat', text=f'/start {m["link_code"]}')
    admin.post('/kuzatuv', {'member_ids': [m['id']], 'text': 'paxtaning hozirgi holati videosini yuboring', 'kind': 'video'})
    tg.dm(901, first='Sadokat', video={'file_id': 'vid1', 'file_unique_id': 'v1', 'file_size': 2100, 'duration': 12,
                                       'mime_type': 'video/mp4', 'thumbnail': {'file_id': 'th1', 'file_unique_id': 't1'}})
    assert 'Video qabul qilindi' in last_text(901)
    tg.dm(901, first='Sadokat', video={'file_id': 'vid2', 'file_unique_id': 'v2', 'file_size': 80 * 1024 * 1024,
                                       'duration': 300, 'mime_type': 'video/mp4'})
    assert '20 MB dan katta' in last_text(901)
    with app.app_context():
        small, big = q('SELECT * FROM media_items ORDER BY id')
        assert small['path'].endswith('.mp4') and small['thumb_path'] and small['duration_s'] == 12
        assert big['path'] is None and 'Telegram arxivida' in big['note']
        kinds = {r['ref']: r['kind'] for r in q("SELECT ref, kind FROM outbox WHERE channel='telegram_archive'")}
        assert kinds[f'kuzatuv:{small["id"]}'] == 'video' and kinds[f'kuzatuv:{big["id"]}'] == 'copy'
    r = admin.get(f'/media/{small["path"]}', headers={'Range': 'bytes=0-99'})
    assert r.status_code == 206 and len(r.data) == 100          # phones stream video with range requests
    assert '<video' in admin.get('/kuzatuv').get_data(as_text=True)


def test_work_group_auto_registration_and_group_answers(app, world):
    admin, tg = world['admin'], TG(app)
    chat = -100555
    # bot added to the group, then two people join
    tg.post({'my_chat_member': {'chat': {'id': chat, 'type': 'supergroup', 'title': 'Surxon ishchilar'},
                                'from': {'id': 1}, 'new_chat_member': {'status': 'administrator', 'user': {'id': 42, 'is_bot': True}}}})
    tg.group(chat, 1, new_chat_members=[{'id': 777, 'first_name': 'Gulbohar', 'is_bot': False},
                                        {'id': 778, 'first_name': 'Robot', 'is_bot': True}])
    with app.app_context():
        g = q('SELECT * FROM tg_members WHERE telegram_id=?', ('777',), one=True)
        assert g['status'] == 'YANGI' and g['source'] == 'guruh' and g['full_name'] == 'Gulbohar'
        assert not q("SELECT 1 FROM tg_members WHERE telegram_id='778'")          # bots are never members
    # admin confirms the group → everyone seen in it becomes active
    r = admin.post('/kuzatuv/odamlar', {'action': 'group', 'chat_id': str(chat), 'on': '1'})
    assert r.get_json()['ok']
    with app.app_context():
        assert q('SELECT status FROM tg_members WHERE id=?', (g['id'],), one=True)['status'] == 'FAOL'
    # she never opened the bot privately → the request goes to the group with a mention
    admin.post('/kuzatuv', {'member_ids': [g['id']], 'text': 'terimchilar ishlayotgan joyni rasmga olib yuboring'})
    sent = [p for m, p in SENT if m == 'sendMessage' and p['chat_id'] == str(chat)][-1]
    assert 'tg://user?id=777' in sent['text']
    with app.app_context():
        req = q('SELECT * FROM media_requests', one=True)
        assert req['sent_via'] == 'group'
    # an unrelated photo from someone else in the group is not stored
    tg.group(chat, 1, first='Boshqa', **photo('x1'))
    # her reply in the group is stored
    tg.group(chat, 777, first='Gulbohar', reply_to_message={'message_id': req['tg_message_id']}, **photo('g1'))
    assert 'Qabul qilindi — Gulbohar' in last_text(chat)
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM media_items') == 1
        assert q('SELECT status FROM media_requests', one=True)['status'] == 'JAVOB'
    # a newcomer joining the confirmed group is active right away; leaving deactivates
    tg.group(chat, 1, new_chat_members=[{'id': 779, 'first_name': 'Yangi', 'is_bot': False}])
    with app.app_context():
        assert q("SELECT status FROM tg_members WHERE telegram_id='779'", one=True)['status'] == 'FAOL'
    tg.group(chat, 779, left_chat_member={'id': 779, 'first_name': 'Yangi', 'is_bot': False})
    with app.app_context():
        assert q("SELECT status FROM tg_members WHERE telegram_id='779'", one=True)['status'] == 'NOFAOL'


def test_stranger_is_registered_but_nothing_stored(app, world):
    tg = TG(app)
    tg.dm(555, first='Notanish', text='salom')
    assert 'ulanmagan' in last_text(555) and 'tasdiqlagach' in last_text(555)
    tg.dm(555, first='Notanish', **photo('s1'))
    with app.app_context():
        assert q("SELECT status FROM tg_members WHERE telegram_id='555'", one=True)['status'] == 'YANGI'
        assert scalar('SELECT COUNT(*) FROM media_items') == 0 and scalar('SELECT COUNT(*) FROM photos') == 0
    # requests cannot target a member who is not approved
    with app.app_context():
        mid = q("SELECT id FROM tg_members WHERE telegram_id='555'", one=True)['id']
    r = world['admin'].post('/kuzatuv', {'member_ids': [mid], 'text': 'rasm yuboring'})
    assert not r.get_json()['ok']


def test_schedule_rules_late_and_report_feed(app, world):
    admin, tg = world['admin'], TG(app)
    with app.app_context():
        m = add_member(admin)
    tg.dm(900, text=f'/start {m["link_code"]}')
    r = admin.post('/kuzatuv/jadval', {'title': 'Ertalab', 'text': 'traktor ishlayotgan rasmni yuboring', 'times': '9:00, 16:00',
                                       'weekdays': list('1234567'), 'member_ids': [m['id']], 'deadline_min': '30'})
    assert r.get_json()['ok'], r.get_data(as_text=True)
    from surxon import kuzatuv as K
    from surxon.utils import now
    with app.app_context():
        rule = q('SELECT * FROM media_rules', one=True)
        assert rule['times'] == '09:00,16:00'
        at = now().replace(hour=9, minute=5, second=0, microsecond=0)
        assert K.run_rules(at) == 1
        assert K.run_rules(at + timedelta(minutes=1)) == 0            # once per slot, never twice
        assert K.run_rules(at.replace(hour=12)) == 0                   # 09:00 is too old to catch up at 12:00
        req = q('SELECT * FROM media_requests', one=True)
        assert req['rule_id'] == rule['id'] and req['slot'].endswith('09:00') and req['sent_via'] == 'dm'
        # past the deadline → KECHIKDI + one line in the report channel
        get_db().execute("UPDATE media_requests SET due_at='2000-01-01 00:00:00'")
        assert K.mark_late() == 1 and K.mark_late() == 0
        assert q('SELECT status FROM media_requests', one=True)['status'] == 'KECHIKDI'
        late = q("SELECT payload_json FROM outbox WHERE channel='telegram_report' AND ref=?", (f'feed:late:{req["id"]}',), one=True)
        assert 'javob kelmadi — Mirjalol' in json.loads(late['payload_json'])['text']
    # a late answer still closes the request
    tg.dm(900, **photo('late1'))
    with app.app_context():
        assert q('SELECT status FROM media_requests', one=True)['status'] == 'JAVOB'
    # remind + cancel from the page
    admin.post('/kuzatuv', {'member_ids': [m['id']], 'text': 'dala rasmini yuboring'})
    with app.app_context():
        rid = q("SELECT id FROM media_requests WHERE status='KUTILMOQDA'", one=True)['id']
    assert admin.post('/kuzatuv', {'action': 'remind', 'request_id': rid}).get_json()['ok']
    assert 'ESLATMA' in last_text('900')
    assert admin.post('/kuzatuv', {'action': 'cancel', 'request_id': rid}).get_json()['ok']
    with app.app_context():
        assert q('SELECT status FROM media_requests WHERE id=?', (rid,), one=True)['status'] == 'BEKOR'
    # toggling the rule off stops it
    admin.post('/kuzatuv/jadval', {'action': 'toggle', 'rule_id': rule['id']})
    with app.app_context():
        assert K.run_rules(now().replace(hour=16, minute=1)) == 0


def test_report_feed_for_cash_movements(app, world):
    world['bux'].post('/buxgalteriya/kirim', {'amount': '50 000 000', 'source': 'Direktor'})
    with app.app_context():
        rows = q("SELECT payload_json FROM outbox WHERE channel='telegram_report' AND kind='feed'")
        texts = [json.loads(r['payload_json'])['text'] for r in rows]
        assert any('KIRIM 50 000 000' in t and 'Direktor' in t and 'INC-' in t for t in texts), texts
        get_db().execute("INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES ('report_feed','0','x')")
    world['bux'].post('/buxgalteriya/kirim', {'amount': '1 000', 'source': 'Direktor'})
    with app.app_context():
        assert scalar("SELECT COUNT(*) FROM outbox WHERE channel='telegram_report' AND kind='feed'") == len(texts)


def test_linked_system_user_photo_routing(app, world):
    """A tally clerk's photo still goes to the photo archive when nothing was asked; answers a request when one is open."""
    admin, tg = world['admin'], TG(app)
    make_user(app, admin, 'mirjalol', 'tally')
    with app.app_context():
        uid = q("SELECT id FROM users WHERE username='mirjalol'", one=True)['id']
    code = admin.post(f'/admin/foydalanuvchi/{uid}/telegram', {}).get_json()['code']
    tg.dm(910, text=f'/start {code}')
    with app.app_context():
        m = q("SELECT * FROM tg_members WHERE user_id=?", (uid,), one=True)
        assert m and m['status'] == 'FAOL' and m['role_label'] == 'Hisobchi (terim)' and m['dm_ok'] == 1
    tg.dm(910, **photo('a1'))
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM photos') == 1 and scalar('SELECT COUNT(*) FROM media_items') == 0
    admin.post('/kuzatuv', {'member_ids': [m['id']], 'text': 'telashka yuklanishini rasmga olib yuboring'})
    tg.dm(910, **photo('a2'))
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM media_items') == 1 and scalar('SELECT COUNT(*) FROM photos') == 1


def test_request_before_person_joins_is_sent_when_they_link(app, world):
    admin, tg = world['admin'], TG(app)
    with app.app_context():
        m = add_member(admin, 'Asadbek', 'Brigadir')
    r = admin.post('/kuzatuv', {'member_ids': [m['id']], 'text': 'paxtaning hozirgi holati rasmini yuboring'})
    assert '0 tasi Telegramga yuborildi' in r.get_json()['message']
    with app.app_context():
        req = q('SELECT * FROM media_requests', one=True)
        assert req['sent_at'] is None and 'taklif havolasini' in req['send_error']
    assert 'yuborilmadi' in admin.get('/kuzatuv').get_data(as_text=True)
    tg.dm(920, first='Asadbek', text=f'/start {m["link_code"]}')
    assert 'paxtaning hozirgi holati' in last_text('920')
    with app.app_context():
        assert q('SELECT sent_via FROM media_requests', one=True)['sent_via'] == 'dm'
