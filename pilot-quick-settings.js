/* No optimistic aircraft settings: render confirmed telemetry, then await readback. */
(() => {
 const fields={rth_alt:['qsRthVal','m'],fence_radius:['qsGeoVal','m'],max_speed:['qsSpdVal',' m/s'],max_alt:['qsAltVal','m'],takeoff_alt:['qsTakeoffVal','m'],stop_distance:['qsObstVal','m'],move_speed:['qsMovVal',' m/s'],climb_rate:['qsClimbVal',' m/s'],yaw_rate:['qsYawVal',' r/s']};
 let state=null,connected=false,armed=false,pending=null,timer,ready=false,status,lastFrame=0,editing=null,messageUntil=0;
 const operator=()=>['operator','actual','real','simulation'].includes(localStorage.getItem('airsentMode'))&&!window.airsentDemoIsolation?.isDemo();
 const enabled=()=>ready&&operator()&&connected&&!armed&&!pending;
 const report=(text,sticky=false)=>{if(sticky)messageUntil=Date.now()+6000;if(status)status.textContent=text};
 function paint(){
  if(!ready)return;
  for(const [key,[id,suffix]] of Object.entries(fields)){
   const input=document.querySelector(`[data-pilot-setting="${key}"]`),value=state?.values[key];
   input.disabled=!enabled()||typeof value!=='number';
   if(!(editing===key&&enabled())){
     E(id).textContent=typeof value==='number'?value+suffix:'--';
     if(typeof value==='number')input.value=value;
   }
  }
  for(const id of ['uMetric','uImperial'])E(id).disabled=!enabled();
  const detection=document.querySelector('[data-pilot-detection]');
  if(detection){detection.setAttribute('aria-disabled',String(!enabled()));detection.style.opacity=enabled()?'1':'.5';}
  if(state){
   const v=state.values;qsRTH=v.rth_alt??0;qsMaxSpd=v.max_speed??0;qsMaxAlt=v.max_alt??0;
   qsTakeoffAlt=v.takeoff_alt;sensMov=v.move_speed;sensClimb=v.climb_rate;sensYaw=v.yaw_rate;qsObstStop=v.stop_distance;
   updateGeofence(v.fence_radius??0);setUnits(v.units);applyDetectionState(v.detection);
   const fence=state.fence;const boundaries=[[1,'altitude'],[2,'radius'],[4,'polygons'],[8,'minimum altitude']].filter(([bit])=>(fence?.FENCE_TYPE&bit)!==0).map(([,name])=>name);E('qsFenceState').textContent=fence?.FENCE_ENABLE===1?`Aircraft fence enabled: ${boundaries.join(', ')||'boundaries unverified'}`:'Aircraft fence inactive or unverified; radius/altitude limits may not be enforced.';
  }
 }
 function receive(snapshot,isConnected,isArmed){
  connected=isConnected;armed=isArmed;lastFrame=Date.now();
  if(snapshot?.supported===true)state=snapshot;else state=null;
  paint();
  if(!pending&&Date.now()>=messageUntil&&!editing)report(!connected?'OFFLINE — changes disabled':!state?'Jetson settings update required':armed?'DISARM TO CHANGE SETTINGS':'Confirmed values · synchronized through aircraft telemetry');
 }
 function submit(key,value){
  if(!enabled()||!state){paint();return false;}
  messageUntil=0;const id=crypto.randomUUID();pending={id,key,value};paint();report('Applying — waiting for confirmation…');
  if(!sendCmd({action:'pilot_setting',key,value,revision:state.revision,request_id:id})){
   pending=null;paint();report('NOT APPLIED — command connection unavailable',true);return false;
  }
  timer=setTimeout(()=>{pending=null;paint();report('NOT CONFIRMED — check connection and current value before retrying',true);},7000);
  return true;
 }
 function reply(data){
  if(data.type!=='pilot_setting_result'||!pending||data.request_id!==pending.id)return;
  const key=pending.key;clearTimeout(timer);pending=null;
  if(data.settings?.supported)state=data.settings;
  paint();report(data.ok?'APPLIED — confirmed by '+(data.settings.confirmed.includes(key)?'Pixhawk':'aircraft backend'):'NOT APPLIED — '+data.msg,true);
 }
 window.AirsentPilotSettings={receive,reply,submit};
 document.addEventListener('DOMContentLoaded',()=>{
  status=E('qsApplyStatus');ready=true;
  for(const [key,[id,suffix]] of Object.entries(fields)){
   const input=document.querySelector(`[data-pilot-setting="${key}"]`);
   input.addEventListener('input',()=>{editing=key;E(id).textContent=input.value+suffix+' · unsaved';report('Release slider to apply');});
   input.addEventListener('change',()=>{const value=Number(input.value);editing=null;submit(key,value)});
  }
  E('uMetric').onclick=()=>submit('units','metric');E('uImperial').onclick=()=>submit('units','imperial');
  receive(null,false,false);
  setInterval(()=>{if(connected&&Date.now()-lastFrame>2500){connected=false;paint();report('STALE TELEMETRY — changes disabled');}},1000);
 });
})();
