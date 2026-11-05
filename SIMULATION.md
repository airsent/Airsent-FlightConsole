# Simulation login

Use the normal console login with username `simulation`. The simulation password is configured on the server as `AIRSENT_SIMULATION_PASSWORD`; operator and viewer passwords and signed sessions are separate.

Simulation mode opens the same dashboard and pilot interface, with an amber **SITL · SIMULATED AIRCRAFT** badge. On the hosted website, telemetry and commands use `/simulation/telem` and `/simulation/cmd`. These authenticated WebSocket routes accept only a signed simulation session and proxy exclusively to a separate simulated aircraft backend. Simulation session query parameters are excluded from nginx access logging.

Operator/viewer sessions continue using `/telem`, `/cmd`, `/presence`, and `/sequence`. The deployed map configuration is preserved. The real Jetson backend is not replaced by deploying this feature.

## Backend isolation

The SIM backend starts only with `python airsent_server.py --sim`. It uses a MAVLink TCP URL, skips camera dependencies and workers, disables physical ESC telemetry, binds its WebSockets to loopback, and never starts the production VPS relays. Normal invocation retains the existing hardware defaults, camera workers and production relays.

The hosted simulator uses the ArduCopter binary and copter defaults from the tested `radarku/ardupilot-sitl` image digest `sha256:2364ae14e190e4cdd5d0839b3a55e6b5c6a980d9ff0000b1be7b8baae6fd50f1`. This is the same binary used by the local Docker setup; the production droplet runs it natively to avoid an additional Docker daemon. Obtain ArduPilot source and licensing from the image's upstream ArduPilot project when redistributing the binary; binaries are not committed here.

Services in `deploy/`: `airsent-sitl`, `airsent-simulation-backend`, `airsent-simulation-gateway`. Files live in `/opt/airsent-simulation`, state in `/var/lib/airsent-simulation`, and Python dependencies in `/opt/airsent-simulation-venv`. The same-user parent wrapper is required by this binary's parent-liveness check under systemd. Services are unprivileged, use low CPU scheduling weight, have memory limits and permit only loopback networking.

Ports: simulator MAVLink 15760; telemetry 18765; commands 18767; camera placeholders 18766/18768; authenticated gateway 19760. Nginx proxies `/simulation/` to the gateway. The gateway reads the existing presence signing secret via `/etc/airsent-presence.env`; credentials and signing secrets must never be committed.

## Local testing

Start the existing local Docker SITL and SIM backend, serve this directory locally, then open `fly-local.html`. Local simulation endpoints remain loopback ports 8765/8767. The local bootstrap is blocked on public hostnames. The hosted simulation account uses the normal server-verified login instead.

## Intentional limits

All simulation users share one simulated aircraft; do not issue conflicting commands from multiple browsers. There are no real camera images, hardware ESC RPM/current, physical RC receiver, or physical rangefinder readings. Shared launch countdown controls are disabled in simulation; operator/viewer presence counters track the real console sessions, not simulated operators. ArduCopter's simulated parameter state resets when its service restarts.

Takeoff stops manual velocity streaming and keeps GUIDED through the climb; switching to LOITER immediately after NAV_TAKEOFF would cancel the climb. Actual aircraft validation remains necessary before relying on flight behavior on hardware.
