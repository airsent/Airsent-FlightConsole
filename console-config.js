// Replace YOUR_CARTO_BASEMAP_KEY with your domain-restricted CARTO basemaps key.
// Keep the configured deployment copy separate from this public example.
// Use an absolute URL here only if the API is hosted on a different domain.
window.AIRSENT_CONFIG = {
  apiBase: location.origin,
  mapTileUrl: "https://basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}.png?key=YOUR_CARTO_BASEMAP_KEY",
  mapAttribution: "&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors &copy; <a href='https://carto.com/attributions'>CARTO</a>",
  mapMaxZoom: 20,
};
window.airsentApiUrl = function(path, websocket = false) {
  const url = new URL(path, window.AIRSENT_CONFIG.apiBase);
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
