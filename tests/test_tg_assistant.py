"""The assistant in Telegram (Claude and Gemini faked): a linked staff member asks privately by text or voice and gets
text (+ an Uzbek voice message); in a work group the bot answers only when addressed, and never with money tools."""
import pytest

from surxon import assistant as A, gemini, tg_assistant
from surxon.db import q
from test_assistant import FakeClaude, say, tool
from test_telegram import SENT, Bot, fake_telegram, link  # noqa: F401 — the autouse fake Telegram


class Now:
    """Runs the background answer at once (tests read the result right after the webhook)."""
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def tg(app, monkeypatch):
    import surxon.outbox as ob
    app.config['SURXON'].ANTHROPIC_API_KEY = 'test-key-not-real'
    app.config['SURXON'].TELEGRAM_BOT_USERNAME = 'SurxonPaxtaBot'
    monkeypatch.setattr(tg_assistant.threading, 'Thread', Now)
    uploads = []
    monkeypatch.setattr(ob, 'telegram_upload', lambda method, fields, files, token=None: uploads.append((method, fields, files)))
    holder = {}

    def use(*script):
        holder['c'] = FakeClaude(script)
        monkeypatch.setattr(A, '_client', lambda: holder['c'])
        return holder['c']
    yield use, uploads
    app.config['SURXON'].ANTHROPIC_API_KEY = ''
    app.config['SURXON'].TELEGRAM_BOT_USERNAME = ''


def test_private_text_and_voice(app, world, tg, monkeypatch):
    use, uploads = tg
    bot = link(app, world['admin'], 'buxgalter', 5501)
    fake = use(tool('kun_holati'), say('Bugun 3 ta telashka, 4 120 kg paxta terildi.'))
    bot.text('Bugun qancha paxta terildi?')
    assert 'kg paxta terildi' in bot.last()
    assert any(m == 'sendChatAction' for m, _ in SENT)
    assert 'Telegram botda' in fake.sent[0]['system'][-1]['text']
    assert not uploads                                         # a written question: a written answer
    # a voice message: heard by Gemini, answered in text and in an Uzbek voice
    app.config['SURXON'].GEMINI_API_KEY = 'AIza' + 'v' * 35
    try:
        monkeypatch.setattr(gemini, 'transcribe', lambda audio, mime='audio/ogg': 'Kassada qancha pul bor?')
        monkeypatch.setattr(gemini, 'tts', lambda text: b'RIFF-wav')
        monkeypatch.setattr(gemini, 'mp3', lambda wav: b'ID3-mp3')
        use(tool('kassa'), say('Kassada 2 450 000 so‘m bor.'))
        bot._post({'message': {'message_id': 9, 'chat': {'id': 5501, 'type': 'private'}, 'from': {'id': 5501},
                               'voice': {'file_id': 'v1', 'mime_type': 'audio/ogg', 'duration': 3}}})
        texts = [p['text'] for m, p in SENT if m == 'sendMessage']
        assert '🎤 Kassada qancha pul bor?' in texts and 'Kassada 2\xa0450\xa0000 so‘m bor.' in texts
        assert uploads and uploads[-1][0] == 'sendVoice' and uploads[-1][2]['voice'][1] == b'ID3-mp3'
    finally:
        app.config['SURXON'].GEMINI_API_KEY = ''
    with app.app_context():
        assert q("SELECT COUNT(*) n FROM yordamchi_messages WHERE role='user'", one=True)['n'] == 2


def test_group_answers_only_when_addressed_and_without_money(app, world, tg):
    use, _ = tg
    link(app, world['admin'], 'buxgalter', 5502)
    g = Bot(app, 5502)

    def group(text, reply_bot=False):
        msg = {'message_id': g.uid, 'chat': {'id': -1001, 'type': 'supergroup', 'title': 'Ish guruhi'},
               'from': {'id': 5502, 'first_name': 'Asadbek'}, 'text': text}
        if reply_bot:
            msg['reply_to_message'] = {'message_id': 1, 'from': {'id': 1, 'is_bot': True, 'username': 'SurxonPaxtaBot'}}
        return g._post({'message': msg})
    SENT.clear()
    fake = use(say('x'))
    group('Bugun ob-havo yaxshi')                              # ordinary chatter: left alone
    assert not fake.sent and not [p for m, p in SENT if m == 'sendMessage']
    fake = use(tool('kassa'), say('Bu haqda menga shaxsiy yozing 🙂'))
    group('Yordamchi, kassada qancha pul bor?')
    names = {t['name'] for t in fake.sent[0]['tools']}
    assert names == {'kun_holati', 'telashkalar', 'yoriqnoma'}
    assert 'yopiq' in fake.sent[1]['messages'][-1]['content'][0]['content']
    assert fake.sent[0]['messages'][-1]['content'] == 'kassada qancha pul bor?'
    assert 'ISHCHI GURUH' in fake.sent[0]['system'][-1]['text']
    sent = [p for m, p in SENT if m == 'sendMessage'][-1]
    assert sent['chat_id'] == -1001 and sent['reply_to_message_id']
    use(say('TL-05 yo‘lda.'))
    group('TL-05 qayerda?', reply_bot=True)
    assert 'TL-05 yo‘lda.' in g.last()
    use(say('Salom!'))
    group('@SurxonPaxtaBot salom')
    assert g.last() == 'Salom!'


def test_stranger_in_group_gets_safe_answers_and_privately_is_told_how_to_link(app, world, tg):
    use, _ = tg
    fake = use(tool('kassa'), say('Bugun 5 ta telashka keldi.'))
    g = Bot(app, 7777)
    g._post({'message': {'message_id': 5, 'chat': {'id': -1002, 'type': 'group', 'title': 'Dala'},
                         'from': {'id': 7777, 'first_name': 'Bunyod'}, 'text': 'Yordamchi, bugun nechta telashka keldi?'}})
    assert g.last() == 'Bugun 5 ta telashka keldi.'
    assert {t['name'] for t in fake.sent[0]['tools']} == {'kun_holati', 'telashkalar', 'yoriqnoma'}
    assert 'yopiq' in fake.sent[1]['messages'][-1]['content'][0]['content']
    assert 'Bunyod' in fake.sent[0]['system'][1]['text'] and 'guruh a’zosi' in fake.sent[0]['system'][1]['text']
    with app.app_context():
        assert q('SELECT COUNT(*) n FROM yordamchi_messages', one=True)['n'] == 0      # no memory for strangers
        assert A.month_usage()['guests'] == 1
    g.text('salom')                                            # privately: not linked → the existing linking flow
    assert 'ulanmagan' in g.last()


def test_director_sends_a_task_to_the_work_group(app, world, tg, monkeypatch):
    use, uploads = tg
    from surxon.db import get_db
    bot = link(app, world['admin'], 'rahbar', 5503)
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO tg_chats(chat_id, title, type, first_seen_at, last_seen_at, is_work) VALUES ('-1009','PAXTA_SURXAN','supergroup','2026-10-14','2026-10-14',1)")
        db.execute("INSERT INTO tg_members(full_name, telegram_id, status, created_at) VALUES ('Yunus Boy', '8801', 'FAOL', '2026-10-14')")
        db.commit()
    app.config['SURXON'].GEMINI_API_KEY = 'AIza' + 't' * 35
    try:
        monkeypatch.setattr(gemini, 'tts', lambda text: b'RIFF')
        monkeypatch.setattr(gemini, 'mp3', lambda wav: b'MP3')
        fake = use(tool('topshiriq', {'kimga': 'Yunus', 'matn': 'TL-05 ni punktda qabul qilib, rasmini yuboring'}),
                   say('Yunusga topshiriq guruhga yuborildi ✅'))
        bot.text('Yunusga ayt: TL-05 ni qabul qilib, rasmini yuborsin')
        task = [p for m, p in SENT if m == 'sendMessage' and p.get('chat_id') == '-1009']
        assert task and 'tg://user?id=8801' in task[-1]['text'] and 'TL-05' in task[-1]['text'] and task[-1]['parse_mode'] == 'HTML'
        assert uploads and uploads[-1][0] == 'sendVoice' and uploads[-1][1]['chat_id'] == '-1009'
        assert 'yuborildi' in bot.last()
    finally:
        app.config['SURXON'].GEMINI_API_KEY = ''
    # an accountant has no such tool
    b2 = link(app, world['admin'], 'buxgalter', 5504)
    fake = use(say('x'))
    b2.text('Hammaga ayt: ertaga ish yo‘q')
    assert 'topshiriq' not in {t['name'] for t in fake.sent[0]['tools']}
