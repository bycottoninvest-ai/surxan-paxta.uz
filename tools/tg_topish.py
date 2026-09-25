"""Check the bot token and find the bot's username (run by `sozlash.sh telegram`, on the server).

The token comes from the TG_TOKEN environment variable (typed hidden), never from the command line.
Prints KEY=VALUE lines for the shell script; explanations go to stderr.
"""
import json
import os
import sys
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
    # Channels are NOT searched here (no getUpdates: it would also pull people's private messages to the bot).
    # Make the bot admin in both channels, write one message in each, then pick them on the site:
    # Admin → Integratsiyalar → "Telegram kanallari".


if __name__ == '__main__':
    main()
