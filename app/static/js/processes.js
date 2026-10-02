/* Background jobs use durable snapshots, independently of turn SSE. */
(function (global) {
  'use strict';
  var state = null;
  var ACTIVE = ['starting', 'running', 'stopping', 'unknown'];
  function element(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text != null) el.textContent = text;
    return el;
  }
  function button(text, action, className) {
    var el = element('button', className || 'process-button', text);
    el.type = 'button'; el.onclick = action; return el;
  }
  function current(s) { return state === s && s.wrap.isConnected && s.wrap.dataset.sessionId === s.sid; }
  function active(job) { return ACTIVE.indexOf(job.status) !== -1 && !job.monitoring_closed; }
  function pending(job) {
    return active(job) || ['pending', 'claimed'].indexOf(job.continuation_status) !== -1 ||
      ['pending', 'sending'].indexOf(job.delivery_status) !== -1;
  }
  function elapsed(job) {
    var start = Number(job.started_at) * 1000;
    if (!Number.isFinite(start) || !start) start = Date.parse(job.started_at);
    var finish = Number(job.finished_at) * 1000 || Date.parse(job.finished_at) || Date.now();
    var secs = Math.max(0, Math.floor((finish - start) / 1000));
    return Number.isFinite(secs) ? Math.floor(secs / 60) + 'm ' + secs % 60 + 's' : '';
  }
  function statusLine(job) {
    var label = job.status.charAt(0).toUpperCase() + job.status.slice(1);
    if (job.returncode != null) label += ' · exit ' + job.returncode;
    if (job.monitoring_closed) label += ' · Monitoring closed';
    if (job.stop_late) label += ' · Stop arrived after exit';
    if (job.status === 'unknown' && job.last_observed_at) label += ' · Last observed ' + new Date(Number(job.last_observed_at) * 1000).toLocaleString();
    return [label, elapsed(job), job.backend, job.workplace_id].filter(Boolean).join(' · ');
  }
  function outcome(job) {
    var parts = [];
    if (['pending', 'claimed'].indexOf(job.continuation_status) !== -1) parts.push('Result waiting for agent');
    if (job.continuation_status === 'blocked') parts.push('Agent continuation blocked');
    if (job.continuation_paused) parts.push('Automatic continuation paused');
    if (['pending', 'sending', 'blocked', 'unknown'].indexOf(job.delivery_status) !== -1) parts.push('Delivery: ' + job.delivery_status);
    if (job.status === 'unknown') parts.push('Observation lost; process may still be running');
    if (job.status === 'interrupted') parts.push('Interrupted by restart; result unconfirmed');
    if (active(job) && job.status !== 'unknown') parts.push(sending() ? 'Agent active · Process running separately' : 'Agent idle · Process continues');
    return parts.join(' · ');
  }
  function sending() { return state && !!state.wrap.querySelector('.composer.is-generating'); }
  function origin(s, job) {
    var targets = s.wrap.querySelectorAll('[data-message-id], .tool');
    for (var i = 0; i < targets.length; i++) {
      var node = targets[i];
      if (job.origin_message_id && node.dataset.messageId === String(job.origin_message_id)) return node;
      if (job.origin_call_id && node.dataset.callId === job.origin_call_id) return node;
      if (node.dataset.backgroundJobId === job.id) return node;
      var result = node.querySelector('.tres');
      if (!result) continue;
      try {
        var value = JSON.parse(result.textContent);
        if ((value.job_id || value.id || value.process_id) === job.id) { node.dataset.backgroundJobId = job.id; return node; }
      } catch (_) {}
    }
    return null;
  }
  function controls(s, job) {
    var actions = element('div', 'process-actions');
    actions.appendChild(button('View log', function () { open(job.id); }));
    var stop = button('Stop process', function () { mutate(s, job, 'stop', stop); });
    stop.disabled = !active(job) || job.status === 'stopping';
    actions.appendChild(stop);
    return actions;
  }
  function updateCard(s, card, job) {
    card.dataset.status = job.status;
    card.querySelector('.process-command').textContent = job.command;
    card.querySelector('.process-id').textContent = job.id;
    card.querySelector('.process-status').textContent = statusLine(job);
    card.querySelector('.process-outcome').textContent = outcome(job);
    var stop = card.querySelector('.process-actions button:last-child');
    if (stop) stop.disabled = !active(job) || job.status === 'stopping';
  }
  function reconcile(s) {
    if (!current(s)) return;
    s.jobs.forEach(function (job) {
      var cards = Array.from(s.wrap.querySelectorAll('.background-job-card')).filter(function (card) { return card.dataset.jobId === job.id; });
      var card = cards.shift(); cards.forEach(function (node) { node.remove(); });
      if (!card) {
        card = element('article', 'background-job-card'); card.dataset.jobId = job.id;
        var head = element('div', 'process-card-head');
        head.appendChild(element('code', 'process-command')); head.appendChild(element('span', 'process-id'));
        card.appendChild(head); card.appendChild(element('div', 'process-status'));
        card.appendChild(element('div', 'process-outcome')); card.appendChild(controls(s, job));
      }
      updateCard(s, card, job);
      var target = origin(s, job);
      if (target && target.parentNode && target.nextSibling !== card) target.after(card);
      else if (!card.isConnected) {
        var host = s.wrap.querySelector('.background-jobs-orphans');
        if (!host) {
          host = element('div', 'turn background-jobs-orphans');
          host.appendChild(element('p', 'process-note', 'Background processes · The original tool message is unavailable.'));
          s.wrap.querySelector('.chat-scroll').appendChild(host);
        }
        host.appendChild(card);
      }
    });
    s.wrap.querySelectorAll('[data-process-count]').forEach(function (node) {
      var n = Array.from(s.jobs.values()).filter(active).length;
      node.textContent = n ? '(' + n + ')' : '';
    });
    if (!s.hashHandled && location.hash.indexOf('#message-') === 0) {
      var anchor;
      try { anchor = document.getElementById(decodeURIComponent(location.hash.slice(1))); } catch (_) {}
      if (anchor && s.wrap.contains(anchor)) { s.hashHandled = true; anchor.scrollIntoView({ block: 'center' }); }
    }
  }
  function accept(s, jobs) {
    if (!current(s)) return;
    jobs.forEach(function (job) {
      var old = s.jobs.get(job.id);
      if (!old || Number(job.version || 0) >= Number(old.version || 0)) s.jobs.set(job.id, job);
    });
    s.stale = false; reconcile(s); renderList(s); renderDetail(s);
  }
  function url(s, suffix) { return '/api/sessions/' + encodeURIComponent(s.sid) + '/processes' + (suffix || ''); }
  async function json(s, path, method) {
    var response = await fetch(url(s, path), { credentials: 'same-origin', method: method || 'GET', signal: s.abort.signal });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.json();
  }
  function schedule(s) {
    if (!current(s)) return;
    clearTimeout(s.timer);
    if (s.stale || Array.from(s.jobs.values()).some(pending)) {
      s.timer = setTimeout(function () { refresh(s); }, document.hidden ? 15000 : 2000);
    }
  }
  async function refresh(s) {
    s = s || state;
    if (!s || !current(s)) return;
    if (s.busy) { s.again = true; return; }
    s.busy = true; clearTimeout(s.timer);
    try {
      var data = await json(s, '');
      if (!current(s)) return;
      accept(s, data.jobs || []);
      if (s.selected && s.panel && s.panel.isConnected) {
        var requested = s.selected;
        var logs = await json(s, '/' + encodeURIComponent(requested) + '/logs');
        if (current(s) && s.selected === requested) {
          var old = s.logs.get(requested);
          if (!old || Number(logs.version || 0) >= Number(old.version || 0)) s.logs.set(requested, logs);
          renderDetail(s);
        }
      }
    } catch (error) {
      if (current(s) && error.name !== 'AbortError') {
        s.stale = true; renderList(s); renderDetail(s);
      }
    } finally {
      s.busy = false;
      if (current(s)) {
        if (s.again) { s.again = false; refresh(s); }
        else schedule(s);
      }
    }
  }
  async function mutate(s, job, action, btn) {
    var text = action === 'stop' ? 'Stop this process and its children?' : 'Close monitoring? This releases the Tomo slot. The process may still be running and will not be stopped.';
    if (!global.confirm(text + '\n\n' + job.id + '\n' + job.command)) return;
    btn.disabled = true;
    try {
      var result = await json(s, '/' + encodeURIComponent(job.id) + '/' + action, 'POST');
      if (current(s)) { accept(s, [result]); refresh(s); }
    } catch (error) {
      if (error.name !== 'AbortError' && current(s)) { global.alert('Could not update process: ' + error.message); refresh(s); }
    } finally {
      if (btn.isConnected) {
        var latest = s.jobs.get(job.id) || job;
        btn.disabled = action === 'stop' && (!active(latest) || latest.status === 'stopping');
      }
    }
  }
  function renderList(s) {
    if (!s.panel || !s.panel.isConnected || !current(s)) return;
    var host = s.panel.querySelector('[data-process-list]'); host.replaceChildren();
    var stale = s.panel.querySelector('[data-process-stale]');
    stale.textContent = s.stale ? 'Connection stale · Showing last known status. Refresh to retry.' : '';
    var jobs = Array.from(s.jobs.values()).sort(function (a, b) { return Number(active(b)) - Number(active(a)) || Number(b.started_at) - Number(a.started_at); });
    if (!jobs.length) host.appendChild(element('p', 'process-note', s.stale ? 'Could not load processes.' : 'No background processes in this chat yet.'));
    jobs.forEach(function (job) {
      var row = button('', function () { s.selected = job.id; s.follow = true; renderList(s); renderDetail(s); refresh(s); }, 'process-row');
      row.dataset.processSelect = job.id; row.setAttribute('aria-pressed', String(s.selected === job.id));
      row.appendChild(element('code', 'process-command', job.command));
      row.appendChild(element('span', 'process-status', statusLine(job)));
      host.appendChild(row);
    });
  }
  function goOrigin(s, job) {
    var target = origin(s, job);
    var note = s.panel.querySelector('[data-process-origin-note]');
    if (!target) { note.textContent = 'The original message was removed or is unavailable. This process still belongs to this chat.'; return; }
    if (!target.id) target.id = 'message-' + (job.origin_message_id || job.origin_call_id || job.id);
    history.replaceState(null, '', '/sessions?s=' + encodeURIComponent(s.sid) + '#' + encodeURIComponent(target.id));
    target.scrollIntoView({ block: 'center', behavior: 'smooth' });
    target.classList.add('process-origin-highlight');
    setTimeout(function () { target.classList.remove('process-origin-highlight'); }, 2500);
  }
  function renderDetail(s) {
    if (!s.panel || !s.panel.isConnected || !current(s)) return;
    var host = s.panel.querySelector('[data-process-detail]');
    var job = s.jobs.get(s.selected);
    if (!job) { host.textContent = 'Select a process to view its log.'; return; }
    if (host.dataset.jobId !== job.id) {
      host.replaceChildren(); host.dataset.jobId = job.id;
      host.appendChild(element('code', 'process-detail-command', job.command));
      host.appendChild(element('div', 'process-status'));
      host.appendChild(element('div', 'process-outcome'));
      host.appendChild(controls(s, job));
      var nav = element('div', 'process-actions');
      var originLink = element('a', 'process-button', 'Go to original chat');
      originLink.href = '/sessions?s=' + encodeURIComponent(s.sid) + (job.origin_message_id ? '#message-' + encodeURIComponent(job.origin_message_id) : '');
      originLink.onclick = function (event) { event.preventDefault(); goOrigin(s, job); };
      nav.appendChild(originLink);
      nav.appendChild(button('Close monitoring', function () { mutate(s, job, 'close-monitoring', this); }));
      host.appendChild(nav);
      var note = element('p', 'process-note'); note.dataset.processOriginNote = ''; host.appendChild(note);
      var flags = element('div', 'process-note'); flags.dataset.processLogNote = ''; host.appendChild(flags);
      var pre = element('pre', 'process-log'); pre.tabIndex = 0; pre.setAttribute('aria-label', 'Process output');
      pre.addEventListener('scroll', function () { s.follow = pre.scrollHeight - pre.clientHeight - pre.scrollTop < 32; });
      host.appendChild(pre);
      var latest = button('Follow latest output', function () { s.follow = true; pre.scrollTop = pre.scrollHeight; });
      latest.dataset.processFollow = ''; host.appendChild(latest);
    }
    host.querySelector('.process-status').textContent = statusLine(job);
    host.querySelector('.process-outcome').textContent = outcome(job);
    var buttons = host.querySelectorAll('.process-actions button');
    buttons[1].disabled = !active(job) || job.status === 'stopping';
    var close = buttons[2]; close.hidden = job.status !== 'unknown' || job.monitoring_closed;
    var logs = s.logs.get(job.id);
    host.querySelector('[data-process-log-note]').textContent = [
      (logs && logs.truncated) || job.truncated ? 'Retained log truncated; only the available tail is shown.' : '',
      (logs && logs.logs_expired) || job.logs_expired ? 'Retained log expired.' : '',
      s.stale ? 'Log may be stale.' : '',
    ].filter(Boolean).join(' ');
    var pre = host.querySelector('.process-log');
    var text = logs ? [logs.stdout || '', logs.stderr ? '\n[stderr]\n' + logs.stderr : ''].join('') : '';
    if (!text) text = logs && logs.logs_expired ? 'Output no longer retained.' : active(job) ? 'Process running; no output yet.' : 'No retained output.';
    var scrollTop = pre.scrollTop;
    if (pre.textContent !== text) pre.textContent = text;
    pre.scrollTop = s.follow ? pre.scrollHeight : scrollTop;
    host.querySelector('[data-process-follow]').textContent = s.follow ? 'Following latest output' : 'Follow latest output';
  }
  function mount(root, sid) {
    var wrap = root.closest('.chat-wrap'); attach(wrap);
    var s = state;
    if (!s || s.sid !== sid) return;
    s.panel = root;
    root.replaceChildren();
    var toolbar = element('div', 'process-toolbar'); toolbar.appendChild(element('strong', '', 'Background processes'));
    toolbar.appendChild(button('Refresh', function () { refresh(s); })); root.appendChild(toolbar);
    var stale = element('p', 'process-note'); stale.dataset.processStale = ''; stale.setAttribute('role', 'status'); root.appendChild(stale);
    var list = element('div', 'process-list'); list.dataset.processList = ''; list.setAttribute('aria-label', 'Background processes'); root.appendChild(list);
    var detail = element('section', 'process-detail'); detail.dataset.processDetail = ''; root.appendChild(detail);
    renderList(s); renderDetail(s); refresh(s);
  }
  function detachPanel() { if (state) { state.panel = null; state.selected = ''; } }
  function detach(wrap) {
    if (!state || (wrap && state.wrap !== wrap)) return;
    clearTimeout(state.timer); state.abort.abort(); state.observer.disconnect(); state = null;
  }
  function attach(wrap) {
    if (!wrap || !wrap.dataset.sessionId || !wrap.querySelector('.chat-scroll')) return;
    if (state && state.wrap === wrap && state.sid === wrap.dataset.sessionId) return;
    detach();
    var s = state = { wrap: wrap, sid: wrap.dataset.sessionId, jobs: new Map(), logs: new Map(),
      abort: new AbortController(), busy: false, again: false, stale: false, timer: null,
      panel: null, selected: '', follow: true };
    s.observer = new MutationObserver(function (records) {
      if (records.some(function (record) {
        return Array.from(record.addedNodes).some(function (node) {
          return node.nodeType === 1 && (node.matches('.tool, .turn:not(.background-jobs-orphans)') || node.querySelector('.tool'));
        });
      })) { reconcile(s); refresh(s); }
    });
    s.observer.observe(wrap.querySelector('.chat-scroll'), { childList: true, subtree: true });
    refresh(s);
  }
  function open(id) {
    if (!state) return;
    var s = state;
    if (global.TomoArtifacts && global.TomoArtifacts.openProcesses) global.TomoArtifacts.openProcesses({ wrap: s.wrap });
    if (!current(s)) return;
    s.selected = id; s.follow = true; renderList(s); renderDetail(s); refresh(s);
  }
  document.addEventListener('visibilitychange', function () { if (state) refresh(state); });
  global.TomoProcesses = { attach: attach, detach: detach, mount: mount, detachPanel: detachPanel,
    refresh: function () { refresh(state); }, open: open };
})(window);
