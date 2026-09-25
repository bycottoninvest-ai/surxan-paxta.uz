"""Find the bot's username and the two channel IDs automatically (run by `sozlash.sh telegram`, on the server).

The token comes from the TG_TOKEN environment variable (typed hidden), never from the command line.
Channels are recognised by name: one containing "arxiv", one containing "hisobot". The bot must be an admin there.
Prints KEY=VALUE lines for the shell script; explanations go to stderr.
"""
import json
import os
import sys
import time
import urllib.request

TOKEN = os.environ.get('TG_TOKEN', '').strip()


def api(method, **payload):
    req = urllib.request.Request(f'https://api.telegram.org/bot{TOKEN}/{method}', data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode())


def say(msg):
    print(msg, file=sys.stderr, flush=True)


def main():
    try:
        me = api('getMe')['result']
    except Exception as exc:
        say(f'❌ Token ishlamadi ({exc}). BotFather bergan tokenni qaytadan nusxalang.')
        sys.exit(1)
    say(f'✅ Bot topildi: @{me["username"]}')
    print(f'TELEGRAM_BOT_USERNAME={me["username"]}')
    api('deleteWebhook', drop_pending_updates=False)   # getUpdates works only without a webhook; set again afterwards
    found, offset, asked = {}, None, False
    deadline = time.time() + 300
    while time.time() < deadline:
        res = api('getUpdates', timeout=20, offset=offset,
                  allowed_updates=['channel_post', 'my_chat_member', 'message'])
        for u in res.get('result', []):
            offset = u['update_id'] + 1
            for key in ('channel_post', 'my_chat_member', 'message'):
                chat = (u.get(key) or {}).get('chat') or {}
                if chat.get('type') != 'channel':
                    continue
                title = (chat.get('title') or '').lower()
                for word, env in (('arxiv', 'TELEGRAM_ARCHIVE_CHAT_ID'), ('hisobot', 'TELEGRAM_REPORT_CHAT_ID')):
                    if word in title and env not in found:
                        found[env] = str(chat['id'])
                        say(f'✅ Kanal topildi: {chat.get("title")} → {chat["id"]}')
        if len(found) == 2:
            break
        if not asked:
            missing = [w for w, e in (('arxiv', 'TELEGRAM_ARCHIVE_CHAT_ID'), ('hisobot', 'TELEGRAM_REPORT_CHAT_ID'))
                       if e not in found]
            say('')
            say(f'👉 Hozir Telegramda {" va ".join("SURXAN-PAXTA " + m for m in missing)} kanaliga '
                'istalgan so‘z yozib yuboring (masalan: salom). Kutyapman (5 daqiqagacha)...')
            asked = True
    for env, val in found.items():
        print(f'{env}={val}')
    if len(found) < 2:
        say('⚠️ Ikkala kanal topilmadi. Bot kanalda ADMIN ekanini va kanal nomida “arxiv” / “hisobot” so‘zi borligini '
            'tekshiring, keyin buyruqni qayta ishga tushiring.')
        sys.exit(2)
    # send a first message so the owner sees it works
    for env, text in (('TELEGRAM_ARCHIVE_CHAT_ID', '✅ SURXAN-PAXTA arxiv kanali ulandi.'),
                      ('TELEGRAM_REPORT_CHAT_ID', '✅ SURXAN-PAXTA hisobot kanali ulandi. Bu yerga faqat tizim yozadi.')):
        try:
            api('sendMessage', chat_id=found[env], text=text)
        except Exception as exc:
            say(f'⚠️ {env}: xabar yuborilmadi ({exc}) — bot admin emasmi?')


if __name__ == '__main__':
    main()
