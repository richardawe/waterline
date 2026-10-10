/* Debtor payment page (/collections/p/<token>). Shows only this person's own balance. */
(function () {
  var view = CL.$('#view'), esc = CL.esc, naira = CL.naira;
  var m = location.pathname.match(/\/collections\/p\/([A-Za-z0-9_-]{32,64})\/?$/);
  var token = m ? m[1] : (CL.param('t') || '');
  var base = '/collections/d/' + encodeURIComponent(token);
  var state = null, mode = null;

  function notFound() {
    view.innerHTML = '<div class="card"><h1>Page not found</h1><p>This link isn\'t valid any more. If you were sent it recently, please check you copied the whole link.</p></div>';
  }
  function load() {
    if (!token) return notFound();
    CL.api(base).then(function (d) { state = d; render(); }).catch(function (e) { e.status === 404 ? notFound() : showError(e); });
  }
  function showError(e) {
    CL.$$('button[disabled]').forEach(function (b) { CL.busy(b, false); });
    view.insertAdjacentHTML('afterbegin', '<div class="banner bad" role="alert">' + esc(e.message) + '</div>'); window.scrollTo(0, 0); }
  function act(path, opts) {
    return CL.api(base + path, opts).then(function (d) { state = d; mode = null; render(); window.scrollTo(0, 0); }).catch(showError);
  }

  function copyRow(label, value) {
    return '<div class="copy-row"><div><div class="tiny muted">' + esc(label) + '</div><b class="mono">' + esc(value) + '</b></div>' +
      '<button class="btn sm ghost" type="button" data-copy="' + esc(value) + '">Copy</button></div>';
  }

  function render() {
    var d = state;
    CL.$('#client-name').textContent = d.client.name;
    var top = '<p class="muted" style="margin-bottom:4px">Hello ' + esc(d.debtor_first_name) + ',</p>' +
      '<h1 style="font-size:1.25rem">' + esc(d.client.name) + '</h1>' +
      '<div class="card"><div class="small muted">' + esc(d.description) + (d.dependant_first_name ? ' for ' + esc(d.dependant_first_name) : '') + '</div>' +
      '<div class="amount">' + naira(d.balance) + '</div>' +
      (d.balance !== d.original_amount ? '<div class="small muted">of ' + naira(d.original_amount) + ' originally</div>' : '') +
      (d.discount ? '<div class="banner ok" style="margin:12px 0 0">Pay <b>' + naira(d.discount.pay_amount) + '</b> in full by ' + CL.date(d.discount.deadline) + ' and save ' + naira(d.discount.discount_amount) + '.</div>' : '') +
      '</div>';

    if (d.is_closed) {
      view.innerHTML = top + '<div class="card"><h2>' + (d.status === 'Paid' ? 'All paid — thank you' : 'This balance is closed') + '</h2><p class="muted">There\'s nothing more to do.</p></div>';
      return;
    }

    var notices = '';
    if (d.pending_proofs.length) notices += '<div class="banner info">Thanks — ' + esc(d.client.name) + ' will confirm receipt of ' + d.pending_proofs.map(function (p) { return naira(p.amount); }).join(' and ') + '.</div>';
    if (d.open_dispute) notices += '<div class="banner warn">We\'ve passed your query (' + esc(d.open_dispute.type) + ') to ' + esc(d.client.name) + '. Messages are paused while they check.</div>';
    if (d.opted_out) notices += '<div class="banner info">You won\'t get any more messages about this. You can still pay here at any time.</div>';
    if (d.plan) notices += '<div class="card"><h2>Your payment plan</h2><ul class="small" style="padding-left:18px;margin:0">' +
      d.plan.schedule.map(function (s) { return '<li>' + CL.date(s.due) + ': ' + naira(s.amount) + '</li>'; }).join('') + '</ul></div>';

    var body;
    if (mode === 'pay') body = payPanel();
    else if (mode === 'plan') body = planPanel();
    else if (mode === 'paid') body = proofPanel('I\'ve already paid', 'Upload your receipt and we\'ll ask ' + esc(d.client.name) + ' to confirm it.');
    else if (mode === 'wrong') body = disputePanel();
    else body = '<div class="card"><h2>What would you like to do?</h2><div style="display:grid;gap:10px">' +
      '<button class="btn block" data-mode="pay">' + (d.plan ? 'Pay my next instalment' : 'Pay in full') + '</button>' +
      (!d.plan && d.plan_options.length ? '<button class="btn ghost block" data-mode="plan">Choose an instalment plan</button>' : '') +
      '<button class="btn ghost block" data-mode="paid">I\'ve already paid</button>' +
      (!d.open_dispute ? '<button class="btn ghost block" data-mode="wrong">This amount is wrong</button>' : '') +
      '</div>' + (d.opted_out ? '' : '<p class="center" style="margin:16px 0 0"><button class="btn sm ghost" id="stop">Stop messages</button></p>') + '</div>';

    view.innerHTML = top + notices + body;
    bind();
  }

  function bankDetails() {
    var d = state;
    return '<div class="card"><h2>Pay into ' + esc(d.client.name) + '\'s account</h2>' +
      copyRow('Bank', d.client.bank_name) + copyRow('Account number', d.client.account_number) + copyRow('Account name', d.client.account_name) +
      copyRow('Reference to use as the transfer narration', d.reference_code) + '</div>';
  }
  function payPanel() {
    return bankDetails() + proofPanel('After you pay', 'Upload your receipt so ' + esc(state.client.name) + ' can confirm it.');
  }
  function proofPanel(title, sub) {
    var suggested = state.plan ? '' : (state.discount ? state.discount.pay_amount : state.balance) / 100;
    return '<form class="card" id="proof"><h2>' + title + '</h2><p class="small muted">' + sub + '</p>' +
      '<div class="field"><label for="amt">Amount paid (₦)</label><input id="amt" name="amount" type="text" inputmode="decimal" required value="' + suggested + '"></div>' +
      '<div class="field"><label for="dt">Date paid</label><input id="dt" name="paid_on" type="date" required max="' + CL.todayISO() + '" value="' + CL.todayISO() + '"></div>' +
      '<div class="field"><label for="sn">Name on the transfer (optional)</label><input id="sn" name="sender_name" type="text" maxlength="120"></div>' +
      '<div class="field"><label for="file">Receipt or screenshot</label><input id="file" name="file" type="file" accept="image/jpeg,image/png,application/pdf" required><div class="hint">JPG, PNG or PDF, up to 5 MB.</div></div>' +
      '<button class="btn block">Send receipt</button><p class="center"><button type="button" class="btn sm ghost" data-mode="">Back</button></p></form>';
  }
  function planPanel() {
    return '<div class="card"><h2>Choose a plan</h2><p class="small muted">Pick what works for you. You\'ll get a reminder before each date.</p>' +
      state.plan_options.map(function (o) {
        return '<div class="list-item"><b>' + o.instalments + ' payments of about ' + naira(o.instalment_amount) + '</b><div class="small muted">' +
          o.schedule.map(function (s) { return CL.date(s.due) + ': ' + naira(s.amount); }).join(' · ') + '</div>' +
          '<button class="btn sm" style="margin-top:8px" data-plan="' + o.instalments + '">Choose this plan</button></div>';
      }).join('') + '<p class="center"><button type="button" class="btn sm ghost" data-mode="">Back</button></p></div>';
  }
  function disputePanel() {
    return '<form class="card" id="dispute"><h2>Tell us what\'s wrong</h2><p class="small muted">Messages pause while ' + esc(state.client.name) + ' checks.</p>' +
      '<div class="field"><label for="dtype">What\'s the problem?</label><select id="dtype" name="type"><option>Wrong amount</option><option>Already paid</option><option>Not my debt</option><option>Other</option></select></div>' +
      '<div class="field"><label for="ddet">Details</label><textarea id="ddet" name="details" maxlength="2000"></textarea></div>' +
      '<div class="field"><label for="dfile">Evidence (optional)</label><input id="dfile" name="file" type="file" accept="image/jpeg,image/png,application/pdf"><div class="hint">JPG, PNG or PDF, up to 5 MB.</div></div>' +
      '<button class="btn block">Send</button><p class="center"><button type="button" class="btn sm ghost" data-mode="">Back</button></p></form>';
  }

  function bind() {
    CL.$$('[data-mode]').forEach(function (b) { b.onclick = function () { mode = b.dataset.mode || null; render(); window.scrollTo(0, 0); }; });
    CL.$$('[data-copy]').forEach(function (b) {
      b.onclick = function () {
        var v = b.dataset.copy;
        (navigator.clipboard ? navigator.clipboard.writeText(v) : Promise.reject()).then(function () { CL.toast('Copied'); }, function () { prompt('Copy this:', v); });
      };
    });
    CL.$$('[data-plan]').forEach(function (b) {
      b.onclick = function () { CL.busy(b, true); act('/plan', { json: { instalments: parseInt(b.dataset.plan, 10) } }); };
    });
    var stop = CL.$('#stop');
    if (stop) stop.onclick = function () { if (confirm('Stop all messages about this balance? You can still pay here.')) act('/opt-out', { method: 'POST' }); };
    var pf = CL.$('#proof');
    if (pf) pf.onsubmit = function (e) {
      e.preventDefault();
      var f = CL.$('#file').files[0];
      if (f && f.size > 5 * 1024 * 1024) { showError({ message: 'That file is larger than 5 MB.' }); return; }
      CL.busy(pf.querySelector('.btn.block'), true, 'Sending…');
      act('/proof', { form: new FormData(pf) }).then(function () { if (state) CL.toast('Thanks — ' + state.client.name + ' will confirm receipt'); });
    };
    var df = CL.$('#dispute');
    if (df) df.onsubmit = function (e) {
      e.preventDefault();
      var fd = new FormData(df);
      if (!CL.$('#dfile').files.length) fd.delete('file');
      CL.busy(df.querySelector('.btn.block'), true, 'Sending…');
      act('/dispute', { form: fd });
    };
  }

  load();
})();
