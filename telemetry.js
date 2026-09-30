// telemetry.js — Airsent GCS
// isRealMode declared in script.js which loads before this file

// ─── FAULT COUNTER ────────────────────────────────────────────────────────────
const _faultMap = new Map();        // key → {level, message}
const _dismissedKeys = new Set();   // keys user has dismissed (won't re-fire until condition clears)

function triggerFaultAlert(level, key, message) {
  if (_dismissedKeys.has(key)) return;
  const existing = _faultMap.get(key);
  if (existing && existing.level === level && existing.message === message) return;
  _faultMap.set(key, { level, message });
  _updateFaultCounter();
}

function clearFaultAlert(key) {
  _dismissedKeys.delete(key); // condition resolved → allow re-trigger next time
  if (!_faultMap.has(key)) return;
  _faultMap.delete(key);
  _updateFaultCounter();
}

function dismissFault(key) {
  _dismissedKeys.add(key);
  _faultMap.delete(key);
  _updateFaultCounter();
}

function dismissAllFaults(e) {
  if (e) e.stopPropagation();
  _faultMap.forEach((_, key) => _dismissedKeys.add(key));
  _faultMap.clear();
  _updateFaultCounter();
}

function toggleFaultPanel(e) {
  if (e) e.stopPropagation();
  const counter = document.getElementById('faultCounter');
  if (counter) counter.classList.toggle('open');
}

document.addEventListener('click', function(e) {
  const counter = document.getElementById('faultCounter');
  if (counter && !counter.contains(e.target)) counter.classList.remove('open');
});

function _updateFaultCounter() {
  const countEl   = document.getElementById('faultCountNum');
  const counter   = document.getElementById('faultCounter');
  const list      = document.getElementById('faultPanelList');
  const emptyEl   = document.getElementById('faultPanelEmpty');
  const dismissEl = document.getElementById('faultDismissAll');

  const count = _faultMap.size;
  if (countEl) countEl.textContent = count;

  if (counter) {
    counter.classList.remove('has-warn', 'has-fault');
    if (count > 0) {
      const hasFault = [..._faultMap.values()].some(v => v.level === 'fault');
      counter.classList.add(hasFault ? 'has-fault' : 'has-warn');
    }
  }

  if (list) {
    list.innerHTML = '';
    _faultMap.forEach(({ level, message }, key) => {
      const item = document.createElement('div');
      item.className = 'fault-panel-item ' + level;
      item.innerHTML =
        `<span class="fault-panel-item-badge">${level.toUpperCase()}</span>` +
        `<span class="fault-panel-item-msg">${message}</span>` +
        `<button class="fault-panel-item-dismiss" onclick="dismissFault('${key}')">✕</button>`;
      list.appendChild(item);
    });
  }

  if (emptyEl)   emptyEl.style.display   = count === 0 ? 'block' : 'none';
  if (dismissEl) dismissEl.style.display = count > 0   ? 'block' : 'none';
}

var socket = null;         // var so script.js can reference it globally
let reconnectTimer = null;
let isConnecting = false;
let mapHasZoomed = false;  // auto-zoom to drone once on first GPS fix per connection

// ─── CHART CONFIG ─────────────────────────────────────────────────────────────
const CHART_POINTS = 60;

const chartBuffers = {
  roll:      [], pitch:     [],
  alt:       [], climb:     [],
  voltage:   [], current:   [],
  sats:      [], hdop:      [],
  vib_x:     [], vib_y:    [], vib_z: [],
  motor_avg: [], throttle:  [],
};

const vibSmooth = { vib_x: null, vib_y: null, vib_z: null };
const VIB_SMOOTH_ALPHA = 0.18;

function pushBuffer(key, value) {
  if (chartBuffers[key] === undefined) return;
  chartBuffers[key].push(typeof value === "number" ? value : 0);
  if (chartBuffers[key].length > CHART_POINTS) chartBuffers[key].shift();
}

function resetAllBuffers() {
  Object.keys(chartBuffers).forEach(k => { chartBuffers[k] = []; });
  Object.keys(vibSmooth).forEach(k => { vibSmooth[k] = null; });
  mapHasZoomed = false;
}

function pushSmoothedVibration(key, value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return;
  vibSmooth[key] = vibSmooth[key] == null
    ? n
    : vibSmooth[key] + (n - vibSmooth[key]) * VIB_SMOOTH_ALPHA;
  pushBuffer(key, vibSmooth[key]);
}

function normalize(arr, fixedMin, fixedMax) {
  if (arr.length === 0) return [];
  const min = fixedMin !== undefined ? fixedMin : Math.min(...arr);
  const max = fixedMax !== undefined ? fixedMax : Math.max(...arr);
  if (max === min) return arr.map(() => 0.5);
  return arr.map(v => Math.max(0, Math.min(1, (v - min) / (max - min))));
}

function lastVal(arr, decimals = 1, unit = "") {
  if (arr.length === 0) return "--";
  const v = arr[arr.length - 1];
  return (typeof v === "number" ? v.toFixed(decimals) : v) + unit;
}

// ─── CHART RENDERER ───────────────────────────────────────────────────────────
function drawLiveChart(canvasId, lines, options = {}) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const rect = canvas.getBoundingClientRect();
  const W = rect.width;
  const H = rect.height;
  if (W <= 0 || H <= 0) return;

  const dpr = window.devicePixelRatio || 1;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const { gridX = 10, gridY = 5, showLegend = true } = options;

  ctx.fillStyle = "#03070c";
  ctx.fillRect(0, 0, W, H);

  const legendH = showLegend ? 14 : 0;
  const plotTop = legendH + 2;
  const plotH = H - plotTop - 4;

  if (showLegend) {
    let lx = 6;
    ctx.font = "9px Arial";
    lines.forEach(line => {
      const val = lastVal(line.data, line.decimals ?? 1, line.unit ?? "");
      const label = `${line.label ?? ""}: ${val}`;
      ctx.fillStyle = line.color;
      ctx.fillRect(lx, 3, 8, 8);
      ctx.fillStyle = "rgba(200,220,235,0.85)";
      ctx.fillText(label, lx + 11, 11);
      lx += ctx.measureText(label).width + 20;
    });
  }

  ctx.strokeStyle = "rgba(255,255,255,0.05)";
  ctx.lineWidth = 1;
  for (let x = 0; x <= gridX; x++) {
    const px = (x / gridX) * W;
    ctx.beginPath(); ctx.moveTo(px, plotTop); ctx.lineTo(px, plotTop + plotH); ctx.stroke();
  }
  for (let y = 0; y <= gridY; y++) {
    const py = plotTop + (y / gridY) * plotH;
    ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(W, py); ctx.stroke();
  }

  const zeroY = plotTop + plotH * 0.5;
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.setLineDash([3, 5]);
  ctx.beginPath(); ctx.moveTo(0, zeroY); ctx.lineTo(W, zeroY); ctx.stroke();
  ctx.setLineDash([]);

  lines.forEach(line => {
    if (!line.data || line.data.length < 2) return;
    const norm = normalize(line.data, line.fixedMin, line.fixedMax);

    ctx.beginPath();
    ctx.strokeStyle = line.color;
    ctx.lineWidth = line.width ?? 1.5;
    ctx.lineJoin = "round";

    norm.forEach((v, i) => {
      const x = (i / (norm.length - 1)) * W;
      const y = plotTop + plotH - v * plotH * 0.9 - plotH * 0.05;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    if (norm.length > 0) {
      const lv = norm[norm.length - 1];
      const lx = W - 2;
      const ly = plotTop + plotH - lv * plotH * 0.9 - plotH * 0.05;
      ctx.beginPath();
      ctx.arc(lx, ly, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = line.color;
      ctx.fill();
    }
  });

  const hasData = lines.some(l => l.data && l.data.length > 0);
  if (!hasData) {
    ctx.fillStyle = "rgba(100,140,160,0.4)";
    ctx.font = "10px Arial";
    ctx.textAlign = "center";
    ctx.fillText("NO DATA", W / 2, plotTop + plotH / 2);
    ctx.textAlign = "left";
  }
}

function redrawAllCharts() {
  drawLiveChart("chart1", [
    { data: chartBuffers.roll,  color: "#7fdcff", label: "ROLL",  unit: "°", decimals: 1, fixedMin: -30, fixedMax: 30 },
    { data: chartBuffers.pitch, color: "#bfff88", label: "PITCH", unit: "°", decimals: 1, fixedMin: -30, fixedMax: 30 },
  ], { gridX: 10, gridY: 4 });

  drawLiveChart("chart2", [
    { data: chartBuffers.alt,   color: "#f0bc59", label: "ALT",   unit: "m",   decimals: 1 },
    { data: chartBuffers.climb, color: "#7fdcff", label: "CLIMB", unit: "m/s", decimals: 2 },
  ], { gridX: 10, gridY: 4 });

  drawLiveChart("chart3", [
    { data: chartBuffers.voltage, color: "#c8ff80", label: "VOLT",    unit: "V", decimals: 1, fixedMin: 10, fixedMax: 17 },
    { data: chartBuffers.current, color: "#ff7eb3", label: "CURRENT", unit: "A", decimals: 1 },
  ], { gridX: 10, gridY: 4 });

  drawLiveChart("chart4", [
    { data: chartBuffers.sats, color: "#bfff88", label: "SATS", unit: "", decimals: 0, fixedMin: 0, fixedMax: 25 },
    { data: chartBuffers.hdop, color: "#7fdcff", label: "HDOP", unit: "", decimals: 2, fixedMin: 0, fixedMax: 5 },
  ], { gridX: 10, gridY: 4 });

  drawLiveChart("chart5", [
    { data: chartBuffers.vib_x, color: "#ffffff", label: "VIB X", unit: "", decimals: 3, width: 1.2 },
    { data: chartBuffers.vib_y, color: "#7fdcff", label: "VIB Y", unit: "", decimals: 3, width: 1.2 },
    { data: chartBuffers.vib_z, color: "#ff674e", label: "VIB Z", unit: "", decimals: 3, width: 1.2 },
  ], { gridX: 12, gridY: 4 });

  drawLiveChart("chart6", [
    { data: chartBuffers.motor_avg, color: "#bfff88", label: "MOTOR AVG", unit: "%", decimals: 1, fixedMin: 0, fixedMax: 100 },
    { data: chartBuffers.current,   color: "#7fdcff", label: "CURRENT",   unit: "A", decimals: 1 },
    { data: chartBuffers.voltage,   color: "#ff674e", label: "VOLTAGE",   unit: "V", decimals: 1, fixedMin: 10, fixedMax: 17 },
  ], { gridX: 18, gridY: 4 });

  const vibMag = chartBuffers.vib_x.map((x, i) => {
    const y = chartBuffers.vib_y[i] ?? 0;
    const z = chartBuffers.vib_z[i] ?? 0;
    return Math.sqrt(x*x + y*y + z*z);
  });
  drawLiveChart("chart7", [
    { data: chartBuffers.sats, color: "#7fdcff", label: "SATS",    unit: "",  decimals: 0 },
    { data: chartBuffers.alt,  color: "#bfff88", label: "ALT",     unit: "m", decimals: 1 },
    { data: vibMag,            color: "#ffffff", label: "VIB MAG", unit: "",  decimals: 3, width: 1.2 },
  ], { gridX: 18, gridY: 4 });
}

// ─── RESET BUTTON ─────────────────────────────────────────────────────────────
function injectResetButton() {
  if (document.getElementById("chartResetBtn")) return;
  const timeline = document.querySelector(".bottom-timeline");
  if (!timeline) return;
  const titleEl = timeline.querySelector(".panel-title");
  if (!titleEl) return;

  titleEl.style.display = "flex";
  titleEl.style.justifyContent = "space-between";
  titleEl.style.alignItems = "center";

  const btn = document.createElement("button");
  btn.id = "chartResetBtn";
  btn.textContent = "RESET CHARTS";
  btn.style.cssText = `
    background: transparent;
    border: 1px solid rgba(127,220,255,0.3);
    color: #7fdcff;
    font-size: 9px;
    letter-spacing: 1px;
    padding: 3px 8px;
    cursor: pointer;
    font-family: inherit;
    text-transform: uppercase;
    border-radius: 3px;
    transition: all 0.15s;
  `;
  btn.onmouseover = () => {
    btn.style.background = "rgba(127,220,255,0.1)";
    btn.style.borderColor = "rgba(127,220,255,0.6)";
  };
  btn.onmouseout = () => {
    btn.style.background = "transparent";
    btn.style.borderColor = "rgba(127,220,255,0.3)";
  };
  btn.addEventListener("click", () => {
    resetAllBuffers();
    redrawAllCharts();
  });
  titleEl.appendChild(btn);
}

// ─── UI HELPERS ───────────────────────────────────────────────────────────────
function getEl(id) { return document.getElementById(id); }

function setText(id, value) {
  const el = getEl(id);
  if (el) el.innerText = value;
}

function setStatusSelect(value) {
  const el = getEl("statusSelect");
  if (!el) return;
  el.value = value;
  el.classList.remove("status-armed", "status-prearm", "status-disarmed");
  if (value === "armed") el.classList.add("status-armed");
  if (value === "prearm") el.classList.add("status-prearm");
  if (value === "disarmed") el.classList.add("status-disarmed");
}

function setOnlineStatus(el, text, online) {
  if (!el) return;
  el.innerText = text;
  el.classList.remove("status-online", "status-offline", "sensor-ok", "sensor-offline", "green-text");
  el.classList.add(online ? "status-online" : "status-offline");
}

function setSensorStatus(id, text, online) {
  const el = getEl(id);
  if (!el) return;
  el.innerText = text;
  el.classList.remove("sensor-ok", "sensor-offline");
  el.classList.add(online ? "sensor-ok" : "sensor-offline");
}

function setDisconnectedState() {
  setStatusSelect("disarmed");
  setText("topVoltage", "0.0V"); setText("topCurrent", "0.0A");
  setText("topBattery", "0%");   setText("cpuLoadTop", "0%");
  setText("missionDistanceVal", "0.0"); setText("ltVal", "0");
  setText("rollVal", "0.0°");    setText("pitchVal", "0.0°");
  setText("headingVal", "0°");   setText("compassHeadingText", "0°");
  setText("altVal", "0.0 m");    setText("spdVal", "0.0 m/s");
  setText("lidarAltVal", "-- m");
  setText("latVal", "0.0000°");  setText("lonVal", "0.0000°");
  setText("mapModeVal", "DISCONNECTED");
  setText("positionVal", "NO DATA"); setText("mslVal", "0.0m");
  setText("rssiVal", "0");
  setText("m1pwm","0"); setText("m2pwm","0");
  setText("m3pwm","0"); setText("m4pwm","0");
  setText("m1out","0%"); setText("m2out","0%");
  setText("m3out","0%"); setText("m4out","0%");

  setOnlineStatus(getEl("gpsFixTop"), "NO FIX", false);
  setText("satCountTop", "0");
  setOnlineStatus(getEl("ekfTop"), "OFFLINE", false);
  setOnlineStatus(getEl("linkTop"), "NO SIGNAL", false);

  const rc = getEl("rcTelemetryState");
  if (rc) {
    rc.innerText = "NO SIGNAL";
    rc.classList.remove("green-text", "status-online");
    rc.classList.add("status-offline");
  }

  ["sensorGps","sensorLidar","sensorOpticalFlow","sensorCompass",
   "sensorBattery","sensorImu","sensorBarometer"].forEach(id => {
    setSensorStatus(id, "OFFLINE", false);
  });

  const arrow = getEl("miniCompassArrow");
  if (arrow) arrow.style.transform = "translate(-50%, -50%) rotate(0deg)";
  const disc = getEl("horizonDisc");
  if (disc) disc.style.transform = "rotate(0deg) translateY(0px)";

  ["m1bar","m2bar","m3bar","m4bar"].forEach(id => {
    const el = getEl(id); if (el) el.style.width = "0%";
  });

  mapHasZoomed = false;
  if (window.map) window.map.setView([39.8283, -98.5795], 4);
  if (window.droneMarker) window.droneMarker.setLatLng([39.8283, -98.5795]);
  if (window.homeMarker) window.homeMarker.setLatLng([39.8283, -98.5795]);
  if (window.trailLine) window.trailLine.setLatLngs([[39.8283, -98.5795]]);
}

// ─── LOS DETECTION ────────────────────────────────────────────────────────────
let losActive = false;
let losTimer = null;

function detectLOS(data) {
  return false;
}

function setLOSState() {
  if (losActive) return;
  losActive = true;

  const topbar = document.querySelector(".topbar");
  if (topbar) {
    topbar.style.background = "linear-gradient(180deg, rgba(80,10,10,.97), rgba(50,5,5,.99))";
    topbar.style.borderBottomColor = "rgba(248,113,113,.4)";
  }

  const linkEl = getEl("linkTop");
  if (linkEl) {
    linkEl.innerText = "LOS";
    linkEl.classList.remove("status-online");
    linkEl.classList.add("status-offline");
    linkEl.style.color = "#f87171";
    linkEl.style.fontWeight = "700";
    linkEl.style.animation = "blink-los 0.8s infinite";
  }

  const rc = getEl("rcTelemetryState");
  if (rc) {
    rc.innerText = "LOSS OF SIGNAL";
    rc.classList.remove("green-text", "status-online");
    rc.classList.add("status-offline");
    rc.style.color = "#f87171";
  }

  ["sensorGps","sensorLidar","sensorOpticalFlow","sensorCompass",
   "sensorBattery","sensorImu","sensorBarometer"].forEach(id => {
    setSensorStatus(id, "LOS", false);
  });

  setText("topVoltage", "0.0V");
  setText("topCurrent", "0.0A");
  setText("topBattery", "0%");
  setOnlineStatus(getEl("gpsFixTop"), "LOS", false);
  setOnlineStatus(getEl("ekfTop"), "LOS", false);

  triggerFaultAlert('fault', 'los', 'LOSS OF SIGNAL — RC link lost');

  if (!document.getElementById("los-style")) {
    const style = document.createElement("style");
    style.id = "los-style";
    style.textContent = `
      @keyframes blink-los { 0%,100%{opacity:1} 50%{opacity:.3} }
      #linkTop { animation: blink-los 0.8s infinite; }
    `;
    document.head.appendChild(style);
  }
}

function clearLOSState() {
  if (!losActive) return;
  losActive = false;

  clearFaultAlert('los');

  const topbar = document.querySelector(".topbar");
  if (topbar) {
    topbar.style.background = "";
    topbar.style.borderBottomColor = "";
  }

  const linkEl = getEl("linkTop");
  if (linkEl) {
    linkEl.style.color = "";
    linkEl.style.fontWeight = "";
    linkEl.style.animation = "";
  }
}

function setConnectedState(data) {
  if (data && detectLOS(data)) {
    setLOSState();
    return;
  }
  clearLOSState();
  setOnlineStatus(getEl("linkTop"), "GOOD", true);
  const rc = getEl("rcTelemetryState");
  if (rc) {
    rc.innerText = "LINK GOOD";
    rc.style.color = "";
    rc.classList.remove("status-offline");
    rc.classList.add("green-text", "status-online");
  }
}

function updateMapFromTelemetry(lat, lon) {
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180 || (lat === 0 && lon === 0)) return;
  setText("latVal", lat.toFixed(4) + "°");
  setText("lonVal", lon.toFixed(4) + "°");

  if (window.droneMarker) window.droneMarker.setLatLng([lat, lon]);

  if (window.map && !mapHasZoomed) {
    window.map.setView([lat, lon], 17);
    mapHasZoomed = true;
    if (window.trailLine) window.trailLine.setLatLngs([[lat, lon]]);
  }

  if (window.trailLine) {
    const latlngs = window.trailLine.getLatLngs();
    latlngs.push([lat, lon]);
    if (latlngs.length > 500) latlngs.shift();
    window.trailLine.setLatLngs(latlngs);
  }
}

// ─── FIX 1: updateAttitude ────────────────────────────────────────────────────
// Jetson already sends roll/pitch/yaw in DEGREES. The previous version wrongly
// applied a radians→degrees conversion (r2d), turning 2° of roll into ~115°.
// Values are now used directly as-is.
function updateAttitude(data) {
  if (data.yaw !== undefined) {
    const yawDeg = ((data.yaw) + 360) % 360;
    setText("headingVal", Math.round(yawDeg) + "°");
    setText("compassHeadingText", Math.round(yawDeg) + "°");
    const arrow = getEl("miniCompassArrow");
    if (arrow) arrow.style.transform = `translate(-50%, -50%) rotate(${yawDeg}deg)`;
  }

  let rollDeg = null, pitchDeg = null;
  if (data.roll !== undefined) {
    rollDeg = data.roll;
    setText("rollVal", `${rollDeg >= 0 ? "+" : ""}${rollDeg.toFixed(1)}°`);
    pushBuffer("roll", rollDeg);
  }
  if (data.pitch !== undefined) {
    pitchDeg = data.pitch;
    setText("pitchVal", `${pitchDeg >= 0 ? "+" : ""}${pitchDeg.toFixed(1)}°`);
    pushBuffer("pitch", pitchDeg);
  }

  const disc = getEl("horizonDisc");
  if (disc && rollDeg !== null && pitchDeg !== null) {
    disc.style.transformOrigin = "25% 25%";
    const pitchPx = Math.max(-40, Math.min(40, pitchDeg * 1.2));
    disc.style.transform = `rotate(${rollDeg}deg) translateY(${pitchPx}px)`;
  }
}

// ─── FIX 2: updateMotors ─────────────────────────────────────────────────────
// Jetson sends m1/m2/m3/m4 as raw PWM (e.g. 1449). There are no m1_out fields.
// Percentage is computed from PWM: clamp((pwm - 1000) / 10, 0, 100).
function updateMotors(data) {
  const pwmPct = p => Math.min(100, Math.max(0, Math.round((p - 1000) / 10)));
  const outs = [];

  ["m1","m2","m3","m4"].forEach(m => {
    if (data[m] !== undefined) {
      const pwm = Math.floor(data[m]);
      const pct = pwmPct(pwm);
      setText(`${m}pwm`, pwm);
      setText(`${m}out`, `${pct}%`);
      const bar = getEl(`${m}bar`);
      if (bar) bar.style.width = `${pct}%`;
      outs.push(pct);
    }
  });

  if (outs.length > 0) {
    pushBuffer("motor_avg", outs.reduce((a,b) => a+b, 0) / outs.length);
  }
}

// ─── MAIN APPLY ───────────────────────────────────────────────────────────────
function applyTelemetry(data) {
  const pxOk = data.pixhawk_connected !== false;
  setConnectedState(data);
  if (losActive) return;

  // Jetson-sourced — always update regardless of FC connection
  if (data.cpu !== undefined) setText("cpuLoadTop", `${Math.round(data.cpu)}%`);

  if (!pxOk) {
    // FC offline — blank all FC-dependent displays
    setText("topVoltage", "--V"); setText("topCurrent", "--A"); setText("topBattery", "--%");
    setText("satCountTop", "--"); setText("rssiVal", "--");
    setText("altVal", "-- m"); setText("spdVal", "-- m/s");
    setOnlineStatus(getEl("gpsFixTop"), "OFFLINE", false);
    setOnlineStatus(getEl("ekfTop"), "OFFLINE", false);
    setSensorStatus("sensorGps",          "OFFLINE", false);
    setSensorStatus("sensorBattery",      "OFFLINE", false);
    setSensorStatus("sensorLidar",        "OFFLINE", false);
    setSensorStatus("sensorCompass",      "OFFLINE", false);
    setSensorStatus("sensorImu",          "OFFLINE", false);
    setSensorStatus("sensorBarometer",    "OFFLINE", false);
    setSensorStatus("sensorOpticalFlow",  "OFFLINE", false);
    triggerFaultAlert('fault', 'pixhawk', 'PIXHAWK OFFLINE — No flight controller connection');
    redrawAllCharts();
    return;
  }

  clearFaultAlert('pixhawk');

  // FC-sourced data
  if (data.voltage !== undefined) {
    setText("topVoltage", data.voltage.toFixed(1) + "V");
    pushBuffer("voltage", data.voltage);
  }
  if (data.current !== undefined) {
    setText("topCurrent", data.current.toFixed(1) + "A");
    pushBuffer("current", data.current);
  }
  if (data.battery !== undefined) {
    setText("topBattery", `${Math.round(data.battery)}%`);
    if (data.battery < 20) triggerFaultAlert('warn', 'battery-low', `LOW BATTERY — ${Math.round(data.battery)}% remaining`);
    else clearFaultAlert('battery-low');
  }

  if (data.sats !== undefined) {
    setText("satCountTop", data.sats);
    pushBuffer("sats", data.sats);
  }
  if (data.fix_type !== undefined) {
    const ok = data.fix_type !== "NO FIX";
    setOnlineStatus(getEl("gpsFixTop"), data.fix_type, ok);
    setSensorStatus("sensorGps", ok ? "NOMINAL" : "NO FIX", ok);
  }
  if (data.hdop != null) pushBuffer("hdop", data.hdop);

  if (data.ekf_healthy !== undefined) {
    const ekfOk = data.ekf_healthy === true;
    setOnlineStatus(getEl("ekfTop"), ekfOk ? "HEALTHY" : "DEGRADED", ekfOk);
    if (!ekfOk) triggerFaultAlert('warn', 'ekf', 'EKF DEGRADED');
    else clearFaultAlert('ekf');
  }

  if (data.alt !== undefined) {
    setText("altVal", data.alt.toFixed(1) + " m");
    pushBuffer("alt", data.alt);
  }
  if (data.rangefinder !== undefined) {
    setText("lidarAltVal", data.rangefinder.toFixed(2) + " m");
  }

  if (data.spd !== undefined) setText("spdVal", data.spd.toFixed(1) + " m/s");
  if (data.vspd !== undefined) pushBuffer("climb", data.vspd);

  if (data.rssi !== undefined) setText("rssiVal", data.rssi.toFixed ? data.rssi.toFixed(1) : data.rssi);

  if (data.flight_mode !== undefined) {
    setText("mapModeVal", data.flight_mode);
    const modeSelect = getEl("flightModeSelect");
    if (modeSelect) {
      const MODE_OPT = {
        "STABILIZE":"STABILIZE","ACRO":"ACRO","ALT HOLD":"ALTHOLD","ALTHOLD":"ALTHOLD",
        "AUTO":"AUTO","GUIDED":"GUIDED","LOITER":"LOITER","RTL":"RTL","LAND":"LAND",
        "POSHOLD":"POSHOLD","BRAKE":"BRAKE","SPORT":"SPORT","DRIFT":"DRIFT","FLIP":"FLIP",
        "AUTOTUNE":"AUTOTUNE","THROW":"THROW","SMART_RTL":"SMART_RTL","SMARTRTL":"SMART_RTL"
      };
      const opt = MODE_OPT[String(data.flight_mode).toUpperCase()] || data.flight_mode;
      const exists = Array.from(modeSelect.options).some(o => o.value === opt);
      if (exists) modeSelect.value = opt;
    }
  }
  if (data.armed !== undefined) setStatusSelect(data.armed ? "armed" : "disarmed");

  // Sensor status
  setSensorStatus("sensorBattery",
    data.voltage > 0 ? "NOMINAL" : "FAULT",
    data.voltage > 0);
  if (data.voltage !== undefined) {
    if (data.voltage <= 0) triggerFaultAlert('fault', 'battery-volt', 'BATTERY FAULT — No voltage reading');
    else clearFaultAlert('battery-volt');
  }

  setSensorStatus("sensorLidar",
    data.rangefinder > 0 ? "NOMINAL" : "OFFLINE",
    data.rangefinder > 0);

  setSensorStatus("sensorCompass",
    data.yaw !== undefined ? "NOMINAL" : "OFFLINE",
    data.yaw !== undefined);

  setSensorStatus("sensorImu",
    (data.roll !== undefined && data.pitch !== undefined) ? "NOMINAL" : "OFFLINE",
    (data.roll !== undefined && data.pitch !== undefined));

  setSensorStatus("sensorBarometer",
    data.alt !== undefined ? "NOMINAL" : "OFFLINE",
    data.alt !== undefined);

  setSensorStatus("sensorOpticalFlow",
    data.of_quality > 0 ? "NOMINAL" : "OFFLINE",
    data.of_quality > 0);

  // Camera status driven by WebRTC — do not overwrite here.

  updateAttitude(data);
  updateMotors(data);

  if (data.vib_x !== undefined) pushSmoothedVibration("vib_x", data.vib_x);
  if (data.vib_y !== undefined) pushSmoothedVibration("vib_y", data.vib_y);
  if (data.vib_z !== undefined) pushSmoothedVibration("vib_z", data.vib_z);

  if (data.lat !== undefined && data.lon !== undefined && !(data.lat === 0 && data.lon === 0)) {
    updateMapFromTelemetry(data.lat, data.lon);
  }

  redrawAllCharts();
}

// ─── WEBSOCKET ────────────────────────────────────────────────────────────────
function connectTelemetry() {
  if (isConnecting) return;
  if (socket && socket.readyState === WebSocket.OPEN) return;
  if (socket && socket.readyState === WebSocket.CONNECTING) return;
  if (!isRealMode) return;

  isConnecting = true;
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }

  // Connects to the VPS relay over the secure nginx reverse proxy. Browsers
  // never connect directly to the Jetson for telemetry (the Jetson is behind
  // NAT). The Jetson pushes into port 9001 and the relay re-serves to browsers
  // on port 9101, which nginx exposes securely as wss://console.airsent.tech/telem.
  socket = new WebSocket(window.airsentApiUrl("/telem", true));

  socket.onopen = () => {
    console.log("✅ WebSocket connected to VPS relay");
    isConnecting = false;
    mapHasZoomed = false;
  };

  socket.onclose = () => {
    console.log("WebSocket disconnected. Retrying in 3s...");
    isConnecting = false;
    socket = null;
    setDisconnectedState();
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connectTelemetry, 3000);
  };

  socket.onerror = () => { isConnecting = false; };

  socket.onmessage = (event) => {
    try { applyTelemetry(JSON.parse(event.data)); }
    catch (e) { console.error("Bad telemetry data:", e); }
  };
}

// ─── DEMO MODE ────────────────────────────────────────────────────────────────
function rnd(min, max) { return Math.random() * (max - min) + min; }

function updateDemoTelemetry() {
  const roll    = rnd(-4, 4);
  const pitch   = rnd(-3, 3);
  const hdg     = rnd(100, 140);
  const alt     = rnd(80, 90);
  const spd     = rnd(4, 7);
  const vspd    = rnd(-0.5, 0.5);
  const voltage = rnd(15.4, 16.1);
  const current = rnd(9.0, 12.0);
  const sats    = Math.floor(rnd(17, 22));
  const hdop    = rnd(0.6, 1.4);
  const vib_x   = rnd(0.1, 0.4);
  const vib_y   = rnd(0.1, 0.4);
  const vib_z   = rnd(0.2, 0.6);
  const lat     = 33.4242 + rnd(-0.0008, 0.0008);
  const lon     = -111.9281 + rnd(-0.0008, 0.0008);
  const lidar   = rnd(0.5, 3.5);

  setStatusSelect("armed");
  setText("topVoltage", voltage.toFixed(1) + "V");
  setText("topCurrent", current.toFixed(1) + "A");
  setText("topBattery", Math.floor(rnd(68, 75)) + "%");
  setText("cpuLoadTop", Math.floor(rnd(20, 40)) + "%");

  setOnlineStatus(getEl("gpsFixTop"), "RTK FIX", true);
  setText("satCountTop", sats);
  setOnlineStatus(getEl("ekfTop"), "HEALTHY", true);
  setOnlineStatus(getEl("linkTop"), "GOOD", true);

  setText("rollVal",  `${roll  >= 0 ? "+" : ""}${roll.toFixed(1)}°`);
  setText("pitchVal", `${pitch >= 0 ? "+" : ""}${pitch.toFixed(1)}°`);
  setText("headingVal", Math.round(hdg) + "°");
  setText("compassHeadingText", Math.round(hdg) + "°");
  setText("altVal", alt.toFixed(1) + " m");
  setText("lidarAltVal", lidar.toFixed(2) + " m");
  setText("spdVal", spd.toFixed(1) + " m/s");
  setText("missionDistanceVal", rnd(22.0, 24.0).toFixed(1));
  setText("ltVal", Math.floor(rnd(10, 13)));
  setText("mapModeVal", "POSHOLD");
  setText("positionVal", "17.20 WAYPOINT");
  setText("mslVal", "-1.5m");
  setText("rssiVal", rnd(82, 86).toFixed(1));

  const arrow = getEl("miniCompassArrow");
  if (arrow) arrow.style.transform = `translate(-50%, -50%) rotate(${hdg}deg)`;
  const disc = getEl("horizonDisc");
  if (disc) {
    disc.style.transformOrigin = "25% 25%";
    const pitchPx = Math.max(-40, Math.min(40, pitch * 1.2));
    disc.style.transform = `rotate(${roll}deg) translateY(${pitchPx}px)`;
  }

  updateMapFromTelemetry(lat, lon);

  ["sensorGps","sensorLidar","sensorOpticalFlow","sensorCompass",
   "sensorBattery","sensorImu","sensorBarometer"].forEach(id => {
    setSensorStatus(id, "NOMINAL", true);
  });

  const pwmVals = [
    Math.floor(rnd(1400,1500)), Math.floor(rnd(1400,1500)),
    Math.floor(rnd(1400,1500)), Math.floor(rnd(1400,1500)),
  ];
  const pwmPct = p => Math.min(100, Math.max(0, Math.round((p - 1000) / 10)));

  pwmVals.forEach((pwm, i) => {
    const m = `m${i+1}`;
    const pct = pwmPct(pwm);
    setText(`${m}pwm`, pwm);
    setText(`${m}out`, `${pct}%`);
    const bar = getEl(`${m}bar`);
    if (bar) bar.style.width = `${pct}%`;
  });

  pushBuffer("roll", roll);       pushBuffer("pitch", pitch);
  pushBuffer("alt", alt);         pushBuffer("climb", vspd);
  pushBuffer("voltage", voltage); pushBuffer("current", current);
  pushBuffer("sats", sats);       pushBuffer("hdop", hdop);
  pushBuffer("vib_x", vib_x);    pushBuffer("vib_y", vib_y); pushBuffer("vib_z", vib_z);
  pushBuffer("motor_avg", pwmVals.reduce((a,p) => a + pwmPct(p), 0) / 4);
  pushBuffer("throttle", rnd(30, 45));

  redrawAllCharts();
}

// ─── INIT ─────────────────────────────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", () => {
  injectResetButton();
  redrawAllCharts();
});

window.addEventListener("resize", () => redrawAllCharts());

if (isRealMode) {
  setDisconnectedState();
  connectTelemetry();
} else {
  updateDemoTelemetry();
  setInterval(updateDemoTelemetry, 1000);
}
