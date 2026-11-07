"""Read and write ArduPilot parameters with confirmation.

The MAVLink reader thread passes every PARAM_VALUE to observe(). read() asks for
parameters by name and waits for the answers; write() sends PARAM_SET and only
reports success once the Pixhawk sends the parameter back with the new value.
A name the Pixhawk never answers for is reported as missing (not on this vehicle).
"""
import math
import struct
import threading
import time

# MAV_PARAM_TYPE
UINT8, INT8, UINT16, INT16, UINT32, INT32, REAL32 = 1, 2, 3, 4, 5, 6, 9
INT_TYPES = {UINT8, INT8, UINT16, INT16, UINT32, INT32}
INT_RANGE = {UINT8: (0, 255), INT8: (-128, 127), UINT16: (0, 65535), INT16: (-32768, 32767),
             UINT32: (0, 4294967295), INT32: (-2147483648, 2147483647)}
NAME_OK = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')


def clean_name(name):
    n = str(name or '').strip().upper()
    if not n or len(n) > 16 or not set(n) <= NAME_OK:
        raise ValueError(f'Invalid parameter name: {name!r}')
    return n


def _decode_name(raw):
    if isinstance(raw, bytes):
        raw = raw.decode('ascii', 'ignore')
    return str(raw).split('\x00', 1)[0].strip()


def _f32(v):
    """Round to what a REAL32 can hold, so read-back comparison is exact."""
    return struct.unpack('<f', struct.pack('<f', float(v)))[0]


class ParamStore:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.values = {}            # name -> {'value', 'type', 'at'}
        self.count = None           # param_count reported by the Pixhawk
        self._cond = threading.Condition()
        self._write_lock = threading.Lock()

    def reset(self):
        with self._cond:
            self.values.clear()
            self.count = None

    def observe(self, msg):
        if msg.get_type() != 'PARAM_VALUE':
            return
        name = _decode_name(msg.param_id)
        if not name:
            return
        with self._cond:
            self.values[name] = {'value': float(msg.param_value), 'type': int(msg.param_type), 'at': self.clock()}
            self.count = getattr(msg, 'param_count', self.count)
            self._cond.notify_all()

    def get(self, name):
        with self._cond:
            v = self.values.get(name)
            return dict(v) if v else None

    def snapshot(self, names, max_age=None):
        """{name: value} for the names currently known (optionally only fresh ones)."""
        now = self.clock()
        out = {}
        with self._cond:
            for n in names:
                v = self.values.get(n)
                if v and (max_age is None or now - v['at'] <= max_age):
                    out[n] = int(round(v['value'])) if v['type'] in INT_TYPES else round(v['value'], 6)
        return out

    def _wait_for(self, names, since, timeout):
        end = self.clock() + timeout
        with self._cond:
            while True:
                pending = [n for n in names if not (n in self.values and self.values[n]['at'] >= since)]
                if not pending:
                    return []
                left = end - self.clock()
                if left <= 0:
                    return pending
                self._cond.wait(min(left, 0.2))

    @staticmethod
    def _out(v):
        return int(round(v['value'])) if v['type'] in INT_TYPES else v['value']

    def read(self, mav, names, max_age=None, timeout=3.0, retries=2):
        """Return ({name: value}, [missing names]). Values newer than max_age are reused."""
        names = list(dict.fromkeys(clean_name(n) for n in names))
        since = self.clock() - max_age if max_age else self.clock()
        want = names
        for attempt in range(retries + 1):
            ask = [n for n in want if not (self.get(n) and self.get(n)['at'] >= since)]
            for n in ask:
                mav.mav.param_request_read_send(mav.target_system, mav.target_component, n.encode('ascii'), -1)
            want = self._wait_for(want, since, timeout if attempt == 0 else timeout / 2)
            if not want:
                break
        found = {n: self._out(self.get(n)) for n in names if n not in want}
        return found, want

    def write(self, mav, name, value, timeout=2.0, retries=2):
        """Set one parameter; returns the value the Pixhawk confirmed. Raises on failure."""
        name = clean_name(name)
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(f'{name}: value must be a number')
        if not math.isfinite(value):
            raise ValueError(f'{name}: value must be finite')
        cur = self.get(name)
        if cur is None:
            found, missing = self.read(mav, [name], timeout=timeout)
            if missing:
                raise ValueError(f'{name} does not exist on this Pixhawk')
            cur = self.get(name)
        ptype = cur['type']
        if ptype in INT_TYPES:
            if abs(value - round(value)) > 1e-6:
                raise ValueError(f'{name} takes whole numbers only')
            lo, hi = INT_RANGE[ptype]
            if not lo <= value <= hi:
                raise ValueError(f'{name}: {value:g} does not fit this parameter ({lo}..{hi})')
            target = float(round(value))
        else:
            target = _f32(value)
        with self._write_lock:
            for attempt in range(retries + 1):
                since = self.clock()
                mav.mav.param_set_send(mav.target_system, mav.target_component, name.encode('ascii'), target, ptype)
                if not self._wait_for([name], since, timeout):
                    got = self.get(name)['value']
                    if abs(got - target) <= max(1e-6, abs(target) * 1e-6):
                        return self._out(self.get(name))
                    raise RuntimeError(f'{name}: Pixhawk kept {got:g} (asked for {target:g}) — value refused')
        raise RuntimeError(f'{name}: no confirmation from the Pixhawk')

    def write_many(self, mav, changes):
        """Write in order; stop at the first failure. Returns (done {name: value}, error or None)."""
        done = {}
        for name, value in changes.items():
            try:
                done[clean_name(name)] = self.write(mav, name, value)
            except (ValueError, RuntimeError) as e:
                return done, str(e)
        return done, None
