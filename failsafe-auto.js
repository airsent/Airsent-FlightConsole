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
const DEMO_DEFS=[['fc_link','Link','Pixhawk heartbeat','s','heartbeat age · lost after 5 s'],['vps_link','Link','Jetson ↔ VPS relay','flag','relay connected'],
 ['console','Link','Operator console','s','hold 10 s · RTL 30 s'],['rc','RC','RC receiver','flag','receiver signal present'],
 ['tilt','Attitude','Tilt angle','°','warn 35° · crit 50°','flying'],['oscillation','Attitude','Attitude oscillation','°σ','warn > 6°σ roll','flying'],
 ['gps_fix','GPS','GPS fix','text','needs 3D fix or better'],['gps_sats','GPS','Satellites','','warn < 10'],['gps_hdop','GPS','HDOP','','warn > 1.5'],
 ['ekf','Estimator','EKF health','flag','EKF must be healthy'],['vibration','Vibration','Vibration','m/s²','warn 30 · crit 60','armed'],
 ['cell_voltage','Power','Cell voltage','V/cell','warn 3.6 · crit 3.5 (6S)'],['current','Power','Current draw','A','warn > 70 A','armed'],
 ['reserve','Power','Energy to return home','%','battery needed to get home','flying'],['motor_balance','Motors','Motor balance','%','warn > 25%','flying'],
 ['esc','Motors','ESC telemetry','','ESCs reporting','armed'],['jetson_temp','Jetson','Jetson temperature','°C','warn ≥ 85 °C'],
 ['jetson_cpu','Jetson','Jetson CPU','%','warn > 95%'],['front_cam','Cameras','Front camera','flag','camera online']];
function demoLive(watching){const t=Date.now()/1000;return DEMO_DEFS.map(([id])=>{
  if(['tilt','oscillation','reserve','motor_balance'].includes(id)&&!watching)return [id,-1,null];
  if(id==='vibration'){const v=+(18+14*Math.sin(t/4)).toFixed(1);return v>30?[id,2,v,'High vibration '+v.toFixed(0)+' m/s²']:[id,0,v];}
  const vals={gps_sats:21,gps_hdop:0.62,gps_fix:'RTK FIX',cell_voltage:+(4.12-0.01*Math.sin(t/7)).toFixed(3),current:+(1.8+0.6*Math.sin(t/3)).toFixed(2),jetson_temp:+(54+2*Math.sin(t/9)).toFixed(1),jetson_cpu:+(37+9*Math.sin(t/2)).toFixed(0),fc_link:+(0.05+0.04*Math.abs(Math.sin(t))).toFixed(2),console:+(0.4+0.3*Math.abs(Math.sin(t/2))).toFixed(1),esc:4};return [id,0,vals[id]??1];});}
const LV={'-1':['○','na','NOT ACTIVE'],0:['●','ok','OK'],1:['●','ok','OK'],2:['▲','warn','WARNING'],3:['■','crit','CRITICAL']};
const WHEN={session:'',armed:'active when armed',flying:'active in flight'};
let panel=null,hideIdle=localStorage.getItem('ascHideIdle')==='1';
const hist={},seen={};            // per-check value history (60 s) and when a problem was first seen
function styles(){if(document.getElementById('fsLiveCss'))return;const st=document.createElement('style');st.id='fsLiveCss';st.textContent=`
.fs-live-btn{margin-left:auto;margin-right:12px;height:24px;padding:0 12px;background:#0b1620;color:#9be5ff;border:1px solid rgba(127,220,255,.35);font:600 10px 'Share Tech Mono',monospace;letter-spacing:1.5px;cursor:pointer}
.fs-live-btn:hover{color:#fff;border-color:#9be5ff}
.fs-live{position:fixed;top:0;right:0;bottom:0;width:min(680px,98vw);z-index:10040;background:#060d14;border-left:1px solid rgba(0,180,255,.25);box-shadow:-14px 0 40px rgba(0,0,0,.7);
  display:flex;flex-direction:column;transform:translateX(105%);transition:transform .2s ease;font-family:'Share Tech Mono',monospace;color:#cfe3f2}
.fs-live.open{transform:none}
.fs-live header{padding:14px 16px 12px;border-bottom:1px solid rgba(0,180,255,.15)}
.fs-live .top{display:flex;align-items:center;gap:12px}
.fs-live .ttl{font:700 13px Rajdhani,sans-serif;letter-spacing:2px;color:#8fa3b5}
.fs-live .x{margin-left:auto;background:none;border:0;color:#8fa3b5;font-size:22px;cursor:pointer;line-height:1}
.fs-live .big{display:flex;align-items:baseline;gap:14px;margin-top:8px}
.fs-live .verdict{font:700 30px Rajdhani,sans-serif;letter-spacing:2px}
.fs-live .verdict.ok{color:#00e383}.fs-live .verdict.warn{color:#ffb800}.fs-live .verdict.crit{color:#ff3355;animation:ascBlink 1s steps(2) infinite}.fs-live .verdict.na{color:#5d7a92}
@keyframes ascBlink{50%{opacity:.55}}
.fs-live .meta{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:6px;font-size:11px;color:#7f9bb1}
.fs-live .meta b{color:#e8f4ff;font-weight:400}
.fs-live .tools{display:flex;gap:8px;margin-top:10px;font-size:11px;align-items:center}
.fs-live .tools .c-ok{color:#00e383}.fs-live .tools .c-warn{color:#ffb800}.fs-live .tools .c-crit{color:#ff5c7a}.fs-live .tools .c-na{color:#5d7a92}
.fs-live .tools label{margin-left:auto;cursor:pointer;color:#8fa3b5;display:flex;gap:6px;align-items:center}
.fs-live .list{flex:1;overflow-y:auto;padding:10px 12px 18px;scrollbar-width:thin}
.fs-live .attn{margin-bottom:12px}
.fs-live .card{border:1px solid;border-left-width:4px;padding:9px 12px;margin-bottom:8px;display:grid;grid-template-columns:1fr auto;gap:2px 12px}
.fs-live .card.warn{border-color:rgba(255,184,0,.45);background:rgba(255,184,0,.07)}.fs-live .card.crit{border-color:rgba(255,51,85,.6);background:rgba(255,51,85,.1)}
.fs-live .card .cl{font:700 15px Rajdhani,sans-serif;letter-spacing:1px;color:#fff}
.fs-live .card .cm{font-size:12px}.fs-live .card.warn .cm{color:#ffcf5c}.fs-live .card.crit .cm{color:#ff8da1}
.fs-live .card .cv{grid-row:1/3;grid-column:2;text-align:right;font:700 20px Rajdhani,sans-serif;color:#fff;align-self:center}
.fs-live .card .cv small{display:block;font:400 10px 'Share Tech Mono',monospace;color:#8fa3b5}
.fs-live .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}
.fs-live .grp{border:1px solid rgba(0,180,255,.12);background:#08121b}
.fs-live .grp h4{margin:0;padding:6px 10px;font:600 10px 'Share Tech Mono',monospace;letter-spacing:2px;color:#7f9bb1;display:flex;justify-content:space-between;border-bottom:1px solid rgba(0,180,255,.08)}
.fs-live .grp h4 .gs.ok{color:#00e383}.fs-live .grp h4 .gs.warn{color:#ffb800}.fs-live .grp h4 .gs.crit{color:#ff5c7a}
.fs-live .row{display:grid;grid-template-columns:14px 1fr 64px 78px;gap:0 8px;align-items:center;padding:4px 10px;min-height:26px}
.fs-live .row+.row{border-top:1px solid rgba(255,255,255,.03)}
.fs-live .row .d{font-size:11px;text-align:center}
.fs-live .row .l{font:500 13px Rajdhani,sans-serif;color:#e8f4ff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fs-live .row .v{font-size:12px;color:#e8f4ff;text-align:right;white-space:nowrap}
.fs-live .row .v small{color:#6f8aa0;font-size:9px;margin-left:2px}
.fs-live .row svg{width:64px;height:16px;display:block}
.fs-live .ok .d{color:#00e383}.fs-live .warn .d,.fs-live .row.warn .l{color:#ffb800}.fs-live .crit .d,.fs-live .row.crit .l{color:#ff5c7a}
.fs-live .row.warn{background:rgba(255,184,0,.06)}.fs-live .row.crit{background:rgba(255,51,85,.1)}
.fs-live .na .d,.fs-live .na .l,.fs-live .na .v{color:#3d5466}
.fs-live .empty{padding:24px;text-align:center;color:#5d7a92}`;document.head.append(st);}
function openPanel(){styles();if(!panel){panel=document.createElement('aside');panel.className='fs-live';panel.setAttribute('aria-label','ASC live checks');
  panel.innerHTML='<header><div class="top"><span class="ttl">ASC · LIVE CHECKS</span><button type="button" class="x" aria-label="Close">×</button></div>'+
    '<div class="big"><span class="verdict"></span></div><div class="meta"></div>'+
    '<div class="tools"><span class="counts"></span><label><input type="checkbox" class="hide"> hide not-active</label></div></header><div class="list"></div>';
  panel.querySelector('.x').onclick=()=>panel.classList.remove('open');document.body.append(panel);
  const hb=panel.querySelector('.hide');hb.checked=hideIdle;hb.onchange=()=>{hideIdle=hb.checked;localStorage.setItem('ascHideIdle',hideIdle?'1':'0');renderPanel()};
  addEventListener('keydown',e=>{if(e.key==='Escape')panel.classList.remove('open')});}
  panel.classList.toggle('open');renderPanel();}
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num=v=>typeof v==='number'&&isFinite(v);
function fmtVal(v,unit,lvl){
  if(unit==='flag')return lvl>=2?'NO':lvl<0?'—':'YES';
  if(v===null||v===undefined||v==='')return '—';
  if(!num(v))return esc(v);
  const a=Math.abs(v),t=Number.isInteger(v)?String(v):a>=100?v.toFixed(0):a>=10?v.toFixed(1):v.toFixed(2);
  return t+(unit&&unit!=='text'?'<small>'+esc(unit)+'</small>':'');
}
const mmss=s=>s==null?'--':(s>=3600?Math.floor(s/3600)+':':'')+String(Math.floor(s%3600/60)).padStart(2,'0')+':'+String(Math.floor(s%60)).padStart(2,'0');
function spark(id,lvl){
  const h=hist[id];if(!h||h.length<3)return '';
  const lo=Math.min(...h),hi=Math.max(...h),span=hi-lo||1;
  const pts=h.map((v,i)=>`${(i/(h.length-1)*64).toFixed(1)},${(15-(v-lo)/span*14).toFixed(1)}`).join(' ');
  const col=lvl>=3?'#ff5c7a':lvl===2?'#ffb800':'#3fa9d8';
  return `<svg viewBox="0 0 64 16" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.2"/></svg>`;
}
let lastSample=0;
function renderPanel(){
  const a=asc(),watching=!!a&&a.state!=='standby';
  const defs=isDemo?DEMO_DEFS:(M()&&M().ascDefs)||[];
  const live=isDemo?demoLive(watching):(a&&a.live)||[];
  const by=Object.fromEntries(live.map(r=>[r[0],r]));
  const now=Date.now();
  if(now-lastSample>=500){lastSample=now;                  // keep 60 s of history even while the panel is closed
    live.forEach(r=>{if(num(r[2])){(hist[r[0]]=hist[r[0]]||[]).push(r[2]);if(hist[r[0]].length>120)hist[r[0]].shift();}
      if(r[1]>=2){seen[r[0]]=seen[r[0]]||now}else delete seen[r[0]];});}
  if(!panel||!panel.classList.contains('open'))return;
  const n={ok:0,warn:0,crit:0,na:0};live.forEach(r=>n[LV[r[1]]?.[1]||'na']++);
  const v=panel.querySelector('.verdict');
  const [vt,vc]=!a?['NO DATA','na']:n.crit?[n.crit+' CRITICAL'+(n.warn?' · '+n.warn+' WARN':''),'crit']:n.warn?[n.warn+' WARNING'+(n.warn>1?'S':''),'warn']:['ALL CLEAR','ok'];
  v.textContent=vt;v.className='verdict '+vc;
  const f=a&&a.flight;
  panel.querySelector('.meta').innerHTML=!a?'<span>No ASC data from the Jetson</span>':
    [a.state==='acting'?'<span style="color:#ff5c7a">ACTING · '+(a.action&&a.action.stage==='rtl'?'RETURNING HOME':'HOLDING')+'</span>':'<span>'+(watching?'● RECORDING':'STANDBY · live view only')+'</span>',
     f?'<span>T+ <b>'+mmss(now/1000-f.since)+'</b></span>':'',f?'<span>flying <b>'+mmss(f.flying_s)+'</b></span>':'',
     f?'<span>flown <b>'+Math.round(f.dist_m)+' m</b></span>':'',f?'<span>saved <b>'+f.rec_mb+' MB</b></span>':'',
     '<span><b>'+(a.checks_per_s||0)+'</b> checks/s</span>',isDemo?'<span>DEMO</span>':''].join('');
  panel.querySelector('.counts').innerHTML=`<span class="c-ok">● ${n.ok} OK</span> <span class="c-warn">▲ ${n.warn}</span> <span class="c-crit">■ ${n.crit}</span> <span class="c-na">○ ${n.na} idle</span>`;
  if(!defs.length){panel.querySelector('.list').innerHTML='<div class="empty">Waiting for the check list from the Jetson…</div>';return;}
  const meta=Object.fromEntries(defs.map(d=>[d[0],{group:d[1],label:d[2],unit:d[3]||'',limit:d[4]||'',when:d[5]||'session'}]));
  // 1. problems first, critical on top, as big cards
  const probs=live.filter(r=>r[1]>=2&&meta[r[0]]).sort((x,y)=>y[1]-x[1]||(seen[x[0]]||0)-(seen[y[0]]||0));
  const attn=probs.length?'<div class="attn">'+probs.map(r=>{const m=meta[r[0]],age=seen[r[0]]?Math.round((now-seen[r[0]])/1000):0;
    return `<div class="card ${LV[r[1]][1]}"><span class="cl">${LV[r[1]][0]} ${esc(m.label)}</span><span class="cv">${fmtVal(r[2],m.unit,r[1])}<small>${age}s</small></span>
      <span class="cm">${esc(r[3]||LV[r[1]][2])}${m.limit?' · limit: '+esc(m.limit):''}</span></div>`}).join('')+'</div>':'';
  // 2. every check by group
  const groups=[];defs.forEach(d=>{if(!groups.includes(d[1]))groups.push(d[1])});
  const grid=groups.map(g=>{
    const rows=defs.filter(d=>d[1]===g).map(d=>by[d[0]]||[d[0],-1,null]).filter(r=>!hideIdle||r[1]>=0);
    if(!rows.length)return '';
    const worst=Math.max(...rows.map(r=>r[1])),gs=LV[worst]||LV[-1];
    return `<div class="grp"><h4>${esc(g.toUpperCase())}<span class="gs ${gs[1]}">${worst<0?'IDLE':gs[2]}</span></h4>`+rows.map(r=>{const m=meta[r[0]],lv=LV[r[1]]||LV[-1];
      const tip=[lv[2],m.limit,r[1]<0?WHEN[m.when]:'',r[3]||''].filter(Boolean).join(' · ');
      return `<div class="row ${lv[1]}" title="${esc(tip)}"><span class="d">${lv[0]}</span><span class="l">${esc(m.label)}</span>${(r[1]>=0&&m.unit!=='flag'&&spark(r[0],r[1]))||'<span></span>'}<span class="v">${fmtVal(r[2],m.unit,r[1])}</span></div>`}).join('')+'</div>';
  }).join('');
  const list=panel.querySelector('.list'),top=list.scrollTop;
  list.innerHTML=attn+'<div class="grid">'+grid+'</div>';list.scrollTop=top;
}
document.getElementById('fsLiveChecksBtn').addEventListener('click',openPanel);
setInterval(renderPanel,500);
addEventListener('airsent-review-frame',event=>{frame=event.detail;draw()});
setInterval(draw,1000);draw();
})();
