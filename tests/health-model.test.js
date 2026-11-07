const assert=require('assert/strict'),{model}=require('../health-model');
const frame={telemetry_age:0,pixhawk_connected:true,roll:3,alt:1,yaw:0,m1:1100,m2:1100,m3:1100,m4:1100,voltage:0,current:.5,battery:96,rangefinder:.31,rangefinder_agl:.01,of_quality:61,rc_signal:{input_present:true,source:'RC_CHANNELS',age:0,health_age:0,percent:null},network_link:{connected:true,type:'wifi',age:0,percent:100},esc_telemetry:{connected:false,age:null}};
let h=model(frame);assert.equal(h.escCount,0);assert.ok(h.rcConnected,'unknown RSSI must not imply disconnected receiver');assert.equal(h.rcPercent,null);assert.equal(h.voltage,null);assert.equal(h.flow,24);assert.equal(h.type,'WIFI');assert.equal(h.strength,'STRONG');assert.equal(h.state('imu'),'warn','attitude alone cannot prove IMU health');
h=model({...frame,sensor_health:{age:0,imu:'fault',outputs:'disabled',mag:'ok'}});assert.equal(h.state('imu'),'err');assert.equal(h.reported('outputs'),false);
h=model({...frame,esc_telemetry:{connected:true,age:0,channels:[1,2,3,4].map(channel=>({channel,connected:true,age:0,rpm:0}))}});assert.equal(h.escCount,4,'stopped motors still have live ESC telemetry');
for(const age of [2,-1,null])assert.equal(model({...frame,esc_telemetry:{connected:true,age,channels:[{channel:1,connected:true,age:0}]}}).escCount,0);
h=model({...frame,power_status:{age:0,voltage:22.1,current:.5,battery:96}});assert.equal(h.voltage,22.1);
h=model({...frame,voltage:22,power_status:{age:6,voltage:22,current:.5,battery:96}});assert.equal(h.voltage,null,'stale authoritative battery report must not fall back to cached legacy data');
h=model({...frame,pixhawk_connected:false});assert.equal(h.rcConnected,false);assert.equal(h.flow,null);
assert.equal(model({...frame,telemetry_age:3}).feed,false);
assert.equal(model({...frame,network_link:{connected:true,type:'cellular',technology:'5gnr',age:0,percent:50}}).strength,'MODERATE');
console.log('PASS: health evidence, RC without RSSI, ESC telemetry vs PWM, raw flow scale, fresh power, network selection and stale rejection');

assert.equal(model({...frame,ground_computer:{connected:true,age:0,cpu:0}}).ground.cpu,0);
assert.equal(model({...frame,ground_computer:{connected:true,age:6,cpu:20}}).ground,null);
assert.equal(model({...frame,rtk_base:{connected:true,age:0,fix_type:'RTK FIX',sats:20}}).rtkBase.sats,20);
