"""Compare live Pixhawk parameters with the approved set in golden_params.json.

Rules per parameter: {"value": x} (exact, small tolerance), {"min": a, "max": b},
or {"in": [a, b, ...]}. A parameter must have been read from the aircraft
within FRESH seconds to count; missing or stale values are never treated as OK.

Newer ArduPilot renamed some parameters. A rule can list the new name(s) with their
own rule under "alt", e.g. "ARMING_CHECK": {"value": 1, "alt": {"ARMING_SKIPCHK": {"value": 0}}};
whichever the Pixhawk has is checked. A name the Pixhawk never answers for
(within ABSENT_AFTER seconds of asking) is reported as not on this Pixhawk.
"""
import json
import math
import threading
import time
from pathlib import Path

FRESH = 120.0
ABSENT_AFTER = 30.0


def _name(msg):
    raw = msg.param_id
    return (raw.decode('ascii', 'ignore') if isinstance(raw, bytes) else str(raw)).rstrip('\x00')


class GoldenParams:
    def __init__(self, path, clock=time.monotonic, extra=None):
        self.clock = clock
        self.lock = threading.Lock()
        self.values = {}
        self.rules = {}
        self.available = False
        try:
            data = json.loads(Path(path).read_text())
            self.rules = dict(data.get('params', {}))
            self.available = bool(self.rules)
        except (OSError, ValueError):
            self.available = False
        for name, rule in (extra or {}).items():
            self.rules[name] = rule
        self.asked_at = None

    def names(self):
        out = list(self.rules)
        for rule in self.rules.values():
            out += [n for n in (rule.get('alt') or {}) if n not in out]
        return out

    def observe(self, msg):
        name = _name(msg)
        if name not in self.names():
            return
        value = float(msg.param_value)
        if not math.isfinite(value):
            return
        with self.lock:
            self.values[name] = (value, self.clock())

    def request(self, mav, only_stale=True):
        now = self.clock()
        if self.asked_at is None:
            self.asked_at = now
        for name in self.names():
            seen = self.values.get(name)
            if only_stale and seen and now - seen[1] < FRESH / 2:
                continue
            mav.mav.param_request_read_send(mav.target_system, mav.target_component, name.encode(), -1)

    def reset(self):
        with self.lock:
            self.values.clear()
            self.asked_at = None

    @staticmethod
    def matches(value, rule):
        if 'value' in rule:
            target = float(rule['value'])
            return math.isclose(value, target, rel_tol=1e-4, abs_tol=float(rule.get('tol', 1e-3)))
        if 'in' in rule:
            return any(math.isclose(value, float(v), abs_tol=1e-3) for v in rule['in'])
        low, high = rule.get('min', -math.inf), rule.get('max', math.inf)
        return low - 1e-6 <= value <= high + 1e-6

    @staticmethod
    def describe(rule):
        if 'value' in rule:
            return f"{rule['value']:g}"
        if 'in' in rule:
            return ' or '.join(f"{v:g}" for v in rule['in'])
        return f"{rule.get('min', '-inf')}..{rule.get('max', 'inf')}"

    def snapshot(self):
        if not self.available:
            return {'state': 'unavailable', 'count': 0, 'diffs': [], 'missing': []}
        now = self.clock()
        diffs, missing, values = [], [], {}
        with self.lock:
            for name, rule in self.rules.items():
                options = [(name, rule)] + list((rule.get('alt') or {}).items())
                hit = next(((n, r, self.values[n][0]) for n, r in options
                            if n in self.values and now - self.values[n][1] <= FRESH), None)
                if hit is None:
                    if self.asked_at is not None and now - self.asked_at > ABSENT_AFTER:
                        diffs.append(f"{' / '.join(n for n, _ in options)} not on this Pixhawk (renamed in newer ArduPilot?)")
                    else:
                        missing.append(name)
                    continue
                n, r, v = hit
                values[n] = v
                if not self.matches(v, r):
                    diffs.append(f"{n}={v:g} (approved {self.describe(r)})")
        if diffs:
            state = 'diff'
        elif missing:
            state = 'loading'
        else:
            state = 'ok'
        return {'state': state, 'count': len(self.rules), 'diffs': diffs, 'missing': missing, 'values': values}
