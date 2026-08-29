(function () {
  const REF_H = 960;

  function fitConsolePage() {
    const viewport = window.visualViewport;
    const width = viewport ? viewport.width : window.innerWidth;
    const height = viewport ? viewport.height : window.innerHeight;
    const scale = height / REF_H;
    const virtualWidth = width / scale;

    document.documentElement.style.setProperty('--console-scale', scale.toFixed(5));
    document.documentElement.style.setProperty('--console-virtual-width', `${virtualWidth.toFixed(2)}px`);
  }

  window.addEventListener('resize', fitConsolePage);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', fitConsolePage);
  }
  fitConsolePage();
})();
