#!/bin/bash
# SURXAN-PAXTA.UZ — secrets and connections, one at a time, typed ON THE SERVER (never in chat, never in git).
#   bash tools/sozlash.sh telegram | sheets | zaxira | narx | holat
# Hidden input (read -s): tokens and passwords are not shown and not kept in shell history.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo ".env yo‘q — avval tools/server_ornatish.sh"; exit 1; }
chmod 600 .env

setenv() {  # setenv KEY VALUE — replaces or appends one line in .env (values with any characters are safe)
  python3 - "$1" "$2" <<'PY'
import sys, re
k, v = sys.argv[1], sys.argv[2]
lines = open('.env', encoding='utf-8').read().splitlines()
out, done = [], False
for l in lines:
    if re.match(rf'^{re.escape(k)}=', l):
        out.append(f'{k}={v}'); done = True
    else:
        out.append(l)
if not done:
    out.append(f'{k}={v}')
open('.env', 'w', encoding='utf-8').write('\n'.join(out) + '\n')
PY
}
restart() { docker compose up -d >/dev/null; sleep 6; }
flask() { docker compose exec -T app flask --app app "$@"; }

case "${1:-}" in
telegram)
  echo "1) @BotFather dagi bot tokeni (ko‘rinmaydi):"; read -rsp "   TOKEN: " T; echo
  [[ "$T" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] || { echo "Token ko‘rinishi noto‘g‘ri"; exit 1; }
  read -rp "2) Bot username (@ siz): " U
  read -rp "3) Arxiv kanali ID (-100… , hozircha bo‘sh qoldirsa ham bo‘ladi): " A
  read -rp "4) Hisobot kanali ID (-100… , hozircha bo‘sh qoldirsa ham bo‘ladi): " R
  read -rsp "5) Hisobot uchun alohida bot tokeni (bo‘lmasa Enter): " RT; echo
  setenv TELEGRAM_BOT_TOKEN "$T"; setenv TELEGRAM_BOT_USERNAME "${U#@}"
  [ -n "$A" ] && setenv TELEGRAM_ARCHIVE_CHAT_ID "$A"
  [ -n "$R" ] && setenv TELEGRAM_REPORT_CHAT_ID "$R"
  [ -n "$RT" ] && setenv TELEGRAM_REPORT_BOT_TOKEN "$RT"
  grep -q '^TELEGRAM_WEBHOOK_SECRET=.\+' .env || setenv TELEGRAM_WEBHOOK_SECRET "$(openssl rand -hex 24)"
  restart
  flask set-webhook
  flask smoke-check --send-tests | grep -i 'telegram' || true
  ;;
sheets)
  read -rp "1) Jadval ID [1eWl21webrxSAV_NDdvv8dX1lWmu5gSEMSD1fvMWh8Mw]: " S
  S=${S:-1eWl21webrxSAV_NDdvv8dX1lWmu5gSEMSD1fvMWh8Mw}
  echo "2) Xizmat akkaunti JSON faylini Bloknotda oching, BUTUN matnini nusxalab shu yerga qo‘ying,"
  echo "   keyin yangi qatorga o‘tib Ctrl+D bosing:"
  cat > data/google-service-account.json
  python3 -c "import json;d=json.load(open('data/google-service-account.json'));print('   xizmat akkaunti:', d['client_email'])" \
    || { echo "JSON noto‘g‘ri"; rm -f data/google-service-account.json; exit 1; }
  chown 1000:1000 data/google-service-account.json; chmod 600 data/google-service-account.json
  echo "   ⚠ Shu email’ni jadvalda “Share” → Editor qilib qo‘shing (agar hali qo‘shilmagan bo‘lsa)."
  setenv GOOGLE_SHEETS_ID "$S"; setenv GOOGLE_SERVICE_ACCOUNT_FILE /data/google-service-account.json
  restart
  echo; echo "== Jadval tuzilmasi (faqat o‘qish, hech narsa yozilmaydi) =="
  flask sheets-inspect
  ;;
zaxira)
  echo "Hetzner Storage Box (yoki boshqa SFTP) — serverdan TASHQARIDAGI nusxa."
  read -rp "1) Host (masalan u123456.your-storagebox.de): " H
  read -rp "2) Foydalanuvchi (masalan u123456): " SU
  read -rsp "3) Parol (ko‘rinmaydi): " SP; echo
  OBS=$(docker compose run --rm -T backup rclone obscure "$SP")
  cat > data/rclone.conf <<CONF
[storagebox]
type = sftp
host = $H
user = $SU
port = 23
pass = $OBS
shell_type = unix
md5sum_command = none
sha1sum_command = none
CONF
  chown 1000:1000 data/rclone.conf; chmod 600 data/rclone.conf
  setenv RCLONE_CONFIG /data/rclone.conf; setenv OFFSITE_RCLONE_REMOTE storagebox:surxon-zaxira
  restart
  echo "== Zaxira → tashqi joy → yuklab olib ALOHIDA bazaga tiklash =="
  flask backup-verify --offsite
  ;;
narx)
  read -rp "Qo‘l terimi narxi, so‘m/kg [1500]: " Q; Q=${Q:-1500}
  read -rp "Kombayn narxi, so‘m/tonna [1500000]: " K; K=${K:-1500000}
  flask narx --qol "$Q" --kombayn-tonna "$K"
  ;;
sheets-sinov)
  # bash tools/sozlash.sh sheets-sinov "Umumiy hisob!B4"   — dashboard katagini ham kuzatish (ixtiyoriy)
  shift; args=(); for k in "$@"; do args+=(--katak "$k"); done
  flask sheets-sinov "${args[@]}"
  ;;
holat)
  flask holat
  ;;
*)
  echo "Foydalanish: bash tools/sozlash.sh telegram | sheets | sheets-sinov | zaxira | narx | holat"; exit 1;;
esac
