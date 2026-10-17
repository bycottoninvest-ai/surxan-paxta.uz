"""Dala nakladnoyi rasmi guruhdan: a full trailer's field waybill photographed into the work group is kept, read in
the background (the reader is replaced here), answered under the photo, shown on the punkt's screen as “Yo‘lda”, and
when the trailer comes Yunus taps KELDI — the paper opens in the UY already read (synthetic data only)."""
import json

from surxon.db import q, scalar
from test_intake import RAW, prepare, save
from test_kuzatuv import SENT, TG, fake_telegram, last_text, photo   # noqa: F401  (the fake Telegram fixture)

CHAT = -100777


def _group(app, admin, tg):
    tg.post({'my_chat_member': {'chat': {'id': CHAT, 'type': 'supergroup', 'title': 'Surxon paxta'}, 'from': {'id': 1},
                                'new_chat_member': {'status': 'administrator', 'user': {'id': 42, 'is_bot': True}}}})
    tg.group(CHAT, 501, first='Sardor', text='salom')
    assert admin.post('/kuzatuv/odamlar', {'action': 'group', 'chat_id': str(CHAT), 'on': '1'}).get_json()['ok']


def _tick(app):
    from surxon.fieldphoto import tick
    with app.app_context():
        return tick()


def test_group_photo_reaches_the_punkt_and_opens_already_read(app, world, monkeypatch):
    calls = []
    yunus, ali, gid, seen = prepare(app, world, monkeypatch, RAW)
    from surxon import talon_ai
    monkeypatch.setattr(talon_ai, 'read_blank', lambda data, names: (calls.append(1), RAW)[1])
    admin, tg = world['admin'], TG(app)
    admin.post('/admin/sozlamalar', {'set_dala_qogoz': '1'})
    _group(app, admin, tg)
    # the field sends the paper of a full trailer — kept at once, read in the background
    tg.group(CHAT, 501, first='Sardor', **photo('pb1'))
    with app.app_context():
        fp = q('SELECT * FROM field_photos', one=True)
        assert fp['status'] == 'YANGI' and fp['sender'] == 'Sardor' and fp['photo_id']
    tg.group(CHAT, 501, first='Sardor', **photo('pb1'))              # Telegram sent it twice: kept once
    assert _tick(app) == 1 and calls == [1]
    with app.app_context():
        fp = q('SELECT * FROM field_photos', one=True)
        assert (fp['status'], fp['blank_no'], fp['total_kg']) == ('YOLDA', 'PB-0001', 1130)
    reply = [p for m, p in SENT if m == 'sendMessage' and p['chat_id'] == CHAT][-1]
    assert 'PB-0001' in reply['text'] and 'punktga yuborildi' in reply['text'] and reply['reply_to_message_id']
    # Yunus sees it on his screen, with the KELDI button into his open UY
    home = yunus.get('/punkt').get_data(as_text=True)
    assert 'YO‘LDA' in home and 'PB-0001' in home and 'KELDI' in home and f'yolda={fp["id"]}' in home
    with app.app_context():
        path = scalar('SELECT thumb_path FROM photos WHERE id=?', (fp['photo_id'],))
    assert yunus.get(f'/media/{path}').status_code == 200                # the punkt may open the photo
    with app.app_context():
        token = scalar('SELECT token FROM load_groups WHERE id=?', (gid,))
    page = yunus.get(f'/punkt/uy/{token}?yolda={fp["id"]}').get_data(as_text=True)
    assert 'Guruhdan kelgan nakladnoy' in page
    # KELDI: the stored photo, the stored reading — no second photo, no second request to the reader
    r = yunus.post(f'/punkt/uy/{gid}/pb-oqish', {'yolda': fp['id']}).get_json()
    assert r['ok'] and r['blank'] == 'PB-0001' and r['yolda'] == fp['id'] and len(r['read']['rows']) == 2, r
    assert calls == [1]
    s = save(yunus, gid, r, yolda=fp['id'])
    assert s['ok'], s
    with app.app_context():
        fp = q('SELECT * FROM field_photos', one=True)
        assert fp['status'] == 'KELDI' and fp['group_id'] == gid
        assert scalar('SELECT COUNT(*) FROM load_group_items WHERE group_id=?', (gid,)) == 2
    assert 'PB-0001' not in yunus.get('/punkt').get_data(as_text=True).split('YO‘LDA')[-1][:300] or \
        'YO‘LDA' not in yunus.get('/punkt').get_data(as_text=True)
    # gone from the list: a second KELDI is refused
    assert not yunus.post(f'/punkt/uy/{gid}/pb-oqish', {'yolda': fp['id']}).get_json()['ok']


def test_other_photos_reader_off_cancel_and_announce(app, world, monkeypatch):
    from surxon import talon_ai
    yunus, ali, gid, seen = prepare(app, world, monkeypatch, RAW)
    admin, tg = world['admin'], TG(app)
    admin.post('/admin/sozlamalar', {'set_dala_qogoz': '1'})
    _group(app, admin, tg)
    # a picture that is not a field waybill: set aside silently
    monkeypatch.setattr(talon_ai, 'read_blank', lambda data, names: {'blank_no': None, 'rows': [], 'total_kg': None})
    n = len(SENT)
    tg.group(CHAT, 501, first='Sardor', **photo('cat'))
    _tick(app)
    with app.app_context():
        assert q('SELECT status FROM field_photos', one=True)['status'] == 'BOSHQA'
    assert not [p for m, p in SENT[n:] if m == 'sendMessage']
    assert 'YO‘LDA' not in yunus.get('/punkt').get_data(as_text=True)
    # reader switched off: still on the punkt's list, the group is told
    app.config['SURXON'].ANTHROPIC_API_KEY = ''
    monkeypatch.setattr(talon_ai, 'enabled', lambda: False)
    tg.group(CHAT, 501, first='Sardor', **photo('pb2'))
    _tick(app)
    assert 'punktga yuborildi' in last_text(CHAT)
    with app.app_context():
        fid = scalar("SELECT id FROM field_photos WHERE status='YOLDA'")
    assert 'YO‘LDA' in yunus.get('/punkt').get_data(as_text=True)
    # Yunus takes a wrong one off the list; another punkt's clerk can too (it is the same list) — but not a stranger
    assert world['juma'].post(f'/punkt/yolda/{fid}/bekor', {'reason': 'x'}).status_code in (302, 403)
    assert yunus.post(f'/punkt/yolda/{fid}/bekor', {'reason': 'takror'}).get_json()['ok']
    with app.app_context():
        assert q('SELECT status, void_reason FROM field_photos WHERE id=?', (fid,), one=True)['status'] == 'BEKOR'
    # the morning message: the bot posts it to the work group
    page = admin.get('/kuzatuv').get_data(as_text=True)
    assert 'Guruhga xabar yuborish' in page and 'DIQQAT, HAMMAGA' in page
    r = admin.post('/kuzatuv', {'action': 'announce', 'text': '📢 DIQQAT! Nakladnoy rasmini guruhga yuboring.'}).get_json()
    assert r['ok'] and '1 ta guruh' in r['message'], r
    assert 'Nakladnoy rasmini' in last_text(CHAT)
    assert world['rahbar'].post('/kuzatuv', {'action': 'announce', 'text': 'salom salom'}).get_json()['ok']
    assert yunus.post('/kuzatuv', {'action': 'announce', 'text': 'salom salom'}).status_code in (302, 403)
