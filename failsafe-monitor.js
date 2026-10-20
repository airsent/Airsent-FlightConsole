/* Display-only observations. Sensor readings never prove failsafe configuration.
   Receipt age describes dashboard frames, not freshness of individual sensors. */
(()=>{
'use strict';
let frame=null,selected=1;
const $=id=>document.getElementById(id);
const numeric=value=>typeof value==='number'&&Number.isFinite(value);
function draw(){
 if(!$('fsSource'))return;
 const age=frame?Math.max(0,Date.now()-frame.receivedAt):Infinity;
 const fresh=age<=4000;
 if($('fsClock'))$('fsClock').textContent=new Date().toLocaleTimeString('en-GB');
 if($('fsUpdated'))$('fsUpdated').textContent=frame?new Date(frame.receivedAt).toLocaleTimeString('en-GB'):'--:--:--';
 if($('fsObservation')){
 const v=fresh?frame.values:null;
 let text=!v?'Waiting for fresh dashboard data.':'No dedicated sensor evidence mapped for this rule.';
 if(v&&selected===12)text=numeric(v.rangefinder)?'Distance: '+v.rangefinder.toFixed(2)+' m · validity / sensor age not available.':'Rangefinder measurement not supplied.';
 if(v&&selected===14)text=numeric(v.of_quality)?'Flow quality: '+v.of_quality+' / 255 · active EKF source / sensor age unknown.':'Optical-flow quality not supplied.';
 if(v&&selected===15)text=typeof v.down_cam_active==='boolean'?'Camera flag: '+(v.down_cam_active?'active':'inactive')+' · frame freshness unverified.':'Down-camera flag not supplied.';
 if(v&&selected===13)text=numeric(v.jetson_temp)?'Jetson temperature: '+v.jetson_temp.toFixed(1)+' °C · thresholds unverified.':'Temperature not supplied.';
 $('fsObservation').textContent=(frame?.source==='demo'&&v?'SIMULATED · ':'')+text;
 }
 $('fsSource').textContent=frame?(frame.source==='demo'?'DEMO':'TELEMETRY'):'TRIAL';
 $('fsVehicleState').textContent=!frame?'WAITING':!fresh?'STALE':frame.source==='demo'?'SIMULATED':frame.values.pixhawk_connected===false?'FC OFFLINE':'UNVERIFIED';
 $('fsVehicleState').dataset.severity=!frame?'unknown':!fresh?'warning':frame.source==='demo'?'info':frame.values.pixhawk_connected===false?'fault':'unknown';
 $('fsVoltage').textContent=fresh&&numeric(frame.values.voltage)&&frame.values.voltage>=0?frame.values.voltage.toFixed(1)+' V':'—';
 $('fsGps').textContent=fresh&&typeof frame.values.fix_type==='string'?frame.values.fix_type:'—';
 $('fsAge').textContent=frame?(age/1000).toFixed(1)+' s'+(!fresh?' · STALE':''):'—';
}
addEventListener('airsent-review-selection',event=>{selected=event.detail.id;draw()});
addEventListener('airsent-review-frame',event=>{frame=event.detail;draw()});
addEventListener('DOMContentLoaded',()=>{window.AirsentReviewBridge?.request();draw();setInterval(draw,500)});
})();
