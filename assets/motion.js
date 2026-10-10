/* Waterline motion helpers (Hybrid design system). Tiny, dependency-free.
 * Everything renders its final state immediately when the visitor prefers
 * reduced motion, and every animated number is readable at rest.
 *
 *   WLMotion.countUp(el, value, {money: true | suffix: "%", decimals: 1})
 *   WLMotion.countAll(root)          // every [data-count] under root
 *   WLMotion.fill(tankEl, percent)   // set a .tank waterline level
 *   WLMotion.tick(button, label)     // morph a button into "✓ label"
 */
(function () {
  var reduce = false;
  try { reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}

  function money(v) {
    var k = Math.round(v);
    return '₦' + Math.floor(k / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + String(k % 100).padStart(2, '0');
  }
  function format(v, o) {
    if (o.money) return money(v);           // value in kobo
    var d = o.decimals == null ? 0 : o.decimals;
    return v.toFixed(d).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (o.suffix || '');
  }

  function countUp(el, value, o) {
    o = o || {};
    var target = Number(value) || 0;
    if (reduce || !window.requestAnimationFrame) { el.textContent = format(target, o); return; }
    var t0 = performance.now(), dur = o.duration || 900;
    (function step(t) {
      var p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      el.textContent = format(target * e, o);
      if (p < 1) requestAnimationFrame(step);
    })(t0);
  }

  function countAll(root) {
    (root || document).querySelectorAll('[data-count]').forEach(function (el) {
      countUp(el, el.dataset.count, {
        money: el.hasAttribute('data-money'), suffix: el.dataset.suffix || '',
        decimals: el.dataset.decimals ? Number(el.dataset.decimals) : 0
      });
    });
  }

  function fill(tank, percent) {
    var water = tank.querySelector('.water');
    var level = Math.max(0, Math.min(100, Number(percent) || 0)) + '%';
    if (!water) return;
    if (reduce) { water.style.setProperty('--level', level); return; }
    water.style.setProperty('--level', '0%');
    requestAnimationFrame(function () { requestAnimationFrame(function () { water.style.setProperty('--level', level); }); });
  }

  var CHECK = '<svg class="tick" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8.5l3 3 7-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  function tick(btn, label) {
    btn.style.minWidth = btn.offsetWidth + 'px';
    btn.innerHTML = CHECK + ' ' + (label || 'Done');
  }

  window.WLMotion = { countUp: countUp, countAll: countAll, fill: fill, tick: tick, reduce: reduce };
})();
