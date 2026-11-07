#!/usr/bin/env bash
# Deploy the Jetson side. Run ON THE JETSON from the repo:  bash deploy/deploy_jetson.sh
# Copies the server and its modules to the folder the airsent-server service runs from,
# restarts the service and shows whether it came back up cleanly.
set -euo pipefail
SELF="$(realpath "$0")"
cd "$(dirname "$0")/.."
TARGET="${AIRSENT_SERVER_DIR:-$HOME}"
FILES="airsent_server.py asc.py mission_engine.py golden_params.py golden_params.json param_store.py
       vehicle_mission.py health_telemetry.py pilot_settings.py datalink_telemetry.py
       pixhawk_log.py video_record.py"

if [ "$(hostname)" = "Console-sfo3" ]; then echo "This is the VPS — run deploy/deploy_vps.sh here instead."; exit 1; fi
if [ -z "${AIRSENT_DEPLOY_PULLED:-}" ]; then
  git pull --ff-only
  # bash reads scripts as it runs: re-run the freshly pulled copy so new files are included
  AIRSENT_DEPLOY_PULLED=1 exec bash "$SELF" "$@"
fi
for f in $FILES; do cp "$f" "$TARGET/"; done
echo "Copied to $TARGET: $(echo $FILES | tr '\n' ' ')"
python3 -m py_compile "$TARGET/airsent_server.py" "$TARGET/asc.py" "$TARGET/pixhawk_log.py" "$TARGET/video_record.py"
command -v ffmpeg >/dev/null || echo "⚠ ffmpeg is not installed — videos stay as raw .h265/.mjpeg. Fix: sudo apt install -y ffmpeg"
sudo systemctl restart airsent-server
echo "Restarted airsent-server — waiting 12 s for the Pixhawk link…"
sleep 12
journalctl -u airsent-server -n 60 --no-pager | grep -E "H.265 recorder|Pixhawk connected|Streams @|Command relay connected|Telemetry relay connected|Traceback|Error|error" | tail -12
systemctl is-active --quiet airsent-server && echo "✓ airsent-server is running" || { echo "✗ airsent-server is NOT running"; exit 1; }
