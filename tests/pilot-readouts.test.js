const assert=require('assert'),{model}=require('../pilot-readouts');
const d={telemetry_age:0,pixhawk_connected:true,power_status:{age:0,voltage:null,current:0,battery:0},voltage:12.6,fix_type:'NO FIX',sats:0,hdop:99.99,sensor_ages:{vib:0},vib_x:.01,vib_y:.02,vib_z:.03,cam_temp:56};
assert.equal(model(d).voltage,null);assert.equal(model(d).current,0);assert.equal(model(d).battery,0);assert.equal(model(d).hdop,null);assert.equal(model(d).vib,.03);assert.equal(model(d).wind,null);
assert.equal(model({...d,power_status:{age:0,voltage:22.4}}).voltage,22.4);
assert.equal(model(d,3).vib,null);assert.equal(model(d,3).temp,null);assert.equal(model(d,3).current,null);
assert.equal(model({...d,pixhawk_connected:false}).current,null);
assert.equal(model({...d,fix_type:'3D FIX',sats:10,hdop:1.2}).hdop,1.2);
assert.equal(model({...d,wind:{age:0,speed:0,direction:20}}).wind.speed,0);
console.log('PASS: Pilot uses valid measured power, preserves zero, rejects stale/missing data, uses measured vibration and requires actual wind data');
