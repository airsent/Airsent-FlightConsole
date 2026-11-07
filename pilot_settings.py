"""Confirmed ArduCopter quick settings and persisted companion command preferences."""
import json
import math
import threading
import time
from pathlib import Path

PARAMETERS={'rth_alt':('RTL_ALT',100,10,120),'fence_radius':('FENCE_RADIUS',1,20,500),
            'max_speed':('WPNAV_SPEED',100,2,20),'max_alt':('FENCE_ALT_MAX',1,10,120)}
ALIASES={'rth_alt':(('RTL_ALT_M',1),('RTL_ALT',100)),'max_speed':(('WP_SPD',1),('WPNAV_SPEED',100))}
DEFAULTS={'units':'metric','takeoff_alt':5.0,'move_speed':2.0,'climb_rate':1.0,'yaw_rate':.5,'stop_distance':2.0,'detection':True}
RANGES={'takeoff_alt':(1,50),'move_speed':(.5,8),'climb_rate':(.2,3),'yaw_rate':(.1,1.5),'stop_distance':(.5,5)}

class PilotSettings:
 def __init__(self,path,clock=time.monotonic):
  self.path=Path(path);self.clock=clock;self.condition=threading.Condition();self.writer=threading.Lock();self.params={};self.revision=0;self.generation=0
  self.values=dict(DEFAULTS)
  if self.path.exists():
   saved=json.loads(self.path.read_text())
   for key,value in saved.items():self.values[key]=self.validate(key,value)
 def validate(self,key,value):
  if key=='units':
   if value not in ('metric','imperial'):raise ValueError('Unknown units')
   return value
  if key=='detection':
   if type(value) is not bool:raise ValueError('Detection must be on or off')
   return value
  bounds=PARAMETERS[key][2:] if key in PARAMETERS else RANGES.get(key)
  if not bounds or type(value) not in (int,float) or not math.isfinite(value) or not bounds[0]<=value<=bounds[1]:raise ValueError('Setting outside allowed range')
  return float(value)
 def candidates(self,key):return ALIASES.get(key,((PARAMETERS[key][0],PARAMETERS[key][1]),))
 def resolve(self,key):
  for name,scale in self.candidates(key):
   entry=self.params.get(name)
   if entry and self.clock()-entry['seen']<45:return name,scale,entry
  return None,None,None
 def parameter_names(self):return list(dict.fromkeys([name for key in PARAMETERS for name,scale in self.candidates(key)]+['FENCE_ENABLE','FENCE_TYPE']))
 def reset(self):
  with self.condition:self.params.clear();self.revision+=1;self.generation+=1;self.condition.notify_all()
 def observe(self,msg):
  name=msg.param_id.decode('ascii').rstrip('\x00') if isinstance(msg.param_id,bytes) else str(msg.param_id).rstrip('\x00')
  if name not in self.parameter_names():return
  if not math.isfinite(float(msg.param_value)):return
  with self.condition:
   previous=self.params.get(name)
   if previous is None or previous['value']!=float(msg.param_value):self.revision+=1
   self.params[name]={'value':float(msg.param_value),'type':int(msg.param_type),'seen':self.clock()};self.condition.notify_all()
 def snapshot(self,connected=True):
  with self.condition:
   values=dict(self.values);confirmed=[];names={}
   for key in PARAMETERS:
    name,scale,entry=self.resolve(key) if connected else (None,None,None)
    valid=entry is not None
    if valid:names[key]=name
    values[key]=entry['value']/scale if valid else None
    if valid:confirmed.append(key)
   fence={name:self.params[name]['value'] if connected and name in self.params and self.clock()-self.params[name]['seen']<45 else None for name in ('FENCE_ENABLE','FENCE_TYPE')}
   return {'supported':True,'revision':self.revision,'values':values,'confirmed':confirmed,'parameters':names,'fence':fence}
 def apply(self,mav,key,value,revision,armed=False,timeout=4):
  value=self.validate(key,value)
  if armed:raise ValueError('Land and disarm before changing quick settings')
  if not self.writer.acquire(blocking=False):raise ValueError('Another setting is being applied; retry after confirmation')
  try:
   with self.condition:
    if revision!=self.revision:raise ValueError('Settings changed on another device; review current values and retry')
    if key in PARAMETERS:
     name,scale,entry=self.resolve(key)
     if entry is None or self.clock()-entry['seen']>=45:raise ValueError('Aircraft parameter not available or stale')
     target=value*scale;sent=self.clock();generation=self.generation
     mav.mav.param_set_send(mav.target_system,mav.target_component,name.encode(),target,entry['type'])
     end=self.clock()+timeout;different=False;last_read=sent
     mav.mav.param_request_read_send(mav.target_system,mav.target_component,name.encode(),-1)
     while True:
      if generation!=self.generation:raise ValueError('Aircraft reconnected; setting not confirmed')
      entry=self.params.get(name)
      if entry and entry['seen']>sent:
       if math.isclose(entry['value'],target,rel_tol=1e-5,abs_tol=.01):return self.snapshot()
       different=True
      remaining=end-self.clock()
      if remaining<=0:raise ValueError('Aircraft readback differs from requested value' if different else 'No aircraft confirmation; setting not confirmed')
      if self.clock()-last_read>=.5:
       mav.mav.param_request_read_send(mav.target_system,mav.target_component,name.encode(),-1);last_read=self.clock()
      self.condition.wait(min(remaining,.5))
    values={**self.values,key:value};self.path.parent.mkdir(parents=True,exist_ok=True)
    temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(values,allow_nan=False));temp.replace(self.path)
    self.values=values;self.revision+=1;return self.snapshot()
  finally:self.writer.release()
 def request_parameters(self,mav):
  for name in self.parameter_names():
   mav.mav.param_request_read_send(mav.target_system,mav.target_component,name.encode(),-1)
 def clamp_velocity(self,vx,vy,vz,yaw_rate):
  values=[vx,vy,vz,yaw_rate]
  if any(not math.isfinite(x) for x in values):raise ValueError('Velocity must be finite')
  with self.condition:
   maximum=self.values['move_speed'];speed=math.hypot(vx,vy)
   if speed>maximum:vx*=maximum/speed;vy*=maximum/speed
   climb=self.values['climb_rate'];yaw=self.values['yaw_rate']
   return vx,vy,max(-climb,min(climb,vz)),max(-yaw,min(yaw,yaw_rate))
