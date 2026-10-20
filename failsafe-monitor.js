/* Display-only observations. Sensor readings never prove failsafe configuration.
   Receipt age describes dashboard frames, not freshness of individual sensors. */
(()=>{
'use strict';
let frame=null;
const $=id=>document.getElementById(id);
const numeric=value=>typeof value==='number'&&Number.isFinite(value);
function draw(){
 if(!$('fsSource'))return;
 const age=frame?Math.max(0,Date.now()-frame.receivedAt):Infinity;
 const fresh=age<=4000;
 $('fsSource').textContent=frame?(frame.source==='demo'?'DASHBOARD DEMO':'TELEMETRY MIRROR'):'READ-ONLY TRIAL';
 $('fsVehicleState').textContent=!frame?'WAITING FOR DASHBOARD':!fresh?'DASHBOARD DATA STALE':frame.source==='demo'?'SIMULATED VEHICLE':frame.values.pixhawk_connected===false?'FC REPORTED OFFLINE':'FC STATUS UNVERIFIED';
 $('fsVehicleState').style.color=fresh?'var(--cyan)':'var(--amber)';
 $('fsVoltage').textContent=fresh&&numeric(frame.values.voltage)&&frame.values.voltage>=0?frame.values.voltage.toFixed(1)+' V':'—';
 $('fsGps').textContent=fresh&&typeof frame.values.fix_type==='string'?frame.values.fix_type:'—';
 $('fsAge').textContent=frame?(age/1000).toFixed(1)+' s'+(!fresh?' · STALE':''):'—';
}
addEventListener('airsent-review-frame',event=>{frame=event.detail;draw()});
addEventListener('DOMContentLoaded',()=>{window.AirsentReviewBridge?.request();draw();setInterval(draw,500)});
})();
