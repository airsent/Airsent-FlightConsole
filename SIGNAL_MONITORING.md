# Live RC and cellular strength

The Dashboard → RC / TELEMETRY panel now displays two independent, real data sources:

- **RC STRENGTH**: only `RC_CHANNELS.rssi` from the selected autopilot system/component is used. `RADIO_STATUS` is never an RC fallback. A usable reading requires fresh RC channels, a nonzero channel count, and fresh `SYS_STATUS` RC receiver present/enabled/healthy bits. Both reports expire after 3 seconds. RSSI 255 immediately invalidates the measurement; 0, missing health evidence, receiver failure, or Pixhawk loss displays no signal. Serial reconnection clears all previous receiver evidence. These are autopilot-reported measurements; receiver firmware and failsafe configuration must be checked with actual transmitter-off tests.
- **5G / CELLULAR**: ModemManager's `mmcli -m any --output-json`, polled every 2 seconds on the Jetson. Displays modem-reported quality (%), and **NETWORK** displays negotiated access technology so LTE is not mislabeled as 5G. If the modem is absent, inaccessible, or its sample is older than 6 seconds, the value shows `--`.
- **PIXHAWK / FEED**: the Pixhawk heartbeat must be less than 5 seconds old; the VPS telemetry feed must be less than 2 seconds old. The browser additionally clears the panel if no fresh telemetry arrives for 2.5 seconds even if WebSocket remains open.

## Before field testing

1. On the Jetson, check `mmcli -L` and `mmcli -m any --output-json` to verify the installed 5G modem is exposed by ModemManager. A phone hotspot, USB tether, or modem without ModemManager support needs a separate adapter; no synthetic signal is shown.
2. Confirm Pixhawk is sending `RC_CHANNELS` and verify its RSSI field is truly populated by the installed RC receiver. Confirm `SYS_STATUS` RC receiver health clears when the transmitter is switched off and when the receiver is unplugged. If firmware keeps reporting healthy input during failsafe, resolve receiver/autopilot configuration before relying on this indicator.
3. Deploy **both** the updated `airsent_server.py` and the updated `dashboard.html` / `script.js` / `telemetry.js` together; leave `telemetry_server.py` as the VPS relay that supplies `telemetry_age`.
4. On a safe, restrained, props-off test, disconnect RC receiver data, MAVLink serial, cellular modem, and Jetson telemetry in turn. Verify each display goes unknown/offline promptly and comes back automatically. Test the actual modem in motion separately under approved operating procedures.

**Important:** The percentages are modem/radio **reported quality**, not calibrated RF power or verified end-to-end command reliability. Frequent telemetry updates and WebSocket reconnects do **not** guarantee an uninterrupted connection in flight. Set and ground-test Pixhawk/ArduPilot onboard RC-loss and GCS-loss failsafes appropriate to the aircraft and mission; a browser indicator cannot replace them.

## Local regression checks

Run `python3 -m unittest discover -s tests -v` and `node tests/rc-display.test.js`.
The tests use simulated MAVLink messages and DOM elements. They verify decision logic, not physical receiver behavior or the deployed data path. A healthy SYS_STATUS report does not independently prove a transmitter is powered on if the receiver/autopilot misreports failsafe state.
