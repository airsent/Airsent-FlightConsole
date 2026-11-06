"""Persistent console collaboration. Never connects to an aircraft command channel."""
import asyncio
import copy
import json
import math
import os
from pathlib import Path
from presence_auth import token_role

DEFAULTS = {
 'airsent-failsafe-activation-v1': {},
 'airsent-failsafe-local-drafts-v1': {},
 'airsent_mission_v1': {'home': None, 'waypoints': []},
 'airsent-preflight-manual-v1': {},
 'airsent-parameter-drafts-v1': {},
 'airsent-asc-enabled-v1': False,
}

def validate(key, value):
 if key not in DEFAULTS: raise ValueError('Unknown setting')
 raw=json.dumps(value,allow_nan=False)
 if len(raw)>200000: raise ValueError('Setting is too large')
 if key=='airsent-asc-enabled-v1':
  if type(value) is not bool: raise ValueError('Expected boolean')
 elif not isinstance(value,dict): raise ValueError('Expected object')
 elif key=='airsent-failsafe-activation-v1':
  if len(value)>100 or any(not isinstance(k,str) or len(k)>80 or type(v) is not bool for k,v in value.items()):raise ValueError('Invalid activation')
 elif key=='airsent-preflight-manual-v1':
  if len(value)>200 or any(v not in ('pending','pass','fail','warn','skip') for v in value.values()):raise ValueError('Invalid checklist')
 elif key=='airsent-parameter-drafts-v1':
  if len(value)>1000 or any(type(v) not in (int,float) or not math.isfinite(v) for v in value.values()):raise ValueError('Invalid parameter draft')
 elif key=='airsent_mission_v1':
  points=value.get('waypoints')
  if not isinstance(points,list) or len(points)>1000:raise ValueError('Invalid mission')
  positions=points+([value['home']] if value.get('home') is not None else [])
  for p in positions:
   if not isinstance(p,dict) or type(p.get('lat')) not in (int,float) or type(p.get('lon')) not in (int,float) or not -90<=p['lat']<=90 or not -180<=p['lon']<=180:raise ValueError('Invalid coordinates')
 return value

class State:
 def __init__(self,path):
  self.path=Path(path);self.values=copy.deepcopy(DEFAULTS);self.versions={k:0 for k in DEFAULTS}
  if self.path.exists():
   data=json.loads(self.path.read_text())
   for key,value in data['values'].items():self.values[key]=validate(key,value)
   self.versions.update({k:int(v) for k,v in data['versions'].items() if k in DEFAULTS})
 def snapshot(self,**extra):return {'type':'shared_snapshot','values':self.values,'versions':self.versions,**extra}
 def update(self,role,key,value,version):
  if role!='operator':raise PermissionError('Only operators can change shared settings')
  validate(key,value)
  if type(version) is not int or version!=self.versions[key]:raise ValueError('Conflict: another operator changed this setting. Review it and retry.')
  values=copy.deepcopy(self.values);versions=dict(self.versions);values[key]=value;versions[key]+=1
  self.path.parent.mkdir(parents=True,exist_ok=True)
  temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps({'values':values,'versions':versions},allow_nan=False));temp.replace(self.path)
  self.values,self.versions=values,versions

async def main():
 import websockets
 state=State(os.environ.get('AIRSENT_SHARED_STATE_FILE','/var/lib/airsent-shared/state.json'))
 secret=os.environ['AIRSENT_PRESENCE_SECRET'];clients=set()
 async def handler(ws):
  try:
   auth=json.loads(await asyncio.wait_for(ws.recv(),8));token=auth.get('token','');role=token_role(token,secret)
   if auth.get('type')!='shared_auth' or role not in ('operator','viewer'):
    await ws.close(code=1008,reason='Verified console session required');return
   clients.add(ws);await ws.send(json.dumps(state.snapshot()))
   async for raw in ws:
    try:
     role=token_role(token,secret)
     if role not in ('operator','viewer'):await ws.close(code=1008,reason='Session expired');break
     data=json.loads(raw)
     if data.get('type')!='shared_set':continue
     state.update(role,data.get('key'),data.get('value'),data.get('version'))
    except (PermissionError,ValueError,TypeError,KeyError,OSError) as error:
     await ws.send(json.dumps(state.snapshot(error=str(error))));continue
    await asyncio.gather(*(client.send(json.dumps(state.snapshot())) for client in list(clients)),return_exceptions=True)
  except (ValueError,asyncio.TimeoutError):await ws.close(code=1008,reason='Invalid session')
  finally:clients.discard(ws)
 async with websockets.serve(handler,'127.0.0.1',int(os.environ.get('AIRSENT_SHARED_STATE_PORT','9203')),max_size=256000,origins=['https://console.airsent.tech','http://localhost:8080'],ping_interval=20,ping_timeout=20):
  await asyncio.Future()

if __name__=='__main__':asyncio.run(main())
