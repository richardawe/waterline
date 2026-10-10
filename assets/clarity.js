/* Microsoft Clarity (heatmaps + session recordings) for Waterline's PUBLIC pages only.
 *
 * Never load this on pages that show customers' personal data: the Collections
 * client app (/collections/app/), ops console (/collections/ops/), debtor payment
 * links (/collections/p/) and admin.html. Those pages simply don't include this
 * file; the path check below is a second line of defence. Forms on public pages
 * (sign-up, login, reset, waitlist) carry data-clarity-mask="true" so what people
 * type is never recorded. The CSP in deploy/.htaccess.preview.template allows the
 * clarity.ms hosts.
 */
(function () {
  var p = location.pathname;
  if (/^\/collections\/(app|ops|p)(\/|$)/.test(p) || /^\/admin(\.html)?$/.test(p)) return;
  (function (c, l, a, r, i, t, y) {
    c[a] = c[a] || function () { (c[a].q = c[a].q || []).push(arguments); };
    t = l.createElement(r); t.async = 1; t.src = "https://www.clarity.ms/tag/" + i;
    y = l.getElementsByTagName(r)[0]; y.parentNode.insertBefore(t, y);
  })(window, document, "clarity", "script", "yvrg6gli3v");
})();
