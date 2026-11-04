# Live RC and cellular strength

The Dashboard → RC / TELEMETRY panel now displays two independent, real data sources:

- **RC STRENGTH**: `RC_CHANNELS.rssi` from Pixhawk over the Jetson serial MAVLink connection. The MAVLink 0–254 field is scaled to 0–100%. `255` means unknown. If `RC_CHANNELS` has not provided a valid measurement for 3 seconds, an available `RADIO_STATUS.rssi` value may be used as a **telemetry-radio** fallback, rather than asserted as an RC-control signal. It shows `--` if no recent value exists.
- **5G / CELLULAR**: ModemManager's `mmcli -m any --output-json`, polled every 2 seconds on the Jetson. Displays modem-reported quality (%), and **NETWORK** displays negotiated access technology so LTE is not mislabeled as 5G. If the modem is absent, inaccessible, or its sample is older than 6 seconds, the value shows `--`.
- **PIXHAWK / FEED**: the Pixhawk heartbeat must be less than 5 seconds old; the VPS telemetry feed must be less than 2 seconds old. The browser additionally clears the panel if no fresh telemetry arrives for 2.5 seconds even if WebSocket remains open.

## Before field testing

1. On the Jetson, check `mmcli -L` and `mmcli -m any --output-json` to verify the installed 5G modem is exposed by ModemManager. A phone hotspot, USB tether, or modem without ModemManager support needs a separate adapter; no synthetic signal is shown.
2. Confirm Pixhawk is sending `RC_CHANNELS` and verify its RSSI field is truly populated by the installed RC receiver. `RADIO_STATUS` may refer to the telemetry radio rather than the pilot's RC receiver.
3. Deploy **both** the updated `airsent_server.py` and the updated `dashboard.html` / `telemetry.js` together; leave `telemetry_server.py` as the VPS relay that supplies `telemetry_age`.
4. On a safe, restrained, props-off test, disconnect RC receiver data, MAVLink serial, cellular modem, and Jetson telemetry in turn. Verify each display goes unknown/offline promptly and comes back automatically. Test the actual modem in motion separately under approved operating procedures.

**Important:** The percentages are modem/radio **reported quality**, not calibrated RF power or verified end-to-end command reliability. Frequent telemetry updates and WebSocket reconnects do **not** guarantee an uninterrupted connection in flight. Set and ground-test Pixhawk/ArduPilot onboard RC-loss and GCS-loss failsafes appropriate to the aircraft and mission; a browser indicator cannot replace them.
