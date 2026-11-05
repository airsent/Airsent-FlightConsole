// Replace YOUR_CARTO_BASEMAP_KEY with your domain-restricted CARTO basemaps key.
// Keep the configured deployment copy separate from this public example.
// Use an absolute URL here only if the API is hosted on a different domain.
window.AIRSENT_CONFIG = {
  apiBase: location.origin,
  mapTileUrl: "https://basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}.png?key=YOUR_CARTO_BASEMAP_KEY",
  mapAttribution: "&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors &copy; <a href='https://carto.com/attributions'>CARTO</a>",
  mapMaxZoom: 20,
};
window.airsentSimulation = localStorage.getItem('airsentMode') === 'simulation' || (['localhost','127.0.0.1'].includes(location.hostname) && localStorage.getItem('airsentSITL') === '1');
if (window.airsentSimulation) {
  document.addEventListener('DOMContentLoaded', () => {
    const badge = document.createElement('div');
    badge.textContent = 'SITL · SIMULATED AIRCRAFT';
    badge.style.cssText = 'position:fixed;bottom:8px;left:8px;z-index:9999;padding:6px 10px;border:1px solid #f4b942;background:#111c26;color:#f4b942;font:11px monospace;pointer-events:none';
    document.body.append(badge);
  });
  window.AIRSENT_CONFIG.mapTileUrl = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
  window.AIRSENT_CONFIG.mapAttribution = 'Tiles &copy; Esri';
  window.AIRSENT_CONFIG.mapMaxZoom = 19;
}
window.airsentApiUrl = function(path, websocket = false) {
  const url = new URL(path, window.AIRSENT_CONFIG.apiBase);
  if (window.airsentSimulation && websocket && ['/telem','/cmd'].includes(path)) {
    if (['localhost','127.0.0.1'].includes(location.hostname)) {
      url.hostname = location.hostname; url.port = path === '/telem' ? '8765' : '8767'; url.pathname = '/';
    } else {
      url.pathname = '/simulation' + path;
      url.searchParams.set('session', localStorage.getItem('airsentPresenceToken') || '');
    }
  }
  if (websocket) url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.href;
};
window.airsentMapLayer = function(map) {
  const cfg = window.AIRSENT_CONFIG;
  const layer = L.tileLayer(cfg.mapTileUrl, {
    maxZoom: cfg.mapMaxZoom, subdomains: "abcd", attribution: cfg.mapAttribution,
  }).addTo(map);
  const notice = L.control({position: "bottomleft"});
  notice.onAdd = function() {
    const el = L.DomUtil.create("div");
    el.textContent = "Map tiles unavailable — check network or map provider configuration";
    el.style.cssText = "display:none;background:#18202b;color:#fff;padding:8px;max-width:260px;font:12px sans-serif";
    this.message = el;
    return el;
  };
  notice.addTo(map);
  layer.on("tileerror", () => { notice.message.style.display = "block"; });
  layer.on("tileload", () => { notice.message.style.display = "none"; });
  return layer;
};

// Refresh legacy cached login forms without replacing their visual layout.
if (['/','/index.html','/signin.html'].includes(location.pathname)) {
  const loadLogin = () => {
    if (window.airsentLoginController || document.querySelector('script[src*="login.js"]')) return;
    const script=document.createElement('script');script.src='login.js?v=simulation-map-2';document.body.append(script);
  };
  if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',loadLogin);
  else loadLogin();
}
window.airsentObserveMap = function(map) {
  const container=map.getContainer(); let pending=false;
  const fit=()=>{
    if(pending)return;pending=true;
    requestAnimationFrame(()=>requestAnimationFrame(()=>{
      pending=false;
      if(container.clientWidth>0 && container.clientHeight>0) map.invalidateSize({pan:true,animate:false,debounceMoveend:true});
    }));
  };
  const observer=typeof ResizeObserver==='function'?new ResizeObserver(fit):null;
  observer?.observe(container);
  window.addEventListener('resize',fit);
  window.visualViewport?.addEventListener('resize',fit);
  document.fonts?.ready.then(fit);
  map.on('unload',()=>{observer?.disconnect();window.removeEventListener('resize',fit);window.visualViewport?.removeEventListener('resize',fit);});
  fit();
  return fit;
};
