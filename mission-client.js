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
    const manualLabels = { manual_wind: 'Wind and weather within limits', manual_airspace: 'Airspace authorization (LAANC) confirmed',
      manual_observers: 'Visual observers in position', manual_payload: 'Payload secured' };
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
        c('gps_fix', 'GPS', 'GPS fix: RTK fixed', 'prearm', true, 'RTK FIX', 'RTK FIX'),
        c('gps_sats', 'GPS', 'Satellites', 'prearm', true, '21', '>= 10'),
        c('gps_hdop', 'GPS', 'Horizontal dilution (HDOP)', 'prearm', true, '0.62', '<= 1.2'),
        c('home', 'GPS', 'Home position set', 'prearm', true, 'SET', 'set'),
        c('ekf', 'Estimator', 'EKF healthy', 'prearm', true, 'HEALTHY', 'healthy'),
        c('battery_volt', 'Battery', 'Battery voltage', 'prearm', true, '25.0 V', '>= 22.8 V'),
        c('battery_pct', 'Battery', 'Battery remaining', 'prearm', true, '97%', '>= 60%'),
        c('imu', 'Sensors', 'IMU', 'prearm', true, 'OK', 'OK'),
        c('baro', 'Sensors', 'Barometer', 'prearm', true, 'OK', 'OK'),
        c('mag', 'Sensors', 'Compass', 'prearm', true, 'OK', 'OK'),
        c('lidar', 'Sensors', 'LiDAR rangefinder (H-Flow)', 'prearm', true, (0.3 + st.alt).toFixed(2) + ' m', 'live'),
        c('flow', 'Sensors', 'Optical flow (H-Flow)', 'prearm', true, 'Q 188', 'live'),
        c('vibration', 'Sensors', 'Vibration', 'prearm', true, '6.1 m/s²', '< 30'),
        c('fence', 'Safety', 'Geofence and max altitude active on Pixhawk', 'prearm', true, 'ENABLED', 'enabled'),
        c('front_cam', 'Jetson', 'Front camera (obstacle detection)', 'prearm', true, 'STREAMING', 'streaming'),
        c('down_cam', 'Jetson', 'Down camera', 'prearm', true, 'STREAMING', 'streaming', true),
        c('jetson_temp', 'Jetson', 'Jetson temperature', 'prearm', true, '54 °C', '< 80 °C'),
      ];
      Object.entries(manualLabels).forEach(([k, label]) => list.push(c(k, 'Manual', label, 'gonogo', !!st.manual[k],
        st.manual[k] ? '✓ ' + st.manual[k].by : 'NOT CONFIRMED', 'confirm by T-60')));
      list.push(c('go_poll', 'GO/NO-GO', 'Pilot in command GO (area and props clear)', 'armed', !!st.go, st.go ? 'GO · ' + st.go.by : 'WAITING', 'GO by T-10'));
      list.push(c('obstacles', 'Safety', 'No obstacle close to the aircraft', 'takeoff', true, 'CLEAR', 'clear'));
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
            if (s === 'takeoff') { st.takeoff_at = t; st.remaining = 0; ev('takeoff', 'T-0 — TAKEOFF to 5 m'); return; }
          }
          const b = blocking(s);
          if (b.length) { hold(b[0].label + ': ' + b[0].value); return; }
        }
      } else if (st.stage === 'takeoff') {
        st.alt = Math.min(5, (t - st.takeoff_at) * 1.2);
        if (st.alt >= 4.5) { st.stage = 'flight'; ev('flight', 'Climb complete — hovering at 4.5 m'); }
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
        events: st.events.slice(0, 20), abort: st.abort, max_alt: st.alt,
        config: { takeoff_alt: 5, require_rtk: true, require_esc_telemetry: true, rc_gate_channel: 7, handover_at: 180, gonogo_at: 60, arm_at: 10, recycle_after: 120, sim: false },
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
          if (String(c.confirm || '').trim().toLowerCase() !== 'flight') return no("Type 'flight' to confirm a real flight");
          if (!String(c.name || '').trim()) return no('Mission name is required');
          st = { ...st, stage: 'countdown', total: c.total || 180, remaining: c.total || 180, deadline: now() + (c.total || 180), held: false,
            mission: { name: c.name, folder: '~/airsent_missions/DEMO_' + c.name, operator: who, created_at: now() }, go: null, manual: {},
            rc: 'SAFE', takeoff_at: null, alt: 0, armed: false, abort: null, pic: { console: c.console_id, name: who }, events: [] };
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
