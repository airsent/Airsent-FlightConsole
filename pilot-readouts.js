/* Pilot measurements: unknown stays unknown; no wind/vibration inferred from motion/PWM. */
(function(root){
 const finite=n=>typeof n==='number'&&Number.isFinite(n);
 function model(d,elapsed=0){
  const fresh=finite(d.telemetry_age)&&d.telemetry_age+elapsed<2&&d.telemetry_age>=0;
  const px=fresh&&d.pixhawk_connected===true;
  const p=d.power_status;
  const power=px&&p&&finite(p.age)&&p.age+elapsed<5;
  const field=(key,valid)=>p ? power&&finite(p[key])&&valid(p[key])?p[key]:null : px&&finite(d[key])&&valid(d[key])?d[key]:null;
  const vib=px&&finite(d.sensor_ages?.vib)&&d.sensor_ages.vib+elapsed<5&&['vib_x','vib_y','vib_z'].every(k=>finite(d[k])&&d[k]>=0)?Math.max(d.vib_x,d.vib_y,d.vib_z):null;
  const w=d.wind,wind=px&&w&&finite(w.age)&&w.age+elapsed<5&&finite(w.speed)&&w.speed>=0&&finite(w.direction)?w:null;
  return {px,fresh,voltage:field('voltage',n=>n>=1&&n<100),current:field('current',n=>n>=0),battery:field('battery',n=>n>=0&&n<=100),
   hdop:px&&d.fix_type!=='NO FIX'&&d.sats>0&&finite(d.hdop)&&d.hdop>0&&d.hdop<99?d.hdop:null,vib,wind,
   temp:fresh&&finite(d.cam_temp)&&d.cam_temp>0?d.cam_temp:null};
 }
 if(typeof module!=='undefined'){module.exports={model};return;}
 let data={},received=0;
 function put(id,text,title){const el=document.getElementById(id);if(el){el.textContent=text;el.title=title||'';}}
 function render(){
  const m=model(data,received?(Date.now()-received)/1000:999);
  put('tsVolt',m.voltage===null?'-- V':m.voltage.toFixed(1)+' V',m.voltage===null?'No valid measured battery voltage reported by Pixhawk':'Measured battery voltage');
  const volt=document.getElementById('tsVolt');if(volt)volt.className='ts-val '+(m.voltage===null?'warn':'');
  put('tsCurr',m.current===null?'-- A':m.current.toFixed(2)+' A');
  put('bBatt',m.battery===null?'--':Math.round(m.battery)+'%');
  put('tsHdop',m.hdop===null?'--':m.hdop.toFixed(2),'HDOP requires a valid GPS fix');
  const hdop=document.getElementById('tsHdop');if(hdop)hdop.className='ts-val '+(m.hdop===null?'':m.hdop<1.5?'ok':m.hdop<3?'warn':'err');
  put('tsVib',m.vib===null?'--':m.vib.toFixed(2)+' m/s²','Maximum measured vibration across X/Y/Z axes');
  const vib=document.getElementById('tsVib');if(vib)vib.className='ts-val';
  put('vibBadge','VIB '+(m.vib===null?'--':m.vib.toFixed(2)+' m/s²'));
  const wind=m.wind?m.wind.speed.toFixed(1)+' m/s '+Math.round(m.wind.direction)+'°':'--';
  put('tsWind',wind,'Reported wind telemetry; unavailable when no wind measurement is supplied');put('windTxt','WIND '+wind);
  put('tsTemp',m.temp===null?'-- °C':m.temp.toFixed(1)+' °C','Front camera temperature');
  if(!m.px){for(const [id,text] of Object.entries({tsRoll:'--°',tsPitch:'--°',tsEkf:'OFFLINE',tsLidar:'-- m',bAlt:'-- m',bSpd:'-- m/s',bGps:'OFFLINE',bMode:'--'}))put(id,text);}
 }
 root.AirsentPilotReadouts={receive(d){data=d;received=Date.now();render();}};
 document.addEventListener('DOMContentLoaded',render);setInterval(render,500);
})(typeof window==='undefined'?{}:window);
