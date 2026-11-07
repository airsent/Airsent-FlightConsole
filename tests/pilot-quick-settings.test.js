const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('pilot-quick-settings.js','utf8');
const keys=['rth_alt','fence_radius','max_speed','max_alt','takeoff_alt','stop_distance','move_speed','climb_rate','yaw_rate'];
function setup(role='operator'){
 const nodes=new Map(),inputs=new Map(keys.map(k=>[k,{disabled:false,value:'',addEventListener(){}}])),sent=[],listeners={};
 const E=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',disabled:false});return nodes.get(id)};
 const c={window:{airsentDemoIsolation:{isDemo:()=>false}},localStorage:{getItem:()=>role},document:{addEventListener:(k,f)=>listeners[k]=f,querySelector:s=>s.includes('data-pilot-setting')?inputs.get(s.match(/"([^"]+)"/)[1]):{setAttribute(){},style:{}}},E,qsRTH:30,qsMaxSpd:10,qsMaxAlt:50,qsTakeoffAlt:5,sensMov:2,sensClimb:1,sensYaw:.5,qsObstStop:2,updateGeofence(){},setUnits(){},applyDetectionState(){},sendCmd:x=>{sent.push(x);return true},crypto:{randomUUID:()=> 'request-1'},Date,setTimeout:()=>1,clearTimeout(){},setInterval(){}};
 vm.runInNewContext(source,c);listeners.DOMContentLoaded();return {api:c.window.AirsentPilotSettings,c,nodes,inputs,sent};
}
const state={supported:true,revision:5,confirmed:['rth_alt'],values:{rth_alt:30,fence_radius:50,max_speed:10,max_alt:50,takeoff_alt:5,stop_distance:2,move_speed:2,climb_rate:1,yaw_rate:.5,units:'metric',detection:true},fence:{FENCE_ENABLE:0,FENCE_TYPE:3}};
const op=setup();assert.ok(op.inputs.get('rth_alt').disabled);op.api.receive(state,true,false);assert.equal(op.inputs.get('rth_alt').disabled,false);
op.api.submit('rth_alt',40);assert.equal(op.sent.length,1);assert.equal(op.sent[0].revision,5);assert.equal(op.nodes.get('qsRthVal').textContent,'30m','unconfirmed request must not become actual value');
op.api.reply({type:'pilot_setting_result',request_id:'request-1',ok:false,msg:'No aircraft confirmation'});assert.equal(op.nodes.get('qsRthVal').textContent,'30m');assert.match(op.nodes.get('qsApplyStatus').textContent,/NOT APPLIED/);
op.api.submit('rth_alt',40);op.api.reply({type:'pilot_setting_result',request_id:'request-1',ok:true,settings:{...state,revision:6,values:{...state.values,rth_alt:40}}});assert.equal(op.nodes.get('qsRthVal').textContent,'40m');assert.match(op.nodes.get('qsApplyStatus').textContent,/Pixhawk/);
const viewer=setup('viewer');viewer.api.receive(state,true,false);viewer.api.submit('rth_alt',40);assert.equal(viewer.sent.length,0);
op.api.receive(state,true,true);assert.ok(op.inputs.get('rth_alt').disabled);op.api.receive(null,false,false);assert.ok(op.inputs.get('move_speed').disabled);
console.log('PASS: settings require live backend, readback acknowledgement, disarmed operator; failed writes restore confirmed value; viewers cannot write');
