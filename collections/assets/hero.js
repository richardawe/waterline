/* Landing pages: raise the waterline in the hero demo and count the recovered amount up. */
(function () {
  function run() {
    if (!window.WLMotion) return;
    document.querySelectorAll('.hero-demo .tank[data-fill]').forEach(function (t) { WLMotion.fill(t, t.dataset.fill); });
    WLMotion.countAll(document.querySelector('.hero-demo'));
  }
  if (document.readyState === 'complete') run(); else window.addEventListener('load', run);
})();
