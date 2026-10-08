#!/usr/bin/env bash
# SURXON auto-update (runs on the server every 5 minutes from a systemd timer; installed by tools/install-autodeploy.sh).
# Follows the GitHub branch "production" — it only moves after the owner approved a release. On a new commit:
#   consistent backup → fast-forward pull → rebuild → health check → Telegram webhook refresh.
# If the new version does not become healthy, the previous commit is rebuilt (automatic rollback).
# The result goes to data/deploy_status.json (shown in Admin → Zaxira) and data/deploy.log.
set -u
DIR=${SURXON_DIR:-/opt/surxan-paxta.uz}
BRANCH=${DEPLOY_BRANCH:-production}
cd "$DIR" || exit 1
LOG=data/deploy.log
exec 9>/tmp/surxon-autodeploy.lock
flock -n 9 || exit 0

MANUAL=""; [ -f data/deploy_request ] && MANUAL=1
rm -f data/deploy_request          # a button press is being handled now
say() { echo "$(date '+%F %T') $*" >> "$LOG"; }
status() {  # ok|fail, message
  printf '{"at":"%s","ok":%s,"from":"%s","to":"%s","message":"%s"}\n' "$(date '+%F %T')" \
    "$([ "$1" = ok ] && echo true || echo false)" "${OLD:-}" "${NEW:-}" "$2" > data/deploy_status.json
  say "$1: $2"
}
healthy() {
  for _ in $(seq 1 40); do
    if docker compose exec -T app python -c "import urllib.request,sys;sys.exit(0 if b'\"ok\"' in urllib.request.urlopen('http://127.0.0.1:5000/health',timeout=5).read() else 1)" >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  return 1
}

running_ver() { docker compose exec -T app python -c "import surxon;print(surxon.VERSION)" 2>/dev/null | tr -d '\r'; }
code_ver() { sed -n "s/^VERSION = '\(.*\)'.*/\1/p" surxon/__init__.py | head -1; }
build() {  # rebuild + start; on a build error write its last lines to the status
  if ! docker compose up -d --build >> "$LOG" 2>&1; then
    ERR=$(tail -n 3 "$LOG" | tr -d '"\\' | tr '\n' ' ' | cut -c1-220)
    return 1
  fi
  return 0
}

git fetch -q origin "$BRANCH" 2>>"$LOG" || { status fail "GitHub bilan aloqa yo'q (git fetch)"; exit 1; }
OLD=$(git rev-parse HEAD)
NEW=$(git rev-parse "origin/$BRANCH")
if [ "$OLD" = "$NEW" ]; then
  RUN=$(running_ver); CODE=$(code_ver)
  if [ -n "$RUN" ] && [ -n "$CODE" ] && [ "$RUN" != "$CODE" ]; then
    # the code was pulled but the app was never rebuilt (an earlier build failed): build it now
    say "kod v$CODE, ishlayotgani v$RUN — qayta qurilmoqda"
    if build && healthy && [ "$(running_ver)" = "$CODE" ]; then
      docker compose exec -T app flask --app app set-webhook >> "$LOG" 2>&1 || true
      status ok "Yangilandi: v$CODE (qayta qurildi)"
    else
      status fail "v$CODE qurilmadi, v$RUN ishlab turibdi. ${ERR:-sog'liq tekshiruvi o'tmadi}"
    fi
    exit 0
  fi
  [ -n "${MANUAL:-}" ] && status ok "Tekshirildi: yangi versiya yo'q, server eng oxirgi versiyada (v${RUN:-?})"
  exit 0
fi
if [ -z "$MANUAL" ] && [ "$(cat data/deploy_failed 2>/dev/null)" = "$NEW" ]; then
  exit 0                           # this version already failed once — wait for a newer one or the admin's button
fi
if ! git merge-base --is-ancestor "$OLD" "$NEW"; then
  status fail "Yangi versiya hozirgisining davomi emas — qo'lda tekshirish kerak"; exit 1
fi
say "yangi versiya: $OLD -> $NEW"
docker compose exec -T app flask --app app backup >> "$LOG" 2>&1 || { status fail "Zaxira olinmadi — yangilash to'xtatildi"; exit 1; }
if ! git merge -q --ff-only "$NEW" >> "$LOG" 2>&1; then
  status fail "Kod olinmadi (serverda qo'lda o'zgartirilgan fayl bor)"; exit 1
fi
BUILT=1; build || BUILT=""
if [ -n "$BUILT" ] && healthy && [ "$(running_ver)" = "$(code_ver)" ]; then
  docker compose exec -T app flask --app app set-webhook >> "$LOG" 2>&1 || true
  VER=$(docker compose exec -T app python -c "import surxon;print(surxon.VERSION)" 2>/dev/null | tr -d '\r')
  rm -f data/deploy_failed
  status ok "Yangilandi: v${VER}"
else
  say "sog'liq tekshiruvi o'tmadi — eski versiyaga qaytarilmoqda"
  git reset -q --hard "$OLD" >> "$LOG" 2>&1
  docker compose up -d --build >> "$LOG" 2>&1
  echo "$NEW" > data/deploy_failed
  status fail "Yangi versiya ishlamadi — avtomatik eski versiyaga qaytarildi. ${ERR:-}"
fi
