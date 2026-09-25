#!/bin/bash
# SURXAN-PAXTA.UZ — finish an ALREADY INSTALLED server (does not reinstall, keeps .env, data/ and local overrides).
#   cd /opt/surxan-paxta.uz && git pull --ff-only && bash tools/yakunlash.sh
# Steps: consistent backup → update code (rebuild) → switch to surxan-paxta.uz if DNS is ready (rolls back on failure)
#        → optional Telegram token → status. Safe to run again.
set -euo pipefail
cd "$(dirname "$0")/.."
DOMAIN=surxan-paxta.uz
say() { echo; echo "==> $*"; }
fl() { docker compose exec -T app flask --app app "$@"; }
setenv() {
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
wait_health() {
  for _ in $(seq 1 40); do
    docker compose exec -T app python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:5000/health')" 2>/dev/null && return 0
    sleep 3
  done
  return 1
}

[ -f .env ] && [ -d data ] || { echo ".env yoki data/ yo‘q — bu skript faqat o‘rnatilgan serverda ishlaydi."; exit 1; }

say "1/5 Hozirgi holat"
echo "   Kod: $(git log --oneline -1)"
[ -f docker-compose.override.yml ] && echo "   Mahalliy override bor (saqlanadi)." || echo "   Mahalliy override yo‘q."
docker compose ps --format '   {{.Service}}: {{.Status}}' || true

say "2/5 Yangilashdan oldin zaxira (baza + rasmlar)"
fl backup
ls -1t data/backups 2>/dev/null | head -2 | sed 's/^/   /'

say "3/5 Kodni yangilash (baza saqlanadi, yangi jadvallar o‘zi qo‘shiladi)"
git pull -q --ff-only || { echo "   git pull o‘tmadi (serverda kod qo‘lda o‘zgartirilganmi?). To‘xtadim — hech narsa buzilmadi."; exit 1; }
echo "   Yangi kod: $(git log --oneline -1)"
docker compose up -d --build --remove-orphans
wait_health || { echo "   ❌ Ilova ishga tushmadi: docker compose logs app | tail -40 natijasini yuboring."; exit 1; }
echo "   ✅ Ilova ishlayapti: $(docker compose exec -T app python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:5000/health').read().decode())")"

say "4/5 Asosiy domen: $DOMAIN"
IP=$(curl -4 -fsS https://api.ipify.org || hostname -I | awk '{print $1}')
DNS_IP=$(dig +short @1.1.1.1 "$DOMAIN" A | grep -E '^[0-9.]+$' | tail -n1 || true)
WWW_IP=$(dig +short @1.1.1.1 "www.$DOMAIN" A | grep -E '^[0-9.]+$' | tail -n1 || true)
echo "   Server $IP · $DOMAIN → ${DNS_IP:-yo‘q} · www → ${WWW_IP:-yo‘q}"
CUR=$(grep '^APP_DOMAIN=' .env | cut -d= -f2- || true)
if [ "$CUR" = "$DOMAIN" ] && curl -fsS -o /dev/null "https://$DOMAIN/health" 2>/dev/null; then
  echo "   ✅ Allaqachon https://$DOMAIN da ishlayapti."
elif [ "$DNS_IP" != "$IP" ]; then
  echo "   ⏳ DNS hali bu serverga ko‘rsatmayapti — vaqtinchalik manzil ishlashda davom etadi. Keyinroq shu skriptni qayta ishga tushiring."
else
  STAMP=$(date +%Y%m%d-%H%M%S)
  cp .env ".env.oldin-domen-$STAMP"; chmod 600 ".env.oldin-domen-$STAMP"
  MOVED=""
  if [ -f docker-compose.override.yml ] && grep -q 'Caddyfile.vaqtinchalik' docker-compose.override.yml; then
    # the override points Caddy at the temporary config; its backup/worker fixes are now in docker-compose.yml
    mv docker-compose.override.yml "docker-compose.override.yml.oldin-domen-$STAMP"; MOVED=1
    echo "   Vaqtinchalik Caddy override chetga olindi (nusxa: docker-compose.override.yml.oldin-domen-$STAMP)."
  fi
  SITES="$DOMAIN"; [ "$WWW_IP" = "$IP" ] && SITES="$DOMAIN, www.$DOMAIN"
  setenv APP_DOMAIN "$DOMAIN"; setenv SITE_ADDRESSES "$SITES"; setenv COOKIE_SECURE 1
  docker compose up -d --build --remove-orphans
  OK=""
  for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "https://$DOMAIN/health" 2>/dev/null; then OK=1; break; fi
    sleep 5
  done
  if [ -n "$OK" ]; then
    echo "   ✅ https://$DOMAIN ishlayapti, sertifikat olindi. Vaqtinchalik sslip manzili endi yopiq."
  else
    echo "   ❌ Domen HTTPS 2,5 daqiqada ochilmadi — avvalgi (vaqtinchalik) holatga qaytarilmoqda..."
    docker compose logs caddy --tail 15 | sed 's/^/     /'
    cp ".env.oldin-domen-$STAMP" .env
    [ -n "$MOVED" ] && mv "docker-compose.override.yml.oldin-domen-$STAMP" docker-compose.override.yml
    docker compose up -d --build --remove-orphans
    echo "   Qaytarildi. Yuqoridagi caddy xatosini rasmga olib yuboring."
  fi
fi

say "5/5 Telegram"
if grep -q '^TELEGRAM_BOT_TOKEN=.\+' .env; then
  echo "   Token bor — webhook yangilanmoqda."
  fl set-webhook || true
else
  read -rp "   Telegram bot tokenini hozir kiritasizmi? (h/y) [h]: " A
  if [ "${A:-h}" = "h" ]; then bash tools/sozlash.sh telegram; else echo "   Keyinroq: bash tools/sozlash.sh telegram"; fi
fi

echo
echo "================ HOLAT ================"
fl holat || true
echo "Manzil: https://$(grep '^APP_DOMAIN=' .env | cut -d= -f2-)"
echo "Xodimlar: docker compose exec -T app flask --app app xodimlar \"Asadbek:hisobchi\" \"Yunus:punkt\" ..."
echo "======================================="
