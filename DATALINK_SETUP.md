# Datalink TX1 motor telemetry

This integration is receive-only. It never writes serial data, arms motors,
changes ESC settings, or issues a motor-test command. Real-mode Actuators
buttons are disabled; run props-off motor tests from QGroundControl.

## Installation under test

- ESC: T-Motor ALPHA 40A, factory configured for the motor.
- Motor: reported as 5208 KV340; T-Motor's MN5208 KV340 specification lists
  24N22P (22 magnetic poles).
- Datalink: V2 TX1 (115200 baud, 8-N-1).
- Adapter: CP2102, identified on this Jetson as
  `/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0`.
- Datalink TX1 connects to adapter RX with common GND. No UART transmission is
  needed. Verify voltage levels against the adapter and Datalink specifications.

Install both `airsent_server.py` and `datalink_telemetry.py` in the same folder.
Preserve the installation's Pixhawk USB path and VPS environment/settings.
Launch the backend with:

```sh
export DATALINK_PORT=/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0
export DATALINK_MOTOR_POLES=22
python3 -u airsent_server.py
```

Deploy `actuators.html` and `actuators-live.js` together to the website root.
The existing VPS relay forwards `esc_telemetry` without a relay change.

## Data interpretation

The reader supports the 160-byte, 0x9B/0x9E TX1 aggregate frame, version 0/1,
command 2, eight 19-byte ESC records. CRC-CCITT/XMODEM validates the entire
payload. Both documented CRC byte orders are recognized; the published
version-0 sample actually uses little-endian CRC despite its text description.
The decoder has passed a user-captured idle sample from the ALPHA 40A/T-Motor Datalink V2: two valid CRC frames, D1–D4 at 19.6–19.7 V, zero output and RPM. Nonzero motor speed still needs validation. Raw signed current -1 is treated as unavailable, as observed in this idle capture.

Input/output throttle are the ESC's reported fractions (raw/32767), not inferred
motor power or thrust. PWM remains the independent Pixhawk command output.
RPM follows the ArduPilot Datalink driver's raw-speed scaling (raw * 10 / poles).
Confirm shaft RPM against a tachometer before treating that conversion as
hardware verified. Electrical calibration by the ESC manufacturer does not
by itself validate a telemetry conversion.

The page displays voltage, current, output percentage and RPM per Datalink slot.
D1 is initially displayed with M1, D2 with M2, etc.; confirm that mapping by
running one motor at a time. Change wiring/mapping if the test identifies a
different correspondence. No torque or thrust percentage is implied.

CRC failures, stale link/ESC samples, empty slots and missing fields show `--`.
A repeated ESC packet counter does not refresh that ESC's age. The UART reader
reconnects after a device disconnect. The browser clears/reconnects on feed loss.
Unconfigured or silent UART is reported as waiting for ESC data, not zero RPM.
Zero RPM is shown only for a fresh, valid ESC sample.

## Checks

```sh
python3 -m unittest discover -s tests -v
node tests/actuators-live.test.js
node tests/rc-display.test.js
node tests/telemetry-reconnect.test.js
```

Hardware checks, with propellers removed:

1. Capture TX1 bytes with the motors stopped; verify CRC-valid frames when present.
2. Have the operator run a low-power, short motor test in QGroundControl.
3. Confirm the expected Datalink slot changes, output percentage rises and RPM
   becomes nonzero; confirm it returns after stopping.
4. Check each motor independently to establish D-slot to M-output mapping.
5. Disconnect only the telemetry adapter and verify ESC fields clear within
   about 2 seconds while Pixhawk command data continues updating.
6. Reconnect the adapter and verify automatic recovery, then test browser feed
   loss/reconnection separately.

A complete pass requires actual motor data, mapping, and RPM cross-checks; unit
tests alone do not establish those hardware properties.

## References

- [T-Motor Datalink V2 manual](https://device.report/m/1de916d1ccb17b4f0b448d10bb4ff5c1616895670a80db48cb9d881b0a6a7380)
- [T-Motor MN5208 KV340 specification](https://store.tmotor.com/product/mn5208-motor-navigator-type.html)
- [HW Data Link protocol 170730](https://robu.in/wp-content/uploads/2023/07/Communication_Protocol_of_HW_Data_Link.pdf)
- [ArduPilot Datalink driver](https://github.com/ArduPilot/ardupilot/blob/master/libraries/AP_Scripting/drivers/Hobbywing_DataLink.lua)
