"""YORDAMCHI (AI): the chat answers from the system through read-only tools, per the asker's permissions, keeps the
conversation in our database, never sends workers' names, stops at the monthly limit (Claude is faked — no real request)."""
import json
from types import SimpleNamespace as NS

import pytest

from conftest import Client, make_user
from surxon import assistant as A
from surxon.db import get_db, q, scalar
from test_dala import open_trip, weigh


class FakeClaude:
    """Plays a scripted conversation: each create() returns the next response and records what was sent."""
    def __init__(self, script):
        self.script, self.sent = list(script), []
        self.messages = self

    def create(self, **kw):
        self.sent.append(json.loads(json.dumps(kw, default=lambda o: getattr(o, '__dict__', str(o)))))
        return self.script.pop(0)


def tool(name, args=None, tid='t1'):
    return NS(stop_reason='tool_use', usage=NS(input_tokens=100, output_tokens=20, cache_read_input_tokens=0),
              content=[NS(type='tool_use', name=name, input=args or {}, id=tid)])


def say(text):
    return NS(stop_reason='end_turn', usage=NS(input_tokens=120, output_tokens=40, cache_read_input_tokens=0),
              content=[NS(type='text', text=text)])


@pytest.fixture
def ai(app, monkeypatch):
    app.config['SURXON'].ANTHROPIC_API_KEY = 'test-key-not-real'
    holder = {}

    def use(*script):
        holder['c'] = FakeClaude(script)
        monkeypatch.setattr(A, '_client', lambda: holder['c'])
        return holder['c']
    yield use
    app.config['SURXON'].ANTHROPIC_API_KEY = ''


def station(app, admin):
    admin.post('/admin/foydalanuvchilar', {'username': 'yunus', 'full_name': 'Yunus Aliyev', 'role': 'station',
                                           'password': 'Worker2026x', 'station_id': 1})
    with app.app_context():
        get_db().execute("UPDATE users SET must_change_password=0 WHERE username='yunus'")
    return Client(app, 'yunus', 'Worker2026x')


def test_director_asks_and_gets_an_answer_from_the_system(app, world, ai):
    rahbar = world['rahbar']
    fake = ai(tool('vazifalarim'), tool('sahifa_tugmasi', {'nom': 'sverka'}, 't2'), say('Bugun hammasi joyida 👍'))
    page = rahbar.get('/ai').get_data(as_text=True)
    assert 'Assalomu alaykum' in page and 'Bugun nima bo‘ldi?' in page
    assert 'ai-fab' in rahbar.get('/rahbar').get_data(as_text=True)                 # the round button on every page
    r = rahbar.post('/ai/savol', {'text': 'Bugun nima qilishim kerak?'}).get_json()
    assert r['ok'] and r['text'] == 'Bugun hammasi joyida 👍'
    assert r['links'] == [{'title': 'Kunlik sverka', 'url': '/rahbar/sverka'}]
    first = fake.sent[0]
    assert first['output_config'] == {'effort': 'low'} and first['system'][0]['cache_control'] == {'type': 'ephemeral'}
    assert 'Kunlik sverka' in first['system'][0]['text'] and 'direktor / rahbar' in first['system'][1]['text']
    names = {t['name'] for t in first['tools']}
    assert {'sverka', 'kassa', 'nazorat', 'vazifalarim', 'sahifa_tugmasi'} <= names
    # the tool result went back to Claude in the next call
    assert fake.sent[1]['messages'][-1]['content'][0]['type'] == 'tool_result'
    with app.app_context():
        rows = q('SELECT role, text FROM yordamchi_messages ORDER BY id')
        assert [(x['role'], x['text']) for x in rows] == [('user', 'Bugun nima qilishim kerak?'), ('assistant', 'Bugun hammasi joyida 👍')]
    # the memory: the next question carries the earlier turn; “Yangi suhbat” starts clean
    fake = ai(say('Ha.'))
    rahbar.post('/ai/savol', {'text': 'Rostdanmi?'})
    assert [m['role'] for m in fake.sent[0]['messages']] == ['user', 'assistant', 'user']
    assert rahbar.post('/ai/yangi', {}).get_json()['ok']
    fake = ai(say('Salom!'))
    rahbar.post('/ai/savol', {'text': 'Salom'})
    assert len(fake.sent[0]['messages']) == 1
    assert 'Salom!' in rahbar.get('/ai').get_data(as_text=True)


def test_punkt_operator_sees_only_punkt_tools(app, world, ai):
    yunus = station(app, world['admin'])
    fake = ai(tool('kassa'), say('Kassa sizga yopiq.'))
    assert yunus.get('/ai').status_code == 200                   # the closed punkt app lets the assistant in
    r = yunus.post('/ai/savol', {'text': 'Kassada qancha pul bor?'}).get_json()
    assert r['ok']
    names = {t['name'] for t in fake.sent[0]['tools']}
    assert 'kassa' not in names and 'sverka' not in names and 'kun_holati' in names
    result = fake.sent[1]['messages'][-1]['content'][0]['content']
    assert 'yopiq' in result and 'qoldiq' not in result
    assert 'punkt operatori' in fake.sent[0]['system'][1]['text']


def test_not_for_field_clerk_and_off_without_key(app, world, ai):
    tally = make_user(app, world['admin'], 'terim1', 'tally')
    ai(say('x'))
    assert tally.get('/ai').status_code == 404
    assert tally.post('/ai/savol', {'text': 'salom'}).status_code == 403
    assert 'ai-fab' not in tally.get('/dala').get_data(as_text=True)
    app.config['SURXON'].ANTHROPIC_API_KEY = ''
    assert world['rahbar'].get('/ai').status_code == 404
    assert 'ai-fab' not in world['rahbar'].get('/rahbar').get_data(as_text=True)


def test_workers_names_never_reach_claude(app, world, ai):
    admin = world['admin']
    admin.post('/admin/sozlamalar', {'set_dala_qogoz': '0'})
    tally = make_user(app, admin, 'terim1', 'tally')
    lid = open_trip(tally, world)
    assert weigh(tally, lid, '55', name='Zuhra Karimova', new=True)['ok']
    with app.app_context():
        user = q("SELECT * FROM users WHERE username='admin'", one=True)
        from flask import g
        with app.test_request_context():
            g.user = user
            out = json.dumps([A.run_tool(user, t['name'], {}) for t in A.tools_for(user)
                              if t['name'] not in ('yoriqnoma', 'sahifa_tugmasi')], ensure_ascii=False, default=str)
    assert '"reyslar": 1' in out
    assert 'Zuhra' not in out and 'Karimova' not in out


def test_monthly_limit(app, world, ai):
    world['admin'].post('/admin/sozlamalar', {'set_ai_yordamchi_limit': '1'})
    ai(say('Birinchi javob'))
    assert world['rahbar'].post('/ai/savol', {'text': 'bir'}).get_json()['ok']
    r = world['rahbar'].post('/ai/savol', {'text': 'ikki'})
    assert r.status_code == 422 and 'chegarasi' in r.get_json()['error']
    with app.app_context():
        assert A.month_usage()['questions'] == 1
