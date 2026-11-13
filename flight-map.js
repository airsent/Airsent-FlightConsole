/* Flight overlay for every Leaflet map in the console (dashboard, pilot, mission planner):
 *  - HOME marker where the aircraft was when the mission took off (set by the Jetson at takeoff)
 *  - FLIGHT 2 box plan: blue until flown, green once covered (the side being flown turns green behind the aircraft)
 *  - a banner when the home point is set
 * Driven by the mission snapshot: home_point, plan {path, sides_done, leg}, pos [lat, lon].
 * Maps register themselves through window.airsentObserveMap (console-config.js). */
(function () {
  'use strict';
  const BLUE = '#3987e5', GREEN = '#00e383';
  const layers = new WeakMap();           // map -> {group, home, sides[], live}
  let last = null;

  function ensure(map) {
    let l = layers.get(map);
    if (l && map.hasLayer(l.group)) return l;
    if (typeof L === 'undefined') return null;
    l = { group: L.layerGroup().addTo(map), home: null, sides: [], dots: [], live: null, sig: '', trail: null };
    layers.set(map, l);
    return l;
  }

  function homeIcon() {
    return L.divIcon({
      className: '', iconSize: [34, 34], iconAnchor: [17, 17],
      html: '<div style="width:30px;height:30px;border-radius:50%;background:#0b1a12;border:2px solid #00e383;color:#00e383;' +
            'font:700 11px \'Share Tech Mono\',monospace;display:flex;align-items:center;justify-content:center;' +
            'box-shadow:0 0 0 3px rgba(0,227,131,.18),0 2px 8px rgba(0,0,0,.6)">H</div>'
    });
  }

  // fraction (0..1) of the way a→b that the aircraft has covered
  function along(a, b, p) {
    const k = Math.cos(a[0] * Math.PI / 180);
    const bx = (b[1] - a[1]) * k, by = b[0] - a[0], px = (p[1] - a[1]) * k, py = p[0] - a[0];
    const len2 = bx * bx + by * by;
    return len2 ? Math.max(0, Math.min(1, (px * bx + py * by) / len2)) : 0;
  }

  function draw(map, s) {
    const l = ensure(map);
    if (!l) return;
    const hp = s && s.home_point, plan = s && s.plan;
    if (!hp) { l.group.clearLayers(); l.home = null; l.sides = []; l.dots = []; l.live = null; l.sig = ''; l.trail = null; return; }
    if (!l.home) {
      l.home = L.marker([hp.lat, hp.lon], { icon: homeIcon(), interactive: true, zIndexOffset: 900, keyboard: false })
        .bindTooltip('HOME — set at takeoff', { direction: 'top' }).addTo(l.group);
    } else l.home.setLatLng([hp.lat, hp.lon]);
    const sig = plan ? JSON.stringify(plan.path) : '';
    if (sig !== l.sig) {                                  // (re)build the box
      l.sides.forEach(x => l.group.removeLayer(x)); l.dots.forEach(x => l.group.removeLayer(x));
      l.sides = []; l.dots = []; l.sig = sig;
      if (l.live) { l.group.removeLayer(l.live); l.live = null; }
      if (plan) {
        for (let k = 0; k < 4; k++) l.sides.push(L.polyline([plan.path[k], plan.path[k + 1]], { color: BLUE, weight: 4, opacity: .95 }).addTo(l.group));
        for (let k = 1; k < 4; k++) l.dots.push(L.circleMarker(plan.path[k], { radius: 4, color: '#0b1118', weight: 1, fillColor: BLUE, fillOpacity: 1 }).addTo(l.group));
      }
    }
    // flown path: where the aircraft has actually been, from takeoff to its position now
    const pts = (s.track || []).slice();
    if (s.pos && s.pos[0] != null && ['takeoff', 'flight'].includes(s.stage)) pts.push(s.pos);
    if (pts.length > 1) {
      if (!l.trail) { l.trail = L.polyline(pts, { color: GREEN, weight: 3, opacity: .85, lineJoin: 'round' }).addTo(l.group); l.trail.bringToBack(); }
      else l.trail.setLatLngs(pts);
    }
    if (!plan) return;
    plan.path.slice(0, 4).forEach((a, k) => {
      const done = k < plan.sides_done;
      l.sides[k].setStyle({ color: done ? GREEN : BLUE });
      if (k >= 1 && l.dots[k - 1]) l.dots[k - 1].setStyle({ fillColor: k <= plan.sides_done ? GREEN : BLUE });
    });
    // the side being flown: green from its start to where the aircraft is now
    const k = plan.leg;
    if (k != null && s.pos && s.pos[0] != null) {
      const a = plan.path[k], b = plan.path[k + 1], f = along(a, b, s.pos);
      const end = [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
      if (!l.live) l.live = L.polyline([a, end], { color: GREEN, weight: 5, opacity: 1 }).addTo(l.group);
      else l.live.setLatLngs([a, end]);
    } else if (l.live) { l.group.removeLayer(l.live); l.live = null; }
  }

  function redraw() {
    (window.airsentMaps || []).forEach(m => { try { draw(m, last); } catch (e) { /* a map mid-teardown */ } });
  }

  // ── banner ───────────────────────────────────────────────────────────────
  let seenHome = 0, bannerEl = null, bannerTimer;
  function banner(text) {
    if (!bannerEl) {
      bannerEl = document.createElement('div');
      bannerEl.style.cssText = 'position:fixed;top:64px;left:50%;transform:translateX(-50%);z-index:10060;background:rgba(6,20,13,.96);' +
        'border:1px solid #00e383;border-left:5px solid #00e383;color:#d7ffe9;padding:10px 18px;font:700 13px "Share Tech Mono",monospace;' +
        'letter-spacing:1.5px;box-shadow:0 8px 28px rgba(0,0,0,.65);pointer-events:none;max-width:92vw;text-align:center';
      document.body.append(bannerEl);
    }
    bannerEl.textContent = text; bannerEl.hidden = false; bannerEl.style.display = '';
    clearTimeout(bannerTimer); bannerTimer = setTimeout(() => { bannerEl.style.display = 'none'; }, 9000);
  }
  function watchEvents(s) {
    const ev = (s.events || []).find(e => e.kind === 'home');
    if (!ev) return;
    if (!seenHome) { seenHome = ev.t; if ((s.server_time || 0) - ev.t > 20) return; }   // joined late: no banner for old news
    else if (ev.t <= seenHome) return;
    seenHome = ev.t;
    banner(ev.msg.replace(' — ', '  ·  '));
  }

  function onState(M) {
    const s = M.state;
    last = s;
    if (s) watchEvents(s);
    redraw();
  }

  function start() {
    const M = window.airsentMission;
    if (!M) return setTimeout(start, 300);
    M.on(onState);
    window.addEventListener('airsent-map', redraw);
  }
  start();
  window.airsentFlightMap = { redraw, banner };
})();
