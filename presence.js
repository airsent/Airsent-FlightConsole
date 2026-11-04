/* One live user per browser, even with several console tabs open. */
(() => {
  let mode = () => (localStorage.getItem('airsentMode') || 'demo').toLowerCase();
  const role = () => ['operator','actual','real'].includes(mode()) ? 'operator' : mode() === 'viewer' ? 'viewer' : 'demo';
  if (role() === 'demo' || window.airsentPresenceStarted) return;
  window.airsentPresenceStarted = true;
  let clientId = localStorage.getItem('airsentPresenceId');
  if (!clientId) {
    if (typeof crypto.randomUUID === 'function') clientId = crypto.randomUUID();
    else {
      const bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      clientId = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
    }
    localStorage.setItem('airsentPresenceId', clientId);
  }
  if (!document.getElementById('consoleUsersTotal')) {
    const counter = document.createElement('aside');
    counter.className = 'presence-counter';
    counter.setAttribute('aria-label', 'Live console users');
    counter.innerHTML = 'CONSOLE: <b id="consoleUsersTotal">--</b> USERS <span>OP <b id="consoleOperators">--</b></span> <span>VIEW <b id="consoleViewers">--</b></span>';
    document.body.append(counter);
    const style = document.createElement('style');
    style.textContent = '.presence-counter{position:fixed;right:12px;bottom:8px;z-index:80;display:flex;align-items:center;gap:7px;padding:7px 10px;background:#08131c;border:1px solid #244351;color:#a5bac7;font:10px Arial,sans-serif;letter-spacing:.3px;box-shadow:0 2px 10px #0008}.presence-counter b{color:#e3edf5;font-variant-numeric:tabular-nums}.presence-counter span{margin-left:4px}';
    document.head.append(style);
  }
  const nodes = ['consoleUsersTotal','consoleOperators','consoleViewers'].map(id=>document.getElementById(id));
  function render(data) {
    nodes.forEach((node,i) => { if (node) { node.textContent = data ? data[['total','operators','viewers'][i]] : '--'; node.title = data ? 'Live connected browser sessions' : 'Presence connection offline'; } });
  }
  let ws, heartbeat, reconnect, stopped = false, lastSeen = 0;
  function send() {
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type:'presence', token:localStorage.getItem('airsentPresenceToken') || '', clientId, page:location.pathname.split('/').pop()}));
  }
  function connect() {
    if (stopped) return;
    const url = new URL('/presence', window.AIRSENT_CONFIG?.apiBase || location.origin);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    const current = ws = new WebSocket(url);
    lastSeen = Date.now();
    current.onopen = () => {
      if (current !== ws) return;
      send();
      heartbeat = setInterval(() => {
        if (Date.now() - lastSeen > 15000) { render(null); current.close(); }
        else send();
      }, 5000);
    };
    current.onmessage = event => {
      if (current !== ws) return;
      try { const data = JSON.parse(event.data); if (data.type === 'presence_login_required') {
        render(null);
        nodes.forEach(node => { if (node) node.title = 'Sign in again to verify this device role'; });
        window.dispatchEvent(new CustomEvent('airsent-presence-registration', {detail:{role:'SIGN IN REQUIRED', device:'--'}}));
        if (!document.getElementById('presenceSignIn')) {
          const link = document.createElement('a'); link.id='presenceSignIn'; link.href='index.html'; link.textContent='VERIFY LOGIN';
          link.style.cssText='color:#ffbd59;margin-left:6px;font-size:10px'; nodes[0]?.parentElement.append(link);
        }
      }
      if (data.type === 'presence_registered') {
        document.getElementById('presenceSignIn')?.remove();
        if (localStorage.getItem('airsentMode') !== data.role) localStorage.setItem('airsentMode', data.role);
        window.dispatchEvent(new CustomEvent('airsent-presence-registration', {detail:data}));
      } if (data.type === 'presence_counts') { lastSeen = Date.now(); render(data); } } catch (_) {}
    };
    current.onclose = () => {
      if (current !== ws) return;
      clearInterval(heartbeat); render(null);
      if (!stopped) reconnect = setTimeout(connect, 2000);
    };
    current.onerror = () => current.close();
    setTimeout(() => { if (current === ws && current.readyState === WebSocket.CONNECTING) current.close(); }, 8000);
  }
  window.addEventListener('storage', e => { if (e.key === 'airsentMode' || e.key === 'airsentPresenceToken') { if (role() === 'demo') { stopped=true; ws?.close(); render(null); } else send(); } });
  window.addEventListener('pagehide', () => { stopped=true; clearInterval(heartbeat); clearTimeout(reconnect); ws?.close(); });
  window.addEventListener('pageshow', e => { if (e.persisted) { stopped=false; connect(); } });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      if (!ws || ws.readyState === WebSocket.CLOSED) { clearTimeout(reconnect); stopped=false; connect(); }
      else send();
    }
  });
  render(null); connect();
})();
