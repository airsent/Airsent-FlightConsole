"""Flight log analyser: ArduPilot DataFlash (.bin) -> report.json

Runs on the Jetson after the Pixhawk log is downloaded into the mission folder (or from
the Logs page). It produces hard numbers and rule-based findings; the AI review on the
VPS only ever sees this report, never the raw log.

    python3 log_analysis.py <mission folder or pixhawk.bin>     # writes analysis/report.json

What it measures (during flight unless noted):
  vibration (VIBE, clipping) · hover throttle (CTUN ThO / learned ThH) · motor outputs and
  balance (RCOU) · attitude / altitude / position tracking (ATT, CTUN, PSCN/PSCE) ·
  battery (BAT) · GPS · errors, failsafes and mode changes (ERR, MSG, MODE, EV) ·
  gyro spectrum for the harmonic notch (ISBH/ISBD batch sampler, needs INS_LOG_BAT_MASK=1)

Nothing here writes to the aircraft. Proposals are suggestions for a person to review.
"""
import json
import math
import os
import sys
import time

REPORT_VERSION = 1

# What the aircraft is — sent with every report so the reviewer knows the airframe.
AIRFRAME = {
    'frame': 'ZD650 quad X, 650 mm carbon',
    'motors': 'T-Motor MN5208 KV340',
    'props': 'CFProp NS16x5.4 carbon (16 in)',
    'escs': 'T-Motor Alpha 40A 6S FOC, PWM',
    'battery': 'Tattu Plus 6S 10000 mAh',
    'flight_controller': 'Pixhawk 6C, ArduCopter 4.6',
    'payload': 'Jetson Orin Nano, OAK-D Lite, RTK GPS, H-Flow, RM520N 5G modem',
}

# Parameters copied into the report (the values the aircraft flew with).
REPORT_PARAMS = (
    'FRAME_CLASS', 'FRAME_TYPE', 'MOT_PWM_MIN', 'MOT_PWM_MAX', 'MOT_SPIN_ARM', 'MOT_SPIN_MIN', 'MOT_SPIN_MAX',
    'MOT_THST_HOVER', 'MOT_HOVER_LEARN', 'MOT_THST_EXPO', 'MOT_BAT_VOLT_MAX', 'MOT_BAT_VOLT_MIN',
    'INS_GYRO_FILTER', 'INS_ACCEL_FILTER', 'INS_LOG_BAT_MASK', 'INS_LOG_BAT_OPT',
    'INS_HNTCH_ENABLE', 'INS_HNTCH_MODE', 'INS_HNTCH_FREQ', 'INS_HNTCH_BW', 'INS_HNTCH_REF', 'INS_HNTCH_ATT',
    'INS_HNTCH_HMNCS', 'INS_HNTCH_OPTS',
    'ATC_RAT_RLL_P', 'ATC_RAT_RLL_I', 'ATC_RAT_RLL_D', 'ATC_RAT_RLL_FLTD', 'ATC_RAT_RLL_FLTT',
    'ATC_RAT_PIT_P', 'ATC_RAT_PIT_I', 'ATC_RAT_PIT_D', 'ATC_RAT_PIT_FLTD', 'ATC_RAT_PIT_FLTT',
    'ATC_RAT_YAW_P', 'ATC_RAT_YAW_I', 'ATC_RAT_YAW_D', 'ATC_RAT_YAW_FLTE',
    'ATC_ANG_RLL_P', 'ATC_ANG_PIT_P', 'ATC_ANG_YAW_P', 'ATC_ACCEL_R_MAX', 'ATC_ACCEL_P_MAX', 'ATC_ACCEL_Y_MAX',
    'PSC_ACCZ_P', 'PSC_ACCZ_I', 'PSC_VELZ_P', 'PSC_POSZ_P', 'PSC_VELXY_P', 'PSC_VELXY_I', 'PSC_VELXY_D', 'PSC_POSXY_P',
    'ANGLE_MAX', 'PILOT_SPEED_UP', 'PILOT_SPEED_DN', 'LOIT_SPEED', 'WPNAV_SPEED',
    'BATT_CAPACITY', 'BATT_LOW_VOLT', 'BATT_CRT_VOLT', 'BATT_FS_LOW_ACT', 'BATT_FS_CRT_ACT',
    'FENCE_ENABLE', 'FENCE_RADIUS', 'FENCE_ALT_MAX', 'RTL_ALT_M', 'RTL_ALT', 'GPS1_POS_X', 'GPS1_POS_Y', 'GPS1_POS_Z',
)

TYPES = ['PARM', 'MSG', 'MODE', 'EV', 'ERR', 'CTUN', 'VIBE', 'RCOU', 'ATT', 'BAT', 'PSCN', 'PSCE', 'PSCD',
         'GPS', 'ISBH', 'ISBD']

COPTER_MODES = {0: 'STABILIZE', 1: 'ACRO', 2: 'ALT_HOLD', 3: 'AUTO', 4: 'GUIDED', 5: 'LOITER', 6: 'RTL', 7: 'CIRCLE',
                9: 'LAND', 11: 'DRIFT', 13: 'SPORT', 14: 'FLIP', 15: 'AUTOTUNE', 16: 'POSHOLD', 17: 'BRAKE',
                18: 'THROW', 19: 'AVOID_ADSB', 20: 'GUIDED_NOGPS', 21: 'SMART_RTL', 22: 'FLOWHOLD', 23: 'FOLLOW',
                24: 'ZIGZAG', 25: 'SYSTEMID', 26: 'AUTOROTATE', 27: 'AUTO_RTL'}
EV_ARMED, EV_DISARMED, EV_LAND_COMPLETE = 10, 11, 18
ERR_SUBSYS = {1: 'main', 2: 'radio', 3: 'compass', 5: 'radio failsafe', 6: 'battery failsafe', 8: 'GCS failsafe',
              9: 'fence', 10: 'flight mode change refused', 11: 'GPS', 12: 'crash check', 13: 'flip', 15: 'parachute',
              16: 'EKF check', 17: 'EKF failsafe', 18: 'barometer', 19: 'CPU', 20: 'ADS-B failsafe', 21: 'terrain',
              22: 'navigation', 23: 'terrain failsafe', 24: 'EKF primary changed', 25: 'thrust loss', 26: 'sensor failsafe',
              27: 'leak failsafe', 28: 'pilot input', 29: 'vibration failsafe', 30: 'internal error', 31: 'dead reckoning failsafe'}
BENIGN_MSG = ('initialised', 'alignment complete', 'origin set', 'is using gps', 'ekf3 active', 'new fence', 'land complete')

# quad X motor layout (ArduPilot numbering): name, x (fwd +), y (right +), spin
QUAD_X = {1: ('front-right', 1, 1, 'CCW'), 2: ('rear-left', -1, -1, 'CCW'),
          3: ('front-left', 1, -1, 'CW'), 4: ('rear-right', -1, 1, 'CW')}


def _r(v, n=2):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(v, n)


def _stats(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    if not xs:
        return None
    xs_s = sorted(xs)
    n = len(xs_s)
    return {'mean': sum(xs) / n, 'max': xs_s[-1], 'min': xs_s[0], 'p95': xs_s[min(n - 1, int(0.95 * n))],
            'rms': math.sqrt(sum(x * x for x in xs) / n), 'n': n}


def _wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


class Intervals:
    """Sorted list of (start_us, end_us) for fast 'is t inside' checks."""
    def __init__(self, spans):
        self.spans = sorted(spans)
        self.i = 0

    def contains(self, t):
        for a, b in self.spans:
            if a <= t <= b:
                return True
            if t < a:
                return False
        return False

    def total_s(self):
        return sum(b - a for a, b in self.spans) / 1e6


def read_log(path, progress=None):
    """One pass over the log, keeping only the message types we use."""
    from pymavlink import mavutil
    m = mavutil.mavlink_connection(path, robust_parsing=True)
    size = os.path.getsize(path) or 1
    d = {t: [] for t in TYPES}
    params = {}
    batch = {}                      # N -> {'hdr': ISBH, 'rows': {seqno: (x, y, z)}}
    last_p = 0.0
    while True:
        msg = m.recv_match(type=TYPES)
        if msg is None:
            break
        t = msg.get_type()
        if t == 'PARM':
            params[msg.Name] = msg.Value
            continue
        if t == 'ISBH':
            if msg.instance == 0:
                batch[msg.N] = {'hdr': msg, 'rows': {}}
            continue
        if t == 'ISBD':
            b = batch.get(msg.N)
            if b is not None:
                b['rows'][msg.seqno] = (msg.x, msg.y, msg.z)
            continue
        d[t].append(msg)
        if progress and len(d[t]) % 2000 == 0:
            p = min(0.99, m.offset / size) if hasattr(m, 'offset') else 0
            if p - last_p > 0.05:
                last_p = p
                progress(p)
    return d, params, batch


def flight_spans(d):
    """Armed spans from EV; flying spans = armed and (throttle out and above 0.5 m)."""
    armed, start = [], None
    for e in d['EV']:
        if e.Id == EV_ARMED and start is None:
            start = e.TimeUS
        elif e.Id == EV_DISARMED and start is not None:
            armed.append((start, e.TimeUS))
            start = None
    end_us = max((m.TimeUS for k in ('CTUN', 'ATT', 'BAT') for m in d[k][-1:]), default=0)
    if start is not None:
        armed.append((start, end_us))
    if not armed and d['CTUN']:                 # log started armed / no EV: fall back to throttle
        on = [c.TimeUS for c in d['CTUN'] if (c.ThO or 0) > 0.05]
        if on:
            armed = [(on[0], on[-1])]
    arm_iv = Intervals(armed)
    flying, fs = [], None
    for c in d['CTUN']:
        up = arm_iv.contains(c.TimeUS) and (c.ThO or 0) > 0.05 and (c.Alt or 0) > 0.5
        if up and fs is None:
            fs = c.TimeUS
        elif not up and fs is not None:
            if c.TimeUS - fs > 1e6:
                flying.append((fs, c.TimeUS))
            fs = None
    if fs is not None and d['CTUN'] and d['CTUN'][-1].TimeUS - fs > 1e6:
        flying.append((fs, d['CTUN'][-1].TimeUS))
    return Intervals(armed), Intervals(flying)


def hover_spans(d, flying):
    """Steady hover: flying, < 0.3 m/s vertical, < 0.5 m/s horizontal, < 5 deg commanded tilt."""
    vd = {m.TimeUS // 200000: m.VD for m in d['PSCD']}
    vn = {m.TimeUS // 200000: m.VN for m in d['PSCN']}
    ve = {m.TimeUS // 200000: m.VE for m in d['PSCE']}
    tilt = {m.TimeUS // 200000: max(abs(m.DesRoll), abs(m.DesPitch)) for m in d['ATT']}
    spans, s, last = [], None, None
    for c in d['CTUN']:
        k = c.TimeUS // 200000
        v_down = vd.get(k)
        if v_down is None:
            cr = getattr(c, 'CRt', 0) or 0
            v_down = -(cr / 100.0 if abs(cr) > 20 else cr)
        h = math.hypot(vn.get(k, 0) or 0, ve.get(k, 0) or 0)
        ok = flying.contains(c.TimeUS) and abs(v_down) < 0.3 and h < 0.5 and tilt.get(k, 0) < 5
        if ok and s is None:
            s = c.TimeUS
        elif not ok and s is not None:
            if c.TimeUS - s > 2e6:
                spans.append((s, c.TimeUS))
            s = None
        last = c.TimeUS
    if s is not None and last and last - s > 2e6:
        spans.append((s, last))
    return Intervals(spans)


def gyro_spectrum(batch, flying, hover):
    """Average gyro spectrum (roll+pitch) from the batch sampler during flight. Hover-only if
    there is enough hover data, since the notch reference frequency is taken at hover."""
    try:
        import numpy as np
    except ImportError:
        return {'available': False, 'why': 'numpy is not installed on the Jetson'}
    if not batch:
        return {'available': False, 'why': 'No high-rate IMU data in the log — set INS_LOG_BAT_MASK = 1 before the next flight'}
    kinds = {}
    for n, b in batch.items():
        kinds.setdefault(b['hdr'].type, []).append(n)
    kind = 1 if 1 in kinds else 0
    blocks = {'hover': [], 'flying': []}
    rate = None
    for n in kinds[kind]:
        b = batch[n]
        h = b['hdr']
        t = h.SampleUS
        if not flying.contains(t):
            continue
        rows = [b['rows'][k] for k in sorted(b['rows'])]
        if not rows:
            continue
        mul = float(h.mul or 1)
        x = np.concatenate([np.asarray(r[0], dtype=float) for r in rows]) / mul
        y = np.concatenate([np.asarray(r[1], dtype=float) for r in rows]) / mul
        if len(x) < 256:
            continue
        rate = float(h.smp_rate)
        blocks['flying'].append((x, y))
        if hover.contains(t):
            blocks['hover'].append((x, y))
    use = 'hover' if len(blocks['hover']) >= 5 else 'flying'
    if not blocks[use] or not rate:
        return {'available': False, 'why': 'High-rate IMU data was logged but none of it while flying'}
    nfft = min(len(b[0]) for b in blocks[use])
    nfft = 1 << int(math.log2(nfft))
    win = np.hanning(nfft)
    acc = np.zeros(nfft // 2 + 1)
    for x, y in blocks[use]:
        for sig in (x[:nfft], y[:nfft]):
            sig = sig - sig.mean()
            acc += np.abs(np.fft.rfft(sig * win)) / nfft
    acc /= 2 * len(blocks[use])
    freqs = np.fft.rfftfreq(nfft, 1.0 / rate)
    band = (freqs >= 15) & (freqs <= min(400.0, 0.45 * rate))
    if not band.any():
        return {'available': False, 'why': 'Sample rate too low for a spectrum'}
    fb, ab = freqs[band], acc[band]
    floor = float(np.median(ab)) or 1e-12
    peaks = []
    for i in range(1, len(ab) - 1):
        if ab[i] >= ab[i - 1] and ab[i] >= ab[i + 1] and ab[i] > 4 * floor:
            peaks.append((float(ab[i]), float(fb[i])))
    peaks.sort(reverse=True)
    picked = []
    for amp, f in peaks:
        if all(abs(f - p['hz']) > 8 for p in picked):
            picked.append({'hz': round(f, 1), 'amp': float(f'{amp:.3g}'), 'x_floor': round(amp / floor, 1)})
        if len(picked) == 4:
            break
    # 32-point summary of the spectrum for the reviewer (log-spaced bins)
    edges = np.geomspace(15, float(fb[-1]), 33)
    shape = []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = (fb >= a) & (fb < b)
        if sel.any():
            shape.append([round(float((a * b) ** 0.5), 1), float(f'{float(ab[sel].max()):.3g}')])
    # ~300-point curve for the charts (max per bin, so peaks survive the downsampling)
    nb = 300
    step = max(1, len(fb) // nb)
    curve = [[round(float(fb[i:i + step].mean()), 1), float(f'{float(ab[i:i + step].max()):.4g}')]
             for i in range(0, len(fb), step)]
    return {'available': True, 'sensor': 'gyro' if kind == 1 else 'accel', 'window': use, 'blocks': len(blocks[use]),
            'sample_rate_hz': rate, 'resolution_hz': round(rate / nfft, 2), 'noise_floor': float(f'{floor:.3g}'),
            'peaks': picked, 'shape': shape, '_curve': curve}


def analyse(path, progress=None):
    t0 = time.time()
    d, params, batch = read_log(path, progress)
    armed, flying = flight_spans(d)
    hover = hover_spans(d, flying)
    P = lambda n, default=None: params.get(n, default)
    r = {'version': REPORT_VERSION, 'file': os.path.basename(path), 'size_mb': round(os.path.getsize(path) / 1e6, 1),
         'airframe': AIRFRAME, 'params': {k: _r(params[k], 4) for k in REPORT_PARAMS if k in params}}
    fw = next((m.Message for m in d['MSG'] if 'ArduCopter' in m.Message or 'ArduPilot' in m.Message), None)
    r['firmware'] = fw

    # ── flight ─────────────────────────────────────────────────────────────
    alts = [c.Alt for c in d['CTUN'] if flying.contains(c.TimeUS)]
    dist = [math.hypot(n.PN, e.PE) for n, e in zip(d['PSCN'], d['PSCE']) if flying.contains(n.TimeUS)]
    t_first = d['CTUN'][0].TimeUS if d['CTUN'] else 0
    rel = lambda us: _r((us - t_first) / 1e6, 1)
    modes = []
    for m in d['MODE']:
        name = COPTER_MODES.get(m.ModeNum, str(m.ModeNum))
        if not modes or modes[-1]['mode'] != name:
            modes.append({'t': rel(m.TimeUS), 'mode': name})
    r['flight'] = {'armed_s': _r(armed.total_s(), 1), 'flying_s': _r(flying.total_s(), 1), 'hover_s': _r(hover.total_s(), 1),
                   'max_alt_m': _r(max(alts), 1) if alts else None, 'max_dist_from_home_m': _r(max(dist), 1) if dist else None,
                   'takeoffs': len(flying.spans), 'modes': modes[:40]}

    # ── vibration ──────────────────────────────────────────────────────────
    vib = {}
    for imu in sorted({v.IMU for v in d['VIBE']}):
        rows = [v for v in d['VIBE'] if v.IMU == imu and flying.contains(v.TimeUS)]
        if not rows:
            continue
        clip_all = [v.Clip for v in d['VIBE'] if v.IMU == imu]
        clip_fly = [v.Clip for v in rows]
        vib[str(imu)] = {ax: {'mean': _r(_stats([getattr(v, 'Vibe' + ax) for v in rows])['mean'], 1),
                              'max': _r(_stats([getattr(v, 'Vibe' + ax) for v in rows])['max'], 1)} for ax in 'XYZ'}
        vib[str(imu)]['clip_in_flight'] = int(max(clip_fly) - min(clip_fly)) if clip_fly else 0
        vib[str(imu)]['clip_total'] = int(max(clip_all)) if clip_all else 0
    r['vibration'] = vib

    # ── throttle / motors ──────────────────────────────────────────────────
    tho_h = [c.ThO for c in d['CTUN'] if hover.contains(c.TimeUS)]
    tho_f = [c.ThO for c in d['CTUN'] if flying.contains(c.TimeUS)]
    thh = [c.ThH for c in d['CTUN'] if flying.contains(c.TimeUS) and getattr(c, 'ThH', None)]
    r['throttle'] = {'hover_out_mean': _r(_stats(tho_h)['mean'], 3) if tho_h else None,
                     'flying_out_mean': _r(_stats(tho_f)['mean'], 3) if tho_f else None,
                     'flying_out_max': _r(_stats(tho_f)['max'], 3) if tho_f else None,
                     'learned_hover': _r(thh[-1], 3) if thh else None, 'param_hover': _r(P('MOT_THST_HOVER'), 3)}
    pmin, pmax = P('MOT_PWM_MIN', 1000) or 1000, P('MOT_PWM_MAX', 2000) or 2000
    if pmax <= pmin:
        pmin, pmax = 1000, 2000
    span = pmax - pmin
    motors = {}
    rc_h = [o for o in d['RCOU'] if hover.contains(o.TimeUS)] or [o for o in d['RCOU'] if flying.contains(o.TimeUS)]
    rc_f = [o for o in d['RCOU'] if flying.contains(o.TimeUS)]
    if rc_h:
        for n in (1, 2, 3, 4):
            vals = [getattr(o, f'C{n}') for o in rc_h]
            motors[n] = (sum(vals) / len(vals) - pmin) / span * 100
        sat = sum(1 for o in rc_f if max(getattr(o, f'C{n}') for n in (1, 2, 3, 4)) >= pmax - 0.02 * span)
        low = sum(1 for o in rc_f if min(getattr(o, f'C{n}') for n in (1, 2, 3, 4)) <= pmin + 0.02 * span)
        avg = sum(motors.values()) / 4
        diag = (motors[1] + motors[2]) / 2 - (motors[3] + motors[4]) / 2      # CCW pair minus CW pair
        fwd = (motors[1] + motors[3]) / 2 - (motors[2] + motors[4]) / 2       # front minus rear
        right = (motors[1] + motors[4]) / 2 - (motors[2] + motors[3]) / 2     # right minus left
        r['motors'] = {'layout': 'quad X (1 front-right CCW, 2 rear-left CCW, 3 front-left CW, 4 rear-right CW)',
                       'window': 'hover' if hover.spans else 'flying',
                       'out_pct': {str(n): _r(v, 1) for n, v in motors.items()},
                       'spread_pct': _r(max(motors.values()) - min(motors.values()), 1),
                       'hardest': f"M{max(motors, key=motors.get)} {QUAD_X[max(motors, key=motors.get)][0]}",
                       'ccw_minus_cw_pct': _r(diag, 1), 'front_minus_rear_pct': _r(fwd, 1), 'right_minus_left_pct': _r(right, 1),
                       'max_out_pct': _r(max((max(getattr(o, f'C{n}') for n in (1, 2, 3, 4)) - pmin) / span * 100 for o in rc_f), 1) if rc_f else None,
                       'saturated_pct_of_flight': _r(100 * sat / max(1, len(rc_f)), 1),
                       'at_min_pct_of_flight': _r(100 * low / max(1, len(rc_f)), 1), 'average_pct': _r(avg, 1)}

    # ── tracking ───────────────────────────────────────────────────────────
    att = [a for a in d['ATT'] if flying.contains(a.TimeUS)]
    if att:
        er = _stats([a.Roll - a.DesRoll for a in att])
        ep = _stats([a.Pitch - a.DesPitch for a in att])
        ey = _stats([_wrap180(a.Yaw - a.DesYaw) for a in att])
        r['attitude'] = {'roll_err_rms_deg': _r(er['rms']), 'roll_err_max_deg': _r(max(abs(er['max']), abs(er['min']))),
                         'pitch_err_rms_deg': _r(ep['rms']), 'pitch_err_max_deg': _r(max(abs(ep['max']), abs(ep['min']))),
                         'yaw_err_rms_deg': _r(ey['rms']), 'yaw_err_max_deg': _r(max(abs(ey['max']), abs(ey['min']))),
                         'max_roll_deg': _r(max(abs(a.Roll) for a in att), 1), 'max_pitch_deg': _r(max(abs(a.Pitch) for a in att), 1)}
    ct = [c for c in d['CTUN'] if flying.contains(c.TimeUS)]
    if ct:
        ea = _stats([c.Alt - c.DAlt for c in ct])
        r['altitude'] = {'err_rms_m': _r(ea['rms']), 'err_max_m': _r(max(abs(ea['max']), abs(ea['min'])))}
    pn = [(n, e) for n, e in zip(d['PSCN'], d['PSCE']) if flying.contains(n.TimeUS)]
    if pn:
        eh = _stats([math.hypot(n.PN - n.TPN, e.PE - e.TPE) for n, e in pn])
        r['position'] = {'err_rms_m': _r(eh['rms']), 'err_max_m': _r(eh['max']), 'err_p95_m': _r(eh['p95'])}

    # ── battery ────────────────────────────────────────────────────────────
    bat = [b for b in d['BAT'] if getattr(b, 'Inst', 0) == 0]
    if bat:
        fly_b = [b for b in bat if flying.contains(b.TimeUS)]
        pre = [b for b in bat if armed.spans and b.TimeUS <= armed.spans[0][0]] or bat[:5]
        rest = pre[-1].Volt if pre else None
        vmin = min(b.Volt for b in fly_b) if fly_b else None
        cells = round(rest / 4.0) if rest else None
        cells = max(1, min(14, cells)) if cells else None
        hov_i = [b.Curr for b in bat if hover.contains(b.TimeUS)]
        used = (bat[-1].CurrTot or 0) - (pre[-1].CurrTot or 0) if pre else bat[-1].CurrTot
        cap = P('BATT_CAPACITY')
        r['battery'] = {'rest_v': _r(rest), 'min_v_in_flight': _r(vmin), 'cells_guess': cells,
                        'min_cell_v': _r(vmin / cells, 3) if vmin and cells else None,
                        'sag_v': _r(rest - vmin) if rest and vmin else None,
                        'max_current_a': _r(max(b.Curr for b in fly_b), 1) if fly_b else None,
                        'hover_current_a': _r(sum(hov_i) / len(hov_i), 1) if hov_i else None,
                        'used_mah': _r(used, 0), 'capacity_mah': cap,
                        'hover_minutes_to_20pct': _r((cap * 0.8 - (used or 0)) / 1000 / (sum(hov_i) / len(hov_i)) * 60, 1)
                        if cap and hov_i and sum(hov_i) > 0 else None}

    # ── GPS ────────────────────────────────────────────────────────────────
    g = [x for x in d['GPS'] if getattr(x, 'I', 0) == 0 and flying.contains(x.TimeUS)]
    if g:
        r['gps'] = {'min_sats': min(x.NSats for x in g), 'max_hdop': _r(max(x.HDop for x in g)),
                    'worst_fix': min(x.Status for x in g), 'fix_names': '3=3D 4=DGPS 5=RTK float 6=RTK fixed'}

    # ── events ─────────────────────────────────────────────────────────────
    # ECode 0 means "that error has cleared" — not a problem in itself
    errs = [{'t': rel(e.TimeUS), 'subsys': ERR_SUBSYS.get(e.Subsys, str(e.Subsys)), 'code': e.ECode} for e in d['ERR'] if e.ECode]
    keys = ('fail', 'error', 'ekf', 'vibration', 'crash', 'prearm', 'fence', 'battery', 'potential thrust loss',
            'yaw imbalance', 'lane', 'compass', 'gps glitch', 'clipping', 'land')
    msgs = [{'t': rel(m.TimeUS), 'text': m.Message} for m in d['MSG']
            if any(k in m.Message.lower() for k in keys) and not any(b in m.Message.lower() for b in BENIGN_MSG)]
    r['events'] = {'errors': errs[:50], 'messages': msgs[:80]}

    r['spectrum'] = gyro_spectrum(batch, flying, hover)
    curve = r['spectrum'].pop('_curve', None)
    series = export_series(d, armed, flying, r)
    if series is not None:
        series['spectrum'] = curve
        series['peaks'] = r['spectrum'].get('peaks')
    r['_series'] = series
    findings, proposals = rules(r)
    r['findings'], r['proposals'] = findings, proposals
    r['verdict'] = ('no_go' if any(f['level'] == 'critical' for f in findings)
                    else 'caution' if any(f['level'] == 'warning' for f in findings) else 'go')
    r['analysed_s'] = round(time.time() - t0, 1)
    if progress:
        progress(1.0)
    return r


SERIES_DT = 0.5           # seconds per point in series.json


def export_series(d, armed, flying, report):
    """Down-sampled time series for the Logs page charts, t = seconds since the first take-off
    (so flights line up when compared). Mean per bucket; max for vibration."""
    if flying.spans:
        t0 = flying.spans[0][0]
        start, end = t0 - 15e6, flying.spans[-1][1] + 15e6
    elif armed.spans:
        t0 = armed.spans[0][0]
        start, end = t0 - 5e6, armed.spans[-1][1] + 5e6
    else:
        return None
    nb = int((end - start) / 1e6 / SERIES_DT) + 1
    if nb > 7200:                                   # very long log: coarser buckets
        return export_series_coarse(d, start, end, t0, report)
    return _bucket(d, start, end, t0, SERIES_DT, report)


def export_series_coarse(d, start, end, t0, report):
    return _bucket(d, start, end, t0, (end - start) / 1e6 / 7200, report)


def _bucket(d, start, end, t0, dt, report):
    nb = int((end - start) / 1e6 / dt) + 1
    acc = {}

    def put(key, t, v, how='mean'):
        if v is None or not (start <= t <= end):
            return
        try:
            v = float(v)
        except (TypeError, ValueError):
            return
        if not math.isfinite(v):
            return
        i = int((t - start) / 1e6 / dt)
        a = acc.setdefault(key, [None] * nb)
        cur = a[i]
        if how == 'max':
            a[i] = v if cur is None else max(cur, v)
        else:
            a[i] = [v, 1] if cur is None else [cur[0] + v, cur[1] + 1]

    params = report.get('params', {})
    pmin, pmax = params.get('MOT_PWM_MIN') or 1000, params.get('MOT_PWM_MAX') or 2000
    if pmax <= pmin:
        pmin, pmax = 1000, 2000
    for c in d['CTUN']:
        put('alt', c.TimeUS, c.Alt)
        put('alt_target', c.TimeUS, c.DAlt)
        put('throttle', c.TimeUS, (c.ThO or 0) * 100)
    for a in d['ATT']:
        put('roll', a.TimeUS, a.Roll)
        put('roll_des', a.TimeUS, a.DesRoll)
        put('pitch', a.TimeUS, a.Pitch)
        put('pitch_des', a.TimeUS, a.DesPitch)
        put('yaw_err', a.TimeUS, abs(_wrap180(a.Yaw - a.DesYaw)), 'max')
    for o in d['RCOU']:
        for n in (1, 2, 3, 4):
            put(f'm{n}', o.TimeUS, (getattr(o, f'C{n}') - pmin) / (pmax - pmin) * 100)
    for v in d['VIBE']:
        if v.IMU == 0:
            put('vibe_x', v.TimeUS, v.VibeX, 'max')
            put('vibe_y', v.TimeUS, v.VibeY, 'max')
            put('vibe_z', v.TimeUS, v.VibeZ, 'max')
    for b in d['BAT']:
        if getattr(b, 'Inst', 0) == 0:
            put('volt', b.TimeUS, b.Volt)
            put('curr', b.TimeUS, b.Curr)
    for n, e in zip(d['PSCN'], d['PSCE']):
        put('speed', n.TimeUS, math.hypot(n.VN, e.VE))
        put('pos_err', n.TimeUS, math.hypot(n.PN - n.TPN, e.PE - e.TPE), 'max')
    for g in d['GPS']:
        if getattr(g, 'I', 0) == 0:
            put('sats', g.TimeUS, g.NSats)
    out = {}
    for k, a in acc.items():
        vals = []
        for x in a:
            if x is None:
                vals.append(None)
            elif isinstance(x, list):
                vals.append(round(x[0] / x[1], 3))
            else:
                vals.append(round(x, 3))
        while vals and vals[-1] is None:
            vals.pop()
        out[k] = vals
    rel = lambda us: round((us - t0) / 1e6, 1)
    modes = []
    for m in d['MODE']:
        name = COPTER_MODES.get(m.ModeNum, str(m.ModeNum))
        if (not modes or modes[-1][1] != name) and start <= m.TimeUS <= end:
            modes.append([rel(m.TimeUS), name])
    return {'version': 1, 't0_offset_s': round((start - t0) / 1e6, 2), 'dt': round(dt, 3), 'n': nb, 'series': out,
            'modes': modes, 'units': {'alt': 'm', 'alt_target': 'm', 'throttle': '%', 'roll': '°', 'roll_des': '°',
                                      'pitch': '°', 'pitch_des': '°', 'yaw_err': '°', 'm1': '%', 'm2': '%', 'm3': '%',
                                      'm4': '%', 'vibe_x': 'm/s²', 'vibe_y': 'm/s²', 'vibe_z': 'm/s²', 'volt': 'V',
                                      'curr': 'A', 'speed': 'm/s', 'pos_err': 'm', 'sats': ''}}


def rules(r):
    """Plain thresholds from ArduPilot's tuning guidance. These run without any AI."""
    F, Pr = [], []
    add = lambda level, area, text: F.append({'level': level, 'area': area, 'text': text})
    prm = r.get('params', {})
    fl = r.get('flight', {})
    if not fl.get('flying_s'):
        add('info', 'flight', 'The aircraft did not fly in this log (ground run or bench test) — flight numbers are empty.')
    # vibration (m/s^2): < 15 good, 15-30 acceptable, > 30 problems; any clipping is bad
    for imu, v in (r.get('vibration') or {}).items():
        worst = max(v[a]['max'] or 0 for a in 'XYZ')
        if worst > 60:
            add('critical', 'vibration', f'IMU{imu} vibration peaked at {worst:.0f} m/s² (over 60 — position and altitude hold will fail). Check prop balance, motor mounts and FC damping before flying again.')
        elif worst > 30:
            add('warning', 'vibration', f'IMU{imu} vibration peaked at {worst:.0f} m/s² (keep under 30, ideally under 15).')
        if v.get('clip_in_flight', 0) > 0:
            add('critical' if v['clip_in_flight'] > 100 else 'warning', 'vibration',
                f'IMU{imu} accelerometer clipped {v["clip_in_flight"]} times in flight — the sensor hit its limit; vibration is too high.')
    th = r.get('throttle') or {}
    hov = th.get('hover_out_mean') or th.get('learned_hover')
    if hov:
        if hov > 0.65:
            add('critical', 'power', f'Hovers at {hov * 100:.0f}% throttle — too little reserve to manoeuvre or fight wind. Reduce weight or more thrust.')
        elif hov > 0.5:
            add('warning', 'power', f'Hovers at {hov * 100:.0f}% throttle — limited reserve; keep manoeuvres gentle.')
        elif hov < 0.15:
            add('warning', 'power', f'Hovers at {hov * 100:.0f}% throttle — very low; check MOT_PWM_MIN/MAX match the ESC range.')
    learned, param = th.get('learned_hover'), th.get('param_hover')
    if learned and param is not None and abs(learned - param) > 0.03 and 0.1 < learned < 0.75 \
            and (prm.get('MOT_HOVER_LEARN') or 0) != 2:
        Pr.append({'param': 'MOT_THST_HOVER', 'value': round(learned, 3), 'source': 'rule',
                   'reason': f'Learned hover throttle in this flight was {learned:.3f} (parameter is {param:.3f}). MOT_HOVER_LEARN is not 2, so it was not saved automatically.'})
    mo = r.get('motors') or {}
    if mo.get('saturated_pct_of_flight', 0) > 1:
        add('critical' if mo['saturated_pct_of_flight'] > 5 else 'warning', 'motors',
            f'At least one motor was at full output for {mo["saturated_pct_of_flight"]}% of the flight — the controller ran out of authority.')
    if mo.get('spread_pct', 0) > 15:
        add('warning', 'motors', f'Motor outputs at hover differ by {mo["spread_pct"]}% (hardest: {mo.get("hardest")}).')
    if abs(mo.get('ccw_minus_cw_pct') or 0) > 8:
        add('warning', 'motors', f'The CCW pair works {mo["ccw_minus_cw_pct"]:+.1f}% vs the CW pair — yaw imbalance: check motor mounts are level/untwisted and props are on the right motors.')
    if abs(mo.get('front_minus_rear_pct') or 0) > 8:
        add('warning', 'balance', f'Front motors work {mo["front_minus_rear_pct"]:+.1f}% vs rear — centre of gravity is off fore/aft; move the battery.')
    if abs(mo.get('right_minus_left_pct') or 0) > 8:
        add('warning', 'balance', f'Right motors work {mo["right_minus_left_pct"]:+.1f}% vs left — centre of gravity is off sideways.')
    at = r.get('attitude') or {}
    for ax in ('roll', 'pitch'):
        rms, mx = at.get(f'{ax}_err_rms_deg'), at.get(f'{ax}_err_max_deg')
        if rms is None:
            continue
        if rms > 3 or (mx or 0) > 15:
            add('critical', 'tuning', f'{ax.title()} tracking error {rms:.1f}° RMS (max {mx:.0f}°) — the attitude tune is not holding the aircraft. Do not go higher until tuned.')
        elif rms > 1.5 or (mx or 0) > 8:
            add('warning', 'tuning', f'{ax.title()} tracking error {rms:.1f}° RMS (max {mx:.0f}°) — tune can be improved (AutoTune recommended).')
    if (at.get('yaw_err_rms_deg') or 0) > 5:
        add('warning', 'tuning', f'Yaw tracking error {at["yaw_err_rms_deg"]:.1f}° RMS.')
    al = r.get('altitude') or {}
    if (al.get('err_max_m') or 0) > 1.5:
        add('warning', 'tuning', f'Altitude strayed up to {al["err_max_m"]:.1f} m from target (RMS {al["err_rms_m"]:.2f} m).')
    po = r.get('position') or {}
    if (po.get('err_max_m') or 0) > 3:
        add('warning', 'navigation', f'Position strayed up to {po["err_max_m"]:.1f} m from target (95% under {po["err_p95_m"]:.1f} m).')
    b = r.get('battery') or {}
    if b.get('min_cell_v') is not None:
        if b['min_cell_v'] < 3.4:
            add('critical', 'battery', f'Battery dropped to {b["min_cell_v"]:.2f} V per cell under load — too low.')
        elif b['min_cell_v'] < 3.55:
            add('warning', 'battery', f'Battery dropped to {b["min_cell_v"]:.2f} V per cell under load.')
    if b.get('sag_v') and b.get('cells_guess') and b['sag_v'] / b['cells_guess'] > 0.35:
        add('warning', 'battery', f'Voltage sagged {b["sag_v"]:.1f} V under load — pack may be weak or cold.')
    g = r.get('gps') or {}
    if g.get('min_sats') is not None and g['min_sats'] < 10:
        add('warning', 'gps', f'GPS dropped to {g["min_sats"]} satellites in flight.')
    if (g.get('max_hdop') or 0) > 1.5:
        add('warning', 'gps', f'GPS HDOP reached {g["max_hdop"]} in flight.')
    for e in (r.get('events') or {}).get('errors', []):
        add('warning', 'errors', f'ArduPilot error at T+{e["t"]} s: {e["subsys"]} (code {e["code"]}).')
    sp = r.get('spectrum') or {}
    if not sp.get('available'):
        Pr.append({'param': 'INS_LOG_BAT_MASK', 'value': 1, 'source': 'rule',
                   'reason': 'Logs high-rate gyro data so the next log can set the vibration notch filter. ' + (sp.get('why') or '')})
    elif sp.get('peaks') and (prm.get('INS_HNTCH_ENABLE') or 0) == 0:
        f0 = sp['peaks'][0]['hz']
        ref = th.get('hover_out_mean') or th.get('learned_hover')
        if 20 <= f0 <= 300 and ref:
            add('info', 'filtering', f'Main vibration peak at {f0:.0f} Hz ({sp["peaks"][0]["x_floor"]}× the noise floor) — a throttle-based harmonic notch will remove it.')
            for p, v, why in (('INS_HNTCH_ENABLE', 1, 'Turns on the harmonic notch filter (reboot the Pixhawk after).'),
                              ('INS_HNTCH_MODE', 1, 'Notch follows throttle (works without ESC telemetry).'),
                              ('INS_HNTCH_FREQ', round(f0), f'Vibration peak at hover was {f0:.0f} Hz.'),
                              ('INS_HNTCH_BW', round(f0 / 2), 'Bandwidth = half the centre frequency (ArduPilot guidance).'),
                              ('INS_HNTCH_REF', round(ref, 3), 'Hover throttle in this flight, so the notch tracks the motors.')):
                Pr.append({'param': p, 'value': v, 'source': 'rule', 'reason': why})
    return F, Pr


def analyse_folder(folder, progress=None):
    """Analyse <folder>/pixhawk.bin -> <folder>/analysis/report.json. Returns the report."""
    src = folder if folder.lower().endswith('.bin') else os.path.join(folder, 'pixhawk.bin')
    if not os.path.isfile(src):
        raise FileNotFoundError('No pixhawk.bin in this mission yet — download it from the Pixhawk first')
    report = analyse(src, progress)
    out_dir = os.path.join(os.path.dirname(src), 'analysis')
    os.makedirs(out_dir, exist_ok=True)
    series = report.pop('_series', None)
    if series is not None:
        with open(os.path.join(out_dir, 'series.json.tmp'), 'w') as f:
            json.dump(series, f, separators=(',', ':'))
        os.replace(os.path.join(out_dir, 'series.json.tmp'), os.path.join(out_dir, 'series.json'))
    report['has_series'] = series is not None
    report['generated_at'] = time.time()
    tmp = os.path.join(out_dir, 'report.json.tmp')
    with open(tmp, 'w') as f:
        json.dump(report, f, indent=1)
    os.replace(tmp, os.path.join(out_dir, 'report.json'))
    return report


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('usage: log_analysis.py <mission folder | file.bin>')
    rep = analyse_folder(sys.argv[1], lambda p: print(f'\r{p * 100:.0f}%', end='', file=sys.stderr))
    print(file=sys.stderr)
    print(json.dumps({k: rep[k] for k in ('verdict', 'findings', 'proposals', 'flight', 'throttle', 'motors', 'spectrum')}, indent=1))
