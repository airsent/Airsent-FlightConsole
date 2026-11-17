# Simulation login

Use the normal console login with username `simulation`. The simulation password is configured on the server as `AIRSENT_SIMULATION_PASSWORD`; operator and viewer passwords and signed sessions are separate.

Simulation mode opens the same dashboard and pilot interface, with an amber **SITL · SIMULATED AIRCRAFT** badge. On the hosted website, telemetry and commands use `/simulation/telem` and `/simulation/cmd`. These authenticated WebSocket routes accept only a signed simulation session and proxy exclusively to a separate simulated aircraft backend. Simulation session query parameters are excluded from nginx access logging.

Operator/viewer sessions continue using `/telem`, `/cmd`, `/presence`, and `/sequence`. The deployed map configuration is preserved. The real Jetson backend is not replaced by deploying this feature.

## Backend isolation

The SIM backend starts only with `python airsent_server.py --sim`. It uses a MAVLink TCP URL, skips camera dependencies and workers, disables physical ESC telemetry, binds its WebSockets to loopback, and never starts the production VPS relays. Normal invocation retains the existing hardware defaults, camera workers and production relays.

The hosted simulator uses the ArduCopter binary and copter defaults from the tested `radarku/ardupilot-sitl` image digest `sha256:2364ae14e190e4cdd5d0839b3a55e6b5c6a980d9ff0000b1be7b8baae6fd50f1`. This is the same binary used by the local Docker setup; the production droplet runs it natively to avoid an additional Docker daemon. Obtain ArduPilot source and licensing from the image's upstream ArduPilot project when redistributing the binary; binaries are not committed here.

**These services are disabled on the droplet and do not start at boot.** They ran continuously from deployment until 2026-11-15, when the gateway showed three log lines and zero connections over thirty days while ArduCopter SITL had consumed 199 minutes of CPU on the droplet's single core. Start them before a demo and stop them after:

```bash
systemctl start airsent-sitl airsent-simulation-backend airsent-simulation-gateway
systemctl stop  airsent-sitl airsent-simulation-backend airsent-simulation-gateway
```

`systemctl enable --now` on all three restores the old always-on behaviour. While they are stopped, signing in as `simulation` reaches nginx and fails at the gateway; the live operator and viewer paths are unaffected, because they never touch these services.

Services in `deploy/`: `airsent-sitl`, `airsent-simulation-backend`, `airsent-simulation-gateway`. Files live in `/opt/airsent-simulation`, state in `/var/lib/airsent-simulation`, and Python dependencies in `/opt/airsent-simulation-venv`. The same-user parent wrapper is required by this binary's parent-liveness check under systemd. Services are unprivileged, use low CPU scheduling weight, have memory limits and permit only loopback networking.

Ports: simulator MAVLink 15760; telemetry 18765; commands 18767; camera placeholders 18766/18768; authenticated gateway 19760. Nginx proxies `/simulation/` to the gateway. The gateway reads the existing presence signing secret via `/etc/airsent-presence.env`; credentials and signing secrets must never be committed.

## Local testing

Start the existing local Docker SITL and SIM backend, serve this directory locally, then open `fly-local.html`. Local simulation endpoints remain loopback ports 8765/8767. The local bootstrap is blocked on public hostnames. The hosted simulation account uses the normal server-verified login instead.

## Safety-path suite simulator

`tests/sitl/run.py` uses a **different simulator from everything above**, and the two are not interchangeable. The hosted and local demo simulators run the `radarku/ardupilot-sitl` image, which carries **ArduCopter 4.0.3 (January 2021)**. That is acceptable for a demo. It is not acceptable for a safety gate: the firmware we fly is 4.6.3, and failsafe behaviour and parameter names moved substantially across those releases.

The suite also cannot attach to any already-running SITL. It spawns the `arducopter` binary itself, wipes its EEPROM for a fresh start, and kills and restarts the process to simulate pulling the Pixhawk's power, so it needs a binary it owns. Pointing it at a container's MAVLink port fails with `FileNotFoundError: .../build/sitl/bin/arducopter`.

So the suite ships its own image, pinned to the firmware under test:

```bash
bash tests/sitl/docker_run.sh                    # all scenarios, ~15 min
bash tests/sitl/docker_run.sh --only s_geofence  # one, or a comma-separated list
AIRSENT_SITL_REBUILD=1 bash tests/sitl/docker_run.sh   # rebuild from scratch
```

`tests/sitl/Dockerfile` builds ArduCopter from source at tag `Copter-4.6.3` (override with `AIRSENT_COPTER_TAG`) and installs only the three hard third-party imports on the `--sim` path: `numpy`, `pymavlink`, `websockets`. The camera stack (`depthai`, `cv2`, `pupil_apriltags`, `gi`) is imported inside functions `--sim` never calls, so it is deliberately absent.

The first build takes 30–45 minutes; afterwards the layer cache makes startup immediate. The repo is bind-mounted so `last_report.json` lands back in the working tree.

**Why a container at all:** the native macOS build hangs — `./waf configure` succeeds, then `./waf copter` sits producing zero object files indefinitely. The same build is fine on Linux, so building in a container fixes the cause rather than working around it. Before this existed the suite had no machine it could run on, and flight-logic changes shipped ungated.

**Keep the pin honest.** If the Pixhawk is flashed to a different firmware, bump `COPTER_TAG` in the Dockerfile to match and rebuild, or the gate silently tests the wrong thing.

Architecture does not matter for correctness: SITL at `--speedup 1` is not CPU-bound. Measured at **1.00x real-time on 5% of one core** even while emulating x86 on Apple Silicon, so timing-sensitive scenarios behave the same either way. Native architecture only builds faster.

## Intentional limits

All simulation users share one simulated aircraft; do not issue conflicting commands from multiple browsers. There are no real camera images, hardware ESC RPM/current, physical RC receiver, or physical rangefinder readings. Shared launch countdown controls are disabled in simulation; operator/viewer presence counters track the real console sessions, not simulated operators. ArduCopter's simulated parameter state resets when its service restarts.

Takeoff stops manual velocity streaming and keeps GUIDED through the climb; switching to LOITER immediately after NAV_TAKEOFF would cancel the climb. Actual aircraft validation remains necessary before relying on flight behavior on hardware.
