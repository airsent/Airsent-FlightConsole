const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),path=require('path');
const src=fs.readFileSync(path.join(__dirname,'../telemetry.js'),'utf8');
const els=new Map();const getEl=id=>{if(!els.has(id))els.set(id,{style:{}});return els.get(id);};
const c={getEl,setText:(id,v)=>getEl(id).text=String(v),pushBuffer:()=>{},window:{},lastFreshTelemetryAt:0,losActive:false,
 setDisconnectedState:()=>{},updateSignalIndicators:()=>{},setConnectedState:()=>{},setOnlineStatus:()=>{},setSensorStatus:()=>{},triggerFaultAlert:()=>{},redrawAllCharts:()=>{}};
vm.createContext(c);vm.runInContext(src.slice(src.indexOf('function updateMotors('),src.indexOf('// ─── MAIN APPLY')),c);
vm.runInContext(src.slice(src.indexOf('function applyTelemetry('),src.indexOf('// ─── WEBSOCKET')),c);
const data={telemetry_age:0,pixhawk_connected:true,m1:1520,m2:1940,m3:1100,m4:0,mot_pwm:{min:1100,max:1940},esc_telemetry:{connected:true,age:0,channels:[{channel:1,connected:true,age:0,output_percent:3.0,rpm:3664}]}};
c.updateMotors(data);
assert.equal(getEl('m1out').text,'50%','command PWM across MOT_PWM_MIN..MAX, not the Datalink output field (3 % at full throttle on the ZD650)');
assert.equal(getEl('m2out').text,'100%');assert.equal(getEl('m3out').text,'0%');assert.equal(getEl('m4out').text,'--','no PWM = no value');
assert.equal(getEl('m1rpm').text,'3,664');assert.equal(getEl('m1pwm').text,'1520');
c.updateMotors({...data,pixhawk_connected:false});assert.equal(getEl('m1out').text,'--','no Pixhawk = no command');assert.equal(getEl('m1pwm').text,'--');
c.updateMotors({pixhawk_connected:true,m1:1520});assert.equal(getEl('m1out').text,'50%','default 1100-1940 range when not reported');
const stale=structuredClone(data);stale.esc_telemetry.channels[0].age=2;c.updateMotors(stale);assert.equal(getEl('m1rpm').text,'--','stale ESC rpm hidden');
c.updateMotors({});assert.equal(getEl('m1pwm').text,'--');assert.equal(getEl('m1out').text,'--');
console.log('Passed dashboard motor output from command PWM range, ESC rpm freshness, missing data');
