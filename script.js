const telemetryEls = {
  missionTime: document.getElementById("missionTime"),
  topVoltage: document.getElementById("topVoltage"),
  topCurrent: document.getElementById("topCurrent"),
  topBattery: document.getElementById("topBattery"),
  cpuLoadTop: document.getElementById("cpuLoadTop"),
  rollVal: document.getElementById("rollVal"),
  pitchVal: document.getElementById("pitchVal"),
  headingVal: document.getElementById("headingVal"),
  altVal: document.getElementById("altVal"),
  spdVal: document.getElementById("spdVal"),
  m1pwm: document.getElementById("m1pwm"),
  m2pwm: document.getElementById("m2pwm"),
  m3pwm: document.getElementById("m3pwm"),
  m4pwm: document.getElementById("m4pwm"),
  m1out: document.getElementById("m1out"),
  m2out: document.getElementById("m2out"),
  m3out: document.getElementById("m3out"),
  m4out: document.getElementById("m4out"),
  horizonDisc: document.getElementById("horizonDisc"),
  latVal: document.getElementById("latVal"),
  lonVal: document.getElementById("lonVal"),
  compassHeadingText: document.getElementById("compassHeadingText"),
  miniCompassArrow: document.getElementById("miniCompassArrow"),
  statusSelect: document.getElementById("statusSelect"),
  flightModeSelect: document.getElementById("flightModeSelect"),
  flightActionIndicator: document.getElementById("flightActionIndicator"),
  confirmOverlay: document.getElementById("confirmOverlay"),
  confirmMessage: document.getElementById("confirmMessage"),
  confirmCancel: document.getElementById("confirmCancel"),
  confirmOk: document.getElementById("confirmOk"),
  missionPauseBtn: document.getElementById("missionPauseBtn"),
  missionResetBtn: document.getElementById("missionResetBtn"),
  eventsPauseBtn: document.getElementById("eventsPauseBtn"),
  eventsResetBtn: document.getElementById("eventsResetBtn"),
 
  failsafeStateTop: document.getElementById("failsafeStateTop"),
  summaryFailsafe: document.getElementById("summaryFailsafe"),
  diagStatus: document.getElementById("diagStatus"),
  diagOscillation: document.getElementById("diagOscillation"),
  diagSpread: document.getElementById("diagSpread"),
  diagHighest: document.getElementById("diagHighest"),
  diagLowest: document.getElementById("diagLowest"),
  diagAverage: document.getElementById("diagAverage"),
 
  quadM1Pwm: document.getElementById("quadM1Pwm"),
  quadM2Pwm: document.getElementById("quadM2Pwm"),
  quadM3Pwm: document.getElementById("quadM3Pwm"),
  quadM4Pwm: document.getElementById("quadM4Pwm"),
  quadM1Out: document.getElementById("quadM1Out"),
  quadM2Out: document.getElementById("quadM2Out"),
  quadM3Out: document.getElementById("quadM3Out"),
  quadM4Out: document.getElementById("quadM4Out"),
 
  m1bar: document.getElementById("m1bar"),
  m2bar: document.getElementById("m2bar"),
  m3bar: document.getElementById("m3bar"),
  m4bar: document.getElementById("m4bar"),
 
  motorCard1: document.getElementById("motorCard1"),
  motorCard2: document.getElementById("motorCard2"),
  motorCard3: document.getElementById("motorCard3"),
  motorCard4: document.getElementById("motorCard4"),
 
  nodeM1: document.getElementById("nodeM1"),
  nodeM2: document.getElementById("nodeM2"),
  nodeM3: document.getElementById("nodeM3"),
  nodeM4: document.getElementById("nodeM4"),
 
  gpsFixTop: document.getElementById("gpsFixTop"),
  satCountTop: document.getElementById("satCountTop"),
  ekfTop: document.getElementById("ekfTop"),
  linkTop: document.getElementById("linkTop"),
  missionDistanceVal: document.getElementById("missionDistanceVal"),
  consoleUsersTotal: document.getElementById("consoleUsersTotal"),
  consoleOperators: document.getElementById("consoleOperators"),
  consoleViewers: document.getElementById("consoleViewers"),
  ltVal: document.getElementById("ltVal"),
  mapModeVal: document.getElementById("mapModeVal"),
  positionVal: document.getElementById("positionVal"),
  mslVal: document.getElementById("mslVal"),
  rcTelemetryState: document.getElementById("rcTelemetryState"),
  rssiVal: document.getElementById("rssiVal"),
  sensorGps: document.getElementById("sensorGps"),
  sensorLidar: document.getElementById("sensorLidar"),
  sensorOpticalFlow: document.getElementById("sensorOpticalFlow"),
  sensorCompass: document.getElementById("sensorCompass"),
  sensorBattery: document.getElementById("sensorBattery"),
  sensorImu: document.getElementById("sensorImu"),
  sensorBarometer: document.getElementById("sensorBarometer"),
};
 
const appMode = (localStorage.getItem("airsentMode") || "demo").toLowerCase();
const isDemoMode = appMode === "demo";
const isViewerMode = appMode === "viewer";
const canSendCommands = appMode === "operator" || appMode === "actual" || appMode === "real";
const isRealMode = !isDemoMode;
window.airsentMode = appMode;
window.airsentCanCommand = canSendCommands;

/* COMMAND CHANNEL — sends real commands to the drone through the VPS relay.
   Browser -> wss://console.airsent.tech/cmd -> VPS:9104 -> VPS:9004 -> Jetson.
   Mirrors pilot.html. Real mode only; demo mode commands are no-ops. */
let cmdSocket = null;
let cmdReconnectTimer = null;

function connectCmd() {
  if (!canSendCommands) return;
  if (cmdSocket && (cmdSocket.readyState === WebSocket.OPEN ||
                    cmdSocket.readyState === WebSocket.CONNECTING)) return;
  cmdSocket = new WebSocket(window.airsentApiUrl("/cmd", true));
  cmdSocket.onopen  = () => { console.log("\u2705 Command channel connected"); };
  cmdSocket.onclose = () => {
    cmdSocket = null;
    if (cmdReconnectTimer) clearTimeout(cmdReconnectTimer);
    cmdReconnectTimer = setTimeout(connectCmd, 3000);
  };
  cmdSocket.onerror = () => {};
  cmdSocket.onmessage = (ev) => {
    try {
      const r = JSON.parse(ev.data);
      if (r && r.msg) prependEvent("DRONE: " + r.msg);
    } catch (e) {}
  };
}

function sendCmd(obj) {
  if (!canSendCommands) {
    if (!isViewerMode) console.log("[demo] would send:", obj);
    return false;
  }
  if (cmdSocket && cmdSocket.readyState === WebSocket.OPEN) {
    cmdSocket.send(JSON.stringify(obj));
    return true;
  }
  prependEvent("CMD FAILED — no link to drone");
  return false;
}
 
let missionSeconds = 25 * 60 + 38;
let missionTimerPaused = false;
let eventLogPaused = false;
let currentActiveFlightButton = null;
let pendingConfirmAction = null;
 
const eventLogPanel = document.querySelector(".events-panel");
const flightButtons = Array.from(document.querySelectorAll(".flight-btn"));
const failsafeSelects = Array.from(document.querySelectorAll(".failsafe-select"));
 
const motorCards = [
  telemetryEls.motorCard1,
  telemetryEls.motorCard2,
  telemetryEls.motorCard3,
  telemetryEls.motorCard4
].filter(Boolean);
 
const motorNodes = [
  telemetryEls.nodeM1,
  telemetryEls.nodeM2,
  telemetryEls.nodeM3,
  telemetryEls.nodeM4
].filter(Boolean);
 
const sensorStatusEls = [
  telemetryEls.sensorGps,
  telemetryEls.sensorLidar,
  telemetryEls.sensorOpticalFlow,
  telemetryEls.sensorCompass,
  telemetryEls.sensorBattery,
  telemetryEls.sensorImu,
  telemetryEls.sensorBarometer
].filter(Boolean);
 
const initialEventLog = [
  { time: "12:45:10", text: "MODE CHANGE: POSHOLD" },
  { time: "12:44:50", text: "GPS FIX ACQUIRED" },
  { time: "12:44:20", text: "SYSTEM ARMED" },
  { time: "12:44:00", text: "PRE-FLIGHT CHECKS PASSED" }
];
 
const realModeEventLog = [
  { time: "00:00:00", text: "REAL MODE ACTIVE" },
  { time: "00:00:00", text: "WAITING FOR VEHICLE CONNECTION" }
];
 
let eventLogData = isRealMode ? [...realModeEventLog] : [...initialEventLog];
 
/* ---------------------------
   HELPERS
---------------------------- */
function pad(n) {
  return String(n).padStart(2, "0");
}
 
function rand(min, max) {
  return Math.random() * (max - min) + min;
}
 
function zeroSeries(length) {
  return Array(length).fill(0.02);
}
 
function updateStatusSelectClass() {
  if (!telemetryEls.statusSelect) return;
 
  telemetryEls.statusSelect.classList.remove(
    "status-armed",
    "status-prearm",
    "status-disarmed"
  );
 
  const value = telemetryEls.statusSelect.value;
  if (value === "armed") telemetryEls.statusSelect.classList.add("status-armed");
  if (value === "prearm") telemetryEls.statusSelect.classList.add("status-prearm");
  if (value === "disarmed") telemetryEls.statusSelect.classList.add("status-disarmed");
}
 
function setStatusSelectValue(value) {
  if (!telemetryEls.statusSelect) return;
  telemetryEls.statusSelect.value = value;
  updateStatusSelectClass();
}
 
function setElementState(el, text, isOnline) {
  if (!el) return;
  el.textContent = text;
  el.classList.remove("status-online", "status-offline");
  el.classList.add(isOnline ? "status-online" : "status-offline");
}
 
function setSensorState(el, text, isOnline) {
  if (!el) return;
  el.textContent = text;
  el.classList.remove("sensor-ok", "sensor-offline");
  el.classList.add(isOnline ? "sensor-ok" : "sensor-offline");
}
 
function openConfirm(message, onConfirm) {
  pendingConfirmAction = onConfirm;
  if (telemetryEls.confirmMessage) {
    telemetryEls.confirmMessage.textContent = message || "Are you sure?";
  }
  if (telemetryEls.confirmOverlay) {
    telemetryEls.confirmOverlay.classList.remove("hidden");
  }
}
 
function closeConfirm() {
  pendingConfirmAction = null;
  if (telemetryEls.confirmOverlay) {
    telemetryEls.confirmOverlay.classList.add("hidden");
  }
}
 
function getCurrentClockTime() {
  const now = new Date();
  return `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}
 
function prependEvent(text) {
  if (eventLogPaused) return;
  eventLogData.unshift({
    time: getCurrentClockTime(),
    text: text.toUpperCase()
  });
  if (eventLogData.length > 8) {
    eventLogData = eventLogData.slice(0, 8);
  }
  renderEventLog();
}
// Expose globally so launch mini panel and other modules can log events
window.prependEvent = prependEvent;
 
function renderEventLog() {
  if (!eventLogPanel) return;
 
  const titleBar = eventLogPanel.querySelector(".panel-title");
  const currentItems = eventLogPanel.querySelectorAll(".event-item");
  currentItems.forEach((item) => item.remove());
 
  if (!titleBar) return;
 
  const insertAfter = titleBar.closest(".panel-title-with-actions") || titleBar;
 
  for (let i = eventLogData.length - 1; i >= 0; i--) {
    const entry = eventLogData[i];
    const row = document.createElement("div");
    row.className = "event-item";
 
    const timeSpan = document.createElement("span");
    timeSpan.textContent = entry.time;
 
    const textSpan = document.createElement("span");
    textSpan.textContent = entry.text;
 
    row.appendChild(timeSpan);
    row.appendChild(textSpan);
    insertAfter.insertAdjacentElement("afterend", row);
  }
}
 
function setFlightCommandState(buttonEl, labelText) {
  flightButtons.forEach((btn) => btn.classList.remove("active-command"));
 
  if (buttonEl) {
    buttonEl.classList.add("active-command");
    currentActiveFlightButton = buttonEl;
  } else {
    currentActiveFlightButton = null;
  }
 
  if (telemetryEls.flightActionIndicator) {
    if (labelText) {
      telemetryEls.flightActionIndicator.textContent = labelText;
      telemetryEls.flightActionIndicator.classList.remove("hidden");
    } else {
      telemetryEls.flightActionIndicator.textContent = "";
      telemetryEls.flightActionIndicator.classList.add("hidden");
    }
  }
 
  if (telemetryEls.flightModeSelect) {
    telemetryEls.flightModeSelect.classList.remove("blink-red");
    if (labelText) {
      telemetryEls.flightModeSelect.classList.add("blink-red");
    }
  }
}
 
function normalizeCommandToMode(command) {
  switch (command) {
    case "LAND":
      return "LAND";
    case "HOLD":
      return "LOITER";
    case "RETURN TO BASE":
      return "RTL";
    case "GUIDED":
      return "GUIDED";
    case "MISSION START":
      return "AUTO";
    case "TAKEOFF":
      return "GUIDED";
    case "EMERGENCY STOP":
      return null;
    case "ARM / DISARM":
      return null;
    default:
      return null;
  }
}
 
function executeFlightCommand(buttonEl) {
  const command = buttonEl.dataset.command || "COMMAND";
  const mappedMode = normalizeCommandToMode(command);

  if (isViewerMode) return;

  if (isRealMode) {
    let sent = false;
    switch (command) {
      case "ARM / DISARM": {
        const armedNow = (telemetryEls.statusSelect &&
                          telemetryEls.statusSelect.value === "armed");
        sent = sendCmd({ action: armedNow ? "disarm" : "arm" });
        break;
      }
      case "TAKEOFF":        sent = sendCmd({ action: "takeoff", alt: 2.0 }); break;
      case "LAND":           sent = sendCmd({ action: "set_land" });         break;
      case "HOLD":           sent = sendCmd({ action: "set_loiter" });       break;
      case "RETURN TO BASE": sent = sendCmd({ action: "set_rtl" });          break;
      case "MISSION START":  sent = sendCmd({ action: "set_auto" });         break;
      case "GUIDED":         sent = sendCmd({ action: "set_guided" });       break;
      case "EMERGENCY STOP": sent = sendCmd({ action: "disarm" });           break;
      default:               sent = false;
    }
    setFlightCommandState(buttonEl, command);
    prependEvent(sent ? `CMD SENT: ${command}` : `CMD FAILED: ${command}`);
    return;
  }

  // Demo mode: original local-only behaviour
  if (command === "ARM / DISARM") {
    if (telemetryEls.statusSelect) {
      telemetryEls.statusSelect.value =
        telemetryEls.statusSelect.value === "armed" ? "disarmed" : "armed";
      updateStatusSelectClass();
    }
  }
  if (mappedMode && telemetryEls.flightModeSelect) {
    telemetryEls.flightModeSelect.value = mappedMode;
  }
  if (telemetryEls.mapModeVal && mappedMode) {
    telemetryEls.mapModeVal.textContent = mappedMode;
  }
  setFlightCommandState(buttonEl, command);
  prependEvent(`COMMAND ACCEPTED: ${command}`);
}
 
function bindConfirmButtons() {
  if (telemetryEls.confirmCancel) {
    telemetryEls.confirmCancel.addEventListener("click", closeConfirm);
  }
 
  if (telemetryEls.confirmOk) {
    telemetryEls.confirmOk.addEventListener("click", () => {
      const action = pendingConfirmAction;
      closeConfirm();
      if (typeof action === "function") {
        action();
      }
    });
  }
 
  if (telemetryEls.confirmOverlay) {
    telemetryEls.confirmOverlay.addEventListener("click", (e) => {
      if (e.target === telemetryEls.confirmOverlay) {
        closeConfirm();
      }
    });
  }
}
 
function sendModeCommand(modeName) {
  const m = (modeName || "").toUpperCase().trim();
  return sendCmd({ action: "set_mode", mode: m });
}

function bindTopbarControls() {
  if (telemetryEls.statusSelect) {
    telemetryEls.statusSelect.addEventListener("change", () => {
      updateStatusSelectClass();
      prependEvent(`STATUS SET: ${telemetryEls.statusSelect.options[telemetryEls.statusSelect.selectedIndex].text}`);
    });
    updateStatusSelectClass();
  }
 
	  if (telemetryEls.flightModeSelect) {
	    let lastModeValue = telemetryEls.flightModeSelect.value;
	    telemetryEls.flightModeSelect.addEventListener("focus", () => {
	      lastModeValue = telemetryEls.flightModeSelect.value;
	    });
	    telemetryEls.flightModeSelect.addEventListener("change", () => {
	      const chosen = telemetryEls.flightModeSelect.value;
	      if (isViewerMode) {
	        telemetryEls.flightModeSelect.value = lastModeValue;
	        return;
	      }
	      if (!isRealMode) {
	        if (telemetryEls.mapModeVal) telemetryEls.mapModeVal.textContent = chosen;
	        prependEvent(`MODE CHANGE: ${chosen}`);
	        lastModeValue = chosen;
        return;
      }
      // Real mode: confirm, and revert the dropdown until telemetry confirms.
      const revert = lastModeValue;
      telemetryEls.flightModeSelect.value = revert;
      openConfirm(`Change flight mode to ${chosen}?`, () => {
        if (sendModeCommand(chosen)) prependEvent(`MODE CMD SENT: ${chosen}`);
      });
    });
  }
}
 
function bindFailsafeControls() {
	  failsafeSelects.forEach((select) => {
	    select.addEventListener("change", () => {
	      const label = select.closest(".failsafe-row")?.querySelector("span")?.textContent || "FAILSAFE";
	      prependEvent(`${label} SET TO ${select.value}`);
	    });
	  });
}

function isViewerReadOnly() {
  return isViewerMode;
}

window.airsentIsViewer = isViewerReadOnly;

let pendingFlightSliderButton = null;
let flightSliderIdleTimer = null;
let flightSliderOutsideArmed = false;

function resetFlightSliderIdle() {
  if (flightSliderIdleTimer) clearTimeout(flightSliderIdleTimer);
  flightSliderIdleTimer = setTimeout(closeFlightSlider, 5000);
}

function resetFlightSliderPosition() {
  const knob = document.getElementById("flightSlideKnob");
  const fill = document.getElementById("flightSlideFill");
  if (knob) {
    knob.style.left = "3px";
    knob.classList.remove("dragging");
  }
  if (fill) fill.style.width = "0%";
}

function closeFlightSlider() {
  const box = document.getElementById("flightSlideConfirm");
  if (box) box.classList.add("hidden");
  pendingFlightSliderButton = null;
  resetFlightSliderPosition();
  if (flightSliderIdleTimer) {
    clearTimeout(flightSliderIdleTimer);
    flightSliderIdleTimer = null;
  }
  flightSliderOutsideArmed = false;
  document.removeEventListener("pointerdown", handleFlightSliderOutside, true);
}

function handleFlightSliderOutside(ev) {
  if (!flightSliderOutsideArmed) return;
  const box = document.getElementById("flightSlideConfirm");
  if (!box || box.classList.contains("hidden")) return;
  if (box.contains(ev.target) || ev.target.closest(".flight-btn")) return;
  closeFlightSlider();
}

function openFlightSlider(buttonEl) {
  const box = document.getElementById("flightSlideConfirm");
  const title = document.getElementById("flightSlideTitle");
  if (!box || !buttonEl) return;
  closeFlightSlider();
  pendingFlightSliderButton = buttonEl;
  const command = buttonEl.dataset.command || "COMMAND";
  if (title) title.textContent = command === "EMERGENCY STOP" ? "EMERGENCY STOP" : `CONFIRM ${command}`;
  box.classList.remove("hidden");
  resetFlightSliderPosition();
  resetFlightSliderIdle();
  setTimeout(() => {
    flightSliderOutsideArmed = true;
    document.addEventListener("pointerdown", handleFlightSliderOutside, true);
  }, 0);
}

function bindFlightSlider() {
  const box = document.getElementById("flightSlideConfirm");
  const closeBtn = document.getElementById("flightSlideClose");
  const track = document.getElementById("flightSlideTrack");
  const knob = document.getElementById("flightSlideKnob");
  const fill = document.getElementById("flightSlideFill");
  if (!box || !track || !knob || !fill) return;

  let dragging = false;
  let startX = 0;
  let startLeft = 3;
  const pad = 3;

  const maxX = () => Math.max(pad, track.clientWidth - knob.offsetWidth - pad);
  const setX = (x) => {
    const mx = maxX();
    const left = Math.max(pad, Math.min(mx, x));
    knob.style.left = `${left}px`;
    fill.style.width = `${((left - pad) / Math.max(1, mx - pad)) * 100}%`;
    return left >= mx - 2;
  };

  box.addEventListener("pointerdown", resetFlightSliderIdle);
  box.addEventListener("pointermove", resetFlightSliderIdle);
  closeBtn?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    closeFlightSlider();
  });

  knob.addEventListener("pointerdown", (ev) => {
    if (!pendingFlightSliderButton) return;
    ev.preventDefault();
    ev.stopPropagation();
    resetFlightSliderIdle();
    dragging = true;
    startX = ev.clientX;
    startLeft = parseFloat(knob.style.left) || pad;
    knob.classList.add("dragging");
    knob.setPointerCapture?.(ev.pointerId);
  });

  knob.addEventListener("pointermove", (ev) => {
    if (!dragging) return;
    ev.preventDefault();
    resetFlightSliderIdle();
    const done = setX(startLeft + ev.clientX - startX);
    if (done) {
      const button = pendingFlightSliderButton;
      closeFlightSlider();
      if (button) executeFlightCommand(button);
    }
  });

  knob.addEventListener("pointerup", (ev) => {
    if (!dragging) return;
    dragging = false;
    knob.classList.remove("dragging");
    knob.releasePointerCapture?.(ev.pointerId);
    if (!document.getElementById("flightSlideConfirm")?.classList.contains("hidden")) {
      resetFlightSliderPosition();
      resetFlightSliderIdle();
    }
  });
}
	 
function bindFlightButtons() {
  flightButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      if (isViewerMode) return;
      openFlightSlider(btn);
    });
  });
  bindFlightSlider();
}
 
function bindMissionButtons() {
  if (telemetryEls.missionPauseBtn) {
    telemetryEls.missionPauseBtn.addEventListener("click", () => {
      openConfirm(
        missionTimerPaused ? "Resume mission timer?" : "Pause mission timer?",
        () => {
          missionTimerPaused = !missionTimerPaused;
          telemetryEls.missionPauseBtn.textContent = missionTimerPaused ? "▶" : "⏸";
          prependEvent(missionTimerPaused ? "MISSION TIMER PAUSED" : "MISSION TIMER RESUMED");
        }
      );
    });
  }
 
  if (telemetryEls.missionResetBtn) {
    telemetryEls.missionResetBtn.addEventListener("click", () => {
      openConfirm("Reset mission timer?", () => {
        missionSeconds = 0;
        missionTimerPaused = false;
        if (telemetryEls.missionPauseBtn) {
          telemetryEls.missionPauseBtn.textContent = "⏸";
        }
        if (telemetryEls.missionTime) {
          telemetryEls.missionTime.textContent = "00:00:00";
        }
        prependEvent("MISSION TIMER RESET");
      });
    });
  }
}
 
function bindEventButtons() {
  if (telemetryEls.eventsPauseBtn) {
    telemetryEls.eventsPauseBtn.addEventListener("click", () => {
      openConfirm(
        eventLogPaused ? "Resume event log updates?" : "Pause event log updates?",
        () => {
          eventLogPaused = !eventLogPaused;
          telemetryEls.eventsPauseBtn.textContent = eventLogPaused ? "▶" : "⏸";
        }
      );
    });
  }
 
  if (telemetryEls.eventsResetBtn) {
    telemetryEls.eventsResetBtn.addEventListener("click", () => {
      openConfirm("Clear event log?", () => {
        eventLogData = isRealMode ? [...realModeEventLog] : [];
        renderEventLog();
      });
    });
  }
}
 
function clearMotorVisualStates() {
  motorCards.forEach((card) => {
    card.classList.remove("warn-card", "hot-card", "motor-fault");
  });
 
  motorNodes.forEach((node) => {
    node.classList.remove("motor-fault");
  });
}
 
function applyMotorFaultState(pwmVals, outVals) {
  const numericOutVals = outVals.map((v) => {
    if (typeof v === "string") return parseInt(v.replace("%", ""), 10);
    return Number(v);
  });
 
  const average = Math.round(numericOutVals.reduce((a, b) => a + b, 0) / numericOutVals.length);
  const spread = Math.max(...numericOutVals) - Math.min(...numericOutVals);
 
  let highestIndex = 0;
  let lowestIndex = 0;
 
  numericOutVals.forEach((value, index) => {
    if (value > numericOutVals[highestIndex]) highestIndex = index;
    if (value < numericOutVals[lowestIndex]) lowestIndex = index;
  });
 
  if (telemetryEls.diagAverage) telemetryEls.diagAverage.textContent = `${average}%`;
  if (telemetryEls.diagSpread) telemetryEls.diagSpread.textContent = `${spread}%`;
  if (telemetryEls.diagHighest) telemetryEls.diagHighest.textContent = `M${highestIndex + 1} @ ${pwmVals[highestIndex]} μs`;
  if (telemetryEls.diagLowest) telemetryEls.diagLowest.textContent = `M${lowestIndex + 1} @ ${pwmVals[lowestIndex]} μs`;
 
  let overallFaultDetected = false;
  let oscillationState = "LOW";
  let statusText = "NO WARNINGS";
  let failsafeText = "NORMAL";
 
  numericOutVals.forEach((output, i) => {
    const card = motorCards[i] || null;
    const node = motorNodes[i] || null;
 
    if (card) {
      card.classList.remove("warn-card", "hot-card", "motor-fault");
    }
    if (node) {
      node.classList.remove("motor-fault");
    }
 
    if (card && output >= average + 4) {
      card.classList.add("warn-card");
    }
 
    if (card && output >= average + 7) {
      card.classList.add("hot-card");
    }
 
    const faultDetected =
      output > 85 ||
      pwmVals[i] < 1050 ||
      pwmVals[i] > 1950;
 
    if (faultDetected) {
      overallFaultDetected = true;
      if (card) card.classList.add("motor-fault");
      if (node) node.classList.add("motor-fault");
    }
  });
 
  if (spread >= 8) {
    oscillationState = "MED";
    statusText = "OUTPUT IMBALANCE";
  }
 
  if (spread >= 12) {
    oscillationState = "HIGH";
    statusText = "HIGH SPREAD";
    failsafeText = "WATCH";
  }
 
  if (overallFaultDetected) {
    oscillationState = "HIGH";
    statusText = "MOTOR FAULT";
    failsafeText = "MOTOR";
    if (typeof triggerFaultAlert === 'function') triggerFaultAlert('fault', 'motor', 'MOTOR FAULT — Check motor outputs');
  } else {
    if (typeof clearFaultAlert === 'function') clearFaultAlert('motor');
  }
 
  if (telemetryEls.diagOscillation) telemetryEls.diagOscillation.textContent = oscillationState;
  if (telemetryEls.diagStatus) telemetryEls.diagStatus.textContent = statusText;
  if (telemetryEls.failsafeStateTop) telemetryEls.failsafeStateTop.textContent = failsafeText;
  if (telemetryEls.summaryFailsafe) telemetryEls.summaryFailsafe.textContent = failsafeText;
}
 
function applyRealModeZeroState() {
  setStatusSelectValue("disarmed");
 
  if (telemetryEls.topVoltage) telemetryEls.topVoltage.textContent = "0.0V";
  if (telemetryEls.topCurrent) telemetryEls.topCurrent.textContent = "0.0A";
  if (telemetryEls.topBattery) telemetryEls.topBattery.textContent = "0%";
  if (telemetryEls.cpuLoadTop) telemetryEls.cpuLoadTop.textContent = "0%";
 
  setElementState(telemetryEls.gpsFixTop, "NO FIX", false);
  if (telemetryEls.satCountTop) telemetryEls.satCountTop.textContent = "0";
  setElementState(telemetryEls.ekfTop, "OFFLINE", false);
  setElementState(telemetryEls.linkTop, "NO SIGNAL", false);
 
  if (telemetryEls.rollVal) telemetryEls.rollVal.textContent = "0.0°";
  if (telemetryEls.pitchVal) telemetryEls.pitchVal.textContent = "0.0°";
  if (telemetryEls.headingVal) telemetryEls.headingVal.textContent = "0°";
  if (telemetryEls.altVal) telemetryEls.altVal.textContent = "0.0 m";
  if (telemetryEls.spdVal) telemetryEls.spdVal.textContent = "0.0 m/s";
 
  if (telemetryEls.m1pwm) telemetryEls.m1pwm.textContent = "0";
  if (telemetryEls.m2pwm) telemetryEls.m2pwm.textContent = "0";
  if (telemetryEls.m3pwm) telemetryEls.m3pwm.textContent = "0";
  if (telemetryEls.m4pwm) telemetryEls.m4pwm.textContent = "0";
 
  if (telemetryEls.m1out) telemetryEls.m1out.textContent = "0%";
  if (telemetryEls.m2out) telemetryEls.m2out.textContent = "0%";
  if (telemetryEls.m3out) telemetryEls.m3out.textContent = "0%";
  if (telemetryEls.m4out) telemetryEls.m4out.textContent = "0%";
 
  if (telemetryEls.quadM1Pwm) telemetryEls.quadM1Pwm.textContent = "0";
  if (telemetryEls.quadM2Pwm) telemetryEls.quadM2Pwm.textContent = "0";
  if (telemetryEls.quadM3Pwm) telemetryEls.quadM3Pwm.textContent = "0";
  if (telemetryEls.quadM4Pwm) telemetryEls.quadM4Pwm.textContent = "0";
 
  if (telemetryEls.quadM1Out) telemetryEls.quadM1Out.textContent = "0%";
  if (telemetryEls.quadM2Out) telemetryEls.quadM2Out.textContent = "0%";
  if (telemetryEls.quadM3Out) telemetryEls.quadM3Out.textContent = "0%";
  if (telemetryEls.quadM4Out) telemetryEls.quadM4Out.textContent = "0%";
 
  if (telemetryEls.m1bar) telemetryEls.m1bar.style.width = "0%";
  if (telemetryEls.m2bar) telemetryEls.m2bar.style.width = "0%";
  if (telemetryEls.m3bar) telemetryEls.m3bar.style.width = "0%";
  if (telemetryEls.m4bar) telemetryEls.m4bar.style.width = "0%";
 
  if (telemetryEls.diagAverage) telemetryEls.diagAverage.textContent = "0%";
  if (telemetryEls.diagSpread) telemetryEls.diagSpread.textContent = "0%";
  if (telemetryEls.diagHighest) telemetryEls.diagHighest.textContent = "N/A";
  if (telemetryEls.diagLowest) telemetryEls.diagLowest.textContent = "N/A";
  if (telemetryEls.diagOscillation) telemetryEls.diagOscillation.textContent = "N/A";
  if (telemetryEls.diagStatus) telemetryEls.diagStatus.textContent = "NO TELEMETRY";
  if (telemetryEls.failsafeStateTop) telemetryEls.failsafeStateTop.textContent = "DISCONNECTED";
  if (telemetryEls.summaryFailsafe) telemetryEls.summaryFailsafe.textContent = "DISCONNECTED";
 
  if (telemetryEls.compassHeadingText) telemetryEls.compassHeadingText.textContent = "0°";
  if (telemetryEls.miniCompassArrow) {
    telemetryEls.miniCompassArrow.style.transform = "translate(-50%, -50%) rotate(0deg)";
  }
 
  if (telemetryEls.horizonDisc) {
    telemetryEls.horizonDisc.style.transform = "rotate(0deg) translateY(0px)";
  }
 
  if (telemetryEls.latVal) telemetryEls.latVal.textContent = "0.0000°";
  if (telemetryEls.lonVal) telemetryEls.lonVal.textContent = "0.0000°";
 
  if (telemetryEls.missionDistanceVal) telemetryEls.missionDistanceVal.textContent = "0.0";
  if (telemetryEls.ltVal) telemetryEls.ltVal.textContent = "0";
  if (telemetryEls.mapModeVal) telemetryEls.mapModeVal.textContent = "DISCONNECTED";
  if (telemetryEls.positionVal) telemetryEls.positionVal.textContent = "NO DATA";
  if (telemetryEls.mslVal) telemetryEls.mslVal.textContent = "0.0m";
 
  if (telemetryEls.rcTelemetryState) {
    telemetryEls.rcTelemetryState.textContent = "NO SIGNAL";
    telemetryEls.rcTelemetryState.classList.remove("green-text");
    telemetryEls.rcTelemetryState.classList.add("status-offline");
  }
  if (telemetryEls.rssiVal) telemetryEls.rssiVal.textContent = "0";
 
  setSensorState(telemetryEls.sensorGps, "OFFLINE", false);
  setSensorState(telemetryEls.sensorLidar, "OFFLINE", false);
  setSensorState(telemetryEls.sensorOpticalFlow, "OFFLINE", false);
  setSensorState(telemetryEls.sensorCompass, "OFFLINE", false);
  setSensorState(telemetryEls.sensorBattery, "OFFLINE", false);
  setSensorState(telemetryEls.sensorImu, "OFFLINE", false);
  setSensorState(telemetryEls.sensorBarometer, "OFFLINE", false);
 
  clearMotorVisualStates();
 
  if (map) {
    map.setView([39.8283, -98.5795], 4);
  }
 
  if (droneMarker) {
    droneMarker.setLatLng([39.8283, -98.5795]);
  }
 
  if (homeMarker) {
    homeMarker.setLatLng([39.8283, -98.5795]);
  }
 
  if (trailLine) {
    trailLine.setLatLngs([[39.8283, -98.5795]]);
  }
}
 
/* ---------------------------
   LEAFLET MAP SETUP
---------------------------- */
const initialLat = 33.4242;
const initialLon = -111.9281;
const usaLat = 39.8283;
const usaLon = -98.5795;
 
let map = null;
let droneMarker = null;
let homeMarker = null;
let trailLine = null;
const trailCoords = [[initialLat, initialLon]];
 
const leafletMapEl = document.getElementById("leafletMap");
 
if (leafletMapEl && typeof L !== "undefined") {
  map = L.map("leafletMap", {
    zoomControl: true,
  }).setView(isRealMode ? [usaLat, usaLon] : [initialLat, initialLon], isRealMode ? 4 : 16);
 
  window.airsentMapLayer(map);
 
  droneMarker = L.circleMarker(isRealMode ? [usaLat, usaLon] : [initialLat, initialLon], {
    radius: 7,
    color: "#7fdcff",
    weight: 2,
    fillColor: "#7fdcff",
    fillOpacity: 0.9
  }).addTo(map);
 
  homeMarker = L.circleMarker(isRealMode ? [usaLat, usaLon] : [initialLat - 0.0005, initialLon - 0.0005], {
    radius: 6,
    color: "#62ff9e",
    weight: 2,
    fillColor: "#62ff9e",
    fillOpacity: 0.85
  }).addTo(map);
 
  trailLine = L.polyline(isRealMode ? [[usaLat, usaLon]] : trailCoords, {
    color: "#f0bc59",
    weight: 3
  }).addTo(map);
 
  // Expose on window so telemetry.js can access them
  window.map = map;
  window.droneMarker = droneMarker;
  window.homeMarker = homeMarker;
  window.trailLine = trailLine;
}
 
/* ---------------------------
   TELEMETRY UPDATE
---------------------------- */
function updateTelemetry() {
  if (!missionTimerPaused) {
    missionSeconds++;
  }
 
  const hours = Math.floor(missionSeconds / 3600);
  const minutes = Math.floor((missionSeconds % 3600) / 60);
  const seconds = missionSeconds % 60;
 
  if (telemetryEls.missionTime) {
    telemetryEls.missionTime.textContent = `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
  }
 
  if (isRealMode) {
    if (typeof socket === "undefined" || !socket || socket.readyState !== WebSocket.OPEN) {
      applyRealModeZeroState();
    }
    return;
  }
 
  const voltage = rand(15.6, 16.0).toFixed(1);
  const current = rand(9.8, 11.4).toFixed(1);
  const battery = Math.floor(rand(69, 73));
  const roll = rand(-4, 4).toFixed(1);
  const pitch = rand(-3, 3).toFixed(1);
  const heading = Math.floor(rand(118, 124));
  const altitude = rand(84.2, 87.1).toFixed(1);
  const speed = rand(5.3, 6.4).toFixed(1);
  const cpu = `${Math.floor(rand(20, 28))}%`;
  const missionDistance = rand(22.4, 23.9).toFixed(1);
  const ltValue = Math.floor(rand(10, 13));
  const rssiValue = rand(82.0, 86.0).toFixed(1);
 
  if (telemetryEls.topVoltage) telemetryEls.topVoltage.textContent = `${voltage}V`;
  if (telemetryEls.topCurrent) telemetryEls.topCurrent.textContent = `${current}A`;
  if (telemetryEls.topBattery) telemetryEls.topBattery.textContent = `${battery}%`;
  if (telemetryEls.cpuLoadTop) telemetryEls.cpuLoadTop.textContent = cpu;
 
  setElementState(telemetryEls.gpsFixTop, "RTK FIX", true);
  if (telemetryEls.satCountTop) telemetryEls.satCountTop.textContent = "19";
  setElementState(telemetryEls.ekfTop, "HEALTHY", true);
  setElementState(telemetryEls.linkTop, "GOOD", true);
 
  if (telemetryEls.rollVal) telemetryEls.rollVal.textContent = `${roll > 0 ? "+" : ""}${roll}°`;
  if (telemetryEls.pitchVal) telemetryEls.pitchVal.textContent = `${pitch > 0 ? "+" : ""}${pitch}°`;
  if (telemetryEls.headingVal) telemetryEls.headingVal.textContent = `${heading}°`;
  if (telemetryEls.altVal) telemetryEls.altVal.textContent = `${altitude} m`;
  if (telemetryEls.spdVal) telemetryEls.spdVal.textContent = `${speed} m/s`;
 
  if (telemetryEls.compassHeadingText) {
    telemetryEls.compassHeadingText.textContent = `${heading}°`;
  }
 
  if (telemetryEls.miniCompassArrow) {
    telemetryEls.miniCompassArrow.style.transform = `translate(-50%, -50%) rotate(${heading}deg)`;
  }
 
  if (telemetryEls.missionDistanceVal) telemetryEls.missionDistanceVal.textContent = missionDistance;
  if (telemetryEls.ltVal) telemetryEls.ltVal.textContent = `${ltValue}`;
  if (telemetryEls.mapModeVal && telemetryEls.flightModeSelect) telemetryEls.mapModeVal.textContent = telemetryEls.flightModeSelect.value;
  if (telemetryEls.positionVal) telemetryEls.positionVal.textContent = "17.20 WAYPOINT";
  if (telemetryEls.mslVal) telemetryEls.mslVal.textContent = "-1.5m";
 
  if (telemetryEls.rcTelemetryState) {
    telemetryEls.rcTelemetryState.textContent = "LINK GOOD";
    telemetryEls.rcTelemetryState.classList.remove("status-offline");
    telemetryEls.rcTelemetryState.classList.add("green-text", "status-online");
  }
  if (telemetryEls.rssiVal) telemetryEls.rssiVal.textContent = rssiValue;
 
  setSensorState(telemetryEls.sensorGps, "NOMINAL", true);
  setSensorState(telemetryEls.sensorLidar, "NOMINAL", true);
  setSensorState(telemetryEls.sensorOpticalFlow, "NOMINAL", true);
  setSensorState(telemetryEls.sensorCompass, "NOMINAL", true);
  setSensorState(telemetryEls.sensorBattery, "NOMINAL", true);
  setSensorState(telemetryEls.sensorImu, "NOMINAL", true);
  setSensorState(telemetryEls.sensorBarometer, "NOMINAL", true);
 
  const pwmVals = [
    Math.floor(rand(1465, 1495)),
    Math.floor(rand(1470, 1500)),
    Math.floor(rand(1460, 1490)),
    Math.floor(rand(1472, 1502)),
  ];
 
  const numericOutVals = [
    Math.floor(rand(39, 43)),
    Math.floor(rand(40, 44)),
    Math.floor(rand(38, 42)),
    Math.floor(rand(41, 45)),
  ];
 
  const outVals = numericOutVals.map((v) => `${v}%`);
 
  if (telemetryEls.m1pwm) telemetryEls.m1pwm.textContent = pwmVals[0];
  if (telemetryEls.m2pwm) telemetryEls.m2pwm.textContent = pwmVals[1];
  if (telemetryEls.m3pwm) telemetryEls.m3pwm.textContent = pwmVals[2];
  if (telemetryEls.m4pwm) telemetryEls.m4pwm.textContent = pwmVals[3];
 
  if (telemetryEls.m1out) telemetryEls.m1out.textContent = outVals[0];
  if (telemetryEls.m2out) telemetryEls.m2out.textContent = outVals[1];
  if (telemetryEls.m3out) telemetryEls.m3out.textContent = outVals[2];
  if (telemetryEls.m4out) telemetryEls.m4out.textContent = outVals[3];
 
  if (telemetryEls.quadM1Pwm) telemetryEls.quadM1Pwm.textContent = pwmVals[0];
  if (telemetryEls.quadM2Pwm) telemetryEls.quadM2Pwm.textContent = pwmVals[1];
  if (telemetryEls.quadM3Pwm) telemetryEls.quadM3Pwm.textContent = pwmVals[2];
  if (telemetryEls.quadM4Pwm) telemetryEls.quadM4Pwm.textContent = pwmVals[3];
 
  if (telemetryEls.quadM1Out) telemetryEls.quadM1Out.textContent = outVals[0];
  if (telemetryEls.quadM2Out) telemetryEls.quadM2Out.textContent = outVals[1];
  if (telemetryEls.quadM3Out) telemetryEls.quadM3Out.textContent = outVals[2];
  if (telemetryEls.quadM4Out) telemetryEls.quadM4Out.textContent = outVals[3];
 
  if (telemetryEls.m1bar) telemetryEls.m1bar.style.width = `${numericOutVals[0]}%`;
  if (telemetryEls.m2bar) telemetryEls.m2bar.style.width = `${numericOutVals[1]}%`;
  if (telemetryEls.m3bar) telemetryEls.m3bar.style.width = `${numericOutVals[2]}%`;
  if (telemetryEls.m4bar) telemetryEls.m4bar.style.width = `${numericOutVals[3]}%`;
 
  applyMotorFaultState(pwmVals, numericOutVals);
 
  const rollNum = parseFloat(roll);
  const pitchNum = parseFloat(pitch);
  if (telemetryEls.horizonDisc) {
    telemetryEls.horizonDisc.style.transform = `rotate(${rollNum * 1.7}deg) translateY(${pitchNum * 2.6}px)`;
  }
 
  const newLat = initialLat + rand(-0.0008, 0.0008);
  const newLon = initialLon + rand(-0.0008, 0.0008);
 
  if (map) {
    map.setView([newLat, newLon], 16);
  }
 
  if (droneMarker) {
    droneMarker.setLatLng([newLat, newLon]);
  }
 
  if (homeMarker) {
    homeMarker.setLatLng([initialLat - 0.0005, initialLon - 0.0005]);
  }
 
  if (trailLine) {
    trailCoords.push([newLat, newLon]);
    if (trailCoords.length > 25) {
      trailCoords.shift();
    }
    trailLine.setLatLngs(trailCoords);
  }
 
  if (telemetryEls.latVal) telemetryEls.latVal.textContent = `${newLat.toFixed(4)}°`;
  if (telemetryEls.lonVal) telemetryEls.lonVal.textContent = `${newLon.toFixed(4)}°`;
}
 
/* ---------------------------
   CHARTS
---------------------------- */
function randomSeries(length, base = 0.5, variation = 0.15) {
  const arr = [];
  let current = base;
 
  for (let i = 0; i < length; i++) {
    current += (Math.random() - 0.5) * variation;
    current = Math.max(0.08, Math.min(0.92, current));
    arr.push(current);
  }
 
  return arr;
}
 
function drawChart(canvasId, lines, gridX = 10, gridY = 6) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
 
  function render() {
    const rect = canvas.getBoundingClientRect();
    const width = rect.width;
    const height = rect.height;
 
    if (width <= 0 || height <= 0) return;
 
    canvas.width = width * window.devicePixelRatio;
    canvas.height = height * window.devicePixelRatio;
    ctx.setTransform(window.devicePixelRatio, 0, 0, window.devicePixelRatio, 0, 0);
 
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#03070c";
    ctx.fillRect(0, 0, width, height);
 
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = 1;
 
    for (let x = 0; x <= gridX; x++) {
      const px = (x / gridX) * width;
      ctx.beginPath();
      ctx.moveTo(px, 0);
      ctx.lineTo(px, height);
      ctx.stroke();
    }
 
    for (let y = 0; y <= gridY; y++) {
      const py = (y / gridY) * height;
      ctx.beginPath();
      ctx.moveTo(0, py);
      ctx.lineTo(width, py);
      ctx.stroke();
    }
 
    for (const line of lines) {
      ctx.beginPath();
      ctx.strokeStyle = line.color;
      ctx.lineWidth = line.width || 2;
 
      line.data.forEach((v, i) => {
        const x = (i / (line.data.length - 1)) * width;
        const y = height - v * height;
 
        if (i === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      });
 
      ctx.stroke();
    }
  }
 
  render();
  window.addEventListener("resize", render);
}
 
drawChart("chart1", [
  { color: "#7fdcff", data: isRealMode ? zeroSeries(24) : randomSeries(24, 0.44, 0.22) },
  { color: "#bfff88", data: isRealMode ? zeroSeries(24) : randomSeries(24, 0.50, 0.25) }
]);
 
drawChart("chart2", [
  { color: "#f0bc59", data: isRealMode ? zeroSeries(24) : randomSeries(24, 0.60, 0.18) },
  { color: "#7fdcff", data: isRealMode ? zeroSeries(24) : randomSeries(24, 0.32, 0.10) }
]);
 
drawChart("chart3", [
  { color: "#c8ff80", data: isRealMode ? zeroSeries(24) : randomSeries(24, 0.62, 0.08) },
  { color: "#7fdcff", data: isRealMode ? zeroSeries(24) : randomSeries(24, 0.40, 0.08) }
]);
 
drawChart("chart4", [
  { color: "#bfff88", data: isRealMode ? zeroSeries(24) : randomSeries(24, 0.54, 0.12) },
  { color: "#7fdcff", data: isRealMode ? zeroSeries(24) : randomSeries(24, 0.28, 0.06) }
]);
 
drawChart("chart5", [
  { color: "#ffffff", data: isRealMode ? zeroSeries(36) : randomSeries(36, 0.40, 0.45), width: 1.4 }
], 12, 5);
 
drawChart("chart6", [
  { color: "#bfff88", data: isRealMode ? zeroSeries(80) : randomSeries(80, 0.62, 0.10) },
  { color: "#7fdcff", data: isRealMode ? zeroSeries(80) : randomSeries(80, 0.36, 0.07) },
  { color: "#ff674e", data: isRealMode ? zeroSeries(80) : randomSeries(80, 0.20, 0.12) }
], 18, 5);
 
drawChart("chart7", [
  { color: "#7fdcff", data: isRealMode ? zeroSeries(80) : randomSeries(80, 0.32, 0.05) },
  { color: "#bfff88", data: isRealMode ? zeroSeries(80) : randomSeries(80, 0.60, 0.12) },
  { color: "#ffffff", data: isRealMode ? zeroSeries(80) : randomSeries(80, 0.22, 0.10) }
], 18, 5);
 
/* ---------------------------
   INIT
---------------------------- */
bindConfirmButtons();
connectCmd();
bindTopbarControls();
bindFailsafeControls();
bindFlightButtons();
bindMissionButtons();
bindEventButtons();
renderEventLog();
 
updateTelemetry();
setInterval(updateTelemetry, 1000);
