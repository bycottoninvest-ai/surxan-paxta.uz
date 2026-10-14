"""The assistant in Telegram: a staff member writes — or sends a voice message — to the bot, privately or in a work group
where the bot is admin; the answer comes as text and, when Gemini is connected, as an Uzbek voice message.

Private chat: every linked staff member, with their own permissions (the same assistant as on the site, the same memory).
Work group: the bot answers only when it is addressed — its @name, a reply to its message, or a message that starts with
“Yordamchi” (a voice message: a reply to the bot) — so ordinary chatter is left alone; and there everybody reads the
answer, so it talks only about kg, trips and how to use the system (never money or names). Someone the system does not
know is told how to link their account. It runs off the webhook thread: Telegram gets its answer at once.
"""
import re
import threading

from flask import current_app

from .db import q

TRIGGER = re.compile(r'^\s*(yordamchi|ёрдамчи|yordamchim)\b[\s,:!.-]*', re.I)


def _user(tid):
    return q('SELECT * FROM users WHERE telegram_id=? AND active=1', (str(tid),), one=True)


def addressed(m, bot_username):
    """(is the bot asked?, the question text without the @name / “Yordamchi”) for a group message."""
    text = (m.get('text') or m.get('caption') or '').strip()
    reply = m.get('reply_to_message') or {}
    to_bot = bool(reply.get('from', {}).get('is_bot')) and (
        not bot_username or (reply.get('from', {}).get('username') or '').lower() == bot_username.lower())
    mention = bool(bot_username) and f'@{bot_username.lower()}' in text.lower()
    if mention:
        text = re.sub(rf'@{re.escape(bot_username)}', '', text, flags=re.I).strip()
    trig = TRIGGER.match(text)
    if trig:
        text = text[trig.end():].strip()
    return (to_bot or mention or bool(trig)), text


def wants(m, user, group):
    """Should the assistant take this message? (text or voice; in a group only when addressed)."""
    from . import assistant as A
    if not A.enabled():
        return False
    if not (m.get('text') or m.get('voice') or m.get('audio')):
        return False
    if (m.get('text') or '').startswith('/'):
        return False
    if group:
        return addressed(m, current_app.config['SURXON'].TELEGRAM_BOT_USERNAME)[0]
    return bool(user) and A.allowed(user, 'telegram')


def handle(m, group=False):
    """Answer in the background (Claude may think for a while; the webhook returns at once)."""
    app = current_app._get_current_object()
    threading.Thread(target=_run, args=(app, m, group), daemon=True).start()


def _action(chat, what):
    from .telegram_bot import tg_api
    try:
        tg_api('sendChatAction', {'chat_id': chat, 'action': what})
    except Exception:
        pass


def _run(app, m, group):
    from . import assistant as A, gemini
    from .telegram_bot import send, tg_download
    from .utils import UserError
    with app.app_context():
        chat, mid = m['chat']['id'], m.get('message_id')
        reply_to = mid if group else None
        user = _user(m['from']['id'])
        if not user or not A.allowed(user, 'telegram'):
            send(chat, '🤝 Men SURXON yordamchisiman. Savol berish uchun Telegramingiz tizimga ulangan '
                       'bo‘lishi kerak — admin “Foydalanuvchilar” sahifasidan sizga kod beradi (/start KOD).', reply_to=reply_to)
            return
        voice_in = bool(m.get('voice') or m.get('audio'))
        try:
            _action(chat, 'typing')
            if voice_in:
                v = m.get('voice') or m.get('audio')
                text = gemini.transcribe(tg_download(v['file_id']), v.get('mime_type') or 'audio/ogg')
                send(chat, f'🎤 {text}', reply_to=reply_to)
            else:
                text = addressed(m, app.config['SURXON'].TELEGRAM_BOT_USERNAME)[1] if group else (m.get('text') or '')
            if not text.strip():
                send(chat, 'Savolingizni yozing yoki ayting 🙂', reply_to=reply_to)
                return
            r = A.ask(user, text, channel='telegram', group=group)
        except UserError as err:
            send(chat, f'⚠️ {err}', reply_to=reply_to)
            return
        except Exception as exc:              # never leave the person without an answer
            app.logger.exception('telegram assistant failed: %s', exc)
            send(chat, '⚠️ Hozir javob bera olmadim — birozdan keyin qayta so‘rang.', reply_to=reply_to)
            return
        domain = app.config['SURXON'].DOMAIN
        buttons = [[{'text': f'{lk["title"]} ›', 'url': f'https://{domain}{lk["url"]}'}] for lk in r['links'][:3]]
        if r.get('cards') and not group:
            buttons.append([{'text': '🛠 Saytda tasdiqlash ›', 'url': f'https://{domain}/ai'}])
        sent = send(chat, r['text'], buttons or None, reply_to=reply_to)
        if gemini.enabled() and voice_in:           # spoken to → answers by voice too
            _voice(app, chat, r['text'], (sent or {}).get('result', {}).get('message_id'))


def _voice(app, chat, text, reply_to):
    """The same answer as an Uzbek voice message (Gemini → MP3). Silent on failure: the text is already there."""
    from . import gemini
    from .outbox import telegram_upload
    try:
        _action(chat, 'record_voice')
        audio = gemini.mp3(gemini.tts(text))
        fields = {'chat_id': chat}
        if reply_to:
            fields['reply_to_message_id'] = reply_to
        telegram_upload('sendVoice', fields, {'voice': ('javob.mp3', audio, 'audio/mpeg')})
    except Exception as exc:
        app.logger.warning('telegram assistant voice failed: %s', exc)
