/* Airsent flight charts — small dependency-free SVG line charts for the Logs page.
 *
 *   const c = FlightCharts.create(el, {
 *     title: 'Altitude', unit: 'm', group: 'flight',            // charts in a group share crosshair + zoom
 *     series: [{ name: 'Actual', color: '#3987e5', points: [[t, v], …] },
 *              { name: 'Target', color: '#7d8fa0', dash: true, points: … }],
 *     thresholds: [{ y: 30, label: 'limit' }], marks: [{ x: 12.5, label: 'LOITER' }],
 *     xLabel: 's from take-off', yMin: 0, height: 190,
 *   });
 *
 * Hover: crosshair + one tooltip listing every series at that x (synced across the group).
 * Drag across a chart to zoom the whole group; double-click (or RESET) to zoom out.
 * TABLE shows the plotted values as rows. Labels are inserted with textContent only.
 * Colours: the console's validated dark categorical order (blue, orange, aqua, yellow).
 */
(() => {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const PALETTE = ['#3987e5', '#d95926', '#199e70', '#c98500'];
  const MUTED = '#7d8fa0';
  const groups = new Map();            // name -> { charts:Set, zoom:[x0,x1]|null }

  const el = (tag, attrs = {}, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  };
  const h = (tag, cls, parent, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  };

  function niceTicks(lo, hi, n = 5) {
    if (!(hi > lo)) { hi = lo + 1; }
    const span = hi - lo, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || 10 * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) out.push(+v.toFixed(10));
    return { ticks: out, step };
  }
  const decimals = step => {                       // enough digits to tell neighbouring ticks apart
    if (!(step > 0)) return 2;
    const str = (+step.toPrecision(6)).toString();
    if (str.includes('e')) return Math.min(6, Math.max(0, -Math.floor(Math.log10(step))));
    return Math.min(6, (str.split('.')[1] || '').length);
  };
  const fmt = (v, step) => {
    if (v == null || !isFinite(v)) return '--';
    const d = step == null ? (Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2) : decimals(step);
    return v.toFixed(d);
  };
  function nearest(points, x) {                       // binary search on sorted x
    let lo = 0, hi = points.length - 1;
    if (hi < 0) return null;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (points[m][0] < x) lo = m; else hi = m; }
    return Math.abs(points[lo][0] - x) <= Math.abs(points[hi][0] - x) ? points[lo] : points[hi];
  }

  function injectCss() {
    if (document.getElementById('fc-css')) return;
    const s = document.createElement('style');
    s.id = 'fc-css';
    s.textContent = `
.fc { position:relative; border:1px solid rgba(0,180,255,.18); background:#060d14; padding:8px 10px 6px; }
.fc-top { display:flex; align-items:baseline; gap:8px; }
.fc-title { font:700 10px Rajdhani,sans-serif; letter-spacing:2px; color:#e8f4ff; text-transform:uppercase; }
.fc-unit { font:9px 'Share Tech Mono',monospace; color:#5d7a92; }
.fc-sp { flex:1; }
.fc-btn { background:none; border:1px solid rgba(127,220,255,.25); color:#9be5ff; font:9px 'Share Tech Mono',monospace; letter-spacing:1px; padding:1px 6px; cursor:pointer; }
.fc-btn:hover { color:#fff; border-color:#9be5ff; }
.fc-legend { display:flex; flex-wrap:wrap; gap:10px; margin-top:3px; font:10px 'Share Tech Mono',monospace; color:#9fb3c4; }
.fc-legend i { display:inline-block; width:14px; height:0; border-top:2px solid; vertical-align:middle; margin-right:5px; }
.fc-legend i.d { border-top-style:dashed; }
.fc svg { display:block; width:100%; overflow:visible; touch-action:none; user-select:none; }
.fc .ax text { font:9px 'Share Tech Mono',monospace; fill:#5d7a92; }
.fc .grid line { stroke:rgba(0,180,255,.07); }
.fc .zero { stroke:rgba(0,180,255,.22); }
.fc .thr line { stroke:#ff3355; stroke-dasharray:3 3; opacity:.6; } .fc .thr text { font:9px 'Share Tech Mono',monospace; fill:#ff8095; }
.fc .mk line { stroke:rgba(232,244,255,.18); } .fc .mk text { font:8px 'Share Tech Mono',monospace; fill:#7d8fa0; letter-spacing:.5px; }
.fc .dl { font:9px 'Share Tech Mono',monospace; fill:#c7d6e3; }
.fc .xh { stroke:rgba(232,244,255,.45); stroke-width:1; }
.fc .sel { fill:rgba(0,200,255,.12); stroke:rgba(0,200,255,.5); }
.fc-tip { position:absolute; pointer-events:none; background:#0a1520; border:1px solid rgba(0,180,255,.35); padding:5px 8px; font:10px 'Share Tech Mono',monospace;
  color:#9fb3c4; white-space:nowrap; z-index:5; display:none; box-shadow:0 4px 14px rgba(0,0,0,.5); }
.fc-tip .x { color:#5d7a92; margin-bottom:2px; } .fc-tip .r { display:flex; align-items:center; gap:6px; }
.fc-tip .r b { color:#fff; font-weight:400; min-width:44px; text-align:right; } .fc-tip .r i { width:12px; border-top:2px solid; display:inline-block; }
.fc-tip .r i.d { border-top-style:dashed; }
.fc-table { max-height:220px; overflow:auto; margin-top:6px; border-top:1px solid rgba(0,180,255,.12); }
.fc-table table { width:100%; border-collapse:collapse; font:10px 'Share Tech Mono',monospace; }
.fc-table th { position:sticky; top:0; background:#060d14; color:#5d7a92; font-weight:400; text-align:right; padding:3px 6px; }
.fc-table td { text-align:right; padding:2px 6px; color:#c7d6e3; border-bottom:1px solid rgba(0,180,255,.05); }
.fc-empty { font:10px 'Share Tech Mono',monospace; color:#5d7a92; padding:30px 0; text-align:center; }`;
    document.head.appendChild(s);
  }

  function create(root, opts) {
    injectCss();
    root.innerHTML = '';
    const o = { height: 180, xLabel: 's', group: null, ...opts };
    const series = (o.series || []).filter(s => s.points && s.points.length).map((s, i) => ({ color: PALETTE[i % PALETTE.length], ...s }));
    const box = h('div', 'fc', root);
    const top = h('div', 'fc-top', box);
    h('span', 'fc-title', top, o.title || '');
    if (o.unit) h('span', 'fc-unit', top, o.unit);
    h('span', 'fc-sp', top);
    const resetBtn = h('button', 'fc-btn', top, 'RESET ZOOM'); resetBtn.type = 'button'; resetBtn.style.display = 'none';
    const tblBtn = h('button', 'fc-btn', top, 'TABLE'); tblBtn.type = 'button';
    if (series.length >= 2) {
      const lg = h('div', 'fc-legend', box);
      series.forEach(s => { const it = h('span', '', lg); const k = h('i', s.dash ? 'd' : '', it); k.style.borderTopColor = s.color; it.appendChild(document.createTextNode(s.name)); });
    }
    if (!series.length) { h('div', 'fc-empty', box, o.empty || 'No data in this log'); return { destroy() {} }; }
    const svg = el('svg', {}, box);
    const tip = h('div', 'fc-tip', box);
    const tableBox = h('div', 'fc-table', box); tableBox.style.display = 'none';

    let xMin = Infinity, xMax = -Infinity, yLo = Infinity, yHi = -Infinity;
    series.forEach(s => s.points.forEach(([x, y]) => { if (x < xMin) xMin = x; if (x > xMax) xMax = x; }));
    const state = { zoom: null, w: 0, hoverX: null };
    const M = { l: 44, r: series.length <= 4 && o.directLabels !== false ? 62 : 12, t: 10, b: 22 };

    function yRange(x0, x1) {
      let lo = Infinity, hi = -Infinity;
      series.forEach(s => s.points.forEach(([x, y]) => { if (y == null || x < x0 || x > x1) return; if (y < lo) lo = y; if (y > hi) hi = y; }));
      (o.thresholds || []).forEach(t => { if (t.y > hi && t.y < hi * 1.6 + 1) hi = t.y; });
      if (o.yMin != null) lo = Math.min(lo, o.yMin);
      if (o.yMax != null) hi = Math.max(hi, o.yMax);
      if (!isFinite(lo)) { lo = 0; hi = 1; }
      if (hi - lo < 1e-9) { hi += 1; lo -= o.yMin != null ? 0 : 1; }
      const pad = (hi - lo) * 0.06;
      return [o.yMin != null && lo >= o.yMin ? lo : lo - pad, hi + pad];
    }

    function draw() {
      const W = box.clientWidth - 20, H = o.height;
      if (W < 50) return;
      state.w = W;
      svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
      svg.setAttribute('height', H);
      svg.innerHTML = '';
      const [x0, x1] = state.zoom || [xMin, xMax];
      [yLo, yHi] = yRange(x0, x1);
      const pw = W - M.l - M.r, ph = H - M.t - M.b;
      const sx = x => M.l + (x - x0) / ((x1 - x0) || 1) * pw;
      const sy = y => M.t + (1 - (y - yLo) / ((yHi - yLo) || 1)) * ph;
      state.sx = sx; state.sy = sy; state.x0 = x0; state.x1 = x1; state.pw = pw; state.ph = ph;
      const clip = 'fc' + Math.random().toString(36).slice(2);
      el('rect', { x: M.l, y: M.t, width: pw, height: ph }, el('clipPath', { id: clip }, el('defs', {}, svg)));
      const yt = niceTicks(yLo, yHi, Math.max(3, Math.floor(ph / 34)));
      const xt = niceTicks(x0, x1, Math.max(3, Math.floor(pw / 80)));
      state.ystep = yt.step;
      const grid = el('g', { class: 'grid' }, svg), ax = el('g', { class: 'ax' }, svg);
      yt.ticks.forEach(v => { el('line', { x1: M.l, x2: M.l + pw, y1: sy(v), y2: sy(v) }, grid); const t = el('text', { x: M.l - 6, y: sy(v) + 3, 'text-anchor': 'end' }, ax); t.textContent = fmt(v, yt.step); });
      xt.ticks.forEach(v => { const t = el('text', { x: sx(v), y: H - 6, 'text-anchor': 'middle' }, ax); t.textContent = fmt(v, xt.step) + (v === xt.ticks[xt.ticks.length - 1] && o.xLabel ? ' ' + o.xLabel : ''); });
      if (yLo < 0 && yHi > 0) el('line', { class: 'zero', x1: M.l, x2: M.l + pw, y1: sy(0), y2: sy(0) }, svg);
      const mk = el('g', { class: 'mk' }, svg);
      (o.marks || []).forEach(m => { if (m.x < x0 || m.x > x1) return; el('line', { x1: sx(m.x), x2: sx(m.x), y1: M.t, y2: M.t + ph }, mk); const t = el('text', { x: sx(m.x) + 3, y: M.t + 8 }, mk); t.textContent = m.label; });
      const thr = el('g', { class: 'thr' }, svg);
      (o.thresholds || []).forEach(t => { if (t.y < yLo || t.y > yHi) return; el('line', { x1: M.l, x2: M.l + pw, y1: sy(t.y), y2: sy(t.y) }, thr); if (t.label) { const tx = el('text', { x: M.l + pw - 2, y: sy(t.y) - 3, 'text-anchor': 'end' }, thr); tx.textContent = t.label; } });
      const lines = el('g', { 'clip-path': `url(#${clip})` }, svg);
      const ends = [];
      series.forEach(s => {
        let d = '', pen = false, last = null;
        const pts = s.points, step = Math.max(1, Math.floor(pts.length / (pw * 1.5)));
        for (let i = 0; i < pts.length; i += step) {
          const [x, y] = pts[i];
          if (y == null || x < x0 - (x1 - x0) * 0.02 || x > x1 + (x1 - x0) * 0.02) { pen = false; continue; }
          d += (pen ? 'L' : 'M') + sx(x).toFixed(1) + ' ' + sy(y).toFixed(1);
          pen = true; if (x <= x1) last = [x, y];
        }
        el('path', { d, fill: 'none', stroke: s.color, 'stroke-width': s.width || 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round',
          'stroke-dasharray': s.dash ? '5 4' : '', opacity: s.dash ? 0.85 : 1 }, lines);
        if (last) ends.push({ y: sy(last[1]), s });
      });
      if (M.r > 20) {                                            // direct labels, de-collided
        ends.sort((a, b) => a.y - b.y);
        for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 11) ends[i].y = ends[i - 1].y + 11;
        const bottom = M.t + ph;                                     // pushed past the bottom: slide the stack up
        if (ends.length && ends[ends.length - 1].y > bottom) {
          ends[ends.length - 1].y = bottom;
          for (let i = ends.length - 2; i >= 0; i--) if (ends[i + 1].y - ends[i].y < 11) ends[i].y = ends[i + 1].y - 11;
        }
        ends.forEach(e => { const t = el('text', { class: 'dl', x: M.l + pw + 5, y: e.y + 3 }, svg); t.textContent = e.s.short || e.s.name; });
      }
      state.xh = el('line', { class: 'xh', y1: M.t, y2: M.t + ph, x1: -10, x2: -10 }, svg);
      state.selRect = el('rect', { class: 'sel', y: M.t, height: ph, x: 0, width: 0, style: 'display:none' }, svg);
      el('rect', { x: M.l, y: M.t, width: pw, height: ph, fill: 'transparent', class: 'hit' }, svg);
      if (state.hoverX != null) showAt(state.hoverX, false);
      resetBtn.style.display = state.zoom ? '' : 'none';
    }

    function showAt(x, local) {
      state.hoverX = x;
      if (x == null || x < state.x0 || x > state.x1) { state.xh.setAttribute('x1', -10); state.xh.setAttribute('x2', -10); tip.style.display = 'none'; return; }
      const px = state.sx(x);
      state.xh.setAttribute('x1', px); state.xh.setAttribute('x2', px);
      if (!local) { tip.style.display = 'none'; return; }
      tip.innerHTML = '';
      h('div', 'x', tip, (o.xFmt ? o.xFmt(x) : fmt(x, 0.1) + ' ' + (o.xLabel || '')));
      series.forEach(s => {
        const p = nearest(s.points, x);
        const r = h('div', 'r', tip);
        const k = h('i', s.dash ? 'd' : '', r); k.style.borderTopColor = s.color;
        h('b', '', r, p && p[1] != null && Math.abs(p[0] - x) <= (o.maxGap || 2) ? fmt(p[1], state.ystep / 10) + (o.unit && o.unit.length < 6 ? ' ' + o.unit : '') : '--');
        h('span', '', r, s.name);
      });
      tip.style.display = 'block';
      const bw = box.clientWidth, tw = tip.offsetWidth;
      tip.style.left = Math.min(bw - tw - 4, px + 24 > bw - tw ? px - tw - 4 : px + 24) + 'px';
      tip.style.top = '30px';
    }

    const toX = ev => {
      const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (state.w / r.width);
      return state.x0 + (px - M.l) / state.pw * (state.x1 - state.x0);
    };
    let drag = null;
    svg.addEventListener('pointermove', ev => {
      const x = toX(ev);
      if (drag) {
        const a = state.sx(Math.max(state.x0, Math.min(drag, x))), b = state.sx(Math.min(state.x1, Math.max(drag, x)));
        state.selRect.setAttribute('x', a); state.selRect.setAttribute('width', Math.max(0, b - a)); state.selRect.style.display = '';
      }
      broadcast(c => c.showAt(x, c === api));
    });
    svg.addEventListener('pointerleave', () => { if (!drag) broadcast(c => c.showAt(null, false)); });
    svg.addEventListener('pointerdown', ev => { drag = toX(ev); svg.setPointerCapture(ev.pointerId); });
    svg.addEventListener('pointerup', ev => {
      if (drag == null) return;
      const x = toX(ev), a = Math.max(xMin, Math.min(drag, x)), b = Math.min(xMax, Math.max(drag, x));
      drag = null; state.selRect.style.display = 'none';
      if (b - a > (state.x1 - state.x0) * 0.02) broadcastZoom([a, b]);
    });
    svg.addEventListener('dblclick', () => broadcastZoom(null));
    resetBtn.onclick = () => broadcastZoom(null);
    tblBtn.onclick = () => {
      const on = tableBox.style.display === 'none';
      tableBox.style.display = on ? '' : 'none';
      tblBtn.textContent = on ? 'CHART ONLY' : 'TABLE';
      if (!on) return;
      tableBox.innerHTML = '';
      const t = h('table', '', tableBox), hr = h('tr', '', t);
      h('th', '', hr, o.xLabel || 'x'); series.forEach(s => h('th', '', hr, s.name + (o.unit ? ` (${o.unit})` : '')));
      const xs = series[0].points.map(p => p[0]), stride = Math.max(1, Math.ceil(xs.length / 300));
      for (let i = 0; i < xs.length; i += stride) {
        const tr = h('tr', '', t); h('td', '', tr, fmt(xs[i], 0.1));
        series.forEach(s => { const p = nearest(s.points, xs[i]); h('td', '', tr, p ? fmt(p[1], state.ystep / 10) : '--'); });
      }
    };

    function broadcast(fn) { const g = o.group && groups.get(o.group); (g ? [...g.charts] : [api]).forEach(fn); }
    function broadcastZoom(z) { const g = o.group && groups.get(o.group); if (g) g.zoom = z; broadcast(c => c.setZoom(z)); }

    const api = {
      showAt, setZoom(z) { state.zoom = z && [Math.max(xMin, z[0]), Math.min(xMax, z[1])]; if (state.zoom && state.zoom[1] - state.zoom[0] <= 0) state.zoom = null; draw(); },
      destroy() { ro.disconnect(); const g = o.group && groups.get(o.group); if (g) g.charts.delete(api); },
    };
    if (o.group) {
      if (!groups.has(o.group)) groups.set(o.group, { charts: new Set(), zoom: null });
      const g = groups.get(o.group); g.charts.add(api);
      if (g.zoom) state.zoom = g.zoom;
    }
    const ro = new ResizeObserver(() => draw());
    ro.observe(box);
    draw();
    return api;
  }

  function clearGroup(name) {
    const g = groups.get(name);
    if (g) { [...g.charts].forEach(c => c.destroy()); groups.delete(name); }
  }

  window.FlightCharts = { create, clearGroup, PALETTE, MUTED };
})();
