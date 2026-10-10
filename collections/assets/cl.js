/* Waterline Collections — shared browser helpers (no framework, no build). */
(function () {
  var API = (window.WATERLINE_API_BASE || 'http://localhost:8001').replace(/\/$/, '');
  var KEYS = { client: 'wlc_client_token', ops: 'wlc_ops_token' };

  function store(kind) {
    // ops sessions die with the tab; client sessions persist on this device
    try { return kind === 'ops' ? window.sessionStorage : window.localStorage; } catch (e) { return null; }
  }
  function getToken(kind) { try { var s = store(kind); return s ? s.getItem(KEYS[kind]) : null; } catch (e) { return null; } }
  function setToken(kind, t) { try { var s = store(kind); if (s) { t ? s.setItem(KEYS[kind], t) : s.removeItem(KEYS[kind]); } } catch (e) {} }

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function naira(kobo) {
    kobo = Math.round(Number(kobo || 0));
    var neg = kobo < 0; kobo = Math.abs(kobo);
    var whole = Math.floor(kobo / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return (neg ? '-' : '') + '₦' + whole + '.' + String(kobo % 100).padStart(2, '0');
  }

  var dateFmt = new Intl.DateTimeFormat('en-GB', { timeZone: 'Africa/Lagos', day: '2-digit', month: 'short', year: 'numeric' });
  var dtFmt = new Intl.DateTimeFormat('en-GB', { timeZone: 'Africa/Lagos', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  function date(v) {
    if (!v) return '';
    if (/^\d{4}-\d{2}-\d{2}$/.test(v)) { var p = v.split('-'); return p[2] + '/' + p[1] + '/' + p[0]; }
    return dateFmt.format(new Date(v));
  }
  function datetime(v) { return v ? dtFmt.format(new Date(v)) + ' WAT' : ''; }
  function todayISO() {
    var parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Africa/Lagos' }).format(new Date());
    return parts; // YYYY-MM-DD
  }

  function detailText(detail) {
    if (!detail) return 'Something went wrong. Please try again.';
    if (typeof detail === 'string') return detail;
    if (detail.message) return detail.message;
    if (Array.isArray(detail)) return detail.map(function (d) { return (d.loc ? d.loc[d.loc.length - 1] + ': ' : '') + d.msg; }).join('; ');
    return 'Something went wrong. Please try again.';
  }

  function ApiError(status, detail) { this.status = status; this.detail = detail; this.message = detailText(detail); }

  /* api('/collections/client/me', {auth:'client', method:'POST', json:{...} | form:FormData, raw:true}) */
  function api(path, opts) {
    opts = opts || {};
    var headers = {};
    var body;
    if (opts.json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.json); }
    if (opts.form) body = opts.form;
    if (opts.auth) { var t = getToken(opts.auth); if (t) headers.Authorization = 'Bearer ' + t; }
    return fetch(API + path, { method: opts.method || (body ? 'POST' : 'GET'), headers: headers, body: body, credentials: 'omit' })
      .then(function (r) {
        if (opts.raw && r.ok) return r;
        return r.text().then(function (text) {
          var data = null;
          try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
          if (!r.ok) {
            if (r.status === 401 && opts.auth && !opts.noRedirect) {
              setToken(opts.auth, null);
              location.href = (opts.auth === 'ops' ? '/collections/ops/' : '/collections/login/') + '?next=' + encodeURIComponent(location.pathname + location.search);
            }
            throw new ApiError(r.status, data && data.detail !== undefined ? data.detail : data);
          }
          return data;
        });
      }, function () { throw new ApiError(0, 'We couldn\'t reach Waterline. Check your connection and try again.'); });
  }

  function download(path, auth, filename) {
    return api(path, { auth: auth, raw: true }).then(function (r) { return r.blob(); }).then(function (blob) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = filename;
      document.body.appendChild(a); a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    });
  }

  var toastTimer;
  function toast(msg) {
    var el = document.getElementById('cl-toast');
    if (!el) { el = document.createElement('div'); el.id = 'cl-toast'; el.className = 'toast'; el.setAttribute('role', 'status'); document.body.appendChild(el); }
    el.textContent = msg; el.classList.remove('hidden');
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { el.classList.add('hidden'); }, 3500);
  }

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function formJSON(form) {
    var out = {};
    $$('input,select,textarea', form).forEach(function (el) {
      if (!el.name) return;
      if (el.type === 'checkbox') out[el.name] = el.checked;
      else out[el.name] = el.value;
    });
    return out;
  }

  function showFieldErrors(form, fields) {
    $$('.err', form).forEach(function (e) { e.remove(); });
    Object.keys(fields || {}).forEach(function (name) {
      var el = form.querySelector('[name="' + name + '"]') || form.querySelector('[data-err="' + name + '"]');
      if (!el) return;
      var d = document.createElement('div'); d.className = 'err'; d.textContent = fields[name];
      (el.closest('.field') || el.parentNode).appendChild(d);
    });
  }

  function busy(btn, on, label) {
    if (!btn) return;
    if (on) { btn.dataset.label = btn.textContent; btn.textContent = label || 'Working…'; btn.disabled = true; }
    else { btn.textContent = btn.dataset.label || btn.textContent; btn.disabled = false; }
  }

  function param(name) { return new URLSearchParams(location.search).get(name); }

  function menuToggle() {
    var b = $('.menu-btn'), m = $('.menu');
    if (b && m) b.addEventListener('click', function () { m.classList.toggle('open'); b.setAttribute('aria-expanded', m.classList.contains('open')); });
  }
  document.addEventListener('DOMContentLoaded', menuToggle);

  var STATUS_CLASS = {
    'Paid': 'green', 'Confirmed': 'green', 'Approved': 'green', 'Sent': 'green', 'Upheld': 'green', 'Adjusted': 'green',
    'On plan': 'blue', 'Proof submitted': 'blue', 'Issued': 'blue', 'Queued': 'blue', 'Active': 'blue',
    'Disputed': 'amber', 'Pending': 'amber', 'Open': 'amber', 'Paused': 'amber', 'Unresponsive': 'amber', 'Draft': 'amber',
    'Rejected': 'red', 'Suspended': 'red', 'Withdrawn': 'red', 'Overdue': 'red', 'Skipped': '', 'Reversed': 'red'
  };
  function pill(status) { return '<span class="pill ' + (STATUS_CLASS[status] || '') + '">' + esc(status) + '</span>'; }

  window.CL = {
    API: API, api: api, download: download, getToken: getToken, setToken: setToken, esc: esc, naira: naira,
    date: date, datetime: datetime, todayISO: todayISO, toast: toast, $: $, $$: $$, formJSON: formJSON,
    showFieldErrors: showFieldErrors, busy: busy, param: param, pill: pill, errorText: detailText
  };
})();
