/* network-panel.js — click NETWORK / DATA LINK on the dashboard: which link the drone is
   using (WiFi first, cellular fallback), both links' health, cellular data used, video
   on-demand state, and a ground-test override (AUTO / WIFI / CELLULAR).
   Reads window.airsentMission.netlink (sent by the Jetson inside telemetry). */
(() => {
  'use strict';
  const M = () => window.airsentMission;
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  let panel = null;

  function css() {
    if (document.getElementById('netPanelCss')) return;
    const st = document.createElement('style');
    st.id = 'netPanelCss';
    st.textContent = `
.net-panel{position:fixed;top:0;right:0;bottom:0;width:min(440px,96vw);z-index:10040;background:#060d14;border-left:1px solid rgba(0,180,255,.25);
  box-shadow:-14px 0 40px rgba(0,0,0,.7);transform:translateX(105%);transition:transform .2s ease;font-family:'Share Tech Mono',monospace;color:#cfe3f2;
  display:flex;flex-direction:column}
.net-panel.open{transform:none}
.net-panel header{display:flex;align-items:center;padding:14px 16px;border-bottom:1px solid rgba(0,180,255,.15)}
.net-panel header b{font:700 13px Rajdhani,sans-serif;letter-spacing:2px;color:#8fa3b5}
.net-panel header button{margin-left:auto;background:none;border:0;color:#8fa3b5;font-size:22px;cursor:pointer}
.net-panel .body{flex:1;overflow-y:auto;padding:12px 16px 20px;scrollbar-width:none}
.net-panel .body::-webkit-scrollbar{display:none}
.net-panel .big{font:700 28px Rajdhani,sans-serif;letter-spacing:2px;margin:4px 0 2px}
.net-panel .big.wifi{color:#00e383}.net-panel .big.cellular{color:#ffb800}.net-panel .big.none{color:#ff5c7a}
.net-panel .sub{font-size:11px;color:#7f9bb1;margin-bottom:12px}
.net-panel .modes{display:flex;gap:6px;margin:6px 0 14px}
.net-panel .modes button{flex:1;height:28px;background:#0b1620;color:#9be5ff;border:1px solid rgba(127,220,255,.25);font:600 11px 'Share Tech Mono',monospace;letter-spacing:1px;cursor:pointer}
.net-panel .modes button.on{background:#123247;border-color:#9be5ff;color:#fff}
.net-panel h4{margin:14px 0 6px;font:600 10px 'Share Tech Mono',monospace;letter-spacing:2px;color:#7f9bb1}
.net-panel .row{display:flex;justify-content:space-between;gap:10px;padding:4px 0;font-size:12px;border-bottom:1px solid rgba(255,255,255,.04)}
.net-panel .row span{color:#8fa3b5}.net-panel .row b{font-weight:400;color:#e8f4ff;text-align:right}
.net-panel .ok{color:#00e383!important}.net-panel .bad{color:#ff5c7a!important}.net-panel .warn{color:#ffb800!important}
.net-panel .bar{height:6px;background:#0f1d29;margin:6px 0 2px}.net-panel .bar i{display:block;height:100%;background:#3fa9d8}
.net-panel .note{font-size:10px;color:#5d7a92;margin-top:10px;line-height:1.5}
#networkSignalState,#cellularSignalVal{cursor:pointer}`;
    document.head.append(st);
  }

  function open() {
    css();
    if (!panel) {
      panel = document.createElement('aside');
      panel.className = 'net-panel';
      panel.innerHTML = '<header><b>NETWORK · DATA LINK</b><button type="button" aria-label="Close">×</button></header><div class="body"></div>';
      panel.querySelector('header button').onclick = () => panel.classList.remove('open');
      panel.addEventListener('click', async e => {
        const b = e.target.closest('[data-prefer]');
        if (!b || !M()) return;
        const mode = b.dataset.prefer;
        if (mode !== 'auto' && !confirm(`Force the drone onto ${mode.toUpperCase()} only? This is for ground testing — set it back to AUTO before flying.`)) return;
        const r = await M().send('net_prefer', { mode });
        const ui = window.airsentMissionUI;
        if (ui) ui.toast((r.ok ? '✓ ' : '') + r.msg, r.ok ? '' : 'alert'); else alert(r.msg);
      });
      document.body.append(panel);
      addEventListener('keydown', e => { if (e.key === 'Escape') panel.classList.remove('open'); });
    }
    panel.classList.toggle('open');
    render();
  }

  const tech = t => String(t || '').split(',').map(x => ({ '5gnr': '5G', 'lte': '4G LTE', 'umts': '3G', 'hspa': '3G' }[x.trim().toLowerCase()] || x.trim().toUpperCase())).filter(Boolean).join(' + ');
  const ms = v => v == null ? '--' : `${Math.round(v)} ms`;
  const yes = (v, a = 'ANSWERING', b = 'NOT ANSWERING') => v ? `<b class="ok">${a}</b>` : `<b class="bad">${b}</b>`;
  const ago = t => { if (!t) return ''; const s = Math.round(Date.now() / 1000 - t); return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`; };

  function render() {
    if (!panel || !panel.classList.contains('open')) return;
    const n = (M() && M().netlink) || null, body = panel.querySelector('.body');
    if (!n) { body.innerHTML = '<div class="sub">No network report from the Jetson yet.</div>'; return; }
    if (!n.ok) {
      body.innerHTML = `<div class="big none">NO LINK MANAGER</div><div class="sub">The Jetson's airsent-netlink service is not running, so there is no automatic
        WiFi → cellular fallback. Install it with <b>bash deploy/deploy_jetson.sh</b>.</div>`;
      return;
    }
    const w = n.wifi || {}, c = n.cellular || {}, d = n.data || {}, v = n.video || {}, ls = n.last_switch;
    const act = n.active || 'none';
    const label = act === 'wifi' ? 'WIFI' : act === 'cellular' ? 'CELLULAR' : 'NO LINK';
    const pct = Math.min(100, d.plan_pct || 0);
    const vid = (on, kbps) => !on ? '<b>PAUSED · nobody watching</b>' : kbps ? `<b class="ok">STREAMING · ${kbps} kbps</b>` : '<b>STARTING…</b>';
    body.innerHTML = `
      <div class="big ${act}">${label}</div>
      <div class="sub">${n.prefer === 'auto' ? 'AUTO — WiFi first, cellular when WiFi stops reaching the server' : `FORCED ${String(n.prefer).toUpperCase()} — ground testing only`}
        · ${n.switches || 0} switch${n.switches === 1 ? '' : 'es'}</div>
      <div class="modes">${['auto', 'wifi', 'cellular'].map(m => `<button type="button" data-prefer="${m}" class="${n.prefer === m ? 'on' : ''}">${m.toUpperCase()}</button>`).join('')}</div>
      ${act === 'cellular' && n.back_to_wifi_s > 10 ? `<div class="note" style="color:#ffb800">WiFi has been unstable — it must stay good for ${n.back_to_wifi_s} s before the drone goes back to it.</div>` : ''}
      <h4>WIFI${w.iface ? ' · ' + esc(w.iface) : ''}</h4>
      ${w.iface ? `<div class="row"><span>Server</span>${yes(w.answering)}</div>
        <div class="row"><span>Round trip</span><b>${ms(w.rtt_ms)}</b></div>
        <div class="row"><span>Loss (30 s)</span><b class="${(w.loss_pct || 0) > 10 ? 'warn' : ''}">${w.loss_pct ?? '--'}%</b></div>`
        : '<div class="row"><span>Status</span><b class="bad">NOT CONNECTED</b></div>'}
      <h4>CELLULAR${c.iface ? ' · ' + esc(c.iface) : ''}</h4>
      ${c.iface ? `<div class="row"><span>Server</span>${yes(c.answering)}</div>
        <div class="row"><span>Round trip</span><b>${ms(c.rtt_ms)}</b></div>
        <div class="row"><span>Signal</span><b>${c.percent != null ? c.percent + '%' : '--'}${c.rsrp != null ? ` · RSRP ${Math.round(c.rsrp)} dBm` : ''}${c.sinr != null ? ` · SINR ${Math.round(c.sinr)} dB` : ''}</b></div>
        ${c.lte_rsrp != null ? `<div class="row"><span>LTE anchor</span><b>RSRP ${Math.round(c.lte_rsrp)} dBm</b></div>` : ''}
        <div class="row"><span>Network</span><b>${esc([c.operator, tech(c.technology)].filter(Boolean).join(' · ') || '--')}</b></div>`
        : '<div class="row"><span>Status</span><b class="bad">NO MODEM</b></div>'}
      <h4>CELLULAR DATA</h4>
      <div class="row"><span>Today</span><b>${d.today_mb ?? 0} MB</b></div>
      <div class="row"><span>Left</span><b class="${pct > 90 ? 'bad' : pct > 70 ? 'warn' : 'ok'}">${d.left_gb != null ? d.left_gb.toFixed(2) + ' GB' : '--'}</b></div>
      <div class="row"><span>Used this cycle</span><b>${((d.cycle_mb ?? d.month_mb ?? 0) / 1000).toFixed(2)} of ${d.plan_gb ?? '--'} GB</b></div>
      <div class="bar"><i style="width:${pct}%;${pct > 90 ? 'background:#ff3355' : pct > 70 ? 'background:#ffb800' : ''}"></i></div>
      ${d.resets ? `<div class="note">Resets ${esc(d.resets)} · counted by the Jetson. To match T-Mobile: <b>sudo airsent-netlink --plan GB --bill-day D --used GB</b></div>` : ''}
      <h4>VIDEO ${v.on_demand === 'off' ? '' : v.on_demand === 'always' ? '· ON DEMAND' : '· ON DEMAND WHEN ON CELLULAR'}</h4>
      <div class="row"><span>Front camera</span>${vid(v.front, v.front_kbps)}</div>
      <div class="row"><span>Down camera</span>${vid(v.down, v.down_kbps)}</div>
      ${ls ? `<h4>LAST SWITCH</h4><div class="row"><span>${esc(String(ls.from || 'start').toUpperCase())} → ${esc(String(ls.to || '').toUpperCase())}</span><b>${ago(ls.at)}</b></div>
        <div class="note">${esc(ls.reason || '')}</div>` : ''}
      <div class="note">On a switch the Jetson reconnects telemetry, commands and video over the new link within about a second.
        On cellular, video is lower bitrate and only sent while a console shows that camera; recording to the SSD is unaffected.</div>`;
  }

  setInterval(render, 1000);
  document.addEventListener('click', e => {
    if (e.target.closest('#networkSignalState, #cellularSignalVal')) open();
  });
  window.AirsentNetworkPanel = { open };
})();
