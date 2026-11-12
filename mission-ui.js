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
    link.rel = 'stylesheet'; link.href = 'mission-ui.css?v=4';
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
        <option value="bench">TEST MODE — checks bypassed (GPS / battery / parameters / sensors) · force-arms at T-10 · TAKES OFF at T-0</option></select>
      <label>AFTER TAKEOFF</label><select id="msnRoute"><option value="">HOVER — the pilot flies from the console</option>${routeOption()}</select>
      <label>BATTERY PACK ID (OPTIONAL)</label><input id="msnPack" maxlength="40" placeholder="e.g. TATTU-03" autocomplete="off">
      <label>TYPE <b style="color:#ffb800" id="msnWord">flight</b> TO CONFIRM</label><input id="msnConfirm" autocomplete="off" spellcheck="false">
      <div class="msn-error" id="msnErr"></div>
      <div class="msn-actions"><button class="msn-btn" id="msnCancel">CANCEL</button><button class="msn-btn go" id="msnStart">START COUNTDOWN</button></div>`);
    $('msnOp').value = M.operatorName === 'Operator' ? '' : M.operatorName;
    ($('msnOp').value ? $('msnName') : $('msnOp')).focus();
    $('msnCancel').onclick = () => d.remove();
    $('msnKind').onchange = () => {
      const bench = $('msnKind').value === 'bench';
      $('msnWord').textContent = bench ? 'test' : 'flight';
      $('msnRoute').disabled = bench;
      d.querySelector('h3').textContent = bench ? 'START A TEST FLIGHT (CHECKS BYPASSED)' : 'START A REAL MISSION';
    };
    const submit = async () => {
      const op = $('msnOp').value.trim(), name = $('msnName').value.trim();
      if (!op) { $('msnErr').textContent = 'Enter your name — it is recorded with every action.'; return; }
      M.operatorName = op;
      $('msnStart').disabled = true;
      const r = await M.send('mission_create', { name, total: Number($('msnTotal').value), pack_id: $('msnPack').value.trim(), confirm: $('msnConfirm').value,
        route: $('msnRoute').value === 'route', bench: $('msnKind').value === 'bench' });
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

  function render() {
    const s = M.state, list = faultList();
    renderBench(s);
    renderDashboardFaults(list);
    renderBadge(pilotBadge, list);
    renderBadge(overlayBadge, list);
    renderBadge(slotBadge, list);
    if (panelEl && panelEl.classList.contains('open')) panel();
    alertNewCritical(list);
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
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
