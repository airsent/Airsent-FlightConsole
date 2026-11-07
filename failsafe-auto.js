/* ASC bar on the failsafe page: shows the Jetson's ASC (Automated Self Checks).
   The switch turns ASC's one automatic action on/off (console link lost in GUIDED/AUTO:
   hold after 10 s, RTL after 30 s). Checks and recording always run. */
(()=>{
'use strict';
const $=id=>document.getElementById(id);
const button=$('fsAutoToggle'),summary=$('fsAutoSummary'),bar=document.querySelector('.fs-auto-bar');
const M=()=>window.airsentMission;
const mode=(localStorage.getItem('airsentMode')||'demo').toLowerCase();
const isDemo=mode==='demo',viewer=mode==='viewer';
let demoAct=true,frame=null,busy=false;
button.title='ASC automatic action: if the operator console link is lost while flying GUIDED/AUTO, hold position after 10 s and return home after 30 s. Checks and recording always run.';
if(viewer)button.disabled=true;

function asc(){
  if(isDemo){const s=M()&&M().state,watching=s&&['prearm','gonogo','armed','takeoff','flight','landed'].includes(s.stage);
    return {state:watching?'watching':'standby',act_enabled:demoAct,checks:45,checks_per_s:watching?880:0,issues:[],session:watching?'DEMO':null,triggered:[]};}
  const a=M()&&M().asc;return a&&M().stateFresh()?a:null;
}
function draw(){
  const a=asc();
  // tile visuals from the sensor lights (unchanged behaviour)
  const cards=[...document.querySelectorAll('.fs-square-tile')],fresh=frame&&Date.now()-frame.receivedAt<=4000;
  const watching=!!a&&a.state!=='standby';
  for(const card of cards){const light=card.querySelector('.fs-status-light'),state=fresh?light?.dataset.state:'unknown';
    card.classList.toggle('fs-monitor-on',watching);card.classList.toggle('fs-emergency',state==='fault');card.classList.toggle('fs-card-caution',state==='warning')}
  const act=a?a.act_enabled:null;
  button.setAttribute('aria-checked',String(!!act));button.textContent=act===null?'—':act?'ON':'OFF';
  const issues=a?(a.issues||[]).length+(a.triggered||[]).filter(t=>!t.cleared).length:0;
  const count=$('fsFaultCount');count.textContent=String(issues);count.parentElement.classList.toggle('fs-multiple-faults',issues>1);
  count.title='Findings ASC is currently raising';
  let text;
  if(!a)text='NO ASC DATA FROM THE JETSON';
  else if(a.state==='acting')text='ACTING · '+(a.action&&a.action.stage==='rtl'?'RETURNING HOME':'HOLDING POSITION')+' · CONSOLE LINK LOST '+Math.round(a.console_age||0)+' S';
  else if(a.state==='watching')text='WATCHING · '+(a.checks_per_s||0)+' CHECKS/S · RECORDING '+(a.session||'')+(issues?' · '+(a.issues||[]).slice(0,2).map(i=>i.msg).join(' · '):'');
  else text='STANDBY · '+(a.checks||0)+' CHECKS · STARTS AT PRE-ARM OR ARMING';
  if(a&&!a.act_enabled)text+=' · AUTO-ACTION OFF';
  summary.textContent=text+(isDemo?' · DEMO':'');
  summary.dataset.fault=String(issues>0||a?.state==='acting');
  bar.classList.toggle('fs-asc-on',watching);bar.classList.toggle('fs-asc-emergency',watching&&(issues>0||a.state==='acting'));
  const cmd=[...document.querySelectorAll('.bar-lbl')].find(x=>['Commands','ASC'].includes(x.textContent.trim()));
  if(cmd){cmd.textContent='ASC';if(cmd.nextElementSibling)cmd.nextElementSibling.textContent=a?a.state.toUpperCase():'—';}
}
async function toggle(){
  if(viewer||busy)return;
  const a=asc(),next=!(a&&a.act_enabled);
  if(!next&&!confirm('Turn OFF the ASC automatic action?\n\nIf the operator console link is lost in flight, ASC will only alert — it will not hold or return home.'))return;
  busy=true;
  try{
    if(isDemo){demoAct=next;window.airsentMissionUI?.toast('ASC automatic action '+(next?'ON':'OFF')+' (demo)');}
    else{const r=await M().send('asc_set',{act_enabled:next});if(r.asc)M().asc=r.asc;window.airsentMissionUI?.toast((r.ok?'✓ ':'✗ ')+r.msg,r.ok?'':'alert');}
  }finally{busy=false;draw();}
}
button.addEventListener('click',()=>window.AirsentASCLock?window.AirsentASCLock.request(toggle):toggle());

// ── Live checks panel: every ASC check, green OK · yellow warning · red critical · grey not active.
// The states come from the Jetson in telemetry, so every console (viewers too) sees the same thing.
const DEMO_DEFS=[['fc_link','Link','Pixhawk heartbeat'],['vps_link','Link','Jetson ↔ VPS relay'],['console','Link','Operator console'],['rc','RC','RC receiver'],
 ['tilt','Attitude','Tilt angle'],['oscillation','Attitude','Attitude oscillation'],['gps_fix','GPS','GPS fix'],['gps_sats','GPS','Satellites'],['gps_hdop','GPS','HDOP'],
 ['ekf','Estimator','EKF health'],['vibration','Vibration','Vibration'],['cell_voltage','Power','Cell voltage'],['current','Power','Current draw'],['reserve','Power','Energy to return home'],
 ['motor_balance','Motors','Motor balance'],['esc','Motors','ESC telemetry'],['jetson_temp','Jetson','Jetson temperature'],['jetson_cpu','Jetson','Jetson CPU'],['front_cam','Cameras','Front camera']];
function demoLive(watching){const t=Date.now()/1000;return DEMO_DEFS.map(([id])=>{
  if(['tilt','oscillation','reserve','motor_balance'].includes(id)&&!watching)return [id,-1,null];
  if(id==='vibration'){const v=+(18+14*Math.sin(t/4)).toFixed(1);return v>30?[id,2,v,'High vibration '+v.toFixed(0)+' m/s²']:[id,0,v];}
  const vals={gps_sats:21,gps_hdop:0.62,cell_voltage:4.12,current:1.8,jetson_temp:54,jetson_cpu:37,fc_link:0.08};return [id,0,vals[id]??null];});}
const LV={'-1':['○','na','NOT ACTIVE'],0:['●','ok','OK'],1:['●','ok','OK'],2:['●','warn','WARNING'],3:['●','crit','CRITICAL']};
let panel=null;
function styles(){if(document.getElementById('fsLiveCss'))return;const st=document.createElement('style');st.id='fsLiveCss';st.textContent=`
.fs-live-btn{margin-left:auto;margin-right:12px;height:24px;padding:0 12px;background:#0b1620;color:#9be5ff;border:1px solid rgba(127,220,255,.35);font:600 10px 'Share Tech Mono',monospace;letter-spacing:1.5px;cursor:pointer}
.fs-live-btn:hover{color:#fff;border-color:#9be5ff}
.fs-live{position:fixed;top:0;right:0;bottom:0;width:min(560px,96vw);z-index:10040;background:#060d14;border-left:1px solid rgba(0,180,255,.25);box-shadow:-14px 0 40px rgba(0,0,0,.7);
  display:flex;flex-direction:column;transform:translateX(105%);transition:transform .18s ease;font:12px 'Share Tech Mono',monospace;color:#e8f4ff}
.fs-live.open{transform:none}
.fs-live header{padding:14px 16px 10px;border-bottom:1px solid rgba(0,180,255,.15)}
.fs-live header h3{margin:0;font:700 16px Rajdhani,sans-serif;letter-spacing:2px;display:flex;align-items:center;gap:10px}
.fs-live header h3 button{margin-left:auto;background:none;border:0;color:#8fa3b5;font-size:20px;cursor:pointer}
.fs-live .sub{color:#5d7a92;font-size:10px;letter-spacing:1px;margin-top:4px}
.fs-live .counts{display:flex;gap:14px;margin-top:8px;font-size:11px}
.fs-live .list{flex:1;overflow-y:auto;padding:4px 0 16px;scrollbar-width:thin}
.fs-live .grp{padding:10px 16px 4px;font-size:9px;letter-spacing:2px;color:#5d7a92}
.fs-live .row{display:grid;grid-template-columns:16px 1fr auto;gap:2px 10px;align-items:center;padding:5px 16px;border-left:2px solid transparent}
.fs-live .row .d{font-size:12px} .fs-live .row .l{font:500 13px Rajdhani,sans-serif;color:#e8f4ff} .fs-live .row .v{color:#9fb3c4;font-size:11px;text-align:right}
.fs-live .row .m{grid-column:2/4;font-size:10px}
.fs-live .ok .d{color:#00e383} .fs-live .warn{border-left-color:#ffb800;background:rgba(255,184,0,.05)} .fs-live .warn .d,.fs-live .warn .m{color:#ffb800}
.fs-live .crit{border-left-color:#ff3355;background:rgba(255,51,85,.08)} .fs-live .crit .d,.fs-live .crit .m{color:#ff5c7a}
.fs-live .na .d,.fs-live .na .l{color:#3d5466} .fs-live .na .v{color:#3d5466}
.fs-live .c-ok{color:#00e383}.fs-live .c-warn{color:#ffb800}.fs-live .c-crit{color:#ff5c7a}.fs-live .c-na{color:#5d7a92}`;document.head.append(st);}
function openPanel(){styles();if(!panel){panel=document.createElement('aside');panel.className='fs-live';panel.setAttribute('aria-label','ASC live checks');
  panel.innerHTML='<header><h3>ASC · LIVE CHECKS<button type="button" aria-label="Close">×</button></h3><div class="sub"></div><div class="counts"></div></header><div class="list"></div>';
  panel.querySelector('h3 button').onclick=()=>panel.classList.remove('open');document.body.append(panel);
  addEventListener('keydown',e=>{if(e.key==='Escape')panel.classList.remove('open')});}
  panel.classList.toggle('open');renderPanel();}
const fmt=v=>v===null||v===undefined?'—':typeof v==='number'?(Math.abs(v)>=100?v.toFixed(0):+v.toFixed(2)):String(v);
function renderPanel(){
  if(!panel||!panel.classList.contains('open'))return;
  const a=asc(),watching=!!a&&a.state!=='standby';
  const defs=isDemo?DEMO_DEFS:(M()&&M().ascDefs)||[];
  const live=isDemo?demoLive(watching):(a&&a.live)||[];
  const by=Object.fromEntries(live.map(r=>[r[0],r]));
  const n={ok:0,warn:0,crit:0,na:0};live.forEach(r=>n[LV[r[1]]?.[1]||'na']++);
  panel.querySelector('.sub').textContent=!a?'NO ASC DATA FROM THE JETSON':(a.state==='standby'?'STANDBY — live view only; recording starts when a mission is created or the drone arms':
    (a.state==='acting'?'ACTING · ':'RECORDING · ')+(a.session||'')+' · '+(a.checks_per_s||0)+' checks/s')+(isDemo?' · DEMO':'');
  panel.querySelector('.counts').innerHTML=`<span class="c-ok">● ${n.ok} OK</span><span class="c-warn">● ${n.warn} WARNING</span><span class="c-crit">● ${n.crit} CRITICAL</span><span class="c-na">○ ${n.na} NOT ACTIVE</span>`;
  const groups=[];defs.forEach(d=>{if(!groups.includes(d[1]))groups.push(d[1])});
  const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  panel.querySelector('.list').innerHTML=!defs.length?'<div class="grp">Waiting for the check list from the Jetson…</div>':groups.map(g=>'<div class="grp">'+esc(g.toUpperCase())+'</div>'+
    defs.filter(d=>d[1]===g).map(([id,,label])=>{const r=by[id]||[id,-1,null],lv=LV[r[1]]||LV[-1];
      return `<div class="row ${lv[1]}" title="${esc(id)} · ${lv[2]}"><span class="d">${lv[0]}</span><span class="l">${esc(label)}</span><span class="v">${esc(fmt(r[2]))}</span>${r[3]?`<span class="m">${esc(r[3])}</span>`:''}</div>`}).join('')).join('');
}
document.getElementById('fsLiveChecksBtn').addEventListener('click',openPanel);
setInterval(renderPanel,500);
addEventListener('airsent-review-frame',event=>{frame=event.detail;draw()});
setInterval(draw,1000);draw();
})();
