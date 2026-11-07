"""Read-only health evidence from MAVLink and Jetson sysfs."""
import math
import threading

GPU_PATHS=('/sys/devices/platform/bus@0/17000000.gpu/load','/sys/devices/gpu.0/load','/sys/devices/platform/host1x/gpu/load','/sys/kernel/debug/gpu.0/load')
def gpu_percent(read=None):
 def read_file(path):
  with open(path) as f:return f.read()
 for path in GPU_PATHS:
  try:
   value=float((read or read_file)(path))
   if math.isfinite(value) and 0<=value<=1000:return round(value/10,1)
  except (OSError,ValueError):pass
 return None

class HealthTelemetry:
 def __init__(self):self.lock=threading.Lock();self.flags=None;self.power={}
 def observe(self,msg,now):
  kind=msg.get_type()
  with self.lock:
   if kind=='SYS_STATUS':
    self.flags=(int(msg.onboard_control_sensors_present),int(msg.onboard_control_sensors_enabled),int(msg.onboard_control_sensors_health),now)
    voltage=msg.voltage_battery/1000 if 1000<=msg.voltage_battery<65535 else None
   elif kind=='BATTERY_STATUS':
    if getattr(msg,'id',0)!=0:return
    cells=[v for v in list(msg.voltages)+list(getattr(msg,'voltages_ext',[])) if 0<v<65535]
    voltage=sum(cells)/1000 if cells and sum(cells)>=1000 else None
   else:return
   self.power[kind]={'voltage':voltage,'current':msg.current_battery/100 if msg.current_battery>=0 else None,'battery':msg.battery_remaining if 0<=msg.battery_remaining<=100 else None,'seen':now}
 def snapshot(self,now,connected):
  with self.lock:
   age=now-self.flags[3] if self.flags else None
   fresh=bool(connected and age is not None and 0<=age<5)
   def sensor(mask):
    if not fresh:return 'unknown'
    present,enabled,healthy,_=self.flags
    if present&mask!=mask:return 'not_reported'
    if enabled&mask!=mask:return 'disabled'
    return 'ok' if healthy&mask==mask else 'fault'
   sensors={'source':'SYS_STATUS','age':age,'imu':sensor(3),'mag':sensor(4),'baro':sensor(8),'outputs':sensor(32768),'gps':sensor(32),'prearm':sensor(268435456)}
   power={'source':None,'age':None,'voltage':None,'current':None,'battery':None}
   if connected:
    for source in ('BATTERY_STATUS','SYS_STATUS'):
     sample=self.power.get(source)
     if not sample or not 0<=now-sample['seen']<5:continue
     for key in ('voltage','current','battery'):
      if power[key] is None and sample[key] is not None:power[key]=sample[key]
     if power['source'] is None:power['source']=source;power['age']=now-sample['seen']
   return sensors,power
