import asyncio
import websockets
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
VPS_TELEM_URL = "ws://165.22.210.225:9001"

try:
    from pupil_apriltags import Detector as _AprilDetector
    _APRILTAG_AVAILABLE = True
except Exception as _e:
    _APRILTAG_AVAILABLE = False
    print(f"[APRILTAG] pupil_apriltags not available ({_e}); detection disabled. "
          f"Run: pip install pupil-apriltags")

# ── Config ──────────────────────────────────────
PIXHAWK_PORT  = '/dev/ttyTHS1'
PIXHAWK_BAUD  = 57600
CAM_PORT      = 8766
TELEM_PORT    = 8765
CMD_PORT      = 8767
DOWNCAM_PORT  = 8768

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
FRONTCAM_FPS  = 20           # pilot-view (OAK-D) RTMP framerate. Was 30.
DOWNCAM_FPS   = 15           # down-cam capture + RTMP + WS preview. Was 30.
                             # NOTE: AprilTag precision-landing detection runs
                             # separately at 5 fps and is NOT affected by this.

APRILTAG_FAMILY = "tag36h11"
RTMP_HOST = "165.22.210.225"
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

mavlink_log = []
mavlink_log_lock = threading.Lock()
MAX_LOG = 20

vel_setpoint = {'vx': 0.0, 'vy': 0.0, 'vz': 0.0, 'yaw_rate': 0.0}
vel_lock = threading.Lock()

obstacle_blocked = False
detection_active = True   # operator can disable obstacle avoidance from any GCS;
                          # synced to all browsers via telemetry. When False,
                          # obstacles are still detected/shown but do NOT block motion.

cam_device_available = False
downcam_device_available = False

cam_color_buf = {'frame': None}
cam_buf_lock  = threading.Lock()

stop_dist_global = 2.0

downcam_raw = {'frame': None}
downcam_jpg = {'b64': None}
downcam_lock = threading.Lock()

apriltag_result = {'detected': False, 'tags': []}
apriltag_lock = threading.Lock()

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
def mavlink_reader():
    global mav_conn, _last_imu_accel, last_mav_report
    while True:
        try:
            print(f"[MAV] Connecting on {PIXHAWK_PORT} @ {PIXHAWK_BAUD}...")
            mav = mavutil.mavlink_connection(
                PIXHAWK_PORT, baud=PIXHAWK_BAUD,
                source_system=255, autoreconnect=True
            )
            hb = mav.wait_heartbeat(timeout=15)
            if hb is None:
                print("[MAV] No heartbeat — retrying")
                time.sleep(3)
                continue
            print(f"[MAV] Pixhawk connected — system {mav.target_system}")
            with mav_lock:
                mav_conn = mav
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
                t = msg.get_type()
                if t == 'BAD_DATA': continue
                _now = time.time()
                last_mav_msg = _now
                if _now - last_mav_report > 5:
                    last_mav_report = _now
                    print(f"[MAV] alive {t} mode={telem.get('flight_mode')} sats={telem.get('sats')} rssi={telem.get('rssi')}")
                if t == 'ATTITUDE':
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
                elif t == 'RC_CHANNELS':
                    telem['rssi'] = round(msg.rssi/2.54)
                elif t == 'HEARTBEAT':
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
            if mav and telem['flight_mode'] == 'GUIDED' and telem['armed']:
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

# ── Command WebSocket handler ─────────────────────
def process_command(raw):
    """Execute a single command (JSON string) and return a reply dict.
    Shared by BOTH the local cmd_handler (port 8767) and vps_cmd_relay
    (VPS:9004) so remote commands run the exact same code as local ones.
    Never raises — always returns a dict ready to json.dumps and send back."""
    global obstacle_blocked, stop_dist_global, detection_active
    try:
        cmd = json.loads(raw)
    except json.JSONDecodeError as e:
        return {'ok': False, 'msg': f'JSON error: {e}'}

    action = cmd.get('action', '')

    with mav_lock:
        mav = mav_conn

    if mav is None:
        return {'ok': False, 'msg': 'No MAVLink connection'}

    try:
        if action == 'set_guided':
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
            arm_drone(mav)
            return {'ok': True, 'msg': 'ARM command sent'}

        elif action == 'disarm':
            disarm_drone(mav)
            return {'ok': True, 'msg': 'DISARM command sent'}

        elif action == 'takeoff':
            alt = float(cmd.get('alt', 2.0))
            send_takeoff(mav, alt)
            return {'ok': True, 'msg': f'TAKEOFF to {alt}m'}

        elif action == 'velocity':
            vx = float(cmd.get('vx', 0))
            vy = float(cmd.get('vy', 0))
            vz = float(cmd.get('vz', 0))
            yr = float(cmd.get('yaw_rate', 0))

            if obstacle_blocked and vx > 0:
                vx = 0.0
                log_mavlink(f"BLOCKED: obstacle in path (fwd={cmd.get('vx',0)} -> 0)")
            else:
                log_mavlink(f"VELOCITY vx={vx:.1f} vy={vy:.1f} vz={vz:.1f} yr={yr:.2f}")

            with vel_lock:
                vel_setpoint.update({'vx':vx,'vy':vy,'vz':vz,'yaw_rate':yr})

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

        elif action == 'set_detection':
            global detection_active
            detection_active = bool(cmd.get('on', True))
            log_mavlink(f"DETECTION {'ENABLED' if detection_active else 'DISABLED'}")
            return {'ok': True, 'msg': f"detection={'on' if detection_active else 'off'}"}

        elif action == 'set_stop_dist':
            stop_dist_global = float(cmd.get('dist', 2.0))
            log_mavlink(f"STOP_DIST updated -> {stop_dist_global}m")
            return {'ok': True, 'msg': f'Stop dist={stop_dist_global}m'}

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
        return {'ok': False, 'msg': str(e)}


async def cmd_handler(websocket):
    """Local command WebSocket (port 8767). Works on the LAN. Delegates the
    actual work to process_command so local and remote behave identically."""
    print(f"[CMD] Client: {websocket.remote_address}")
    try:
        async for raw in websocket:
            reply = process_command(raw)
            await websocket.send(json.dumps(reply))
    except websockets.exceptions.ConnectionClosed:
        print("[CMD] Disconnected")
        with vel_lock:
            vel_setpoint.update({'vx':0,'vy':0,'vz':0,'yaw_rate':0})

# ── Telemetry payload builder ─────────────────────────────────────────────
def build_telemetry_payload():
    now = time.time()
    with mavlink_log_lock:
        log = list(mavlink_log)
    payload = dict(telem)
    payload['mavlink_log'] = log
    with mav_lock:
        px_ok = mav_conn is not None
    payload['pixhawk_connected'] = px_ok
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
    payload['detection_active'] = detection_active
    of_offline  = (_sensor_ts['of']          == 0 or (now - _sensor_ts['of'])          > SENSOR_TIMEOUT)
    rng_offline = (_sensor_ts['rangefinder'] == 0 or (now - _sensor_ts['rangefinder']) > SENSOR_TIMEOUT
                   or of_offline)
    if rng_offline:
        payload['rangefinder'] = 0
    if of_offline:
        payload['of_quality'] = 0
        payload['of_flow_x']  = 0.0
        payload['of_flow_y']  = 0.0
    if _sensor_ts['vib'] == 0 or (now - _sensor_ts['vib']) > SENSOR_TIMEOUT:
        payload.pop('vib_x', None)
        payload.pop('vib_y', None)
        payload.pop('vib_z', None)
    return payload

# ── Telemetry WebSocket ──────────────────────────
async def telem_handler(websocket):
    print(f"[TELEM] Client: {websocket.remote_address}")
    try:
        while True:
            await websocket.send(json.dumps(build_telemetry_payload()))
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
                colorOut = colorCam.requestOutput((1280,720), dai.ImgFrame.Type.BGR888p)
                colorQueue = colorOut.createOutputQueue(maxSize=1, blocking=False)
                colorNN = colorCam.requestOutput((NN_WIDTH,NN_HEIGHT), dai.ImgFrame.Type.BGR888p)

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
                colorNN.link(nn.input)
                nnQueue = nn.out.createOutputQueue(maxSize=1, blocking=False)

                pipeline.start()
                print("[CAM] Pipeline started — color + stereo depth + YOLOv8n")

                frame_counter = 0
                while True:
                    frame_data = colorQueue.get()
                    if frame_data is None:
                        time.sleep(0.01)
                        continue
                    frame = frame_data.getCvFrame()
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

                    with cam_buf_lock:
                        cam_color_buf['frame'] = frame

        except Exception as e:
            print(f"[CAM] Pipeline error: {e}")
            traceback.print_exc()

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


# ── Front camera RTMP → VPS ─────────────────────────────────────────────────
def cam_rtmp_thread():
    W, H, FPS  = 1280, 720, FRONTCAM_FPS
    VPS_RTMP   = f"rtmp://{RTMP_HOST}:{RTMP_PORT}/frontcam"

    while True:
        with cam_buf_lock:
            ready = cam_color_buf['frame'] is not None
        if not ready:
            time.sleep(2)
            continue
        if not rtmp_port_reachable():
            print("[RTMP] VPS RTMP port unreachable — retrying in 3s")
            time.sleep(3)
            continue

        proc = None
        try:
            cmd = [
                'ffmpeg', '-y', '-loglevel', 'error',
                '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                '-s', f'{W}x{H}', '-r', str(FPS),
                '-i', 'pipe:0',
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-tune', 'zerolatency',
                # 720p20 FRONT cam — bitrate raised to 8 Mbps for a sharper
                # pilot view. This costs ~no extra CPU (bitrate != CPU on this
                # board) — only uplink bandwidth (~8 Mbps just for this cam).
                # bufsize kept ~1.5x bitrate so rate control can hold detail
                # through motion. RETUNE FOR 5G: if the pilot view stutters on
                # cellular, drop -b:v/-maxrate first (e.g. 5000k/6000k), keeping
                # bufsize ~1.5x whatever -b:v you pick.
                '-b:v', '8000k', '-maxrate', '9000k', '-bufsize', '12000k',
                '-g', str(FPS),
                '-f', 'flv', VPS_RTMP
            ]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                bufsize=0
            )
            print(f"[RTMP] Streaming front cam → {VPS_RTMP}")

            interval   = 1.0 / FPS
            last_sent  = 0.0
            last_frame = None

            while True:
                if proc.poll() is not None:
                    print("[RTMP] ffmpeg exited — reconnecting in 3s")
                    break

                now = time.time()
                if now - last_sent < interval:
                    time.sleep(0.005)
                    continue

                with cam_buf_lock:
                    frame = cam_color_buf['frame']

                if frame is None:
                    time.sleep(0.05)
                    continue

                if frame is last_frame:
                    time.sleep(0.005)
                    continue

                try:
                    proc.stdin.write(frame.tobytes())
                    last_frame = frame
                    last_sent  = now
                except BrokenPipeError:
                    print("[RTMP] Pipe broken — reconnecting")
                    break

        except Exception as e:
            print(f"[RTMP] Error: {e}")
        finally:
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try: proc.kill()
                    except: pass
        time.sleep(3)


# ── Down camera RTMP → VPS ──────────────────────────────────────────────────
# Reads frames from downcam_raw (full 720p BGR, written by downcam_capture_thread)
# and pushes H.264 RTMP to the VPS. MediaMTX re-serves it as WebRTC via WHEP
# at rtmp://165.22.210.225:1935/downcam → ws://165.22.210.225:8889/downcam/whep
def downcam_rtmp_thread():
    W, H, FPS  = STREAM_W, STREAM_H, 10
    VPS_RTMP   = f"rtmp://{RTMP_HOST}:{RTMP_PORT}/downcam"

    while True:
        # Wait until downcam_capture_thread is producing frames
        with downcam_lock:
            ready = downcam_raw['frame'] is not None
        if not ready:
            time.sleep(2)
            continue
        if not rtmp_port_reachable():
            print("[RTMP-DOWN] VPS RTMP port unreachable — retrying in 3s")
            time.sleep(3)
            continue

        proc = None
        try:
            cmd = [
                'ffmpeg', '-y', '-loglevel', 'warning',
                '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                '-s', f'{W}x{H}', '-r', str(FPS),
                '-i', 'pipe:0',
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-tune', 'zerolatency',
                '-threads', '1',
                # Keep the down cam light so RTMP cannot starve MAVLink/telemetry
                # relays on the same Jetson uplink. AprilTag landing still reads
                # the full raw frame locally, before this encode.
                '-b:v', '700k', '-maxrate', '900k', '-bufsize', '1200k',
                '-g', str(FPS),
                '-f', 'flv', VPS_RTMP
            ]
            # stderr -> our stdout so any ffmpeg complaint is visible in the
            # server log instead of dying silently (was DEVNULL before).
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=None,
                bufsize=0
            )
            print(f"[RTMP-DOWN] Streaming down cam → {VPS_RTMP}")

            interval   = 1.0 / FPS
            last_sent  = 0.0
            last_frame = None

            while True:
                if proc.poll() is not None:
                    print("[RTMP-DOWN] ffmpeg exited — reconnecting in 3s")
                    break

                now = time.time()
                if now - last_sent < interval:
                    time.sleep(0.005)
                    continue

                with downcam_lock:
                    frame = downcam_raw['frame']

                if frame is None:
                    time.sleep(0.05)
                    continue

                if frame is last_frame:
                    time.sleep(0.005)
                    continue

                try:
                    rtmp_frame = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)
                    proc.stdin.write(rtmp_frame.tobytes())
                    last_frame = frame
                    last_sent  = now
                except BrokenPipeError:
                    print("[RTMP-DOWN] Pipe broken — reconnecting")
                    break

        except Exception as e:
            print(f"[RTMP-DOWN] Error: {e}")
        finally:
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try: proc.kill()
                    except: pass
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
                ping_interval=None
            )

            print("[VPS] Telemetry relay connected")

            while True:
                await remote.send(json.dumps(build_telemetry_payload()))
                await asyncio.sleep(0.05)

        except Exception as e:
            print(f"[VPS] Relay error: {e}")

        await asyncio.sleep(2)

# ── VPS command relay ────────────────────────────
# The reverse of vps_telem_relay: dials OUT to the VPS command port and HOLDS
# the connection open. Commands the operator sends from the dashboard/pilot
# (browser → VPS:9104 → VPS:9004 → here) arrive on this socket. Each is run
# through the SAME process_command() the local handler uses, and the reply is
# sent back up so the GCS gets acks / MAVLink log remotely too. Reconnects on
# failure. This is what makes ARM/DISARM/velocity work over 5G.
VPS_CMD_URL = "ws://165.22.210.225:9004"

async def vps_cmd_relay():
    while True:
        try:
            print("[VPS] Connecting command relay...")
            remote = await websockets.connect(
                VPS_CMD_URL,
                ping_interval=None
            )
            print("[VPS] Command relay connected")

            async for raw in remote:
                reply = process_command(raw)
                try:
                    await remote.send(json.dumps(reply))
                except Exception:
                    break

        except Exception as e:
            print(f"[VPS] Command relay error: {e}")

        # On any disconnect, zero velocity for safety (same as local handler).
        with vel_lock:
            vel_setpoint.update({'vx':0,'vy':0,'vz':0,'yaw_rate':0})
        await asyncio.sleep(2)

# ── Main ─────────────────────────────────────────
async def main():
    print("="*50)
    print("  Airsent Server — Full MAVLink Control")
    print(f"  Camera  -> ws://0.0.0.0:{CAM_PORT}")
    print(f"  Telem   -> ws://0.0.0.0:{TELEM_PORT}")
    print(f"  Command -> ws://0.0.0.0:{CMD_PORT}")
    print(f"  DownCam -> ws://0.0.0.0:{DOWNCAM_PORT}")
    print("="*50)
    cam_srv      = await websockets.serve(cam_handler,      "0.0.0.0", CAM_PORT,      max_size=15_000_000, ping_interval=None)
    telem_srv    = await websockets.serve(telem_handler,    "0.0.0.0", TELEM_PORT,    max_size=1_000_000,  ping_interval=None)
    cmd_srv      = await websockets.serve(cmd_handler,      "0.0.0.0", CMD_PORT,      max_size=1_000_000,  ping_interval=None)
    downcam_srv  = await websockets.serve(downcam_handler,  "0.0.0.0", DOWNCAM_PORT,  max_size=15_000_000, ping_interval=None)
    relay_task = asyncio.create_task(vps_telem_relay())
    cmd_relay_task = asyncio.create_task(vps_cmd_relay())

    await asyncio.gather(
        cam_srv.wait_closed(),
        telem_srv.wait_closed(),
        cmd_srv.wait_closed(),
        downcam_srv.wait_closed(),
        relay_task,
        cmd_relay_task,
    )

if __name__ == "__main__":
    threading.Thread(target=mavlink_reader,         daemon=True).start()
    threading.Thread(target=velocity_sender,        daemon=True).start()
    threading.Thread(target=thermal_monitor,        daemon=True).start()
    threading.Thread(target=downcam_capture_thread, daemon=True).start()
    threading.Thread(target=cam_capture_thread,     daemon=True).start()
    threading.Thread(target=cam_rtmp_thread,        daemon=True).start()
    threading.Thread(target=downcam_rtmp_thread,    daemon=True).start()  # ← NEW
    asyncio.run(main())
