import os
from pilot_settings import PilotSettings
from health_telemetry import HealthTelemetry, gpu_percent
from datalink_telemetry import start_monitor
from mission_engine import MissionEngine, default_config, disk_free_gb
from golden_params import GoldenParams
from vehicle_mission import VehicleMission, MissionBusy
from param_store import ParamStore
from collections import deque
import asyncio
import websockets
import sys

SIM_MODE = "--sim" in sys.argv
if not SIM_MODE:
    import depthai as dai
    import cv2
import base64
import json
import math
import time
import threading
import traceback
import subprocess
import socket
import numpy as np
from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as mavlink2
VPS_TELEM_URL = os.environ.get("VPS_TELEM_URL", "ws://146.190.50.189:9001")

try:
    from pupil_apriltags import Detector as _AprilDetector
    _APRILTAG_AVAILABLE = True
except Exception as _e:
    _APRILTAG_AVAILABLE = False
    print(f"[APRILTAG] pupil_apriltags not available ({_e}); detection disabled. "
          f"Run: pip install pupil-apriltags")

# ── Config ──────────────────────────────────────
PIXHAWK_PORT = os.environ.get('MAV_URL', 'tcp:127.0.0.1:5760') if SIM_MODE else '/dev/ttyTHS1'
PIXHAWK_BAUD = 115200 if SIM_MODE else 57600
CAM_PORT = int(os.environ.get("SIM_CAM_PORT", "8766")) if SIM_MODE else 8766
TELEM_PORT = int(os.environ.get("SIM_TELEM_PORT", "8765")) if SIM_MODE else 8765
CMD_PORT = int(os.environ.get("SIM_CMD_PORT", "8767")) if SIM_MODE else 8767
DOWNCAM_PORT = int(os.environ.get("SIM_DOWNCAM_PORT", "8768")) if SIM_MODE else 8768

DOWNCAM_W     = 1280          # 4:3 output to match the full-FOV sensor mode
DOWNCAM_H     = 960           # below (1640x1232). Was 1280x720 (16:9), but the
                             # IMX219 720p mode is a CENTER CROP that throws
                             # away the wide lens — see downcam_capture_thread.
STREAM_W      = 640           # low-res JPEG for the legacy WebSocket preview
STREAM_H      = 480           # 4:3 to match (was 360); preview path is idle.

# ── Stream framerates ───────────────────────────
# The Orin Nano has NO hardware video encoder (no NVENC) — every H.264 stream
# is encoded on the CPU. Lower framerate = less CPU spent encoding AND sharper
# frames at the same bitrate (more bits per frame). These are the main levers
# to trade motion smoothness for CPU headroom / picture quality on this board.
FRONTCAM_FPS  = 30           # pilot-view (OAK-D) RTMP framerate. Was 30.
RNG_GND_CLEAR_M = 0.30       # H-Flow height above ground when landed (matches RNGFND1_GNDCLEAR)
DOWNCAM_FPS   = 15           # down-cam capture + RTMP + WS preview. Was 30.
                             # NOTE: AprilTag precision-landing detection runs
                             # separately at 5 fps and is NOT affected by this.

APRILTAG_FAMILY = "tag36h11"
RTMP_HOST = os.environ.get("RTMP_HOST", "146.190.50.189")
RTMP_PORT = 1935

BLOB_PATH     = '/home/airsent/.cache/blobconverter/yolov8n_coco_640x352_openvino_2022.1_6shave.blob'
NN_WIDTH      = 640
NN_HEIGHT     = 352
CONF_THRESH   = 0.45

COCO_LABELS = [
    'person','bicycle','car','motorcycle','airplane','bus','train','truck','boat',
    'traffic light','fire hydrant','stop sign','parking meter','bench','bird','cat',
    'dog','horse','sheep','cow','elephant','bear','zebra','giraffe','backpack',
    'umbrella','handbag','tie','suitcase','frisbee','skis','snowboard','sports ball',
    'kite','baseball bat','baseball glove','skateboard','surfboard','tennis racket',
    'bottle','wine glass','cup','fork','knife','spoon','bowl','banana','apple',
    'sandwich','orange','broccoli','carrot','hot dog','pizza','donut','cake','chair',
    'couch','potted plant','bed','dining table','toilet','tv','laptop','mouse',
    'remote','keyboard','cell phone','microwave','oven','toaster','sink','refrigerator',
    'book','clock','vase','scissors','teddy bear','hair drier','toothbrush'
]

# ── Shared state ─────────────────────────────────
telem = {
    "roll":0,"pitch":0,"yaw":0,
    "alt":0,"spd":0,"vspd":0,
    "lat":0,"lon":0,"hdop":0,
    "sats":0,"fix_type":"NO FIX",
    "voltage":0,"current":0,"battery":0,
    "rssi":0,"armed":False,
    "flight_mode":"UNKNOWN",
    "ekf_healthy":True,
    "m1":0,"m2":0,"m3":0,"m4":0,
    "rangefinder":0,
    "of_quality":0,
    "of_flow_x":0.0,
    "of_flow_y":0.0,
}

# Signal measurements are independent. Unknown is never treated as 0% or healthy.
class RCSignalMonitor:
    """RC quality is usable only with fresh autopilot receiver-health evidence."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.percent = None
        self.sample_at = None
        self.health_at = None
        self.healthy = False
        self.has_channels = False

    def update(self, msg, now):
        kind = msg.get_type()
        if kind == 'SYS_STATUS':
            bit = 1 << 16  # MAV_SYS_STATUS_SENSOR_RC_RECEIVER
            self.health_at = now
            self.healthy = all(getattr(msg, field, 0) & bit for field in (
                'onboard_control_sensors_present', 'onboard_control_sensors_enabled',
                'onboard_control_sensors_health'))
            if not self.healthy:
                self.percent = None
                self.sample_at = None
        elif kind == 'RC_CHANNELS':
            self.has_channels = getattr(msg, 'chancount', 0) > 0
            value = getattr(msg, 'rssi', 255)
            # Unknown RSSI and absent channels invalidate the previous sample immediately.
            self.percent = round(value * 100 / 254) if 0 <= value <= 254 and getattr(msg, 'chancount', 0) > 0 else None
            self.sample_at = now

    def snapshot(self, now, pixhawk_connected):
        age = now - self.sample_at if self.sample_at is not None else None
        health_age = now - self.health_at if self.health_at is not None else None
        input_present = bool(pixhawk_connected and self.healthy and health_age is not None
                         and 0 <= health_age < 3 and age is not None and 0 <= age < 3
                         and self.has_channels)
        connected = input_present and self.percent is not None and self.percent > 0
        return {'percent': self.percent if input_present else None, 'connected': connected,
                'input_present': input_present,
                'age': age, 'health_age': health_age, 'source': 'RC_CHANNELS'}


rc_monitor = RCSignalMonitor()
rc_lock = threading.Lock()
cellular_signal = {"percent": None, "last_seen": 0.0, "technology": None,
                   "connected": False, "source": "ModemManager"}
cellular_lock = threading.Lock()
last_mav_heartbeat = 0.0


def signal_percent(value):
    try:
        if isinstance(value, bool):
            return None
        number = float(value)
        return round(number) if math.isfinite(number) and 0 <= number <= 100 else None
    except (TypeError, ValueError):
        return None


def read_network_link(run=subprocess.run):
    """Inspect the interface routing to the VPS, not an arbitrary connected adapter."""
    result = {'connected': False, 'type': None, 'interface': None,
              'ssid': None, 'percent': None, 'technology': None, 'source': 'NetworkManager'}
    def command(args):
        return run(args, capture_output=True, text=True, timeout=3, check=True).stdout.strip()
    try:
        import urllib.parse
        host = urllib.parse.urlparse(VPS_TELEM_URL).hostname
        address = socket.gethostbyname(host)
        routes = json.loads(command(['ip', '-j', 'route', 'get', address]))
        interface = routes[0].get('dev') if routes else None
        if not interface:
            return result
        result['interface'] = interface
        nm_device = interface
        try:
            kind = command(['nmcli', '-g', 'GENERAL.TYPE', 'device', 'show', nm_device])
        except subprocess.CalledProcessError:
            # Modems may be named cdc-wdm0 in NM while their IP route uses wwan0.
            devices = command(['nmcli', '-t', '-f', 'DEVICE,TYPE', 'device', 'status'])
            kind = None
            for row in devices.splitlines():
                candidate, _, candidate_kind = row.partition(':')
                if candidate_kind not in ('gsm', 'cdma', 'wwan'):
                    continue
                ip_interface = command(['nmcli', '-g', 'GENERAL.IP-IFACE', 'device', 'show', candidate])
                if ip_interface == interface:
                    nm_device, kind = candidate, candidate_kind
                    break
            if kind is None:
                # Some USB modems manage their own Ethernet bearer outside NM.
                listing = json.loads(command(['mmcli', '-L', '--output-json']))
                for modem_path in listing.get('modem-list', []):
                    generic = json.loads(command(['mmcli', '-m', modem_path, '--output-json'])).get('modem', {}).get('generic', {})
                    ports = generic.get('ports', [])
                    if isinstance(ports, str):
                        ports = [ports]
                    if any(str(port).split()[0] == interface for port in ports):
                        kind = 'modem-ethernet'
                        break
                if kind is None:
                    return result
        state = '100' if kind == 'modem-ethernet' else command(['nmcli', '-g', 'GENERAL.STATE', 'device', 'show', nm_device])
        if not state.startswith('100'):
            return result
        if kind in ('wifi', '802-11-wireless'):
            result.update(connected=True, type='wifi')
            profile = command(['nmcli', '-g', 'GENERAL.CONNECTION', 'device', 'show', interface])
            result['ssid'] = command(['nmcli', '-g', '802-11-wireless.ssid', 'connection', 'show', profile]) or None
            rows = command(['nmcli', '-t', '-f', 'IN-USE,SIGNAL', 'device', 'wifi', 'list', 'ifname', interface, '--rescan', 'no'])
            for row in rows.splitlines():
                if row.startswith('*:'):
                    result['percent'] = signal_percent(row.split(':', 1)[1])
                    break
        elif kind in ('gsm', 'cdma', 'wwan', 'ethernet', '802-3-ethernet', 'modem-ethernet'):
            # USB cellular adapters can expose an Ethernet interface instead of
            # an NM gsm device. Identify the modem owning the actual route.
            result.update(connected=True, type='cellular' if kind in ('gsm', 'cdma', 'wwan') else 'ethernet')
            try:
                listing = json.loads(command(['mmcli', '-L', '--output-json']))
                for modem_path in listing.get('modem-list', []):
                    generic = json.loads(command(['mmcli', '-m', modem_path, '--output-json'])).get('modem', {}).get('generic', {})
                    ports = generic.get('ports', [])
                    if isinstance(ports, str):
                        ports = [ports]
                    matches = generic.get('primary-port') in (interface, nm_device) or any(str(port).split()[0] == interface for port in ports)
                    if not matches:
                        continue
                    result.update(type='cellular', source='NetworkManager/ModemManager')
                    quality = generic.get('signal-quality', {})
                    if not isinstance(quality, dict) or str(quality.get('recent', 'yes')).lower() in ('yes', 'true'):
                        result['percent'] = signal_percent(quality.get('value') if isinstance(quality, dict) else quality)
                    technology = generic.get('access-technologies') or generic.get('access-technology')
                    result['technology'] = ', '.join(map(str, technology)) if isinstance(technology, list) else technology
                    break
            except (OSError, ValueError, subprocess.SubprocessError):
                pass

    except (OSError, ValueError, IndexError, KeyError, subprocess.SubprocessError):
        # Missing/failed queries are not evidence of a connection.
        result['connected'] = False
    return result


network_link = {'connected': False, 'last_seen': 0.0}


def cellular_signal_reader():
    """Refresh active network state independently of MAVLink/RC status."""
    while True:
        result = read_network_link()
        result['last_seen'] = time.time()
        with cellular_lock:
            network_link.clear()
            network_link.update(result)
        time.sleep(2)


detections = []
det_lock = threading.Lock()

# Depth-based proximity obstacle detector (independent of YOLO classes).
# Catches ANY close object — hand, wall, branch — not just COCO classes.
proximity = {
    'obstacle': False,   # is something closer than stop dist?
    'dist': -1.0,        # measured closest distance (m), -1 if unmeasurable
    'reason': '',        # 'depth' (measured) or 'tooclose' (dead-zone fill)
}
proximity_lock = threading.Lock()

_sensor_ts = {
    'rangefinder': 0.0,
    'of': 0.0,
    'vib': 0.0,
}
SENSOR_TIMEOUT = 3.0

_last_imu_accel = None
last_mav_report = 0.0

mav_conn = None
mav_lock = threading.Lock()
health_telemetry = HealthTelemetry()
pilot_settings = PilotSettings(os.environ.get("AIRSENT_PILOT_SETTINGS_FILE", os.path.expanduser("~/.config/airsent/pilot-settings.json")))

mavlink_log = []
mavlink_log_lock = threading.Lock()
MAX_LOG = 20

vel_setpoint = {'vx': 0.0, 'vy': 0.0, 'vz': 0.0, 'yaw_rate': 0.0}
vel_last_cmd_ts = 0.0
vel_lock = threading.Lock()

obstacle_blocked = False
detection_active = True   # operator can disable obstacle avoidance from any GCS;
                          # synced to all browsers via telemetry. When False,
                          # obstacles are still detected/shown but do NOT block motion.

cam_device_available = False
downcam_device_available = False

cam_color_buf = {'frame': None}
cam_buf_lock  = threading.Lock()

stop_dist_global = pilot_settings.values["stop_distance"]
detection_active = pilot_settings.values["detection"]

downcam_raw = {'frame': None}
downcam_jpg = {'b64': None}
downcam_lock = threading.Lock()

apriltag_result = {'detected': False, 'tags': []}
apriltag_lock = threading.Lock()

# ── Launch sequencer / faults (see mission_engine.py) ─────────────────────────
statustext_log = deque(maxlen=60)          # (time, severity, text) from the Pixhawk
home_state = {'set': False}
vps_relay_state = {'telem': False}
MISSION_CONFIG = default_config(sim=SIM_MODE)
_gate_extra = ({f"RC{MISSION_CONFIG['rc_gate_channel']}_OPTION": {'value': 31, 'why': 'RC gate = Motor Emergency Stop'}}
               if MISSION_CONFIG['rc_gate_channel'] else {})
golden = GoldenParams(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'golden_params.json'),
                      extra=_gate_extra)
mission_cache = {'mission': None, 'faults': None}

def log_mavlink(msg):
    with mavlink_log_lock:
        ts = time.strftime('%H:%M:%S')
        mavlink_log.append(f"[{ts}] {msg}")
        if len(mavlink_log) > MAX_LOG:
            mavlink_log.pop(0)
    print(f"[MAV CMD] {msg}")

def rtmp_port_reachable(timeout=1.5):
    try:
        with socket.create_connection((RTMP_HOST, RTMP_PORT), timeout=timeout):
            return True
    except OSError:
        return False

# ── Thermal monitor ──────────────────────────────
import glob

cam_temp = {'jetson_c': 0.0, 'hot': False}
system_stats = {'cpu': None, 'gpu': None, 'ram': None}

def read_cpu_times():
    try:
        with open('/proc/stat') as f:
            vals = [int(v) for v in f.readline().split()[1:8]]
        idle = vals[3] + vals[4]
        return idle, sum(vals)
    except Exception:
        return None

def read_ram_percent():
    try:
        mem = {}
        with open('/proc/meminfo') as f:
            for line in f:
                key, raw = line.split(':', 1)
                mem[key] = float(raw.strip().split()[0])
        total = mem.get('MemTotal', 0)
        available = mem.get('MemAvailable', 0)
        if total <= 0:
            return None
        return round((total - available) / total * 100, 1)
    except Exception:
        return None

def read_gpu_percent():
    measured = gpu_percent()
    if measured is not None:
        return measured
    for path in (
        '/sys/devices/gpu.0/load',
        '/sys/devices/platform/host1x/gpu/load',
        '/sys/kernel/debug/gpu.0/load',
    ):
        try:
            with open(path) as f:
                raw = float(f.read().strip())
            return round(raw / 10 if raw > 100 else raw, 1)
        except Exception:
            pass
    try:
        result = subprocess.run(
            ['tegrastats', '--interval', '100', '--count', '1'],
            capture_output=True, text=True, timeout=2
        )
        import re
        m = re.search(r'GR3D_FREQ\s+(\d+(?:\.\d+)?)%', result.stdout)
        if m:
            return round(float(m.group(1)), 1)
    except Exception:
        pass
    return None

def read_jetson_temp():
    zones = glob.glob('/sys/devices/virtual/thermal/thermal_zone*/temp')
    temps = []
    for z in zones:
        try:
            with open(z) as f:
                temps.append(int(f.read().strip()) / 1000.0)
        except:
            pass
    return max(temps) if temps else 0.0

def check_oak_d_connected():
    try:
        result = subprocess.run(
            ['lsusb'], capture_output=True, text=True, timeout=3
        )
        return '03e7:2485' in result.stdout or 'luxonis' in result.stdout.lower()
    except Exception:
        return False

def thermal_monitor():
    global cam_device_available, downcam_device_available
    last_cpu = read_cpu_times()
    while True:
        try:
            t = read_jetson_temp()
            cam_temp['jetson_c'] = round(t, 1)
            cam_temp['hot'] = t >= 75.0
            if cam_temp['hot']:
                print(f"[THERMAL] WARNING: {t}C")
            cam_device_available = check_oak_d_connected()
            with downcam_lock:
                downcam_device_available = downcam_jpg['b64'] is not None
            cpu_now = read_cpu_times()
            if last_cpu and cpu_now:
                idle_delta = cpu_now[0] - last_cpu[0]
                total_delta = cpu_now[1] - last_cpu[1]
                if total_delta > 0:
                    system_stats['cpu'] = round((1 - idle_delta / total_delta) * 100, 1)
            last_cpu = cpu_now
            system_stats['gpu'] = read_gpu_percent()
            system_stats['ram'] = read_ram_percent()
        except Exception as e:
            print(f"[THERMAL] {e}")
        time.sleep(5)

# ── MAVLink thread ───────────────────────────────
# ── Battery % from resting voltage at power-up ──────────────────────────────
# ArduPilot assumes a full pack at boot and counts down by current. If the pack
# isn't full, % is wrong all flight. Once per Pixhawk connection, while disarmed
# and resting, estimate SoC from voltage and send MAV_CMD_BATTERY_RESET.
BATT_CELLS = 6
_LIPO_CURVE = [(4.20,100),(4.15,95),(4.11,90),(4.08,85),(4.02,80),(3.98,75),(3.95,70),(3.91,65),
               (3.87,60),(3.85,55),(3.84,50),(3.82,45),(3.80,40),(3.79,35),(3.77,30),(3.75,25),
               (3.73,20),(3.71,15),(3.69,10),(3.61,5),(3.27,0)]

def _lipo_soc(vcell):
    if vcell >= _LIPO_CURVE[0][0]:
        return 100
    for (v1, p1), (v2, p2) in zip(_LIPO_CURVE, _LIPO_CURVE[1:]):
        if vcell >= v2:
            return round(p2 + (p1 - p2) * (vcell - v2) / (v1 - v2))
    return 0

def battery_soc_init():
    done_for, last_set = None, None
    while True:
        time.sleep(1)
        with mav_lock:
            mav = mav_conn
        if mav is None:
            continue
        # Pixhawk rebooted and forgot our correction -> do it again
        if (mav is done_for and last_set is not None and last_set < 90
                and telem.get('battery', 0) >= 99 and not telem.get('armed', False)):
            done_for = None
        if mav is done_for:
            continue
        samples, ok = [], True
        for _ in range(5):
            v, a = telem.get('voltage', 0) or 0, telem.get('current', 0) or 0
            if telem.get('armed', False) or v < BATT_CELLS * 3.0 or a > 3.0:
                ok = False
                break
            samples.append(v)
            time.sleep(1)
        if not ok or max(samples) - min(samples) > 0.15:
            continue
        v = sum(samples) / len(samples)
        pct = _lipo_soc(v / BATT_CELLS)
        try:
            with mav_lock:
                if mav_conn is not mav:
                    continue
                mav.mav.command_long_send(mav.target_system, mav.target_component,
                                          mavutil.mavlink.MAV_CMD_BATTERY_RESET, 0,
                                          1, pct, 0, 0, 0, 0, 0)
            print(f"[BATT] Resting {v:.2f} V ({v/BATT_CELLS:.2f} V/cell) -> remaining set to {pct}%")
            done_for, last_set = mav, pct
        except Exception as e:
            print(f"[BATT] reset failed: {e}")


# ── COMMAND_ACK tracking ─────────────────────────
# The reader thread records every COMMAND_ACK so command handlers can report
# what the Pixhawk actually answered instead of "command sent".
cmd_acks = {}                      # MAV_CMD id -> (result, time received)
ack_cond = threading.Condition()
ACK_RESULTS = {0: 'ACCEPTED', 1: 'TEMPORARILY REJECTED', 2: 'DENIED', 3: 'UNSUPPORTED', 4: 'FAILED',
               5: 'IN PROGRESS', 6: 'CANCELLED'}
vehicle_mission = VehicleMission()
param_store = ParamStore()


def wait_ack(command, since, timeout):
    """Return the COMMAND_ACK result for `command` received after `since`, or None."""
    end = time.time() + timeout
    with ack_cond:
        while True:
            hit = cmd_acks.get(command)
            if hit and hit[1] >= since:
                return hit[0]
            left = end - time.time()
            if left <= 0:
                return None
            ack_cond.wait(left)


def mavlink_reader():
    global mav_conn, _last_imu_accel, last_mav_report, last_mav_heartbeat
    while True:
        try:
            print(f"[MAV] Connecting on {PIXHAWK_PORT} @ {PIXHAWK_BAUD}...")
            mav = mavutil.mavlink_connection(
                PIXHAWK_PORT, baud=PIXHAWK_BAUD,
                source_system=255, autoreconnect=True
            )
            with rc_lock:
                rc_monitor.reset()
            last_mav_heartbeat = 0.0
            golden.reset()
            vehicle_mission.reset()
            param_store.reset()
            home_state.clear(); home_state['set'] = False
            hb = mav.wait_heartbeat(timeout=15)
            if hb is None:
                print("[MAV] No heartbeat — retrying")
                time.sleep(3)
                continue
            last_mav_heartbeat = time.time()
            print(f"[MAV] Pixhawk connected — system {mav.target_system}")
            with mav_lock:
                mav_conn = mav
            pilot_settings.reset()
            pilot_settings.request_parameters(mav)
            settings_poll_at = time.monotonic()
            mav.mav.request_data_stream_send(
                mav.target_system, mav.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1
            )
            last_mav_msg = time.time()
            while True:
                msg = mav.recv_match(blocking=True, timeout=2)
                if not msg:
                    if time.time() - last_mav_msg > 6:
                        print("[MAV] No MAVLink messages for 6s — reconnecting")
                        with mav_lock:
                            if mav_conn is mav:
                                mav_conn = None
                        try:
                            mav.close()
                        except Exception:
                            pass
                        break
                    continue
                if time.monotonic()-settings_poll_at > 15:
                    pilot_settings.request_parameters(mav);settings_poll_at=time.monotonic()
                t = msg.get_type()
                if t == 'BAD_DATA': continue
                if msg.get_srcSystem() != hb.get_srcSystem() or msg.get_srcComponent() != hb.get_srcComponent():
                    continue
                _now = time.time()
                with rc_lock:
                    rc_monitor.update(msg, _now)
                last_mav_msg = _now
                if t == 'COMMAND_ACK':
                    with ack_cond:
                        cmd_acks[msg.command] = (msg.result, _now)
                        ack_cond.notify_all()
                elif t.startswith('MISSION_'):
                    vehicle_mission.observe(msg)
                if _now - last_mav_report > 5:
                    last_mav_report = _now
                    print(f"[MAV] alive {t} mode={telem.get('flight_mode')} sats={telem.get('sats')} rssi={telem.get('rssi')}")
                if t in ('SYS_STATUS','BATTERY_STATUS'):
                    health_telemetry.observe(msg, _now)
                if t == 'PARAM_VALUE':
                    pilot_settings.observe(msg)
                    golden.observe(msg)
                    param_store.observe(msg)
                elif t == 'ATTITUDE':
                    telem['roll']  = round(math.degrees(msg.roll), 1)
                    telem['pitch'] = round(math.degrees(msg.pitch), 1)
                    telem['yaw']   = round((math.degrees(msg.yaw)+360)%360, 1)
                elif t == 'GLOBAL_POSITION_INT':
                    telem['lat']  = msg.lat/1e7
                    telem['lon']  = msg.lon/1e7
                    telem['alt']  = round(msg.relative_alt/1000, 2)
                    telem['vspd'] = round(msg.vz/-100, 2)
                elif t == 'VFR_HUD':
                    telem['spd'] = round(msg.groundspeed, 1)
                elif t == 'GPS_RAW_INT':
                    fix_map={0:'NO FIX',1:'NO FIX',2:'2D FIX',3:'3D FIX',4:'DGPS',5:'RTK FLOAT',6:'RTK FIX'}
                    telem['fix_type'] = fix_map.get(msg.fix_type,'NO FIX')
                    telem['sats']     = msg.satellites_visible
                    telem['hdop']     = round(msg.eph/100, 2)
                elif t == 'SYS_STATUS':
                    telem['voltage'] = round(msg.voltage_battery/1000, 1)
                    telem['current'] = round(msg.current_battery/100, 1)
                    telem['battery'] = msg.battery_remaining
                elif t == 'HEARTBEAT':
                    last_mav_heartbeat = _now
                    telem['armed'] = bool(msg.base_mode & 128)
                    mode_map={0:'STABILIZE',1:'ACRO',2:'ALT HOLD',3:'AUTO',
                              4:'GUIDED',5:'LOITER',6:'RTL',9:'LAND',16:'POSHOLD'}
                    telem['flight_mode'] = mode_map.get(msg.custom_mode, str(msg.custom_mode))
                elif t == 'SERVO_OUTPUT_RAW':
                    telem['m1']=msg.servo1_raw; telem['m2']=msg.servo2_raw
                    telem['m3']=msg.servo3_raw; telem['m4']=msg.servo4_raw
                elif t == 'RANGEFINDER':
                    telem['rangefinder'] = round(msg.distance, 2)
                    _sensor_ts['rangefinder'] = time.time()
                elif t == 'DISTANCE_SENSOR' and getattr(msg, 'orientation', 25) == 25:
                    # ArduPilot 4.6 sends DroneCAN rangefinders (H-Flow) as DISTANCE_SENSOR, not RANGEFINDER
                    telem['rangefinder'] = round(msg.current_distance / 100.0, 2)   # raw distance (0 = no data, UI relies on this)
                    telem['rangefinder_agl'] = round(max(0.0, msg.current_distance / 100.0 - RNG_GND_CLEAR_M), 2)   # height above ground, display only
                    _sensor_ts['rangefinder'] = time.time()
                elif t == 'OPTICAL_FLOW':
                    telem['of_quality'] = msg.quality
                    telem['of_flow_x']  = round(msg.flow_comp_m_x, 3)
                    telem['of_flow_y']  = round(msg.flow_comp_m_y, 3)
                    _sensor_ts['of'] = time.time()
                elif t == 'VIBRATION':
                    telem['vib_x'] = round(float(msg.vibration_x), 3)
                    telem['vib_y'] = round(float(msg.vibration_y), 3)
                    telem['vib_z'] = round(float(msg.vibration_z), 3)
                    _sensor_ts['vib'] = _now
                elif t in ('RAW_IMU', 'SCALED_IMU', 'SCALED_IMU2', 'SCALED_IMU3'):
                    scale = 9.80665 / 1000.0
                    accel = (
                        float(msg.xacc) * scale,
                        float(msg.yacc) * scale,
                        float(msg.zacc) * scale,
                    )
                    if _last_imu_accel is not None and _now - _sensor_ts['vib'] > 1.0:
                        telem['vib_x'] = round(abs(accel[0] - _last_imu_accel[0]), 3)
                        telem['vib_y'] = round(abs(accel[1] - _last_imu_accel[1]), 3)
                        telem['vib_z'] = round(abs(accel[2] - _last_imu_accel[2]), 3)
                        _sensor_ts['vib'] = _now
                    _last_imu_accel = accel
                elif t == 'HIGHRES_IMU':
                    accel = (
                        float(msg.xacc),
                        float(msg.yacc),
                        float(msg.zacc),
                    )
                    if _last_imu_accel is not None and _now - _sensor_ts['vib'] > 1.0:
                        telem['vib_x'] = round(abs(accel[0] - _last_imu_accel[0]), 3)
                        telem['vib_y'] = round(abs(accel[1] - _last_imu_accel[1]), 3)
                        telem['vib_z'] = round(abs(accel[2] - _last_imu_accel[2]), 3)
                        _sensor_ts['vib'] = _now
                    _last_imu_accel = accel
                elif t == 'RC_CHANNELS':
                    telem['rc_channels'] = [getattr(msg, f'chan{i}_raw', 0) for i in range(1, 19)]
                elif t == 'HOME_POSITION':
                    home_state.update(set=True, lat=msg.latitude / 1e7, lon=msg.longitude / 1e7,
                                      alt=msg.altitude / 1000, seen=_now)
                elif t == 'STATUSTEXT':
                    text = msg.text.decode('ascii', 'ignore') if isinstance(msg.text, bytes) else str(msg.text)
                    statustext_log.append((_now, int(msg.severity), text.strip('\x00').strip()))
                elif t == 'EKF_STATUS_REPORT':
                    telem['ekf_healthy'] = bool(msg.flags & 0x1F == 0x1F)

                if (_sensor_ts['rangefinder'] > 0 and
                        _now - _sensor_ts['rangefinder'] > SENSOR_TIMEOUT):
                    telem['rangefinder'] = 0
                    _sensor_ts['rangefinder'] = 0.0
                if (_sensor_ts['of'] > 0 and
                        _now - _sensor_ts['of'] > SENSOR_TIMEOUT):
                    telem['of_quality'] = 0
                    telem['of_flow_x']  = 0.0
                    telem['of_flow_y']  = 0.0
                    _sensor_ts['of'] = 0.0
                if (_sensor_ts['vib'] > 0 and
                        _now - _sensor_ts['vib'] > SENSOR_TIMEOUT):
                    telem.pop('vib_x', None)
                    telem.pop('vib_y', None)
                    telem.pop('vib_z', None)
                    _sensor_ts['vib'] = 0.0
        except Exception as e:
            print(f"[MAV] Error: {e}")
            with mav_lock:
                mav_conn = None
            time.sleep(3)

# ── Velocity sender thread ────────────────────────
def velocity_sender():
    while True:
        try:
            with mav_lock:
                mav = mav_conn
            if (mav and telem['flight_mode'] == 'GUIDED' and telem['armed']
                    and (not SIM_MODE or (vel_last_cmd_ts > 0 and time.time() - vel_last_cmd_ts < 1.0))):
                with vel_lock:
                    vx = vel_setpoint['vx']
                    vy = vel_setpoint['vy']
                    vz = vel_setpoint['vz']
                    yr = vel_setpoint['yaw_rate']

                if obstacle_blocked and vx > 0:
                    vx = 0.0
                    log_mavlink("OBSTACLE BLOCK — forward velocity zeroed")

                type_mask = (
                    mavutil.mavlink.POSITION_TARGET_TYPEMASK_X_IGNORE |
                    mavutil.mavlink.POSITION_TARGET_TYPEMASK_Y_IGNORE |
                    mavutil.mavlink.POSITION_TARGET_TYPEMASK_Z_IGNORE |
                    mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE |
                    mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE |
                    mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE |
                    mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
                )

                mav.mav.set_position_target_local_ned_send(
                    int(time.time() * 1000) & 0xFFFFFFFF,
                    mav.target_system,
                    mav.target_component,
                    mavutil.mavlink.MAV_FRAME_BODY_NED,
                    type_mask,
                    0, 0, 0,
                    vx, vy, vz,
                    0, 0, 0,
                    0,
                    yr
                )
        except Exception as e:
            print(f"[VEL] Error: {e}")
        time.sleep(0.1)

# ── MAVLink command execution ─────────────────────
def set_guided_mode(mav):
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_MODE,
        0,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        4,
        0, 0, 0, 0, 0
    )
    log_mavlink("MAV_CMD_DO_SET_MODE → GUIDED (4)")

# Modes an operator may select from the console. Everything else (STABILIZE,
# ACRO, FLIP, AUTOTUNE ...) stays on the RC transmitter only.
CONSOLE_MODES = {'LOITER', 'GUIDED', 'AUTO', 'RTL', 'LAND', 'ALTHOLD', 'ALT HOLD'}


def set_mode(mav, mode_num, mode_name):
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_MODE,
        0,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        mode_num,
        0, 0, 0, 0, 0
    )
    log_mavlink(f"MAV_CMD_DO_SET_MODE → {mode_name} ({mode_num})")

def arm_drone(mav):
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 1, 0, 0, 0, 0, 0, 0
    )
    log_mavlink("MAV_CMD_COMPONENT_ARM_DISARM → ARM")

def disarm_drone(mav):
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 0, 0, 0, 0, 0, 0, 0
    )
    log_mavlink("MAV_CMD_COMPONENT_ARM_DISARM → DISARM")

def emergency_stop(mav, who='Operator'):
    """FORCE disarm (param2 = 21196): the motors stop even in flight and the
    aircraft falls. A normal disarm is refused by ArduPilot while flying.
    Retries until the Pixhawk acknowledges or reports disarmed."""
    with vel_lock:
        vel_setpoint.update({'vx': 0, 'vy': 0, 'vz': 0, 'yaw_rate': 0})
    log_mavlink(f"EMERGENCY STOP by {who}")
    last = None
    for attempt in range(1, 4):
        since = time.time()
        mav.mav.command_long_send(mav.target_system, mav.target_component,
                                  mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 0, 21196, 0, 0, 0, 0, 0)
        log_mavlink(f"MAV_CMD_COMPONENT_ARM_DISARM → FORCE DISARM (attempt {attempt})")
        last = wait_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, since, 0.7)
        if last == 0:
            return {'ok': True, 'msg': f'EMERGENCY STOP — motors stopped (Pixhawk accepted, attempt {attempt})'}
    if not telem.get('armed'):
        return {'ok': True, 'msg': 'EMERGENCY STOP — telemetry shows DISARMED (no acknowledgement received)'}
    return {'ok': False, 'msg': 'EMERGENCY STOP NOT CONFIRMED — ' + (f'Pixhawk answered {ACK_RESULTS.get(last, last)}' if last is not None
            else 'no answer from the Pixhawk') + '. Use the RC kill switch.'}


def send_rtl(mav):
    set_mode(mav, 6, "RTL")

def send_land(mav):
    set_mode(mav, 9, "LAND")

def send_loiter(mav):
    set_mode(mav, 5, "LOITER")

def send_takeoff(mav, alt):
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
        0, 0, 0, 0, 0, 0, 0, alt
    )
    log_mavlink(f"MAV_CMD_NAV_TAKEOFF → {alt}m")

# ── Launch sequencer: vehicle adapter, faults, 5 Hz loop ────────────────────
class MavVehicle:
    """What the mission engine may do to the aircraft. Never raises."""
    MODES = {'GUIDED': 4, 'LOITER': 5, 'RTL': 6, 'LAND': 9}

    def _run(self, label, fn):
        with mav_lock:
            mav = mav_conn
        if mav is None:
            log_mavlink(f"MISSION {label} FAILED — no MAVLink connection")
            return False
        try:
            fn(mav)
            return True
        except Exception as e:
            log_mavlink(f"MISSION {label} FAILED — {e}")
            return False

    def set_mode(self, name):
        return self._run('MODE ' + name, lambda mav: set_mode(mav, self.MODES[name], name))

    def arm(self):
        return self._run('ARM', arm_drone)

    def disarm(self, force=False):
        with vel_lock:
            vel_setpoint.update({'vx': 0, 'vy': 0, 'vz': 0, 'yaw_rate': 0})
        if not force:
            return self._run('DISARM', disarm_drone)
        def forced(mav):
            mav.mav.command_long_send(mav.target_system, mav.target_component,
                                      mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 0, 21196, 0, 0, 0, 0, 0)
            log_mavlink("MAV_CMD_COMPONENT_ARM_DISARM → FORCE DISARM")
        return self._run('FORCE DISARM', forced)

    def takeoff(self, alt):
        return self._run('TAKEOFF', lambda mav: send_takeoff(mav, alt))

    def land(self):
        return self._run('LAND', send_land)

    def rtl(self):
        return self._run('RTL', send_rtl)


MISSION_ROOT = os.environ.get('AIRSENT_MISSION_DIR',
                              os.path.expanduser('~/airsent_missions_sim' if SIM_MODE else '~/airsent_missions'))
mission_engine = MissionEngine(
    os.environ.get('AIRSENT_MISSION_STATE', os.path.expanduser(
        '~/.local/share/airsent/' + ('sim_mission_state.json' if SIM_MODE else 'mission_state.json'))),
    MISSION_ROOT, MavVehicle(), config=MISSION_CONFIG)


def last_prearm_text(now, window=40):
    for t, severity, text in reversed(list(statustext_log)):
        if now - t > window:
            break
        if text.lower().startswith('prearm'):
            return text
    return ''


def system_fault_conditions(s, now):
    """Faults that exist whether or not a mission is running."""
    c = {}
    if not s.get('pixhawk_connected'):
        c['sys:pixhawk'] = ('critical', 'Pixhawk', 'Flight controller offline — no heartbeat')
    else:
        if not s.get('ekf_healthy'):
            c['sys:ekf'] = ('critical' if s.get('armed') else 'warning', 'Pixhawk', 'EKF position estimate unhealthy')
        fix = s.get('fix_type') or 'NO FIX'
        if fix in ('NO FIX', '2D FIX'):
            c['sys:gps'] = ('warning', 'GPS', f'GPS: {fix}')
        volt = s.get('voltage')
        if isinstance(volt, (int, float)) and 1 < volt < 21.6:
            c['sys:battery'] = ('critical', 'Battery', f'Battery low: {volt:.1f} V')
        vib = [s.get(k) for k in ('vib_x', 'vib_y', 'vib_z') if isinstance(s.get(k), (int, float))]
        if vib and max(vib) >= MISSION_CONFIG['max_vibration']:
            c['sys:vibration'] = ('warning', 'Sensors', f'High vibration: {max(vib):.0f} m/s²')
        if not SIM_MODE and not (s.get('rc_signal') or {}).get('input_present'):
            c['sys:rc'] = ('warning', 'RC', 'RC receiver signal not present')
        gs = s.get('_golden') or {}
        if gs.get('state') == 'diff':
            c['sys:golden'] = ('warning', 'Config', f"{len(gs['diffs'])} Pixhawk parameter(s) differ from the approved set")
    if not SIM_MODE:
        if not s.get('front_cam_active'):
            c['sys:frontcam'] = ('warning', 'Camera', 'Front camera (OAK-D) offline')
        if not s.get('down_cam_active'):
            c['sys:downcam'] = ('warning', 'Camera', 'Down camera offline')
        if MISSION_CONFIG['require_esc_telemetry'] and not (s.get('esc_telemetry') or {}).get('connected'):
            c['sys:esc'] = ('warning', 'ESC', 'ESC telemetry (Datalink) not connected')
    temp = s.get('jetson_temp')
    if isinstance(temp, (int, float)) and temp >= MISSION_CONFIG['max_jetson_temp']:
        c['sys:jetson_temp'] = ('warning', 'Jetson', f'Jetson hot: {temp:.0f} °C')
    disk = s.get('_disk_free_gb')
    if disk is not None and disk < MISSION_CONFIG['min_disk_gb']:
        c['sys:disk'] = ('warning', 'Jetson', f'Low storage for mission logs: {disk:.1f} GB')
    for t, severity, text in list(statustext_log):
        age = now - t
        if text.lower().startswith('prearm') and age < 35:
            c['px:' + text[:48]] = ('warning', 'Pixhawk', text)
        elif severity <= 2 and age < 30:
            c['px:' + text[:48]] = ('critical', 'Pixhawk', text)
        elif severity == 3 and age < 20:
            c['px:' + text[:48]] = ('warning', 'Pixhawk', text)
    return c


def mission_loop():
    last_golden = last_home = last_disk = 0.0
    disk = None
    while True:
        try:
            now = time.time()
            with mav_lock:
                mav = mav_conn
            if mav is not None:
                if now - last_golden > 20:
                    last_golden = now
                    golden.request(mav)
                if now - last_home > 5:
                    last_home = now
                    mav.mav.command_long_send(mav.target_system, mav.target_component,
                                              mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE, 0,
                                              mavutil.mavlink.MAVLINK_MSG_ID_HOME_POSITION, 0, 0, 0, 0, 0, 0)
            if now - last_disk > 5:
                last_disk = now
                disk = disk_free_gb(MISSION_ROOT)
            s = build_telemetry_payload(include_mission=False)
            s['_golden'] = golden.snapshot()
            s['_disk_free_gb'] = disk
            s['_vps_relay_connected'] = vps_relay_state['telem'] or SIM_MODE
            s['_last_prearm'] = last_prearm_text(now)
            mission_engine.tick(s, system_fault_conditions(s, now))
            mission_cache['mission'] = mission_engine.snapshot()
            mission_cache['faults'] = mission_engine.faults.snapshot()
        except Exception as e:
            print(f"[MISSION] loop error: {e}")
            traceback.print_exc()
        time.sleep(0.2)


# ── Command WebSocket handler ─────────────────────
SLOW_ACTIONS = {'wp_upload', 'wp_download', 'param_read', 'param_write'}


def process_command(raw):
    """Run a command and make sure the reply carries the console's request_id,
    so the console that asked can match it (the relay broadcasts every reply)."""
    reply = _process_command(raw)
    try:
        cmd = json.loads(raw)
        if isinstance(cmd, dict) and isinstance(reply, dict):
            reply.setdefault('request_id', cmd.get('request_id'))
            reply.setdefault('action', cmd.get('action'))
    except Exception:
        pass
    return reply


def is_slow_command(raw):
    try:
        return json.loads(raw).get('action') in SLOW_ACTIONS
    except Exception:
        return False


def _process_command(raw):
    """Execute a single command (JSON string) and return a reply dict.
    Shared by BOTH the local cmd_handler (port 8767) and vps_cmd_relay
    (VPS:9004) so remote commands run the exact same code as local ones.
    Never raises — always returns a dict ready to json.dumps and send back."""
    global obstacle_blocked, stop_dist_global, detection_active, vel_last_cmd_ts
    try:
        cmd = json.loads(raw)
    except json.JSONDecodeError as e:
        return {'ok': False, 'msg': f'JSON error: {e}'}

    action = cmd.get('action', '')

    # Launch sequencer requests are handled by the Jetson mission engine,
    # including abort/hold when the Pixhawk link is down.
    if isinstance(action, str) and (action.startswith('mission_') or action == 'fault_ack'):
        return mission_engine.handle(cmd)

    with mav_lock:
        mav = mav_conn

    if mav is None:
        return {'ok': False, 'msg': 'No MAVLink connection','type':'pilot_setting_result' if action=='pilot_setting' else 'command_error','request_id':cmd.get('request_id')}

    try:
        if action == 'pilot_setting':
            if time.time()-last_mav_heartbeat>=3:raise ValueError('Fresh aircraft heartbeat required')
            snapshot=pilot_settings.apply(mav,cmd.get('key'),cmd.get('value'),cmd.get('revision'),armed=bool(telem.get('armed')))
            detection_active=pilot_settings.values['detection'];stop_dist_global=pilot_settings.values['stop_distance']
            return {'ok':True,'type':'pilot_setting_result','request_id':cmd.get('request_id'),'settings':snapshot,'msg':'Setting confirmed'}
        elif action == 'set_guided':
            set_guided_mode(mav)
            return {'ok': True, 'msg': 'Switching to GUIDED'}

        elif action == 'set_loiter':
            send_loiter(mav)
            with vel_lock:
                vel_setpoint.update({'vx':0,'vy':0,'vz':0,'yaw_rate':0})
            return {'ok': True, 'msg': 'Switching to LOITER'}

        elif action == 'set_rtl':
            send_rtl(mav)
            return {'ok': True, 'msg': 'RTL initiated'}

        elif action == 'set_land':
            send_land(mav)
            return {'ok': True, 'msg': 'LAND initiated'}

        elif action == 'arm':
            since = time.time()
            arm_drone(mav)
            r = wait_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, since, 2.0)
            if r == 0:
                return {'ok': True, 'msg': 'ARMED — Pixhawk accepted'}
            why = (last_prearm_text(time.time()) or '') if r is not None else ''
            return {'ok': False, 'msg': 'ARM refused by Pixhawk: ' + ACK_RESULTS.get(r, str(r)) + (f' — {why}' if why else '')
                    if r is not None else 'ARM sent, no answer from the Pixhawk — check telemetry'}

        elif action == 'disarm':
            since = time.time()
            disarm_drone(mav)
            r = wait_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, since, 2.0)
            if r == 0:
                return {'ok': True, 'msg': 'DISARMED — Pixhawk accepted'}
            if r is None:
                return {'ok': False, 'msg': 'DISARM sent, no answer from the Pixhawk — check telemetry'}
            return {'ok': False, 'msg': 'DISARM refused: ' + ACK_RESULTS.get(r, str(r))
                    + (' — the aircraft is flying; use EMERGENCY STOP to cut the motors' if telem.get('armed') else '')}

        elif action == 'emergency_stop':
            return emergency_stop(mav, cmd.get('operator') or 'Operator')

        elif action == 'wp_upload':
            if telem.get('armed'):
                return {'ok': False, 'msg': 'Mission upload is blocked while the aircraft is armed'}
            home = (home_state['lat'], home_state['lon']) if home_state.get('set') else None
            who = cmd.get('operator') or 'Operator'
            try:
                r = vehicle_mission.upload_plan(mav, cmd.get('plan') or {}, home, who=who,
                                                limits={'max_alt': float(os.environ.get('AIRSENT_WP_MAX_ALT', 120)),
                                                        'max_range': float(os.environ.get('AIRSENT_WP_MAX_RANGE', 2000))})
            except (ValueError, RuntimeError) as e:
                log_mavlink(f"MISSION UPLOAD FAILED — {e}")
                return {'ok': False, 'msg': str(e), 'vehicle_mission': vehicle_mission.snapshot()}
            log_mavlink(f"MISSION UPLOADED + VERIFIED — {r['nav_items']} waypoints ({r['items']} items) by {who}")
            return {'ok': True, 'msg': f"Uploaded and verified {r['nav_items']} waypoints on the Pixhawk",
                    'warnings': r['warnings'], 'result': r, 'vehicle_mission': vehicle_mission.snapshot()}

        elif action == 'param_read':
            names = cmd.get('names') or []
            if not isinstance(names, list) or not names or len(names) > 300:
                return {'ok': False, 'msg': 'param_read needs 1-300 names'}
            try:
                found, missing = param_store.read(mav, names)
            except ValueError as e:
                return {'ok': False, 'msg': str(e)}
            return {'ok': True, 'msg': f'Read {len(found)} parameters from the Pixhawk'
                    + (f' ({len(missing)} not on this vehicle)' if missing else ''),
                    'values': found, 'missing': missing}

        elif action == 'param_write':
            changes = cmd.get('params') or {}
            if not isinstance(changes, dict) or not changes or len(changes) > 100:
                return {'ok': False, 'msg': 'param_write needs 1-100 parameters'}
            if telem.get('armed'):
                return {'ok': False, 'msg': 'Parameter writes are blocked while the aircraft is armed'}
            who = cmd.get('operator') or 'Operator'
            done, err = param_store.write_many(mav, changes)
            for n, v in done.items():
                log_mavlink(f"PARAM {n} = {v} (confirmed) by {who}")
            if err:
                log_mavlink(f"PARAM WRITE STOPPED — {err}")
                return {'ok': False, 'msg': f'Wrote {len(done)}/{len(changes)} — stopped: {err}', 'values': done}
            return {'ok': True, 'msg': f'Wrote and confirmed {len(done)} parameter(s) on the Pixhawk', 'values': done}

        elif action == 'wp_download':
            home = (home_state['lat'], home_state['lon']) if home_state.get('set') else None
            try:
                r = vehicle_mission.download_plan(mav, home, who=cmd.get('operator') or 'Operator')
            except RuntimeError as e:
                return {'ok': False, 'msg': str(e)}
            return {'ok': True, 'msg': f"Downloaded {len(r['waypoints'])} waypoints from the Pixhawk",
                    'result': r, 'vehicle_mission': vehicle_mission.snapshot()}

        elif action == 'takeoff':
            if SIM_MODE: vel_last_cmd_ts = 0.0
            alt = float(cmd.get('alt', pilot_settings.values['takeoff_alt']))
            if not math.isfinite(alt) or alt<1 or alt>50:raise ValueError('Invalid takeoff altitude')
            send_takeoff(mav, alt)
            return {'ok': True, 'msg': f'TAKEOFF to {alt}m'}

        elif action == 'velocity':
            vx = float(cmd.get('vx', 0))
            vy = float(cmd.get('vy', 0))
            vz = float(cmd.get('vz', 0))
            yr = float(cmd.get('yaw_rate', 0))
            vx,vy,vz,yr=pilot_settings.clamp_velocity(vx,vy,vz,yr)

            if obstacle_blocked and vx > 0:
                vx = 0.0
                log_mavlink(f"BLOCKED: obstacle in path (fwd={cmd.get('vx',0)} -> 0)")
            else:
                log_mavlink(f"VELOCITY vx={vx:.1f} vy={vy:.1f} vz={vz:.1f} yr={yr:.2f}")

            with vel_lock:
                vel_setpoint.update({'vx':vx,'vy':vy,'vz':vz,'yaw_rate':yr})
                vel_last_cmd_ts = time.time()

            return {
                'ok': True,
                'msg': f'vx={vx:.1f} vy={vy:.1f} vz={vz:.1f} yr={yr:.2f}',
                'blocked': obstacle_blocked and cmd.get('vx',0) > 0
            }

        elif action == 'get_log':
            with mavlink_log_lock:
                log = list(mavlink_log)
            return {'ok': True, 'log': log}

        elif action == 'set_mode':
            # General flight-mode change by name. Covers the full dashboard
            # dropdown. ArduCopter custom_mode numbers (must match the
            # mode_map used in mavlink_reader for telemetry to read back right).
            mode_name = str(cmd.get('mode', '')).upper().strip()
            MODE_NUMS = {
                'STABILIZE': 0, 'ACRO': 1, 'ALTHOLD': 2, 'ALT HOLD': 2,
                'AUTO': 3, 'GUIDED': 4, 'LOITER': 5, 'RTL': 6,
                'CIRCLE': 7, 'LAND': 9, 'DRIFT': 11, 'SPORT': 13,
                'FLIP': 14, 'AUTOTUNE': 15, 'POSHOLD': 16, 'BRAKE': 17,
                'THROW': 18, 'SMART_RTL': 21, 'SMARTRTL': 21,
            }
            if mode_name not in MODE_NUMS:
                return {'ok': False, 'msg': f'Unknown mode: {mode_name}'}
            if mode_name not in CONSOLE_MODES:
                return {'ok': False, 'msg': f'{mode_name} is not available from the console — use the RC transmitter'}
            num = MODE_NUMS[mode_name]
            set_mode(mav, num, mode_name)
            # Zero velocity on any non-GUIDED mode for safety.
            if mode_name != 'GUIDED':
                with vel_lock:
                    vel_setpoint.update({'vx':0,'vy':0,'vz':0,'yaw_rate':0})
            return {'ok': True, 'msg': f'Switching to {mode_name}'}

        elif action == 'set_auto':
            set_mode(mav, 3, 'AUTO')
            return {'ok': True, 'msg': 'Switching to AUTO (mission)'}

        elif action in ('set_detection','set_stop_dist'):
            key='detection' if action=='set_detection' else 'stop_distance'
            value=cmd.get('on') if key=='detection' else cmd.get('dist')
            pilot_settings.apply(mav,key,value,pilot_settings.revision,armed=bool(telem.get('armed')))
            detection_active=pilot_settings.values['detection'];stop_dist_global=pilot_settings.values['stop_distance']
            return {'ok':True,'msg':'Companion setting confirmed'}

        elif action == 'buzzer':
            # Play a tone on the flight controller's buzzer (the one on the GPS
            # module) via the MAVLink PLAY_TUNE message. The tune is MML (Music
            # Macro Language) — the same format ArduPilot's own boot/mode tones
            # use.
            #
            # ⚠ SAFETY — DO NOT pass arbitrary tune strings. A MALFORMED MML
            #   string can REBOOT the flight controller (ArduPilot issue
            #   #13868). A reboot in flight = loss of vehicle. So we ONLY accept
            #   a name from this fixed whitelist of well-formed tunes, never a
            #   raw string from the client. If you add a tune here, BENCH-TEST
            #   it (props off, on the ground) before ever flying with it.
            TUNES = {
                'beep':   'MFT200L8>A',           # one short chirp
                'double': 'MFT200L16>AP16A',      # two quick chirps
                'manual': 'MST160L8>AAA',          # three quick beeps —
                                                   # autopilot-disengaged cue
                                                   # (Airbus-style). MS=staccato
                                                   # so the notes don't merge.
                'ready':  'MFT200L8>CDEG',         # rising "ready" arpeggio
                'alert':  'MFT200L8>GP8GP8G',      # three urgent notes
                'locate': 'MFT120L2>AA',           # two long loud notes — find
                                                   # the drone on the ground
            }
            name = str(cmd.get('tune', 'beep')).lower().strip()
            tune = TUNES.get(name)
            if tune is None:
                return {'ok': False, 'msg': f'Unknown tune: {name}'}
            try:
                mav.mav.play_tune_send(
                    mav.target_system, mav.target_component, tune.encode('ascii')
                )
            except Exception as e:
                return {'ok': False, 'msg': f'PLAY_TUNE failed: {e}'}
            log_mavlink(f"PLAY_TUNE -> {name}")
            return {'ok': True, 'msg': f'Buzzer: {name}'}

        else:
            return {'ok': False, 'msg': f'Unknown action: {action}'}

    except Exception as e:
        print(f"[CMD] Error: {e}")
        return {'ok': False, 'msg': str(e), 'type':'pilot_setting_result' if action=='pilot_setting' else 'command_error', 'request_id':cmd.get('request_id')}


_bg_commands = set()


async def serve_commands(source, sink):
    """Run commands from one socket. Quick commands run in order; slow ones
    (mission upload/download) run in the background so an EMERGENCY STOP or
    ABORT sent during a transfer is never queued behind it."""
    send_lock = asyncio.Lock()

    async def run(raw):
        reply = await asyncio.to_thread(process_command, raw)
        async with send_lock:
            await sink.send(json.dumps(reply))

    async def run_bg(raw):
        try:
            await run(raw)
        except Exception as e:
            print(f"[CMD] background reply failed: {e}")

    async for raw in source:
        if is_slow_command(raw):
            task = asyncio.create_task(run_bg(raw))
            _bg_commands.add(task)
            task.add_done_callback(_bg_commands.discard)
        else:
            await run(raw)


async def cmd_handler(websocket):
    """Local command WebSocket (port 8767). Works on the LAN. Delegates the
    actual work to process_command so local and remote behave identically."""
    print(f"[CMD] Client: {websocket.remote_address}")
    try:
        await serve_commands(websocket, websocket)
    except websockets.exceptions.ConnectionClosed:
        print("[CMD] Disconnected")
        with vel_lock:
            vel_setpoint.update({'vx':0,'vy':0,'vz':0,'yaw_rate':0})

# ── Telemetry payload builder ─────────────────────────────────────────────
def mission_interval():
    """Mission/fault state is ~6 KB: send it 2x/s during a mission, every 2 s otherwise."""
    stage = (mission_cache['mission'] or {}).get('stage', 'standby')
    return 2.0 if stage == 'standby' else 0.5


def build_telemetry_payload(include_mission=True):
    now = time.time()
    with mavlink_log_lock:
        log = list(mavlink_log)
    payload = dict(telem)
    payload['mavlink_log'] = log
    payload['esc_telemetry'] = esc_monitor.snapshot() if esc_monitor else None
    payload['simulation'] = SIM_MODE
    payload['telemetry_age'] = 0.0  # Local link; VPS relay overwrites with its measured age
    with mav_lock:
        px_ok = mav_conn is not None
    heartbeat_age = now - last_mav_heartbeat if last_mav_heartbeat else None
    payload['pixhawk_connected'] = px_ok and heartbeat_age is not None and heartbeat_age < 5
    payload['pixhawk_heartbeat_age'] = round(heartbeat_age, 2) if heartbeat_age is not None else None
    sensors,power = health_telemetry.snapshot(now,payload['pixhawk_connected'])
    payload['sensor_health'] = sensors
    payload['power_status'] = power
    for key in ('voltage','current','battery'):
        if power[key] is not None:payload[key] = power[key]
    payload['sensor_ages'] = {key:now-_sensor_ts[key] if _sensor_ts[key]>0 else None for key in ('rangefinder','of','vib')}
    with rc_lock:
        payload['rc_signal'] = rc_monitor.snapshot(now, payload['pixhawk_connected'])
    # Keep legacy consumers from receiving a stale/default RSSI.
    payload['rssi'] = payload['rc_signal']['percent']
    with cellular_lock:
        network = dict(network_link)
    sampled_at = network.pop('last_seen', 0.0)
    network_age = now - sampled_at if sampled_at else None
    network['age'] = round(network_age, 2) if network_age is not None else None
    network['connected'] = bool(network.get('connected') and network_age is not None and 0 <= network_age < 6)
    if not network['connected']:
        network['percent'] = None
    payload['network_link'] = network
    # Compatibility for existing consumers expecting cellular-only measurements.
    payload['cellular_signal'] = dict(network) if network.get('type') == 'cellular' else {
        'connected': False, 'percent': None, 'age': network['age'], 'technology': None,
        'source': 'ModemManager'}
    payload['cam_temp'] = cam_temp['jetson_c']
    payload['cam_hot']  = cam_temp['hot']
    payload['jetson_temp'] = cam_temp['jetson_c']
    for key, val in system_stats.items():
        if val is not None:
            payload[key] = val
            payload[f'jetson_{key}'] = val
    payload['front_cam_active'] = cam_device_available
    payload['down_cam_active']  = downcam_device_available
    with apriltag_lock:
        payload['apriltag'] = dict(apriltag_result)
    with det_lock:
        payload['detections'] = list(detections)
    with proximity_lock:
        payload['proximity'] = dict(proximity)
    payload['pilot_settings'] = pilot_settings.snapshot(payload['pixhawk_connected'])
    payload['detection_active'] = detection_active
    payload['stop_distance'] = stop_dist_global
    of_offline  = (_sensor_ts['of']          == 0 or (now - _sensor_ts['of'])          > SENSOR_TIMEOUT)
    rng_offline = (_sensor_ts['rangefinder'] == 0 or (now - _sensor_ts['rangefinder']) > SENSOR_TIMEOUT
                   or of_offline)
    if rng_offline:
        payload['rangefinder'] = 0
        payload['rangefinder_agl'] = None
    if of_offline:
        payload['of_quality'] = 0
        payload['of_flow_x']  = 0.0
        payload['of_flow_y']  = 0.0
    if _sensor_ts['vib'] == 0 or (now - _sensor_ts['vib']) > SENSOR_TIMEOUT:
        payload.pop('vib_x', None)
        payload.pop('vib_y', None)
        payload.pop('vib_z', None)
    payload['home'] = dict(home_state)
    if include_mission:
        payload['vehicle_mission'] = vehicle_mission.snapshot()
        payload['mission'] = mission_cache['mission']
        payload['faults'] = mission_cache['faults']
    return payload

# ── Telemetry WebSocket ──────────────────────────
async def telem_handler(websocket):
    print(f"[TELEM] Client: {websocket.remote_address}")
    last_mission = 0.0
    try:
        while True:
            include = time.time() - last_mission >= mission_interval()
            if include:
                last_mission = time.time()
            await websocket.send(json.dumps(build_telemetry_payload(include_mission=include)))
            await asyncio.sleep(0.05)
    except websockets.exceptions.ConnectionClosed:
        print("[TELEM] Disconnected")

# ── YOLOv8 parser ────────────────────────────────
def parse_yolov8(tensor, conf_thresh):
    results = []
    try:
        t = np.array(tensor).reshape(85, -1)
        cx_all = t[0]; cy_all = t[1]
        w_all  = t[2]; h_all  = t[3]
        scores_all = t[4:]
        class_ids = np.argmax(scores_all, axis=0)
        confs = scores_all[class_ids, np.arange(t.shape[1])]
        mask = confs >= conf_thresh
        if not np.any(mask):
            return results
        cx=cx_all[mask]; cy=cy_all[mask]
        w=w_all[mask];   h=h_all[mask]
        conf=confs[mask]; cids=class_ids[mask]
        x1=np.clip((cx-w/2)/NN_WIDTH, 0,1)
        y1=np.clip((cy-h/2)/NN_HEIGHT,0,1)
        x2=np.clip((cx+w/2)/NN_WIDTH, 0,1)
        y2=np.clip((cy+h/2)/NN_HEIGHT,0,1)
        boxes=np.stack([x1,y1,x2,y2],axis=1)
        keep=nms(boxes,conf,0.45)
        for i in keep:
            label=COCO_LABELS[int(cids[i])] if int(cids[i])<len(COCO_LABELS) else str(int(cids[i]))
            results.append({'label':label,'conf':round(float(conf[i]),2),
                           'x1':round(float(x1[i]),4),'y1':round(float(y1[i]),4),
                           'x2':round(float(x2[i]),4),'y2':round(float(y2[i]),4),
                           'dist':-1.0})
    except Exception as e:
        print(f"[PARSE] {e}")
    return results

def nms(boxes, scores, iou_thresh=0.45):
    x1,y1,x2,y2=boxes[:,0],boxes[:,1],boxes[:,2],boxes[:,3]
    areas=(x2-x1)*(y2-y1)
    order=scores.argsort()[::-1]
    keep=[]
    while order.size>0:
        i=order[0]; keep.append(i)
        xx1=np.maximum(x1[i],x1[order[1:]]); yy1=np.maximum(y1[i],y1[order[1:]])
        xx2=np.minimum(x2[i],x2[order[1:]]); yy2=np.minimum(y2[i],y2[order[1:]])
        inter=np.maximum(0,xx2-xx1)*np.maximum(0,yy2-yy1)
        iou=inter/(areas[i]+areas[order[1:]]-inter+1e-6)
        order=order[np.where(iou<=iou_thresh)[0]+1]
    return keep

def get_depth_at_box(depth_frame, x1, y1, x2, y2):
    try:
        h,w=depth_frame.shape
        cx=int(((x1+x2)/2)*w); cy=int(((y1+y2)/2)*h)
        pad=4
        patch=depth_frame[max(0,cy-pad):min(h,cy+pad),max(0,cx-pad):min(w,cx+pad)]
        valid=patch[patch>0]
        if len(valid)==0: return -1.0
        dist_m=round(float(np.median(valid))/1000.0,2)
        return dist_m if 0.10<=dist_m<=15.0 else -1.0
    except:
        return -1.0

def scan_proximity(depth_frame, stop_dist):
    """Class-agnostic obstacle check straight off the stereo depth map.
    Looks at the central region of the frame and decides if SOMETHING is
    within stop_dist — independent of YOLO. Returns (obstacle, dist, reason).

    Two ways to trigger:
      1. 'depth'    — a real measured median distance <= stop_dist.
      2. 'tooclose' — the centre is mostly INVALID depth (zeros). On the
                      OAK-D Lite, an object closer than the stereo minimum
                      range (~0.2 m) can't be matched and reads as 0. A large
                      invalid blob dead-centre therefore means something is
                      right against the lens — treat as an obstacle.
    """
    try:
        h, w = depth_frame.shape
        # Central 50% box — the drone's direction of travel.
        x0, x1 = int(w*0.25), int(w*0.75)
        y0, y1 = int(h*0.25), int(h*0.75)
        roi = depth_frame[y0:y1, x0:x1]
        total = roi.size
        if total == 0:
            return (False, -1.0, '')

        valid = roi[roi > 0]
        valid_frac = len(valid) / float(total)

        # Case 1: we can measure depth — use a low percentile (closest things),
        # not the median, because an obstacle may occupy only part of the ROI.
        if len(valid) > 0:
            # 10th percentile in metres = "the close stuff", robust to noise.
            close_m = float(np.percentile(valid, 10)) / 1000.0
            if 0.10 <= close_m <= stop_dist:
                return (True, round(close_m, 2), 'depth')

        # Case 2: dead-zone fill. If most of the centre has NO valid depth,
        # something is too close for stereo to resolve. Require a clear
        # majority so empty-scene (also low-valid) doesn't false-trigger:
        # an empty/far scene still returns *some* depth, whereas a hand on the
        # lens blacks out the centre.
        if valid_frac < 0.35:
            return (True, -1.0, 'tooclose')

        return (False, round(close_m, 2) if len(valid) > 0 else -1.0, '')
    except Exception as e:
        print(f"[PROX] {e}")
        return (False, -1.0, '')

# ── Front camera background capture thread ─────────────────────────────────
def cam_capture_thread():
    global obstacle_blocked
    while True:
        try:
            with dai.Device() as device:
                pipeline = dai.Pipeline(device)

                colorCam = pipeline.create(dai.node.Camera)
                colorCam.build(dai.CameraBoardSocket.CAM_A)
                colorOut = colorCam.requestOutput((1280,720), dai.ImgFrame.Type.NV12, fps=30)
                colorQueue = colorOut.createOutputQueue(maxSize=1, blocking=False)

                leftCam = pipeline.create(dai.node.Camera)
                leftCam.build(dai.CameraBoardSocket.CAM_B)
                leftOut = leftCam.requestOutput((640,480), dai.ImgFrame.Type.GRAY8)

                rightCam = pipeline.create(dai.node.Camera)
                rightCam.build(dai.CameraBoardSocket.CAM_C)
                rightOut = rightCam.requestOutput((640,480), dai.ImgFrame.Type.GRAY8)

                stereo = pipeline.create(dai.node.StereoDepth)
                stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.HIGH_DETAIL)
                stereo.setDepthAlign(dai.CameraBoardSocket.CAM_A)
                stereo.setOutputSize(640,360)
                stereo.setLeftRightCheck(True)
                leftOut.link(stereo.left)
                rightOut.link(stereo.right)
                depthQueue = stereo.depth.createOutputQueue(maxSize=1, blocking=False)

                nn = pipeline.create(dai.node.NeuralNetwork)
                nn.setBlobPath(BLOB_PATH)
                nn.setNumInferenceThreads(2)
                # YOLO is fed from the host (see _color_reader) so the NN can never
                # throttle the camera. Camera stays at 30 fps; YOLO gets ~10 fps.
                try:
                    nnIn = nn.input.createInputQueue(maxSize=1, blocking=False)
                except TypeError:
                    nnIn = nn.input.createInputQueue()
                nnQueue = nn.out.createOutputQueue(maxSize=1, blocking=False)

                pipeline.start()
                print("[CAM] Pipeline started — color + stereo depth + YOLOv8n")

                # Color frames -> cam_color_buf on their own thread so the
                # video stream never waits on depth / proximity / YOLO.
                color_stop = threading.Event()
                _cc = [0, time.time()]
                _nn_t = [0.0]
                NN_FEED_INTERVAL = 0.1   # ~10 fps to YOLO
                def _color_reader(q=colorQueue, stop=color_stop, nnq=nnIn):
                    while not stop.is_set():
                        try:
                            fd = q.tryGet()
                        except Exception:
                            break
                        if fd is None:
                            time.sleep(0.002)
                            continue
                        f = fd.getCvFrame()
                        _cc[0] += 1
                        if time.time() - _cc[1] >= 5:
                            print(f"[CAM-FPS] color from OAK: {_cc[0]/(time.time()-_cc[1]):.1f} fps")
                            _cc[0], _cc[1] = 0, time.time()
                        with cam_buf_lock:
                            cam_color_buf['frame'] = f
                        _now = time.time()
                        if _now - _nn_t[0] >= NN_FEED_INTERVAL:
                            _nn_t[0] = _now
                            try:
                                hh, ww = f.shape[:2]
                                ch = int(round(ww * NN_HEIGHT / NN_WIDTH))
                                y0 = max(0, (hh - ch) // 2)
                                small = cv2.resize(f[y0:y0+ch], (NN_WIDTH, NN_HEIGHT), interpolation=cv2.INTER_AREA)
                                img = dai.ImgFrame()
                                img.setCvFrame(small, dai.ImgFrame.Type.BGR888p)
                                nnq.send(img)
                            except Exception as _e:
                                print(f"[NN-FEED] {_e}")
                threading.Thread(target=_color_reader, daemon=True).start()

                frame_counter = 0
                while True:
                    frame_counter += 1

                    depth_frame = None
                    depth_data = depthQueue.get()
                    if depth_data is not None:
                        depth_frame = depth_data.getCvFrame()

                    # ── Class-agnostic proximity check (every frame) ──
                    # Independent of YOLO: catches hands, walls, anything close.
                    prox_obstacle = False
                    prox_dist = -1.0
                    prox_reason = ''
                    if depth_frame is not None:
                        prox_obstacle, prox_dist, prox_reason = scan_proximity(
                            depth_frame, stop_dist_global
                        )
                        with proximity_lock:
                            proximity['obstacle'] = prox_obstacle
                            proximity['dist'] = prox_dist
                            proximity['reason'] = prox_reason

                    nn_data = nnQueue.get() if frame_counter % 3 == 0 else None
                    if nn_data is not None:
                        try:
                            raw = np.array(nn_data.getFirstTensor())
                            dets = parse_yolov8(raw, CONF_THRESH)
                            if depth_frame is not None:
                                for det in dets:
                                    det['dist'] = get_depth_at_box(
                                        depth_frame,
                                        det['x1'],det['y1'],
                                        det['x2'],det['y2']
                                    )
                            with det_lock:
                                detections.clear()
                                detections.extend(dets)
                            yolo_blocked = any(
                                d['dist'] > 0 and d['dist'] <= stop_dist_global
                                for d in dets
                            )
                            obstacle_blocked = detection_active and (yolo_blocked or prox_obstacle)
                            if dets:
                                print(f"[DET] {len(dets)} obj | closest: " +
                                      str(min((d['dist'] for d in dets if d['dist']>0), default=-1)) + "m" +
                                      f" | blocked={obstacle_blocked}")
                        except Exception as e:
                            print(f"[NN] {e}")

                    # Keep obstacle_blocked responsive on non-YOLO frames:
                    # proximity is computed every frame, so honour it here too.
                    if nn_data is None and depth_frame is not None:
                        with det_lock:
                            yolo_blocked = any(
                                d.get('dist', -1) > 0 and d['dist'] <= stop_dist_global
                                for d in detections
                            )
                        obstacle_blocked = detection_active and (yolo_blocked or prox_obstacle)


        except Exception as e:
            print(f"[CAM] Pipeline error: {e}")
            traceback.print_exc()

        try:
            color_stop.set()
        except NameError:
            pass
        with cam_buf_lock:
            cam_color_buf['frame'] = None
        time.sleep(2)


# ── Camera WebSocket handler (OAK-D Lite) ──────────────────────────────────
async def cam_handler(websocket):
    print(f"[CAM WS] Client: {websocket.remote_address}")
    try:
        last = None
        while True:
            with cam_buf_lock:
                frame = cam_color_buf['frame']
            if frame is not None and frame is not last:
                last = frame
                _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
                img_b64 = base64.b64encode(buf).decode('utf-8')
                with det_lock:
                    dets_copy = list(detections)
                await websocket.send(f"CAM:{img_b64}|DETS:{json.dumps(dets_copy)}")
            await asyncio.sleep(0.033)
    except websockets.exceptions.ConnectionClosed:
        print("[CAM WS] Disconnected")
    except Exception as e:
        print(f"[CAM WS] Error: {e}")
        traceback.print_exc()


# ── Front camera → VPS: GStreamer x264 → SRT, adaptive bitrate ──────────────
frontcam_bitrate_kbps = 0

def cam_rtmp_thread():
    """OAK-D color frames → x264enc (bitrate changed live) → MPEG-TS → SRT → VPS MediaMTX 'frontcam'.
    Controller reads SRT stats every 1s: cuts 30% on loss/RTT spike, +500 kbps after 3 clean seconds."""
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    global frontcam_bitrate_kbps

    W, H, FPS      = 1280, 720, FRONTCAM_FPS
    SRT_LATENCY_MS = 150            # lower to ~150 once you trust the link (≈3-4x ping)
    BR_MIN, BR_MAX = 1500, 10000    # kbps floor / ceiling
    BR_START       = 4000           # kbps at (re)connect
    STEP_UP        = 500            # kbps added after a clean window
    DOWN_FACTOR    = 0.7            # multiply on congestion
    CLEAN_SECS     = 3              # clean seconds before stepping up
    LOSS_LIMIT     = 0.02           # (retransmits + drops) / sent that counts as congestion
    uri            = f"srt://{RTMP_HOST}:8890"
    STAT_KEYS      = ('packets-sent', 'packets-retransmitted', 'packets-sent-dropped', 'rtt-ms', 'bandwidth-mbps')

    def build():
        desc = (
            f'appsrc name=src is-live=true format=time do-timestamp=true block=false '
            f'caps=video/x-raw,format=BGR,width={W},height={H},framerate={FPS}/1 ! '
            f'queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream ! '
            f'videoconvert ! video/x-raw,format=I420 ! '
            f'x264enc name=enc tune=zerolatency speed-preset=ultrafast bitrate={BR_START} '
            f'key-int-max={FPS} vbv-buf-capacity=1000 threads=4 ! '
            f'h264parse config-interval=-1 ! mpegtsmux alignment=7 ! '
            f'srtsink name=sink uri="{uri}" streamid="publish:frontcam" latency={SRT_LATENCY_MS} '
            f'wait-for-connection=false sync=false async=false'
        )
        return Gst.parse_launch(desc)

    def read_stats(sink):
        out = {}
        try:
            s = sink.get_property('stats')
        except Exception:
            return out
        if s is None:
            return out
        for k in STAT_KEYS:
            try:
                if s.has_field(k):
                    out[k] = float(s.get_value(k))
            except Exception:
                pass
        return out

    while True:
        with cam_buf_lock:
            ready = cam_color_buf['frame'] is not None
        if not ready:
            time.sleep(2)
            continue
        if not rtmp_port_reachable():
            print("[SRT] VPS unreachable — retrying in 3s")
            time.sleep(3)
            continue

        pipe = None
        try:
            pipe = build()
            src  = pipe.get_by_name('src')
            enc  = pipe.get_by_name('enc')
            sink = pipe.get_by_name('sink')
            bus  = pipe.get_bus()
            pipe.set_state(Gst.State.PLAYING)
            print(f"[SRT] Streaming front cam → {uri} streamid=publish:frontcam | ABR {BR_MIN}-{BR_MAX} kbps, start {BR_START}")

            br, clean, rtt_base, prev = BR_START, 0, None, None
            pushed, ctl_n = 0, 0
            frontcam_bitrate_kbps = br
            interval, last_sent, last_frame, last_ctl = 1.0 / FPS, 0.0, None, time.time()

            while True:
                msg = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
                if msg:
                    if msg.type == Gst.MessageType.ERROR:
                        err, _dbg = msg.parse_error()
                        print(f"[SRT] Pipeline error: {err.message} — reconnecting in 3s")
                    else:
                        print("[SRT] EOS — reconnecting in 3s")
                    break

                now = time.time()

                # ── ABR controller (1 Hz) ──
                if now - last_ctl >= 1.0:
                    last_ctl = now
                    ctl_n += 1
                    if ctl_n % 5 == 0:
                        print(f"[SRT-FPS] pushing {pushed/5:.1f} fps @ {br} kbps")
                        pushed = 0
                    st = read_stats(sink)
                    if prev is not None and 'packets-sent' in st:
                        d_sent = st['packets-sent'] - prev.get('packets-sent', 0)
                        d_bad  = sum(st.get(k, 0) - prev.get(k, 0) for k in ('packets-retransmitted', 'packets-sent-dropped'))
                        loss   = (d_bad / d_sent) if d_sent > 0 else 0.0
                        rtt    = st.get('rtt-ms', 0.0)
                        if rtt > 0:
                            rtt_base = rtt if rtt_base is None else min(rtt, rtt_base + 0.5)
                        rtt_spike = bool(rtt_base) and rtt > max(rtt_base * 2, rtt_base + 80)
                        new = br
                        if loss > LOSS_LIMIT or rtt_spike:
                            new, clean = max(BR_MIN, int(br * DOWN_FACTOR)), 0
                        else:
                            clean += 1
                            if clean >= CLEAN_SECS:
                                new, clean = min(BR_MAX, br + STEP_UP), 0
                        if new != br:
                            enc.set_property('bitrate', new)
                            print(f"[SRT-ABR] {br} → {new} kbps | rtt {rtt:.0f}ms (base {rtt_base or 0:.0f}) loss {loss*100:.1f}% bw-est {st.get('bandwidth-mbps', 0):.1f}Mbps")
                            br = new
                            frontcam_bitrate_kbps = br
                    prev = st

                # ── push newest frame at FPS, never queue stale ones ──
                if now - last_sent < interval:
                    time.sleep(0.003)
                    continue
                with cam_buf_lock:
                    frame = cam_color_buf['frame']
                if frame is None or frame is last_frame:
                    time.sleep(0.003)
                    continue
                if frame.shape[0] != H or frame.shape[1] != W:
                    time.sleep(0.01)
                    continue
                ret = src.emit('push-buffer', Gst.Buffer.new_wrapped(frame.tobytes()))
                if ret != Gst.FlowReturn.OK:
                    print(f"[SRT] push-buffer returned {ret} — reconnecting in 3s")
                    break
                last_frame, last_sent = frame, now
                pushed += 1

        except Exception as e:
            print(f"[SRT] Error: {e}")
            traceback.print_exc()
        finally:
            if pipe is not None:
                try:
                    pipe.set_state(Gst.State.NULL)
                except Exception:
                    pass
        time.sleep(3)


# ── Down camera → VPS: GStreamer x264 → SRT, adaptive bitrate (front cam has priority) ──
downcam_bitrate_kbps = 0

def downcam_rtmp_thread():
    """downcam_raw frames → resize to STREAM_WxSTREAM_H → x264enc (live bitrate) → MPEG-TS → SRT → VPS 'downcam'.
    ABR ceiling follows the front cam's bitrate so the pilot view always keeps priority on the uplink."""
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    global downcam_bitrate_kbps

    W, H, FPS      = STREAM_W, STREAM_H, DOWNCAM_FPS
    SRT_LATENCY_MS = 150
    BR_MIN, BR_MAX = 400, 3000      # kbps
    BR_START       = 1000
    STEP_UP        = 250
    DOWN_FACTOR    = 0.7
    CLEAN_SECS     = 3
    LOSS_LIMIT     = 0.02
    uri            = f"srt://{RTMP_HOST}:8890"
    STAT_KEYS      = ('packets-sent', 'packets-retransmitted', 'packets-sent-dropped', 'rtt-ms')

    def ceiling():
        fb = globals().get('frontcam_bitrate_kbps', 0) or 0
        if fb == 0 or fb >= 6000: return BR_MAX     # front idle or link healthy
        if fb >= 3000:            return 1500
        return 600                                  # link struggling: pilot view first

    def build():
        desc = (
            f'appsrc name=src is-live=true format=time do-timestamp=true block=false '
            f'caps=video/x-raw,format=BGR,width={W},height={H},framerate={FPS}/1 ! '
            f'queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream ! '
            f'videoconvert ! video/x-raw,format=I420 ! '
            f'x264enc name=enc tune=zerolatency speed-preset=ultrafast bitrate={BR_START} '
            f'key-int-max={FPS} vbv-buf-capacity=1000 threads=2 ! '
            f'h264parse config-interval=-1 ! mpegtsmux alignment=7 ! '
            f'srtsink name=sink uri="{uri}" streamid="publish:downcam" latency={SRT_LATENCY_MS} '
            f'wait-for-connection=false sync=false async=false'
        )
        return Gst.parse_launch(desc)

    def read_stats(sink):
        out = {}
        try:
            s = sink.get_property('stats')
        except Exception:
            return out
        if s is None:
            return out
        for k in STAT_KEYS:
            try:
                if s.has_field(k):
                    out[k] = float(s.get_value(k))
            except Exception:
                pass
        return out

    while True:
        with downcam_lock:
            ready = downcam_raw['frame'] is not None
        if not ready:
            time.sleep(2)
            continue
        if not rtmp_port_reachable():
            print("[SRT-DOWN] VPS unreachable — retrying in 3s")
            time.sleep(3)
            continue

        pipe = None
        try:
            pipe = build()
            src  = pipe.get_by_name('src')
            enc  = pipe.get_by_name('enc')
            sink = pipe.get_by_name('sink')
            bus  = pipe.get_bus()
            pipe.set_state(Gst.State.PLAYING)
            print(f"[SRT-DOWN] Streaming down cam → {uri} streamid=publish:downcam | {W}x{H}@{FPS} ABR {BR_MIN}-{BR_MAX} kbps")

            br, clean, rtt_base, prev = BR_START, 0, None, None
            downcam_bitrate_kbps = br
            pushed, ctl_n = 0, 0
            interval, last_sent, last_frame, last_ctl = 1.0 / FPS, 0.0, None, time.time()

            while True:
                msg = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
                if msg:
                    if msg.type == Gst.MessageType.ERROR:
                        err, _dbg = msg.parse_error()
                        print(f"[SRT-DOWN] Pipeline error: {err.message} — reconnecting in 3s")
                    else:
                        print("[SRT-DOWN] EOS — reconnecting in 3s")
                    break

                now = time.time()

                # ── ABR controller (1 Hz) ──
                if now - last_ctl >= 1.0:
                    last_ctl = now
                    ctl_n += 1
                    if ctl_n % 5 == 0:
                        print(f"[SRT-DOWN-FPS] pushing {pushed/5:.1f} fps @ {br} kbps (cap {ceiling()})")
                        pushed = 0
                    st = read_stats(sink)
                    if prev is not None and 'packets-sent' in st:
                        d_sent = st['packets-sent'] - prev.get('packets-sent', 0)
                        d_bad  = sum(st.get(k, 0) - prev.get(k, 0) for k in ('packets-retransmitted', 'packets-sent-dropped'))
                        loss   = (d_bad / d_sent) if d_sent > 0 else 0.0
                        rtt    = st.get('rtt-ms', 0.0)
                        if rtt > 0:
                            rtt_base = rtt if rtt_base is None else min(rtt, rtt_base + 0.5)
                        rtt_spike = bool(rtt_base) and rtt > max(rtt_base * 2, rtt_base + 80)
                        cap = ceiling()
                        new = br
                        if loss > LOSS_LIMIT or rtt_spike:
                            new, clean = max(BR_MIN, int(br * DOWN_FACTOR)), 0
                        else:
                            clean += 1
                            if clean >= CLEAN_SECS:
                                new, clean = br + STEP_UP, 0
                        new = max(BR_MIN, min(new, cap))
                        if new != br:
                            enc.set_property('bitrate', new)
                            print(f"[SRT-DOWN-ABR] {br} → {new} kbps | rtt {rtt:.0f}ms (base {rtt_base or 0:.0f}) loss {loss*100:.1f}% cap {cap}")
                            br = new
                            downcam_bitrate_kbps = br
                    prev = st

                # ── push newest frame at FPS ──
                if now - last_sent < interval:
                    time.sleep(0.003)
                    continue
                with downcam_lock:
                    frame = downcam_raw['frame']
                if frame is None or frame is last_frame:
                    time.sleep(0.003)
                    continue
                out = frame if (frame.shape[0] == H and frame.shape[1] == W) else cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)
                ret = src.emit('push-buffer', Gst.Buffer.new_wrapped(out.tobytes()))
                if ret != Gst.FlowReturn.OK:
                    print(f"[SRT-DOWN] push-buffer returned {ret} — reconnecting in 3s")
                    break
                last_frame, last_sent = frame, now
                pushed += 1

        except Exception as e:
            print(f"[SRT-DOWN] Error: {e}")
            traceback.print_exc()
        finally:
            if pipe is not None:
                try:
                    pipe.set_state(Gst.State.NULL)
                except Exception:
                    pass
        time.sleep(3)


# ── April tag detection ──────────────────────────
_at_detector = None
if _APRILTAG_AVAILABLE:
    _at_detector = _AprilDetector(
        families=APRILTAG_FAMILY, nthreads=3, quad_decimate=1.0,
        quad_sigma=0.0, refine_edges=1, decode_sharpening=0.25
    )

def detect_apriltags(frame_bgr):
    if _at_detector is None:
        return
    h, w = frame_bgr.shape[:2]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    tags = _at_detector.detect(gray)
    out = []
    for t in tags:
        cx, cy = t.center
        corners = [[round(float(px) / w, 4), round(float(py) / h, 4)]
                   for (px, py) in t.corners]
        out.append({
            'id': int(t.tag_id),
            'cx': round(float(cx) / w, 4),
            'cy': round(float(cy) / h, 4),
            'corners': corners,
            'off_x': round((cx - w / 2) / (w / 2), 4),
            'off_y': round((cy - h / 2) / (h / 2), 4),
        })
    with apriltag_lock:
        apriltag_result['detected'] = len(out) > 0
        apriltag_result['tags'] = out

# ── Downward CSI Camera (IMX219 on CAM0) ─────────
def downcam_capture_thread():
    frame_bytes = DOWNCAM_W * DOWNCAM_H * 3
    gst_cmd = [
        "gst-launch-1.0", "-q",
        "nvarguscamerasrc", "sensor-id=0", "!",
        # FULL-FOV CAPTURE. The IMX219's 1280x720 mode is a CENTER CROP of the
        # sensor — it reads a small window from the middle and discards the
        # wide-angle edges, so a 160-degree lens looks zoomed in. 1640x1232 is
        # the full-sensor (2x2 binned) mode = the complete field of view. We
        # capture that, then nvvidconv downscales to DOWNCAM_W x DOWNCAM_H
        # (1280x960, same 4:3 aspect so no distortion). 1640x1232 supports up
        # to 30fps, so DOWNCAM_FPS (15) is fine. If the sensor won't negotiate
        # this the pipeline won't start ([DOWNCAM] gst-launch exited
        # immediately) — revert by putting width=1280,height=720 back here AND
        # DOWNCAM_W/H back to 1280/720 above.
        f"video/x-raw(memory:NVMM),width=1640,height=1232,framerate={DOWNCAM_FPS}/1", "!",
        "nvvidconv", "!",
        f"video/x-raw,format=BGRx,width={DOWNCAM_W},height={DOWNCAM_H}", "!",
        "videoconvert", "!",
        "video/x-raw,format=BGR", "!",
        "fdsink", "fd=1"
    ]
    while True:
        proc = None
        try:
            print("[DOWNCAM] Launching GStreamer capture (gst-launch -> pipe)")
            proc = subprocess.Popen(
                gst_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=0
            )
            time.sleep(1.0)
            if proc.poll() is not None:
                print("[DOWNCAM] gst-launch exited immediately — camera busy or sensor-id wrong. Retry in 3s")
                time.sleep(3)
                continue
            print("[DOWNCAM] Pipeline open — streaming IMX219 raw frames")

            frame_arr = bytearray(frame_bytes)
            view = memoryview(frame_arr)
            min_interval = 1.0 / DOWNCAM_FPS
            last_encode = 0.0
            detect_interval = 1.0 / 5.0
            last_detect = 0.0

            while True:
                got = 0
                eof = False
                while got < frame_bytes:
                    n = proc.stdout.readinto(view[got:])
                    if not n:
                        eof = True
                        break
                    got += n
                if eof:
                    print("[DOWNCAM] Pipe closed (EOF) — reopening")
                    break

                now = time.time()
                if now - last_encode < min_interval:
                    continue
                last_encode = now

                frame = np.frombuffer(frame_arr, dtype=np.uint8).reshape(
                    (DOWNCAM_H, DOWNCAM_W, 3)).copy()

                if now - last_detect >= detect_interval:
                    last_detect = now
                    try:
                        detect_apriltags(frame)
                    except Exception as e:
                        print(f"[APRILTAG] {e}")

                stream_frame = cv2.resize(frame, (STREAM_W, STREAM_H),
                                          interpolation=cv2.INTER_AREA)
                ok, jbuf = cv2.imencode('.jpg', stream_frame,
                                        [cv2.IMWRITE_JPEG_QUALITY, 60])
                if ok:
                    b64 = base64.b64encode(jbuf).decode('utf-8')
                    with downcam_lock:
                        downcam_raw['frame'] = frame   # full-res for RTMP + AprilTag
                        downcam_jpg['b64'] = b64        # downscaled JPEG for WebSocket
        except Exception as e:
            print(f"[DOWNCAM] Capture error: {e}")
            traceback.print_exc()
        finally:
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try: proc.kill()
                    except Exception: pass
            with downcam_lock:
                downcam_jpg['b64'] = None
                downcam_raw['frame'] = None
        time.sleep(2)

async def downcam_handler(websocket):
    print(f"[DOWNCAM] Client: {websocket.remote_address}")
    try:
        last = None
        frame_interval = 1.0 / DOWNCAM_FPS
        while True:
            with downcam_lock:
                jpg = downcam_jpg['b64']
            if jpg is not None and jpg is not last:
                await websocket.send(f"DOWNCAM:{jpg}")
                last = jpg
            await asyncio.sleep(frame_interval)
    except websockets.exceptions.ConnectionClosed:
        print("[DOWNCAM] Client disconnected")
    except Exception as e:
        print(f"[DOWNCAM] Handler error: {e}")
        traceback.print_exc()

# ── VPS telemetry relay ──────────────────────────
async def vps_telem_relay():
    while True:
        try:
            print("[VPS] Connecting telemetry relay...")

            remote = await websockets.connect(
                VPS_TELEM_URL,
                ping_interval=5, ping_timeout=10
            )

            print("[VPS] Telemetry relay connected")
            vps_relay_state['telem'] = True
            last_mission = 0.0

            while True:
                include = time.time() - last_mission >= mission_interval()
                if include:
                    last_mission = time.time()
                await remote.send(json.dumps(build_telemetry_payload(include_mission=include)))
                await asyncio.sleep(0.05)

        except Exception as e:
            print(f"[VPS] Relay error: {e}")
        vps_relay_state['telem'] = False

        await asyncio.sleep(2)

# ── VPS command relay ────────────────────────────
# The reverse of vps_telem_relay: dials OUT to the VPS command port and HOLDS
# the connection open. Commands the operator sends from the dashboard/pilot
# (browser → VPS:9104 → VPS:9004 → here) arrive on this socket. Each is run
# through the SAME process_command() the local handler uses, and the reply is
# sent back up so the GCS gets acks / MAVLink log remotely too. Reconnects on
# failure. This is what makes ARM/DISARM/velocity work over 5G.
VPS_CMD_URL = os.environ.get("VPS_CMD_URL", "ws://146.190.50.189:9004")

async def vps_cmd_relay():
    while True:
        try:
            print("[VPS] Connecting command relay...")
            remote = await websockets.connect(
                VPS_CMD_URL,
                ping_interval=5, ping_timeout=10
            )
            print("[VPS] Command relay connected")

            await serve_commands(remote, remote)

        except Exception as e:
            print(f"[VPS] Command relay error: {e}")

        # On any disconnect, zero velocity for safety (same as local handler).
        with vel_lock:
            vel_setpoint.update({'vx':0,'vy':0,'vz':0,'yaw_rate':0})
        await asyncio.sleep(2)

# ── Main ─────────────────────────────────────────
async def main():
    bind_host = "127.0.0.1" if SIM_MODE else "0.0.0.0"
    print("="*50)
    print("  Airsent Server — Full MAVLink Control")
    print(f"  Camera  -> ws://0.0.0.0:{CAM_PORT}")
    print(f"  Telem   -> ws://0.0.0.0:{TELEM_PORT}")
    print(f"  Command -> ws://0.0.0.0:{CMD_PORT}")
    print(f"  DownCam -> ws://0.0.0.0:{DOWNCAM_PORT}")
    print("="*50)
    cam_srv      = await websockets.serve(cam_handler,      bind_host, CAM_PORT,      max_size=15_000_000, ping_interval=None)
    telem_srv    = await websockets.serve(telem_handler,    bind_host, TELEM_PORT,    max_size=1_000_000,  ping_interval=None)
    cmd_srv      = await websockets.serve(cmd_handler,      bind_host, CMD_PORT,      max_size=1_000_000,  ping_interval=None)
    downcam_srv  = await websockets.serve(downcam_handler,  bind_host, DOWNCAM_PORT,  max_size=15_000_000, ping_interval=None)
    tasks = [cam_srv.wait_closed(), telem_srv.wait_closed(), cmd_srv.wait_closed(), downcam_srv.wait_closed()]
    if not SIM_MODE:
        tasks.extend([asyncio.create_task(vps_telem_relay()), asyncio.create_task(vps_cmd_relay())])
    else:
        print("[SITL] Local simulation: cameras, hardware ESCs and VPS relays disabled")
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    os.environ.setdefault("DATALINK_PORT", '/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0')
    os.environ.setdefault("DATALINK_MOTOR_POLES", "22")
    esc_monitor = None if SIM_MODE else start_monitor()
    threading.Thread(target=mavlink_reader,         daemon=True).start()
    threading.Thread(target=battery_soc_init,       daemon=True).start()
    threading.Thread(target=cellular_signal_reader, daemon=True).start()
    threading.Thread(target=velocity_sender,        daemon=True).start()
    threading.Thread(target=mission_loop,           daemon=True).start()
    if not SIM_MODE:
        threading.Thread(target=thermal_monitor,        daemon=True).start()
        threading.Thread(target=downcam_capture_thread, daemon=True).start()
        threading.Thread(target=cam_capture_thread,     daemon=True).start()
        threading.Thread(target=cam_rtmp_thread,        daemon=True).start()
        threading.Thread(target=downcam_rtmp_thread,    daemon=True).start()  # ← NEW
    asyncio.run(main())
