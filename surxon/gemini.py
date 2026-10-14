"""The assistant's Uzbek voice: Google Gemini text-to-speech (the phones' own voices have no Uzbek and read it with a
Russian or Turkish accent — “pakhta”).

Only the assistant's own answers are spoken (the text must be one of the person's last answers — this is not a free
speech service for anyone), the audio is kept in a small cache (the same sentence is never paid twice), and without a
key — or when Google does not answer — the phone's voice reads it as before.

The key: the server's .env (GEMINI_API_KEY) if set, else the one the admin pasted on Admin → Integratsiyalar — kept in a
private file next to the database (never in the database, the backups, the audit or on a page; only its last 4 characters
are shown). The model and the voice are settings (gemini_tts_model, gemini_tts_voice) — Google renames TTS models often.
"""
import hashlib
import io
import json
import os
import re
import wave
from pathlib import Path

from flask import current_app

from .settings import get_setting
from .utils import UserError

KEY_FILE = 'gemini-api.key'
KEY_RE = re.compile(r'[A-Za-z0-9._-]{30,200}')     # AIza… and Google's newer key formats — Google itself checks it
API = 'https://generativelanguage.googleapis.com/v1beta'
DEFAULT_MODEL = 'gemini-3.8-flash-tts'
DEFAULT_VOICE = 'Kore'
RATE = 24000                    # Gemini TTS returns raw 16-bit mono PCM at 24 kHz
MAX_CHARS = 1500


def _key_path():
    return Path(current_app.config['SURXON'].DATA_DIR) / KEY_FILE


def _saved_key():
    try:
        return _key_path().read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def api_key():
    return current_app.config['SURXON'].GEMINI_API_KEY or _saved_key()


def enabled():
    return bool(api_key())


def source():
    if current_app.config['SURXON'].GEMINI_API_KEY:
        return 'env'
    return 'site' if _saved_key() else ''


def key_tail():
    k = api_key()
    return ('…' + k[-4:]) if k else ''


def model():
    return (get_setting('gemini_tts_auto_model') or '').strip() or (get_setting('gemini_tts_model') or '').strip() or DEFAULT_MODEL


def _find_tts_model():
    """Google renames its TTS models: ask which ones this key has (names with “tts”), prefer flash, remember it."""
    import requests
    try:
        r = requests.get(f'{API}/models', params={'pageSize': 1000}, headers={'x-goog-api-key': api_key()}, timeout=20)
        names = [m['name'].split('/', 1)[-1] for m in r.json().get('models', []) if 'tts' in m.get('name', '')]
    except Exception:
        return None
    if not names:
        return None
    best = sorted(names, key=lambda n: (-_version(n), 'flash' not in n, 'lite' in n))[0]
    from .db import tx
    from .utils import now_str
    with tx() as db:
        db.execute("INSERT INTO settings(key, value, updated_at) VALUES ('gemini_tts_auto_model', ?, ?) ON CONFLICT(key) DO "
                   "UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (best, now_str()))
    return best


def _version(name):
    m = re.search(r'gemini-(\d+(?:\.\d+)?)', name)
    return float(m.group(1)) if m else 0.0


def voice():
    return (get_setting('gemini_tts_voice') or '').strip() or DEFAULT_VOICE


def _verify(key):
    """Asks Google whether the key works (the model list — free). True / False, None: no answer."""
    import requests
    try:
        r = requests.get(f'{API}/models', params={'pageSize': 1}, headers={'x-goog-api-key': key}, timeout=20)
    except requests.RequestException:
        return None
    if r.status_code == 200:
        return True
    return False if r.status_code in (400, 401, 403) else None


def save_key(raw):
    key = re.sub(r'\s+', '', raw or '').strip('\'"«»“”')
    if not KEY_RE.fullmatch(key):
        raise UserError('Kalit to‘liq nusxalanmagan — aistudio.google.com → API Keys → kalit yonidagi 📋 belgisini bosib, '
                        'qaytadan qo‘ying.')
    ok = _verify(key)
    if ok is False:
        raise UserError('Google bu kalitni qabul qilmadi — kalit o‘chirilgan yoki to‘liq nusxalanmagan. Yangi kalit yarating.')
    path = _key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        out.write(key)
    os.replace(tmp, path)
    return ok


def clear_key():
    _key_path().unlink(missing_ok=True)


def speakable(text):
    """What is read aloud: no emoji, no arrows/bullets, numbers kept (12 450 → read as a number)."""
    t = re.sub(r'[\U0001F000-\U0001FAFF☀-➿️›•→]', ' ', text or '')
    t = t.replace(' ', ' ').replace(' ', ' ')
    t = re.sub(r'(?<=\d) (?=\d{3}\b)', '', t)          # 12 450 → 12450, read as one number
    return re.sub(r'\s+', ' ', t).strip()[:MAX_CHARS]


def _wav(pcm):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def _cache_path(text):
    h = hashlib.sha256(f'{model()}|{voice()}|{text}'.encode()).hexdigest()[:32]
    return Path(current_app.config['SURXON'].DATA_DIR) / 'tts_cache' / f'{h}.wav'


def tts(text):
    """The sentence as WAV bytes in an Uzbek voice. Raises UserError (the phone then reads it itself)."""
    import requests
    text = speakable(text)
    if not text:
        raise UserError('O‘qiladigan matn yo‘q.')
    if not enabled():
        raise UserError('Gemini kaliti ulanmagan.')
    path = _cache_path(text)
    if path.exists():
        return path.read_bytes()
    body = {'contents': [{'parts': [{'text': text}]}],
            'generationConfig': {'responseModalities': ['AUDIO'],
                                 'speechConfig': {'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': voice()}}}}}

    def call(name):
        return requests.post(f'{API}/models/{name}:generateContent', headers={'x-goog-api-key': api_key(),
                             'Content-Type': 'application/json'}, data=json.dumps(body), timeout=60)
    try:
        r = call(model())
        if r.status_code == 404:                  # the model was renamed: find the one this key has and try once more
            found = _find_tts_model()
            if found:
                r = call(found)
    except requests.RequestException:
        raise UserError('Google’ga ulanib bo‘lmadi (internet).')
    if r.status_code != 200:
        try:
            msg = r.json().get('error', {}).get('message', '')[:200]
        except ValueError:
            msg = ''
        raise UserError(f'Gemini ovoz xatosi ({r.status_code}){": " + msg if msg else ""}')
    try:
        part = next(p for c in r.json()['candidates'] for p in c['content']['parts'] if p.get('inlineData'))
        import base64
        data = base64.b64decode(part['inlineData']['data'])
    except (KeyError, StopIteration, ValueError):
        raise UserError('Gemini ovoz qaytarmadi.')
    mime = part['inlineData'].get('mimeType', '')
    out = data if data[:4] == b'RIFF' or 'wav' in mime else _wav(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)
    return out
