/* Client app (/collections/app/*). One script, rendered by <body data-page>. */
(function () {
  var page = document.body.dataset.page;
  var view = CL.$('#view');
  var esc = CL.esc, naira = CL.naira;
  var ME = null;

  if (!CL.getToken('client')) { location.href = '/collections/login/?next=' + encodeURIComponent(location.pathname + location.search); return; }

  function get(path) { return CL.api('/collections/client' + path, { auth: 'client' }); }
  function post(path, json) { return CL.api('/collections/client' + path, { auth: 'client', method: 'POST', json: json || {} }); }
  function put(path, json) { return CL.api('/collections/client' + path, { auth: 'client', method: 'PUT', json: json }); }
  function fail(err) { CL.toast(err.message || String(err)); }
  function kobo(naira_) { var v = String(naira_ || '').replace(/[₦,\s]/g, ''); return v === '' ? null : Math.round(parseFloat(v) * 100); }

  var NAV = [
    ['dashboard', '/collections/app/', 'Dashboard'], ['uploads', '/collections/app/uploads/', 'Uploads'],
    ['accounts', '/collections/app/accounts/', 'Accounts'], ['confirmations', '/collections/app/confirmations/', 'Confirmations'],
    ['disputes', '/collections/app/disputes/', 'Disputes'], ['invoices', '/collections/app/invoices/', 'Invoices'],
    ['settings', '/collections/app/settings/', 'Settings']
  ];
  var ACTIVE = { 'upload-new': 'uploads', 'account': 'accounts' };

  function shell(me) {
    var on = ACTIVE[page] || page;
    CL.$('#shell').innerHTML =
      '<header class="bar"><div class="wrap bar-in">' +
      '<a class="logo" href="/collections/app/"><span class="logo-mark" aria-hidden="true">W</span>' + esc(me.client.business_name) + '</a>' +
      '<button class="menu-btn" aria-label="Menu" aria-expanded="false">☰</button>' +
      '<nav class="menu" aria-label="App">' + NAV.map(function (n) {
        return '<a href="' + n[1] + '"' + (n[0] === on ? ' class="on" aria-current="page"' : '') + '>' + n[2] + '</a>';
      }).join('') + '<a href="#" id="logout">Log out</a></nav></div></header>' + statusBanner(me.client);
    var b = CL.$('.menu-btn'), m = CL.$('.menu');
    b.addEventListener('click', function () { m.classList.toggle('open'); });
    CL.$('#logout').addEventListener('click', function (e) {
      e.preventDefault();
      CL.api('/collections/public/logout', { auth: 'client', method: 'POST', noRedirect: true }).finally(function () {
        CL.setToken('client', null); location.href = '/collections/login/';
      });
    });
  }

  function statusBanner(c) {
    var text = {
      Pending: ['info', "We're verifying your business. This usually takes 1–2 working days. You can prepare your uploads now — nothing is sent until you're verified."],
      Suspended: ['bad', 'Outreach for your account is paused' + (c.status_reason ? ': ' + c.status_reason : '.') + ' Contact us to sort it out.'],
      Rejected: ['bad', 'We could not verify your business' + (c.status_reason ? ': ' + c.status_reason : '.')]
    }[c.status];
    return text ? '<div class="wrap" style="margin-top:16px"><div class="banner ' + text[0] + '" role="status">' + esc(text[1]) + '</div></div>' : '';
  }

  /* ------------------------------------------------------------ rules form (uploads + settings) */
  function rulesForm(prefix, rules) {
    rules = rules || {};
    var v = function (k, d) { return rules[k] == null ? d : rules[k]; };
    return '<div class="grid2">' +
      '<div class="field"><label for="' + prefix + 'min">Smallest instalment (₦)</label><input id="' + prefix + 'min" type="text" inputmode="decimal" value="' + (v('min_instalment', 500000) / 100) + '"></div>' +
      '<div class="field"><label for="' + prefix + 'max">Most instalments allowed</label><select id="' + prefix + 'max">' +
      [1, 2, 3, 4, 5, 6].map(function (n) { return '<option' + (n === v('max_instalments', 3) ? ' selected' : '') + '>' + n + '</option>'; }).join('') + '</select><div class="hint">1 means pay in full only.</div></div>' +
      '<div class="field"><label for="' + prefix + 'disc">Discount for paying in full (%)</label><input id="' + prefix + 'disc" type="number" min="0" max="50" step="0.5" value="' + v('discount_percent', 0) + '"></div>' +
      '<div class="field"><label for="' + prefix + 'dl">…if paid in full by</label><input id="' + prefix + 'dl" type="date" value="' + esc(v('discount_deadline', '') || '') + '"></div>' +
      '<div class="field"><label for="' + prefix + 'start">Start messages on</label><input id="' + prefix + 'start" type="date" value="' + esc(v('start_date', '') || '') + '"><div class="hint">Leave empty to start as soon as you\'re verified.</div></div>' +
      '</div>';
  }
  function readRules(prefix) {
    return {
      min_instalment: kobo(CL.$('#' + prefix + 'min').value) || 0,
      max_instalments: parseInt(CL.$('#' + prefix + 'max').value, 10),
      discount_percent: parseFloat(CL.$('#' + prefix + 'disc').value || '0'),
      discount_deadline: CL.$('#' + prefix + 'dl').value || null,
      start_date: CL.$('#' + prefix + 'start').value || null
    };
  }
  function rulesSummary(r) {
    if (!r) return 'Account default';
    var parts = [r.max_instalments > 1 ? 'up to ' + r.max_instalments + ' instalments (min ' + naira(r.min_instalment) + ')' : 'pay in full only'];
    if (r.discount_percent) parts.push(r.discount_percent + '% off by ' + CL.date(r.discount_deadline));
    if (r.start_date) parts.push('starts ' + CL.date(r.start_date));
    return parts.join(' · ');
  }

  /* ------------------------------------------------------------ pages */
  var pages = {};

  pages.dashboard = function () {
    return get('/dashboard').then(function (d) {
      var s = d.accounts_by_status || {};
      view.innerHTML = '<h1>Dashboard</h1>' +
        (d.awaiting_count ? '<div class="banner warn"><b>' + d.awaiting_count + ' payment' + (d.awaiting_count > 1 ? 's' : '') + ' waiting for you to confirm</b> — check your bank and <a href="/collections/app/confirmations/">confirm or reject</a>.</div>' : '') +
        (d.open_disputes ? '<div class="banner warn"><b>' + d.open_disputes + ' open quer' + (d.open_disputes > 1 ? 'ies' : 'y') + '</b> — messages to these people are paused until you <a href="/collections/app/disputes/">resolve them</a>.</div>' : '') +
        '<div class="stats">' +
        stat(naira(d.total_uploaded), 'Total uploaded') + stat(naira(d.total_recovered), 'Recovered (confirmed)') +
        stat((d.recovery_rate * 100).toFixed(1) + '%', 'Recovery rate') + stat(naira(d.awaiting_confirmation), 'Awaiting your confirmation') +
        stat(d.open_disputes, 'Open disputes') + stat(d.active_plans, 'Active payment plans') + stat(naira(d.fees_this_month), 'Fees this month') +
        '</div>' +
        '<div class="card"><h2>Accounts by status</h2>' +
        (Object.keys(s).length ? '<div class="row-btns">' + Object.keys(s).map(function (k) {
          return '<a class="pill" style="text-decoration:none;font-size:.85rem;padding:6px 12px" href="/collections/app/accounts/?status=' + encodeURIComponent(k) + '">' + esc(k) + ': ' + s[k] + '</a>';
        }).join('') + '</div>' : '<p class="muted">No accounts yet. <a href="/collections/app/uploads/new/">Upload your first list</a>.</p>') + '</div>' +
        '<div class="row-btns"><a class="btn" href="/collections/app/uploads/new/">Upload balances</a><a class="btn ghost" href="/collections/app/accounts/">View accounts</a></div>';
    });
  };
  function stat(v, label) { return '<div class="stat"><b>' + esc(v) + '</b><span>' + esc(label) + '</span></div>'; }

  pages.uploads = function () {
    return get('/uploads').then(function (rows) {
      view.innerHTML = '<div class="bar-in" style="min-height:0;margin-bottom:12px"><h1 style="margin:0">Uploads</h1><a class="btn" href="/collections/app/uploads/new/">New upload</a></div>' +
        (rows.length ? '<div class="card table-wrap"><table><thead><tr><th>File</th><th>Uploaded</th><th>Status</th><th class="right">Accounts</th><th>Rules</th><th></th></tr></thead><tbody>' +
          rows.map(function (b) {
            return '<tr><td>' + esc(b.file_name) + '</td><td class="nowrap">' + CL.date(b.created_at) + '</td><td>' + CL.pill(b.status === 'Preview' ? 'Draft' : b.status) + '</td>' +
              '<td class="right">' + (b.status === 'Imported' ? '<a href="/collections/app/accounts/?batch_id=' + b.id + '">' + b.imported_count + '</a>' : b.valid_count + ' valid') + '</td>' +
              '<td class="small">' + esc(b.status === 'Imported' ? rulesSummary(b.rules) : '') + '</td>' +
              '<td class="nowrap">' + (b.status === 'Preview' ? '<a class="btn sm ghost" href="/collections/app/uploads/new/?batch=' + b.id + '">Continue</a>' :
                '<button class="btn sm ghost" data-rules="' + b.id + '">Edit rules</button>') + '</td></tr>' +
              '<tr class="hidden" id="rules-' + b.id + '"><td colspan="6"><div class="card" style="margin:8px 0">' +
              '<label class="check"><input type="checkbox" id="def-' + b.id + '"' + (b.rules ? '' : ' checked') + '> Use my account default rules</label>' +
              '<div id="rf-' + b.id + '">' + rulesForm('r' + b.id.slice(0, 8), b.rules) + '</div>' +
              '<button class="btn sm" data-save-rules="' + b.id + '">Save rules</button></div></td></tr>';
          }).join('') + '</tbody></table></div>' : '<div class="card"><p>You haven\'t uploaded any balances yet.</p><a class="btn" href="/collections/app/uploads/new/">Upload your first list</a></div>');
      CL.$$('[data-rules]').forEach(function (btn) {
        btn.onclick = function () { CL.$('#rules-' + btn.dataset.rules).classList.toggle('hidden'); };
      });
      CL.$$('[data-save-rules]').forEach(function (btn) {
        btn.onclick = function () {
          var id = btn.dataset.saveRules;
          var rules = CL.$('#def-' + id).checked ? null : readRules('r' + id.slice(0, 8));
          put('/uploads/' + id + '/rules', { rules: rules }).then(function () { CL.toast('Rules saved'); pages.uploads(); }).catch(fail);
        };
      });
    });
  };

  var COLS = ['debtor_name', 'phone', 'email', 'description', 'dependant_first_name', 'amount_owed', 'due_date'];
  pages['upload-new'] = function () {
    var batchId = CL.param('batch');
    if (batchId) return get('/uploads/' + batchId).then(renderPreview);
    var tplBase = CL.API + '/collections/public/upload-template';
    view.innerHTML = '<h1>Upload balances</h1>' +
      '<div class="card"><h2>1. Get the template</h2><p>Fill in one row per person who owes you. Only these columns are allowed — never add details of anyone other than the person who owes (no guarantors, relatives or employers).</p>' +
      '<div class="row-btns"><a class="btn ghost" href="' + tplBase + '.xlsx">Excel template</a><a class="btn ghost" href="' + tplBase + '.csv">CSV template</a></div>' +
      '<ul class="small muted" style="margin:12px 0 0;padding-left:20px"><li><b>debtor_name</b>, <b>phone</b>, <b>description</b> (e.g. "School fees – 2nd term 2025/26") and <b>amount_owed</b> in naira are required.</li>' +
      '<li><b>email</b>, <b>due_date</b> (DD/MM/YYYY) and <b>dependant_first_name</b> (schools only — first name only, never a surname) are optional.</li></ul></div>' +
      '<form class="card" id="up"><h2>2. Upload your file</h2><div class="field"><label for="file">CSV or Excel (.xlsx) file</label><input id="file" name="file" type="file" accept=".csv,.xlsx" required></div>' +
      '<div id="up-msg"></div><button class="btn" type="submit">Check file</button></form>';
    CL.$('#up').addEventListener('submit', function (e) {
      e.preventDefault();
      var f = CL.$('#file').files[0]; if (!f) return;
      var fd = new FormData(); fd.append('file', f);
      var btn = e.target.querySelector('button'); CL.busy(btn, true, 'Checking…');
      CL.api('/collections/client/uploads', { auth: 'client', form: fd }).then(function (r) {
        history.replaceState(null, '', '?batch=' + r.batch.id); renderPreview(r);
      }).catch(function (err) { CL.busy(btn, false); CL.$('#up-msg').innerHTML = '<div class="banner bad">' + esc(err.message) + '</div>'; });
    });
  };

  function renderPreview(r) {
    var b = r.batch;
    if (b.status !== 'Preview') {
      view.innerHTML = '<h1>Upload imported</h1><div class="banner ok">' + b.imported_count + ' accounts were added from ' + esc(b.file_name) + '.</div><a class="btn" href="/collections/app/accounts/?batch_id=' + b.id + '">View accounts</a>';
      return;
    }
    var rows = r.rows;
    var errors = rows.filter(function (x) { return x.status === 'error'; });
    var dups = rows.filter(function (x) { return x.status === 'duplicate'; });
    var valid = rows.filter(function (x) { return x.status === 'valid'; });
    view.innerHTML = '<h1>Check your upload</h1><p class="muted">' + esc(b.file_name) + '</p>' +
      '<div class="stats">' + stat(b.row_count, 'Rows') + stat(b.valid_count, 'Ready to import') + stat(b.error_count, 'Need fixing') + stat(b.duplicate_count, 'Duplicates (skipped)') + '</div>' +
      (errors.length ? '<div class="card"><h2>Rows with errors</h2><p class="small muted">Fix them here and press “Re-check”, or leave them — rows with errors are skipped.</p><div class="table-wrap"><table><thead><tr><th>Row</th>' +
        COLS.map(function (c) { return '<th>' + c.replace(/_/g, ' ') + '</th>'; }).join('') + '<th>Problem</th></tr></thead><tbody>' +
        errors.map(function (x) {
          return '<tr class="err-row" data-row="' + x.row + '"><td>' + x.row + '</td>' + COLS.map(function (c) {
            return '<td><input type="text" style="min-width:110px;min-height:36px;padding:6px 8px" data-col="' + c + '" value="' + esc(x.raw[c] || '') + '" aria-label="Row ' + x.row + ' ' + c + '"></td>';
          }).join('') + '<td class="small" style="color:var(--red)">' + esc(x.errors.join('; ')) + '</td></tr>';
        }).join('') + '</tbody></table></div><button class="btn ghost sm" id="recheck" style="margin-top:10px">Re-check fixed rows</button></div>' : '') +
      (dups.length ? '<div class="card"><h2>Duplicates</h2><p class="small muted">Same person, description and amount as another row or an account you already have. These won\'t be imported.</p><ul class="small">' +
        dups.map(function (x) { return '<li>Row ' + x.row + ': ' + esc(x.raw.debtor_name) + ' — ' + esc(x.raw.amount_owed) + '</li>'; }).join('') + '</ul></div>' : '') +
      '<div class="card"><h2>Ready to import (' + valid.length + ')</h2><p class="small muted">Untick anyone you don\'t want us to contact.</p><div class="table-wrap"><table><thead><tr><th></th><th>Row</th><th>Name</th><th>Phone</th><th>For</th><th class="right">Amount</th></tr></thead><tbody>' +
        valid.map(function (x) {
          return '<tr><td><input type="checkbox" checked data-keep="' + x.row + '" aria-label="Import row ' + x.row + '"></td><td>' + x.row + '</td><td>' + esc(x.clean.debtor_name) + '</td><td class="nowrap">' + esc(x.clean.phone) + '</td><td>' + esc(x.clean.description) +
            (x.clean.dependant_first_name ? ' <span class="muted">(' + esc(x.clean.dependant_first_name) + ')</span>' : '') + '</td><td class="right nowrap">' + naira(x.clean.amount_kobo) + '</td></tr>';
        }).join('') + '</tbody></table></div></div>' +
      '<div class="card"><h2>Recovery rules for this upload</h2><label class="check"><input type="checkbox" id="use-default" checked> Use my account default rules (set in Settings)</label><div id="custom-rules" class="hidden">' + rulesForm('n', null) + '</div></div>' +
      '<div class="card"><h2>Confirm</h2><label class="check"><input type="checkbox" id="attest"> <span>' + esc(r.attestation) + '</span></label>' +
      '<div id="imp-msg"></div><div class="row-btns"><button class="btn" id="import">Import ' + valid.length + ' accounts</button><button class="btn ghost" id="discard">Discard upload</button></div></div>';

    CL.$('#use-default').onchange = function () { CL.$('#custom-rules').classList.toggle('hidden', this.checked); };
    var rc = CL.$('#recheck');
    if (rc) rc.onclick = function () {
      var fixes = {};
      CL.$$('tr[data-row]').forEach(function (tr) {
        var o = {}; CL.$$('input[data-col]', tr).forEach(function (i) { o[i.dataset.col] = i.value; }); fixes[tr.dataset.row] = o;
      });
      CL.busy(rc, true, 'Checking…');
      post('/uploads/' + b.id + '/revalidate', { fixes: fixes }).then(renderPreview).catch(function (e) { CL.busy(rc, false); fail(e); });
    };
    CL.$('#discard').onclick = function () {
      if (!confirm('Discard this upload? Nothing will be imported.')) return;
      post('/uploads/' + b.id + '/discard').then(function () { location.href = '/collections/app/uploads/'; }).catch(fail);
    };
    CL.$('#import').onclick = function () {
      if (!CL.$('#attest').checked) { CL.$('#imp-msg').innerHTML = '<div class="banner bad">Please tick the confirmation first.</div>'; return; }
      var skip = CL.$$('[data-keep]').filter(function (c) { return !c.checked; }).map(function (c) { return parseInt(c.dataset.keep, 10); });
      var body = { attest: true, skip_rows: skip, rules: CL.$('#use-default').checked ? null : readRules('n') };
      var btn = this; CL.busy(btn, true, 'Importing…');
      post('/uploads/' + b.id + '/import', body).then(function (res) {
        renderPreview({ batch: res.batch, rows: [] });
      }).catch(function (e) { CL.busy(btn, false); CL.$('#imp-msg').innerHTML = '<div class="banner bad">' + esc(e.message) + '</div>'; });
    };
  }

  pages.accounts = function () {
    var status = CL.param('status') || '', q = CL.param('q') || '', pg = parseInt(CL.param('page') || '1', 10), batch = CL.param('batch_id') || '';
    var qs = '?page=' + pg + (status ? '&status=' + encodeURIComponent(status) : '') + (q ? '&q=' + encodeURIComponent(q) : '') + (batch ? '&batch_id=' + encodeURIComponent(batch) : '');
    return get('/accounts' + qs).then(function (d) {
      var link = function (p) {
        var u = new URLSearchParams(location.search); Object.keys(p).forEach(function (k) { p[k] ? u.set(k, p[k]) : u.delete(k); }); return '?' + u.toString();
      };
      view.innerHTML = '<h1>Accounts</h1>' +
        '<div class="tabs">' + [''].concat(d.statuses).map(function (s) {
          return '<button data-s="' + esc(s) + '"' + (s === status ? ' class="on"' : '') + '>' + esc(s || 'All') + '</button>';
        }).join('') + '</div>' +
        '<form id="search" class="row-btns" style="margin-bottom:12px"><input type="search" name="q" value="' + esc(q) + '" placeholder="Search name, phone or reference" style="max-width:340px" aria-label="Search"><button class="btn ghost">Search</button></form>' +
        (batch ? '<p class="small">Showing one upload. <a href="' + link({ batch_id: '' }) + '">Show all</a></p>' : '') +
        '<div class="card table-wrap"><table><thead><tr><th>Ref</th><th>Name</th><th>For</th><th class="right">Balance</th><th>Status</th><th>Last message</th></tr></thead><tbody>' +
        (d.accounts.length ? d.accounts.map(function (a) {
          return '<tr><td class="mono small"><a href="' + accountUrl(a.id) + '">' + esc(a.reference_code) + '</a></td><td>' + esc(a.debtor_name) + '</td><td class="small">' + esc(a.description) + '</td>' +
            '<td class="right nowrap">' + naira(a.balance) + '</td><td>' + CL.pill(a.status) + (a.opted_out ? ' <span class="pill red">Opted out</span>' : '') + '</td><td class="small nowrap">' + (a.last_contacted_at ? CL.date(a.last_contacted_at) : '—') + '</td></tr>';
        }).join('') : '<tr><td colspan="6" class="muted">No accounts here.</td></tr>') + '</tbody></table></div>' +
        (d.total > d.page_size ? '<div class="row-btns">' + (pg > 1 ? '<a class="btn ghost sm" href="' + link({ page: pg - 1 }) + '">Previous</a>' : '') +
          '<span class="small muted" style="align-self:center">Page ' + pg + ' of ' + Math.ceil(d.total / d.page_size) + '</span>' +
          (pg * d.page_size < d.total ? '<a class="btn ghost sm" href="' + link({ page: pg + 1 }) + '">Next</a>' : '') + '</div>' : '');
      CL.$$('[data-s]').forEach(function (b) { b.onclick = function () { location.search = link({ status: b.dataset.s, page: '' }); }; });
      CL.$('#search').onsubmit = function (e) { e.preventDefault(); location.search = link({ q: e.target.q.value, page: '' }); };
    });
  };

  function accountUrl(id) {
    // pretty URL is rewritten by collections/.htaccess; plain static servers (local dev) can't
    return /^(localhost|127\.0\.0\.1)$/.test(location.hostname) ? '/collections/app/accounts/view/?id=' + id : '/collections/app/accounts/' + id;
  }

  function accountIdFromUrl() {
    var m = location.pathname.match(/\/collections\/app\/accounts\/([0-9a-f-]{36})\/?$/);
    return m ? m[1] : CL.param('id');
  }

  pages.account = function () {
    var id = accountIdFromUrl();
    if (!id) { view.innerHTML = '<p>Account not found.</p>'; return Promise.resolve(); }
    return get('/accounts/' + id).then(function (d) {
      var a = d.account, closed = a.status === 'Paid' || a.status === 'Withdrawn';
      var plan = d.plans.filter(function (p) { return p.status === 'Active'; })[0];
      view.innerHTML = '<p class="small"><a href="/collections/app/accounts/">← Accounts</a></p>' +
        '<h1>' + esc(a.debtor_name) + ' ' + CL.pill(a.status) + (a.opted_out ? ' <span class="pill red">Opted out</span>' : '') + '</h1>' +
        '<div class="card"><dl class="kv"><dt>Reference</dt><dd class="mono">' + esc(a.reference_code) + '</dd><dt>For</dt><dd>' + esc(a.description) + (a.dependant_first_name ? ' (' + esc(a.dependant_first_name) + ')' : '') + '</dd>' +
        '<dt>Original amount</dt><dd>' + naira(a.original_amount) + '</dd><dt>Balance</dt><dd>' + naira(a.balance) + '</dd>' +
        '<dt>Phone</dt><dd>' + esc(a.phone || '—') + '</dd><dt>Email</dt><dd>' + esc(a.email || '—') + '</dd>' + (a.due_date ? '<dt>Due</dt><dd>' + CL.date(a.due_date) + '</dd>' : '') + '</dl></div>' +
        (plan ? '<div class="card"><h2>Payment plan</h2><p>' + plan.instalment_count + ' instalments of about ' + naira(plan.instalment_amount) + '</p><ul class="small">' +
          plan.schedule.map(function (s) { return '<li>' + CL.date(s.due) + ': ' + naira(s.amount) + '</li>'; }).join('') + '</ul></div>' : '') +
        '<div class="card"><h2>Payments</h2>' + (d.payments.length ? d.payments.map(paymentRow).join('') : '<p class="muted">No payments yet.</p>') +
        (closed ? '' : '<details style="margin-top:12px;border:0;padding:0"><summary>Record a payment you received directly</summary><form id="rec" style="margin-top:10px"><div class="grid2">' +
          '<div class="field"><label for="ramt">Amount (₦)</label><input id="ramt" name="amount" type="text" inputmode="decimal" required></div>' +
          '<div class="field"><label for="rdate">Date received</label><input id="rdate" name="paid_on" type="date" max="' + CL.todayISO() + '" required></div></div>' +
          '<div class="field"><label for="rnote">Note (optional)</label><input id="rnote" name="note" type="text" maxlength="120" placeholder="e.g. cash at the bursary"></div><button class="btn sm">Record payment</button></form></details>') + '</div>' +
        (d.disputes.length ? '<div class="card"><h2>Queries</h2>' + d.disputes.map(function (x) {
          return '<div class="list-item">' + CL.pill(x.status) + ' <b>' + esc(x.type) + '</b> <span class="small muted">' + CL.date(x.created_at) + '</span>' + (x.details ? '<p class="small">' + esc(x.details) + '</p>' : '') +
            (x.status === 'Open' ? '<a class="btn sm ghost" href="/collections/app/disputes/">Resolve</a>' : '') + '</div>';
        }).join('') + '</div>' : '') +
        '<div class="card"><h2>History</h2><ul class="timeline">' + d.timeline.map(function (t) { return '<li><span class="small muted">' + CL.datetime(t.at) + '</span><br>' + esc(t.text) + '</li>'; }).join('') + '</ul></div>' +
        (closed ? '' : '<div class="card"><h2>Manage</h2><div class="row-btns">' +
          (a.status === 'Paused' ? '<button class="btn ghost" data-act="resume">Resume messages</button>' : (a.status !== 'Disputed' ? '<button class="btn ghost" data-act="pause">Pause messages</button>' : '')) +
          '<button class="btn danger" data-act="withdraw">Withdraw (close, no fee)</button></div><p class="small muted" style="margin-top:8px">Withdraw if this balance shouldn\'t be collected. Messages stop for good.</p></div>');
      bindPaymentActions(pages.account);
      var rec = CL.$('#rec');
      if (rec) rec.onsubmit = function (e) {
        e.preventDefault();
        post('/accounts/' + id + '/payments', CL.formJSON(rec)).then(function () { CL.toast('Payment recorded'); pages.account(); }).catch(fail);
      };
      CL.$$('[data-act]').forEach(function (b) {
        b.onclick = function () {
          if (b.dataset.act === 'withdraw' && !confirm('Withdraw this account? We will stop all messages and no fee will be charged on it.')) return;
          post('/accounts/' + id + '/' + b.dataset.act).then(function () { pages.account(); }).catch(fail);
        };
      });
    });
  };

  function paymentRow(p) {
    var acct = p.account ? '<b>' + esc(p.account.debtor_name) + '</b> <span class="mono small">' + esc(p.account.reference_code) + '</span><br>' : '';
    return '<div class="list-item" data-payment="' + p.id + '">' + acct +
      CL.pill(p.status) + ' <b>' + naira(p.amount_confirmed != null ? p.amount_confirmed : p.amount_claimed) + '</b>' +
      (p.amount_confirmed != null && p.amount_confirmed !== p.amount_claimed ? ' <span class="small muted">(claimed ' + naira(p.amount_claimed) + ')</span>' : '') +
      ' <span class="small muted">paid ' + CL.date(p.paid_on) + ' · ' + esc(p.source) + (p.sender_name ? ' · ' + esc(p.sender_name) : '') + '</span>' +
      (p.possible_duplicate ? ' <span class="pill amber">Possible duplicate</span>' : '') +
      (p.status === 'Confirmed' ? ' <span class="pill' + (p.fee_eligible ? '' : ' green') + '">' + (p.fee_eligible ? 'Fee ' + naira(p.fee_amount) : 'No fee') + '</span>' : '') +
      (p.reject_reason ? '<div class="small muted">' + esc(p.reject_reason) + '</div>' : '') +
      (p.proof_url ? '<div><a class="small" href="' + esc(p.proof_url) + '" target="_blank" rel="noopener">View receipt</a> <span class="tiny muted">(link valid 15 minutes)</span></div>' : '') +
      (p.status === 'Proof submitted' ? '<div class="row-btns" style="margin-top:8px"><input type="text" inputmode="decimal" data-amt placeholder="Amount received (₦)" value="' + (p.amount_claimed / 100) + '" style="max-width:190px;min-height:36px" aria-label="Amount received">' +
        '<button class="btn sm" data-confirm>Confirm received</button><select data-reason style="max-width:170px;min-height:36px" aria-label="Reject reason"><option>Not received</option><option>Wrong amount</option><option>Other</option></select><button class="btn sm ghost" data-reject>Reject</button></div>' : '') +
      (p.status === 'Confirmed' ? '<div style="margin-top:6px"><button class="btn sm ghost" data-reverse>Undo confirmation</button></div>' : '') + '</div>';
  }

  function bindPaymentActions(reload) {
    CL.$$('[data-payment]').forEach(function (row) {
      var id = row.dataset.payment;
      var c = CL.$('[data-confirm]', row), r = CL.$('[data-reject]', row), v = CL.$('[data-reverse]', row);
      if (c) c.onclick = function () {
        CL.busy(c, true, 'Confirming…');
        post('/payments/' + id + '/confirm', { amount_received: CL.$('[data-amt]', row).value }).then(function () { CL.toast('Payment confirmed'); reload(); }).catch(function (e) { CL.busy(c, false); fail(e); });
      };
      if (r) r.onclick = function () {
        var note = prompt('Anything to add? (optional)') || null;
        post('/payments/' + id + '/reject', { reason: CL.$('[data-reason]', row).value, note: note }).then(function () { CL.toast('Proof rejected — the account is back in outreach'); reload(); }).catch(fail);
      };
      if (v) v.onclick = function () {
        var why = prompt('Why are you undoing this confirmation?'); if (!why) return;
        post('/payments/' + id + '/reverse', { reason: why }).then(function () { CL.toast('Confirmation undone'); reload(); }).catch(fail);
      };
    });
  }

  pages.confirmations = function () {
    var status = CL.param('status') || 'Proof submitted';
    return get('/confirmations?status=' + encodeURIComponent(status)).then(function (rows) {
      view.innerHTML = '<h1>Confirmations</h1><p class="muted">Check your bank statement, then confirm what actually arrived. Only confirmed payments count.</p>' +
        '<div class="tabs">' + [['Proof submitted', 'Waiting'], ['Confirmed', 'Confirmed'], ['Rejected', 'Rejected']].map(function (t) {
          return '<button data-s="' + t[0] + '"' + (t[0] === status ? ' class="on"' : '') + '>' + t[1] + '</button>';
        }).join('') + '</div>' +
        '<div class="card">' + (rows.length ? rows.map(paymentRow).join('') : '<p class="muted">Nothing here.</p>') + '</div>';
      CL.$$('[data-s]').forEach(function (b) { b.onclick = function () { location.search = '?status=' + encodeURIComponent(b.dataset.s); }; });
      bindPaymentActions(pages.confirmations);
    });
  };

  pages.disputes = function () {
    var status = CL.param('status') || 'Open';
    return get('/disputes?status=' + encodeURIComponent(status)).then(function (rows) {
      view.innerHTML = '<h1>Disputes</h1><p class="muted">Messages to these people are paused until you decide.</p>' +
        '<div class="tabs">' + ['Open', 'All'].map(function (s) { return '<button data-s="' + s + '"' + (s === status ? ' class="on"' : '') + '>' + s + '</button>'; }).join('') + '</div>' +
        (rows.length ? rows.map(function (d) {
          return '<div class="card" data-dispute="' + d.id + '"><h3>' + esc(d.account.debtor_name) + ' <span class="mono small">' + esc(d.account.reference_code) + '</span> ' + CL.pill(d.status) + '</h3>' +
            '<p><b>' + esc(d.type) + '</b> <span class="small muted">raised by ' + esc(d.raised_by === 'Ops' ? 'Waterline (from a reply)' : 'the customer') + ' on ' + CL.date(d.created_at) + '</span></p>' +
            (d.details ? '<p class="msg">' + esc(d.details) + '</p>' : '') +
            (d.evidence_url ? '<p><a href="' + esc(d.evidence_url) + '" target="_blank" rel="noopener">View their evidence</a> <span class="tiny muted">(link valid 15 minutes)</span></p>' : '') +
            '<p class="small">' + esc(d.account.description) + ' · balance ' + naira(d.account.balance) + ' of ' + naira(d.account.original_amount) + '</p>' +
            (d.status === 'Open' ? '<div class="field"><label>Outcome</label><select data-outcome><option value="Uphold">Balance is correct — resume messages</option><option value="Adjust">Correct the balance — resume messages</option><option value="Withdraw">Withdraw — close the account, no fee</option></select></div>' +
              '<div class="field hidden" data-newbal-wrap><label>Corrected balance (₦)</label><input type="text" inputmode="decimal" data-newbal></div>' +
              '<div class="field"><label>Note (optional)</label><input type="text" data-note maxlength="500"></div><button class="btn" data-resolve>Save decision</button>' :
              (d.resolution_note ? '<p class="small muted">Note: ' + esc(d.resolution_note) + '</p>' : '')) + '</div>';
        }).join('') : '<div class="card"><p class="muted">No disputes here.</p></div>');
      CL.$$('[data-s]').forEach(function (b) { b.onclick = function () { location.search = '?status=' + b.dataset.s; }; });
      CL.$$('[data-dispute]').forEach(function (card) {
        var sel = CL.$('[data-outcome]', card); if (!sel) return;
        sel.onchange = function () { CL.$('[data-newbal-wrap]', card).classList.toggle('hidden', sel.value !== 'Adjust'); };
        CL.$('[data-resolve]', card).onclick = function () {
          if (sel.value === 'Withdraw' && !confirm('Withdraw this account? Messages stop for good and no fee is charged.')) return;
          post('/disputes/' + card.dataset.dispute + '/resolve', { outcome: sel.value, new_balance: CL.$('[data-newbal]', card).value || null, note: CL.$('[data-note]', card).value || null })
            .then(function () { CL.toast('Saved'); pages.disputes(); }).catch(fail);
        };
      });
    });
  };

  pages.invoices = function () {
    return get('/invoices').then(function (d) {
      var pay = d.pay_to;
      view.innerHTML = '<h1>Invoices</h1><p class="muted">One invoice a month for payments you confirmed that came in after we contacted the person.</p>' +
        '<div class="card"><h2>Pay Waterline to</h2><dl class="kv"><dt>Bank</dt><dd>' + esc(pay.bank_name) + '</dd><dt>Account number</dt><dd class="mono">' + esc(pay.account_number) + '</dd><dt>Account name</dt><dd>' + esc(pay.account_name) + '</dd></dl><p class="small muted" style="margin-top:8px">Use the invoice number as your transfer narration.</p></div>' +
        (d.invoices.length ? '<div class="card table-wrap"><table><thead><tr><th>Invoice</th><th>Month</th><th class="right">Recovered</th><th class="right">Fee</th><th>Due</th><th>Status</th></tr></thead><tbody>' +
          d.invoices.map(function (i) {
            return '<tr><td><a href="#" data-inv="' + i.id + '" class="mono small">' + esc(i.number) + '</a></td><td>' + CL.date(i.period_start).slice(3) + '</td><td class="right">' + naira(i.total_confirmed) + '</td><td class="right"><b>' + naira(i.fee_amount) + '</b></td><td class="nowrap">' + CL.date(i.due_at) + '</td><td>' + CL.pill(i.status) + '</td></tr>' +
              '<tr class="hidden" id="inv-' + i.id + '"><td colspan="6"></td></tr>';
          }).join('') + '</tbody></table></div>' : '<div class="card"><p class="muted">No invoices yet.</p></div>');
      CL.$$('[data-inv]').forEach(function (a) {
        a.onclick = function (e) {
          e.preventDefault();
          var row = CL.$('#inv-' + a.dataset.inv);
          if (!row.classList.contains('hidden')) { row.classList.add('hidden'); return; }
          get('/invoices/' + a.dataset.inv).then(function (r) {
            var inv = r.invoice;
            row.firstChild.innerHTML = '<table><thead><tr><th>Ref</th><th>Name</th><th>Paid on</th><th class="right">Amount</th><th class="right">Fee (' + inv.fee_percent + '%)</th></tr></thead><tbody>' +
              inv.lines.map(function (l) { return '<tr><td class="mono small">' + esc(l.reference_code) + '</td><td>' + esc(l.debtor_name) + '</td><td>' + CL.date(l.paid_on) + '</td><td class="right">' + naira(l.amount) + '</td><td class="right">' + naira(l.fee) + '</td></tr>'; }).join('') +
              '<tr><td colspan="3"><b>Total</b></td><td class="right"><b>' + naira(inv.total_confirmed) + '</b></td><td class="right"><b>' + naira(inv.fee_amount) + '</b></td></tr></tbody></table>';
            row.classList.remove('hidden');
          }).catch(fail);
        };
      });
    });
  };

  pages.settings = function () {
    return Promise.all([get('/me'), get('/team'), CL.api('/collections/public/config')]).then(function (res) {
      var c = res[0].client, me = res[0].user, team = res[1], cfg = res[2], owner = me.role === 'Owner';
      var opts = function (list, v) { return list.map(function (x) { return '<option' + (x === v ? ' selected' : '') + '>' + esc(x) + '</option>'; }).join(''); };
      var dis = owner ? '' : ' disabled';
      view.innerHTML = '<h1>Settings</h1>' + (owner ? '' : '<div class="banner info">Only the account owner can change these settings.</div>') +
        '<form class="card" id="biz"><h2>Business details</h2>' +
        '<div class="field"><label for="bn">Business name</label><input id="bn" name="business_name" type="text" value="' + esc(c.business_name) + '"' + dis + '></div>' +
        '<div class="grid2"><div class="field"><label for="bt">Business type</label><select id="bt" name="business_type"' + dis + '>' + opts(cfg.business_types, c.business_type) + '</select></div>' +
        '<div class="field"><label for="st">State</label><select id="st" name="state"' + dis + '>' + opts(cfg.states, c.state) + '</select></div></div>' +
        '<div class="field"><label for="ct">City</label><input id="ct" name="city" type="text" value="' + esc(c.city) + '"' + dis + '></div>' +
        '<h2>Bank account your customers pay into</h2><div class="field"><label for="bk">Bank name</label><input id="bk" name="bank_name" type="text" value="' + esc(c.bank_name) + '"' + dis + '></div>' +
        '<div class="grid2"><div class="field"><label for="an">Account number</label><input id="an" name="account_number" type="text" inputmode="numeric" maxlength="10" value="' + esc(c.account_number) + '"' + dis + '></div>' +
        '<div class="field"><label for="am">Account name</label><input id="am" name="account_name" type="text" value="' + esc(c.account_name) + '"' + dis + '></div></div>' +
        (owner ? '<button class="btn">Save details</button>' : '') + '</form>' +
        '<div class="card"><h2>Default recovery rules</h2><p class="small muted">Used for uploads that don\'t set their own. You can\'t change message wording — Waterline keeps every message polite and consistent.</p>' + rulesForm('d', c.default_rules) +
        (owner ? '<button class="btn" id="save-rules">Save default rules</button>' : '') + '</div>' +
        '<div class="card"><h2>Team</h2><p class="small muted">Add up to ' + team.max_teammates + ' teammates.</p>' +
        team.users.map(function (u) {
          return '<div class="list-item">' + esc(u.name) + ' <span class="small muted">' + esc(u.email) + ' · ' + esc(u.role) + (u.is_active ? '' : ' · removed') + (u.email_verified ? '' : ' · invited') + '</span>' +
            (owner && u.role !== 'Owner' && u.is_active ? ' <button class="btn sm ghost" data-remove="' + u.id + '">Remove</button>' : '') + '</div>';
        }).join('') +
        (owner ? '<form id="invite" class="grid2" style="margin-top:12px"><div class="field"><label for="iname">Name</label><input id="iname" name="name" type="text" required></div><div class="field"><label for="iemail">Email</label><input id="iemail" name="email" type="email" required></div><div><button class="btn sm">Invite teammate</button></div></form>' : '') + '</div>' +
        '<div class="card"><h2>Your data</h2><p class="small muted">Fee: ' + c.fee_percent + '% of confirmed recoveries · attribution window ' + c.attribution_days + ' days · upload limit ' + c.upload_row_cap + ' rows.</p>' +
        (owner ? '<div class="row-btns"><button class="btn ghost" id="export">Export all my data (CSV)</button><button class="btn ghost" id="delete-req">Request data deletion</button></div>' : '') + '</div>';
      if (!owner) { CL.$$('#view input, #view select').forEach(function (el) { el.disabled = true; }); return; }
      CL.$('#biz').onsubmit = function (e) { e.preventDefault(); put('/settings', CL.formJSON(e.target)).then(function () { CL.toast('Saved'); }).catch(fail); };
      CL.$('#save-rules').onclick = function () { put('/settings', { default_rules: readRules('d') }).then(function () { CL.toast('Default rules saved'); }).catch(fail); };
      CL.$('#invite').onsubmit = function (e) { e.preventDefault(); post('/team', CL.formJSON(e.target)).then(function () { CL.toast('Invitation sent'); pages.settings(); }).catch(fail); };
      CL.$$('[data-remove]').forEach(function (b) {
        b.onclick = function () {
          if (!confirm('Remove this teammate?')) return;
          CL.api('/collections/client/team/' + b.dataset.remove, { auth: 'client', method: 'DELETE' }).then(function () { pages.settings(); }).catch(fail);
        };
      });
      CL.$('#export').onclick = function () { CL.download('/collections/client/export.csv', 'client', 'waterline-collections-export.csv').catch(fail); };
      CL.$('#delete-req').onclick = function () {
        var note = prompt('Tell us what you want deleted (optional). We will confirm by email.');
        if (note === null) return;
        post('/deletion-request', { note: note }).then(function (r) { CL.toast(r.message); }).catch(fail);
      };
    });
  };

  get('/me').then(function (me) {
    ME = me; shell(me);
    return (pages[page] || pages.dashboard)();
  }).catch(function (err) {
    if (err.status !== 401) view.innerHTML = '<div class="banner bad">' + esc(err.message) + '</div>';
  });
})();
