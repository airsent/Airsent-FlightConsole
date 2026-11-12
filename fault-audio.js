/* Airsent console alarm tones — no words, no sound files: tones are synthesised with Web Audio.
 *
 *   MASTER WARNING  (critical, unacknowledged): triple harsh buzz, repeats every 1.6 s until the
 *                   fault is acknowledged (ACK / ACK ALL) or clears. Viewers (who can't ACK) hear 3 rounds.
 *   MASTER CAUTION  (new warning): two short dry alert pulses, once per new warning (grouped, max 1 per 3 s).
 *   CLEARED         (a critical fault went away) and countdown cues (start, VEHICLE HANDOVER, GO/NO-GO,
 *                   ARM, TAKE-OFF, LANDED / DISARMED): a short soft falling note.
 *   Mute is locked while the aircraft is armed (and switches itself back on at arming).
 *
 * Only one console tab per browser sounds (the visible one, else the oldest), so a pilot with
 * five console pages open hears one alarm. Browsers block audio until the page has been clicked
 * once; until then the speaker button pulses and says so.
 *
 *   AirsentAlarm.update(faultList, { viewer })   // called by mission-ui.js on every render
 *   AirsentAlarm.button()                        // a speaker toggle element (mute / test)
 *   AirsentAlarm.test('warning' | 'caution' | 'cleared')
 */
(() => {
  'use strict';
  if (window.AirsentAlarm) return;
  const KEY = 'airsentAlarm';
  const prefs = (() => { try { return { muted: false, volume: 0.7, ...JSON.parse(localStorage.getItem(KEY) || '{}') }; } catch (e) { return { muted: false, volume: 0.7 }; } })();
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch (e) {} };

  let ctx = null, master = null, unlocked = false;
  function audio() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
      master = ctx.createGain();
      master.gain.value = prefs.volume;
      // gentle limiter so stacked tones never clip
      const comp = ctx.createDynamicsCompressor();
      comp.threshold.value = -10; comp.ratio.value = 8;
      master.connect(comp).connect(ctx.destination);
    }
    return ctx;
  }
  function unlock() {
    const c = audio(); if (!c) return;
    if (c.state === 'suspended') c.resume().then(() => { unlocked = true; paint(); }).catch(() => {});
    else { unlocked = true; paint(); }
  }
  ['pointerdown', 'keydown', 'touchstart'].forEach(ev => window.addEventListener(ev, unlock, { capture: true, passive: true }));

  // one tone: frequency, start offset, duration, waveform, peak gain, optional harmonic
  function tone(f, at, dur, type = 'sine', peak = 0.5, harmonic = 0) {
    const c = audio(); if (!c || prefs.muted) return;
    const t0 = c.currentTime + at;
    const g = c.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(peak, t0 + 0.012);
    g.gain.setValueAtTime(peak, t0 + Math.max(0.02, dur - 0.05));
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    g.connect(master);
    const o = c.createOscillator(); o.type = type; o.frequency.value = f; o.connect(g); o.start(t0); o.stop(t0 + dur + 0.02);
    if (harmonic) { const h = c.createOscillator(), hg = c.createGain(); h.frequency.value = f * 2; hg.gain.value = harmonic; h.connect(hg).connect(g); h.start(t0); h.stop(t0 + dur + 0.02); }
  }
  // Angry buzz: two detuned square waves + a saw an octave up, low-passed. Short and harsh.
  function buzz(at, dur, f = 440, peak = 0.6) {
    const c = audio(); if (!c || prefs.muted) return;
    const t0 = c.currentTime + at;
    const g = c.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(peak, t0 + 0.006);
    g.gain.setValueAtTime(peak, t0 + dur - 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 3000;
    [[f, 'square'], [f * 1.018, 'square'], [f * 2.001, 'sawtooth']].forEach(([fr, type]) => {
      const o = c.createOscillator(), og = c.createGain(); o.type = type; o.frequency.value = fr; og.gain.value = 0.33;
      o.connect(og).connect(lp); o.start(t0); o.stop(t0 + dur + 0.02);
    });
    lp.connect(g).connect(master);
  }
  // Serious alert pulse: a dissonant-free fifth, square tone softened by a low-pass, short and dry.
  function pulse(at, dur, f, peak = 0.42) {
    const c = audio(); if (!c || prefs.muted) return;
    const t0 = c.currentTime + at;
    const g = c.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(peak, t0 + 0.006);
    g.gain.setValueAtTime(peak, t0 + dur - 0.03);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 1800; lp.Q.value = 0.5;
    [[f, 'square', 0.32], [f * 1.5, 'square', 0.16], [f / 2, 'triangle', 0.25]].forEach(([fr, type, a]) => {
      const o = c.createOscillator(), og = c.createGain(); o.type = type; o.frequency.value = fr; og.gain.value = a;
      o.connect(og).connect(lp); o.start(t0); o.stop(t0 + dur + 0.03);
    });
    lp.connect(g).connect(master);
  }
  const SOUNDS = {
    warning: () => { buzz(0, 0.16); buzz(0.22, 0.16); buzz(0.44, 0.16); },   // BZT-BZT-BZT, every 1.6 s until ACK
    caution: () => { pulse(0, 0.16, 620); pulse(0.24, 0.16, 620); },    // two short, dry alert pulses
    cleared: () => { tone(660, 0, 0.12, 'sine', 0.25); tone(495, 0.13, 0.22, 'sine', 0.22); },
  };

  // ── one sounding tab per browser ─────────────────────────────────────────
  const tabId = Date.now() + Math.random();
  const peers = new Map();                               // id -> {visible, t}
  let bc = null;
  try { bc = new BroadcastChannel('airsent-alarm'); } catch (e) {}
  const announce = () => bc && bc.postMessage({ id: tabId, visible: document.visibilityState === 'visible', t: Date.now() });
  if (bc) bc.onmessage = ev => { const d = ev.data || {}; if (d.bye) peers.delete(d.id); else if (d.id) peers.set(d.id, d); };
  setInterval(announce, 1000); announce();
  document.addEventListener('visibilitychange', announce);
  window.addEventListener('pagehide', () => bc && bc.postMessage({ id: tabId, bye: true }));
  function iAmLeader() {
    const now = Date.now(), me = { id: tabId, visible: document.visibilityState === 'visible' };
    const all = [me, ...[...peers.values()].filter(p => now - p.t < 3000)];
    const vis = all.filter(p => p.visible);
    const pool = vis.length ? vis : all;
    return pool.reduce((a, b) => (a.id < b.id ? a : b)).id === tabId;
  }

  // ── fault state → sounds ────────────────────────────────────────────────
  const seen = new Set(), lastCrit = new Set(), critSince = new Map(), alarmed = new Set();
  const loadedAt = Date.now();
  const GRACE_MS = 6000, PERSIST_MS = 2000;      // ignore start-up blips and one-off flickers
  let first = true, loopTimer = null, loopRounds = 0, lastCaution = 0, viewerMode = false, wantLoop = false, needsUnlock = false;

  const stats = { warning: 0, caution: 0, cleared: 0, blocked: 0 };
  function play(name) {
    if (prefs.muted || !iAmLeader()) return;
    stats[name]++;
    const c = audio();
    if (!c || c.state !== 'running') { stats.blocked++; needsUnlock = true; paint(); return; }
    SOUNDS[name]();
  }
  function loop() {
    clearTimeout(loopTimer);
    if (!wantLoop) { loopRounds = 0; return; }
    if (viewerMode && loopRounds >= 3) return;          // viewers can't ACK: three rounds, then quiet
    loopRounds++;
    play('warning');
    loopTimer = setTimeout(loop, 1600);
  }

  // Countdown phases that play the soft 'cleared' tone (operator's choice): start, each phase,
  // take-off, and landed / disarmed at the end.
  const PHASE_TONE = new Set(['countdown', 'prearm', 'gonogo', 'armed', 'takeoff', 'landed', 'safed']);
  let lastStage = null, endToned = false, armedNow = false;
  function phase(stage) {
    if (stage === lastStage) return;
    const prev = lastStage; lastStage = stage;
    if (prev === null || stage == null) return;                   // first look after page load: no tone
    if (stage === 'countdown') endToned = false;
    if (!PHASE_TONE.has(stage)) return;
    if (stage === 'landed' || stage === 'safed') { if (endToned) return; endToned = true; }
    play('cleared');
  }

  function update(list, opts = {}) {
    viewerMode = !!opts.viewer;
    armedNow = !!opts.armed;
    if (armedNow && prefs.muted) { prefs.muted = false; save(); }   // no muted alarms on a live aircraft
    if ('stage' in opts) phase(opts.stage);
    const open = (list || []).filter(f => !f.acked_by && f.severity !== 'info');
    const crit = open.filter(f => f.severity === 'critical');
    const critIds = new Set((list || []).filter(f => f.severity === 'critical').map(f => f.id));
    // new warnings -> one caution chime (grouped)
    const fresh = open.filter(f => f.severity !== 'critical' && !seen.has(f.id));
    (list || []).forEach(f => seen.add(f.id));
    if (Date.now() - loadedAt > GRACE_MS && fresh.length && Date.now() - lastCaution > 3000) { lastCaution = Date.now(); play('caution'); }
    // a critical fault that had been sounding cleared (gone, not just acknowledged)
    const goneAlarmed = [...lastCrit].some(id => !critIds.has(id));
    lastCrit.clear(); (list || []).filter(f => f.severity === 'critical' && alarmed.has(f.id)).forEach(f => lastCrit.add(f.id));
    // unacknowledged critical (present > 2 s, and not in the first seconds after the page loads)
    // -> master warning until ACK / clear
    const now = Date.now();
    crit.forEach(f => { if (!critSince.has(f.id)) critSince.set(f.id, now); });
    [...critSince.keys()].forEach(id => { if (!crit.some(f => f.id === id)) critSince.delete(id); });
    const was = wantLoop;
    const loud = now - loadedAt > GRACE_MS ? crit.filter(f => now - critSince.get(f.id) >= PERSIST_MS) : [];
    loud.forEach(f => { alarmed.add(f.id); lastCrit.add(f.id); });
    wantLoop = loud.length > 0;
    if (goneAlarmed && !crit.length) play('cleared');
    if (wantLoop && !was) { loopRounds = 0; loop(); }
    if (!wantLoop && was) clearTimeout(loopTimer);
    first = false;
    paint();
  }

  // ── speaker button ───────────────────────────────────────────────────────
  const buttons = new Set();
  function injectCss() {
    if (document.getElementById('aa-css')) return;
    const s = document.createElement('style'); s.id = 'aa-css';
    s.textContent = `.aa-btn { background:none; border:1px solid rgba(127,220,255,.25); color:#9be5ff; font:10px 'Share Tech Mono',monospace; letter-spacing:1px;
  height:24px; padding:0 7px; cursor:pointer; margin-left:6px; display:inline-flex; align-items:center; gap:4px; vertical-align:middle; position:relative; }
.aa-btn:hover { color:#fff; border-color:#9be5ff; } .aa-btn.muted { color:#ff8095; border-color:rgba(255,51,85,.5); }
.aa-btn.locked { animation:aa-pulse 1s ease-in-out infinite; color:#ffb800; border-color:#ffb800; } @keyframes aa-pulse { 50% { opacity:.4; } }
.aa-menu { position:absolute; top:calc(100% + 6px); right:0; z-index:10000; background:#060d14; border:1px solid rgba(0,180,255,.3); padding:10px; width:230px;
  font:10px 'Share Tech Mono',monospace; color:#9fb3c4; text-align:left; box-shadow:0 8px 24px rgba(0,0,0,.6); letter-spacing:.5px; }
.aa-menu b { color:#e8f4ff; font-weight:400; } .aa-menu .r { display:flex; gap:6px; margin-top:8px; flex-wrap:wrap; }
.aa-menu button { background:#0b1620; border:1px solid rgba(127,220,255,.3); color:#9be5ff; font:9px 'Share Tech Mono',monospace; padding:3px 6px; cursor:pointer; letter-spacing:1px; }
.aa-menu button:hover { color:#fff; } .aa-menu input { width:100%; accent-color:#00c8ff; margin-top:6px; }`;
    document.head.appendChild(s);
  }
  function paint() {
    buttons.forEach(b => {
      if (!b.isConnected) { buttons.delete(b); return; }
      const c = ctx;
      const locked = !prefs.muted && (!c || c.state !== 'running') && (needsUnlock || wantLoop);
      b.classList.toggle('muted', prefs.muted);
      b.classList.toggle('locked', locked);
      b.firstChild.textContent = prefs.muted ? '🔇 SOUND OFF' : locked ? '🔈 CLICK FOR SOUND' : '🔊';
      b.title = prefs.muted ? 'Fault tones are OFF — click to change' : locked ? 'Your browser blocks sound until you click the page once' : 'Fault tones on — click for options';
    });
  }
  function button() {
    injectCss();
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'aa-btn';
    b.appendChild(document.createTextNode('🔊'));
    b.addEventListener('click', e => {
      e.stopPropagation(); unlock();
      let m = b.querySelector('.aa-menu');
      if (m) { m.remove(); return; }
      m = document.createElement('div'); m.className = 'aa-menu';
      m.innerHTML = `<div><b>FAULT TONES</b> — critical (any time, incl. holds): triple buzz until ACK · new warning: two pulses · countdown start, each phase, take-off, landed and fault cleared: soft tone</div>
        <div class="r"><button data-a="mute" ${armedNow && !prefs.muted ? 'disabled style="opacity:.4;cursor:not-allowed"' : ''}>${prefs.muted ? 'TURN SOUND ON' : 'MUTE'}</button>${armedNow ? '<span style="color:#ffb800;align-self:center">locked on while armed — ACK silences the buzzer</span>' : ''}</div>
        <label style="display:block;margin-top:8px">VOLUME<input type="range" min="0.1" max="1" step="0.05" value="${prefs.volume}"></label>
        <div class="r"><button data-a="warning">TEST WARNING</button><button data-a="caution">TEST CAUTION</button><button data-a="cleared">TEST CLEARED</button></div>`;
      m.addEventListener('click', ev => {
        ev.stopPropagation();
        const a = ev.target.closest('[data-a]'); if (!a) return;
        if (a.dataset.a === 'mute') { if (armedNow && !prefs.muted) return; prefs.muted = !prefs.muted; save(); if (prefs.muted) clearTimeout(loopTimer); else if (wantLoop) loop(); m.remove(); paint(); return; }
        test(a.dataset.a);
      });
      m.querySelector('input').addEventListener('input', ev => { prefs.volume = +ev.target.value; save(); if (master) master.gain.value = prefs.volume; });
      b.appendChild(m);
      if (b.getBoundingClientRect().left < window.innerWidth / 2) { m.style.left = '0'; m.style.right = 'auto'; }   // keep it on screen
      const close = ev => { if (!b.contains(ev.target)) { m.remove(); document.removeEventListener('click', close, true); } };
      setTimeout(() => document.addEventListener('click', close, true), 0);
    });
    buttons.add(b); paint();
    return b;
  }
  function test(name) {
    unlock();
    const c = audio(); if (!c) return;
    const go = () => { const was = prefs.muted; prefs.muted = false; SOUNDS[name](); prefs.muted = was; };
    if (c.state === 'running') go(); else c.resume().then(go).catch(() => {});
  }

  window.AirsentAlarm = { update, button, test, stats, get muted() { return prefs.muted; } };
})();
