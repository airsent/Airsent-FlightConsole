const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../telemetry.js'), 'utf8');
const elements = new Map();
const context = {document: {getElementById(id) {
  if (!elements.has(id)) elements.set(id, {classList:{add(){},remove(){}},innerText:'',textContent:''});
  return elements.get(id);
}}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function getEl('), source.indexOf('function setDisconnectedState(')), context);
const valid = {pixhawk_connected:true, rc_signal:{percent:100,connected:true,age:0,health_age:0,source:'RC_CHANNELS'}};
context.updateSignalIndicators(valid,true);
assert.equal(elements.get('rssiVal').innerText,'100%');
for (const overrides of [
  {connected:false}, {connected:undefined}, {source:'RADIO_STATUS'},
  {age:3}, {age:-1}, {health_age:3}, {health_age:undefined}
]) {
  context.updateSignalIndicators({...valid,rc_signal:{...valid.rc_signal,...overrides}},true);
  assert.equal(elements.get('rssiVal').innerText,'--',JSON.stringify(overrides));
  assert.equal(elements.get('rcTelemetryState').textContent,'NO SIGNAL');
}
context.updateSignalIndicators({...valid,rc_signal:{...valid.rc_signal,percent:null}},true);
assert.equal(elements.get('rssiVal').innerText,'--');
assert.equal(elements.get('rcTelemetryState').textContent,'RC CONNECTED');
context.updateSignalIndicators({...valid,rc_signal:{...valid.rc_signal,percent:null,connected:false}},true);
assert.equal(elements.get('rcTelemetryState').textContent,'NO SIGNAL');
context.updateSignalIndicators(valid,false);
assert.equal(elements.get('rssiVal').innerText,'--');
context.updateSignalIndicators({...valid,pixhawk_connected:false},true);
assert.equal(elements.get('rssiVal').innerText,'--');
context.resetSignalIndicators();
assert.equal(elements.get('rssiVal').innerText,'--');
const legacy = fs.readFileSync(path.join(__dirname,'../script.js'),'utf8');
assert.ok(!/telemetryEls\.(rcTelemetryState|rssiVal)\./.test(legacy), 'Legacy script must not write RC indicators');
console.log('Passed RC display checks: health/source/age gating, stale feed, Pixhawk loss, refresh reset, single writer');
