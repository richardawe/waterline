/* Sign-up, login, email verification and password reset pages. */
(function () {
  var page = document.body.dataset.page;
  function msg(el, text, kind) { el.innerHTML = text ? '<div class="banner ' + (kind || 'bad') + '">' + CL.esc(text) + '</div>' : ''; }
  function goApp() { location.href = CL.param('next') && CL.param('next').indexOf('/collections/app') === 0 ? CL.param('next') : '/collections/app/'; }

  if (page === 'signup') {
    var form = CL.$('#signup'), segSel = CL.$('#segment'), typeSel = CL.$('#business_type');
    var SEGS = [];
    function current() { return SEGS.filter(function (x) { return x.key === segSel.value; })[0]; }
    function onSegment() {
      var seg = current(); if (!seg) return;
      var live = seg.status === 'live';
      form.classList.toggle('hidden', false);
      CL.$('#waitlist-note').classList.toggle('hidden', live);
      CL.$$('#signup > :not(:first-child)').forEach(function (el) {
        if (el.querySelector && el.querySelector('#segment')) return;
        if (el.tagName !== 'H2' || el.textContent !== 'Your organisation') el.classList.toggle('hidden', !live);
      });
      if (!live) {
        CL.$('#waitlist-note').innerHTML = '<h2>' + CL.esc(seg.name) + ': coming soon</h2><p>We\'re not open to ' + CL.esc(seg.name.toLowerCase()) +
          ' yet. <a href="/collections/#waitlist">Join the waitlist</a> and we\'ll be in touch as soon as we are.</p>';
        return;
      }
      typeSel.innerHTML = '<option value="">Choose…</option>' + seg.client_types.map(function (t) { return '<option>' + CL.esc(t) + '</option>'; }).join('');
      CL.$('#segment-hint').textContent = seg.tagline;
      CL.$('#client-fields').innerHTML = seg.client_fields.map(function (f) {
        return '<div class="field"><label for="cf-' + f.name + '">' + CL.esc(f.label) + '</label><input id="cf-' + f.name + '" data-client-field="' + f.name + '" name="' + f.name + '" type="text"' + (f.required ? ' required' : '') + '>' +
          (f.help ? '<div class="hint">' + CL.esc(f.help) + '</div>' : '') + '</div>';
      }).join('');
      CL.$('#fee-default').textContent = 'Standard fee: ' + seg.default_fee_text + '. You pay only on money you confirm you\'ve recovered.';
    }
    CL.api('/collections/public/config').then(function (c) {
      SEGS = c.segments;
      segSel.innerHTML = SEGS.map(function (x) {
        return '<option value="' + CL.esc(x.key) + '">' + CL.esc(x.name) + (x.status === 'live' ? '' : ' (coming soon)') + '</option>';
      }).join('');
      var want = CL.param('segment');
      if (want && SEGS.some(function (x) { return x.key === want; })) segSel.value = want;
      CL.$('#state').insertAdjacentHTML('beforeend', c.states.map(function (t) { return '<option>' + CL.esc(t) + '</option>'; }).join(''));
      onSegment();
    });
    segSel.addEventListener('change', onSegment);
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var btn = form.querySelector('button[type=submit]');
      var body = CL.formJSON(form), fields = {};
      CL.$$('[data-client-field]').forEach(function (el) { fields[el.dataset.clientField] = el.value; delete body[el.name]; });
      body.client_fields = fields;
      body.proposed_fee_percent = body.proposed_fee_percent ? parseFloat(body.proposed_fee_percent) : null;
      if (!body.fee_note) body.fee_note = null;
      CL.busy(btn, true, 'Creating account…'); msg(CL.$('#form-msg'), '');
      CL.api('/collections/public/signup', { json: body }).then(function () {
        form.classList.add('hidden'); CL.$('#done').classList.remove('hidden'); window.scrollTo(0, 0);
      }).catch(function (err) {
        CL.busy(btn, false);
        if (err.detail && err.detail.fields) CL.showFieldErrors(form, err.detail.fields);
        msg(CL.$('#form-msg'), err.message);
      });
    });
  }

  if (page === 'login') {
    var lf = CL.$('#login');
    if (CL.getToken('client')) goApp();
    lf.addEventListener('submit', function (e) {
      e.preventDefault();
      var btn = lf.querySelector('button[type=submit]'), body = CL.formJSON(lf);
      CL.busy(btn, true, 'Logging in…'); msg(CL.$('#form-msg'), '');
      CL.api('/collections/public/login', { json: body }).then(function (r) {
        CL.setToken('client', r.token); goApp();
      }).catch(function (err) {
        CL.busy(btn, false);
        msg(CL.$('#form-msg'), err.message);
        if (err.detail && err.detail.code === 'unverified') {
          var b = document.createElement('button'); b.type = 'button'; b.className = 'btn ghost sm'; b.textContent = 'Send a new link';
          b.onclick = function () {
            CL.api('/collections/public/resend-verification', { json: { email: body.email } }).then(function (r) { msg(CL.$('#form-msg'), r.message, 'ok'); });
          };
          CL.$('#form-msg').appendChild(b);
        }
      });
    });
  }

  if (page === 'verify') {
    var box = CL.$('#verify-box'), token = CL.param('token');
    if (!token) { box.innerHTML = '<h1>Link not valid</h1><p>Open the link from your email again.</p>'; return; }
    CL.api('/collections/public/verify-email', { json: { token: token } }).then(function (r) {
      CL.setToken('client', r.token);
      box.innerHTML = '<h1>Email confirmed</h1><p>Taking you to your dashboard…</p>';
      setTimeout(goApp, 900);
    }).catch(function (err) {
      box.innerHTML = '<h1>That link didn\'t work</h1><p>' + CL.esc(err.message) + '</p><p><a href="/collections/login/">Log in</a> to request a new one.</p>';
    });
  }

  if (page === 'reset') {
    var t = CL.param('token'), invite = CL.param('invite') === '1';
    if (!t) {
      CL.$('#request').classList.remove('hidden');
      var ff = CL.$('#forgot');
      ff.addEventListener('submit', function (e) {
        e.preventDefault();
        CL.api('/collections/public/forgot-password', { json: CL.formJSON(ff) })
          .then(function (r) { msg(CL.$('#form-msg'), r.message, 'ok'); })
          .catch(function (err) { msg(CL.$('#form-msg'), err.message); });
      });
      return;
    }
    CL.$('#choose').classList.remove('hidden');
    if (invite) CL.$('#choose-title').textContent = 'Set your password';
    var nf = CL.$('#newpass');
    nf.addEventListener('submit', function (e) {
      e.preventDefault();
      var btn = nf.querySelector('button[type=submit]');
      CL.busy(btn, true, 'Saving…');
      CL.api('/collections/public/reset-password', { json: { token: t, password: CL.formJSON(nf).password, purpose: invite ? 'invite' : 'reset_password' } })
        .then(function (r) { CL.setToken('client', r.token); goApp(); })
        .catch(function (err) { CL.busy(btn, false); msg(CL.$('#form-msg2'), err.message); });
    });
  }
})();
