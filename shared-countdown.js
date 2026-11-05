/* Server-owned countdown; independent of telemetry and vehicle command sockets. */
(() => {
  'use strict';
  if (window.airsentSimulation) {
    ['lmBtnStart','lmBtnHold','lmBtnAbort','lmBtnReset','btnStart','btnHold','btnAbort','btnReset'].forEach(id=>{const e=document.getElementById(id);if(e)e.disabled=true;});
    const badge=document.getElementById('lmStatus') || document.getElementById('statusBadge');
    if(badge)badge.textContent='SITL · TIMER OFF';
    return;
  }
  const mode = (localStorage.getItem('airsentMode') || 'demo').toLowerCase();
  if (mode === 'demo') return;
  const operator = ['operator', 'actual', 'real'].includes(mode);
  const launchPage = !!document.getElementById('mainClock');
  const byId = id => document.getElementById(id);
  const pad = n => String(n).padStart(2, '0');
  const format = n => `${pad(Math.floor(n / 60))}:${pad(n % 60)}`;
  let socket, snapshot, receivedAt, authorized = false, connected = false;
  const ids = launchPage ? ['btnStart', 'btnHold', 'btnAbort', 'btnReset'] : ['lmBtnStart', 'lmBtnHold', 'lmBtnAbort', 'lmBtnReset'];
  const clock = byId(launchPage ? 'mainClock' : 'lmClock');
  const badge = byId(launchPage ? 'statusBadge' : 'lmStatus');
  const message = byId(launchPage ? 'abortReason' : 'lmFaultBar');
  const configIds = ['cfgMins', 'cfgSecs', 'cfgHandoverMins', 'cfgHandoverSecs', 'handoffInput'];
  function render() {
    const fresh = connected && snapshot && performance.now() - receivedAt < 3500;
    ids.forEach(id => { if (byId(id)) byId(id).disabled = true; });
    configIds.forEach(id => { if (byId(id)) byId(id).disabled = !fresh || !operator || snapshot.state !== 'standby'; });
    if (!fresh) {
      badge.textContent = 'SYNC OFFLINE';
      badge.className = launchPage ? 'status-badge abort' : 'launch-status-badge abort';
      clock.textContent = '--:--';
      return;
    }
    const d = snapshot, state = d.state;
    byId("launchMiniPanel")?.classList.toggle("has-countdown-message", state === "abort" || state === "hold");
    window.airsentRenderSharedCountdown?.(d);
    const remaining = d.remaining, elapsed = d.launchElapsed;
    clock.textContent = (state === 'launch' && !launchPage ? 'T+' : '') + format(state === 'launch' ? elapsed : remaining);
    clock.className = launchPage ? `clock ${state === 'abort' ? 'abort' : state === 'launch' ? 'launch' : state === 'hold' ? 'warning' : 'nominal'}` : `launch-clock${state === 'abort' ? ' abort' : state === 'hold' ? ' warn' : state === 'launch' ? ' launch' : ''}`;
    badge.textContent = ({standby:'STANDBY', counting:'COUNTING', hold:'HOLD', abort:'ABORT', launch:'T+ RUNNING'})[state];
    badge.className = (launchPage ? 'status-badge ' : 'launch-status-badge ') + state;
    if (message) {
      message.textContent = d.abortReason || (state === 'hold' ? 'HOLD' : '');
      message.style.display = d.abortReason || state === 'hold' ? 'block' : 'none';
    }
    const [start, hold, abort, reset] = ids.map(byId);
    start.disabled = !operator || state !== 'standby';
    hold.disabled = !operator || !['counting', 'hold'].includes(state);
    hold.textContent = state === 'hold' ? '▶ RESUME' : 'Ⅱ';
    abort.disabled = !operator || !['counting', 'hold'].includes(state);
    abort.style.display = state === 'abort' ? 'none' : '';
    reset.style.display = ['abort', 'launch'].includes(state) ? '' : 'none';
    reset.disabled = !operator;
    const sign = byId('clockSign');
    if (sign) sign.textContent = state === 'launch' ? 'T+' : 'T−';
    const fill = byId('progressFill');
    if (fill) fill.style.width = `${100 * (d.total - remaining) / d.total}%`;
    const handover = byId(launchPage ? 'handoverBadge' : 'lmHandoverBadge');
    if (handover) handover.style.display = d.handoverTriggered ? 'block' : 'none';
    if (launchPage && document.activeElement && !configIds.includes(document.activeElement.id)) {
      byId('cfgMins').value = Math.floor(d.total / 60);
      byId('cfgSecs').value = d.total % 60;
      byId('cfgHandoverMins').value = Math.floor(d.handoverAt / 60);
      byId('cfgHandoverSecs').value = d.handoverAt % 60;
      byId('handoffInput').value = d.total;
    }
  }
  function authenticate() {
    let password = sessionStorage.getItem('airsentSequencePassword');
    if (!password) password = window.prompt('Enter your operator login password to control the shared countdown.');
    if (!password) return false;
    sessionStorage.setItem('airsentSequencePassword', password);
    socket.send(JSON.stringify({type:'sequence_auth', password}));
    return true;
  }
  let pending = null;
  function send(cmd, extra = {}) {
    if (!operator || !connected || !snapshot || performance.now() - receivedAt >= 3500) return;
    if (!authorized) {
      pending = {cmd, extra};
      if (!authenticate()) pending = null;
      return;
    }
    socket.send(JSON.stringify({type:'sequence_command', cmd, revision:snapshot.revision, ...extra}));
  }
  ids.forEach((id, i) => byId(id).addEventListener('click', () => {
    const cmd = ['start', snapshot?.state === 'hold' ? 'resume' : 'hold', 'abort', 'reset'][i];
    if (cmd === 'reset' && !window.confirm('Reset the shared countdown for every operator and viewer?')) return;
    send(cmd);
  }));
  configIds.forEach(id => byId(id)?.addEventListener('change', () => {
    const total = id === 'handoffInput' ? Number(byId(id).value) : Number(byId('cfgMins').value) * 60 + Number(byId('cfgSecs').value);
    const handoverAt = Math.min(total, Number(byId('cfgHandoverMins').value) * 60 + Number(byId('cfgHandoverSecs').value));
    send('configure', {total, handoverAt});
  }));
  function connect() {
    const url = new URL('/sequence', location.origin);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = new WebSocket(url);
    socket.onopen = () => { connected = true; authorized = false; };
    socket.onmessage = event => {
      let data;
      try { data = JSON.parse(event.data); } catch (_) { return; }
      if (data.type === 'sequence_state') { snapshot = data; receivedAt = performance.now(); render(); }
      if (data.type === 'sequence_auth') {
        authorized = data.authorized === true;
        if (authorized && pending) { const p = pending; pending = null; send(p.cmd, p.extra); }
        if (!authorized) { pending = null; sessionStorage.removeItem('airsentSequencePassword'); window.alert('Operator password was not accepted.'); }
      }
      if (data.type === 'sequence_error') { pending = null; window.alert(data.message); }
    };
    socket.onclose = () => { connected = false; authorized = false; pending = null; render(); setTimeout(connect, 2000); };
    socket.onerror = () => socket.close();
  }
  setInterval(render, 250);
  render(); connect();
})();
