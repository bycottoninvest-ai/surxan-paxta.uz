#!/bin/bash
# SURXON PAXTA — one-command server install (Ubuntu 22.04/24.04, run as root).
#   curl -fsSL https://raw.githubusercontent.com/bycottoninvest-ai/surxan-paxta.uz/main/tools/server_ornatish.sh -o ornatish.sh && bash ornatish.sh
# Safe to run again: existing .env and data/ are kept, code is updated.
set -euo pipefail
REPO=https://github.com/bycottoninvest-ai/surxan-paxta.uz
DIR=/opt/surxan-paxta.uz
DOMAIN=surxan-paxta.uz

[ "$(id -u)" = 0 ] || { echo "root sifatida ishga tushiring (ssh root@IP)"; exit 1; }
say() { echo; echo "==> $*"; }

say "1/6 Kerakli dasturlar (Docker, git, firewall) o‘rnatilmoqda..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ufw ca-certificates dnsutils >/dev/null
if ! docker compose version >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh >/dev/null
fi
systemctl enable --now docker >/dev/null

say "2/6 Dastur kodi: $DIR"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q --ff-only; else git clone -q "$REPO" "$DIR"; fi
cd "$DIR"

say "3/6 Sozlamalar (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$(openssl rand -hex 32)/" .env
  sed -i "s/^TELEGRAM_WEBHOOK_SECRET=.*/TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 24)/" .env
  chmod 600 .env
  echo "   .env yaratildi (maxfiy kalitlar avtomatik)."
else
  echo "   .env bor — o‘zgartirilmadi."
fi
mkdir -p data && chown -R 1000:1000 data

say "4/6 Firewall: faqat 22 (SSH), 80, 443"
ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

IP=$(curl -4 -fsS https://api.ipify.org || hostname -I | awk '{print $1}')
DNS_IP=$(dig +short "$DOMAIN" A | tail -n1 || true)
say "5/6 Server IP: $IP · $DOMAIN → ${DNS_IP:-(hali ulanmagan)}"
if [ "$DNS_IP" = "$IP" ]; then
  sed -i "s/^APP_DOMAIN=.*/APP_DOMAIN=$DOMAIN/" .env
  docker compose up -d --build
  URL="https://$DOMAIN"
else
  echo "   Domen hali shu serverga ulanmagan — parolli vaqtinchalik HTTPS manzil ochiladi."
  echo "   Hozir 2 narsa so‘raladi: vaqtinchalik login va parol (o‘zingiz o‘ylab toping, 12+ belgi)."
  bash tools/vaqtinchalik_https.sh "$IP" </dev/tty
  URL="https://$(echo "$IP" | tr . -).sslip.io"
fi

say "6/6 Tekshiruv"
for i in $(seq 1 30); do
  docker compose exec -T app python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:5000/health')" 2>/dev/null && break
  sleep 2
done
docker compose ps --format 'table {{.Service}}\t{{.Status}}'
docker compose exec -T app flask --app app smoke-check || true

echo
echo "================================================================"
echo "  TAYYOR:  $URL"
if [ -f data/BIRINCHI_ADMIN_PAROLI.txt ]; then
  echo "  Tizim logini: admin   Parol: data/BIRINCHI_ADMIN_PAROLI.txt da."
  echo "  Ko‘rish uchun:  cat $DIR/data/BIRINCHI_ADMIN_PAROLI.txt"
  echo "  (bu parolni rasmga olib hech kimga yubormang)"
fi
echo "  Keyingi ulanishlar (bittadan, sirlar faqat shu serverda kiritiladi):"
echo "    cd $DIR && bash tools/sozlash.sh narx       # 1 500 so‘m/kg, 1 500 000 so‘m/t"
echo "    cd $DIR && bash tools/sozlash.sh telegram"
echo "    cd $DIR && bash tools/sozlash.sh sheets"
echo "    cd $DIR && bash tools/sozlash.sh zaxira"
echo "    cd $DIR && bash tools/sozlash.sh holat      # hammasining holati"
echo "================================================================"
