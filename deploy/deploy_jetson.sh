#!/usr/bin/env bash
# Deploy the Jetson side. Run ON THE JETSON from the repo:  bash deploy/deploy_jetson.sh
# Copies the server and its modules to the folder the airsent-server service runs from,
# restarts the service and shows whether it came back up cleanly.
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET="${AIRSENT_SERVER_DIR:-$HOME}"
FILES="airsent_server.py asc.py mission_engine.py golden_params.py golden_params.json param_store.py
       vehicle_mission.py health_telemetry.py pilot_settings.py datalink_telemetry.py"

if [ "$(hostname)" = "Console-sfo3" ]; then echo "This is the VPS — run deploy/deploy_vps.sh here instead."; exit 1; fi
git pull --ff-only
for f in $FILES; do cp "$f" "$TARGET/"; done
echo "Copied to $TARGET: $(echo $FILES | tr '\n' ' ')"
python3 -m py_compile "$TARGET/airsent_server.py" "$TARGET/asc.py"
sudo systemctl restart airsent-server
echo "Restarted airsent-server — waiting 12 s for the Pixhawk link…"
sleep 12
journalctl -u airsent-server -n 60 --no-pager | grep -E "Pixhawk connected|Streams @|Command relay connected|Telemetry relay connected|Traceback|Error|error" | tail -12
systemctl is-active --quiet airsent-server && echo "✓ airsent-server is running" || { echo "✗ airsent-server is NOT running"; exit 1; }
