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
 *   AirsentAlarm.button()                        // small mute toggle (placed inside the fault panels)
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

  // ── small mute toggle, shown inside the fault panels ────────────────────
  const buttons = new Set();
  function injectCss() {
    if (document.getElementById('aa-css')) return;
    const st = document.createElement('style'); st.id = 'aa-css';
    st.textContent = `.aa-mute { background:none; border:1px solid rgba(127,220,255,.25); color:#9be5ff; font:9px 'Share Tech Mono',monospace; letter-spacing:1px;
  padding:2px 6px; cursor:pointer; margin-left:auto; margin-right:6px; white-space:nowrap; line-height:1.4; }
.aa-mute:hover { color:#fff; border-color:#9be5ff; } .aa-mute.muted { color:#ff8095; border-color:rgba(255,51,85,.5); }
.aa-mute:disabled { opacity:.45; cursor:not-allowed; }`;
    document.head.appendChild(st);
  }
  function paint() {
    buttons.forEach(b => {
      if (!b.isConnected && b._shown) { buttons.delete(b); return; }     // removed from the page
      if (b.isConnected) b._shown = true;
      b.classList.toggle('muted', prefs.muted);
      b.textContent = prefs.muted ? '🔇 MUTED' : '🔊 SOUND';
      b.disabled = armedNow && !prefs.muted;
      b.title = armedNow ? 'Sound stays on while armed — ACK silences the buzzer'
        : prefs.muted ? 'Fault tones are off — click to turn them on' : 'Fault tones on — click to mute';
    });
  }
  function button() {
    injectCss();
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'aa-mute';
    b.addEventListener('click', e => {
      e.stopPropagation(); unlock();
      if (armedNow && !prefs.muted) return;
      prefs.muted = !prefs.muted; save();
      if (prefs.muted) clearTimeout(loopTimer); else if (wantLoop) loop();
      paint();
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
