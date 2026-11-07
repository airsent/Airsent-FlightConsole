"""SITL end-to-end harness: real ArduCopter SITL <-> airsent_server.py --sim <-> this script.

The script talks to the server exactly like a console does (command + telemetry WebSockets),
and watches the simulated vehicle independently on a second SITL MAVLink port (no heartbeats
sent from there, so it never counts as a GCS for the Pixhawk failsafe).

    python3 tests/sitl/run.py              # starts SITL + server, runs every scenario
"""
import itertools
import json
import threading
import time

from pymavlink import mavutil
from websockets.sync.client import connect

COPTER_MODES = {0: 'STABILIZE', 2: 'ALT_HOLD', 3: 'AUTO', 4: 'GUIDED', 5: 'LOITER', 6: 'RTL', 9: 'LAND', 16: 'POSHOLD', 17: 'BRAKE', 21: 'SMART_RTL'}


class Console:
    """One operator console: command socket + telemetry socket."""
    _ids = itertools.count(1)

    def __init__(self, cmd_url='ws://127.0.0.1:8767', telem_url='ws://127.0.0.1:8765', console_id='sitl-console', operator='SITL'):
        self.console_id, self.operator = console_id, operator
        self.cmd = connect(cmd_url, max_size=50_000_000)
        self.replies = {}
        self.cond = threading.Condition()
        self.telem = {}
        self.mission_block = {}
        self.alive = True
        self._telem_url = telem_url
        threading.Thread(target=self._read_cmd, daemon=True).start()
        threading.Thread(target=self._read_telem, daemon=True).start()

    def _read_cmd(self):
        try:
            for raw in self.cmd:
                r = json.loads(raw)
                with self.cond:
                    self.replies[r.get('request_id')] = r
                    self.cond.notify_all()
        except Exception:
            pass

    def _read_telem(self):
        while self.alive:
            try:
                with connect(self._telem_url, max_size=50_000_000) as ws:
                    for raw in ws:
                        if not self.alive:
                            return
                        d = json.loads(raw)
                        self.telem = d
                        if 'asc' in d or 'mission' in d:
                            self.mission_block = d
            except Exception:
                time.sleep(0.5)

    def send(self, action, timeout=15, quiet_console=False, **data):
        rid = f'h{next(self._ids)}'
        msg = {'action': action, 'request_id': rid, **data}
        if not quiet_console:
            msg.setdefault('console_id', self.console_id)
            msg.setdefault('operator', self.operator)
        self.cmd.send(json.dumps(msg))
        end = time.time() + timeout
        with self.cond:
            while rid not in self.replies and time.time() < end:
                self.cond.wait(0.1)
            return self.replies.pop(rid, {'ok': False, 'msg': f'(no reply to {action} in {timeout}s)'})

    def ping(self):
        return self.send('mission_ping', page='harness')

    def close(self):
        self.alive = False
        try:
            self.cmd.close()
        except Exception:
            pass


class Vehicle:
    """Independent view of the SITL vehicle on its second port (tcp 5762)."""
    def __init__(self, url='tcp:127.0.0.1:5762'):
        self.m = mavutil.mavlink_connection(url, source_system=250, source_component=191)
        self.state = {'armed': False, 'mode': None, 'alt': 0.0, 'lat': None, 'lon': None, 'texts': [], 'params': {}}
        self.lock = threading.Lock()
        self.m.wait_heartbeat(timeout=30)
        # streams on this port too
        for mid, hz in ((33, 10), (0, 2), (74, 5), (24, 2)):
            self.m.mav.command_long_send(1, 1, 511, 0, mid, 1e6 / hz, 0, 0, 0, 0, 0)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                msg = self.m.recv_match(blocking=True, timeout=1)
            except Exception:
                return                     # SITL went away (power-cycle test makes a new Vehicle)
            if not msg:
                continue
            t = msg.get_type()
            with self.lock:
                if t == 'HEARTBEAT' and msg.get_srcSystem() == 1 and msg.get_srcComponent() == 1:
                    self.state['armed'] = bool(msg.base_mode & 128)
                    self.state['mode'] = COPTER_MODES.get(msg.custom_mode, str(msg.custom_mode))
                elif t == 'GLOBAL_POSITION_INT':
                    self.state['alt'] = msg.relative_alt / 1000.0
                    self.state['lat'], self.state['lon'] = msg.lat / 1e7, msg.lon / 1e7
                elif t == 'STATUSTEXT':
                    self.state['texts'].append((time.time(), msg.text))
                    self.state['texts'] = self.state['texts'][-200:]
                elif t == 'PARAM_VALUE':
                    self.state['params'][msg.param_id] = msg.param_value

    def get(self, k):
        with self.lock:
            return self.state[k]

    def wait(self, pred, timeout, step=0.2):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if pred(self.state):
                    return True
            time.sleep(step)
        return False

    def texts_since(self, t0):
        with self.lock:
            return [x for ts, x in self.state['texts'] if ts >= t0]

    def set_param(self, name, value, timeout=5):
        """Direct SITL tweak (failure injection: SIM_* params)."""
        with self.lock:
            self.state['params'].pop(name, None)
        self.m.mav.param_set_send(1, 1, name.encode(), float(value), mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
        return self.wait(lambda s: name in s['params'], timeout)

    def get_param(self, name, timeout=5):
        with self.lock:
            self.state['params'].pop(name, None)
        self.m.mav.param_request_read_send(1, 1, name.encode(), -1)
        if self.wait(lambda s: name in s['params'], timeout):
            return self.get('params')[name]
        return None
