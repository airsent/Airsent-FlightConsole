#!/usr/bin/env bash
# Run the safety-path suite inside the pinned ArduCopter SITL image.
#
#   bash tests/sitl/docker_run.sh                      # all scenarios
#   bash tests/sitl/docker_run.sh --only s_geofence    # one, or a comma-separated list
#
# The first run builds the image (ArduCopter from source, 30-45 min). Later runs reuse
# the layer cache and start immediately. Rebuild from scratch with AIRSENT_SITL_REBUILD=1.
#
# Why the whole suite runs in the container rather than just SITL: run.py spawns the
# arducopter binary itself and kills it mid-test to simulate a Pixhawk power cut, so it
# needs process control over SITL. Host-side run.py + containerised SITL cannot work.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO="$PWD"
IMAGE="${AIRSENT_SITL_IMAGE:-airsent-sitl:copter-4.6.3}"
TAG="${AIRSENT_COPTER_TAG:-Copter-4.6.3}"

docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and retry."; exit 1; }

if [ -n "${AIRSENT_SITL_REBUILD:-}" ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building $IMAGE (ArduCopter $TAG from source — first build takes 30-45 min)…"
  docker build ${AIRSENT_SITL_REBUILD:+--no-cache} \
    --build-arg "COPTER_TAG=$TAG" -t "$IMAGE" -f tests/sitl/Dockerfile tests/sitl
fi

# The repo is mounted rather than copied so last_report.json lands back in the working
# tree. Scenarios write missions and logs under --work, kept inside the container.
exec docker run --rm -i \
  -v "$REPO:/repo" \
  -w /repo \
  -e PYTHONUNBUFFERED=1 \
  "$IMAGE" \
  python3 -u tests/sitl/run.py --ardupilot /ardupilot --work /tmp/airsent_sitl "$@"
