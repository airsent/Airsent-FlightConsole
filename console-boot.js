/* Console boot screen.
 *
 * Shown over the dashboard from first paint until the console is actually usable, so the
 * hand-off from the sign-in page is continuous: same field, same mark, same type. The A is
 * the meter — it charges along its climb as each step completes.
 *
 * The steps are real, not a timer:
 *   session    the signed-in role, from storage
 *   console    window load (scripts, styles, images)
 *   map        the first Leaflet map registers (airsent-map, dispatched by airsentMapLayer)
 *   telemetry  the /telem socket opens and the first fresh frame lands (telemetry.js calls mark)
 *
 * It never traps anyone: telemetry that does not arrive is reported offline and the console
 * opens anyway, and a hard timer clears the overlay whatever happens.
 *
 * Loaded as the first element of <body> so it paints before the console does.
 */
(() => {
  'use strict';
  if (window.airsentBoot) return;

  const params = new URLSearchParams(location.search);
  const fresh = params.has('boot') || params.get('login') === 'current' || params.get('simulation') === '1'
    || !sessionStorage.getItem('airsentBooted');
  const marks = Object.create(null);
  // Something may mark a step before the overlay exists (or when it is skipped); keep a no-op that records.
  window.airsentBoot = { mark: name => { marks[name] = true; }, done: () => {} };
  if (!fresh) return;

  const HARD_STOP = 9000;       // the console opens by now whatever happened
  const MIN_SHOW = 1200;        // everything can be ready at once; do not flash the screen
  const TELEM_WAIT = 5000;      // after this, telemetry is called offline and boot carries on
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const role = (localStorage.getItem('airsentMode') || '').toLowerCase();
  const demo = role === 'demo';
  const roleName = { operator: 'Operator', viewer: 'Viewer', simulation: 'Simulator', demo: 'Demo' }[role] || 'Console';

  const STEPS = [
    { key: 'session', label: 'Session' },
    { key: 'console', label: 'Console' },
    { key: 'map', label: 'Map' },
    { key: 'telemetry', label: demo ? 'Demo data' : 'Telemetry' },
  ];
  const state = {};
  STEPS.forEach(s => { state[s.key] = { done: false, note: 'waiting' }; });

  const css = `
  @font-face { font-family: "SairaBoot"; src: url("vendor/saira/saira-latin-wdth-normal.woff2") format("woff2-variations");
               font-weight: 100 900; font-stretch: 50% 125%; font-display: swap; }
  #airsentBoot { position: fixed; inset: 0; z-index: 2147483000; display: grid; place-items: center;
    background: radial-gradient(90vh 80vh at 50% 42%, #050e1e 0%, rgba(5,14,30,.5) 45%, #01040c 78%), #01040c;
    color: #eaf0f9; font-family: "SairaBoot", "Helvetica Neue", Arial, sans-serif; font-stretch: 104%;
    -webkit-font-smoothing: antialiased; transition: opacity .55s ease; }
  #airsentBoot.gone { opacity: 0; pointer-events: none; }
  #airsentBoot .wrap { width: min(86vw, 420px); }
  #airsentBoot svg.mark { width: 100%; height: auto; display: block; overflow: visible; }
  #airsentBoot .outline { fill: none; stroke: rgba(126,170,224,.38); stroke-width: .6; }
  #airsentBoot .litedge { fill: none; stroke: rgba(196,224,255,.8); stroke-width: .9; }
  #airsentBoot .rows { margin-top: clamp(26px, 5vh, 40px); }
  #airsentBoot .row { display: flex; justify-content: space-between; align-items: baseline; gap: 16px;
    padding: 7px 0; border-bottom: 1px solid rgba(234,240,249,.08); font-size: 14px; }
  #airsentBoot .row:last-child { border-bottom: 0; }
  #airsentBoot .row b { font-weight: 500; color: rgba(234,240,249,.55); }
  #airsentBoot .row span { color: rgba(234,240,249,.38); font-variant-numeric: tabular-nums; }
  #airsentBoot .row.done b { color: #eaf0f9; }
  #airsentBoot .row.done span { color: #35e59b; }
  #airsentBoot .row.warn span { color: #ffb35c; }
  #airsentBoot .head { font-size: 13px; color: rgba(234,240,249,.5); margin-top: 18px; }
  @media (prefers-reduced-motion: reduce) { #airsentBoot { transition: none; } }`;

  const A_PATH = 'M0.4 79 L120.3 1.2 Q121.6 0 123.6 0 L135 0 A9 9 0 0 1 144 9 L144 79 L124 79 L124 20.2 L33 79 Z';
  const CLIMB = 'M0.4 79 L120.3 1.2';
  const SPAN = 86;              // the mark's half-extent along the climb axis, in glyph units

  const host = document.createElement('div');
  host.id = 'airsentBoot';
  host.setAttribute('role', 'status');
  host.setAttribute('aria-live', 'polite');
  host.innerHTML = `<style>${css}</style>
    <div class="wrap">
      <svg class="mark" viewBox="0 0 145 80" fill="none" aria-hidden="true">
        <defs>
          <path id="bootA" d="${A_PATH}"/>
          <clipPath id="bootClip"><use href="#bootA"/></clipPath>
          <linearGradient id="bootCharge" x1="0" y1="80" x2="145" y2="0" gradientUnits="userSpaceOnUse">
            <stop offset="0" stop-color="#11284a"/><stop offset="1" stop-color="#2b5fa0"/>
          </linearGradient>
        </defs>
        <use href="#bootA" class="outline"/>
        <g clip-path="url(#bootClip)">
          <g transform="rotate(-32.9 72.5 40)">
            <rect id="bootFill" x="${-SPAN - 4}" y="-190" width="0" height="420" fill="url(#bootCharge)"/>
            <rect id="bootFront" x="${-SPAN - 4}" y="-190" width="2.5" height="420" fill="#cfe4ff" opacity=".85"/>
          </g>
        </g>
        <path class="litedge" id="bootLit" d="${CLIMB}" stroke-dasharray="143" stroke-dashoffset="143"/>
      </svg>
      <div class="rows">${STEPS.map(s => `<div class="row" id="bootRow-${s.key}"><b>${s.label}</b><span>waiting</span></div>`).join('')}</div>
      <div class="head" id="bootHead">Opening the ${roleName.toLowerCase()} console</div>
    </div>`;
  // The dashboard scales <body> to fit the screen (body.console-fit), and a transformed ancestor
  // makes position:fixed resolve against that box, so the overlay lives outside <body>.
  document.documentElement.appendChild(host);

  const $ = id => host.querySelector('#' + id);
  const fill = $('bootFill'), front = $('bootFront'), lit = $('bootLit'), head = $('bootHead');

  const bornAt = Date.now();
  let removed = false;
  function remove() {
    if (removed) return;
    const early = MIN_SHOW - (Date.now() - bornAt);
    if (early > 0) { setTimeout(remove, early); return; }
    removed = true;
    sessionStorage.setItem('airsentBooted', '1');
    host.classList.add('gone');
    setTimeout(() => host.remove(), calm ? 0 : 600);
  }
  const hardStop = setTimeout(remove, HARD_STOP);

  function paint(step, done, note) {
    const s = state[step];
    if (!s || s.done) return;
    if (done) s.done = true;
    s.note = note;
    const row = $('bootRow-' + step);
    if (!row) return;
    row.className = 'row' + (done ? ' done' : '') + (note === 'offline' ? ' warn' : '');
    row.lastElementChild.textContent = note;
    check();
  }

  // the A charges as steps complete; the lit edge draws in step with it
  let shown = 0, target = 0, raf = 0;
  function advance() {
    target = STEPS.filter(s => state[s.key].done).length / STEPS.length;
    if (calm) { shown = target; apply(); return; }
    if (!raf) raf = requestAnimationFrame(tick);
  }
  function tick() {
    shown += (target - shown) * 0.12;
    if (Math.abs(target - shown) < 0.002) { shown = target; raf = 0; } else raf = requestAnimationFrame(tick);
    apply();
  }
  function apply() {
    const w = shown * (SPAN * 2 + 8);
    fill.setAttribute('width', w.toFixed(2));
    front.setAttribute('x', (-SPAN - 4 + w - 1.2).toFixed(2));
    front.setAttribute('opacity', shown > 0 && shown < 1 ? '.85' : '0');
    lit.setAttribute('stroke-dashoffset', (143 * (1 - shown)).toFixed(1));
  }

  function check() {
    const left = STEPS.filter(s => !state[s.key].done);
    advance();
    head.textContent = left.length ? `Opening the ${roleName.toLowerCase()} console` : 'Console ready';
    if (!left.length) { clearTimeout(hardStop); setTimeout(remove, calm ? 120 : 520); }
  }

  // ── the real signals ──────────────────────────────────────────────────────
  window.airsentBoot = {
    mark(name, note) {
      marks[name] = true;
      if (name === 'link') { if (!state.telemetry.done) paint('telemetry', false, 'connected'); return; }
      paint(name, true, note || 'ready');
    },
    done: remove,
  };

  paint('session', !!role, role ? roleName : 'none');
  if (document.readyState === 'complete') paint('console', true, 'ready');
  else addEventListener('load', () => paint('console', true, 'ready'), { once: true });
  addEventListener('airsent-map', () => paint('map', true, 'ready'), { once: true });
  if (demo) setTimeout(() => paint('telemetry', true, 'simulated'), 500);
  Object.keys(marks).forEach(k => window.airsentBoot.mark(k));   // anything that fired before this ran

  // a map that never arrives should not hold the console (no map on this page, tiles blocked)
  setTimeout(() => paint('map', true, 'ready'), 4000);
  setTimeout(() => { if (!state.telemetry.done) paint('telemetry', true, 'offline'); }, TELEM_WAIT);
})();
