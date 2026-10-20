/* Browser telemetry monitor only. Does not enable onboard flight protections. */
(()=>{
'use strict';let enabled=false,frame=null,flight=false;
const button=document.getElementById('fsAutoToggle'),summary=document.getElementById('fsAutoSummary');
function setEnabled(value){enabled=value;button.setAttribute('aria-checked',String(value));button.textContent=value?'ON':'OFF';draw()}
function draw(){
 const cards=[...document.querySelectorAll('.fs-square-tile')],fresh=frame&&Date.now()-frame.receivedAt<=4000;
 let faults=0,known=0;
 for(const card of cards){const light=card.querySelector('.fs-status-light'),state=fresh?light?.dataset.state:'unknown';const fault=state==='fault';if(fault)faults++;if(enabled&&fresh&&state!=='unknown')known++;card.classList.toggle('fs-monitor-on',enabled);card.classList.toggle('fs-emergency',fault);card.classList.toggle('fs-card-caution',state==='warning')}
 summary.textContent=cards.length+' SYSTEM CHECKS · '+(!enabled?'OFF':faults?faults+' FAULT'+(faults===1?'':'S')+' · '+(cards.length-known)+' UNKNOWN':!fresh?'WAITING FOR TELEMETRY':known+' REPORTED · '+(cards.length-known)+' UNKNOWN')+(enabled&&frame?.source==='demo'?' · DEMO':'');
 const count=document.getElementById('fsFaultCount');count.textContent=String(faults);count.parentElement.classList.toggle('fs-multiple-faults',faults>1);count.title=fresh?'Reported system faults':'No fresh fault evidence — status unknown';summary.dataset.fault=String(faults>0);const bar=document.querySelector('.fs-auto-bar');bar.classList.toggle('fs-asc-on',enabled);bar.classList.toggle('fs-asc-emergency',enabled&&faults>0);
}
button.addEventListener('click',()=>window.AirsentASCLock?.request(()=>setEnabled(!enabled)));
addEventListener('airsent-review-frame',event=>{frame=event.detail;const nowFlying=frame.source==='telemetry'&&frame.values.in_air===true;if(nowFlying&&!flight)setEnabled(true);flight=nowFlying;draw()});
setInterval(draw,1000);draw();
})();
