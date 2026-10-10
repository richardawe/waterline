/* Sign-up, login, email verification and password reset pages. */
(function () {
  var page = document.body.dataset.page;
  function msg(el, text, kind) { el.innerHTML = text ? '<div class="banner ' + (kind || 'bad') + '">' + CL.esc(text) + '</div>' : ''; }
  function goApp() { location.href = CL.param('next') && CL.param('next').indexOf('/collections/app') === 0 ? CL.param('next') : '/collections/app/'; }

  if (page === 'signup') {
    var form = CL.$('#signup');
    CL.api('/collections/public/config').then(function (c) {
      CL.$('#business_type').insertAdjacentHTML('beforeend', c.business_types.map(function (t) { return '<option>' + CL.esc(t) + '</option>'; }).join(''));
      CL.$('#state').insertAdjacentHTML('beforeend', c.states.map(function (t) { return '<option>' + CL.esc(t) + '</option>'; }).join(''));
    });
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var btn = form.querySelector('button[type=submit]');
      CL.busy(btn, true, 'Creating account…'); msg(CL.$('#form-msg'), '');
      CL.api('/collections/public/signup', { json: CL.formJSON(form) }).then(function () {
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
