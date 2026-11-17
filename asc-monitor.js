/* ASC Monitor page — a standing view of the Jetson's Automated Self Checks.
 *
 * Built for one job: somebody watches this on flight day and must notice a problem the
 * moment it appears, without touching anything that could reach the aircraft. There are
 * no controls here. The failsafe page keeps the ASC action switch; this page only reads.
 *
 * Data is the same ASC block every console gets in telemetry (viewers included):
 *   defs : [id, group, label, unit, note, when]      sent every ~10 s
 *   live : [id, level, value, msg?]                  every frame; level -1 not active,
 *                                                    0/1 ok, 2 warning, 3 critical
 * So this page cannot disagree with the failsafe page's drawer — one source.
 *
 * Three things it does that the drawer does not:
 *   - alerts: title prefix for a background tab, a flashing banner, an optional tone
 *   - keeps an event log with wall-clock times, so "when did that start" is answerable
 *   - tracks the worst level seen this session, because a critical that cleared still
 *     happened and still matters afterwards
 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const M = () => window.airsentMission;
  const mode = (localStorage.getItem('airsentMode') || 'demo').toLowerCase();
  const isDemo = mode === 'demo';

  const LV = { '-1': ['na', 'NOT ACTIVE'], 0: ['ok', 'OK'], 1: ['ok', 'OK'], 2: ['warn', 'WARNING'], 3: ['crit', 'CRITICAL'] };
  const WHEN = { session: '', armed: 'active when armed', flying: 'active in flight' };
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const num = v => typeof v === 'number' && isFinite(v);

  const hist = {};                 // id -> last 120 numeric samples (60 s at 2 Hz)
  const since = {};                // id -> when its current problem was first seen
  let worst = -1;                  // worst level seen this session
  let lastCrit = '';               // signature of the criticals already alerted on
  let sessionKey = null;
  let audio = null;

  const sound = $('ascSound'), hideIdle = $('ascHideIdle');
  sound.checked = localStorage.getItem('ascSound') === '1';
  hideIdle.checked = localStorage.getItem('ascHideIdle') === '1';
  sound.onchange = () => localStorage.setItem('ascSound', sound.checked ? '1' : '0');
  hideIdle.onchange = () => { localStorage.setItem('ascHideIdle', hideIdle.checked ? '1' : '0'); render(); };

  /* A tone, not a file: the console ships with no audio assets and must come up on a
     Jetson in the field without fetching anything. Browsers block audio until the page
     has been interacted with, which is why the checkbox is the opt-in. */
  function beep() {
    if (!sound.checked) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === 'suspended') audio.resume();
      const t = audio.currentTime;
      for (let i = 0; i < 3; i++) {
        const o = audio.createOscillator(), g = audio.createGain();
        o.type = 'square'; o.frequency.value = 880;
        g.gain.setValueAtTime(0.0001, t + i * 0.22);
        g.gain.exponentialRampToValueAtTime(0.12, t + i * 0.22 + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.22 + 0.16);
        o.connect(g); g.connect(audio.destination);
        o.start(t + i * 0.22); o.stop(t + i * 0.22 + 0.18);
      }
    } catch (e) { /* no audio available: the banner and title still alert */ }
  }

  function ascBlock() {
    if (isDemo) return demo();
    const a = M() && M().asc;
    return a && M().stateFresh() ? a : null;
  }

  // Demo mode must never imply a real aircraft is being watched.
  function demo() {
    const s = M() && M().state;
    const watching = s && ['prearm', 'gonogo', 'armed', 'takeoff', 'flight', 'landed'].includes(s.stage);
    const t = Date.now() / 1000;
    const vib = +(18 + 14 * Math.sin(t / 4)).toFixed(1);
    const live = [['fc_link', 0, 0.08], ['vps_link', 0, 1], ['console', 0, 0.4], ['rc', 0, 1],
      ['gps_fix', 0, 'RTK FIX'], ['gps_sats', 0, 21], ['gps_hdop', 0, 0.62], ['gps_degraded', 0, 0],
      ['ekf', 0, 1], ['cell_voltage', 0, 4.12], ['current', 0, 1.8], ['jetson_temp', 0, 54],
      ['vibration', vib > 30 ? 2 : 0, vib, vib > 30 ? 'High vibration ' + vib.toFixed(0) + ' m/s²' : undefined]];
    return { state: watching ? 'watching' : 'standby', checks: live.length, checks_per_s: watching ? 880 : 0,
             session: watching ? 'DEMO' : null, live, events: [], triggered: [],
             flight: watching ? { flying_s: 42, dist_m: 18 } : null };
  }

  function defsOf() {
    if (isDemo) {
      return [['fc_link', 'Link', 'Pixhawk heartbeat', 's', 'lost after 5 s', 'session'],
        ['vps_link', 'Link', 'Jetson ↔ VPS relay', 'flag', 'relay connected', 'session'],
        ['console', 'Link', 'Operator console', 's', 'hold 10 s · RTL 30 s', 'session'],
        ['rc', 'RC', 'RC receiver', 'flag', 'receiver signal present', 'session'],
        ['gps_fix', 'GPS', 'GPS fix', 'text', 'needs 3D fix or better', 'session'],
        ['gps_sats', 'GPS', 'Satellites', '', 'warn < 10', 'session'],
        ['gps_hdop', 'GPS', 'HDOP', '', 'warn > 1.5', 'session'],
        ['gps_degraded', 'GPS', 'Satellites lost in flight', '', 'warn -35% · critical -50%', 'armed'],
        ['ekf', 'Estimator', 'EKF health', 'flag', 'EKF must be healthy', 'session'],
        ['cell_voltage', 'Power', 'Cell voltage', 'V/cell', 'warn 3.6 · crit 3.5', 'session'],
        ['current', 'Power', 'Current draw', 'A', 'warn > 70 A', 'armed'],
        ['vibration', 'Vibration', 'Vibration', 'm/s²', 'warn 30 · crit 60', 'armed'],
        ['jetson_temp', 'Jetson', 'Jetson temperature', '°C', 'warn ≥ 85 °C', 'session']];
    }
    return (M() && M().ascDefs) || [];
  }

  const mmss = s => s == null ? '--' : (s >= 3600 ? Math.floor(s / 3600) + ':' : '')
    + String(Math.floor(s % 3600 / 60)).padStart(2, '0') + ':' + String(Math.floor(s % 60)).padStart(2, '0');

  function fmt(v, unit, lvl) {
    if (unit === 'flag') return lvl >= 2 ? 'NO' : lvl < 0 ? '—' : 'YES';
    if (v === null || v === undefined || v === '') return '—';
    if (!num(v)) return esc(v);
    const a = Math.abs(v);
    const t = Number.isInteger(v) ? String(v) : a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
    return t + (unit && unit !== 'text' ? '<small>' + esc(unit) + '</small>' : '');
  }

  function spark(id, lvl) {
    const h = hist[id];
    if (!h || h.length < 3) return '';
    const lo = Math.min(...h), hi = Math.max(...h), span = hi - lo || 1;
    const pts = h.map((v, i) => `${(i / (h.length - 1) * 60).toFixed(1)},${(14 - (v - lo) / span * 13).toFixed(1)}`).join(' ');
    const col = lvl >= 3 ? '#f87171' : lvl === 2 ? '#fbbf24' : '#60a5fa';
    return `<svg viewBox="0 0 60 15" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.2"/></svg>`;
  }

  let lastSample = 0;

  function render() {
    const a = ascBlock();
    const defs = defsOf();
    const live = (a && a.live) || [];
    const by = Object.fromEntries(live.map(r => [r[0], r]));
    const now = Date.now();

    // A new session resets what "worst so far" means.
    const key = a && a.session ? String(a.session) : null;
    if (key !== sessionKey) { sessionKey = key; worst = -1; Object.keys(since).forEach(k => delete since[k]); }

    if (now - lastSample >= 500) {
      lastSample = now;
      live.forEach(r => {
        if (num(r[2])) { (hist[r[0]] = hist[r[0]] || []).push(r[2]); if (hist[r[0]].length > 120) hist[r[0]].shift(); }
        if (r[1] >= 2) { since[r[0]] = since[r[0]] || now; } else { delete since[r[0]]; }
      });
    }

    const n = { ok: 0, warn: 0, crit: 0, na: 0 };
    live.forEach(r => { n[(LV[r[1]] || LV['-1'])[0]]++; });
    live.forEach(r => { if (r[1] > worst) worst = r[1]; });

    // ── banner ──────────────────────────────────────────────────────────────
    const fresh = isDemo || (M() && M().stateFresh());
    const vd = $('ascVerdict'), vt = $('ascVerdictText'), vs = $('ascVerdictSub');
    let cls, text, sub;
    if (!fresh || !a) {
      cls = 'na'; text = 'NO DATA';
      sub = fresh ? 'ASC is not reporting' : 'telemetry link lost — these numbers are stale';
    } else if (n.crit) {
      cls = 'crit'; text = n.crit + ' CRITICAL';
      sub = live.filter(r => r[1] >= 3).map(r => r[3] || r[0]).slice(0, 2).join(' · ');
    } else if (n.warn) {
      cls = 'warn'; text = n.warn + ' WARNING' + (n.warn > 1 ? 'S' : '');
      sub = live.filter(r => r[1] === 2).map(r => r[3] || r[0]).slice(0, 2).join(' · ');
    } else if (a.state === 'standby') {
      cls = 'ok'; text = 'STANDBY'; sub = 'checks start at pre-arm or arming';
    } else {
      cls = 'ok'; text = 'ALL CLEAR'; sub = n.ok + ' checks passing';
    }
    vd.className = 'asc-verdict ' + cls;
    vt.textContent = text;
    vs.textContent = sub || '';

    // Alert only on a change, so a standing critical does not beep forever.
    const sig = live.filter(r => r[1] >= 3).map(r => r[0]).sort().join(',');
    if (sig && sig !== lastCrit) beep();
    lastCrit = sig;

    document.title = (n.crit ? `(!) ${n.crit} CRIT — ` : n.warn ? `(!) ${n.warn} WARN — ` : '') + 'Airsent — ASC Monitor';

    $('ascTopState').textContent = a ? String(a.state || '--').toUpperCase() : '--';
    const set = (id, val, bad) => { const e = $(id); e.textContent = val; e.className = bad ? 'bad' : (val === '--' ? 'dim' : ''); };
    set('ascRate', a && a.checks_per_s ? String(Math.round(a.checks_per_s)) : '--');
    set('ascCount', a ? String(a.checks != null ? a.checks : live.length) : '--');
    set('ascRec', a && a.session ? String(a.session).slice(-17) : 'not recording', !(a && a.session));
    set('ascFlying', a && a.flight ? mmss(a.flight.flying_s) : '--');
    set('ascWorst', worst < 0 ? '--' : (LV[worst] || LV['-1'])[1], worst >= 2);
    set('ascFresh', fresh ? 'LIVE' : 'STALE', !fresh);

    // ── grouped checks ──────────────────────────────────────────────────────
    const groups = [];
    defs.forEach(d => {
      const [id, group] = d;
      let g = groups.find(x => x.name === group);
      if (!g) groups.push(g = { name: group, rows: [] });
      g.rows.push(d);
    });
    let html = '';
    groups.forEach(g => {
      const rows = g.rows.filter(d => {
        const r = by[d[0]];
        const lvl = r ? r[1] : -1;
        return !(hideIdle.checked && lvl < 0);
      });
      if (!rows.length) return;
      html += `<section class="asc-group"><h2>${esc(g.name)}</h2><div class="asc-rows">`;
      rows.forEach(d => {
        const [id, , label, unit, note, when] = d;
        const r = by[id];
        const lvl = r ? r[1] : -1;
        const cls = (LV[lvl] || LV['-1'])[0];
        const msg = r && r[3];
        const held = since[id] ? ` · ${Math.round((now - since[id]) / 1000)}s` : '';
        html += `<div class="asc-row lvl-${cls}">
            <span class="asc-dot"></span>
            <span class="asc-name"><b>${esc(label)}</b><i>${esc(note || WHEN[when] || '')}</i>${
              msg ? `<u>${esc(msg)}${held}</u>` : ''}</span>
            <span class="asc-val">${fmt(r && r[2], unit, lvl)}${spark(id, lvl)}</span>
          </div>`;
      });
      html += '</div></section>';
    });
    $('ascGroups').innerHTML = html || '<div class="asc-empty" style="margin:22px">Waiting for ASC check definitions from the aircraft…</div>';

    // ── event log ───────────────────────────────────────────────────────────
    const evs = ((a && a.events) || []).slice();
    const start = a && a.flight && a.flight.since ? a.flight.since * 1000 : null;
    if (!evs.length) {
      $('ascEvents').innerHTML = '<div class="asc-empty">No events yet.</div>';
    } else {
      $('ascEvents').innerHTML = evs.map(e => {
        const lvl = e.level >= 3 ? 'crit' : e.level === 2 ? 'warn' : 'info';
        const wall = start ? new Date(start + e.t * 1000) : null;
        const stamp = wall ? wall.toTimeString().slice(0, 8) : 'T+' + mmss(e.t);
        return `<div class="asc-ev ev-${lvl}"><time>${esc(stamp)}</time><p>${esc(e.msg)}</p></div>`;
      }).join('');
    }
  }

  render();
  setInterval(render, 500);
})();
