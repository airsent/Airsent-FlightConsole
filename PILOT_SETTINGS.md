# Pilot quick settings — confirmed aircraft state

Prepared against upstream d6ae2bf. Installed on the verified live Jetson at 192.168.0.43, with the original installed server backed up.

## Setting ownership

- RTH altitude -> ArduCopter RTL_ALT_M (metres) or legacy RTL_ALT (centimetres).
- Fence radius -> FENCE_RADIUS (metres).
- Navigation maximum speed -> WP_SPD (m/s) or legacy WPNAV_SPEED (cm/s).
- Fence maximum altitude -> FENCE_ALT_MAX (metres).
- Units, takeoff altitude, obstacle stop distance/detection and keyboard movement,
  climb and yaw rates -> persisted Jetson preferences. Takeoff commands consume
  the selected height; received manual velocity vectors are clamped by the
  companion before sending to the flight controller.

A parameter is editable only after that aircraft has reported it. PARAM_SET
writes wait for a subsequent matching PARAM_VALUE; rejection, different values,
reconnection and timeout do not produce a success state. Parameter reads use the
existing single MAVLink receive loop. Waiting writes run outside the async
telemetry event loop. No aircraft parameter is written automatically at startup.

All connected pages consume pilot_settings in aircraft telemetry. Viewers cannot
write. Edits require a fresh heartbeat and a disarmed aircraft. Version checks
reject stale edits; simultaneous backend writes are serialized. Stale telemetry
disables settings controls. Slider previews do not become confirmed values.

FENCE_ENABLE and FENCE_TYPE are read for display. Saving fence radius or altitude
does not enable the fence or change its configured types/actions. The panel
states when those limits may be inactive. Navigation speed is not advertised as
a universal speed limit for every flight mode.

## Installation when the Jetson is available

1. Read the current installed server and service configuration, compare against
   upstream, and back it up. Preserve field-specific camera, MAVLink, streaming
   and environment configuration; do not overwrite it blindly with the repo.
2. Install pilot_settings.py beside airsent_server.py and apply the server hooks
   from this change. Do not include the separately pending cellular changes.
3. Capture the current obstacle detection/stop-distance settings before any
   restart and preserve reviewed preferences in AIRSENT_PILOT_SETTINGS_FILE.
   The default preference path is ~/.config/airsent/pilot-settings.json; choose
   a writable state directory if systemd restricts the home directory.
4. Confirm the aircraft is disarmed before restarting its telemetry backend.
5. Verify the connected firmware reports the supported parameter names and
   types. Compare the values with the ground station before enabling writes.
6. Deploy pilot.html and pilot-quick-settings.js together only after the live
   backend supports this protocol. Existing live pages have not been deployed
   with an unsupported settings UI.

## Verification completed

- Unit checks: conversions, actual readback, timeout/rejection, missing parameters,
  persistence, stale versions, armed rejection, finite values and velocity clamps.
- Browser checks: changing RTH altitude reached an isolated simulated Pixhawk;
  a navigation-speed change appeared on another Pilot page; test values restored.
- Isolated simulator integration: all eleven settings changed and restored,
  three telemetry clients converged, invalid/stale edits rejected, companion
  preferences persisted through backend restart. No arm or flight commands sent.
- The existing public simulator was armed and was left untouched. Separate
  loopback-only test instances were used and stopped after verification.

Live verification: host key matched the previous .31 address. Jetson telemetry
confirmed the aircraft disarmed with a fresh heartbeat before installation.
The installed field-specific server was patched in place, not replaced with repo
configuration. All eleven current values passed same-value confirmations;
RTL_ALT_M, FENCE_RADIUS, WP_SPD and FENCE_ALT_MAX were read back from the Pixhawk.
A units change reached a second hosted Pilot page and was restored to metric.
No arm, takeoff or movement commands were sent, and flight parameter values were
not changed. Actual flight behavior was not flight-tested.

The existing public SITL session was armed. It was not restarted; its settings
backend update must wait until that session is disarmed. This does not block the
live Jetson installation. The separate test simulators have been stopped.
