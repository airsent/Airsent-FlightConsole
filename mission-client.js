/* mission-client.js — one shared connection to the Jetson launch sequencer.
   The Jetson owns the countdown, stages, checks and faults. This file only
   receives its snapshot (inside telemetry) and sends requests. Demo mode runs a
   local imitation so the console can be explored without an aircraft. */
(() => {
  'use strict';
  if (window.airsentMission) return;
  const apiUrl = (path, ws) => {
    if (window.airsentApiUrl) return window.airsentApiUrl(path, ws);
    const url = new URL(path, location.origin);
    if (ws) url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.href;
  };

  const mode = (() => {
    if (window.airsentSimulation) return 'simulation';
    const m = (localStorage.getItem('airsentMode') || 'demo').toLowerCase();
    if (['operator', 'actual', 'real'].includes(m)) return 'operator';
    return m === 'viewer' ? 'viewer' : 'demo';
  })();
  const canCommand = mode === 'operator' || mode === 'simulation' || mode === 'demo';
  const listeners = new Set();
  const pending = new Map();
  let consoleId = sessionStorage.getItem('airsentConsoleId');
  if (!consoleId) {
    consoleId = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)).slice(0, 36);
    sessionStorage.setItem('airsentConsoleId', consoleId);
  }
  const api = {
    mode, canCommand, consoleId,
    state: null, faults: null, receivedAt: 0, telemetryAt: 0,
    get operatorName() { return localStorage.getItem('airsentOperatorName') || 'Operator'; },
    set operatorName(v) { localStorage.setItem('airsentOperatorName', String(v || '').slice(0, 40)); },
    on(fn) { listeners.add(fn); if (api.state) fn(api); return () => listeners.delete(fn); },
    linkOk() { return api.telemetryAt > 0 && performance.now() - api.telemetryAt < 3500; },
    stateFresh() { return api.receivedAt > 0 && performance.now() - api.receivedAt < 6000; },
    isPIC() { return !!api.state && api.state.pic && api.state.pic.console === consoleId; },
    active() { return !!api.state && api.state.stage !== 'standby'; },
    remaining() {
      const s = api.state;
      if (!s) return 0;
      const counting = ['countdown', 'prearm', 'gonogo', 'armed'].includes(s.stage) && !s.held;
      const elapsed = (performance.now() - api.receivedAt) / 1000;
      return Math.max(0, counting ? s.remaining - elapsed : s.remaining);
    },
    tPlus() {
      const s = api.state;
      if (!s || s.t_plus == null) return null;
      const live = ['takeoff', 'flight'].includes(s.stage);
      return s.t_plus + (live ? (performance.now() - api.receivedAt) / 1000 : 0);
    },
    clockText() {
      const s = api.state;
      if (!s) return '--:--';
      const t = api.tPlus();
      if (t != null && ['takeoff', 'flight', 'landed', 'safed'].includes(s.stage)) return 'T+' + mmss(t);
      return 'T-' + mmss(s.stage === 'standby' ? s.total : Math.ceil(api.remaining()));
    },
    send,
  };
  window.airsentMission = api;

  function mmss(sec) {
    sec = Math.max(0, Math.floor(sec));
    return String(Math.floor(sec / 60)).padStart(2, '0') + ':' + String(sec % 60).padStart(2, '0');
  }
  api.mmss = mmss;

  function emit() { listeners.forEach(fn => { try { fn(api); } catch (e) { console.error(e); } }); }

  function accept(data) {
    api.telemetryAt = performance.now();
    if (data && data.mission) { api.state = data.mission; api.receivedAt = performance.now(); }
    if (data && data.faults) api.faults = data.faults;
    if (data && data.vehicle_mission) api.vehicleMission = data.vehicle_mission;
    if (data && data.failsafe_params) { api.failsafeParams = data.failsafe_params; api.failsafeAt = performance.now(); }
    if (data && data.failsafe_rules) api.failsafeRules = data.failsafe_rules;
    if (data && data.asc) { api.asc = data.asc; if (data.asc.defs) api.ascDefs = data.asc.defs; }
    if (data && data.param_rev) api.paramRev = data.param_rev;
    if (data && data.bin_log) api.binLog = data.bin_log;
    if (data && data.video) api.video = data.video;
    if (data && data.netlink) api.netlink = data.netlink;
    if (data && (data.mission || data.faults)) emit();
  }
  api.accept = accept;

  // ── requests ─────────────────────────────────────────────────────────────
  let cmd = null, rid = 0;
  function send(action, data = {}, opts = {}) {
    if (!canCommand) return Promise.resolve({ ok: false, msg: 'Viewer mode is read only' });
    if (mode === 'demo') return Promise.resolve(demo.handle({ action, console_id: consoleId, operator: api.operatorName, ...data }));
    return new Promise(resolve => {
      if (!cmd || cmd.readyState !== WebSocket.OPEN) { resolve({ ok: false, msg: 'No command link to the Jetson' }); return; }
      const request_id = consoleId.slice(0, 8) + '-' + (++rid);
      const timer = setTimeout(() => { pending.delete(request_id); resolve({ ok: false, msg: 'No reply from the Jetson (request may not have arrived)' }); }, opts.timeout || 6000);
      pending.set(request_id, reply => { clearTimeout(timer); resolve(reply); });
      cmd.send(JSON.stringify({ action, console_id: consoleId, operator: api.operatorName, request_id, page: location.pathname.split('/').pop(), ...data }));
    });
  }

  function connectCommands() {
    if (!canCommand || mode === 'demo') return;
    try { cmd = new WebSocket(apiUrl('/cmd', true)); } catch (e) { setTimeout(connectCommands, 3000); return; }
    cmd.onmessage = ev => {
      let r; try { r = JSON.parse(ev.data); } catch (e) { return; }
      const cb = r && r.request_id && pending.get(r.request_id);
      if (cb) { pending.delete(r.request_id); cb(r); }
    };
    cmd.onclose = () => { cmd = null; setTimeout(connectCommands, 3000); };
    cmd.onerror = () => {};
  }

  function ping() {
    if (mode === 'demo') { demo.handle({ action: 'mission_ping', console_id: consoleId, operator: api.operatorName }); return; }
    if (canCommand && cmd && cmd.readyState === WebSocket.OPEN)
      cmd.send(JSON.stringify({ action: 'mission_ping', console_id: consoleId, operator: api.operatorName, page: location.pathname.split('/').pop() }));
  }

  // ── video on demand: tell the Jetson which cameras are on screen ─────────
  // On cellular the Jetson only streams a camera while some console shows it
  // (elements marked data-cam-watch="front|down" that are visible right now).
  function watchedCams() {
    if (document.hidden) return [];
    const cams = new Set();
    document.querySelectorAll('[data-cam-watch]').forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width > 4 && r.height > 4 && getComputedStyle(el).visibility !== 'hidden')
        el.dataset.camWatch.split(/\s+/).filter(Boolean).forEach(c => cams.add(c));
    });
    return [...cams].sort();
  }
  api.watchedCams = watchedCams;
  let lastWatch = '', lastWatchAt = 0;
  setInterval(() => {
    if (mode === 'demo' || !canCommand || !cmd || cmd.readyState !== WebSocket.OPEN) return;
    const cams = watchedCams(), key = cams.join(',');
    const now = performance.now();
    if (!cams.length || (key === lastWatch && now - lastWatchAt < 4000)) { if (!cams.length) lastWatch = ''; return; }
    lastWatch = key; lastWatchAt = now;                       // new camera on screen: tell the Jetson at once
    cmd.send(JSON.stringify({ action: 'cam_watch', cams, console_id: consoleId, operator: api.operatorName }));
  }, 1000);

  function connectTelemetry() {
    let ws;
    try { ws = new WebSocket(apiUrl('/telem', true)); } catch (e) { setTimeout(connectTelemetry, 3000); return; }
    ws.onmessage = ev => {
      // Only the mission/fault keys are needed here; skip full parsing when absent.
      if (typeof ev.data !== 'string') return;
      if (ev.data.indexOf('"mission"') < 0 && ev.data.indexOf('"faults"') < 0) { api.telemetryAt = performance.now(); return; }
      try { accept(JSON.parse(ev.data)); } catch (e) {}
    };
    ws.onclose = () => setTimeout(connectTelemetry, 3000);
    ws.onerror = () => {};
  }

  // ── demo imitation (no aircraft, no network) ─────────────────────────────
  const demo = (() => {
    const order = ['standby', 'countdown', 'prearm', 'gonogo', 'armed', 'takeoff', 'flight', 'landed', 'safed', 'aborted'];
    const pre = ['countdown', 'prearm', 'gonogo', 'armed'];
    const gateStage = { create: 'countdown', countdown: 'countdown', prearm: 'prearm', gonogo: 'gonogo', armed: 'armed', takeoff: 'takeoff' };
    let st = { stage: 'standby', total: 180, remaining: 180, deadline: null, held: false, hold_reason: '', hold_since: null,
      mission: null, go: null, manual: {}, rc: 'SAFE', takeoff_at: null, alt: 0, armed: false, abort: null, pic: null, events: [] };
    const now = () => Date.now() / 1000;
    const faults = new Map([['demo:esc', { id: 'D1', key: 'demo:esc', severity: 'warning', source: 'ESC', message: 'ESC telemetry (Datalink) not connected — demo', since: now() - 90, acked_by: null }]]);
    const ev = (kind, msg, who = 'Jetson') => st.events.unshift({ t: now(), kind, msg, who, stage: st.stage, clock: '' });
    const remaining = () => pre.includes(st.stage) && !st.held && st.deadline ? Math.max(0, st.deadline - now()) : st.remaining;
    const stageFor = r => r <= 0 ? 'takeoff' : r <= 10 ? 'armed' : r <= 60 ? 'gonogo' : r <= Math.min(180, st.total) ? 'prearm' : 'countdown';
    const manualLabels = { manual_observers: 'Visual observers in position' };
    function checks() {
      const c = (id, group, label, gate, ok, value, need, warn) => ({ id, group, label, gate, raw: ok ? 'pass' : warn ? 'warn' : 'fail', value, need, note: '' });
      const list = [
        c('pixhawk', 'Link', 'Flight controller connected', 'create', true, 'CONNECTED', 'heartbeat < 3 s'),
        c('disk', 'Jetson', 'Mission log storage free', 'create', true, '182.4 GB', '>= 5 GB'),
        c('vps_link', 'Link', 'Jetson to console relay', 'countdown', true, 'CONNECTED', 'connected'),
        c('pic_console', 'Link', 'Pilot-in-command console connected', 'countdown', true, '1s ago', 'seen < 6 s'),
        c('network', 'Link', 'Network link quality', 'prearm', true, 'CELLULAR 78%', 'connected, >= 30%'),
        st.stage === 'standby' || st.stage === 'countdown' || st.stage === 'prearm'
          ? c('rc_gate_safe', 'RC', 'RC arm switch in SAFE until GO/NO-GO', 'create', st.rc === 'SAFE', st.rc, 'SAFE')
          : c('rc_gate_arm', 'RC', 'RC arm switch in ARM (pilot sees area clear)', 'armed', st.rc === 'ARM', st.rc, 'ARM by T-10'),
        c('rc_link', 'RC', 'RC receiver signal present', 'prearm', true, 'PRESENT', 'present'),
        c('golden_params', 'Config', 'Pixhawk parameters match the approved set', 'prearm', true, 'MATCH', '21 params'),
        c('ardupilot_prearm', 'Config', 'ArduPilot pre-arm checks', 'prearm', true, 'PASS', 'all pass'),
        c('gps_fix', 'GPS', 'GPS fix (3D or better)', 'prearm', true, 'RTK FIX', '3D FIX or better'),
        c('gps_sats', 'GPS', 'Satellites', 'prearm', true, '21', '>= 10'),
        c('gps_hdop', 'GPS', 'Horizontal dilution (HDOP)', 'prearm', true, '0.62', '<= 1.2'),
        c('home', 'GPS', 'Home position set', 'prearm', true, 'SET', 'set'),
        c('ekf', 'Estimator', 'EKF healthy', 'prearm', true, 'HEALTHY', 'healthy'),
        c('battery_volt', 'Battery', 'Battery voltage', 'prearm', true, '25.0 V', '>= 22.8 V'),
        c('battery_pct', 'Battery', 'Battery remaining (estimate)', 'prearm', true, '97%', '>= 60%', true),
        c('imu', 'Sensors', 'IMU', 'prearm', true, 'OK', 'OK'),
        c('baro', 'Sensors', 'Barometer', 'prearm', true, 'OK', 'OK'),
        c('mag', 'Sensors', 'Compass', 'prearm', true, 'OK', 'OK'),
        c('lidar', 'Sensors', 'LiDAR rangefinder (H-Flow)', 'prearm', true, (0.3 + st.alt).toFixed(2) + ' m', 'reporting'),
        c('flow', 'Sensors', 'Optical flow (H-Flow)', 'prearm', true, 'Q 188', 'live'),
        c('vibration', 'Sensors', 'Vibration', 'prearm', true, '6.1 m/s²', '< 30'),
        c('fence', 'Safety', 'Geofence and max altitude active on Pixhawk', 'prearm', true, 'ENABLED', 'enabled'),
        c('front_cam', 'Jetson', 'Front camera', 'prearm', true, 'CONNECTED', 'connected', true),
        c('down_cam', 'Jetson', 'Down camera', 'prearm', true, 'CONNECTED', 'connected', true),
        c('jetson_temp', 'Jetson', 'Jetson temperature', 'prearm', true, '54 °C', '< 80 °C'),
      ];
      Object.entries(manualLabels).forEach(([k, label]) => list.push(c(k, 'Manual', label, 'gonogo', !!st.manual[k],
        st.manual[k] ? '✓ ' + st.manual[k].by : 'NOT CONFIRMED', 'confirm by T-60')));
      list.push(c('go_poll', 'GO/NO-GO', 'Pilot in command GO (area and props clear)', 'armed', !!st.go, st.go ? 'GO · ' + st.go.by : 'WAITING', 'GO by T-10'));
      const spun = (st.stage === 'armed' && st.armed_at && now() - st.armed_at > 3) || ['takeoff', 'flight', 'landed', 'safed'].includes(st.stage);
      list.push(c('motor_health', 'Motors', 'Motor health at idle (ESC telemetry)', 'takeoff', spun, spun ? '1488 / 1502 / 1495 / 1510 rpm' : 'WAITING FOR SPIN-UP', 'all spinning, spread <= 25%'));
      return list;
    }
    function blocking(stage) {
      const lvl = order.indexOf(stage);
      return checks().filter(c => c.raw === 'fail' && order.indexOf(gateStage[c.gate]) <= lvl);
    }
    function hold(reason, who = 'Jetson') {
      if (st.held || !pre.includes(st.stage)) return;
      st.remaining = remaining(); st.deadline = null; st.held = true; st.hold_reason = reason; st.hold_since = now();
      if (st.armed) { st.armed = false; ev('disarm', 'Disarmed because the countdown is on hold'); }
      ev('hold', reason, who);
    }
    function tick() {
      const t = now();
      if (pre.includes(st.stage) && !st.held) {
        const target = stageFor(remaining());
        for (const s of order.slice(order.indexOf(st.stage), order.indexOf(target) + 1)) {
          if (s !== st.stage) {
            if (s === 'takeoff' && blocking('takeoff').length) { const b = blocking('takeoff')[0]; hold(b.label + ': ' + b.value); return; }
            st.stage = s;
            if (s === 'prearm') ev('handover', 'VEHICLE HANDOVER — full pre-arm check');
            if (s === 'gonogo') ev('gonogo', 'GO/NO-GO — pilot: confirm area clear, RC pilot: switch to ARM');
            if (s === 'armed' && !blocking('armed').length) { st.armed = true; st.armed_at = t; ev('arm', 'ARM — motors to idle (T-00:10)'); }
            if (s === 'takeoff') { st.takeoff_at = t; st.remaining = 0; st.home = { lat: 33.4242, lon: -111.9281, heading: 0, at: t }; ev('home', 'HOME POINT SET — 33.424200, -111.928100'); ev('takeoff', `T-0 — TAKEOFF to ${st.profile === 'flight1' ? 2 : 5} m`); return; }
          }
          const b = blocking(s);
          if (b.length) { hold(b[0].label + ': ' + b[0].value); return; }
        }
      } else if (st.stage === 'takeoff') {
        const top = st.profile === 'flight1' ? 2 : 5;
        st.alt = Math.min(top, (t - st.takeoff_at) * 1.2);
        if (st.alt >= 0.9 * top) { st.stage = 'flight'; st.flight_at = t; ev('flight', `Climb complete — hovering at ${st.alt.toFixed(1)} m`); }
      } else if (st.stage === 'flight' && st.profile === 'flight1' && !st.abort) {
        // demo FLIGHT 1: 20 s check at 2 m → climb → 30 s at 5 m → land
        const e = t - st.flight_at, plan = { low_alt: 2, low_hold: 20, high_alt: 5, high_hold: 30 };
        const chk = { ok: true, fails: [], summary: { alt_err_max: 0.12, tilt_max: 2.1, vib_max: 6.4, drift_max: 0.4 } };
        if (e < 20) { st.alt = 2; st.step = { name: 'hold_low', remaining: 20 - e, plan }; }
        else if (e < 23) { if (st.step.name === 'hold_low') ev('profile', 'FLIGHT 1 CHECK PASSED at 2 m — climbing to 5 m'); st.alt = Math.min(5, 2 + (e - 20)); st.step = { name: 'climb', remaining: null, plan, checks: chk }; }
        else if (e < 53) { st.alt = 5; st.step = { name: 'hold_high', remaining: 53 - e, plan, checks: chk }; }
        else { if (st.step.name !== 'landing') ev('profile', 'FLIGHT 1 — 30 s hover at 5 m complete — landing'); st.alt = Math.max(0, 5 - (e - 53) * 0.8); st.step = { name: 'landing', remaining: null, plan, checks: chk };
          if (st.alt <= 0) { st.step = { name: 'done', plan, checks: chk }; st.armed = false; st.stage = 'landed'; ev('landed', 'Aircraft disarmed — landed'); } }
      } else if (st.stage === 'flight' && st.profile === 'flight2' && !st.abort) {
        // demo FLIGHT 2: 15 s per step at 5/10/15/20 m (2 s climbs), 12 s turn, 4 x 4 s box, 4 s home, landing
        const e = t - st.flight_at, labels = ['5 m', '10 m', '15 m', '20 m', '360°', 'BOX', 'HOME', 'LAND'];
        const plan = { alts: [5, 10, 15, 20], hold: 15, box: 5 };
        const sum = { alt_err_max: 0.14, tilt_max: 2.4, vib_max: 6.8, drift_max: 0.5 };
        const results = [], mk = (a) => ({ alt: a, ok: true, fails: [], summary: sum });
        let step, pos = 0;
        if (e < 15 + 3 * 17) {                                   // staircase: 15 s hold + 2 s climb per step
          const i = Math.min(3, Math.floor(e / 17)), r = e - i * 17;
          for (let k = 0; k < i; k++) results.push(mk(plan.alts[k]));
          pos = i; st.alt = r < 15 ? plan.alts[i] : plan.alts[i] + (r - 15) * 2.5;
          step = { name: r < 15 ? 'hold' : 'climb', i, remaining: r < 15 ? 15 - r : null };
          if (r >= 15 && i < 3) st.alt = Math.min(plan.alts[i + 1], plan.alts[i] + (r - 15) * 2.5);
        } else if (e < 15 + 3 * 17 + 12) {
          pos = 4; st.alt = 20; results.push(...plan.alts.map(mk));
          step = { name: 'yaw', yaw_done: Math.round(((e - 66) / 12) * 360) };
        } else if (e < 78 + 16) {
          pos = 5; st.alt = 20; results.push(...plan.alts.map(mk));
          step = { name: 'leg', leg: Math.min(3, Math.floor((e - 78) / 4)) };
        } else if (e < 94 + 4) {
          pos = 6; st.alt = 20; results.push(...plan.alts.map(mk)); step = { name: 'home' };
        } else {
          pos = 7; results.push(...plan.alts.map(mk)); st.alt = Math.max(0, 20 - (e - 98) * 1.5);
          step = st.alt <= 0 ? { name: 'done' } : { name: 'landing' };
          if (st.alt <= 0) { st.armed = false; st.stage = 'landed'; ev('landed', 'Aircraft disarmed — landed'); }
        }
        if (!st.f2 || st.f2.name !== step.name) {
          const msg = { yaw: 'FLIGHT 2 CHECK PASSED at 20 m — turning 360°', leg: 'FLIGHT 2: 360° turn complete — flying the box',
                        home: 'FLIGHT 2: box complete — returning to the home point at 20 m', landing: 'FLIGHT 2 — over the home point — descending and landing' }[step.name];
          if (msg) ev('profile', msg);
        }
        st.f2 = step;
        const d = 60, dLat = d / 111320, dLon = d / (111320 * Math.cos(33.4242 * Math.PI / 180)), h = st.home;
        const path = [[h.lat, h.lon], [h.lat + dLat, h.lon], [h.lat + dLat, h.lon - dLon], [h.lat, h.lon - dLon], [h.lat, h.lon]];
        const sides = pos >= 6 ? 4 : pos === 5 ? step.leg : 0, k = pos === 5 ? step.leg : null;
        let at = path[0];
        if (k != null) { const f = ((e - 78) % 4) / 4, a = path[k], b = path[k + 1]; at = [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]; }
        else if (pos >= 6) at = path[4];
        st.demoPlan = { path, sides_done: sides, leg: k }; st.demoPos = at;
        st.step = { ...step, pos, labels, plan, results, at_pos: null, failed: false };
      } else if (st.stage === 'flight' && st.abort) {
        st.alt = Math.max(0, st.alt - 0.15);
        if (st.alt <= 0) { st.armed = false; st.stage = 'landed'; ev('landed', 'Aircraft disarmed — landed'); }
      } else if (st.stage === 'landed' && st.rc === 'SAFE') {
        st.stage = 'safed'; ev('safed', 'SAFED — RC switch in SAFE, aircraft disarmed. Safe to approach.');
      }
      publish();
    }
    function snapshot() {
      const lvl = order.indexOf(st.stage);
      return {
        v: 1, demo: true, stage: st.stage, held: st.held, hold_reason: st.hold_reason,
        hold_elapsed: st.held ? now() - st.hold_since : 0, mission: st.mission, total: st.total, remaining: remaining(),
        t_plus: st.takeoff_at ? now() - st.takeoff_at : null, marks: { handover: Math.min(180, st.total), gonogo: 60, arm: 10 },
        go: st.go, manual: st.manual, rc_gate: st.rc, pic: st.pic ? { ...st.pic, connected: true } : { console: null, name: null, connected: false },
        consoles: [{ console: consoleId, operator: api.operatorName, page: '', age: 0 }],
        checks: checks().map(c => ({ ...c, status: (st.stage !== 'standby' && lvl >= order.indexOf(gateStage[c.gate])) || c.raw === 'pass' ? c.raw : 'pending' })),
        motors: [1, 2, 3, 4].map(i => ({ ch: i, rpm: st.armed ? 1480 + i * 8 + (st.alt > 0 ? 2600 : 0) : 0, current: st.armed ? (st.alt > 0 ? 9.5 : 0.8) : 0, connected: true, status: 0 })),
        events: st.events.slice(0, 20), abort: st.abort, max_alt: st.alt, armed: !!st.armed,
        profile: st.mission ? st.profile : null, step: st.step || null,
        home_point: st.home || null, plan: st.profile === 'flight2' && st.home ? st.demoPlan || null : null, pos: st.demoPos || (st.home ? [st.home.lat, st.home.lon] : null),
        config: { takeoff_alt: st.profile === 'flight1' ? 2 : 5, require_rtk: false, require_esc_telemetry: false, rc_gate_channel: 5, handover_at: 180, gonogo_at: 60, arm_at: 10, recycle_after: 120, sim: false },
      };
    }
    function publish() {
      accept({ mission: snapshot(), faults: { revision: 1, active: [...faults.values()], recent: [],
        counts: { critical: [...faults.values()].filter(f => f.severity === 'critical').length, warning: [...faults.values()].filter(f => f.severity === 'warning').length, info: 0 } } });
      if (st.held) faults.set('mission:hold', { id: 'D2', key: 'mission:hold', severity: 'critical', source: 'Countdown', message: 'HOLD — ' + st.hold_reason, since: st.hold_since, acked_by: null });
      else faults.delete('mission:hold');
    }
    let demoRoute = null;
    function handle(c) {
      const who = c.operator || 'Operator', ok = msg => { tick(); return { ok: true, msg }; }, no = msg => ({ ok: false, msg });
      switch (c.action) {
        case 'mission_ping': return { ok: true };
        case 'mission_create':
          if (st.stage !== 'standby') return no('A mission is already active');
          const word = c.bench ? 'test' : 'flight';
          if (String(c.confirm || '').trim().toLowerCase() !== word) return no(`Type '${word}' to confirm`);
          if (!String(c.name || '').trim()) return no('Mission name is required');
          st = { ...st, stage: 'countdown', total: c.total || 180, remaining: c.total || 180, deadline: now() + (c.total || 180), held: false,
            mission: { name: c.name, folder: '~/airsent_missions/DEMO_' + c.name, operator: who, created_at: now(), profile: c.profile || 'hover', bench: !!c.bench }, profile: c.profile || 'hover', step: null, go: null, manual: {},
            rc: 'SAFE', takeoff_at: null, alt: 0, armed: false, abort: null, pic: { console: c.console_id, name: who }, events: [], home: null, demoPlan: null, demoPos: null, f2: null };
          ev('create', `Mission '${c.name}' created (demo)`, who); return ok('Mission started (demo)');
        case 'mission_hold': if (!pre.includes(st.stage)) return no('Hold is only available before takeoff'); hold('Hold requested by ' + who, who); return ok('Held');
        case 'mission_resume': {
          if (!st.held) return no('The countdown is not on hold');
          let r = st.remaining;
          if (now() - st.hold_since > 120 && r < 180) r = Math.min(180, st.total);
          if (r <= 60) { r = 60; st.go = null; st.rc = 'SAFE'; }
          st.held = false; st.hold_reason = ''; st.remaining = r; st.deadline = now() + r; st.stage = stageFor(r);
          ev('resume', 'Countdown resumed', who); return ok('Resumed');
        }
        case 'mission_go':
          if (!['gonogo', 'prearm'].includes(st.stage)) return no('GO is accepted from the vehicle handover until T-10');
          st.go = { by: who, at: now() }; st.rc = 'ARM'; ev('go', 'GO — area and props clear (demo: RC switch flipped to ARM)', who); return ok('GO recorded');
        case 'mission_manual':
          if (c.value) st.manual[c.key] = { by: who, at: now() }; else delete st.manual[c.key];
          ev('check', (c.value ? 'Confirmed: ' : 'Unconfirmed: ') + manualLabels[c.key], who); return ok('Updated');
        case 'mission_abort':
          if (pre.includes(st.stage)) { st.armed = false; st.remaining = remaining(); st.deadline = null; st.held = false; st.abort = { reason: 'Aborted by operator', by: who, mode: 'disarm' }; st.stage = 'aborted'; ev('abort', 'ABORTED before takeoff', who); return ok('Aborted'); }
          if (['takeoff', 'flight'].includes(st.stage)) { st.stage = 'flight'; st.abort = { reason: 'Aborted by operator', by: who, mode: c.mode === 'rtl' ? 'rtl' : 'land' }; ev('abort', 'ABORT in flight — ' + (c.mode === 'rtl' ? 'RETURN TO HOME' : 'LAND NOW'), who); return ok('Abort'); }
          return no('Nothing to abort');
        case 'mission_close':
          if (!['safed', 'aborted'].includes(st.stage)) return no('Close the mission after the aircraft is safed or aborted');
          st.stage = 'standby'; st.mission = null; st.remaining = st.total; st.events = []; st.abort = null; st.takeoff_at = null; st.alt = 0; st.rc = 'SAFE';
          return ok('Mission closed');
        case 'fault_ack':
          faults.forEach(f => { if (c.fault_id === 'all' || f.id === c.fault_id) f.acked_by = f.acked_by || who; }); return ok('Acknowledged');
        case 'mission_claim_pic': st.pic = { console: c.console_id, name: who }; return ok('You are pilot in command');
        case 'emergency_stop': return ok('EMERGENCY STOP — motors stopped (demo)');
        case 'goto': {
          if (!(c.alt >= 2 && c.alt <= 115)) return no('Altitude must be 2–115 m');
          return ok(`Flying to the point at ${Math.round(c.alt)} m (demo)`);
        }
        case 'motor_test':
          if (!c.props_off_confirmed) return no('Confirm the propellers are removed');
          if (!(c.throttle >= 5 && c.throttle <= 20) || !(c.seconds >= 0.5 && c.seconds <= 3)) return no('Throttle must be 5–20 % and time 0.5–3 s');
          return ok(`Motor test running — ${c.motor ? 'M' + c.motor : 'all motors in turn'} at ${c.throttle}% for ${c.seconds} s (demo)`);
        case 'wp_upload': {
          const wps = (c.plan && c.plan.waypoints) || [];
          if (!wps.length) return no('The plan has no waypoints');
          const bad = wps.findIndex(w => !['RTL', 'LAND'].includes(w.cmd) && (w.alt < 2 || w.alt > 120));
          if (bad >= 0) return no(`WP ${bad + 1}: altitude ${wps[bad].alt} m outside 2-120 m`);
          const last = wps[wps.length - 1].cmd;
          demoRoute = wps.map(w => ({ ...w }));
          api.vehicleMission = { known: true, verified: true, count: wps.length + 2, nav_count: wps.length, signature: 'demo' + Date.now(), by: who, at: now() };
          return { ok: true, msg: `Uploaded and verified ${wps.length} waypoints on the Pixhawk (demo)`, vehicle_mission: api.vehicleMission,
            result: { signature: api.vehicleMission.signature, nav_items: wps.length },
            warnings: ['RTL', 'LAND'].includes(last) ? [] : ['The mission does not end with RTL or LAND — the drone will hover at the last waypoint'] };
        }
        case 'wp_download':
          if (!demoRoute) return no('No mission stored on the demo drone');
          return { ok: true, msg: `Downloaded ${demoRoute.length} waypoints from the Pixhawk (demo)`, vehicle_mission: api.vehicleMission,
            result: { waypoints: demoRoute.map(w => ({ ...w })), signature: api.vehicleMission.signature } };
        default: return no('Unknown action');
      }
    }
    return { handle, tick };
  })();

  // ── start ────────────────────────────────────────────────────────────────
  function start() {
    if (mode === 'demo') { setInterval(demo.tick, 250); demo.tick(); }
    else { connectTelemetry(); connectCommands(); }
    setInterval(ping, 2000);
    setInterval(emit, 250);   // smooth clock rendering between snapshots
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
