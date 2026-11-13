/* mission-ui.js — shared launch-sequencer UI built on mission-client.js:
   • fault badge + panel (dashboard header, pilot top bar, overlay)
   • dashboard countdown card, pilot top-bar chip
   • thin countdown overlay on every other page while a mission is active
   • create-mission and in-flight abort dialogs
   Everything displayed comes from the Jetson snapshot (or the demo imitation). */
(() => {
  'use strict';
  const M = window.airsentMission;
  const page = location.pathname.split('/').pop() || 'index.html';
  if (!M || ['index.html', 'signin.html', ''].includes(page)) return;

  const STAGE = {
    standby: ['STANDBY', 'standby'], countdown: ['COUNTDOWN', 'counting'], prearm: ['PRE-ARM', 'counting'],
    gonogo: ['GO / NO-GO', 'counting'], armed: ['ARMED', 'counting'], takeoff: ['TAKEOFF', 'launch'],
    flight: ['IN FLIGHT', 'launch'], landed: ['LANDED', 'launch'], safed: ['SAFED', 'launch'], aborted: ['ABORTED', 'abort'],
  };
  const PRE = ['countdown', 'prearm', 'gonogo', 'armed'];
  const AIR = ['takeoff', 'flight'];
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ago = t => { const s = Math.max(0, Date.now() / 1000 - t); return s < 60 ? Math.floor(s) + 's' : s < 3600 ? Math.floor(s / 60) + 'm' : Math.floor(s / 3600) + 'h'; };
  const stageLabel = s => s ? (s.held ? 'HOLD' : (STAGE[s.stage] || [s.stage.toUpperCase()])[0]) : 'NO DATA';
  const viewer = () => !M.canCommand;

  function addStyles() {
    if (document.querySelector('link[href^="mission-ui.css"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet'; link.href = 'mission-ui.css?v=5';
    document.head.append(link);
  }

  // ── toast ────────────────────────────────────────────────────────────────
  let toastTimer;
  function toast(msg, kind = '') {
    let t = document.querySelector('.msn-toast');
    if (!t) { t = document.createElement('div'); document.body.append(t); }
    t.className = 'msn-toast ' + kind; t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, kind === 'alert' ? 6000 : 3500);
  }

  async function act(action, data = {}, okMsg) {
    if (viewer()) { toast('Viewer mode — read only', 'err'); return { ok: false }; }
    const r = await M.send(action, data);
    toast(r.ok ? (okMsg || r.msg || 'Done') : (r.msg || 'Request failed'), r.ok ? '' : 'err');
    return r;
  }

  // ── dialogs ──────────────────────────────────────────────────────────────
  function dialog(html, danger) {
    const back = document.createElement('div');
    back.className = 'msn-dialog-back';
    back.innerHTML = `<div class="msn-dialog${danger ? ' danger' : ''}" role="dialog" aria-modal="true">${html}</div>`;
    back.addEventListener('mousedown', e => { if (e.target === back) back.remove(); });
    back.addEventListener('keydown', e => { if (e.key === 'Escape') back.remove(); });
    document.body.append(back);
    return back;
  }

  function routeOption() {
    const vm = M.vehicleMission || {};
    return vm.verified && vm.nav_count ? `<option value="route">FLY THE ROUTE ON THE DRONE (${vm.nav_count} waypoints, verified)</option>`
      : '<option value="" disabled>FLY THE ROUTE — no verified route on the drone (upload on the mission page)</option>';
  }

  function openCreate() {
    if (viewer()) { toast('Viewer mode — read only', 'err'); return; }
    if (M.active()) { location.href = 'launch.html'; return; }
    const d = dialog(`
      <h3>START A REAL MISSION</h3>
      <p>The Jetson creates a mission folder and starts recording. The countdown arms the aircraft at T-10 and takes off at T-0 — only with the RC pilot's switch in ARM.</p>
      <div class="msn-row">
        <div><label>OPERATOR (YOU)</label><input id="msnOp" maxlength="40" autocomplete="off"></div>
        <div><label>COUNTDOWN</label><select id="msnTotal"><option value="180">3:00</option><option value="300">5:00</option></select></div>
      </div>
      <label>MISSION NAME</label><input id="msnName" maxlength="48" placeholder="mission 1" autocomplete="off">
      <label>TYPE OF RUN</label><select id="msnKind"><option value="flight">REAL FLIGHT — all checks enforced, takes off at T-0</option>
        <option value="flight1">FLIGHT 1 — real checks · take off to 2 m · automatic check · climb to 5 m · hover 30 s · land</option>
        <option value="flight2">FLIGHT 2 — real checks · 5 / 10 / 15 / 20 m staircase · 360° turn · 5 m box · home · land</option>
        <option value="bench">TEST MODE — checks bypassed (GPS / battery / parameters / sensors) · force-arms at T-10 · TAKES OFF at T-0</option></select>
      <div class="msn-note" id="msnKindNote" style="display:none;font:10px var(--msn-mono, monospace);color:#9fb3c4;margin:4px 0 2px;line-height:1.5">
        FLIGHT 1: hovers 20 s at 2 m while the Jetson checks height hold (±0.6 m), tilt (&lt;12°), vibration (&lt;30), drift (&lt;1.5 m),
        battery sag and EKF. All good → climbs to 5 m and hovers 30 s, then lands. Anything wrong → lands from 2 m.
        The RC pilot can take over at any time (mode switch) — the Jetson then stops sending commands.</div>
      <div class="msn-note" id="msnKindNote2" style="display:none;font:10px var(--msn-mono, monospace);color:#9fb3c4;margin:4px 0 2px;line-height:1.5">
        FLIGHT 2 (fly it only after Flight 1 passed and its tuning is written): takes off to 5 m, hovers 15 s and is checked at 5, 10, 15 and 20 m
        (height ±0.6 m, tilt &lt;12°, vibration &lt;30, drift &lt;2.5 m, battery, EKF). Then a 360° turn, a 5 m box (front, left, back, right),
        back over the home point at 20 m, and a landing. A failed check or an unsafe reading lands the aircraft where it is.
        The RC pilot can take over at any time. Needs 20 m of clear sky and a fence set above 20 m.</div>
      <label>AFTER TAKEOFF</label><select id="msnRoute"><option value="">HOVER — the pilot flies from the console</option>${routeOption()}</select>
      <label>BATTERY PACK ID (OPTIONAL)</label><input id="msnPack" maxlength="40" placeholder="e.g. TATTU-03" autocomplete="off">
      <label>TYPE <b style="color:#ffb800" id="msnWord">flight</b> TO CONFIRM</label><input id="msnConfirm" autocomplete="off" spellcheck="false">
      <div class="msn-error" id="msnErr"></div>
      <div class="msn-actions"><button class="msn-btn" id="msnCancel">CANCEL</button><button class="msn-btn go" id="msnStart">START COUNTDOWN</button></div>`);
    $('msnOp').value = M.operatorName === 'Operator' ? '' : M.operatorName;
    ($('msnOp').value ? $('msnName') : $('msnOp')).focus();
    $('msnCancel').onclick = () => d.remove();
    $('msnKind').onchange = () => {
      const kind = $('msnKind').value, bench = kind === 'bench';
      $('msnWord').textContent = bench ? 'test' : 'flight';
      $('msnRoute').disabled = bench || kind === 'flight1' || kind === 'flight2';
      $('msnKindNote').style.display = kind === 'flight1' ? '' : 'none';
      $('msnKindNote2').style.display = kind === 'flight2' ? '' : 'none';
      d.querySelector('h3').textContent = bench ? 'START A TEST FLIGHT (CHECKS BYPASSED)' : kind === 'flight1' ? 'START FLIGHT 1 (2 m → 5 m → LAND)'
        : kind === 'flight2' ? 'START FLIGHT 2 (STAIRCASE → 360° → BOX → LAND)' : 'START A REAL MISSION';
    };
    const submit = async () => {
      const op = $('msnOp').value.trim(), name = $('msnName').value.trim();
      if (!op) { $('msnErr').textContent = 'Enter your name — it is recorded with every action.'; return; }
      M.operatorName = op;
      $('msnStart').disabled = true;
      const r = await M.send('mission_create', { name, total: Number($('msnTotal').value), pack_id: $('msnPack').value.trim(), confirm: $('msnConfirm').value,
        route: $('msnKind').value === 'flight' && $('msnRoute').value === 'route', bench: $('msnKind').value === 'bench',
        profile: ['flight1', 'flight2'].includes($('msnKind').value) ? $('msnKind').value : undefined });
      $('msnStart').disabled = false;
      if (!r.ok) { $('msnErr').textContent = r.msg || 'Could not start'; return; }
      d.remove(); toast(r.msg);
    };
    $('msnStart').onclick = submit;
    d.querySelectorAll('input').forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); }));
  }

  /** One tap before takeoff. In the air: choose land here or return home. */
  function abort() {
    const s = M.state;
    if (!s) return;
    if (PRE.includes(s.stage)) { act('mission_abort', { reason: 'Aborted by ' + M.operatorName }, 'Mission aborted — aircraft disarmed'); return; }
    if (!AIR.includes(s.stage)) return;
    const d = dialog(`
      <h3>ABORT FLIGHT</h3><p>Choose what the aircraft does now. The RC pilot can still override.</p>
      <div class="msn-actions" style="justify-content:stretch">
        <button class="msn-btn abort" id="msnLand" style="flex:1">▼ LAND HERE</button>
        <button class="msn-btn hold" id="msnRtl" style="flex:1">⌂ RETURN HOME</button>
      </div>
      <div class="msn-actions"><button class="msn-btn" id="msnAbCancel">CANCEL</button></div>`, true);
    $('msnLand').onclick = () => { d.remove(); act('mission_abort', { mode: 'land' }); };
    $('msnRtl').onclick = () => { d.remove(); act('mission_abort', { mode: 'rtl' }); };
    $('msnAbCancel').onclick = () => d.remove();
    $('msnLand').focus();
  }

  function holdOrResume() {
    const s = M.state;
    if (!s || !PRE.includes(s.stage)) return;
    if (s.held) act('mission_resume'); else act('mission_hold', {}, 'Countdown held');
  }

  function go() { act('mission_go', {}, 'GO recorded — RC pilot: switch to ARM'); }
  function close() {
    if (confirm('Close this mission? The summary is written to the Jetson and the console returns to STANDBY.')) act('mission_close');
  }

  window.airsentMissionUI = { openCreate, abort, holdOrResume, go, close, toast, act, openFaults: () => panel(true), STAGE, stageLabel };

  // ── faults ───────────────────────────────────────────────────────────────
  const localAcks = new Set();
  let localFaults = new Map();
  window.airsentFaultsLocal = map => { localFaults = new Map(map); render(); };   // from telemetry.js
  const seenCritical = new Set();

  // Acks are shown at once (greyed) while the Jetson confirms; it re-sends fault state every 0.5–2 s.
  const pendingAcks = new Map();          // fault id -> time clicked
  const SEV_ORDER = { critical: 0, warning: 1, info: 2 };
  function sortFaults(list) {             // unacknowledged first, then by severity, then oldest first
    return list.sort((a, b) => (!!a.acked_by - !!b.acked_by) || ((SEV_ORDER[a.severity] ?? 3) - (SEV_ORDER[b.severity] ?? 3)) || ((a.since || 0) - (b.since || 0)));
  }

  function faultList() {
    const live = M.mode !== 'demo';
    const fresh = M.faults && M.stateFresh();
    const now = Date.now();
    pendingAcks.forEach((t, id) => { if (now - t > 10000) pendingAcks.delete(id); });
    const list = fresh ? M.faults.active.map(f => {
      if (f.acked_by) { pendingAcks.delete(f.id); return { ...f, local: false }; }
      return { ...f, acked_by: pendingAcks.has(f.id) || pendingAcks.has('all') ? M.operatorName || 'you' : null, local: false };
    }) : [];
    if (live && !M.linkOk()) list.unshift({ id: 'local:link', key: 'local:link', severity: 'critical', source: 'Console',
      message: 'No telemetry from the Jetson — fault list may be out of date', since: Date.now() / 1000, acked_by: localAcks.has('local:link') ? M.operatorName : null, local: true });
    if (!live || !fresh) localFaults.forEach((f, key) => list.push({ id: 'local:' + key, key, severity: f.level === 'fault' ? 'critical' : 'warning',
      source: 'Console', message: f.message, since: null, acked_by: localAcks.has('local:' + key) ? M.operatorName : null, local: true }));
    return sortFaults(list);
  }

  async function ack(id) {
    if (viewer()) { toast('Viewer mode — read only', 'err'); return; }
    if (id === 'all') {
      faultList().forEach(f => { if (f.local) localAcks.add(f.id); else if (!f.acked_by) pendingAcks.set(f.id, Date.now()); });
      render();
      if (M.faults && M.faults.active.length) {
        const r = await act('fault_ack', { fault_id: 'all' }, 'All faults acknowledged');
        if (!r.ok) { pendingAcks.clear(); render(); }
      }
      return;
    }
    if (id.startsWith('local:')) { localAcks.add(id); render(); return; }
    pendingAcks.set(id, Date.now()); render();
    const r = await act('fault_ack', { fault_id: id }, 'Fault acknowledged — it stays (greyed) until the problem is fixed');
    if (!r.ok) { pendingAcks.delete(id); render(); }
  }

  function faultHtml(f, cleared) {
    const sev = cleared ? 'cleared' : f.severity;
    const acked = !cleared && f.acked_by;
    const meta = [esc(f.source), f.since ? 'since ' + ago(f.since) : '', f.acked_by ? 'ACK ' + esc(f.acked_by) + (f.acked_at ? ' ' + ago(f.acked_at) + ' ago' : '') : '',
      cleared && f.cleared_at ? 'cleared ' + ago(f.cleared_at) + ' ago' : ''].filter(Boolean).join(' · ');
    const btn = acked ? '<span class="msn-acked-tag">ACK’D</span>'
      : !cleared && !viewer() ? `<button class="msn-btn msn-ack" data-ack="${esc(f.id)}">ACK</button>` : '<span></span>';
    return `<div class="msn-fault ${sev}${acked ? ' acked' : ''}"><span class="msn-sev ${sev}">${cleared ? 'CLEARED' : esc(f.severity).toUpperCase()}</span>
      <span class="msn-fault-msg">${esc(f.message)}</span>${btn}<span class="msn-fault-meta">${meta}</span></div>`;
  }

  let panelEl;
  function panel(open) {
    if (!panelEl) {
      panelEl = document.createElement('aside');
      panelEl.className = 'msn-fault-panel';
      panelEl.innerHTML = `<div class="msn-fp-head"><h3>FAULTS</h3><button class="msn-btn" data-ack="all">ACK ALL</button><button class="msn-btn" data-close>✕</button></div><div class="msn-fp-body"></div>`;
      setTimeout(placeMute, 0);
      panelEl.addEventListener('click', e => {
        const a = e.target.closest('[data-ack]'); if (a) ack(a.dataset.ack);
        if (e.target.closest('[data-close]')) panelEl.classList.remove('open');
      });
      document.body.append(panelEl);
    }
    if (open !== undefined) panelEl.classList.toggle('open', open);
    if (!panelEl.classList.contains('open')) return;
    const active = faultList(), recent = (M.faults && M.faults.recent) || [];
    const unacked = active.filter(f => !f.acked_by).length;
    panelEl.querySelector('[data-ack="all"]').style.display = unacked && !viewer() ? '' : 'none';
    panelEl.querySelector('.msn-fp-body').innerHTML =
      `<div class="msn-fp-section">ACTIVE (${active.length}${active.length > unacked ? ` · ${active.length - unacked} ACKNOWLEDGED` : ''})</div>` + (active.length ? active.map(f => faultHtml(f)).join('') : '<div class="msn-empty">No active faults</div>') +
      (recent.length ? `<div class="msn-fp-section">RECENTLY CLEARED</div>` + recent.slice(0, 15).map(f => faultHtml(f, true)).join('') : '');
  }
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && panelEl) panelEl.classList.remove('open'); });

  function badge(container) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'msn-fault-badge'; b.title = 'Faults reported by the Jetson';
    b.innerHTML = 'FAULTS <b>0</b>';
    b.addEventListener('click', e => { e.stopPropagation(); panel(!(panelEl && panelEl.classList.contains('open'))); });
    container.append(b);
    return b;
  }
  function renderBadge(b, list) {
    if (!b) return;
    const open = list.filter(f => !f.acked_by), crit = open.filter(f => f.severity === 'critical');
    b.querySelector('b').textContent = list.length;
    b.classList.toggle('has-critical', crit.length > 0);
    b.classList.toggle('has-warn', !crit.length && open.length > 0);
    b.classList.toggle('all-acked', list.length > 0 && !open.length);
    b.classList.toggle('unacked', crit.length > 0);
    b.title = list.length ? `${list.length} active fault(s), ${list.length - open.length} acknowledged` : 'Faults reported by the Jetson';
  }

  // Dashboard header fault counter (existing markup).
  function renderDashboardFaults(list) {
    const counter = $('faultCounter'); if (!counter) return;
    $('faultCountNum').textContent = list.length;
    const open = list.filter(f => !f.acked_by);
    counter.classList.remove('has-warn', 'has-fault', 'msn-all-acked');
    if (open.length) counter.classList.add(open.some(f => f.severity === 'critical') ? 'has-fault' : 'has-warn');
    else if (list.length) counter.classList.add('msn-all-acked');
    counter.classList.toggle('msn-unacked', list.some(f => f.severity === 'critical' && !f.acked_by));
    const box = $('faultPanelList');
    if (box) box.innerHTML = list.map(f => `<div class="fault-panel-item ${f.severity === 'critical' ? 'fault' : 'warn'}${f.acked_by ? ' acked' : ''}">
      <span class="fault-panel-item-badge">${f.severity === 'critical' ? 'FAULT' : 'WARN'}</span>
      <span class="fault-panel-item-msg"><span class="msn-fault-src">${esc(f.source)}</span>${esc(f.message)}</span>
      ${f.acked_by ? `<span class="fault-acked-by">ACK’D · ${esc(f.acked_by)}</span>` : viewer() ? '' : `<button class="fault-ack-btn" data-ack="${esc(f.id)}">ACK</button>`}</div>`).join('');
    const empty = $('faultPanelEmpty'); if (empty) empty.style.display = list.length ? 'none' : 'block';
    const all = $('faultDismissAll');
    if (all) { all.textContent = 'Acknowledge all'; all.style.display = list.some(f => !f.acked_by) && !viewer() ? 'block' : 'none'; }
  }
  function wireDashboardFaults() {
    const counter = $('faultCounter'); if (!counter) return;
    counter.addEventListener('click', e => { const a = e.target.closest('[data-ack]'); if (a) { e.stopPropagation(); ack(a.dataset.ack); } }, true);
    const all = $('faultDismissAll');
    if (all) { all.removeAttribute('onclick'); all.addEventListener('click', e => { e.stopPropagation(); ack('all'); }); }
  }

  function alertNewCritical(list) {
    list.forEach(f => {
      if (f.severity === 'critical' && !f.acked_by && !seenCritical.has(f.id)) {
        seenCritical.add(f.id);
        if (seenCritical.size > 1 || M.active()) toast('⚠ ' + f.message, 'alert');
      }
    });
  }

  // ── dashboard countdown card (existing lm* markup) ───────────────────────
  function wireDashboardCard() {
    if (!$('lmClock')) return false;
    const btn = id => { const b = $(id); if (!b) return null; const c = b.cloneNode(true); b.replaceWith(c); return c; };
    const start = btn('lmBtnStart'), hold = btn('lmBtnHold'), ab = btn('lmBtnAbort'), reset = btn('lmBtnReset');
    start && start.addEventListener('click', () => {
      const s = M.state;
      if (!s || s.stage === 'standby') openCreate();
      else if (['gonogo', 'prearm'].includes(s.stage) && !s.go) go();
      else location.href = 'launch.html';
    });
    hold && hold.addEventListener('click', holdOrResume);
    ab && ab.addEventListener('click', abort);
    reset && reset.addEventListener('click', close);
    const modal = $('lmResetModal'); if (modal) modal.remove();
    return true;
  }
  function renderDashboardCard(s) {
    const clock = $('lmClock'); if (!clock) return;
    const label = document.querySelector('.launch-clock-label');
    const text = M.clockText();
    if (label) label.textContent = text.startsWith('T+') ? 'T+' : 'T−';
    clock.textContent = text.slice(2);
    const air = s && ['takeoff', 'flight', 'landed', 'safed'].includes(s.stage);
    clock.className = 'launch-clock' + (!s ? '' : s.held ? ' warn' : s.stage === 'aborted' ? ' abort' : air ? ' launch' : '');
    const badgeEl = $('lmStatus');
    if (badgeEl) { badgeEl.textContent = M.stateFresh() || M.mode === 'demo' ? stageLabel(s) : 'NO JETSON'; badgeEl.className = 'launch-status-badge ' + (!s ? 'abort' : s.held ? 'hold' : (STAGE[s.stage] || [0, 'standby'])[1]); }
    const bar = $('lmFaultBar'), hb = $('lmHandoverBadge'), panelBox = $('launchMiniPanel');
    let msg = '', color = '#ffb800';
    if (s && s.held) { msg = '⏸ ' + s.hold_reason; color = '#ff5566'; }
    else if (s && s.stage === 'aborted') { msg = '✗ ' + ((s.abort && s.abort.reason) || 'ABORTED'); color = '#ff5566'; }
    else if (s && s.stage === 'gonogo') msg = s.go ? (s.rc_gate === 'ARM' ? 'GO ✓ · RC ARM ✓' : 'GO ✓ · RC pilot: switch to ARM') : 'GO / NO-GO — confirm area clear';
    else if (s && s.stage === 'flight' && s.abort) { msg = '✗ ABORT — ' + s.abort.mode.toUpperCase(); color = '#ff5566'; }
    else if (s && s.stage === 'safed') { msg = '✓ SAFED — safe to approach'; color = '#00ff88'; }
    if (bar) { bar.textContent = msg; bar.style.color = color; }
    if (hb) { hb.style.display = s && s.stage === 'prearm' && !s.held ? '' : 'none'; hb.textContent = '⚡ VEHICLE HANDOVER'; }
    if (panelBox) { panelBox.classList.toggle('alert', !!s && (s.held || s.stage === 'aborted')); panelBox.classList.toggle('has-countdown-message', !!msg); }
    const start = $('lmBtnStart'), hold = $('lmBtnHold'), ab = $('lmBtnAbort'), reset = $('lmBtnReset');
    const stage = s ? s.stage : 'none', pic = M.isPIC();
    if (start) {
      start.disabled = viewer() || !s || !(stage === 'standby' || (['gonogo', 'prearm'].includes(stage) && !s.go && pic));
      start.textContent = stage === 'standby' || !s ? '▶ GO' : '✓ GO';
      start.style.display = ['safed', 'aborted'].includes(stage) ? 'none' : '';
    }
    if (hold) { hold.disabled = viewer() || !PRE.includes(stage) || (s.held && !pic); hold.textContent = s && s.held ? '▶' : '⏸'; hold.title = s && s.held ? 'Resume (pilot in command)' : 'Hold'; }
    if (ab) { ab.disabled = viewer() || !(PRE.includes(stage) || AIR.includes(stage)); ab.style.display = ['safed', 'aborted'].includes(stage) ? 'none' : ''; }
    if (reset) { reset.style.display = ['safed', 'aborted'].includes(stage) ? '' : 'none'; reset.textContent = '✓ CLOSE'; reset.disabled = viewer() || !pic; }
  }

  // ── pilot top-bar chip ───────────────────────────────────────────────────
  let chip, chipAbort, pilotBadge;
  function buildPilot() {
    const slot = $('missionPilotSlot'); if (!slot) return;
    chip = document.createElement('a');
    chip.className = 'msn-chip'; chip.href = 'launch.html'; chip.title = 'Open the launch sequencer';
    chip.innerHTML = '<span class="msn-chip-clock">T-03:00</span><span class="msn-chip-stage">STANDBY</span>';
    chipAbort = document.createElement('button');
    chipAbort.className = 'msn-btn abort msn-chip-abort msn-hidden'; chipAbort.textContent = '■ ABORT';
    chipAbort.addEventListener('click', abort);
    slot.append(chip, chipAbort);
    pilotBadge = badge(slot);
  }
  function renderPilot(s) {
    if (!chip) return;
    chip.querySelector('.msn-chip-clock').textContent = M.clockText();
    const st = chip.querySelector('.msn-chip-stage');
    st.textContent = s && s.held ? 'HOLD · ' + s.hold_reason : stageLabel(s);
    st.classList.toggle('msn-blink', !!(s && s.held));
    chip.classList.toggle('held', !!(s && s.held));
    chipAbort.classList.toggle('msn-hidden', !s || viewer() || !(PRE.includes(s.stage) || AIR.includes(s.stage)));
  }

  // ── overlay on every other page ──────────────────────────────────────────
  let slotBadge;
  function buildSlot() { const el = $('missionFaultSlot'); if (el) slotBadge = badge(el); }

  let overlay, overlayBadge;
  const overlayPages = !['dashboard.html', 'pilot.html', 'launch.html'].includes(page);
  function buildOverlay() {
    if (!overlayPages) return;
    overlay = document.createElement('div');
    overlay.className = 'msn-overlay msn-hidden';
    overlay.innerHTML = '<span class="msn-ov-name"></span><span class="msn-ov-clock"></span><span class="msn-ov-stage"></span><span class="msn-ov-reason"></span>';
    overlay.addEventListener('click', e => { if (!e.target.closest('button')) location.href = 'launch.html'; });
    overlayBadge = badge(overlay);
    const ab = document.createElement('button');
    ab.className = 'msn-btn abort'; ab.textContent = '■ ABORT'; ab.dataset.role = 'abort';
    ab.addEventListener('click', e => { e.stopPropagation(); abort(); });
    overlay.append(ab);
    document.body.append(overlay);
  }
  function renderOverlay(s) {
    if (!overlay) return;
    const show = !!s && s.stage !== 'standby';
    overlay.classList.toggle('msn-hidden', !show);
    if (!show) return;
    overlay.classList.toggle('held', s.held);
    overlay.querySelector('.msn-ov-name').textContent = (s.mission && s.mission.name) || 'MISSION';
    overlay.querySelector('.msn-ov-clock').textContent = M.clockText();
    const st = overlay.querySelector('.msn-ov-stage');
    st.textContent = stageLabel(s); st.classList.toggle('msn-blink', s.held);
    overlay.querySelector('.msn-ov-reason').textContent = s.held ? s.hold_reason : (s.abort ? 'ABORT — ' + s.abort.reason : '');
    overlay.querySelector('[data-role="abort"]').classList.toggle('msn-hidden', viewer() || !(PRE.includes(s.stage) || AIR.includes(s.stage)));
  }

  // ── render loop ──────────────────────────────────────────────────────────
  let dashboardCard = false;
  // Bench test: an unmissable strip on every page while one is running.
  let benchEl = null;
  function renderBench(s) {
    const on = !!(s && s.mission && s.mission.bench && s.stage && s.stage !== 'standby');
    if (on && !benchEl) {
      benchEl = document.createElement('div');
      benchEl.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:10050;background:repeating-linear-gradient(45deg,#ffb800 0 18px,#1a1300 18px 36px);' +
        'color:#000;font:700 12px "Share Tech Mono",monospace;letter-spacing:2px;text-align:center;padding:4px;pointer-events:none';
      benchEl.innerHTML = '<span style="background:#ffb800;padding:2px 10px">TEST MODE — CHECKS BYPASSED · FORCE-ARMS AT T-10 · TAKES OFF AT T-0 · RC KILL SWITCH STILL ACTIVE</span>';
      document.body.append(benchEl);
    }
    if (!on && benchEl) { benchEl.remove(); benchEl = null; }
  }

  // FLIGHT 1: live progress strip on every page while the profile runs.
  let f1El = null;
  const F1_STEPS = [['hold_low', 'CHECK AT 2 m'], ['climb', 'CLIMB'], ['hold_high', 'HOVER 5 m'], ['landing', 'LAND']];
  function flight2Html(s, st) {
    const labels = (st && st.labels) || ['5 m', '10 m', '15 m', '20 m', '360°', 'BOX', 'HOME', 'LAND'];
    const plan = (st && st.plan) || { alts: [5, 10, 15, 20], hold: 15, box: 5 };
    const stopped = st && st.failed ? st.at_pos : null;
    const pos = st ? (st.pos == null ? 0 : st.pos) : -1;
    const res = (st && st.results) || [];
    const steps = labels.map((label, i) => {
      const bad = stopped === i || (res[i] && res[i].ok === false);
      const done = !bad && (pos > i || (st && st.name === 'done')) && !(stopped != null && i >= stopped);
      const now = !bad && i === pos && !(st && ['done', 'released'].includes(st.name)) && !(st && st.failed);
      const col = bad ? '#ff4d4d' : now ? '#00c8ff' : done ? '#00e383' : '#4d6478';
      let extra = '';
      if (now && st.name === 'hold' && st.remaining != null) extra = ' ' + Math.ceil(st.remaining) + 's';
      else if (now && st.name === 'yaw' && st.yaw_done != null) extra = ' ' + Math.min(360, Math.abs(st.yaw_done)) + '°';
      else if (now && st.name === 'leg' && st.leg != null) extra = ' ' + (st.leg + 1) + '/4';
      return `<span style="color:${col}">${bad ? '✗' : done ? '✓' : now ? '▶' : '·'} ${label}${extra}</span>`;
    }).join('<span style="color:#4d6478"> → </span>');
    let msg = '';
    if (!st) msg = s.stage === 'takeoff' ? `taking off to ${plan.alts[0]} m` : `staged second flight · ${plan.alts.join(' / ')} m staircase → 360° → ${plan.box} m box → home → land`;
    else if (st.name === 'released') msg = `<span style="color:#ffb800">HANDED TO THE PILOT — ${esc(st.why || '')}</span>`;
    else if (st.failed) msg = `<span style="color:#ffb800">${esc(st.why || '')}</span>`;
    else if (st.name === 'done') msg = 'FLIGHT 2 complete';
    else if (res.length) { const r = res[res.length - 1], c = r.summary || {}; msg = `<span style="color:#00e383">${r.alt} m check passed</span> · height ±${c.alt_err_max} m · tilt ${c.tilt_max}° · vib ${c.vib_max}`; }
    return `<span style="color:#00c8ff;font-weight:700">FLIGHT 2</span> &nbsp; ${steps}${msg ? `<div style="margin-top:3px;font-size:10px;color:#9fb3c4">${msg}</div>` : ''}`;
  }

  function renderFlight1(s) {
    const st = s && s.step, on = !!(s && (s.profile === 'flight1' || s.profile === 'flight2') && s.stage && !['standby', 'safed', 'aborted'].includes(s.stage));
    if (!on) { if (f1El) { f1El.remove(); f1El = null; } return; }
    if (!f1El) {
      f1El = document.createElement('div');
      f1El.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);bottom:34px;z-index:10049;background:rgba(6,13,20,.94);border:1px solid rgba(0,200,255,.5);' +
        'padding:6px 12px;font:11px "Share Tech Mono",monospace;letter-spacing:1px;color:#c7d6e3;pointer-events:none;box-shadow:0 6px 20px rgba(0,0,0,.6);max-width:92vw';
      document.body.append(f1El);
    }
    if (s.profile === 'flight2') { f1El.innerHTML = flight2Html(s, st); return; }
    const plan = (st && st.plan) || { low_alt: 2, low_hold: 20, high_alt: 5, high_hold: 30 };
    const cur = st ? st.name : (s.stage === 'takeoff' ? 'takeoff' : null);
    const idx = F1_STEPS.findIndex(x => x[0] === cur);
    const steps = F1_STEPS.map(([k, label], i) => {
      const done = idx > i || cur === 'done', now = k === cur;
      const col = now ? (st && st.failed && k === 'landing' ? '#ffb800' : '#00c8ff') : done ? '#00e383' : '#4d6478';
      return `<span style="color:${col}">${done ? '✓' : now ? '▶' : '·'} ${label}${now && st && st.remaining != null ? ' ' + Math.ceil(st.remaining) + 's' : ''}</span>`;
    }).join('<span style="color:#4d6478"> → </span>');
    let msg = '';
    if (!st) msg = s.stage === 'takeoff' ? `taking off to ${plan.low_alt} m` : `staged first flight · ${plan.low_alt} m check → ${plan.high_alt} m hover ${plan.high_hold}s → land`;
    else if (st.name === 'released') msg = `<span style="color:#ffb800">HANDED TO THE PILOT — ${esc(st.why || '')}</span>`;
    else if (st.failed) msg = `<span style="color:#ffb800">${esc(st.why || '')}</span>`;
    else if (st.checks && st.checks.ok && st.name !== 'hold_low') { const c = st.checks.summary || {}; msg = `<span style="color:#00e383">2 m check passed</span> · height ±${c.alt_err_max} m · tilt ${c.tilt_max}° · vib ${c.vib_max}`; }
    else if (st.name === 'done') msg = 'FLIGHT 1 complete';
    f1El.innerHTML = `<span style="color:#00c8ff;font-weight:700">FLIGHT 1</span> &nbsp; ${steps}${msg ? `<div style="margin-top:3px;font-size:10px;color:#9fb3c4">${msg}</div>` : ''}`;
  }

  function render() {
    const s = M.state, list = faultList();
    renderBench(s);
    renderFlight1(s);
    renderDashboardFaults(list);
    renderBadge(pilotBadge, list);
    renderBadge(overlayBadge, list);
    renderBadge(slotBadge, list);
    if (panelEl && panelEl.classList.contains('open')) panel();
    alertNewCritical(list);
    if (window.AirsentAlarm) window.AirsentAlarm.update(list, { viewer: viewer(), stage: s ? s.stage : null, armed: !!(s && s.armed) });
    if (dashboardCard) renderDashboardCard(s);
    renderPilot(s);
    renderOverlay(s);
  }

  function init() {
    addStyles();
    try { if (typeof _faultMap !== 'undefined') localFaults = new Map(_faultMap); } catch (e) {}
    dashboardCard = wireDashboardCard();
    wireDashboardFaults();
    buildPilot();
    buildOverlay();
    buildSlot();
    M.on(render);
    render();
    loadAlarm();
  }

  // Fault tones (fault-audio.js): a small mute toggle inside the fault panels.
  function loadAlarm() {
    const add = () => { placeMute(); render(); };
    if (window.AirsentAlarm) return add();
    const sc = document.createElement('script');
    sc.src = 'fault-audio.js?v=6'; sc.onload = add;
    document.head.appendChild(sc);
  }
  function placeMute() {
    const A = window.AirsentAlarm; if (!A) return;
    const heads = [panelEl && panelEl.querySelector('.msn-fp-head'), document.querySelector('#faultPanel .fault-panel-header')];
    heads.forEach(h => {
      if (!h || h.querySelector('.aa-mute')) return;
      if (h.classList.contains('fault-panel-header')) { h.style.display = 'flex'; h.style.alignItems = 'center'; }
      const first = h.querySelector('[data-ack="all"]');
      h.insertBefore(A.button(), first || null);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
