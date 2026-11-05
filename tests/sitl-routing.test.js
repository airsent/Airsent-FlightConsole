const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('console-config.js','utf8');
function config(host,enabled,role){const c={window:{},location:{hostname:host,origin:`${host==='console.airsent.tech'?'https':'http'}://${host}${host==='console.airsent.tech'?'':':8000'}`},document:{addEventListener(){}},localStorage:{getItem:key=>key==='airsentMode'?role:(enabled?'1':null)},URL};vm.runInNewContext(source,c);return c.window;}
for(const enabled of [false,true]){const production=config('console.airsent.tech',enabled);assert.equal(production.airsentSimulation,false);assert.equal(production.airsentApiUrl('/telem',true),'wss://console.airsent.tech/telem');assert.equal(production.airsentApiUrl('/cmd',true),'wss://console.airsent.tech/cmd');assert.ok(production.AIRSENT_CONFIG.mapTileUrl.includes('cartocdn'));}
const local=config('localhost',true);assert.equal(local.airsentSimulation,true);assert.equal(local.airsentApiUrl('/telem',true),'ws://localhost:8765/');assert.equal(local.airsentApiUrl('/cmd',true),'ws://localhost:8767/');assert.ok(local.AIRSENT_CONFIG.mapTileUrl.includes('arcgisonline'));
const pilot=fs.readFileSync('pilot.html','utf8');
const actions=[],timers=[];
const ctx={isViewerMode:false,slidePendingAction:'takeoff',closeSlide(){},stopVelSender(){actions.push('stop_velocity')},uiAutopilotState(){actions.push('autopilot_ui')},sendCmd:x=>actions.push(x.action),setTimeout:fn=>timers.push(fn),qsTakeoffAlt:2,E:()=>({}),setMode:()=>{throw new Error('Takeoff must not switch to LOITER');}};
vm.runInNewContext(pilot.slice(pilot.indexOf('function executeSlideAction()'),pilot.indexOf('function qAction(')),ctx);
ctx.executeSlideAction();while(timers.length)timers.shift()();assert.deepEqual(actions,['stop_velocity','autopilot_ui','set_guided','arm','takeoff']);
console.log('PASS: hosted routes unchanged; explicit localhost routing; takeoff stops velocity and stays GUIDED');

const hostedSim=config('console.airsent.tech',false,'simulation');
assert.equal(hostedSim.airsentSimulation,true);
assert.equal(new URL(hostedSim.airsentApiUrl('/telem',true)).pathname,'/simulation/telem');
assert.equal(new URL(hostedSim.airsentApiUrl('/cmd',true)).pathname,'/simulation/cmd');
assert.equal(config('console.airsent.tech',false,'operator').airsentApiUrl('/cmd',true),'wss://console.airsent.tech/cmd');
