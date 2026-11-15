// Read-only view of Pixhawk command outputs and Datalink ESC measurements.
(function () {
  function finite(value) { return typeof value === 'number' && Number.isFinite(value); }
  function model(data, fresh) {
    const feed = !!(fresh && finite(data.telemetry_age) && data.telemetry_age >= 0 && data.telemetry_age < 2);
    const esc = data.esc_telemetry;
    const link = !!(feed && esc?.connected === true && finite(esc.age) && esc.age >= 0 && esc.age < 2);
    const motors = [1,2,3,4].map(id => {
      const row = esc?.channels?.find(item => item.channel === id);
      const live = !!(link && row?.connected === true && finite(row.age) && row.age >= 0 && row.age < 2);
      const value = key => live && finite(row[key]) ? row[key] : null;
      return {id, live, pwm:feed && data.pixhawk_connected === true && finite(data[`m${id}`]) ? data[`m${id}`] : null,
        input:value('input_percent'), output:value('output_percent'), rpm:value('rpm'),
        voltage:value('voltage'), current:value('current'), status:value('status')};
    });
    const all = motors.every(m => m.live && finite(m.output));
    const outputs = motors.map(m => m.output);
    return {feed, fc:feed && data.pixhawk_connected === true, link, motors, average:all ? outputs.reduce((a,b)=>a+b,0)/4 : null,
      spread:all ? Math.max(...outputs)-Math.min(...outputs) : null};
  }
  globalThis.AirsentActuatorView = {model};
  if (typeof document === 'undefined') return;
  const mode = (new URLSearchParams(location.search).get('trial') === 'demo' ? 'demo' : localStorage.getItem('airsentMode') || 'real').toLowerCase();
  const el = id => document.getElementById(id);
  if (mode === 'demo') {
    document.querySelectorAll('.actuator-tests-panel .test-btn, .actuator-tests-panel input, .actuator-tests-panel .mt-presets button')
      .forEach(node => { node.disabled = true; });
    const demoNote = document.getElementById('mtMsg');
    if (demoNote) demoNote.textContent = 'Demo mode — the motor test only runs on a real aircraft.';
    return;
  }
  const text = (id, value) => { if(el(id)) el(id).textContent=value; };
  const format = (value, suffix='', digits=0) => finite(value) ? value.toFixed(digits)+suffix : '--';
  const histories = [[],[],[],[]];
  let socket=null, retry=null, lastReceived=0, started=0, latest={};
  let mtPaint=null;
  const maxAge=2500;
  // ── Props-off motor test ────────────────────────────────────────────────────
  // Full control: any motor, any throttle, up to three minutes. The Jetson keeps the run
  // alive on a short refresh window, so the motors stop within ~2 s if anything drops out.
  (function motorTest(){
    const panel=document.querySelector('.actuator-tests-panel'); if(!panel||!el('mtThrottle')) return;
    const viewer=mode==='viewer';
    const thr=el('mtThrottle'),thrN=el('mtThrottleNum'),sec=el('mtSeconds'),secN=el('mtSecondsNum');
    const props=el('mtProps'),note=el('mtNote'),stopBtn=el('mtStop'),live=el('mtLive');
    const runButtons=[...panel.querySelectorAll('.test-btn[data-motor]')];
    const mapTags=new Map([...panel.querySelectorAll('[data-map]')].map(n=>[Number(n.dataset.map),n]));
    const presets=[...panel.querySelectorAll('.mt-presets button')];
    const warn=panel.querySelector('.tests-warning');
    if(warn) warn.textContent='PROPS MUST BE REMOVED. The motors spin for the time you set, then stop by themselves. Disarmed only.';
    let running=false, sending=false;

    const clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,Number.isFinite(v)?v:lo));
    const throttle=()=>clamp(Math.round(Number(thrN.value)),0,100);
    const seconds=()=>clamp(Math.round(Number(secN.value)),1,180);
    const say=(s,kind='')=>{text('mtMsg',s);if(el('mtMsg'))el('mtMsg').className='mt-msg '+kind;};

    function advice(){
      const p=throttle(),s=seconds();
      if(p>=70&&s>30) return 'High throttle for this long with no load: watch ESC and motor temperature, and stop if anything gets hot to touch.';
      if(p>=70) return 'Unloaded motors reach very high RPM at this throttle. Keep clear of the bell.';
      if(s>60) return 'Long run: ESCs get no airflow with the props off, so check their temperature afterwards.';
      if(p===0) return 'At 0 % the motor will not turn — raise the throttle to spin it.';
      return '';
    }
    function sync(){
      const ok=props.checked&&!viewer&&!running&&!sending;
      runButtons.forEach(b=>{b.disabled=!ok;
        b.title=viewer?'Viewer mode — read only':running?'A test is running':props.checked?'Spin this motor':'Tick "propellers are removed" first';});
      stopBtn.disabled=viewer;
      presets.forEach(b=>b.classList.toggle('on',Number(b.dataset.secs)===seconds()));
      if(note) note.textContent=advice();
    }
    const pair=(range,num,lo,hi)=>{
      range.addEventListener('input',()=>{num.value=range.value;sync();});
      num.addEventListener('input',()=>{const v=clamp(Math.round(Number(num.value)),lo,hi);range.value=v;sync();});
      num.addEventListener('blur',()=>{const v=clamp(Math.round(Number(num.value)),lo,hi);num.value=v;range.value=v;sync();});
    };
    pair(thr,thrN,0,100); pair(sec,secN,1,180);
    presets.forEach(b=>b.addEventListener('click',()=>{sec.value=secN.value=b.dataset.secs;sync();}));
    props.addEventListener('change',sync);

    async function send(payload,label){
      if(sending) return;
      sending=true;sync();say(label+'…');
      const r=window.airsentMission
        ? await window.airsentMission.send('motor_test',payload,{timeout:6000})
        : {ok:false,msg:'Command link not loaded'};
      sending=false;say((r.ok?'✓ ':'✗ ')+r.msg,r.ok?'ok':'err');sync();
      return r;
    }
    runButtons.forEach(b=>b.addEventListener('click',()=>{
      if(b.disabled) return;
      if(latest.armed) return say('Disarm first — motor test is blocked while armed','err');
      const motor=Number(b.dataset.motor);
      send({motor,throttle:throttle(),seconds:seconds(),props_off_confirmed:true},
           motor===0?'Starting every position in turn':`Starting position ${'ABCD'[motor-1]}`);
    }));
    stopBtn.addEventListener('click',()=>{ if(!stopBtn.disabled) send({stop:true,props_off_confirmed:true},'Stopping'); });
    // Space or Escape stops a running test without hunting for the button.
    addEventListener('keydown',e=>{
      if(running&&!viewer&&(e.key==='Escape'||e.key===' ')&&!/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)){
        e.preventDefault();stopBtn.click();
      }
    });

    mtPaint=mt=>{
      const map=(mt&&mt.map)||{};
      mapTags.forEach((node,pos)=>{node.textContent=map[pos]?` M${map[pos]}`:'';});
      const on=!!(mt&&mt.active);
      if(on!==running){running=on;sync();}
      live.hidden=!on;
      if(!on){
        const why=mt&&mt.outcome;
        if(why&&why!=='finished'&&!/^stopped by/.test(why)&&!sending) say('Test ended — '+why,'err');
        runButtons.forEach(b=>b.classList.remove('running'));
        return;
      }
      const letter='ABCD'[(mt.stage||1)-1]||'?';
      const out=mt.output?` → M${mt.output}`:'';
      const what=(mt.sequence?`${letter} of 4`:letter)+out+` · ${Math.round(mt.pct)}%`;
      text('mtLiveWhat',what);
      text('mtLiveLeft',`${Math.ceil(mt.remaining||0)} s left`);
      if(el('mtBar')) el('mtBar').style.width=`${mt.total?Math.min(100,100*mt.elapsed/mt.total):0}%`;
      runButtons.forEach(b=>b.classList.toggle('running',Number(b.dataset.motor)===(mt.sequence?0:mt.stage)));
    };
    sync();
  })();

  ['statusSelect','flightModeSelect'].forEach(id=>{if(el(id)) el(id).disabled=true;});
  ['failsafeStateTop','summaryFailsafe'].forEach(id=>{const node=el(id);if(node) node.parentElement.childNodes.forEach(child=>{if(child.nodeType===3)child.textContent=child.textContent.replace(/FAILSAFE:/g,'ESC STATUS:');});});
  const escLabel=el('failsafeStateTop')?.parentElement.querySelector('.subbar-label');if(escLabel)escLabel.textContent='ESC STATUS:';
  const summary=document.querySelector('.summary-chip.ok');
  if(summary) { summary.classList.remove('ok');summary.textContent='WAITING FOR TELEMETRY';summary.id='actuatorLiveState'; }
  document.querySelectorAll('.motor-card').forEach((card,i)=>{
    const title=card.querySelector('.motor-card-title');if(title) {title.textContent=`M${i+1} / D${i+1}`;title.title='Confirm Datalink slot to motor mapping during individual motor tests';}
    const metrics=document.createElement('div');metrics.className='esc-metrics';
    metrics.innerHTML=`<div>RPM <b id="escRpm${i+1}">--</b></div><div>ESC INPUT <b id="escInput${i+1}">--</b></div><div>VOLTAGE <b id="escVoltage${i+1}">--</b></div><div>CURRENT <b id="escCurrent${i+1}">--</b></div><div>ESC D${i+1} <b id="escState${i+1}">NO DATA</b></div>`;
    card.insertBefore(metrics,card.querySelector('canvas'));
  });
  const style=document.createElement('style');style.textContent='.esc-metrics{display:grid;grid-template-columns:1fr 1fr;gap:5px;font-size:11px;color:#91a8ba;margin:8px 0}.esc-metrics b{float:right;color:#ddeaf4}.esc-metrics div:last-child{grid-column:1/-1}.test-btn:disabled{opacity:.45;cursor:not-allowed}.motor-card[data-esc-live="false"]{border-color:#5b3541}';document.head.appendChild(style);
  function chart(id, series, max) {
    const canvas=el(id);if(!canvas) return;
    const rect=canvas.getBoundingClientRect();if(!rect.width||!rect.height) return;
    const scale=window.devicePixelRatio||1;canvas.width=rect.width*scale;canvas.height=rect.height*scale;
    const ctx=canvas.getContext('2d');ctx.setTransform(scale,0,0,scale,0,0);ctx.clearRect(0,0,rect.width,rect.height);
    ctx.strokeStyle='#20303c';ctx.strokeRect(0,0,rect.width,rect.height);
    const colors=['#7fdcff','#7fff98','#d8ff73','#8bc1ff'];
    series.forEach((values,i)=>{ctx.strokeStyle=colors[i%4];ctx.beginPath();let pen=false;values.forEach((v,j)=>{if(!finite(v)){pen=false;return;}const x=j/59*rect.width,y=rect.height-Math.min(1,Math.max(0,v/max))*rect.height;if(pen)ctx.lineTo(x,y);else ctx.moveTo(x,y);pen=true;});ctx.stroke();});
  }
  function render(data, fresh, append) {
    const view=model(data,fresh);
    text('actuatorLiveState',!view.feed ? 'TELEMETRY OFFLINE' : !view.fc ? 'PIXHAWK OFFLINE' : data.armed ? 'ARMED' : 'DISARMED');
    text('summaryMode',view.fc ? data.flight_mode||'--' : '--');
    text('gpsFixTop',view.fc ? data.fix_type||'NO FIX' : 'OFFLINE');
    text('satCountTop',view.fc && finite(data.sats) ? String(data.sats) : '--');
    text('ekfTop',view.fc ? data.ekf_healthy ? 'HEALTHY' : 'DEGRADED' : 'OFFLINE');
    text('linkTop',view.feed ? 'LIVE' : 'OFFLINE');
    for (const [id,ok] of [['gpsFixTop',view.fc && data.sats>0],['ekfTop',view.fc && data.ekf_healthy],['linkTop',view.feed]]) if(el(id))el(id).style.color=ok ? '#18c78f' : '#ff506a';

    if(el('flightModeSelect')) el('flightModeSelect').value=view.fc ? data.flight_mode : '';
    if(el('statusSelect')) { if(!el('statusSelect').querySelector('option[value="unknown"]')) { const option=document.createElement('option');option.value='unknown';option.textContent='NO FC DATA';el('statusSelect').appendChild(option); } el('statusSelect').value=!view.fc ? 'unknown' : data.armed ? 'armed' : 'disarmed'; }
    text('topVoltage',format(view.fc ? data.voltage : null,'V',1));text('topCurrent',format(view.fc ? data.current : null,'A',1));text('topBattery',format(view.fc&&data.battery>=0 ? data.battery : null,'%'));
    for(const id of ['avgMotorOutput','summaryAverage','diagAverage']) text(id,format(view.average,'%'));
    for(const id of ['motorSpread','summarySpread','diagSpread']) text(id,format(view.spread,'%'));
    for(const id of ['throttlePercent','summaryThrottle']) text(id,'--'); // No unreported collective throttle estimate.
    text('diagOscillation','--');
    const active=view.motors.filter(m=>m.live);const faults=active.filter(m=>m.status!==null&&(m.status&0x7fff)!==0);
    const state=!view.feed?'OFFLINE':!view.link?'WAITING FOR ESC DATA':faults.length?'ESC FAULT':active.length?`${active.length}/4 ESCs LIVE`:'WAITING FOR ESC DATA';
    for(const id of ['diagStatus','summaryFailsafe','failsafeStateTop']) text(id,state);
    const measured=view.motors.filter(m=>finite(m.output));
    const sorted=[...measured].sort((a,b)=>a.output-b.output);
    text('diagLowest',sorted.length ? `D${sorted[0].id} @ ${format(sorted[0].output,'%')}`:'--');
    text('diagHighest',sorted.length ? `D${sorted.at(-1).id} @ ${format(sorted.at(-1).output,'%')}`:'--');
    view.motors.forEach((m,i)=>{
      for(const id of [`m${m.id}pwm`,`quadM${m.id}Pwm`]) text(id,format(m.pwm));
      for(const id of [`m${m.id}out`,`quadM${m.id}Out`]) text(id,format(m.output,'%'));
      if(el(`m${m.id}bar`)) el(`m${m.id}bar`).style.width=`${Math.max(0,Math.min(100,m.output||0))}%`;
      text(`escRpm${m.id}`,format(m.rpm));text(`escInput${m.id}`,format(m.input,'%'));
      text(`escVoltage${m.id}`,format(m.voltage,'V',1));text(`escCurrent${m.id}`,format(m.current,'A',2));
      text(`escState${m.id}`,!m.live?'NO DATA':m.status&0x7fff?'FAULT':'LIVE');
      if(el(`motorCard${m.id}`))el(`motorCard${m.id}`).dataset.escLive=String(m.live);
      if(append){histories[i].push({pwm:m.pwm,output:m.output});if(histories[i].length>60)histories[i].shift();}
      chart(`motorSpark${m.id}`,[histories[i].map(v=>v.output)],100);
    });
    if(mtPaint) mtPaint(data.motor_test);
    chart('motorPwmChart',histories.map(h=>h.map(v=>v.pwm)),2200);
    chart('motorOutputChart',histories.map(h=>h.map(v=>v.output)),100);
  }
  function reconnect(ws) {
    if(socket!==ws)return;socket=null;lastReceived=0;ws.onclose=ws.onmessage=ws.onerror=null;try{ws.close();}catch(_){}
    histories.forEach(h=>h.splice(0));render({},false,false);clearTimeout(retry);retry=setTimeout(connect,1000);
  }
  function connect(){
    if(socket)return;const ws=new WebSocket(window.airsentApiUrl('/telem',true));socket=ws;started=Date.now();
    ws.onmessage=event=>{if(socket!==ws)return;try{const data=JSON.parse(event.data);if(!finite(data.telemetry_age)||data.telemetry_age<0||data.telemetry_age>=2)return;latest=data;lastReceived=Date.now();render(data,true,true);}catch(error){console.error('Actuator telemetry:',error);}};
    ws.onclose=ws.onerror=()=>reconnect(ws);
  }
  setInterval(()=>{if(socket&&Date.now()-(lastReceived||started)>(lastReceived?maxAge:5000))reconnect(socket);else if(lastReceived)render(latest,true,false);},500);
  text('missionTime','--:--:--');render({},false,false);connect();
})();
