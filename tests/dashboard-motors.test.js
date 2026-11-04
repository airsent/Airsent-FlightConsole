const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),path=require('path');
const src=fs.readFileSync(path.join(__dirname,'../telemetry.js'),'utf8');
const els=new Map();const getEl=id=>{if(!els.has(id))els.set(id,{style:{}});return els.get(id);};
const c={getEl,setText:(id,v)=>getEl(id).text=String(v),pushBuffer:()=>{},window:{},lastFreshTelemetryAt:0,losActive:false,
 setDisconnectedState:()=>{},updateSignalIndicators:()=>{},setConnectedState:()=>{},setOnlineStatus:()=>{},setSensorStatus:()=>{},triggerFaultAlert:()=>{},redrawAllCharts:()=>{}};
vm.createContext(c);vm.runInContext(src.slice(src.indexOf('function updateMotors('),src.indexOf('// ─── MAIN APPLY')),c);
vm.runInContext(src.slice(src.indexOf('function applyTelemetry('),src.indexOf('// ─── WEBSOCKET')),c);
const data={telemetry_age:0,pixhawk_connected:false,m1:1500,esc_telemetry:{connected:true,age:0,channels:[{channel:1,connected:true,age:0,output_percent:23.4}]}};
c.applyTelemetry(data);assert.equal(getEl('m1out').text,'23%');assert.equal(getEl('m1pwm').text,'--');assert.equal(getEl('m2out').text,'--');
c.updateMotors({...data,pixhawk_connected:true});assert.equal(getEl('m1pwm').text,'1500');assert.equal(getEl('m1out').text,'23%','reported ESC output, not PWM-derived 50%');
const stale=structuredClone(data);stale.esc_telemetry.channels[0].age=2;c.updateMotors(stale);assert.equal(getEl('m1out').text,'--');
c.updateMotors({pixhawk_connected:true,m1:1600});assert.equal(getEl('m1out').text,'--','no synthetic PWM percentage');
c.updateMotors({});assert.equal(getEl('m1pwm').text,'--');assert.equal(getEl('m1out').text,'--');
console.log('Passed dashboard ESC output with FC offline, separate PWM, stale/missing data, no inferred percentages');
