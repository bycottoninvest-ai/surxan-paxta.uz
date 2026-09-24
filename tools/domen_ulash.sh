#!/bin/bash
# Switch from the temporary address to https://surxan-paxta.uz — keeps ./data untouched.
set -euo pipefail
cd "$(dirname "$0")/.."
DOMAIN=${1:-surxan-paxta.uz}
MYIP=$(curl -s https://api.ipify.org)
DNSIP=$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)
echo "Server IP: $MYIP · $DOMAIN DNS: ${DNSIP:-topilmadi}"
[ "$DNSIP" = "$MYIP" ] || { echo "DNS hali bu serverga ko'rsatmayapti (A yozuvi $DOMAIN -> $MYIP). Keyinroq qayta urining."; exit 1; }
docker compose exec -T app flask --app app backup            # safety copy before switching
sed -i "s/^APP_DOMAIN=.*/APP_DOMAIN=$DOMAIN/" .env
docker compose -f docker-compose.yml up -d --build --remove-orphans   # main Caddyfile (domain, automatic HTTPS)
sleep 10
curl -fsS "https://$DOMAIN/health" && echo
if grep -q '^TELEGRAM_BOT_TOKEN=.\+' .env; then docker compose exec -T app flask --app app set-webhook; fi
docker compose exec -T app flask --app app smoke-check || true
echo "Tayyor: https://$DOMAIN  (vaqtinchalik Caddyfile.vaqtinchalik endi ishlatilmaydi; xohlasangiz o'chiring)"
