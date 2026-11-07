/* Onboard (Pixhawk) failsafes: what is actually set on the drone.
 * Values come from the Jetson in telemetry (shared by every console, viewers included).
 * Changes go through the Jetson's param_write, which confirms each value by read-back.
 * Used by failsafe.html (tiles + editor) and dashboard.html (failsafe box).
 */
(() => {
  'use strict';
  const M = () => window.airsentMission;
  const mode = (localStorage.getItem('airsentMode') || 'demo').toLowerCase();
  const isDemo = mode === 'demo';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // [value, label, short]
  const RC_ACT = [[0, 'Disabled', 'OFF'], [1, 'Return to launch (RTL)', 'RTL'], [2, 'Continue mission in AUTO, else RTL', 'CONTINUE'], [3, 'Land', 'LAND'],
                  [4, 'SmartRTL, else RTL', 'SMARTRTL'], [5, 'SmartRTL, else Land', 'SMARTRTL'], [6, 'Auto DO_LAND_START, else RTL', 'AUTO LAND'], [7, 'Brake, else Land', 'BRAKE']];
  const GCS_ACT = [[0, 'Disabled', 'OFF'], [1, 'Return to launch (RTL)', 'RTL'], [2, 'Continue mission in AUTO, else RTL', 'CONTINUE'], [3, 'SmartRTL, else RTL', 'SMARTRTL'],
                   [4, 'SmartRTL, else Land', 'SMARTRTL'], [5, 'Land', 'LAND'], [6, 'Auto DO_LAND_START, else RTL', 'AUTO LAND'], [7, 'Brake, else Land', 'BRAKE']];
  const BATT_ACT = [[0, 'None (warn only)', 'OFF'], [1, 'Land', 'LAND'], [2, 'Return to launch (RTL)', 'RTL'], [3, 'SmartRTL, else RTL', 'SMARTRTL'],
                    [4, 'SmartRTL, else Land', 'SMARTRTL'], [5, 'Terminate (motors off)', 'TERMINATE']];
  const EKF_ACT = [[1, 'Land', 'LAND'], [2, 'Alt Hold', 'ALTHOLD'], [3, 'Land, even in Stabilize', 'LAND']];
  const FENCE_ACT = [[0, 'Report only', 'REPORT'], [1, 'RTL, else Land', 'RTL'], [2, 'Always Land', 'LAND'], [3, 'SmartRTL, else RTL, else Land', 'SMARTRTL'],
                     [4, 'Brake, else Land', 'BRAKE'], [5, 'SmartRTL, else Land', 'SMARTRTL']];
  const ON_OFF = [[0, 'Off', 'OFF'], [1, 'On', 'ON']];

  // p: name, label, opts | unit/min/max/step, int, scale (shown = raw * scale), rec (raw), primary
  const DEFS = [
    { id: 'rc', tile: 6, rule: 0, title: 'RC signal lost', dash: 'RC LOSS',
      what: 'The RC receiver loses the transmitter: throttle drops below the threshold the receiver outputs on signal loss, or no RC input for the timeout.',
      params: [
        { name: 'FS_THR_ENABLE', label: 'Action', opts: RC_ACT, int: true, rec: 1, primary: true },
        { name: 'FS_THR_VALUE', label: 'Throttle PWM threshold', unit: 'µs', min: 910, max: 1100, step: 1, int: true, rec: 975 },
        { name: 'RC_FS_TIMEOUT', label: 'No-input timeout', unit: 's', min: 0.1, max: 3, step: 0.1, rec: 1 }] },
    { id: 'battery', tile: 4, rule: 2, title: 'Battery low / critical', dash: 'BATTERY',
      what: 'Low level: return home with reserve. Critical level: land immediately. Voltages are for the 6S pack under load.',
      params: [
        { name: 'BATT_FS_LOW_ACT', label: 'Low — action', opts: BATT_ACT, int: true, rec: 2, primary: true },
        { name: 'BATT_LOW_VOLT', label: 'Low — voltage', unit: 'V', min: 0, max: 30, step: 0.1, rec: 21.6 },
        { name: 'BATT_LOW_MAH', label: 'Low — remaining capacity (0 = off)', unit: 'mAh', min: 0, max: 20000, step: 50 },
        { name: 'BATT_FS_CRT_ACT', label: 'Critical — action', opts: BATT_ACT, int: true, rec: 1 },
        { name: 'BATT_CRT_VOLT', label: 'Critical — voltage', unit: 'V', min: 0, max: 30, step: 0.1, rec: 21.0 },
        { name: 'BATT_CRT_MAH', label: 'Critical — remaining capacity (0 = off)', unit: 'mAh', min: 0, max: 20000, step: 50 }] },
    { id: 'gcs', tile: 2, rule: 1, title: 'Jetson link lost', dash: 'JETSON LOST',
      what: 'The Pixhawk stops receiving the Jetson\'s 1 Hz heartbeat (Jetson crashed, lost power, or the server stopped). Loss of the 5G / console link is handled by ASC on the Jetson (coming later).',
      params: [
        { name: 'FS_GCS_ENABLE', label: 'Action', opts: GCS_ACT, int: true, rec: 1, primary: true },
        { name: 'FS_GCS_TIMEOUT', label: 'Heartbeat timeout', unit: 's', min: 2, max: 120, step: 1, rec: 5 },
        { name: 'SYSID_MYGCS', label: 'Ground-station system id (the Jetson uses 255)', min: 1, max: 255, step: 1, int: true, rec: 255 }] },
    { id: 'ekf', tile: 0, rule: 3, title: 'GPS / EKF failure', dash: 'GPS / EKF',
      what: 'The position estimate becomes unreliable (bad GPS, compass or estimator variance above the threshold).',
      params: [
        { name: 'FS_EKF_ACTION', label: 'Action', opts: EKF_ACT, int: true, rec: 1, primary: true },
        { name: 'FS_EKF_THRESH', label: 'Variance threshold (0.6 strict · 0.8 default · 1.0 relaxed · 0 off)', min: 0, max: 1, step: 0.1, rec: 0.8 }] },
    { id: 'fence', tile: 0, rule: 4, title: 'Geofence breach', dash: 'GEOFENCE',
      what: 'The drone goes above the altitude ceiling or outside the circle around home.',
      params: [
        { name: 'FENCE_ENABLE', label: 'Fence', opts: ON_OFF, int: true, rec: 1, primary: true, gate: true },
        { name: 'FENCE_ACTION', label: 'Action on breach', opts: FENCE_ACT, int: true, rec: 1, show: true },
        { name: 'FENCE_TYPE', label: 'Fence types (bitmask: 1 alt max · 2 circle · 4 polygon)', min: 0, max: 15, step: 1, int: true, rec: 3 },
        { name: 'FENCE_ALT_MAX', label: 'Altitude ceiling', unit: 'm', min: 10, max: 120, step: 1 },
        { name: 'FENCE_RADIUS', label: 'Circle radius from home', unit: 'm', min: 30, max: 2000, step: 10 }] },
    { id: 'crash', tile: 1, rule: 10, title: 'Crash detected', dash: 'CRASH',
      what: 'The attitude is far from what was commanded for 2 s (the drone has tipped over or hit something): the motors are disarmed.',
      params: [{ name: 'FS_CRASH_CHECK', label: 'Action', opts: [[0, 'Disabled', 'OFF'], [1, 'Disarm motors', 'DISARM']], int: true, rec: 1, primary: true }] },
    { id: 'vibe', tile: 7, rule: 8, title: 'High vibration', dash: 'VIBRATION',
      what: 'Vibration high enough to corrupt the altitude estimate: the Pixhawk switches to a vibration-resistant altitude controller.',
      params: [{ name: 'FS_VIBE_ENABLE', label: 'Vibration compensation', opts: ON_OFF, int: true, rec: 1, primary: true }] },
    { id: 'prearm', tile: 1, rule: 11, title: 'Pre-arm checks', dash: 'PRE-ARM',
      what: 'Checks the Pixhawk runs before it allows arming. 1 = all checks. Never set 0.',
      params: [{ name: 'ARMING_CHECK', label: 'Checks (1 = all)', min: 0, max: 1048575, step: 1, int: true, rec: 1, primary: true, okIf: v => v !== 0, show: v => (v === 1 ? 'ALL' : v === 0 ? 'NONE' : 'CUSTOM') }] },
    { id: 'rtl', tile: 10, rule: 17, title: 'Return to home', dash: 'RETURN HOME',
      what: 'How every RTL action above flies home: climb to the return altitude, fly back, loiter, then land.',
      params: [
        { name: 'RTL_ALT', label: 'Return altitude', unit: 'm', scale: 0.01, min: 2, max: 120, step: 1, int: true, primary: true, show: v => Math.round(v / 100) + ' M' },
        { name: 'RTL_SPEED', label: 'Return speed (0 = waypoint speed)', unit: 'm/s', scale: 0.01, min: 0, max: 20, step: 0.5, int: true },
        { name: 'RTL_LOIT_TIME', label: 'Loiter over home before landing', unit: 's', scale: 0.001, min: 0, max: 60, step: 1, int: true },
        { name: 'RTL_ALT_FINAL', label: 'Final altitude (0 = land)', unit: 'm', scale: 0.01, min: 0, max: 50, step: 1, int: true, rec: 0 }] },
  ];
  const ALL_NAMES = DEFS.flatMap(d => d.params.map(p => p.name));

  // Demo drone: recommended values, with one setting off so the "check" state is visible.
  const demoValues = {};
  DEFS.forEach(d => d.params.forEach(p => { demoValues[p.name] = p.rec ?? ({ FENCE_ALT_MAX: 60, FENCE_RADIUS: 300, RTL_ALT: 3000, RTL_SPEED: 0, RTL_LOIT_TIME: 5000, BATT_LOW_MAH: 0, BATT_CRT_MAH: 0 }[p.name] ?? 0); }));
  demoValues.FS_VIBE_ENABLE = 0;
  const demoRules = { FS_THR_ENABLE: { value: 1 }, FS_GCS_ENABLE: { in: [1, 5] }, FS_EKF_ACTION: { value: 1 }, FS_CRASH_CHECK: { value: 1 }, ARMING_CHECK: { value: 1 },
    BATT_FS_LOW_ACT: { value: 2 }, BATT_FS_CRT_ACT: { value: 1 }, BATT_LOW_VOLT: { value: 21.6 }, BATT_CRT_VOLT: { value: 21.0 }, FENCE_ENABLE: { value: 1 },
    FENCE_ALT_MAX: { min: 10, max: 120 }, FENCE_ACTION: { in: [1, 4] } };

  const values = () => (isDemo ? demoValues : (M() && M().failsafeParams) || {});
  const rules = () => (isDemo ? demoRules : (M() && M().failsafeRules) || {});
  const fresh = () => isDemo || (M() && M().failsafeAt && performance.now() - M().failsafeAt < 10000);
  const near = (a, b) => a !== undefined && b !== undefined && Math.abs(a - b) <= Math.max(1e-4, Math.abs(b) * 1e-4);

  function ruleOk(rule, v) {
    if (!rule || v === undefined) return true;
    if ('value' in rule) return near(v, rule.value);
    if ('in' in rule) return rule.in.some(x => near(v, x));
    return (rule.min === undefined || v >= rule.min - 1e-6) && (rule.max === undefined || v <= rule.max + 1e-6);
  }
  const ruleText = r => !r ? '' : 'value' in r ? `= ${r.value}` : 'in' in r ? `one of ${r.in.join(', ')}` : `${r.min ?? '…'} – ${r.max ?? '…'}`;
  const shown = (p, raw) => raw === undefined ? '—' : p.scale ? +(raw * p.scale).toFixed(3) : raw;
  const optLabel = (p, raw, short) => { const o = (p.opts || []).find(o => near(raw, o[0])); return o ? (short ? o[2] : o[1]) : String(raw); };

  function paramStatus(p, v, rl) {
    if (v === undefined) return { state: 'unknown', note: 'not read' };
    if (!ruleOk(rl[p.name], v)) return { state: 'bad', note: `launch check needs ${ruleText(rl[p.name])} — the countdown would HOLD` };
    if (p.okIf && !p.okIf(v)) return { state: 'bad', note: 'unsafe' };
    if (p.rec !== undefined && !near(v, p.rec)) return { state: 'warn', note: `recommended ${p.opts ? optLabel(p, p.rec) : shown(p, p.rec) + (p.unit ? ' ' + p.unit : '')}` };
    return { state: 'ok', note: '' };
  }

  function status(def) {
    const v = values(), rl = rules(), prim = def.params.find(p => p.primary);
    if (v[prim.name] === undefined) return { state: 'unknown', text: '—', title: fresh() ? 'Not reported by the Pixhawk' : 'Waiting for the Jetson' };
    const states = def.params.map(p => paramStatus(p, v[p.name], rl));
    let text = prim.show ? prim.show(v[prim.name]) : prim.opts ? optLabel(prim, v[prim.name], true) : String(shown(prim, v[prim.name]));
    if (prim.gate && near(v[prim.name], 1)) { const act = def.params.find(p => p.show === true); if (act && v[act.name] !== undefined) text = optLabel(act, v[act.name], true); }
    if (def.id === 'battery' && v.BATT_FS_CRT_ACT !== undefined) text += '/' + optLabel(def.params[3], v.BATT_FS_CRT_ACT, true);
    const off = prim.opts && near(v[prim.name], prim.opts[0][0]) && prim.opts[0][2] === 'OFF';
    const bad = states.some(s => s.state === 'bad'), warn = states.some(s => s.state === 'warn');
    const notes = def.params.map((p, i) => states[i].note && states[i].state !== 'unknown' ? `${p.name}: ${states[i].note}` : '').filter(Boolean);
    return { state: off || bad ? 'bad' : warn ? 'warn' : 'ok', text: off ? 'OFF' : text, title: notes.join('\n') || 'Matches the recommended setting' };
  }
  const issues = () => DEFS.filter(d => ['bad', 'warn'].includes(status(d).state)).length;

  // ── styles ────────────────────────────────────────────────────────────────
  const css = `
  .fso-chip{height:26px;padding:0 10px;font:600 11px 'Share Tech Mono',monospace;letter-spacing:1px;border:1px solid #2a3e4e;background:#0b1620;color:#8fa3b5;cursor:pointer;white-space:nowrap}
  .fso-chip.ok{border-color:rgba(0,227,131,.45);color:#00e383;background:rgba(0,227,131,.07)} .fso-chip.warn{border-color:rgba(255,184,0,.5);color:#ffb800;background:rgba(255,184,0,.07)}
  .fso-chip.bad{border-color:rgba(255,51,85,.6);color:#ff5c7a;background:rgba(255,51,85,.09)}
  .failsafe-page .fso-chip{position:absolute;right:12px;top:84px;min-width:76px;height:auto;padding:6px 9px;font-size:11px}
  .fso-tag{margin-left:auto;margin-right:10px;font:700 10px 'Share Tech Mono',monospace;letter-spacing:.5px;padding:2px 6px;border:1px solid #2a3e4e;color:#8fa3b5;white-space:nowrap}
  .fso-tag.ok{color:#00e383;border-color:rgba(0,227,131,.4)} .fso-tag.warn{color:#ffb800;border-color:rgba(255,184,0,.45)} .fso-tag.bad{color:#ff5c7a;border-color:rgba(255,51,85,.55)}
  .fs-tile-buttons button.fso-btn{display:flex;align-items:center}
  .fso-back{position:fixed;inset:0;z-index:10050;background:rgba(0,4,8,.72);display:flex;align-items:center;justify-content:center}
  .fso-dlg{width:min(760px,94vw);max-height:90vh;overflow:auto;background:#060d14;border:1px solid rgba(0,180,255,.25);box-shadow:0 20px 60px rgba(0,0,0,.8);color:#e8f4ff;font:13px Rajdhani,sans-serif}
  .fso-dlg header{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid rgba(0,180,255,.15)}
  .fso-dlg header h3{margin:0;font:700 17px Rajdhani,sans-serif;letter-spacing:2px;text-transform:uppercase}
  .fso-dlg header small{font:10px 'Share Tech Mono',monospace;color:#5d7a92;letter-spacing:1.5px} .fso-dlg header .x{margin-left:auto;background:none;border:0;color:#8fa3b5;font-size:20px;cursor:pointer}
  .fso-what{padding:10px 18px;color:#9fb3c4;font-size:13px;line-height:1.35;border-bottom:1px solid rgba(0,180,255,.08)}
  .fso-tbl{width:100%;border-collapse:collapse;font:12px 'Share Tech Mono',monospace}
  .fso-tbl th{text-align:left;font-weight:400;font-size:9px;letter-spacing:1.5px;color:#5d7a92;padding:8px 10px;border-bottom:1px solid rgba(0,180,255,.12)}
  .fso-tbl td{padding:7px 10px;border-bottom:1px solid rgba(0,180,255,.06);vertical-align:middle}
  .fso-tbl td.lbl{font:13px Rajdhani,sans-serif;color:#e8f4ff} .fso-tbl td.lbl small{display:block;color:#5d7a92;font:9px 'Share Tech Mono',monospace;letter-spacing:.5px}
  .fso-tbl select,.fso-tbl input{width:100%;background:#0b1620;color:#e8f4ff;border:1px solid #2a3e4e;padding:4px 6px;font:12px 'Share Tech Mono',monospace}
  .fso-tbl tr.changed select,.fso-tbl tr.changed input{border-color:#ffb800;color:#ffb800}
  .fso-st{font-size:10px;letter-spacing:.5px} .fso-st.ok{color:#00e383} .fso-st.warn{color:#ffb800} .fso-st.bad{color:#ff5c7a} .fso-st.unknown{color:#5d7a92}
  .fso-foot{display:flex;gap:10px;align-items:center;padding:12px 18px;flex-wrap:wrap}
  .fso-foot button{height:32px;padding:0 14px;font:600 11px 'Share Tech Mono',monospace;letter-spacing:1.5px;background:#0b1620;color:#9be5ff;border:1px solid rgba(127,220,255,.3);cursor:pointer}
  .fso-foot button.go{background:rgba(0,227,131,.1);color:#00e383;border-color:rgba(0,227,131,.5)} .fso-foot button:disabled{opacity:.4;cursor:not-allowed}
  .fso-foot .msg{font:11px 'Share Tech Mono',monospace;color:#9fb3c4;flex:1;min-width:200px}
  .fso-foot .msg.err{color:#ff5c7a} .fso-foot .msg.ok{color:#00e383}
  .fso-row{cursor:pointer} .fso-pill{justify-self:end;font:700 12px 'Share Tech Mono',monospace;letter-spacing:1px;padding:4px 8px;border:1px solid #2a3e4e;color:#8fa3b5;text-align:center;min-width:92px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .fso-pill.ok{color:#00e383;border-color:rgba(0,227,131,.4);background:rgba(0,227,131,.06)} .fso-pill.warn{color:#ffb800;border-color:rgba(255,184,0,.45);background:rgba(255,184,0,.06)}
  .fso-pill.bad{color:#ff5c7a;border-color:rgba(255,51,85,.55);background:rgba(255,51,85,.08)}`;
  function addStyles() { if (document.getElementById('fsoStyles')) return; const s = document.createElement('style'); s.id = 'fsoStyles'; s.textContent = css; document.head.append(s); }

  // ── editor ────────────────────────────────────────────────────────────────
  let open = null;
  function edit(id) {
    const def = DEFS.find(d => d.id === id); if (!def) return false;
    addStyles(); close();
    const canWrite = isDemo || (M() && M().canCommand && mode !== 'viewer');
    const back = document.createElement('div'); back.className = 'fso-back';
    back.innerHTML = `<div class="fso-dlg" role="dialog" aria-label="${esc(def.title)}">
      <header><h3>${esc(def.title)}</h3><small>ON THE DRONE · PIXHAWK</small><button class="x" aria-label="Close">×</button></header>
      <div class="fso-what">${esc(def.what)}</div>
      <table class="fso-tbl"><thead><tr><th>SETTING</th><th>ON DRONE</th><th style="width:34%">SET TO</th><th>STATUS</th></tr></thead><tbody></tbody></table>
      <div class="fso-foot"><button class="rec" type="button">USE RECOMMENDED</button><button class="go" type="button">WRITE TO DRONE</button><span class="msg"></span></div></div>`;
    document.body.append(back);
    const tbody = back.querySelector('tbody'), msg = back.querySelector('.msg'), goBtn = back.querySelector('.go'), recBtn = back.querySelector('.rec');
    const draft = {};
    function row(p) {
      const v = values()[p.name], st = paramStatus(p, v, rules());
      const cur = draft[p.name] ?? v;
      const input = p.opts
        ? `<select data-p="${p.name}" ${canWrite ? '' : 'disabled'}>${(v === undefined ? '<option value="">—</option>' : '') + p.opts.map(o => `<option value="${o[0]}" ${near(cur, o[0]) ? 'selected' : ''}>${esc(o[1])}</option>`).join('')}</select>`
        : `<input data-p="${p.name}" type="number" min="${p.min}" max="${p.max}" step="${p.step || 'any'}" value="${cur === undefined ? '' : shown(p, cur)}" ${canWrite && v !== undefined ? '' : 'disabled'}>`;
      const onDrone = v === undefined ? '<span class="fso-st unknown">NOT REPORTED</span>' : esc(p.opts ? optLabel(p, v) : shown(p, v) + (p.unit ? ' ' + p.unit : ''));
      const stText = { ok: '✓ OK', warn: '⚠ ' + st.note, bad: '✗ ' + st.note, unknown: '—' }[st.state];
      return `<tr class="${p.name in draft && !near(draft[p.name], v) ? 'changed' : ''}"><td class="lbl">${esc(p.label)}<small>${p.name}${rules()[p.name] ? ' · launch check ' + esc(ruleText(rules()[p.name])) : ''}</small></td>
        <td>${onDrone}</td><td>${input}</td><td class="fso-st ${st.state}">${esc(stText)}</td></tr>`;
    }
    function render() {
      tbody.innerHTML = def.params.map(row).join('');
      const changes = pending();
      goBtn.disabled = !canWrite || !Object.keys(changes).length;
      goBtn.textContent = Object.keys(changes).length ? `WRITE ${Object.keys(changes).length} CHANGE(S) TO DRONE` : 'WRITE TO DRONE';
      recBtn.disabled = !canWrite;
      if (!canWrite && !msg.textContent) msg.textContent = 'Viewer mode — read only.';
    }
    function pending() {
      const out = {}, v = values();
      Object.entries(draft).forEach(([n, raw]) => { if (v[n] !== undefined && !near(raw, v[n])) out[n] = raw; });
      return out;
    }
    tbody.addEventListener('change', e => {
      const el = e.target.closest('[data-p]'); if (!el || el.value === '') return;
      const p = def.params.find(x => x.name === el.dataset.p);
      let raw = Number(el.value);
      if (!p.opts) {
        if (!Number.isFinite(raw) || raw < p.min || raw > p.max) { msg.textContent = `${p.label}: enter ${p.min}–${p.max}${p.unit ? ' ' + p.unit : ''}`; msg.className = 'msg err'; render(); return; }
        if (p.scale) raw = raw / p.scale;
      }
      if (p.int) raw = Math.round(raw);
      draft[p.name] = raw; msg.textContent = ''; msg.className = 'msg'; render();
    });
    recBtn.onclick = () => { def.params.forEach(p => { if (p.rec !== undefined) draft[p.name] = p.rec; }); render(); };
    goBtn.onclick = async () => {
      const changes = pending(); const names = Object.keys(changes); if (!names.length) return;
      const list = names.map(n => { const p = def.params.find(x => x.name === n); const fmt = r => p.opts ? optLabel(p, r) : shown(p, r) + (p.unit ? ' ' + p.unit : ''); return `${p.label}: ${fmt(values()[n])} → ${fmt(changes[n])}`; }).join('\n');
      if (!confirm(`Write to the Pixhawk?\n\n${list}\n\nEach value is confirmed by reading it back. Blocked while armed.`)) return;
      goBtn.disabled = true; msg.className = 'msg'; msg.textContent = 'Writing…';
      let r;
      if (isDemo) { Object.assign(demoValues, changes); r = { ok: true, msg: `Wrote and confirmed ${names.length} parameter(s) (demo)`, values: changes }; }
      else r = await M().send('param_write', { params: changes }, { timeout: 30000 });
      if (r.values && M() && !isDemo) M().failsafeParams = Object.assign({}, M().failsafeParams || {}, r.values);
      Object.keys(r.values || {}).forEach(n => delete draft[n]);
      msg.className = 'msg ' + (r.ok ? 'ok' : 'err'); msg.textContent = (r.ok ? '✓ ' : '✗ ') + r.msg;
      render(); notify();
    };
    const closeIt = () => { back.remove(); open = null; };
    back.querySelector('.x').onclick = closeIt;
    back.addEventListener('click', e => { if (e.target === back) closeIt(); });
    back.addEventListener('keydown', e => { if (e.key === 'Escape') closeIt(); });
    open = { render, close: closeIt };
    render();
    return true;
  }
  function close() { if (open) open.close(); }

  // ── failsafe page: chips on the tiles, response buttons open the editor ────
  const BUTTON_LABELS = { 0: 'RC-loss response', 1: '5G-loss response', 2: 'Battery response', 3: 'GPS / EKF response', 4: 'Fence response',
                          8: 'Vibration response', 10: 'Crash response', 11: 'Pre-arm checks', 17: 'Return-home settings' };
  function wireFailsafePage() {
    const tiles = [...document.querySelectorAll('.fs-square-tile')]; if (!tiles.length) return false;
    addStyles();
    tiles.forEach(tile => tile.querySelectorAll('.fs-tile-buttons button').forEach(b => {
      const def = DEFS.find(d => String(d.tile) === tile.dataset.system && BUTTON_LABELS[d.rule] === b.textContent.trim()); if (!def) return;
      if (def.id === 'gcs') b.textContent = 'Jetson-loss response';
      const tag = document.createElement('span'); tag.className = 'fso-tag'; tag.dataset.fsoDef = def.id; tag.textContent = '—';
      b.classList.add('fso-btn'); b.append(tag);
    }));
    tiles.forEach(tile => {
      const defs = DEFS.filter(d => String(d.tile) === tile.dataset.system); if (!defs.length) return;
      const act = tile.querySelector('.fs-card-activation');
      const chip = document.createElement('button'); chip.type = 'button'; chip.className = 'fso-chip'; chip.dataset.fsoTile = tile.dataset.system;
      chip.addEventListener('click', e => { e.stopPropagation(); edit(defs[0].id); });
      if (act) { act.replaceWith(chip); } else tile.append(chip);
    });
    return true;
  }
  function renderFailsafePage() {
    document.querySelectorAll('[data-fso-tile]').forEach(chip => {
      const defs = DEFS.filter(d => String(d.tile) === chip.dataset.fsoTile);
      const st = defs.map(status), worst = st.find(s => s.state === 'bad') || st.find(s => s.state === 'warn') || st.find(s => s.state === 'unknown') || st[0];
      chip.className = 'fso-chip ' + worst.state;
      chip.textContent = { ok: '✓ ON DRONE', warn: '⚠ CHECK', bad: '✗ FIX', unknown: '— NOT READ' }[worst.state];
      chip.title = defs.map((d, i) => `${d.title}: ${st[i].text}\n${st[i].title}`).join('\n\n') + '\n\nClick to view or change on the drone';
    });
    document.querySelectorAll('[data-fso-def]').forEach(tag => {
      const st = status(DEFS.find(d => d.id === tag.dataset.fsoDef));
      tag.className = 'fso-tag ' + st.state; tag.textContent = st.text + (st.state === 'ok' ? ' ✓' : st.state === 'unknown' ? '' : ' !'); tag.title = st.title;
    });
    const lbl = [...document.querySelectorAll('.bar-lbl')].find(x => x.textContent.trim() === 'Config');
    if (lbl && lbl.nextElementSibling) {
      const val = lbl.nextElementSibling, known = DEFS.some(d => status(d).state !== 'unknown'), n = issues();
      val.textContent = !known ? 'NOT READ' : n ? `${n} TO CHECK` : 'ON DRONE ✓';
      val.style.color = !known ? '#8a9bb0' : n ? '#ffb800' : '#00e383';
    }
  }

  // ── dashboard: scrollable list of every onboard failsafe ─────────────────
  function wireDashboard() {
    const list = document.querySelector('.failsafe-panel .failsafe-list'); if (!list) return false;
    addStyles();
    list.classList.add('fso-list');
    list.innerHTML = DEFS.map(d => `<div class="failsafe-row fso-row" data-fso="${d.id}" role="button" tabindex="0" title="${esc(d.title)} — click to view or change"><span>${esc(d.dash)}</span><b class="fso-pill">—</b></div>`).join('');
    list.addEventListener('click', e => { const r = e.target.closest('[data-fso]'); if (r) edit(r.dataset.fso); });
    list.addEventListener('keydown', e => { const r = e.target.closest('[data-fso]'); if (r && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); edit(r.dataset.fso); } });
    return true;
  }
  function renderDashboard() {
    document.querySelectorAll('.fso-list [data-fso]').forEach(row => {
      const st = status(DEFS.find(d => d.id === row.dataset.fso)), pill = row.querySelector('.fso-pill');
      pill.textContent = st.text + (st.state === 'ok' ? ' ✓' : st.state === 'unknown' ? '' : ' !');
      pill.className = 'fso-pill ' + st.state; pill.title = st.title;
    });
  }

  let page = { fs: false, dash: false }, lastKey = '';
  function notify() { lastKey = ''; tick(); }
  function tick() {
    const key = JSON.stringify([values(), rules(), fresh()]);
    if (key === lastKey) return;
    lastKey = key;
    if (page.fs) renderFailsafePage();
    if (page.dash) renderDashboard();
    if (open) open.render();
  }
  function init() {
    page.fs = wireFailsafePage();
    page.dash = wireDashboard();
    if (M()) M().on(tick);
    setInterval(tick, 1000);
    tick();
  }

  window.AirsentFailsafes = { DEFS, ALL_NAMES, status, edit, values, rules, openRule: rule => { const d = DEFS.find(x => x.rule === rule); return d ? edit(d.id) : false; } };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
