"""The assistant's Uzbek voice (Gemini TTS, faked — no real request): only the person's own answers are read, the audio
is cached, the key is kept like the Claude key, and without it the phone reads as before."""
import base64
import json
from types import SimpleNamespace as NS

from surxon import gemini
from surxon.db import get_db, q


def fake_google(calls):
    def post(url, headers=None, data=None, timeout=None):
        calls.append({'url': url, 'body': json.loads(data), 'key': headers.get('x-goog-api-key')})
        pcm = b'\x00\x01' * 2400
        return NS(status_code=200, json=lambda: {'candidates': [{'content': {'parts': [
            {'inlineData': {'mimeType': 'audio/L16;codec=pcm;rate=24000', 'data': base64.b64encode(pcm).decode()}}]}}]})
    return post


def test_uzbek_voice_reads_only_the_assistants_answers(app, world, monkeypatch):
    import requests
    rahbar, admin = world['rahbar'], world['admin']
    calls = []
    monkeypatch.setattr(requests, 'post', fake_google(calls))
    monkeypatch.setattr(gemini, '_verify', lambda k: True)
    app.config['SURXON'].ANTHROPIC_API_KEY = 'test-key-not-real'
    try:
        page = admin.get('/admin/integratsiyalar').get_data(as_text=True)
        assert 'o‘zbekcha ovozi' in page and 'Ulanmagan — telefon ovozi' in page
        assert 'UZ_VOICE = false' in rahbar.get('/ai').get_data(as_text=True)
        assert not admin.post('/admin/integratsiyalar', {'action': 'gemini_key_save', 'gemini_key': 'qisqa'}).get_json()['ok']
        key = 'AIza' + 'k' * 35
        assert admin.post('/admin/integratsiyalar', {'action': 'gemini_key_save', 'gemini_key': key}).get_json()['ok']
        with app.app_context():
            assert gemini.enabled() and gemini.key_tail() == '…kkkk'
            assert key not in json.dumps([dict(r) for r in q('SELECT * FROM audit_logs')]) and key not in json.dumps(
                [dict(r) for r in q('SELECT * FROM settings')])
        page = admin.get('/admin/integratsiyalar').get_data(as_text=True)
        assert 'Ulangan …kkkk' in page and key not in page and 'Ovozni sinash' in page
        assert 'UZ_VOICE = true' in rahbar.get('/ai').get_data(as_text=True)
        # an answer of this person is read; any other text is refused (not a free speech service)
        with app.app_context():
            uid = q("SELECT id FROM users WHERE username='rahbar'", one=True)['id']
            get_db().execute("INSERT INTO yordamchi_messages(user_id, role, text, created_at) VALUES (?, 'assistant', ?, '2026-10-14 20:00:00')",
                             (uid, 'Bugun 12 450 kg paxta terildi 👍'))
            get_db().commit()
        r = rahbar.post('/ai/ovoz', {'text': 'Bugun 12 450 kg paxta terildi 👍'})
        assert r.status_code == 200 and r.mimetype == 'audio/wav' and r.data[:4] == b'RIFF'
        body = calls[0]['body']
        assert body['contents'][0]['parts'][0]['text'] == 'Bugun 12450 kg paxta terildi'
        assert body['generationConfig']['responseModalities'] == ['AUDIO'] and calls[0]['key'] == key
        assert 'gemini-3.8-flash-tts:generateContent' in calls[0]['url']
        rahbar.post('/ai/ovoz', {'text': 'Bugun 12 450 kg paxta terildi 👍'})
        assert len(calls) == 1                                              # the same sentence: from the cache
        assert rahbar.post('/ai/ovoz', {'text': 'Boshqa istalgan matn'}).status_code == 422
        assert admin.get('/admin/integratsiyalar/ovoz-sinov').mimetype == 'audio/wav'
        assert admin.post('/admin/integratsiyalar', {'action': 'gemini_key_clear'}).get_json()['ok']
        with app.app_context():
            assert not gemini.enabled()
    finally:
        app.config['SURXON'].ANTHROPIC_API_KEY = ''


def test_renamed_model_is_found_by_itself(app, world, monkeypatch):
    import requests
    calls = []
    good = fake_google([])

    def post(url, headers=None, data=None, timeout=None):
        calls.append(url)
        if 'gemini-3.8-flash-tts:' in url:
            return NS(status_code=404, json=lambda: {'error': {'message': 'models/gemini-3.8-flash-tts is not found'}})
        return good(url, headers, data, timeout)
    monkeypatch.setattr(requests, 'post', post)
    monkeypatch.setattr(requests, 'get', lambda url, params=None, headers=None, timeout=None: NS(
        status_code=200, json=lambda: {'models': [{'name': 'models/gemini-3.1-flash-tts-preview'}, {'name': 'models/gemini-3.1-pro'}]}))
    app.config['SURXON'].GEMINI_API_KEY = 'AIza' + 'z' * 35
    try:
        with app.app_context():
            assert gemini.tts('Salom, paxta')[:4] == b'RIFF'
            assert gemini.model() == 'gemini-3.1-flash-tts-preview'
        assert 'gemini-3.1-flash-tts-preview:generateContent' in calls[-1]
    finally:
        app.config['SURXON'].GEMINI_API_KEY = ''
