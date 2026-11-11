/* Measurements are evidence, not guesses from default UI values. */
(function(root){
 const number=x=>typeof x==='number'&&Number.isFinite(x);
 const fresh=(age,limit)=>number(age)&&age>=0&&age<limit;
 function model(d){
  const feed=fresh(d.telemetry_age,2),px=feed&&d.pixhawk_connected===true;
  const sensor=d.sensor_health||{},sensorFresh=px&&fresh(sensor.age,5);
  const state=key=>sensorFresh&&sensor[key]==='ok'?'ok':sensorFresh&&sensor[key]==='fault'?'err':'warn';
  const reported=key=>sensorFresh&&['ok','fault'].includes(sensor[key]);
  const rc=d.rc_signal||{},rcFresh=px&&rc.source==='RC_CHANNELS'&&fresh(rc.age,3)&&fresh(rc.health_age,3);
  const rcConnected=rcFresh&&rc.input_present===true;
  const esc=d.esc_telemetry||{},escFresh=feed&&esc.connected===true&&fresh(esc.age,2);
  const channels=[1,2,3,4].map(id=>esc.channels?.find(x=>x.channel===id)).filter(c=>escFresh&&c?.connected===true&&fresh(c.age,2));
  const hasPower=d.power_status!==null&&typeof d.power_status==='object';
  const power=d.power_status||{},powerFresh=px&&fresh(power.age,5);
  const voltage=powerFresh&&number(power.voltage)&&power.voltage>=1?power.voltage:!hasPower&&px&&number(d.voltage)&&d.voltage>=1&&d.voltage<65?d.voltage:null;
  const current=powerFresh&&number(power.current)?power.current:!hasPower&&px&&number(d.current)&&d.current>=0?d.current:null;
  const battery=powerFresh&&number(power.battery)?power.battery:!hasPower&&px&&number(d.battery)&&d.battery>=0&&d.battery<=100?d.battery:null;
  const link=d.network_link||{},network=feed&&link.connected===true&&fresh(link.age,6),tech=String(link.technology||'').toLowerCase();
  const type=!network?'NO LINK':link.type==='wifi'?'WIFI':link.type==='cellular'?/5g|5gnr|nr5g/.test(tech)?'5G':/lte/.test(tech)?'4G':'CELLULAR':link.type==='ethernet'?'ETHERNET':'NETWORK';
  const percent=network&&number(link.percent)&&link.percent>=0&&link.percent<=100?link.percent:null;
  const rsrp=network&&number(link.rsrp_dbm)&&link.rsrp_dbm>=-160&&link.rsrp_dbm<=-30?link.rsrp_dbm:null;
  const strength=rsrp!==null?rsrp>=-90?'STRONG':rsrp>=-110?'MODERATE':'WEAK':percent===null?'--':percent>=80?'STRONG':percent>=40?'MODERATE':'WEAK';
  const ages=d.sensor_ages||{},rangeFresh=px&&(ages.rangefinder===undefined?number(d.rangefinder)&&d.rangefinder>0:fresh(ages.rangefinder,5));
  const flowFresh=px&&(ages.of===undefined?number(d.of_quality)&&d.of_quality>0:fresh(ages.of,5));
  const device=value=>feed&&value?.connected===true&&fresh(value.age,5)?value:null;
  // The ground station has no link of its own; the rover holding RTK proves its corrections are arriving.
  const rc0=d.rtk_corrections||{},rtkLink={online:px&&rc0.online===true,ever:px&&rc0.ever===true,age:number(rc0.age)?rc0.age:null,fix:rc0.fix||null,forS:number(rc0.for_s)?rc0.for_s:null};
  return {ground:device(d.ground_computer),rtkBase:device(d.rtk_base),rtkLink,feed,px,state,reported,rcConnected,rcFresh,rcPercent:rcConnected&&number(rc.percent)&&rc.percent>=0&&rc.percent<=100?rc.percent:null,
   escCount:channels.length,escError:esc.error||'',voltage,current,battery,network,type,strength,
   range:rangeFresh&&number(d.rangefinder)&&d.rangefinder>0?d.rangefinder:null,agl:rangeFresh&&number(d.rangefinder_agl)?d.rangefinder_agl:null,
   flow:flowFresh&&number(d.of_quality)&&d.of_quality>=0&&d.of_quality<=255?Math.round(d.of_quality*100/255):null};
 }
 if(typeof module!=='undefined')module.exports={model};else root.AirsentHealthModel={model};
})(typeof window==='undefined'?{}:window);
