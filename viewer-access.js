// Viewer controls are read-only; telemetry, cameras, navigation and exports remain available.
(() => {
  const viewer = () => localStorage.getItem('airsentMode') === 'viewer';
  const page = location.pathname.split('/').pop();
  const rules = {
    'dashboard.html': '#statusSelect,#flightModeSelect,.failsafe-row select,.flight-controls-grid button,#missionPauseBtn,#missionResetBtn,#lmBtnStart,#lmBtnHold,#lmBtnAbort,#lmBtnReset,#confirmOk',
    'pilot.html': '#pilotEstop,#btnAuto,#btnMan,#qa-takeoff,#qa-rth,#qa-land,#obstBadge,#wpBtn,.obstBadge,[onclick*="toggleObstDetection"],#qsPanel input,#qsPanel select,#slideConfirm',
    'mission.html': '#btnUpload,#btnDownload,#btnLoad,#btnClearAll,#btnReverse,#btnAutoAlt,#toolAddWp,#toolAddLoiter,#toolAddRTL,#toolAddLand,.wp-del-btn,#detailPanel input,#detailPanel select,#detailAlt,#detailCmd,#detailSpeed,#detailRadius',
    'parameters.html': '#btnWriteAll,#btnLoadFile,#btnResetMod,#dInput,.d-option,#dBtnSet,#dBtnReset',
    'preflight.html': '#btnClearFail,#btnReset,#btnResetHard,.check-item:not(.auto)',
    'failsafe.html': 'input,select,textarea,.fs-card-activation,#fsAutoToggle,#fsEditDraft,#fsTestDraft,#fsDiscardDraft,#fsAck,button[type="submit"],[data-policy-system]',
    'launch.html': '#lsCreate,#lsGo,#lsHold,#lsAbort,#lsClose,#lsClaim,.ls-manual-btn',
    'actuators.html': '.test-btn,#statusSelect,#flightModeSelect'
  };
  const selector = rules[page] || '';
  window.airsentViewerReadOnly = viewer;
  window.airsentMutationSelector = selector;
  function lock() {
    if (!viewer() || !selector) return;
    document.querySelectorAll(selector).forEach(el => {
      if ('disabled' in el) el.disabled = true;
      el.setAttribute('aria-disabled','true'); el.title = 'Viewer mode — read only';
    });
  }
  for (const eventName of ['click','input','change','submit','pointerdown']) document.addEventListener(eventName,event => {
    if (!viewer() || !selector || !event.target.closest?.(selector)) return;
    event.preventDefault(); event.stopImmediatePropagation();
  },true);
  document.addEventListener('DOMContentLoaded', () => {
    lock();
    if (!viewer() || ['/', '/index.html', '/signin.html'].includes(location.pathname)) return;
    const notice = document.createElement('div');
    notice.textContent='VIEWER · READ ONLY';
    notice.style.cssText='position:fixed;bottom:8px;left:8px;z-index:9999;background:#08151e;color:#8fcce5;padding:5px 9px;border:1px solid #284653;font:11px monospace;pointer-events:none';
    document.body.append(notice);
    new MutationObserver(lock).observe(document.body,{childList:true,subtree:true});
  });
})();
