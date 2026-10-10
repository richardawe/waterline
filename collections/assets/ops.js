/* Waterline ops console (/collections/ops/). Separate login with a 6-digit authenticator code. */
(function () {
  var view = CL.$('#view'), esc = CL.esc, naira = CL.naira;
  var ME = null;
  var TABS = [['overview', 'Overview'], ['approvals', 'Approvals'], ['queue', 'Outreach queue'], ['replies', 'Replies'],
    ['followups', 'Follow-ups'], ['clients', 'Clients'], ['invoices', 'Invoices'], ['templates', 'Templates'], ['config', 'Config'], ['audit', 'Audit']];
  var CHECK_LABELS = { phone_call_made: 'Phone call made', business_exists: 'Business confirmed to exist', bank_name_matches: 'Bank account name matches business', contact_confirmed: 'Contact person confirmed' };

  function get(p) { return CL.api('/collections/ops' + p, { auth: 'ops' }); }
  function post(p, json) { return CL.api('/collections/ops' + p, { auth: 'ops', method: 'POST', json: json || {} }); }
  function put(p, json) { return CL.api('/collections/ops' + p, { auth: 'ops', method: 'PUT', json: json }); }
  function fail(e) { CL.toast(e.message || String(e)); }
  function tab() { return (location.hash || '#overview').slice(1).split('/')[0]; }

  function loginForm() {
    CL.$('#shell').innerHTML = '<header class="bar"><div class="wrap bar-in"><span class="logo"><span class="logo-mark">W</span>Collections ops</span></div></header>';
    view.innerHTML = '<div class="narrow"><h1>Ops sign-in</h1><form id="ol" class="card">' +
      '<div class="field"><label for="e">Email</label><input id="e" name="email" type="email" required autocomplete="username"></div>' +
      '<div class="field"><label for="p">Password</label><input id="p" name="password" type="password" required autocomplete="current-password"></div>' +
      '<div class="field"><label for="c">Authenticator code</label><input id="c" name="code" type="text" inputmode="numeric" maxlength="6" required autocomplete="one-time-code"></div>' +
      '<div id="m"></div><button class="btn block">Sign in</button></form></div>';
    CL.$('#ol').onsubmit = function (e) {
      e.preventDefault();
      CL.api('/collections/public/ops-login', { json: CL.formJSON(e.target) }).then(function (r) {
        CL.setToken('ops', r.token); start();
      }).catch(function (err) { CL.$('#m').innerHTML = '<div class="banner bad">' + esc(err.message) + '</div>'; });
    };
  }

  function shell() {
    var on = tab();
    CL.$('#shell').innerHTML = '<header class="bar"><div class="wrap bar-in"><span class="logo"><span class="logo-mark">W</span>Collections ops</span>' +
      '<button class="menu-btn" aria-label="Menu">☰</button><nav class="menu">' + TABS.map(function (t) {
        return '<a href="#' + t[0] + '"' + (t[0] === on ? ' class="on"' : '') + '>' + t[1] + '</a>';
      }).join('') + '<a href="#" id="out">Sign out (' + esc(ME.name.split(' ')[0]) + ')</a></nav></div></header>';
    CL.$('.menu-btn').onclick = function () { CL.$('.menu').classList.toggle('open'); };
    CL.$('#out').onclick = function (e) {
      e.preventDefault();
      post('/logout').finally(function () { CL.setToken('ops', null); location.reload(); });
    };
  }

  var R = {};

  R.overview = function () {
    return get('/overview').then(function (o) {
      view.innerHTML = '<h1>Today — ' + CL.date(o.today) + '</h1>' +
        '<div class="banner ' + (o.in_contact_window ? 'ok' : 'warn') + '">' + (o.in_contact_window ? 'Inside the contact window (8am–7pm WAT, Mon–Sat).' : 'Outside the contact window — messages can\'t be marked sent right now.') + '</div>' +
        '<div class="stats">' +
        s(o.pending_approvals, 'Pending approvals', '#approvals') + s(o.queue_remaining, 'Messages left to send today', '#queue') +
        s(o.overdue_confirmations, 'Confirmations overdue (5+ working days)', '#followups') + s(o.stale_disputes, 'Disputes open 10+ working days', '#followups') +
        s(o.flagged_clients, 'Flagged clients', '#clients') + s(o.draft_invoices, 'Draft invoices', '#invoices') + '</div>' +
        (ME.role === 'Admin' ? '<button class="btn ghost" id="daily">Run daily jobs now</button>' : '');
      var d = CL.$('#daily');
      if (d) d.onclick = function () { post('/jobs/daily').then(function (r) { CL.toast('Done: ' + r.queue.created + ' queued'); R.overview(); }).catch(fail); };
    });
    function s(n, l, href) { return '<a class="stat" style="text-decoration:none" href="' + href + '"><b>' + n + '</b><span>' + l + '</span></a>'; }
  };

  function clientCard(c) {
    var checks = c.verification_checklist || {};
    return '<div class="card" data-client="' + c.id + '"><h2>' + esc(c.business_name) + ' ' + CL.pill(c.status) + (c.flagged_at ? ' <span class="pill red">Flagged</span>' : '') + '</h2>' +
      '<dl class="kv small"><dt>Type</dt><dd>' + esc(c.business_type) + '</dd><dt>Location</dt><dd>' + esc(c.city + ', ' + c.state) + '</dd>' +
      '<dt>Bank</dt><dd>' + esc(c.bank_name + ' · ' + c.account_number + ' · ' + c.account_name) + '</dd><dt>Signed up</dt><dd>' + CL.datetime(c.created_at) + '</dd>' +
      '<dt>Accounts</dt><dd>' + c.accounts + ' · recovered ' + naira(c.recovered) + '</dd><dt>Fee / window / cap</dt><dd>' + c.fee_percent + '% · ' + c.attribution_days + ' days · ' + c.upload_row_cap + ' rows</dd>' +
      (c.flagged_reason ? '<dt>Flag</dt><dd style="color:var(--red)">' + esc(c.flagged_reason) + '</dd>' : '') +
      (c.status_reason ? '<dt>Reason</dt><dd>' + esc(c.status_reason) + '</dd>' : '') + '</dl>' +
      '<div data-users class="small muted" style="margin:8px 0"></div>' +
      '<details style="margin-top:10px"><summary>Verification checks</summary><div style="margin-top:10px">' +
      Object.keys(CHECK_LABELS).map(function (k) { return '<label class="check"><input type="checkbox" data-check="' + k + '"' + (checks[k] ? ' checked' : '') + '> ' + CHECK_LABELS[k] + '</label>'; }).join('') +
      '<div class="field"><label>Notes</label><textarea data-notes>' + esc(c.verification_notes || '') + '</textarea></div><button class="btn sm ghost" data-save-checks>Save checks</button></div></details>' +
      '<div class="row-btns" style="margin-top:12px">' +
      (c.status === 'Pending' || c.status === 'Suspended' || c.status === 'Rejected' ? '<button class="btn sm" data-status="Approved">Approve</button>' : '') +
      (c.status === 'Pending' ? '<button class="btn sm danger" data-status="Rejected">Reject</button>' : '') +
      (c.status === 'Approved' ? '<button class="btn sm danger" data-status="Suspended">Suspend outreach</button>' : '') +
      '<button class="btn sm ghost" data-limits>Limits &amp; fee</button>' + (c.flagged_at ? '<button class="btn sm ghost" data-unflag>Clear flag</button>' : '') +
      '<button class="btn sm ghost" data-audit>Audit CSV</button>' + (ME.role === 'Admin' ? '<button class="btn sm ghost" data-anon>Delete data…</button>' : '') + '</div></div>';
  }

  function bindClientCards(reload) {
    CL.$$('[data-client]').forEach(function (card) {
      var id = card.dataset.client;
      get('/clients/' + id).then(function (d) {
        CL.$('[data-users]', card).innerHTML = 'Team: ' + d.users.map(function (u) { return esc(u.name + ' (' + u.role + (u.job_title ? ', ' + u.job_title : '') + ') ' + u.email + (u.phone ? ' ' + u.phone : '')); }).join(' · ') +
          (d.batches.length ? '<br>Uploads: ' + d.batches.map(function (b) { return esc(b.file_name) + ' (' + b.imported_count + (b.flagged ? ', flagged' : '') + ')'; }).join(', ') : '');
      });
      CL.$('[data-save-checks]', card).onclick = function () {
        var cl = {}; CL.$$('[data-check]', card).forEach(function (c) { cl[c.dataset.check] = c.checked; });
        put('/clients/' + id + '/verification', { notes: CL.$('[data-notes]', card).value, checklist: cl }).then(function () { CL.toast('Checks saved'); }).catch(fail);
      };
      CL.$$('[data-status]', card).forEach(function (b) {
        b.onclick = function () {
          var st = b.dataset.status, reason = null;
          if (st !== 'Approved') { reason = prompt('Reason (sent to the client):'); if (!reason) return; }
          post('/clients/' + id + '/status', { status: st, reason: reason }).then(function () { CL.toast('Client ' + st.toLowerCase()); reload(); }).catch(fail);
        };
      });
      CL.$('[data-limits]', card).onclick = function () {
        var cap = prompt('Upload row limit:'); if (cap === null) return;
        var body = { upload_row_cap: parseInt(cap, 10) };
        if (ME.role === 'Admin') {
          var fee = prompt('Fee % (blank = unchanged):'); if (fee) body.fee_percent = parseFloat(fee);
          var days = prompt('Attribution window in days (blank = unchanged):'); if (days) body.attribution_days = parseInt(days, 10);
        }
        put('/clients/' + id + '/limits', body).then(function () { CL.toast('Saved'); reload(); }).catch(fail);
      };
      var uf = CL.$('[data-unflag]', card);
      if (uf) uf.onclick = function () { put('/clients/' + id + '/limits', { clear_flag: true }).then(reload).catch(fail); };
      CL.$('[data-audit]', card).onclick = function () { CL.download('/collections/ops/audit.csv?client_id=' + id, 'ops', 'audit-' + id.slice(0, 8) + '.csv').catch(fail); };
      var an = CL.$('[data-anon]', card);
      if (an) an.onclick = function () {
        if (prompt('This anonymises every debtor record for this client and deletes their files. Type DELETE to confirm.') !== 'DELETE') return;
        post('/clients/' + id + '/anonymise').then(function (r) { CL.toast(r.anonymised + ' accounts anonymised'); }).catch(fail);
      };
    });
  }

  R.approvals = function () {
    return get('/clients?status=Pending').then(function (rows) {
      view.innerHTML = '<h1>Approvals</h1><p class="muted">Call the business, confirm it exists, check the bank account name matches, and confirm the contact person. Nothing is sent for a client until you approve.</p>' +
        (rows.length ? rows.map(clientCard).join('') : '<div class="card"><p class="muted">No clients waiting.</p></div>');
      bindClientCards(R.approvals);
    });
  };

  R.clients = function () {
    var f = CL.param('f') || '';
    return get('/clients' + (f === 'flagged' ? '?flagged=true' : f ? '?status=' + f : '')).then(function (rows) {
      view.innerHTML = '<h1>Clients</h1><div class="tabs">' + [['', 'All'], ['Approved', 'Approved'], ['Suspended', 'Suspended'], ['Rejected', 'Rejected'], ['flagged', 'Flagged']].map(function (t) {
        return '<button data-f="' + t[0] + '"' + (t[0] === f ? ' class="on"' : '') + '>' + t[1] + '</button>';
      }).join('') + '</div>' +
        '<form id="find" class="row-btns" style="margin-bottom:14px"><input type="search" name="q" placeholder="Find a debtor by reference (WL-…) or phone" style="max-width:360px" aria-label="Find a debtor"><button class="btn ghost">Find</button></form><div id="found"></div>' +
        (rows.length ? rows.map(clientCard).join('') : '<div class="card"><p class="muted">None.</p></div>');
      CL.$$('[data-f]').forEach(function (b) { b.onclick = function () { history.replaceState(null, '', '?f=' + b.dataset.f + '#clients'); R.clients(); }; });
      CL.$('#find').onsubmit = function (e) {
        e.preventDefault();
        get('/accounts/search?q=' + encodeURIComponent(e.target.q.value)).then(function (list) {
          CL.$('#found').innerHTML = '<div class="card">' + (list.length ? list.map(function (a) {
            return '<div class="list-item" data-acc="' + a.id + '"><b>' + esc(a.debtor_name) + '</b> <span class="mono small">' + esc(a.reference_code) + '</span> · ' + esc(a.client_name) + ' · ' + naira(a.balance) + ' ' + CL.pill(a.status) +
              (a.opted_out ? ' <span class="pill red">Opted out</span>' : '') + '<div class="row-btns" style="margin-top:6px">' +
              (a.last_sent_item_id ? '<button class="btn sm ghost" data-reply-item="' + a.last_sent_item_id + '">Log a reply</button>' : '') +
              '<button class="btn sm ghost" data-reissue>Reissue link</button></div></div>';
          }).join('') : '<p class="muted">No match.</p>') + '</div>';
          bindReplyButtons();
          CL.$$('[data-reissue]').forEach(function (b) {
            b.onclick = function () {
              if (!confirm('Revoke the current link and issue a new one?')) return;
              post('/accounts/' + b.closest('[data-acc]').dataset.acc + '/reissue-link').then(function (r) { prompt('New link (the old one no longer works):', r.link); }).catch(fail);
            };
          });
        }).catch(fail);
      };
      bindClientCards(R.clients);
    });
  };

  var REPLY_CATS = ['Promised to pay', 'Already paid', 'Disputes amount', 'Asks for plan', 'Asks to stop', 'Other'];
  function bindReplyButtons(reload) {
    CL.$$('[data-reply-item]').forEach(function (b) {
      b.onclick = function () {
        var cat = prompt('Reply category — type the number:\n' + REPLY_CATS.map(function (c, i) { return (i + 1) + '. ' + c; }).join('\n'));
        var c = REPLY_CATS[parseInt(cat, 10) - 1]; if (!c) return;
        var note = prompt('What did they say? (optional)') || null;
        post('/queue/' + b.dataset.replyItem + '/reply', { category: c, note: note }).then(function () {
          CL.toast(c === 'Asks to stop' ? 'Opted out' : (c === 'Disputes amount' || c === 'Already paid') ? 'Reply logged — dispute opened' : 'Reply logged');
          if (reload) reload();
        }).catch(fail);
      };
    });
  }

  R.queue = function () {
    var day = CL.param('day') || '';
    return get('/queue' + (day ? '?day=' + day : '')).then(function (q) {
      var queued = q.items.filter(function (i) { return i.status === 'Queued'; });
      var done = q.items.filter(function (i) { return i.status !== 'Queued'; });
      view.innerHTML = '<h1>Outreach queue — ' + CL.date(q.date) + '</h1>' +
        '<div class="banner ' + (q.in_contact_window ? 'ok' : 'warn') + '">' + (q.in_contact_window ? 'Inside the contact window. Send from the Waterline phone, then mark each one.' : 'Outside 8am–7pm WAT Mon–Sat (or a public holiday). Sending is blocked.') + '</div>' +
        '<div class="row-btns" style="margin-bottom:14px"><button class="btn ghost" id="build">Build / refresh today\'s queue</button><span class="small muted" style="align-self:center">' + queued.length + ' to send · ' + done.length + ' done</span></div>' +
        (queued.length ? queued.map(function (i) {
          return '<div class="card" data-item="' + i.id + '"><h3>' + esc(i.debtor_name) + ' <span class="small muted">' + esc(i.phone) + '</span></h3>' +
            '<p class="small muted">' + esc(i.client_name) + ' · ' + esc(i.reference_code) + ' · ' + esc(i.template_key.replace(/_/g, ' ').toLowerCase()) + (i.has_email ? ' · has email' : '') + '</p>' +
            '<div class="msg">' + esc(i.rendered_text) + '</div>' +
            '<div class="row-btns" style="margin-top:10px"><a class="btn wa" href="' + esc(i.links.whatsapp) + '" target="_blank" rel="noopener">Open WhatsApp</a><a class="btn ghost" href="' + esc(i.links.sms) + '">Open SMS</a></div>' +
            '<div class="row-btns" style="margin-top:8px"><button class="btn sm" data-sent="WhatsApp">Mark sent (WhatsApp)</button><button class="btn sm" data-sent="SMS">Mark sent (SMS)</button>' +
            '<select data-skip-reason style="max-width:170px;min-height:34px" aria-label="Skip reason">' + q.skip_reasons.map(function (r) { return '<option>' + esc(r) + '</option>'; }).join('') + '</select><button class="btn sm ghost" data-skip>Skip' + (i.has_email ? ' (email instead)' : '') + '</button></div></div>';
        }).join('') : '<div class="card"><p class="muted">Nothing left to send.</p></div>') +
        (done.length ? '<div class="card"><h2>Done today</h2>' + done.map(function (i) {
          return '<div class="list-item">' + CL.pill(i.status) + ' <b>' + esc(i.debtor_name) + '</b> <span class="small muted">' + esc(i.client_name) + (i.channel ? ' · ' + i.channel : '') + (i.skip_reason ? ' · ' + esc(i.skip_reason) : '') + (i.reply_category ? ' · replied: ' + esc(i.reply_category) : '') + '</span>' +
            (i.status === 'Sent' ? ' <button class="btn sm ghost" data-reply-item="' + i.id + '">Log reply</button>' : '') + '</div>';
        }).join('') + '</div>' : '');
      CL.$('#build').onclick = function () { post('/queue/build').then(function (r) { CL.toast(r.reason || (r.created + ' added')); R.queue(); }).catch(fail); };
      CL.$$('[data-item]').forEach(function (card) {
        var id = card.dataset.item;
        CL.$$('[data-sent]', card).forEach(function (b) {
          b.onclick = function () { post('/queue/' + id + '/sent', { channel: b.dataset.sent }).then(function () { card.remove(); CL.toast('Marked sent'); }).catch(function (e) { fail(e); R.queue(); }); };
        });
        CL.$('[data-skip]', card).onclick = function () {
          var reason = CL.$('[data-skip-reason]', card).value, note = reason === 'Other' ? prompt('What happened?') : null;
          post('/queue/' + id + '/skip', { reason: reason, note: note }).then(function (r) { card.remove(); CL.toast(r.channel === 'Email' ? 'Sent by email instead' : 'Skipped'); }).catch(fail);
        };
      });
      bindReplyButtons(R.queue);
    });
  };

  R.replies = function () {
    return get('/replies').then(function (rows) {
      view.innerHTML = '<h1>Replies (last 14 days)</h1><p class="muted">To log a reply that came in later, find the debtor under Clients → Find.</p><div class="card">' + (rows.length ? rows.map(function (i) {
        return '<div class="list-item"><b>' + esc(i.debtor_name) + '</b> <span class="small muted">' + esc(i.client_name) + ' · ' + esc(i.reference_code) + ' · ' + CL.datetime(i.replied_at) + '</span><br>' + CL.pill(i.reply_category) + (i.reply_note ? ' ' + esc(i.reply_note) : '') + '</div>';
      }).join('') : '<p class="muted">No replies logged.</p>') + '</div>';
    });
  };

  R.followups = function () {
    return get('/follow-ups').then(function (f) {
      function list(rows, fn) { return rows.length ? rows.map(fn).join('') : '<p class="muted">None.</p>'; }
      view.innerHTML = '<h1>Follow-ups</h1>' +
        '<div class="card"><h2>Proofs waiting 5+ working days for client confirmation</h2>' + list(f.overdue_confirmations, function (p) {
          return '<div class="list-item">' + esc(p.client_name) + ' · <span class="mono small">' + esc(p.reference_code) + '</span> · ' + naira(p.amount_claimed) + ' · submitted ' + CL.date(p.created_at) + '</div>';
        }) + '</div>' +
        '<div class="card"><h2>Disputes open 10+ working days</h2>' + list(f.stale_disputes, function (d) {
          return '<div class="list-item">' + esc(d.client_name) + ' · <span class="mono small">' + esc(d.reference_code) + '</span> · ' + esc(d.type) + ' · opened ' + CL.date(d.created_at) + '</div>';
        }) + '</div>' +
        '<div class="card"><h2>Proofs rejected by clients (14 days)</h2>' + list(f.recent_rejections, function (p) {
          return '<div class="list-item">' + esc(p.client_name) + ' · <span class="mono small">' + esc(p.reference_code) + '</span> · ' + naira(p.amount_claimed) + ' · ' + esc(p.reject_reason || '') + '</div>';
        }) + '</div>';
    });
  };

  R.invoices = function () {
    return get('/invoices').then(function (rows) {
      var last = new Date(); last.setDate(1); last.setMonth(last.getMonth() - 1);
      view.innerHTML = '<h1>Invoices</h1><div class="card"><h2>Draft invoices for a month</h2><p class="small muted">Runs automatically on the 1st for the previous month. Re-running adds any new eligible confirmations to existing drafts.</p>' +
        '<div class="row-btns"><input type="month" id="mon" value="' + last.toISOString().slice(0, 7) + '" style="max-width:200px" aria-label="Month"><button class="btn ghost" id="draft">Draft</button></div></div>' +
        '<div class="card table-wrap"><table><thead><tr><th>Invoice</th><th>Client</th><th>Month</th><th class="right">Recovered</th><th class="right">Fee</th><th>Status</th><th></th></tr></thead><tbody>' +
        (rows.length ? rows.map(function (i) {
          return '<tr><td class="mono small"><a href="#" data-view="' + i.id + '">' + esc(i.number) + '</a></td><td>' + esc(i.client_name) + '</td><td>' + CL.date(i.period_start).slice(3) + '</td><td class="right">' + naira(i.total_confirmed) + '</td><td class="right">' + naira(i.fee_amount) + '</td><td>' + CL.pill(i.status) + '</td><td class="nowrap">' +
            (i.status === 'Draft' ? '<button class="btn sm" data-issue="' + i.id + '">Issue</button>' : '') + (i.status === 'Issued' || i.status === 'Overdue' ? '<button class="btn sm ghost" data-paid="' + i.id + '">Mark paid</button>' : '') + '</td></tr>' +
            '<tr class="hidden" id="iv-' + i.id + '"><td colspan="7"></td></tr>';
        }).join('') : '<tr><td colspan="7" class="muted">No invoices yet.</td></tr>') + '</tbody></table></div>';
      CL.$('#draft').onclick = function () { post('/invoices/draft', { month: CL.$('#mon').value + '-01' }).then(function (r) { CL.toast(r.length + ' draft(s) updated'); R.invoices(); }).catch(fail); };
      CL.$$('[data-issue]').forEach(function (b) { b.onclick = function () { if (confirm('Issue this invoice and email the client?')) post('/invoices/' + b.dataset.issue + '/issue').then(R.invoices).catch(fail); }; });
      CL.$$('[data-paid]').forEach(function (b) { b.onclick = function () { if (confirm('Mark as paid?')) post('/invoices/' + b.dataset.paid + '/paid').then(R.invoices).catch(fail); }; });
      CL.$$('[data-view]').forEach(function (a) {
        a.onclick = function (e) {
          e.preventDefault();
          var row = CL.$('#iv-' + a.dataset.view);
          get('/invoices/' + a.dataset.view).then(function (inv) {
            row.firstChild.innerHTML = '<table><tbody>' + inv.lines.map(function (l) {
              return '<tr><td class="mono small">' + esc(l.reference_code) + '</td><td>' + esc(l.debtor_name) + '</td><td>' + CL.date(l.paid_on) + '</td><td class="right">' + naira(l.amount) + '</td><td class="right">' + naira(l.fee) + '</td></tr>';
            }).join('') + '</tbody></table>';
            row.classList.toggle('hidden');
          }).catch(fail);
        };
      });
    });
  };

  R.templates = function () {
    return get('/templates').then(function (rows) {
      var admin = ME.role === 'Admin';
      var active = rows.filter(function (t) { return t.is_active; });
      view.innerHTML = '<h1>Message templates</h1><p class="muted">Placeholders: {{debtor_first_name}}, {{client_name}}, {{description}}, {{balance}}, {{link}}, {{discount_line}}, {{instalment_amount}}, {{due_date}}. Every template must include {{client_name}}, “via Waterline”, {{link}} and “Reply STOP to stop messages.” Threats, police/court/arrest, blacklisting, exams/results/exclusion and shaming are blocked.</p>' +
        active.map(function (t) {
          var history = rows.filter(function (h) { return h.key === t.key && h.language === t.language && !h.is_active; });
          return '<div class="card" data-key="' + esc(t.key) + '"><h2>' + esc(t.key) + ' <span class="small muted">v' + t.version + ' · ' + esc(t.language) + '</span></h2>' +
            (admin ? '<textarea data-body style="min-height:120px">' + esc(t.body) + '</textarea><button class="btn sm" data-save style="margin-top:8px">Save as new version</button>' : '<div class="msg">' + esc(t.body) + '</div>') +
            (history.length ? '<details style="margin-top:10px"><summary>Earlier versions</summary>' + history.map(function (h) { return '<p class="small"><b>v' + h.version + '</b> ' + esc(h.body) + '</p>'; }).join('') + '</details>' : '') + '</div>';
        }).join('');
      CL.$$('[data-save]').forEach(function (b) {
        b.onclick = function () {
          var card = b.closest('[data-key]');
          post('/templates/' + card.dataset.key, { body: CL.$('[data-body]', card).value, language: 'en' }).then(function (r) { CL.toast('Saved v' + r.version); R.templates(); }).catch(fail);
        };
      });
    });
  };

  R.config = function () {
    return get('/config').then(function (cfg) {
      var admin = ME.role === 'Admin';
      view.innerHTML = '<h1>Config</h1>' + (admin ? '' : '<div class="banner info">Only Waterline Admin can change these.</div>') +
        Object.keys(cfg).map(function (k) {
          return '<div class="card" data-cfg="' + k + '"><h3>' + esc(k.replace(/_/g, ' ')) + '</h3><textarea data-val style="min-height:' + (typeof cfg[k] === 'object' ? 120 : 48) + 'px"' + (admin ? '' : ' disabled') + '>' + esc(JSON.stringify(cfg[k], null, typeof cfg[k] === 'object' ? 1 : 0)) + '</textarea>' +
            (admin ? '<button class="btn sm" data-save style="margin-top:6px">Save</button>' : '') + '</div>';
        }).join('');
      CL.$$('[data-cfg] [data-save]').forEach(function (b) {
        b.onclick = function () {
          var card = b.closest('[data-cfg]'), val;
          try { val = JSON.parse(CL.$('[data-val]', card).value); } catch (e) { CL.toast('That isn\'t valid JSON'); return; }
          put('/config/' + card.dataset.cfg, { value: val }).then(function () { CL.toast('Saved'); }).catch(fail);
        };
      });
    });
  };

  R.audit = function () {
    return get('/audit?limit=200').then(function (rows) {
      view.innerHTML = '<h1>Audit log</h1><div class="card"><h2>Export</h2><div class="row-btns"><input type="date" id="as" aria-label="From"><input type="date" id="ae" aria-label="To"><button class="btn ghost" id="exp">Download CSV</button></div></div>' +
        '<div class="card table-wrap"><table><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Entity</th><th>Details</th></tr></thead><tbody>' +
        rows.map(function (r) {
          return '<tr><td class="small nowrap">' + CL.datetime(r.created_at) + '</td><td class="small">' + esc(r.actor_type) + '</td><td class="small mono">' + esc(r.action) + '</td><td class="small">' + esc(r.entity) + '</td><td class="tiny mono" style="max-width:380px;word-break:break-all">' + esc(r.after || '') + '</td></tr>';
        }).join('') + '</tbody></table></div>';
      CL.$('#exp').onclick = function () {
        var qs = []; if (CL.$('#as').value) qs.push('start=' + CL.$('#as').value); if (CL.$('#ae').value) qs.push('end=' + CL.$('#ae').value);
        CL.download('/collections/ops/audit.csv' + (qs.length ? '?' + qs.join('&') : ''), 'ops', 'collections-audit.csv').catch(fail);
      };
    });
  };

  function route() {
    shell();
    view.innerHTML = '<p class="muted">Loading…</p>';
    (R[tab()] || R.overview)().catch(function (e) { if (e.status !== 401) view.innerHTML = '<div class="banner bad">' + esc(e.message) + '</div>'; });
  }

  function start() {
    if (!CL.getToken('ops')) return loginForm();
    CL.api('/collections/ops/me', { auth: 'ops', noRedirect: true }).then(function (me) {
      ME = me; window.addEventListener('hashchange', route); route();
    }).catch(function () { CL.setToken('ops', null); loginForm(); });
  }
  start();
})();
