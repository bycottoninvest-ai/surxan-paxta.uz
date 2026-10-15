"""YORDAMCHI-USTOZ: the admin gives a staff member a task; when they come in the assistant greets them, asks how they
are and teaches the task one step at a time (Claude is faked — no real request)."""
from conftest import Client, make_user
from surxon import assistant as A
from surxon.db import get_db, q, scalar
from test_assistant import ai, say  # noqa: F401  (fixture)


def test_admin_gives_a_task_and_the_assistant_teaches_it(app, world, ai):
    admin = world['admin']
    admin.post('/admin/sozlamalar', {'set_ai_yordamchi_on': '1'})
    mirjalol = make_user(app, admin, 'mirjalol', 'tally')
    with app.app_context():
        uid = scalar("SELECT id FROM users WHERE username='mirjalol'")
    assert mirjalol.get('/ai').status_code == 404                     # a field clerk: not on the site without a task
    page = admin.get('/ai/ustoz').get_data(as_text=True)
    assert 'Eski nakladnoylarni tizimga kiritish' in page and 'Buxgalterning kundalik ishi' in page and 'Mirjalol' in page
    assert mirjalol.get('/ai/ustoz').status_code in (302, 404)
    assert not admin.post('/ai/ustoz', {'user_id': uid, 'title': 'x', 'body': ''}).get_json()['ok']
    from surxon.ustoz import READY
    title, body = READY['nakladnoy']
    r = admin.post('/ai/ustoz', {'user_id': uid, 'title': title, 'body': body}).get_json()
    assert r['ok'], r
    # he comes in: straight to the assistant, a big “Boshladik” (the tap lets the phone speak)
    c = Client(app)
    c.get('/login')
    r = c.c.post('/login', data={'username': 'mirjalol', 'password': 'Worker2026x', '_csrf': c.csrf()})
    assert '/ai?boshla=1' in r.headers['Location']
    page = c.get('/ai?boshla=1').get_data(as_text=True)
    assert 'id="go"' in page and '🔊 Boshladik' in page and title in page and '✔ Tugatdim' in page
    home = mirjalol.get('/dala').get_data(as_text=True)
    assert 'ai-bubble' in home and f'{title} — birga qilamiz' in home
    fake = ai(say('Assalomu alaykum, Mirjalol! Charchamadingizmi? Bugun eski nakladnoylarni kiritamiz. Birinchi qadam…'))
    r = mirjalol.post('/ai/savol', {'text': '🚀 Salom! Bugungi ishni birga qilamiz'}).get_json()
    assert r['ok'] and 'Mirjalol' in r['text']
    system = ' '.join(x['text'] for x in fake.sent[0]['system'])
    assert 'USTOZ REJIMI' in system and title in system and 'UY varag‘ining QR' in system and 'hol-ahvol' in system
    with app.app_context():
        user = q('SELECT * FROM users WHERE id=?', (uid,), one=True)
        assert A.todo_count(user) == 1 and A.allowed(user)
        assert A.t_vazifalar(user)['vazifalar'][0].startswith('Admin topshirig‘i')
    # he finishes it: the page is back to normal, the admin sees it done
    with app.app_context():
        gid = scalar('SELECT id FROM yordamchi_guides WHERE user_id=?', (uid,))
    assert world['kassa'].post(f'/ai/ustoz/{gid}/tugadi').get_json()['ok'] is False
    assert mirjalol.post(f'/ai/ustoz/{gid}/tugadi').get_json()['ok']
    with app.app_context():
        assert q('SELECT done_at FROM yordamchi_guides WHERE id=?', (gid,), one=True)['done_at']
        assert scalar("SELECT COUNT(*) FROM audit_logs WHERE entity_type='yordamchi_guide'") == 2
    assert mirjalol.get('/ai').status_code == 404
    assert '✔ tugagan' in admin.get('/ai/ustoz').get_data(as_text=True)


def test_a_new_task_replaces_the_open_one_and_the_accountant_keeps_the_assistant(app, world, ai):
    admin, bux = world['admin'], world['bux']
    admin.post('/admin/sozlamalar', {'set_ai_yordamchi_on': '1'})
    with app.app_context():
        uid = scalar("SELECT id FROM users WHERE role='accountant' ORDER BY id LIMIT 1")
    from surxon.ustoz import READY
    for k in ('buxgalter', 'nakladnoy'):
        assert admin.post('/ai/ustoz', {'user_id': uid, 'title': READY[k][0], 'body': READY[k][1]}).get_json()['ok']
    with app.app_context():
        rows = q('SELECT * FROM yordamchi_guides WHERE user_id=? ORDER BY id', (uid,))
        assert len(rows) == 2 and rows[0]['done_at'] and not rows[1]['done_at']
    assert 'Eski nakladnoylarni' in bux.get('/ai').get_data(as_text=True)
    assert admin.post('/ai/ustoz', {'action': 'done', 'id': rows[1]['id']}).get_json()['ok']
    assert bux.get('/ai').status_code == 200                           # an accountant has the assistant anyway
