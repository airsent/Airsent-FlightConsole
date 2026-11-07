#!/usr/bin/env bash
# Deploy the VPS side. Run ON THE VPS (as root) from the repo:  bash deploy/deploy_vps.sh
# Web files → nginx web root (console-local.js with the map key is never touched),
# relay + its modules → where the services run, restart the relay, check the services.
set -euo pipefail
cd "$(dirname "$0")/.."
WEB="${AIRSENT_WEB_ROOT:-/var/www/groundcontrol/groundcontrol}"
OPT="/opt/airsent"
RELAY="$(systemctl show -p ExecStart airsent-relay 2>/dev/null | grep -o '/[^ ;]*relay\.py' | head -1)"
RELAY="${RELAY:-/root/relay.py}"

if [ "$(hostname)" = "airsent-desktop" ]; then echo "This is the Jetson — run deploy/deploy_jetson.sh here instead."; exit 1; fi
[ -d "$WEB" ] || { echo "Web root $WEB not found (set AIRSENT_WEB_ROOT)"; exit 1; }
git pull --ff-only
BACKUP="$HOME/gcs_backup_$(date +%F_%H%M%S)"; cp -r "$WEB" "$BACKUP"; echo "Backed up the live site to $BACKUP"

cp ./*.html ./*.js ./*.css "$WEB/"
cp ./*.png "$WEB/" 2>/dev/null || true
[ -f "$WEB/console-local.js" ] && echo "✓ console-local.js (map key) kept" || echo "⚠ $WEB/console-local.js is missing — the map will have no key (see console-local.example.js)"

cp event_journal.py presence_auth.py shared_state_server.py "$OPT/"
cp "$RELAY" "$RELAY.bak" 2>/dev/null || true
cp deploy/relay.py "$RELAY"
python3 -m py_compile "$RELAY" "$OPT/event_journal.py"
systemctl restart airsent-relay
echo "Web → $WEB · relay → $RELAY · modules → $OPT"
sleep 2
for s in airsent-relay airsent-shared-state airsent-presence nginx; do
  printf '%-22s %s\n' "$s" "$(systemctl is-active "$s" 2>/dev/null || true)"
done
systemctl is-active --quiet airsent-shared-state || echo "⚠ airsent-shared-state is not running — shared settings (mission plan, checklist, drafts) will not sync. Start it: systemctl enable --now airsent-shared-state"
