#!/usr/bin/env bash
# Deploy the VPS side. Run ON THE VPS (as root) from the repo:  bash deploy/deploy_vps.sh
# Web files → nginx web root (console-local.js with the map key is never touched),
# relay + its modules → where the services run, restart the relay, check the services.
set -euo pipefail
SELF="$(realpath "$0")"
cd "$(dirname "$0")/.."
WEB="${AIRSENT_WEB_ROOT:-/var/www/groundcontrol/groundcontrol}"
OPT="/opt/airsent"
RELAY="$(systemctl show -p ExecStart airsent-relay 2>/dev/null | grep -o '/[^ ;]*relay\.py' | head -1)"
RELAY="${RELAY:-/root/relay.py}"

if [ "$(hostname)" = "airsent-desktop" ]; then echo "This is the Jetson — run deploy/deploy_jetson.sh here instead."; exit 1; fi
[ -d "$WEB" ] || { echo "Web root $WEB not found (set AIRSENT_WEB_ROOT)"; exit 1; }
if [ -z "${AIRSENT_DEPLOY_PULLED:-}" ]; then
  git pull --ff-only
  # bash reads scripts as it runs: re-run the freshly pulled copy so new files are included
  AIRSENT_DEPLOY_PULLED=1 exec bash "$SELF" "$@"
fi
BACKUP="$HOME/gcs_backup_$(date +%F_%H%M%S)"; cp -r "$WEB" "$BACKUP"; echo "Backed up the live site to $BACKUP"

cp ./*.html ./*.js ./*.css "$WEB/"
cp ./*.png "$WEB/" 2>/dev/null || true
mkdir -p "$WEB/vendor" && cp -r ./vendor/. "$WEB/vendor/"     # Leaflet etc. served from our own site, no CDN
[ -f "$WEB/console-local.js" ] && echo "✓ console-local.js (map key) kept" || echo "⚠ $WEB/console-local.js is missing — the map will have no key (see console-local.example.js)"

cp event_journal.py presence_auth.py shared_state_server.py ai_review.py "$OPT/"
cp "$RELAY" "$RELAY.bak" 2>/dev/null || true
cp deploy/relay.py "$RELAY"
python3 -m py_compile "$RELAY" "$OPT/event_journal.py" "$OPT/ai_review.py"

# Units, so a rebuilt droplet comes up from the repo rather than from memory. This script
# previously installed none of them — it only restarted the relay, while telling you to
# `systemctl enable --now airsent-shared-state` for a unit that wasn't in the repo at all.
for u in airsent-relay airsent-presence airsent-sequence airsent-shared-state; do
  install -m 644 "deploy/$u.service" "/etc/systemd/system/$u.service"
done
# Relay drop-ins carry its resource limits, auth and logging settings.
if [ -d deploy/airsent-relay.service.d ]; then
  install -d -m 755 /etc/systemd/system/airsent-relay.service.d
  install -m 644 deploy/airsent-relay.service.d/*.conf /etc/systemd/system/airsent-relay.service.d/
fi
# The simulation stack is installed but deliberately left disabled — it burned 199 min of
# CPU on this single core for three log lines in thirty days (SIMULATION.md). Start it by
# hand for a demo; do not enable it here.
for u in airsent-sitl airsent-simulation-backend airsent-simulation-gateway; do
  install -m 644 "deploy/$u.service" "/etc/systemd/system/$u.service"
done
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/airsent-relay.service >/dev/null 2>&1 \
  || echo "⚠ systemd-analyze verify flagged airsent-relay.service"
systemctl enable --quiet airsent-relay airsent-presence airsent-sequence airsent-shared-state
systemctl restart airsent-relay
[ -s /etc/airsent/ai.env ] && echo "✓ AI flight review: API key installed" || echo "ℹ AI flight review not set up yet — run: bash deploy/set_ai_key.sh"
echo "Web → $WEB · relay → $RELAY · modules → $OPT"
sleep 2
for s in airsent-relay airsent-shared-state airsent-presence nginx; do
  printf '%-22s %s\n' "$s" "$(systemctl is-active "$s" 2>/dev/null || true)"
done
systemctl is-active --quiet airsent-shared-state || echo "⚠ airsent-shared-state is enabled but not running — shared settings (mission plan, checklist, drafts) will not sync. Why: journalctl -u airsent-shared-state -n 30"
