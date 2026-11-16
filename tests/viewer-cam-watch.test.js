/* On cellular the Jetson streams a camera only while a console says it is showing one
   (cam_watch). Viewers never opened the command socket, so a viewer console could never
   ask — the feed sat on OFFLINE for ever while the camera itself reported nominal. */
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function console_(role, { visibleCams = ['front'] } = {}) {
  const store = new Map([['airsentMode', role]]);
  const sockets = [];
  const timers = [];
  class WS {
    static OPEN = 1; static CLOSED = 3;
    constructor(url) { this.url = url; this.readyState = 1; this.sent = []; sockets.push(this); }
    send(s) { this.sent.push(JSON.parse(s)); }
    close() { this.readyState = 3; }
  }
  const camEl = cams => ({
    dataset: { camWatch: cams },
    getBoundingClientRect: () => ({ width: 320, height: 180 }),
  });
  const ctx = {
    window: { addEventListener() {}, dispatchEvent() {} },
    document: {
      hidden: false,
      addEventListener() {},
      querySelectorAll: sel => (sel === '[data-cam-watch]' && visibleCams.length ? [camEl(visibleCams.join(' '))] : []),
    },
    getComputedStyle: () => ({ visibility: 'visible' }),
    localStorage: { getItem: k => store.get(k) || null, setItem: (k, v) => store.set(k, v), removeItem: k => store.delete(k) },
    sessionStorage: { getItem: () => 'console-1', setItem() {} },
    location: { origin: 'https://console.airsent.tech', pathname: '/dashboard.html' },
    performance: { now: () => Date.now() },
    crypto: { randomUUID: () => 'abc' },
    URL, WebSocket: WS, Date, JSON, Math, Set, Map, console,
    setInterval: fn => { timers.push(fn); return timers.length; },
    clearInterval() {}, setTimeout() {}, clearTimeout() {},
  };
  ctx.window = ctx;
  vm.runInNewContext(fs.readFileSync('mission-client.js', 'utf8'), ctx);
  return { api: ctx.window.airsentMission, sockets, tick: () => timers.forEach(fn => fn()) };
}

// A viewer opens the command socket and asks for the camera it is showing.
const viewer = console_('viewer');
const cmd = viewer.sockets.find(s => s.url.includes('/cmd'));
assert.ok(cmd, 'viewer must open the command socket so it can ask for video');
viewer.tick();
const watch = cmd.sent.find(m => m.action === 'cam_watch');
assert.ok(watch, 'viewer never sent cam_watch — the feed would stay offline on cellular');
assert.deepEqual(watch.cams, ['front']);
console.log('PASS: a viewer asks the Jetson for the camera on its screen');

// ...but it is still read only.
assert.equal(viewer.api.canCommand, false);
viewer.api.send('arm').then(r => {
  assert.equal(r.ok, false);
  assert.match(r.msg, /read only/i);
  assert.equal(cmd.sent.filter(m => m.action === 'arm').length, 0, 'a viewer must not reach the aircraft');
  console.log('PASS: a viewer still cannot command the aircraft');
});

// Nothing on screen: no pointless traffic.
const idle = console_('viewer', { visibleCams: [] });
idle.tick();
assert.equal(idle.sockets.find(s => s.url.includes('/cmd')).sent.filter(m => m.action === 'cam_watch').length, 0);
console.log('PASS: no camera on screen, nothing asked for');

// Operators keep working as before.
const op = console_('operator');
op.tick();
assert.ok(op.sockets.find(s => s.url.includes('/cmd')).sent.some(m => m.action === 'cam_watch'));
assert.equal(op.api.canCommand, true);
console.log('PASS: operator unchanged');
