# Airsent FlightConsole

The real AirSent ground control station: a plain HTML/JS console plus the Python server that runs on the Jetson companion computer beside a Pixhawk 6 running **ArduCopter**. Commands and telemetry are ArduCopter MAVLink (GUIDED, `NAV_TAKEOFF`, ArduPilot params) over `pymavlink`.

## Shape of the repo

- **Backend (Python, Jetson-side)** — `airsent_server.py` is the entry point; subsystems are flat modules beside it: `mission_engine.py`, `vehicle_mission.py`, `failsafe` logic, `health_telemetry.py`, `datalink_telemetry.py`, `param_store.py`, `golden_params.py`, `video_record.py`, `pixhawk_log.py`, `asc.py`. Separate relay/presence/state servers: `telemetry_server.py`, `presence_server.py`, `shared_state_server.py`, `launch_sequence_server.py`, `netlink.py`.
- **Frontend** — one `.html` per console page (`dashboard`, `pilot`, `mission`, `failsafe`, `health`, `launch`, `preflight`, `parameters`, `logs`, `actuators`, `sensor-status`, `signin`) with matching `.js`/`.css`. Shared layers: `shared-state.js`, `shared-events.js`, `telemetry.js`, `console-config.js`.
- **Vendored deps only** — `vendor/leaflet-1.9.4`, `vendor/saira`. No `package.json`, no bundler, no framework, no build step. This is deliberate: the console must come up on a Jetson in the field without a toolchain.
- **Docs** — one markdown file per subsystem (`FAILSAFE.md`, `SIMULATION.md`, `DATALINK_SETUP.md`, `SIGNAL_MONITORING.md`, `SYSTEM_HEALTH.md`, `PILOT_SETTINGS.md`, `SHARED_EVENTS.md`, `SHARED_SETTINGS.md`, `DROPLET_SETUP.md`, `REMOTE_ACCESS.md`).
- **Deploy** — `deploy/` holds the systemd units (`airsent-*.service`), `deploy_jetson.sh`, `deploy_vps.sh`, `nginx.conf`, `relay.py`. Telemetry relays through the droplet at `146.190.50.189`.

## Rules

1. **SITL gate.** Nothing that affects flight behavior — MAVLink commands, mission logic, failsafe paths, parameters — ships to hardware until it has been exercised in the ArduCopter sim. Run it and report the result; never assert it works.
2. **Docs track behavior.** A behavior change updates its subsystem markdown in the same commit. Pure refactors leave docs alone. New subsystem → new doc, in the existing dense, declarative style with exact ports, service names and intentional limits.
3. **No new dependencies without asking.** Plain JS/CSS and Python stdlib plus what's already imported. If a library is genuinely needed, propose it first; if approved, vendor it under `vendor/`.
4. **Minimal diffs.** No drive-by refactors, reformatting or renaming. Follow the patterns already here rather than inventing new ones.
5. **Secrets never land in git.** Signing secrets live in `/etc/airsent-presence.env`; API keys go in via `deploy/set_ai_key.sh`. `console-local.example.js` is the template for local config.

## Tests

```bash
python3 tests/sitl/run.py              # safety-path scenarios against real SITL; report in tests/sitl/last_report.json
python3 tests/sitl/run.py --only name  # single scenario
node tests/health-model.test.js        # JS tests are plain node scripts that print "PASS: …"
python airsent_server.py --sim         # sim backend: TCP MAVLink, no camera, loopback sockets, no production relays
```

`tests/sitl/run.py` needs a built SITL: `cd ~/ardupilot && ./waf configure --board sitl --disable-DroneCAN && ./waf copter`. The full one-command rig lives in `~/airsent-sitl`.

## Git

Small fixes may go straight to `main`; substantial changes get a branch and a PR. Flight-critical changes are never "small."

Branch names are bare, lowercase and hyphenated, naming the thing being changed — `viewer-video-watch`, `srt-reachability`, `flight1-profile`, `motor-test-full`. No prefix, no slashes.

Commit subjects are **prose sentences describing the behavior change**, usually with a subsystem prefix — not Conventional Commits:

```
Video: say why nothing is streaming, and tell the truth about a silent camera
FLIGHT 2: hold over the home point for photographs until the pilot lands
Motor test: full control, and fix which motor the buttons actually spin
```

PR descriptions say what changed and how it was verified.
