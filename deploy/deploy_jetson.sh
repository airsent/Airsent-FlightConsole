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
       pixhawk_log.py video_record.py log_analysis.py ai_review.py"

if [ "$(hostname)" = "Console-sfo3" ]; then echo "This is the VPS — run deploy/deploy_vps.sh here instead."; exit 1; fi
if [ -z "${AIRSENT_DEPLOY_PULLED:-}" ]; then
  git pull --ff-only
  # bash reads scripts as it runs: re-run the freshly pulled copy so new files are included
  AIRSENT_DEPLOY_PULLED=1 exec bash "$SELF" "$@"
fi
for f in $FILES; do cp "$f" "$TARGET/"; done
echo "Copied to $TARGET: $(echo $FILES | tr '\n' ' ')"
python3 -m py_compile "$TARGET/airsent_server.py" "$TARGET/asc.py" "$TARGET/pixhawk_log.py" "$TARGET/video_record.py" "$TARGET/log_analysis.py"
python3 -c "import numpy" 2>/dev/null || echo "⚠ numpy missing — the log analyser cannot compute the vibration spectrum. Fix: pip3 install numpy"
command -v ffmpeg >/dev/null || echo "⚠ ffmpeg is not installed — videos stay as raw .h265/.mjpeg. Fix: sudo apt install -y ffmpeg"
# Link manager (root service): WiFi first, cellular fallback for everything going to the VPS
sudo install -m 755 netlink.py /usr/local/bin/airsent-netlink
# ModemManager can't read the RM520N's signal in ECM mode (and shows it as detached while data
# flows); the modem dials by itself, so the link manager talks to it directly instead.
# Undo with: sudo systemctl enable --now ModemManager
if systemctl is-active --quiet ModemManager; then
  sudo systemctl disable --now ModemManager >/dev/null 2>&1 && echo "Stopped ModemManager (the link manager reads the modem directly)"
fi
sudo install -m 644 deploy/airsent-netlink.service /etc/systemd/system/airsent-netlink.service
# Every unit this machine runs, so a rebuilt Jetson comes up from the repo rather than
# from memory. These three used to exist only on the SD card; airsent-server.service in
# particular is how the flight server starts, and nothing in git described it.
sudo install -m 644 deploy/airsent-server.service /etc/systemd/system/airsent-server.service
# airsent-route.service runs this script; it was on the SD card but never installed by
# this script, so a rebuilt Jetson would have had the unit enabled and the script missing.
sudo install -m 755 deploy/airsent-route.sh /usr/local/bin/airsent-route.sh
sudo install -m 644 deploy/airsent-route.service  /etc/systemd/system/airsent-route.service
# The tunnel needs its key and known_hosts, which deploy/install_reverse_tunnel.py sets up.
# Install the unit either way, but only enable it once the key exists, or boot logs fill
# with autossh failures on a machine that was never given one.
sudo install -m 644 deploy/airsent-tunnel.service /etc/systemd/system/airsent-tunnel.service
# Drop-ins carry the Pixhawk baud and which RC channel is the kill switch. These were
# on the SD card only; a rebuilt Jetson would have come up with no kill switch configured.
if [ -d deploy/airsent-server.service.d ]; then
  sudo install -d -m 755 /etc/systemd/system/airsent-server.service.d
  sudo install -m 644 deploy/airsent-server.service.d/*.conf /etc/systemd/system/airsent-server.service.d/
fi
sudo systemctl daemon-reload
sudo systemctl enable --quiet airsent-netlink airsent-server airsent-route
if sudo test -f /etc/airsent/tunnel_key; then
  sudo systemctl enable --quiet airsent-tunnel
else
  echo "ℹ airsent-tunnel installed but not enabled — no /etc/airsent/tunnel_key yet. Set it up: sudo python3 deploy/install_reverse_tunnel.py"
fi
sudo systemctl restart airsent-netlink
sleep 3
systemctl is-active --quiet airsent-netlink && echo "✓ airsent-netlink running: $(python3 -c 'import json;d=json.load(open("/run/airsent-netlink/status.json"));print("active link =", d.get("active"), "| wifi", d["wifi"].get("iface"), "| cellular", d["cellular"].get("iface") or "none")' 2>/dev/null)" || echo "⚠ airsent-netlink is not running — journalctl -u airsent-netlink"
sudo systemctl restart airsent-server
echo "Restarted airsent-server — waiting 12 s for the Pixhawk link…"
sleep 12
journalctl -u airsent-server -n 60 --no-pager | grep -E "\[NET\]|H.265 recorder|Pixhawk connected|Streams @|Command relay connected|Telemetry relay connected|Traceback|Error|error" | tail -12
systemctl is-active --quiet airsent-server && echo "✓ airsent-server is running" || { echo "✗ airsent-server is NOT running"; exit 1; }
