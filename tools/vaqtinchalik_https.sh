#!/bin/bash
# Password-protected temporary HTTPS address on the server's IP (before the domain is active).
# Uses <ip-with-dashes>.sslip.io, which resolves to the server IP, so Caddy can get a real Let's Encrypt certificate.
# The same ./data folder is used — nothing is copied or deleted.
set -euo pipefail
cd "$(dirname "$0")/.."
IP=${1:-$(curl -s https://api.ipify.org)}
HOST="$(echo "$IP" | tr . -).sslip.io"
read -rp "Vaqtinchalik kirish uchun login [surxon]: " U; U=${U:-surxon}
read -rsp "Vaqtinchalik parol (kamida 12 belgi): " P; echo
[ ${#P} -ge 12 ] || { echo "Parol juda qisqa"; exit 1; }
HASH=$(docker run --rm caddy:2 caddy hash-password --plaintext "$P")
cat > Caddyfile.vaqtinchalik <<CADDY
$HOST {
    encode zstd gzip
    # Everything is behind a password EXCEPT endpoints that have their own secret:
    # Telegram webhook (secret path + header), ERP API (Bearer key), health check.
    @protected not path /telegram/webhook/* /api/erp/* /health
    basic_auth @protected {
        $U $HASH
    }
    header -Server
    reverse_proxy app:5000
}
CADDY
chmod 600 Caddyfile.vaqtinchalik
# the app must know its public address for the Telegram webhook URL
if grep -q '^APP_DOMAIN=' .env; then sed -i "s/^APP_DOMAIN=.*/APP_DOMAIN=$HOST/" .env; else echo "APP_DOMAIN=$HOST" >> .env; fi
docker compose -f docker-compose.yml -f docker-compose.vaqtinchalik.yml up -d --build
sleep 8
curl -fsS "https://$HOST/health" && echo
if grep -q '^TELEGRAM_BOT_TOKEN=.\+' .env; then docker compose exec -T app flask --app app set-webhook; fi
docker compose exec -T app flask --app app smoke-check || true
echo
echo "Vaqtinchalik manzil: https://$HOST   (login: $U + siz kiritgan parol, keyin tizim logini)"
