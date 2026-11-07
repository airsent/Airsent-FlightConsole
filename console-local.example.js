// Copy to console-local.js ON THE SERVER (next to console-config.js) and put the real key in.
// console-local.js is git-ignored, so deploys never overwrite it.
window.AIRSENT_LOCAL = {
  mapTileUrl: "https://basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}.png?key=YOUR_CARTO_BASEMAP_KEY",
};
