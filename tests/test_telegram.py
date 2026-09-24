"""Telegram bot: same services, update_id dedupe, linking, full field+scale flow."""
import pytest

from surxon.db import get_db, q, scalar
from conftest import jpeg

SENT = []


@pytest.fixture(autouse=True)
def fake_telegram(monkeypatch):
    import surxon.telegram_bot as tb
    SENT.clear()

    def api(method, payload=None):
        SENT.append((method, payload))
        return {'ok': True, 'result': {}}
    monkeypatch.setattr(tb, 'tg_api', api)
    monkeypatch.setattr(tb, 'tg_download', lambda file_id: jpeg((10, 120, 40)))
    monkeypatch.setattr(tb, 'notify_async', lambda *a, **k: None)
    yield


class Bot:
    def __init__(self, app, tid):
        self.c, self.tid = app.test_client(), tid
        self.uid = 1000 + tid * 1000  # Telegram update_ids are unique per bot

    def _post(self, upd):
        self.uid += 1
        upd['update_id'] = self.uid
        return self.c.post('/telegram/webhook/hooksecret', json=upd)

    def text(self, t, resend=False):
        if resend:
            self.uid -= 1
        return self._post({'message': {'message_id': self.uid, 'chat': {'id': self.tid, 'type': 'private'},
                                       'from': {'id': self.tid}, 'text': t}})

    def photo(self):
        return self._post({'message': {'message_id': self.uid, 'chat': {'id': self.tid, 'type': 'private'}, 'from': {'id': self.tid},
                                       'photo': [{'file_id': 'f1', 'file_unique_id': f'u{self.uid}'}]}})

    def tap(self, data):
        return self._post({'callback_query': {'id': 'cb', 'data': data, 'from': {'id': self.tid},
                                              'message': {'chat': {'id': self.tid}}}})

    def last(self):
        msgs = [p for m, p in SENT if m == 'sendMessage']
        return msgs[-1]['text'] if msgs else ''


def link(app, admin, username, tid):
    with app.app_context():
        uid = q('SELECT id FROM users WHERE username=?', (username,), one=True)['id']
    code = admin.post(f'/admin/foydalanuvchi/{uid}/telegram', {}).get_json()['code']
    bot = Bot(app, tid)
    bot.text(f'/start {code}')
    assert 'Akkaunt ulandi' in bot.last()
    return bot


def test_webhook_rejects_wrong_secret(app):
    assert app.test_client().post('/telegram/webhook/wrong', json={}).status_code == 404


def test_unlinked_user_is_refused(app, world):
    bot = Bot(app, 555)
    bot.text('/start')
    assert 'ulanmagan' in bot.last()


def test_field_and_scale_flow_through_bot(app, world):
    bri = link(app, world['admin'], 'juma', 777)
    bri.tap('m:open')
    bri.tap(f'ot:{world["eq"]["TL-03"]}')
    bri.tap(f'of:{world["f"]["D-04"]}')
    bri.tap(f'otr:{world["eq"]["T-02"]}')
    assert 'Ochildi' in bri.last()
    bri.text('Gulbahor opa 85\nMirjalol 95,5\nK-01 1500\nnoto‘g‘ri qator')
    reply = bri.last()
    assert 'Gulbahor opa' in reply and '❌' in reply and '1 680' in reply.replace(' ', ' ')
    # Telegram re-delivers the same update -> nothing is duplicated
    bri.text('Gulbahor opa 85\nMirjalol 95,5\nK-01 1500\nnoto‘g‘ri qator', resend=True)
    with app.app_context():
        lid = q('SELECT id FROM trailer_loads', one=True)['id']
        assert scalar('SELECT COUNT(*) FROM harvests') == 3
        assert scalar('SELECT SUM(kg) FROM harvests') == 1680.5
        assert q("SELECT source FROM harvests LIMIT 1", one=True)[0] == 'telegram'
    bri.tap(f'tf:{lid}')
    bri.tap('tsave')                       # no photo yet -> refused
    assert 'rasm' in bri.last().lower()
    bri.photo()
    bri.tap('tsave')
    assert 'TOLDI saqlandi' in bri.last()
    scale = link(app, world['admin'], 'tarozi01', 888)
    scale.tap('m:scale')
    scale.tap(f'sc:{lid}')
    scale.text('3700')
    assert 'Brutto' in scale.last()
    scale.tap(f'sc:{lid}')
    scale.text('2000')                    # net 1700 vs internal 1680.5 -> +1.2% ok
    assert 'PA-000001' in scale.last()
    with app.app_context():
        assert q('SELECT status FROM trailer_loads WHERE id=?', (lid,), one=True)[0] == 'TORTILDI'
        assert scalar("SELECT COUNT(*) FROM photos WHERE source='telegram'") == 1
        assert scalar("SELECT COUNT(*) FROM audit_logs WHERE source='telegram'") >= 6


def test_bot_respects_roles(app, world):
    bri = link(app, world['admin'], 'juma', 901)
    bri.tap('m:scale')                    # brigadier has no scale menu item; crafted callback still refused
    with app.app_context():
        assert scalar('SELECT COUNT(*) FROM weighings') == 0
