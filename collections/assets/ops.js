/* Waterline ops console (/collections/ops/). Separate login with a 6-digit authenticator code. */
(function () {
  var view = CL.$('#view'), esc = CL.esc, naira = CL.naira;
  var ME = null;
  var TABS = [['overview', 'Overview'], ['approvals', 'Approvals'], ['queue', 'Outreach queue'], ['replies', 'Replies'],
    ['followups', 'Follow-ups'], ['clients', 'Clients'], ['fees', 'Fee proposals'], ['invoices', 'Invoices'], ['segments', 'Segments'],
    ['waitlist', 'Waitlist'], ['prospects', 'Prospects'], ['templates', 'Templates'], ['config', 'Config'], ['audit', 'Audit']];
  var SEG_NAMES = {};

  function get(p) { return CL.api('/collections/ops' + p, { auth: 'ops' }); }
  function post(p, json) { return CL.api('/collections/ops' + p, { auth: 'ops', method: 'POST', json: json || {} }); }
  function put(p, json) { return CL.api('/collections/ops' + p, { auth: 'ops', method: 'PUT', json: json }); }
  function del(p) { return CL.api('/collections/ops' + p, { auth: 'ops', method: 'DELETE' }); }
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
        (o.contact_window_enforced ? '<div class="banner ' + (o.in_contact_window ? 'ok' : 'warn') + '">' + (o.in_contact_window ? 'Inside the contact window (8am–7pm WAT, Mon–Sat).' : 'Outside the contact window — messages can\'t be marked sent right now.') + '</div>' : '') +
        '<div class="stats">' +
        s(o.pending_approvals, 'Pending approvals', '#approvals') + s(o.queue_remaining, 'Messages left to send today', '#queue') +
        s(o.overdue_confirmations, 'Confirmations overdue (5+ working days)', '#followups') + s(o.stale_disputes, 'Disputes open 10+ working days', '#followups') +
        s(o.flagged_clients, 'Flagged clients', '#clients') + s(o.draft_invoices, 'Draft invoices', '#invoices') +
        s(o.pending_fee_proposals, 'Fee proposals to review', '#fees') + s(o.new_waitlist, 'New waitlist sign-ups', '#waitlist') + '</div>' +
        (ME.role === 'Admin' ? '<button class="btn ghost" id="daily">Run daily jobs now</button>' : '');
      var d = CL.$('#daily');
      if (d) d.onclick = function () { post('/jobs/daily').then(function (r) { CL.toast('Done: ' + r.queue.created + ' queued'); R.overview(); }).catch(fail); };
    });
    function s(n, l, href) { return '<a class="stat" style="text-decoration:none" href="' + href + '"><b>' + n + '</b><span>' + l + '</span></a>'; }
  };

  function clientCard(c) {
    var checks = c.verification_checklist || {};
    return '<div class="card" data-client="' + c.id + '"><h2>' + esc(c.business_name) + ' ' + CL.pill(c.status) + (c.flagged_at ? ' <span class="pill red">Flagged</span>' : '') + '</h2>' +
      '<dl class="kv small"><dt>Segment</dt><dd>' + esc(SEG_NAMES[c.segment] || c.segment) + '</dd><dt>Type</dt><dd>' + esc(c.business_type) + '</dd><dt>Location</dt><dd>' + esc(c.city + ', ' + c.state) + '</dd>' +
      '<dt>Bank</dt><dd>' + esc(c.bank_name + ' · ' + c.account_number + ' · ' + c.account_name) + '</dd><dt>Signed up</dt><dd>' + CL.datetime(c.created_at) + '</dd>' +
      '<dt>Accounts</dt><dd>' + c.accounts + ' · recovered ' + naira(c.recovered) + '</dd><dt>Fee</dt><dd>' + esc(c.fee_text) + '</dd><dt>Window / cap</dt><dd>' + c.attribution_days + ' days · ' + c.upload_row_cap + ' rows</dd>' +
      Object.keys(c.profile || {}).map(function (k) { return '<dt>' + esc(k.replace(/_/g, ' ')) + '</dt><dd>' + esc(c.profile[k]) + '</dd>'; }).join('') +
      (c.flagged_reason ? '<dt>Flag</dt><dd style="color:var(--red)">' + esc(c.flagged_reason) + '</dd>' : '') +
      (c.status_reason ? '<dt>Reason</dt><dd>' + esc(c.status_reason) + '</dd>' : '') + '</dl>' +
      '<div data-users class="small muted" style="margin:8px 0"></div>' +
      '<details style="margin-top:10px"><summary>Verification checks</summary><div style="margin-top:10px"><div data-checks data-done="' + esc(JSON.stringify(checks)) + '"><p class="small muted">Loading…</p></div>' +
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
        var done = JSON.parse(CL.$('[data-checks]', card).dataset.done || '{}');
        CL.$('[data-checks]', card).innerHTML = d.checklist_items.map(function (it) {
          return '<label class="check"><input type="checkbox" data-check="' + esc(it.key) + '"' + (done[it.key] ? ' checked' : '') + '> ' + esc(it.label) + '</label>';
        }).join('');
        if (d.pending_fee_proposal) CL.$('[data-users]', card).insertAdjacentHTML('beforebegin', '<div class="banner info small">Fee proposal pending: ' + esc(d.pending_fee_proposal.text) + ' — see <a href="#fees">Fee proposals</a>.</div>');
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
          var fee = prompt('Set a flat fee % (blank = unchanged; use Fee proposals for tiered schedules):'); if (fee) body.fee_schedule = { type: 'flat', percent: parseFloat(fee) };
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
        (q.contact_window_enforced ? '<div class="banner ' + (q.in_contact_window ? 'ok' : 'warn') + '">' + (q.in_contact_window ? 'Inside the contact window. Send from the Waterline phone, then mark each one.' : 'Outside 8am–7pm WAT Mon–Sat (or a public holiday). Sending is blocked.') + '</div>' : '<p class="muted small">Send each message from the Waterline phone, then mark it sent.</p>') +
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
    var seg = CL.param('seg') || 'school';
    return get('/templates?segment=' + encodeURIComponent(seg)).then(function (rows) {
      var admin = ME.role === 'Admin';
      var active = rows.filter(function (t) { return t.is_active; });
      view.innerHTML = '<h1>Message templates</h1><div class="tabs">' + Object.keys(SEG_NAMES).map(function (k) {
        return '<button data-seg="' + esc(k) + '"' + (k === seg ? ' class="on"' : '') + '>' + esc(SEG_NAMES[k]) + '</button>';
      }).join('') + '</div><p class="muted">Placeholders: {{debtor_first_name}}, {{client_name}}, {{description}}, {{balance}}, {{link}}, {{discount_line}}, {{instalment_amount}}, {{due_date}}. Every template must include {{client_name}}, “via Waterline”, {{link}} and “Reply STOP to stop messages.” Threats, police/court/arrest, blacklisting, shaming and third-party disclosure are blocked everywhere; each segment blocks more (e.g. exams for schools, eviction for estates).</p>' +
        active.map(function (t) {
          var history = rows.filter(function (h) { return h.key === t.key && h.language === t.language && !h.is_active; });
          return '<div class="card" data-key="' + esc(t.key) + '"><h2>' + esc(t.key) + ' <span class="small muted">v' + t.version + ' · ' + esc(t.language) + '</span></h2>' +
            (admin ? '<textarea data-body style="min-height:120px">' + esc(t.body) + '</textarea><button class="btn sm" data-save style="margin-top:8px">Save as new version</button>' : '<div class="msg">' + esc(t.body) + '</div>') +
            (history.length ? '<details style="margin-top:10px"><summary>Earlier versions</summary>' + history.map(function (h) { return '<p class="small"><b>v' + h.version + '</b> ' + esc(h.body) + '</p>'; }).join('') + '</details>' : '') + '</div>';
        }).join('');
      CL.$$('[data-seg]').forEach(function (b) { b.onclick = function () { history.replaceState(null, '', '?seg=' + b.dataset.seg + '#templates'); R.templates(); }; });
      CL.$$('[data-save]').forEach(function (b) {
        b.onclick = function () {
          var card = b.closest('[data-key]');
          post('/templates/' + seg + '/' + card.dataset.key, { body: CL.$('[data-body]', card).value, language: 'en' }).then(function (r) { CL.toast('Saved v' + r.version); R.templates(); }).catch(fail);
        };
      });
    });
  };

  var STATUS_TEXT = { live: 'Live — open for sign-up', waitlist: 'Coming soon — waitlist only', hidden: 'Hidden' };
  R.segments = function () {
    return get('/segments').then(function (rows) {
      var admin = ME.role === 'Admin';
      view.innerHTML = '<h1>Segments</h1><p class="muted">Switch a segment on when you\'re ready to take sign-ups from it. Status controls the landing page and new sign-ups only — existing clients of a segment keep working. Conduct rules can be made stricter here, never looser than the baseline.</p>' +
        (admin ? '' : '<div class="banner info">Only Waterline Admin can change segments.</div>') +
        rows.map(function (g) {
          var p = g.policy;
          return '<div class="card" data-segkey="' + esc(g.key) + '"><h2>' + esc(g.name) + ' ' + '<span class="pill ' + ({ live: 'green', waitlist: 'amber', hidden: '' }[g.status]) + '">' + esc(STATUS_TEXT[g.status]) + '</span>' + '</h2>' +
            '<p class="small muted">' + esc(g.tagline) + '</p>' +
            '<dl class="kv small"><dt>Client types</dt><dd>' + esc(g.client_types.join(', ')) + '</dd>' +
            '<dt>Clients / waitlist</dt><dd>' + g.clients + (g.clients === 1 ? ' client · ' : ' clients · ') + g.waitlist_new + ' new on the waitlist</dd>' +
            '<dt>Upload columns</dt><dd>' + esc(g.columns.map(function (c) { return c.name + (c.required ? '*' : ''); }).join(', ')) + '</dd>' +
            '<dt>Default fee</dt><dd>' + esc(g.default_fee_text) + '</dd>' +
            '<dt>Contact rules</dt><dd>' + esc(p.contact_start + '–' + p.contact_end + ' WAT, days ' + p.contact_weekdays.map(function (d) { return 'MTWTFS'[d]; }).join('') +
              ' · max ' + p.max_per_7_days + '/7 days · sequence days ' + p.sequence_days.join(', ') + ' then every ' + p.repeat_every_days + ' · stop after ' + p.max_unanswered + ' unanswered') + '</dd>' +
            (g.requires_policy_review ? '<dt>Policy review</dt><dd>' + (g.policy_reviewed_at ? 'Confirmed ' + CL.date(g.policy_reviewed_at) : '<span style="color:var(--red)">Needed before going live</span>') + '</dd>' : '') + '</dl>' +
            (admin ? '<div class="row-btns" style="margin-top:12px">' +
              ['live', 'waitlist', 'hidden'].filter(function (st) { return st !== g.status; }).map(function (st) {
                return '<button class="btn sm' + (st === 'live' ? '' : ' ghost') + '" data-set="' + st + '">' + { live: 'Switch on (live)', waitlist: 'Move to waitlist', hidden: 'Hide' }[st] + '</button>';
              }).join('') +
              (g.requires_policy_review && !g.policy_reviewed_at ? '<button class="btn sm ghost" data-review>Confirm policy reviewed by counsel</button>' : '') + '</div>' +
              '<details style="margin-top:12px"><summary>Tighten conduct rules or change the default fee</summary><div style="margin-top:10px">' +
              '<div class="field"><label>Policy overrides (JSON — stricter only)</label><textarea data-policy style="min-height:70px">' + esc(JSON.stringify(g.policy_overrides)) + '</textarea>' +
              '<div class="hint">e.g. {"contact_start": "09:00", "max_per_7_days": 1}. Baseline: ' + esc(JSON.stringify(g.base_policy)) + '</div></div>' +
              '<div class="field"><label>Default fee for new clients (JSON)</label><textarea data-fee style="min-height:70px">' + esc(JSON.stringify(g.default_fee)) + '</textarea>' +
              '<div class="hint">{"type": "flat", "percent": 20} or {"type": "tiered_by_age", "tiers": [{"max_days": 90, "percent": 15}, {"max_days": null, "percent": 25}]}</div></div>' +
              '<button class="btn sm" data-save-seg>Save</button></div></details>' : '') + '</div>';
        }).join('');
      CL.$$('[data-segkey]').forEach(function (card) {
        var key = card.dataset.segkey;
        var send = function (body, msg) { put('/segments/' + key, body).then(function () { CL.toast(msg); R.segments(); }).catch(fail); };
        CL.$$('[data-set]', card).forEach(function (b) {
          b.onclick = function () {
            if (b.dataset.set === 'live' && !confirm('Open ' + SEG_NAMES[key] + ' for sign-up and show it as live on the landing page?')) return;
            send({ status: b.dataset.set }, 'Segment updated');
          };
        });
        var rv = CL.$('[data-review]', card);
        if (rv) rv.onclick = function () {
          if (confirm('Confirm that counsel has reviewed this segment\'s conduct policy and message templates?')) send({ confirm_policy_review: true }, 'Review recorded');
        };
        var sv = CL.$('[data-save-seg]', card);
        if (sv) sv.onclick = function () {
          var pol, fee;
          try { pol = JSON.parse(CL.$('[data-policy]', card).value || '{}'); fee = JSON.parse(CL.$('[data-fee]', card).value); } catch (e) { CL.toast('That isn\'t valid JSON'); return; }
          send({ policy_overrides: pol, default_fee_schedule: fee }, 'Saved');
        };
      });
    });
  };

  R.fees = function () {
    var st = CL.param('fs') || 'Pending';
    return get('/fee-proposals?status=' + st).then(function (rows) {
      var admin = ME.role === 'Admin';
      view.innerHTML = '<h1>Fee proposals</h1><div class="tabs">' + ['Pending', 'All'].map(function (x) {
        return '<button data-fs="' + x + '"' + (x === st ? ' class="on"' : '') + '>' + x + '</button>';
      }).join('') + '</div>' + (admin ? '' : '<div class="banner info">Only Waterline Admin can approve fees.</div>') +
        (rows.length ? rows.map(function (p) {
          return '<div class="card" data-prop="' + p.id + '"><h3>' + esc(p.client_name) + ' <span class="small muted">' + esc(SEG_NAMES[p.segment] || p.segment) + '</span> ' + CL.pill(p.status) + '</h3>' +
            '<dl class="kv small"><dt>Proposed</dt><dd>' + esc(p.text) + '</dd><dt>Current</dt><dd>' + esc(p.current_text) + '</dd><dt>Sent</dt><dd>' + CL.datetime(p.created_at) + '</dd>' +
            (p.note ? '<dt>Their note</dt><dd>' + esc(p.note) + '</dd>' : '') + (p.decision_note ? '<dt>Decision note</dt><dd>' + esc(p.decision_note) + '</dd>' : '') + '</dl>' +
            (admin && p.status === 'Pending' ? '<div class="field" style="margin-top:10px"><label>Note to the client (optional)</label><input type="text" data-note maxlength="1000"></div>' +
              '<div class="row-btns"><button class="btn sm" data-decide="1">Approve</button><button class="btn sm danger" data-decide="0">Reject</button></div>' : '') + '</div>';
        }).join('') : '<div class="card"><p class="muted">No proposals.</p></div>');
      CL.$$('[data-fs]').forEach(function (b) { b.onclick = function () { history.replaceState(null, '', '?fs=' + b.dataset.fs + '#fees'); R.fees(); }; });
      CL.$$('[data-decide]').forEach(function (b) {
        b.onclick = function () {
          var card = b.closest('[data-prop]'), approve = b.dataset.decide === '1';
          if (!confirm(approve ? 'Approve this fee? It applies to payments the client confirms from now on.' : 'Reject this proposal?')) return;
          post('/fee-proposals/' + card.dataset.prop + '/decide', { approve: approve, note: CL.$('[data-note]', card).value || null })
            .then(function () { CL.toast(approve ? 'Approved' : 'Rejected'); R.fees(); }).catch(fail);
        };
      });
    });
  };

  R.waitlist = function () {
    return get('/waitlist').then(function (rows) {
      view.innerHTML = '<h1>Waitlist</h1><p class="muted">Organisations from segments that aren\'t live yet. When you switch a segment on (Segments tab), contact them to sign up.</p>' +
        '<div class="card table-wrap"><table><thead><tr><th>When</th><th>Segment</th><th>Organisation</th><th>Contact</th><th>Note</th><th>Status</th></tr></thead><tbody>' +
        (rows.length ? rows.map(function (w) {
          return '<tr data-w="' + w.id + '"><td class="small nowrap">' + CL.date(w.created_at) + '</td><td class="small">' + esc(SEG_NAMES[w.segment] || w.segment) + '</td>' +
            '<td>' + esc(w.business_name) + '<div class="tiny muted">' + esc(w.business_type || '') + '</div></td>' +
            '<td class="small">' + esc(w.contact_name) + '<br><a href="mailto:' + esc(w.email) + '">' + esc(w.email) + '</a>' + (w.phone ? '<br>' + esc(w.phone) : '') + '</td>' +
            '<td class="small">' + esc(w.note || '') + '</td><td><select data-ws style="min-height:34px" aria-label="Status">' + ['New', 'Contacted', 'Invited'].map(function (x) {
              return '<option' + (x === w.status ? ' selected' : '') + '>' + x + '</option>';
            }).join('') + '</select></td></tr>';
        }).join('') : '<tr><td colspan="6" class="muted">Nobody yet.</td></tr>') + '</tbody></table></div>';
      CL.$$('[data-ws]').forEach(function (sel) {
        sel.onchange = function () { post('/waitlist/' + sel.closest('[data-w]').dataset.w + '/status', { status: sel.value }).then(function () { CL.toast('Saved'); }).catch(fail); };
      });
    });
  };

  /* ------------------------------------------------------------ prospects: Waterline's own sales outreach */
  var P_STATUS_CLASS = { New: '', Contacted: 'blue', Replied: 'amber', Interested: 'green', 'Signed up': 'green', 'Not interested': '', 'Do not contact': 'red' };
  var FIT_CLASS = { High: 'green', Medium: 'amber', Low: '' };
  function ppill(s) { return '<span class="pill ' + (P_STATUS_CLASS[s] || '') + '">' + esc(s) + '</span>'; }

  R.prospects = function () {
    var id = (location.hash.split('/')[1] || '');
    if (id === 'new') return prospectForm(null);
    if (id) return prospectDetail(id);
    var st = CL.param('pst') || '', list = CL.param('plist') || '';
    return get('/prospects?' + (st ? 'status=' + encodeURIComponent(st) + '&' : '') + (list ? 'list_id=' + encodeURIComponent(list) : '')).then(function (d) {
      var counts = {}; d.prospects.forEach(function (p) { counts[p.status] = (counts[p.status] || 0) + 1; });
      var cur = d.lists.filter(function (l) { return l.id === list; })[0];
      view.innerHTML = '<div class="bar-in" style="min-height:0;margin-bottom:12px"><h1 style="margin:0">Prospects</h1><a class="btn sm" href="#prospects/new">Add prospect</a></div>' +
        '<p class="muted small">Schools, estates and other organisations we\'d like as clients. Email goes out from the app with replies to the Collections inbox; WhatsApp opens on this computer with the message filled in, then you mark it sent. Every message carries our company details and a way to opt out.</p>' +
        '<div class="row-btns" style="margin-bottom:10px"><select id="plist" aria-label="List" style="max-width:360px;min-height:36px"><option value="">All lists</option>' +
        d.lists.map(function (l) { return '<option value="' + esc(l.id) + '"' + (l.id === list ? ' selected' : '') + '>' + esc(l.name) + '</option>'; }).join('') + '</select></div>' +
        (cur && cur.description ? '<details class="card" style="padding:12px 16px"><summary class="small"><b>About this list</b></summary><p class="small" style="margin:8px 0 0">' + esc(cur.description) + '</p></details>' : '') +
        '<div class="tabs">' + [''].concat(d.statuses).map(function (x) {
          return '<button data-pst="' + esc(x) + '"' + (x === st ? ' class="on"' : '') + '>' + esc(x || 'All') + (x && !st && counts[x] ? ' (' + counts[x] + ')' : '') + '</button>';
        }).join('') + '</div>' +
        '<div class="card table-wrap"><table><thead><tr><th>Organisation</th><th>Fit</th><th>Contact</th><th>Reach</th><th>Status</th><th>Last contact</th></tr></thead><tbody>' +
        (d.prospects.length ? d.prospects.map(function (p) {
          return '<tr><td><a href="#prospects/' + p.id + '"><b>' + esc(p.org_name) + '</b></a><div class="tiny muted">' + esc(SEG_NAMES[p.segment] || p.segment) + (p.city ? ' · ' + esc(p.city) : '') + '</div></td>' +
            '<td>' + (p.fit ? '<span class="pill ' + FIT_CLASS[p.fit] + '">' + esc(p.fit) + '</span>' : '') + '</td>' +
            '<td class="small">' + (p.contact_name ? esc(p.contact_name) + (p.contact_role ? '<div class="tiny muted">' + esc(p.contact_role) + '</div>' : '') + (p.contact_confirmed ? '' : '<div class="tiny muted">not confirmed</div>') : '<span class="muted">—</span>') + '</td>' +
            '<td class="small">' + [p.email ? 'Email' : '', p.whatsapp ? 'WhatsApp' : ''].filter(Boolean).join(' · ') + (!p.email && !p.whatsapp ? '<span class="muted">' + (p.address ? 'Visit / post' : 'Find a contact') + '</span>' : '') + '</td>' +
            '<td>' + ppill(p.status) + (p.unanswered ? '<div class="tiny muted">' + p.unanswered + ' unanswered</div>' : '') + '</td>' +
            '<td class="small nowrap">' + (p.last_contacted_at ? CL.date(p.last_contacted_at) : '<span class="muted">Never</span>') + (p.next_follow_up ? '<div class="tiny muted">follow up ' + CL.date(p.next_follow_up) + '</div>' : '') + '</td></tr>';
        }).join('') : '<tr><td colspan="6" class="muted">No prospects here.</td></tr>') + '</tbody></table></div>';
      var go = function (k, v) { var u = new URLSearchParams(location.search); v ? u.set(k, v) : u.delete(k); history.replaceState(null, '', '?' + u.toString() + location.hash); R.prospects(); };
      CL.$('#plist').onchange = function () { go('plist', this.value); };
      CL.$$('[data-pst]').forEach(function (b) { b.onclick = function () { go('pst', b.dataset.pst); }; });
    });
  };

  var P_FIELDS = [['org_name', 'Organisation', 'text', true], ['org_type', 'What they are', 'text'], ['city', 'City', 'text'], ['address', 'Address', 'text'],
    ['contact_name', 'Contact name (as published)', 'text'], ['contact_role', 'Their role', 'text'], ['email', 'Official email', 'email'],
    ['phone', 'Phone (mobile, for WhatsApp)', 'tel'], ['phone_note', 'Phone note', 'text'], ['fit_note', 'Why they fit', 'text'], ['source', 'Source', 'text'],
    ['next_follow_up', 'Next follow-up', 'date']];

  function prospectFields(p, statuses) {
    p = p || { segment: 'school', status: 'New' };
    return '<div class="grid2">' +
      '<div class="field"><label for="pf-segment">Segment</label><select id="pf-segment" name="segment">' + Object.keys(SEG_NAMES).map(function (k) {
        return '<option value="' + esc(k) + '"' + (k === p.segment ? ' selected' : '') + '>' + esc(SEG_NAMES[k]) + '</option>'; }).join('') + '</select></div>' +
      '<div class="field"><label for="pf-fit">Fit</label><select id="pf-fit" name="fit"><option value="">—</option>' + ['High', 'Medium', 'Low'].map(function (f) {
        return '<option' + (f === p.fit ? ' selected' : '') + '>' + f + '</option>'; }).join('') + '</select></div>' +
      P_FIELDS.map(function (f) {
        return '<div class="field"><label for="pf-' + f[0] + '">' + f[1] + '</label><input id="pf-' + f[0] + '" name="' + f[0] + '" type="' + f[2] + '"' + (f[3] ? ' required' : '') + ' value="' + esc(p[f[0]] || '') + '"></div>';
      }).join('') +
      '<div class="field"><label for="pf-status">Status</label><select id="pf-status" name="status">' + statuses.map(function (x) {
        return '<option' + (x === p.status ? ' selected' : '') + '>' + esc(x) + '</option>'; }).join('') + '</select></div></div>' +
      '<label class="check"><input type="checkbox" name="contact_confirmed"' + (p.contact_confirmed ? ' checked' : '') + '> I\'ve confirmed this person still holds the role. Greet them by name.</label>' +
      '<div class="field"><label for="pf-notes">Notes</label><textarea id="pf-notes" name="notes" style="min-height:70px">' + esc(p.notes || '') + '</textarea></div>';
  }

  function readProspect(form, listId) {
    var body = {};
    P_FIELDS.forEach(function (f) { body[f[0]] = form.elements[f[0]].value.trim() || null; });
    body.segment = form.elements.segment.value; body.fit = form.elements.fit.value || null; body.status = form.elements.status.value;
    body.notes = form.elements.notes.value.trim() || null; body.contact_confirmed = form.elements.contact_confirmed.checked; body.list_id = listId || null;
    return body;
  }

  function prospectForm() {
    return get('/prospects?status=__none__').then(function (d) {
      view.innerHTML = '<p><a href="#prospects">← Prospects</a></p><h1>Add prospect</h1>' +
        '<form class="card" id="pf"><p class="small muted" style="margin-top:0">Only an organisation\'s official, published contacts. No personal emails or numbers.</p>' +
        '<div class="field"><label for="pf-list">List</label><select id="pf-list"><option value="">No list</option>' + d.lists.map(function (l) { return '<option value="' + esc(l.id) + '">' + esc(l.name) + '</option>'; }).join('') + '</select></div>' +
        prospectFields(null, d.statuses) + '<div id="pf-msg"></div><button class="btn">Add prospect</button></form>';
      CL.$('#pf').onsubmit = function (e) {
        e.preventDefault();
        post('/prospects', readProspect(e.target, CL.$('#pf-list').value)).then(function (p) { location.hash = '#prospects/' + p.id; })
          .catch(function (err) { CL.$('#pf-msg').innerHTML = '<div class="banner bad">' + esc(err.message) + '</div>'; });
      };
    });
  }

  function prospectDetail(id) {
    return Promise.all([get('/prospects/' + id), get('/prospects?status=__none__')]).then(function (res) {
      var d = res[0], p = d.prospect, dr = d.drafts, statuses = res[1].statuses;
      var tpl = 'intro', channel = p.email ? 'Email' : 'WhatsApp';
      view.innerHTML = '<p><a href="#prospects">← Prospects</a></p>' +
        '<h1 style="margin-bottom:4px">' + esc(p.org_name) + '</h1><p class="muted" style="margin-top:0">' + esc(p.org_type || SEG_NAMES[p.segment] || '') + (p.city ? ' · ' + esc(p.city) : '') + ' · ' + ppill(p.status) +
        (p.fit ? ' <span class="pill ' + FIT_CLASS[p.fit] + '">' + esc(p.fit) + ' fit</span>' : '') + '</p>' +
        '<div class="card"><h2>Reach them</h2>' +
        '<p class="small">' + (p.email ? '✉︎ <a href="mailto:' + esc(p.email) + '">' + esc(p.email) + '</a> · ' : '') + (p.phone ? '☎︎ <a href="tel:' + esc(p.phone) + '">' + esc(p.phone) + '</a>' + (p.phone_note ? ' (' + esc(p.phone_note) + ')' : '') : '') +
        (p.address ? ' · ' + esc(p.address) : '') + (p.contact_name ? '<br>' + esc(p.contact_name) + (p.contact_role ? ', ' + esc(p.contact_role) : '') + (p.contact_confirmed ? '' : ' <span class="tiny muted">(not confirmed, so messages greet the role)</span>') : '') + '</p>' +
        '<div class="tabs" id="tpl">' + [['intro', 'First message'], ['follow_up', 'Follow-up'], ['custom', 'Blank']].map(function (t) { return '<button data-tpl="' + t[0] + '"' + (t[0] === tpl ? ' class="on"' : '') + '>' + t[1] + '</button>'; }).join('') + '</div>' +
        '<div class="tabs" id="chan">' + ['Email', 'WhatsApp'].map(function (c) { return '<button data-chan="' + c + '"' + (c === channel ? ' class="on"' : '') + '>' + c + '</button>'; }).join('') + '</div>' +
        '<div id="compose"></div></div>' +
        '<div class="card"><h2>Log a call, reply or note</h2><form id="lg"><div class="row-btns"><select name="channel" style="max-width:150px;min-height:38px" aria-label="Type"><option value="Call">Call</option><option value="Reply">They replied</option><option value="Note">Note</option></select></div>' +
        '<textarea name="body" required style="min-height:60px;margin-top:8px" placeholder="What was said?" aria-label="Details"></textarea><button class="btn sm" style="margin-top:8px">Save</button></form></div>' +
        '<div class="card"><h2>History</h2>' + (d.touches.length ? d.touches.map(function (t) {
          return '<div class="list-item"><div class="small"><b>' + esc(t.channel) + '</b> ' + (t.status === 'Failed' ? '<span class="pill red">Failed</span> ' : '') + '<span class="muted">' + CL.datetime(t.created_at) + (t.by ? ' · ' + esc(t.by) : '') + '</span></div>' +
            (t.subject ? '<div class="small"><b>' + esc(t.subject) + '</b></div>' : '') + (t.body ? '<details><summary class="tiny muted">Show message</summary><div class="msg small">' + esc(t.body) + '</div></details>' : '') + (t.error ? '<div class="tiny" style="color:var(--red)">' + esc(t.error) + '</div>' : '') + '</div>';
        }).join('') : '<p class="muted">Nothing yet.</p>') + '</div>' +
        '<details class="card"><summary><b>Edit details</b></summary><form id="pe" style="margin-top:12px">' + prospectFields(p, statuses) +
        '<div class="row-btns"><button class="btn">Save</button>' + (ME.role === 'Admin' ? '<button type="button" class="btn ghost" id="pdel">Delete for good</button>' : '') + '</div></form></details>';

      function compose() {
        var blocked = d.blocked[channel], text = tpl === 'custom' ? { subject: '', email: '', whatsapp: '' } : dr[tpl];
        var foot = channel === 'Email' ? dr.footer.email : dr.footer.whatsapp;
        var html = blocked ? '<div class="banner warn">' + esc(blocked) + '</div>' : '';
        if (channel === 'Email') {
          html += '<div class="field"><label for="c-subj">Subject</label><input id="c-subj" type="text" value="' + esc(text.subject) + '"></div>' +
            '<div class="field"><label for="c-body">Message</label><textarea id="c-body" style="min-height:300px">' + esc(text.email) + '</textarea></div>';
        } else {
          html += '<div class="field"><label for="c-body">Message</label><textarea id="c-body" style="min-height:150px">' + esc(text.whatsapp) + '</textarea></div>';
        }
        html += '<p class="tiny muted" style="margin:-4px 0 4px">Added automatically at the end:</p><div class="msg tiny">' + esc(foot.trim()) + '</div>' +
          '<div class="row-btns" style="margin-top:12px">' + (channel === 'Email'
            ? '<button class="btn" id="c-send"' + (blocked ? ' disabled' : '') + '>Send email</button>'
            : '<a class="btn wa' + (blocked ? ' disabled' : '') + '" id="c-wa" target="_blank" rel="noopener"' + (blocked ? ' aria-disabled="true"' : '') + '>Open WhatsApp</a><button class="btn ghost" id="c-sent"' + (blocked ? ' disabled' : '') + '>Mark sent</button>') + '</div>';
        CL.$('#compose').innerHTML = html;
        var body = CL.$('#c-body');
        if (channel === 'Email') {
          CL.$('#c-send').onclick = function () {
            var b = this; if (!confirm('Send this email to ' + p.email + '?')) return;
            CL.busy(b, true, 'Sending…');
            post('/prospects/' + id + '/email', { subject: CL.$('#c-subj').value, body: body.value, template_key: tpl })
              .then(function () { CL.toast('Email sent'); prospectDetail(id); }).catch(function (e) { CL.busy(b, false); fail(e); prospectDetail(id); });
          };
        } else {
          var wa = CL.$('#c-wa'), link = function () { wa.href = 'https://wa.me/' + (p.whatsapp || '').replace('+', '') + '?text=' + encodeURIComponent(body.value.trim() + foot); };
          link(); body.oninput = link;
          if (blocked) wa.onclick = function (e) { e.preventDefault(); };
          CL.$('#c-sent').onclick = function () {
            post('/prospects/' + id + '/whatsapp-sent', { body: body.value, template_key: tpl })
              .then(function () { CL.toast('Marked sent'); prospectDetail(id); }).catch(fail);
          };
        }
      }
      CL.$$('[data-tpl]').forEach(function (b) { b.onclick = function () { tpl = b.dataset.tpl; CL.$$('[data-tpl]').forEach(function (x) { x.classList.toggle('on', x === b); }); compose(); }; });
      CL.$$('[data-chan]').forEach(function (b) { b.onclick = function () { channel = b.dataset.chan; CL.$$('[data-chan]').forEach(function (x) { x.classList.toggle('on', x === b); }); compose(); }; });
      compose();
      CL.$('#lg').onsubmit = function (e) {
        e.preventDefault();
        post('/prospects/' + id + '/log', CL.formJSON(e.target)).then(function () { CL.toast('Saved'); prospectDetail(id); }).catch(fail);
      };
      CL.$('#pe').onsubmit = function (e) {
        e.preventDefault();
        put('/prospects/' + id, readProspect(e.target, p.list_id)).then(function () { CL.toast('Saved'); prospectDetail(id); }).catch(fail);
      };
      if (CL.$('#pdel')) CL.$('#pdel').onclick = function () {
        if (!confirm('Delete ' + p.org_name + ' and its history for good? To just stop contact, set the status to "Do not contact".')) return;
        del('/prospects/' + id).then(function () { location.hash = '#prospects'; }).catch(fail);
      };
    });
  }

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
      ME = me;
      return get('/segments').then(function (segs) {
        segs.forEach(function (g) { SEG_NAMES[g.key] = g.name; });
      }).catch(function () {}).then(function () { window.addEventListener('hashchange', route); route(); });
    }).catch(function () { CL.setToken('ops', null); loginForm(); });
  }
  start();
})();
