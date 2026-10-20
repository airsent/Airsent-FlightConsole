/* UI-only review page. No sockets, parameter writes, or aircraft commands. */
(()=>{
'use strict';
const $=id=>document.getElementById(id);
const groups=[{name:'LINK & CONTROL',ids:[0,1,7]},{name:'POWER & FLIGHT',ids:[2,8,10,11]},{name:'NAVIGATION',ids:[3,4,9]},{name:'LANDING & SENSORS',ids:[5,6,12,13]}];
let group=0, selected=1;
function fit(){const scale=Math.min(innerWidth/1280,innerHeight/900);const shell=$('fsShell');shell.style.width=Math.max(1280,innerWidth/scale)+'px';shell.style.height='900px';shell.style.transform=`translate(-50%,-50%) scale(${scale})`;}
function render(){
$('fsGroups').replaceChildren();groups.forEach((g,i)=>{const b=document.createElement('button');b.type='button';b.className='fs-group';b.setAttribute('aria-pressed',String(group===i));b.innerHTML=`${g.name}<span>${String(g.ids.length).padStart(2,'0')}</span>`;b.addEventListener('click',()=>{group=i;selected=g.ids[0];render()});$('fsGroups').append(b)});
$('fsGroupTitle').textContent=groups[group].name;$('fsGroupCount').textContent=groups[group].ids.length+' RULES';$('fsRules').replaceChildren();
groups[group].ids.forEach(i=>{const r=FAILSAFE_RULES[i];const b=document.createElement('button');b.type='button';b.className='fs-rule';b.setAttribute('aria-pressed',String(selected===i));b.innerHTML=`<span><strong>${r[0]}</strong><small>${r[1]}</small></span><span class="fs-rule-owner">${r[2]==='Flight controller'?'FC':r[2]==='Mission integration'?'MISSION':'FC + JETSON'}</span><span class="fs-rule-state">UNVERIFIED</span>`;b.addEventListener('click',()=>{selected=i;render()});$('fsRules').append(b)});
const r=FAILSAFE_RULES[selected];['fsTitle','fsTrigger','fsOwner','fsResponse','fsCaveat','fsRecovery'].forEach((id,i)=>$(id).textContent=r[i]);$('fsSelectedId').textContent='FS / '+String(selected+1).padStart(2,'0');
}
const scenarios={review:{id:1,title:'No active scenario',detail:'Parameter readback pending. No aircraft protection is represented as configured.'},link:{id:1,title:'5G connection lost',detail:'Remote input expired → fallback requested → aircraft response UNKNOWN.'},landing:{id:5,title:'Landing target lost',detail:'Target freshness failed → retry / alternate policy evaluated → response UNKNOWN.'},battery:{id:2,title:'Critical battery',detail:'Simulated critical threshold crossed → configured response required → aircraft response UNKNOWN.'}};
function scenario(){const key=$('fsScenario').value,s=scenarios[key];selected=s.id;group=groups.findIndex(g=>g.ids.includes(selected));$('fsEventTitle').textContent=s.title;$('fsEventTitle').style.color=key==='review'?'var(--text)':'var(--red)';$('fsEventDetail').textContent=s.detail;$('fsAck').disabled=key==='review';$('fsAckState').textContent='';render()}
$('fsScenario').addEventListener('change',scenario);$('fsAck').addEventListener('click',()=>{$('fsAckState').textContent='Alert acknowledged. Aircraft state remains unconfirmed.';$('fsAck').disabled=true});
addEventListener('resize',fit);fit();scenario();
})();
