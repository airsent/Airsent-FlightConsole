# Integrated failsafe console

The full console includes failsafe.html in the common Airsent navigation. Run this directory with a local HTTP server and open dashboard.html and failsafe.html on the same origin. For generated dashboard telemetry, use dashboard.html?trial=demo and keep that tab open.

## Current behavior

Twelve system cards share the console navigation. Each has a local activation toggle and response settings. Red/green card top edges indicate local activation, not verified aircraft health. ASC reviews available dashboard telemetry every second, counts reported faults, and auto-enables on a real telemetry in_air=true transition. Missing data remains unknown. Only explicit mapped status evidence is evaluated; this is not comprehensive onboard protection.

ASC manual changes use a browser-local password lock. A salted PBKDF2 hash is stored in localStorage. This is a convenience lock, not server-side authorization. Deployed control services require backend authentication and authorization. No passwords are embedded in source.

Return-home settings, motor responses, and parachute requirements are local review/simulation only. No aircraft command, parameter-write, motor-stop or parachute-deploy interface is added. Back-camera hardware remains unverified. The original dashboard telemetry connection remains separate.

## Files and integration

failsafe.html, failsafe.css, failsafe.js, failsafe-data.js, failsafe-bridge.js, failsafe-monitor.js, failsafe-auto.js, failsafe-planning.js, asc-lock.js and console-select.js implement the page. console-navigation.css standardizes console page menus. Dashboard telemetry publishes whitelisted ephemeral fields through the optional bridge; telemetry is not persisted.

## Backup and deployment

The original console is preserved in outputs/FlightConsole-before-failsafe-trial.zip outside this project directory. The failsafe console is integrated into main. Deploying the GitHub code to the droplet is a separate step. The latest full-project ZIP is outputs/Airsent-FlightConsole-with-failsafes.zip.

## Dashboard integration update

The dashboard includes a live UTC event clock, CSV export of all events captured in the current page session, a direct failsafe link, and Activate/Deactivate controls for RC Loss, Power, 5G Link and GPS/Navigation. Activation state is shared through browser-local storage with the full failsafe page. It does not change aircraft configuration. Event export survives visible log pause/reset within the session, but not a page reload. Shared branding and navigation compensate for each page’s visual scale.
