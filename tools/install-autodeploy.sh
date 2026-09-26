#!/usr/bin/env bash
# One-time: install the SURXON auto-update timer (every 5 minutes). Run as root in /opt/surxan-paxta.uz:
#   bash tools/install-autodeploy.sh
set -e
DIR=$(cd "$(dirname "$0")/.." && pwd)
chmod +x "$DIR/tools/autodeploy.sh"
cat > /etc/systemd/system/surxon-autodeploy.service <<UNIT
[Unit]
Description=SURXON auto-update from GitHub (production branch)
After=docker.service network-online.target
[Service]
Type=oneshot
Environment=SURXON_DIR=$DIR
ExecStart=$DIR/tools/autodeploy.sh
UNIT
cat > /etc/systemd/system/surxon-autodeploy.timer <<UNIT
[Unit]
Description=SURXON auto-update every 5 minutes
[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
Persistent=true
[Install]
WantedBy=timers.target
UNIT
# the “Hozir yangilash” button in Admin → Zaxira drops data/deploy_request — this starts the update at once
cat > /etc/systemd/system/surxon-autodeploy.path <<UNIT
[Unit]
Description=SURXON update requested from the admin page
[Path]
PathExists=$DIR/data/deploy_request
Unit=surxon-autodeploy.service
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now surxon-autodeploy.timer
systemctl enable --now surxon-autodeploy.path
echo "OK: avtomatik yangilash yoqildi (har 5 daqiqada GitHub production tekshiriladi)."
systemctl list-timers surxon-autodeploy.timer --no-pager | head -3
