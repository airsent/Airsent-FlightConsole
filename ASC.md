# ASC — Automated Self Checks

`asc.py` on the Jetson. A flight watchdog that runs from vehicle handover (pre-arm) or arming until the aircraft is disarmed on the ground.

**It checks, records and warns. It does not act** — with exactly one exception, below. That is deliberate: ASC never fights the pilot or the Pixhawk. Acting on more findings is intended later; nothing in this file should be read as the aircraft defending itself today.

## What it does

- Runs every check many times a second (`AIRSENT_ASC_HZ`, default 20).
- Raises warnings and critical findings to every console through the fault list.
- Records closely ("focus") anything that is not OK, and records everything for the flight into the mission folder's `asc/`.
- Writes `summary.json` on disarm for later analysis, plus `samples.csv`, `events.jsonl`, `mavlink.tlog`, `params_start.json` and charts.

**The one action it takes on its own:** if the operator console link is lost while the aircraft is in a console-driven mode (GUIDED / AUTO), it holds position after `AIRSENT_ASC_HOLD_AFTER` (10 s — LOITER if the RC throttle is centred, else BRAKE) and returns home after `AIRSENT_ASC_RTL_AFTER` (30 s). If the RC pilot or a Pixhawk failsafe changes mode first, ASC stands down. Nothing else in ASC commands the aircraft.

## Checks

Grouped as the console shows them: Link, RC, Attitude, Motion, GPS, Estimator, Geofence, Altitude, Vibration, Power, Motors, Jetson, Cameras, Network.

Each check is a `Check(id, group, label, fn, when, sustain, fault)`. `fn(sample, ctx) -> (level, message, value)` with levels `OK / INFO / WARN / CRIT`. `sustain` is how long a condition must hold before it is raised, which keeps single-frame noise out of the fault list. `when` is `session`, `armed` or `flying`; a check outside its window is shown greyed rather than hidden, so nothing silently disappears.

### The GPS group, and why `gps_degraded` exists

`gps_fix`, `gps_sats` (warn under `min_sats`, 10) and `gps_hdop` (warn over `max_hdop`, 1.5) are **absolute** thresholds. They miss the shape that actually happens on this airframe.

`gps_degraded` compares satellites against **this session's own best**, warning at `sats_drop_warn` (35%) and going critical at `sats_drop_crit` (50% — more than half of them gone). It needs a baseline of at least `sats_baseline_min` (12) before it will judge anything, so a flight that never had a good fix is not measured against one.

Why: 26 satellites falling to 12 is a collapse that `min_sats = 10` never sees. On 2026-11-16 the 5G modem was measured taking this aircraft from 26-30 satellites down to 3-8, so a large relative drop is a known shape here, not a hypothetical. Motor and ESC noise produces the same shape and scales with current — see MOTOR_EMI.md.

The baseline is taken **while disarmed and frozen at arming**. If it kept rising it would track the signal down as ESC noise pulled it, and the check would never fire. It runs `when='armed'`, so it covers motors at idle before take-off as well as flight.

Overridable: `AIRSENT_ASC_SATS_DROP_WARN`, `AIRSENT_ASC_SATS_DROP_CRIT`, `AIRSENT_ASC_SATS_BASELINE_MIN`.

## Where it appears in the console

ASC state reaches every console (viewers included) in telemetry as the `asc` block. Check names and groups (`defs`) are sent every ~10 s; states (`live`) every frame. So no two views can disagree — there is one source.

| Surface | What it is for |
|---|---|
| **`asc.html` — ASC Monitor** | A standing page for somebody whose job is watching. Read-only. |
| `failsafe.html` — ASC bar + live drawer | The ASC action switch, and a slide-out of every check. |
| `logs.html` | The recorded session after the flight. |

### The ASC Monitor page

`asc.html` + `asc-monitor.js`. Built for flight day, when one person watches this and nothing else. **It has no controls that can reach the aircraft** — the action switch stays on the failsafe page.

- **Verdict banner**: `ALL CLEAR` / `n WARNING` / `n CRITICAL` / `NO DATA`, naming the offending checks. The banner flashes while anything is critical.
- **A stale link reads as `NO DATA`, never as healthy.** `stateFresh()` is false after 6 s without telemetry, and the Telemetry tile reads `STALE`. This matters: silence must never look like health.
- **Document title carries the alarm** (`(!) 1 CRIT — …`) so a background tab shows it.
- **Audible tone on a *new* critical**, opt-in via a checkbox and remembered. It is a generated tone, not an audio file, because the console ships no assets and must come up in the field without fetching anything; browsers also block audio until the page has been interacted with, which the checkbox satisfies. A standing critical does not re-sound; a second, different critical does.
- **Worst so far** survives the problem clearing — a critical that cleared still happened. It resets when a new session starts.
- **Event log** with wall-clock times derived from the session start, so "when did that begin" is answerable without arithmetic.
- Per-check sparkline over the last 60 s, kept whether or not the page is in front.

Covered by `tests/asc-monitor.test.js` against a stub DOM: banner truthfulness, stale-link handling, title alarm, tone firing only on new criticals, worst-so-far across clearing and session change, grouped rendering, hide-not-active, event stamps.

## Intentional limits

ASC warns; it does not land the aircraft on a bad check. A satellite collapse will shout on the monitor page and be recorded in full, and the aircraft will keep flying. The Pixhawk's own `FS_EKF_ACTION = 1` lands it if the estimator gives up, and `mission_engine` lands a countdown profile if `ekf_healthy` goes false during a climb or hold — but neither watches satellite count. Acting on ASC findings is future work and a decision for the operator, not a default.

The monitor page cannot show what the Jetson does not send. If telemetry is down, it says so rather than guessing.
