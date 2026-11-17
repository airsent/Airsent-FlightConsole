# Motor EMI and the GNSS receiver

ESC switching noise reaching the GPS antenna is the most common cause of satellite loss on a multirotor, and it **scales with motor current**. That single fact decides how this can and cannot be tested.

As of 2026-11-16 it is **unmeasured on this aircraft**. Every flight log on the Jetson is a ground test: peak current across all four is **1.8 A**, against 30-45 A expected at hover on the ZD650 with 16 in props. The motors have never really spun while anything was recording.

## Why a props-off bench test cannot clear the aircraft

| What | Current drawn | Share of hover |
|---|---|---|
| One motor, props off, `MAV_CMD_DO_MOTOR_TEST` at 100% | ~1-4 A | ~5-10% |
| All four, props off, armed at full throttle | ~8-16 A | ~25-40% |
| **Hover with 16 in props** | **30-45 A** | **100%** |

`MAV_CMD_DO_MOTOR_TEST` spins **one motor at a time** — ArduPilot's `param5` is a motor *count* that tests them in sequence, not simultaneously. So the console's motor test, which is the only props-off path that does not arm, understates hover current by roughly 10-30x.

A props-off sweep is therefore useful for catching a **gross** fault — a GPS cable cable-tied to a motor lead, a receiver sitting on the power distribution board — and useless as clearance. It can come back clean on an aircraft whose fix collapses in flight. Treating it as clearance is worse than not testing.

**BENCH TEST mode is not a props-off mode.** `mission_engine`'s `bench` flag force-arms at T-10 and takes off at T-0 (`self.vehicle.arm(force=self.bench)`); it bypasses every check except `pixhawk`, `rc_gate_arm` and `armed_confirmed`. It was props-off and no-takeoff when first written, and "Test mode flies" (2026-11-11) changed that. Nothing that spins motors to 100% belongs in any countdown, bench or not: the countdown runs with propellers fitted.

## What actually measures it: `gps_vs_current`

`log_analysis.gps_vs_current()` buckets every GPS fix in a flight log by the motor current drawn at that moment, and reports mean and minimum satellites and mean HDOP per band. It runs on any real flight automatically, costs nothing, and measures the aircraft at genuine hover current.

Bands: `0-5A`, `5-15A`, `15-30A`, `30-45A`, `45A+`.

It **refuses to answer** rather than mislead: a log whose peak current is under 5 A returns `{'available': False, 'why': 'motors never spun …'}`, and the report carries an `info` finding saying the question is still open. That is the state every log on the aircraft is in today.

Findings raised by `rules()`:

- **critical** — 6 or more satellites lost on average between the quietest and busiest current bands. Move the GPS away from the ESCs and power leads, keep its cable off motor wiring, and treat RTK as unusable in flight until fixed.
- **warning** — 3 to 6 satellites lost. Borderline.
- **info** — peak current under 5 A, so the question was not answered.

Covered by `tests/test_gps_vs_current.py` (6 checks) against synthetic DataFlash records, because no real log on the aircraft reaches the interesting branch.

## Cellular is far less exposed than GPS

GPS signals arrive at about **-130 dBm**; cellular at about **-70 dBm**, measured -65 to -74 dBm on this airframe. Cellular also retransmits what it loses. It takes orders of magnitude more noise to disturb the modem than the GNSS receiver, so GPS is the canary — if satellites hold through a flight, the modem almost certainly did too.

Low bands are more exposed to ESC switching harmonics than high ones, so n71 (600 MHz) is the worst case here and n25 (1.9 GHz) the better one. See LINK_ROUTING.md.

## Intentional limits

The GPS mast on the ZD650 (`GPS1_POS_Z = -0.213`, about 21 cm above the centre of gravity) is the standard mitigation and was already in place before any of this was measured. It is not evidence that the problem is absent — it is evidence someone expected it.

Nothing here spins a motor. The only way to answer the question is to fly, and the answer will be in the next log.
