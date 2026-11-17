/* ASC Monitor page logic, run headless against a stub DOM.
 *
 *   node tests/asc-monitor.test.js
 *
 * The page exists so somebody watching it on flight day notices a problem the moment it
 * appears. These checks are about that: the banner must tell the truth, a stale link must
 * not read as "all clear", the title must carry the alarm into a background tab, and the
 * tone must fire on a NEW critical rather than every frame.
 */
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict'), path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../asc-monitor.js'), 'utf8');

let beeps = 0, timers = [];

function el() {
  return { textContent: '', className: '', innerHTML: '', checked: false, onchange: null, style: {} };
}

function makeCtx(asc, opts = {}) {
  const els = {};
  const ctx = {
    console,
    Date, Math, Object, String, Number, isFinite, JSON,
    document: {
      title: '',
      getElementById: id => (els[id] = els[id] || el()),
    },
    localStorage: {
      store: { airsentMode: opts.mode || 'real', ascSound: '1' },
      getItem(k) { return this.store[k] === undefined ? null : this.store[k]; },
      setItem(k, v) { this.store[k] = v; },
    },
    setInterval: fn => { timers.push(fn); return timers.length; },
    AudioContext: function () {
      return {
        state: 'running', currentTime: 0, resume() {},
        createOscillator: () => ({ type: '', frequency: {}, connect() {}, start() { beeps++; }, stop() {} }),
        createGain: () => ({ gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }),
        destination: {},
      };
    },
    window: {},
  };
  ctx.window.airsentMission = {
    asc,
    ascDefs: opts.defs || [
      ['gps_sats', 'GPS', 'Satellites', '', 'warn < 10', 'session'],
      ['gps_degraded', 'GPS', 'Satellites lost in flight', '', 'warn -35%', 'armed'],
      ['vibration', 'Vibration', 'Vibration', 'm/s²', 'warn 30', 'armed'],
    ],
    stateFresh: () => opts.fresh !== false,
  };
  ctx.window.AudioContext = ctx.AudioContext;
  vm.createContext(ctx);
  ctx.els = els;
  return ctx;
}

function run(asc, opts) {
  timers = [];
  const ctx = makeCtx(asc, opts);
  vm.runInContext(source, ctx);
  return ctx;
}

const block = (live, extra = {}) => ({
  state: 'watching', checks: live.length, checks_per_s: 880, session: '2026-11-17_flight',
  live, events: [], triggered: [], flight: { flying_s: 42, since: 1700000000 }, ...extra,
});

// ── the banner tells the truth ────────────────────────────────────────────────
let c = run(block([['gps_sats', 0, 24], ['vibration', 0, 12]]));
assert.equal(c.els.ascVerdictText.textContent, 'ALL CLEAR', 'all checks ok should read ALL CLEAR');
assert.match(c.els.ascVerdict.className, /\bok\b/);
assert.equal(c.els.ascFresh.textContent, 'LIVE');

c = run(block([['gps_sats', 2, 9, 'Only 9 satellites'], ['vibration', 0, 12]]));
assert.equal(c.els.ascVerdictText.textContent, '1 WARNING');
assert.match(c.els.ascVerdictSub.textContent, /Only 9 satellites/, 'the banner must name the problem');

c = run(block([['gps_degraded', 3, 16, 'Lost 16 of 26 satellites — likely motor/ESC interference'],
               ['gps_sats', 2, 10, 'Only 10 satellites']]));
assert.equal(c.els.ascVerdictText.textContent, '1 CRITICAL');
assert.match(c.els.ascVerdict.className, /\bcrit\b/);
assert.match(c.els.ascVerdictSub.textContent, /motor\/ESC interference/);

// ── a stale link must never read as all clear ─────────────────────────────────
c = run(block([['gps_sats', 0, 24]]), { fresh: false });
assert.equal(c.els.ascVerdictText.textContent, 'NO DATA', 'stale telemetry must not look healthy');
assert.match(c.els.ascVerdictSub.textContent, /stale/);
assert.equal(c.els.ascFresh.textContent, 'STALE');

c = run(null);
assert.equal(c.els.ascVerdictText.textContent, 'NO DATA', 'no ASC block at all is NO DATA');

// ── the alarm reaches a background tab ───────────────────────────────────────
c = run(block([['gps_degraded', 3, 16, 'Lost 16 of 26 satellites']]));
assert.match(c.document.title, /^\(!\) 1 CRIT/, 'title must carry the alarm');
c = run(block([['gps_sats', 2, 9, 'Only 9']]));
assert.match(c.document.title, /^\(!\) 1 WARN/);
c = run(block([['gps_sats', 0, 24]]));
assert.equal(c.document.title, 'Airsent — ASC Monitor', 'no alarm, no prefix');

// ── the tone fires on a new critical, not on every frame ────────────────────
beeps = 0;
c = run(block([['gps_degraded', 3, 16, 'Lost 16']]));
const afterFirst = beeps;
assert.ok(afterFirst > 0, 'a new critical should sound');
timers[0]();                     // same critical still standing
assert.equal(beeps, afterFirst, 'a standing critical must not beep again');
c.window.airsentMission.asc = block([['gps_degraded', 3, 16, 'Lost 16'], ['vibration', 3, 70, 'Vibration 70']]);
timers[0]();                     // a second, different critical appears
assert.ok(beeps > afterFirst, 'a new critical alongside the old one should sound');

// ── worst-so-far survives the problem clearing ──────────────────────────────
c = run(block([['vibration', 3, 70, 'Vibration 70 m/s²']]));
assert.equal(c.els.ascWorst.textContent, 'CRITICAL');
c.window.airsentMission.asc = block([['vibration', 0, 12]]);
timers[0]();
assert.equal(c.els.ascVerdictText.textContent, 'ALL CLEAR', 'cleared means the banner clears');
assert.equal(c.els.ascWorst.textContent, 'CRITICAL', 'but it still happened, so worst-so-far holds');

// a new session starts the record over
c.window.airsentMission.asc = block([['vibration', 0, 12]], { session: '2026-11-18_flight' });
timers[0]();
assert.equal(c.els.ascWorst.textContent, 'OK', 'a new flight is judged on its own');

// ── rows render, grouped, with the message and the unit ─────────────────────
c = run(block([['gps_sats', 2, 9, 'Only 9 satellites'], ['vibration', 0, 31.5]]));
const html = c.els.ascGroups.innerHTML;
assert.match(html, /<h2>GPS<\/h2>/, 'checks are grouped');
assert.match(html, /Only 9 satellites/, 'the warning message is shown on the row');
assert.match(html, /lvl-warn/);
assert.match(html, /31\.5<small>m\/s²<\/small>/, 'value and unit');

// ── not-active rows can be hidden ───────────────────────────────────────────
c = run(block([['gps_sats', 0, 24], ['gps_degraded', -1, null], ['vibration', -1, null]]));
assert.match(c.els.ascGroups.innerHTML, /lvl-na/, 'shown by default');
c.els.ascHideIdle.checked = true;
timers[0]();
assert.doesNotMatch(c.els.ascGroups.innerHTML, /lvl-na/, 'hidden when asked');
assert.match(c.els.ascGroups.innerHTML, /Satellites/, 'the active ones stay');

// ── the event log uses wall-clock time from the session start ───────────────
c = run(block([['gps_sats', 0, 24]], {
  events: [{ seq: 2, t: 12.5, level: 3, msg: 'Lost 16 of 26 satellites' }, { seq: 1, t: 0.4, level: 1, msg: 'ASC recording' }],
}));
assert.match(c.els.ascEvents.innerHTML, /ev-crit/);
assert.match(c.els.ascEvents.innerHTML, /Lost 16 of 26 satellites/);
assert.match(c.els.ascEvents.innerHTML, /<time>\d\d:\d\d:\d\d<\/time>/, 'wall-clock stamps, not just T+');

console.log('Passed ASC monitor checks: banner truth, stale link, title alarm, tone on new criticals only, '
  + 'worst-so-far across clearing and sessions, grouped rows, hide-not-active, event log stamps');
