# System Health evidence and freshness

The page consumes the same authenticated console telemetry feed as other live
pages. It never sends flight commands. Demo stays isolated; simulation uses its
own routed telemetry and does not probe real cameras.

Confirmed mappings:
- IMU, barometer, magnetometer and FC outputs use SYS_STATUS present/enabled/
  healthy masks, with a five-second freshness limit. Missing evidence is not OK.
- RC uses rc_signal.input_present with channel and receiver-health freshness;
  RSSI may remain unknown while a receiver is connected.
- PWM outputs are displayed as command outputs. ESC health uses fresh Datalink
  channel telemetry, not PWM. Zero RPM is valid when telemetry is live.
- Power prefers primary BATTERY_STATUS values with SYS_STATUS fallback. Unknown
  sentinels, stale reports and sub-one-volt noise are not battery measurements.
  Current and reported percentage may be available while voltage remains unknown.
- Raw optical-flow quality (0..255) is converted to percent. Range remains sensor
  distance; relative altitude is labelled REL ALT, not AGL.
- GPU reads Jetson sysfs load counters as permille, including the newer
  /sys/devices/platform/bus@0/17000000.gpu/load path. Zero means measured idle.
- Network uses the fresh network_link report for Wi-Fi/Ethernet/cellular type and
  strength. No fabricated dBm reading or hardcoded cellular-only status.
- Camera status requires recent inbound video bytes from each WebRTC probe;
  an open track with no ongoing video does not remain STREAMING indefinitely.

The telemetry socket clears every telemetry-backed field and reconnects after
3.5 seconds without valid data, or seven seconds without an initial frame.
Camera activity remains independently monitored.

Optional ground sources (not supplied by the current Jetson):
- ground_computer: {connected:true, age:<5, cpu, ram, temp,
  ntrip_connected, radio_connected, pad_ready}
- rtk_base: {connected:true, age:<5, fix_type, sats}

Absent sources display NOT REPORTED. No readings are derived from unrelated
sensors, and this change does not install or invent a ground-station collector.

Live validation found that the configured Datalink USB-UART adapter is absent,
voltage measurements are invalid/noise-level, and magnetometer health is not
reported in SYS_STATUS. Those conditions remain visible instead of being hidden
with healthy defaults. Existing camera/streaming configuration is preserved.
