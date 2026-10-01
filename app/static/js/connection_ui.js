/* Dynamic private forms. Schema is metadata; all entered values go to the backend. */
(function () {
  'use strict';
  function esc(value) {
    var el = document.createElement('span');
    el.textContent = String(value == null ? '' : value);
    return el.innerHTML;
  }

  function showCard(d, host, scrollEl, autoOpen) {
    if (!d || !/^[a-f0-9]+$/.test(d.id || '') || !host || !d.form) return null;
    var existing = host.querySelector('.secret-card[data-id="' + d.id + '"]');
    if (existing) return existing;
    var definition = d.form;
    var usage = d.usage || {};
    var isHttp = usage.type === 'http';
    var card = document.createElement('div');
    card.className = 'hitl-card secret-card';
    card.dataset.id = d.id;
    card.setAttribute('role', 'region');
    card.setAttribute('aria-label', 'Private input requested');
    card.innerHTML = '<div class="hitl-rail" aria-hidden="true"></div><div class="hitl-body">' +
      '<header class="hitl-hd"><span class="hitl-kicker">Private input</span>' +
      '<span class="hitl-tool">' + esc(d.name) + '</span></header>' +
      '<p class="hitl-desc">' + esc(definition.purpose || definition.title) + '</p>' +
      '<button type="button" class="hitl-btn hitl-btn-primary">Enter private values</button></div>';
    host.appendChild(card);
    var resolved = false, dialog = null;
    function finish(status) {
      resolved = true;
      if (dialog) {
        dialog.querySelectorAll('[data-private-field]').forEach(function (el) { el.value = ''; });
        dialog.close(); dialog.remove(); dialog = null;
      }
      if (window.TomoHitl) TomoHitl.collapseCard(card, {
        kicker: 'Private input', tool: d.name, status: status,
        tone: status === 'Ready' || status === 'Stored' ? 'allow' : 'deny',
      });
    }
    function open() {
      if (resolved || !card.isConnected) return;
      if (dialog) { dialog.showModal(); return; }
      dialog = document.createElement('dialog');
      dialog.className = 'connection-dialog';
      dialog.setAttribute('aria-labelledby', 'secret-title-' + d.id);
      dialog.innerHTML = '<form class="connection-form" autocomplete="off">' +
        '<header class="modal-head"><h3 id="secret-title-' + d.id + '">' + esc(definition.title) + '</h3></header>' +
        '<div class="modal-body"><p class="modal-lead">All entered values stay private, including text fields and selected options. Sent directly to the backend, not to the model or chat history.</p>' +
        (definition.purpose ? '<p class="connection-purpose">' + esc(definition.purpose) + '</p>' : '') +
        '<div class="connection-fields"></div>' +
        '<p class="connection-scope">Available only in this chat. Encrypted at rest; a shell sharing the backend’s OS account is not isolated from its storage.</p>' +
        '<p class="connection-error" role="alert" hidden></p></div>' +
        '<footer class="modal-foot"><button type="button" class="btn ghost connection-cancel">Cancel request</button>' +
        '<button type="submit" class="btn primary">' + (isHttp ? 'Save connection' : 'Save private values') + '</button></footer></form>';
      card.appendChild(dialog);
      var form = dialog.querySelector('form');
      var fieldsHost = form.querySelector('.connection-fields');
      var origin = null, allowHttp = null;
      if (isHttp) {
        var originLabel = document.createElement('label');
        originLabel.className = 'field';
        originLabel.innerHTML = '<span>Approved API origin</span>';
        origin = document.createElement('input');
        origin.className = 'input'; origin.type = 'url'; origin.required = true;
        origin.dataset.config = 'base_url'; origin.value = usage.base_url;
        originLabel.appendChild(origin); fieldsHost.appendChild(originLabel);
        var policy = document.createElement('pre');
        policy.className = 'connection-policy';
        policy.textContent = 'HTTP execution: ' + (usage.workplace_name || 'Tomo backend') + '\nHTTP authentication: ' + JSON.stringify(usage.auth, null, 2);
        fieldsHost.appendChild(policy);
      }
      (definition.fields || []).forEach(function (field) {
        var label = document.createElement('label'); label.className = 'field';
        var caption = document.createElement('span'); caption.textContent = field.label;
        label.appendChild(caption);
        var control = document.createElement(field.type === 'select' ? 'select' : field.type === 'textarea' ? 'textarea' : 'input');
        control.className = 'input'; control.name = field.name;
        control.dataset.privateField = field.name; control.required = field.required;
        control.autocomplete = field.type === 'password' ? 'new-password' : 'off';
        control.spellcheck = false; control.setAttribute('autocapitalize', 'off');
        if (field.type === 'select') {
          var empty = document.createElement('option'); empty.value = ''; empty.textContent = 'Choose…'; control.appendChild(empty);
          (field.options || []).forEach(function (value) {
            var option = document.createElement('option'); option.value = value; option.textContent = value; control.appendChild(option);
          });
        } else {
          control.maxLength = field.type === 'textarea' ? 32768 : 8192;
          if (field.type === 'textarea') control.rows = 4;
          else control.type = field.type;
        }
        label.appendChild(control);
        if (field.description) {
          var help = document.createElement('small'); help.className = 'connection-field-help';
          help.textContent = field.description; label.appendChild(help);
        }
        fieldsHost.appendChild(label);
      });
      if (isHttp) {
        var warning = document.createElement('label'); warning.className = 'connection-http-warning';
        allowHttp = document.createElement('input'); allowHttp.type = 'checkbox'; allowHttp.name = 'allow_http';
        warning.appendChild(allowHttp); warning.appendChild(document.createTextNode('Allow unencrypted HTTP for this origin'));
        fieldsHost.appendChild(warning);
        var access = document.createElement('p'); access.className = 'connection-scope';
        access.textContent = 'This HTTP consumer can send requests permitted by these credentials. Prefer least-privilege/read-only access.';
        fieldsHost.appendChild(access);
      } else {
        var stored = document.createElement('p'); stored.className = 'connection-scope';
        stored.textContent = 'Store only. No protocol or executable consumer is granted by this form.';
        fieldsHost.appendChild(stored);
      }
      function submit(cancel) {
        if (!cancel && !form.reportValidity()) return;
        var payload = { cancel: !!cancel };
        if (!cancel) {
          payload.values = Object.create(null);
          form.querySelectorAll('[data-private-field]').forEach(function (el) { payload.values[el.dataset.privateField] = el.value; });
          if (isHttp) { payload.base_url = origin.value; payload.allow_http = allowHttp.checked; }
        }
        form.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
        fetch('/api/sessions/' + encodeURIComponent(d.session_id) + '/secrets/requests/' + d.id, {
          method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        }).then(function (res) {
          return res.json().then(function (data) {
            if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not save private values');
            finish(cancel ? 'Cancelled' : isHttp ? 'Ready' : 'Stored');
          });
        }).catch(function (error) {
          if (!dialog) return;
          var message = form.querySelector('.connection-error'); message.textContent = error.message; message.hidden = false;
          form.querySelectorAll('button').forEach(function (b) { b.disabled = false; });
        }).finally(function () {
          Object.keys(payload.values || {}).forEach(function (key) { payload.values[key] = ''; });
        });
      }
      form.addEventListener('submit', function (ev) { ev.preventDefault(); submit(false); });
      form.querySelector('.connection-cancel').addEventListener('click', function () { submit(true); });
      dialog.addEventListener('cancel', function (ev) { ev.preventDefault(); submit(true); });
      dialog.showModal();
      var first = form.querySelector('[data-private-field]'); if (first) first.focus();
    }
    card.querySelector('button').addEventListener('click', open);
    if (autoOpen) open();
    if (d.expires_at) setTimeout(function () { if (!resolved) finish('Expired — request again'); }, Math.max(0, d.expires_at * 1000 - Date.now()));
    if (scrollEl && window.Tomo && Tomo.scrollToBottomInstant) Tomo.scrollToBottomInstant(scrollEl);
    return card;
  }
  window.TomoSecrets = { showCard: showCard };
})();
