/* nav-toggle.js — open/close behaviour for the topbar logo navigation menu.
   The menu was pure CSS :hover/:focus-within (styles.css "UNIVERSAL LOGO DROPDOWN FIX"),
   so the instant the pointer left the logo+menu box — crossing the gap under the
   logo, or a diagonal move that clipped the menu's edge — it vanished before an
   item could be clicked. This script drives an `nav-open` class instead:
     • hover opens it; leaving starts a CLOSE_DELAY grace timer, and re-entering
       the logo or the menu cancels it, so a sloppy path down to an item still lands;
     • click on the logo pins it open (click again to unpin/close);
     • click outside or Escape closes it.
   Class-name pairs cover both variants used across the console pages. */
(function () {
  var CLOSE_DELAY = 450;   /* ms the menu survives after the pointer leaves */
  var PAIRS = [
    ['.logo-dropdown', '.logo-dropdown-btn', '.logo-dropdown-menu'],
    ['.logo-wrap',     '.logo-btn',          '.logo-menu']
  ];

  /* Beats the stylesheet's `display:none !important` on specificity (0,3,0 vs 0,1,0). */
  var style = document.createElement('style');
  style.textContent =
    '.logo-dropdown.nav-open .logo-dropdown-menu,' +
    '.logo-wrap.nav-open .logo-menu{display:block !important;}' +
    /* While open, an invisible strip spans the gap between logo and menu. */
    '.logo-dropdown.nav-open::after,.logo-wrap.nav-open::after{content:"";position:absolute;' +
    'top:100%;left:0;width:260px;height:12px;}';
  document.head.appendChild(style);

  var wraps = [];

  function setOpen(wrap, open) {
    clearTimeout(wrap._navTimer);
    wrap.classList.toggle('nav-open', open);
    if (!open) wrap._navPinned = false;
    var b = wrap._navBtn;
    if (b) b.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function closeAll(except) {
    wraps.forEach(function (w) { if (w !== except) setOpen(w, false); });
  }

  PAIRS.forEach(function (p) {
    document.querySelectorAll(p[0]).forEach(function (wrap) {
      var btn = wrap.querySelector(p[1]);
      if (!btn) return;
      wrap._navBtn = btn;
      wraps.push(wrap);
      btn.setAttribute('aria-expanded', 'false');

      wrap.addEventListener('mouseenter', function () {
        closeAll(wrap);
        setOpen(wrap, true);
      });
      wrap.addEventListener('mouseleave', function () {
        if (wrap._navPinned) return;
        clearTimeout(wrap._navTimer);
        wrap._navTimer = setTimeout(function () { setOpen(wrap, false); }, CLOSE_DELAY);
      });

      btn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();   /* keeps the document handler from closing it instantly */
        if (wrap._navPinned) { setOpen(wrap, false); return; }
        closeAll(wrap);
        setOpen(wrap, true);
        wrap._navPinned = true;
      });
    });
  });

  document.addEventListener('click', function (e) {
    wraps.forEach(function (w) { if (!w.contains(e.target)) setOpen(w, false); });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeAll();
  });
})();
