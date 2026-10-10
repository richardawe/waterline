/* Landing page: segment cards and the waitlist reflect what ops have switched on. */
(function () {
  var esc = CL.esc, segs = [];
  var wl = CL.$('#wl'), segSel = CL.$('#wl-seg'), typeSel = CL.$('#wl-type');

  function fillTypes() {
    var seg = segs.filter(function (s) { return s.key === segSel.value; })[0];
    typeSel.innerHTML = seg ? seg.client_types.map(function (t) { return '<option>' + esc(t) + '</option>'; }).join('') : '';
  }

  function card(s) {
    var live = s.status === 'live';
    return '<div class="seg' + (live ? '' : ' soon') + '"><span class="pill ' + (live ? 'green' : 'amber') + '">' + (live ? 'Live now' : 'Coming soon') + '</span>' +
      '<h3>' + esc(s.name) + '</h3><p>' + esc(s.tagline) + '</p><p class="types">' + esc(s.client_types.join(' · ')) + '</p>' +
      (live ? '<a class="btn sm" href="/collections/signup/?segment=' + esc(s.key) + '">Get started</a>'
        : '<a class="btn sm ghost" href="#waitlist" data-wait="' + esc(s.key) + '">Join the waitlist</a>') + '</div>';
  }

  function bindWait() {
    CL.$$('[data-wait]').forEach(function (a) {
      a.addEventListener('click', function () { segSel.value = a.dataset.wait; fillTypes(); });
    });
  }

  CL.api('/collections/public/config').then(function (c) {
    segs = c.segments;
    CL.$$('[data-email]').forEach(function (e) { e.textContent = c.contact_email; e.href = 'mailto:' + c.contact_email; });
    // the number lives in Ops → Config (contact_whatsapp); the page ships with it so it reads right before this loads
    var digits = String(c.contact_whatsapp || '').replace(/\D/g, '');
    if (digits.length >= 10) {
      CL.$$('[data-whatsapp]').forEach(function (el) { el.textContent = c.contact_whatsapp; });
      CL.$$('[data-wa-link]').forEach(function (a) { a.href = 'https://wa.me/' + digits; });
      CL.$$('[data-tel-link]').forEach(function (a) { a.href = 'tel:+' + digits; });
    }
    // live segments first; the headline fee is the live segments' flat default
    var ordered = segs.filter(function (s) { return s.status === 'live'; }).concat(segs.filter(function (s) { return s.status !== 'live'; }));
    CL.$('#segs').innerHTML = ordered.map(card).join('');
    var flat = ordered.filter(function (s) { return s.status === 'live' && s.default_fee.type === 'flat'; })[0];
    if (flat) CL.$$('[data-fee]').forEach(function (el) { el.textContent = flat.default_fee.percent; });
    var waiting = segs.filter(function (s) { return s.status === 'waitlist'; });
    if (waiting.length) {
      segSel.innerHTML = waiting.map(function (s) { return '<option value="' + esc(s.key) + '">' + esc(s.name) + '</option>'; }).join('');
      fillTypes();
    } else {
      CL.$('#waitlist').classList.add('hidden');
    }
    bindWait();
  }).catch(function () { bindWait(); });

  segSel.addEventListener('change', fillTypes);
  wl.addEventListener('submit', function (e) {
    e.preventDefault();
    var btn = wl.querySelector('button[type=submit]');
    CL.busy(btn, true, 'Sending…');
    CL.api('/collections/public/waitlist', { json: CL.formJSON(wl) }).then(function (r) {
      wl.innerHTML = '<div class="banner ok" role="status">' + esc(r.message) + '</div>';
    }).catch(function (err) {
      CL.busy(btn, false);
      if (err.detail && err.detail.fields) CL.showFieldErrors(wl, err.detail.fields);
      CL.$('#wl-msg').innerHTML = '<div class="banner bad">' + esc(err.message) + '</div>';
    });
  });
})();
