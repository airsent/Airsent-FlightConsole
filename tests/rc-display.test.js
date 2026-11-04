const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../telemetry.js'),'utf8');
const elements=new Map();
const context={document:{getElementById(id){
 if(!elements.has(id)) {const classes=new Set();elements.set(id,{classList:{add(...s){s.forEach(x=>classes.add(x));},remove(...s){s.forEach(x=>classes.delete(x));},contains:s=>classes.has(s)},innerText:'',textContent:''});}
 return elements.get(id);
}}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function getEl('),source.indexOf('function setDisconnectedState(')),context);
const valid={pixhawk_connected:true,rc_signal:{percent:null,connected:false,input_present:true,age:0,health_age:0,source:'RC_CHANNELS'}};
context.updateSignalIndicators(valid,true);
assert.equal(elements.get('rcTelemetryState').textContent,'CONNECTED');
assert.ok(elements.get('rcTelemetryState').classList.contains('signal-good'));
assert.equal(elements.get('rssiVal').innerText,'--','unknown strength must not be invented');
for(const [percent,label] of [[0,'WEAK'],[39,'WEAK'],[40,'MODERATE'],[79,'MODERATE'],[80,'STRONG'],[100,'STRONG'],[255,'--']]) {
 context.updateSignalIndicators({...valid,rc_signal:{...valid.rc_signal,percent}},true);
 assert.equal(elements.get('rssiVal').innerText,label);
 assert.equal(elements.get('rcTelemetryState').textContent,'CONNECTED');
}
for(const overrides of [{input_present:false},{input_present:undefined},{source:'RADIO_STATUS'},{age:3},{age:-1},{health_age:3},{health_age:undefined}]) {
 context.updateSignalIndicators({...valid,rc_signal:{...valid.rc_signal,percent:100,...overrides}},true);
 assert.equal(elements.get('rcTelemetryState').textContent,'DISCONNECTED',JSON.stringify(overrides));
 assert.ok(elements.get('rcTelemetryState').classList.contains('signal-none'));
 assert.equal(elements.get('rssiVal').innerText,'--');
}
context.updateSignalIndicators(valid,true); // receiver reconnects without a refresh
assert.equal(elements.get('rcTelemetryState').textContent,'CONNECTED');
context.updateSignalIndicators(valid,false);
assert.equal(elements.get('rcTelemetryState').textContent,'DISCONNECTED');
context.updateSignalIndicators({...valid,pixhawk_connected:false},true);
assert.equal(elements.get('rcTelemetryState').textContent,'DISCONNECTED');
context.resetSignalIndicators();assert.equal(elements.get('rcTelemetryState').textContent,'DISCONNECTED');
const legacy=fs.readFileSync(path.join(__dirname,'../script.js'),'utf8');
assert.ok(!/telemetryEls\.(rcTelemetryState|rssiVal)\./.test(legacy));
console.log('Passed RC connection colors, unplug/reconnect, stale data, quality thresholds, unknown RSSI, and single writer checks');
for (const [link,label,quality] of [
 [{type:'wifi',ssid:'TheChair',percent:84},'WIFI','84%'],
 [{type:'cellular',technology:'5gnr',percent:75},'5G','75%'],
 [{type:'cellular',technology:'lte, 5gnr',percent:75},'5G','75%'],
 [{type:'cellular',technology:'lte',percent:60},'4G','60%'],
 [{type:'cellular',technology:null,percent:null},'CELLULAR','--'],
 [{type:'ethernet',percent:null},'ETHERNET','--']
]) {
 context.updateSignalIndicators({pixhawk_connected:false,network_link:{...link,connected:true,age:0}},true);
 assert.equal(elements.get('networkSignalState').textContent,label);
 assert.ok(elements.get('networkSignalState').classList.contains('signal-good'));
 assert.equal(elements.get('cellularSignalVal').innerText,quality);
}
for (const patch of [{age:6},{age:-1},{connected:false}]) {
 context.updateNetworkIndicator({network_link:{type:'wifi',connected:true,age:0,...patch}},true);
 assert.equal(elements.get('networkSignalState').textContent,'DISCONNECTED');
}
context.updateNetworkIndicator({network_link:{type:'wifi',connected:true,age:0}},false);
assert.equal(elements.get('networkSignalState').textContent,'DISCONNECTED');
console.log('Passed Wi-Fi/5G/4G/unknown technology, unknown quality, stale feed and independent Pixhawk network checks');
