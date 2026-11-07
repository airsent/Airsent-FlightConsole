"""Compare live Pixhawk parameters with the approved set in golden_params.json.

Rules per parameter: {"value": x} (exact, small tolerance), {"min": a, "max": b},
or {"in": [a, b, ...]}. A parameter must have been read from the aircraft
within FRESH seconds to count; missing or stale values are never treated as OK.
"""
import json
import math
import threading
import time
from pathlib import Path

FRESH = 120.0


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

    def names(self):
        return list(self.rules)

    def observe(self, msg):
        name = _name(msg)
        if name not in self.rules:
            return
        value = float(msg.param_value)
        if not math.isfinite(value):
            return
        with self.lock:
            self.values[name] = (value, self.clock())

    def request(self, mav, only_stale=True):
        now = self.clock()
        for name in self.rules:
            seen = self.values.get(name)
            if only_stale and seen and now - seen[1] < FRESH / 2:
                continue
            mav.mav.param_request_read_send(mav.target_system, mav.target_component, name.encode(), -1)

    def reset(self):
        with self.lock:
            self.values.clear()

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
                seen = self.values.get(name)
                if not seen or now - seen[1] > FRESH:
                    missing.append(name)
                    continue
                values[name] = seen[0]
                if not self.matches(seen[0], rule):
                    diffs.append(f"{name}={seen[0]:g} (approved {self.describe(rule)})")
        if diffs:
            state = 'diff'
        elif missing:
            state = 'loading'
        else:
            state = 'ok'
        return {'state': state, 'count': len(self.rules), 'diffs': diffs, 'missing': missing, 'values': values}
