/* Keep branding and navigation physically consistent across scaled console layouts. */
(()=>{function sync(){document.querySelectorAll('.logo-wrap,.logo-dropdown').forEach(w=>{const width=w.offsetWidth;if(width){const scale=w.getBoundingClientRect().width/width;if(Number.isFinite(scale)&&scale>0)w.style.setProperty('--nav-scale',scale.toFixed(5))}})}
function schedule(){requestAnimationFrame(()=>requestAnimationFrame(sync))}
addEventListener('DOMContentLoaded',schedule);addEventListener('load',schedule);addEventListener('resize',schedule);window.visualViewport?.addEventListener('resize',schedule);document.fonts?.ready.then(schedule);
})();

/* Also attach presence on cached console HTML that predates the script tag. */
(() => {
  const start = () => {
    if (window.airsentPresenceStarted || /(?:^|\/)index\.html$/.test(location.pathname) || location.pathname === '/') return;
    if (document.querySelector('script[src*="presence.js"]')) return;
    const script = document.createElement('script');
    script.src = 'presence.js?v=6';
    document.body.append(script);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
