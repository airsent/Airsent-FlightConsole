# Reversible local failsafe trial

Branch: codex/failsafe-trial. The main branch is unchanged. No deployment or push is part of this trial.

Start the console directory with a local HTTP server. Open dashboard.html?trial=demo and failsafe.html in two tabs on the same origin. The dashboard emits its existing generated demo frame through an optional BroadcastChannel adapter; the failsafe page mirrors selected fields. The explicit trial URL uses demo mode without changing the saved login mode. Keep the dashboard tab open for continuous updates.

The adapter has no aircraft command interface and stores no telemetry persistently. It whitelists battery/sensor health fields, excluding GPS coordinates and logs. The failsafe page creates no vehicle WebSocket. Dashboard data older than four seconds is marked stale and values are cleared. That is dashboard receipt age, not proof of fresh vehicle sensor readings. Onboard configuration remains unverified even when telemetry arrives. Incident scenarios remain explicitly simulated.

A normal dashboard in real mode can mirror received telemetry, but that requires the normal relay configuration. This trial verifies the local demo path only, not aircraft failsafe execution. There are no parameter writes or automatic recovery commands.

## Discard the trial

Close the local preview tabs and server. In this checkout, with no newer uncommitted changes, run `git switch main`. That returns to the original committed console and removes the trial files from the active checkout. The trial branch remains recoverable; there is no need to delete it. Preserve any later edits before switching.

An independent original-code ZIP is also supplied as FlightConsole-before-failsafe-trial.zip. Its contents come directly from main before this trial. Restoring it does not require Git.

Trial files: failsafe.html, failsafe.css, failsafe.js, failsafe-data.js, failsafe-bridge.js, failsafe-monitor.js; existing HTML menus add one navigation link. dashboard.html loads the optional bridge; telemetry.js publishes selected original demo/received frames; script.js accepts the explicit local demo trial URL. No shared dashboard CSS changes are required.

## Existing-hardware sensor additions

Landing & Sensors contains target loss, obstacle/depth loss, rangefinder validity/freshness, optical-flow degradation, and downward-camera loss/frozen frames. Camera/thermal review is in Power & Flight. Tower/payload interlocks were removed from the catalogue because their feedback integrations are not confirmed. Total: 16 review rules.

The bridge now allows existing rangefinder and optical-flow quality values. The selected-rule evidence display can show those values and the existing downward-camera flag, but explicitly reports missing sensor timestamps / validity. These are display and catalogue additions, not working onboard failsafe logic. Demo frames without those fields correctly show “not supplied”; values are not fabricated. Independent acquisition timestamps, active EKF source, tested sensor limits and onboard integration remain required.

## Visual status semantics

System Health is the reference for this page's IBM Plex Sans / IBM Plex Mono typography and neutral surfaces. Unknown/unverified configuration is neutral gray, incomplete software checks are amber, selection and demo source are blue. Red is reserved for reported fault observations; simulated incidents are labeled and amber. No unknown state is displayed as healthy green. Existing System Health and Sensor Status pages are unchanged.
