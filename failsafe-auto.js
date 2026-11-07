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
addEventListener('airsent-review-frame',event=>{frame=event.detail;draw()});
setInterval(draw,1000);draw();
})();
