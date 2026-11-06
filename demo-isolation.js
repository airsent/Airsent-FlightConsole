// Demo may use local sample data and public map tiles, never aircraft transports.
(() => {
  if (window.airsentDemoIsolation) return;
  const role = () => (new URLSearchParams(location.search).get('trial') === 'demo' ? 'demo' : localStorage.getItem('airsentMode') || 'demo').toLowerCase();
  const initialRole = role();
  const sockets = new Set(), peers = new Set();
  const aircraft = input => {
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
    return /^(\/simulation)?\/(telem|cmd)(\/|$)/.test(url.pathname) || /^\/video(\/|$)/.test(url.pathname) || ['8765','8766','8767','8768'].includes(url.port);
  };
  const allowed = input => role() !== 'demo' || !aircraft(input);
  window.airsentDemoIsolation = {isDemo:() => role() === 'demo', allowed};
  if (window.WebSocket) {
    const Native = window.WebSocket;
    window.WebSocket = class extends Native {
      constructor(url, protocols) {
        if (!allowed(url)) throw new Error('Demo mode blocks aircraft connections');
        super(url, protocols); sockets.add(this);
        this.addEventListener('close', () => sockets.delete(this));
      }
      send(data) {
        if (!allowed(this.url)) { this.close(); return; }
        return super.send(data);
      }
    };
  }
  if (window.fetch) {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, options) => allowed(input) ? nativeFetch(input, options) : Promise.reject(new Error('Demo mode blocks aircraft connections'));
  }
  if (window.RTCPeerConnection) {
    const Native = window.RTCPeerConnection;
    window.RTCPeerConnection = class extends Native {
      constructor(...args) {
        if (role() === 'demo') throw new Error('Demo mode blocks live cameras');
        super(...args); peers.add(this);
      }
      close() { peers.delete(this); return super.close(); }
    };
  }
  document.addEventListener('DOMContentLoaded', () => {
    if (role() !== 'demo') return;
    const badge = document.getElementById('telemBadge');
    if (badge) { badge.textContent = 'DEMO · NO DRONE LINK'; badge.classList.remove('live'); }
  });
  // Role constants in an existing operator tab must not outlive a login change.
  window.addEventListener('storage', event => {
    if (event.key !== 'airsentMode' && event.key !== null) return;
    if (role() === initialRole) return;
    sockets.forEach(socket => socket.close());
    peers.forEach(peer => peer.close());
    location.reload();
  });
})();
