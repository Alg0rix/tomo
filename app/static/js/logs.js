/* Runtime inspector: native SSE, safe text rendering, bounded DOM. */
(function () {
  'use strict';
  var section = document.getElementById('sec-logs');
  if (!section) return;
  var form = document.getElementById('logFilters');
  var rows = document.getElementById('logRecords');
  var viewport = document.getElementById('logViewport');
  var empty = document.getElementById('logEmpty');
  var status = document.getElementById('logStatus');
  var count = document.getElementById('logCount');
  var pause = document.getElementById('logPause');
  var source = null;
  var paused = false;
  var seen = new Set();
  var applied = new URLSearchParams();

  function clear() {
    rows.replaceChildren();
    seen.clear();
    count.textContent = '0 records';
    empty.hidden = false;
    empty.textContent = 'No matching logs yet.';
  }

  function append(item) {
    if (!item || !item.record || seen.has(item.id)) return;
    seen.add(item.id);
    var record = item.record;
    var detail = document.createElement('details');
    detail.className = 'runtime-log-record';
    detail.dataset.level = record.level || '';
    detail.dataset.id = item.id;
    var summary = document.createElement('summary');
    var time = document.createElement('time');
    time.dateTime = record.timestamp || '';
    var date = new Date(record.timestamp);
    time.textContent = isNaN(date.getTime()) ? '—' : date.toLocaleTimeString([], { hour12: false });
    time.title = record.timestamp || '';
    var level = document.createElement('span');
    level.className = 'runtime-log-level';
    level.textContent = record.level || '';
    var type = document.createElement('span');
    type.className = 'runtime-log-type';
    type.textContent = record.type || record.logger || '';
    type.title = type.textContent;
    var message = document.createElement('span');
    message.className = 'runtime-log-message';
    message.textContent = record.message || '';
    summary.append(time, level, type, message);
    var body = document.createElement('pre');
    body.textContent = JSON.stringify(record, null, 2);
    detail.append(summary, body);
    rows.append(detail);
    while (rows.childElementCount > 500) {
      seen.delete(rows.firstElementChild.dataset.id);
      rows.firstElementChild.remove();
    }
    empty.hidden = true;
    count.textContent = rows.childElementCount + ' records';
  }

  function scroll() {
    if (document.getElementById('logAutoScroll').checked) viewport.scrollTop = viewport.scrollHeight;
  }

  function stop() {
    if (source) source.close();
    source = null;
  }

  function start() {
    if (section.hidden || paused || source) return;
    status.textContent = 'Connecting…';
    var query = new URLSearchParams(applied);
    query.set('tail', '100');
    var current = new EventSource('/api/logs/stream?' + query.toString());
    source = current;
    current.onopen = function () { if (source === current) status.textContent = 'Live'; };
    current.addEventListener('snapshot', function (event) {
      if (source !== current) return;
      JSON.parse(event.data).forEach(append);
      if (!rows.childElementCount) { empty.hidden = false; empty.textContent = 'No matching logs yet.'; }
      scroll();
    });
    current.addEventListener('log', function (event) {
      if (source !== current) return;
      append(JSON.parse(event.data));
      scroll();
    });
    function unavailable(text) {
      if (source !== current) return;
      stop();
      paused = true;
      pause.textContent = 'Resume';
      status.textContent = text;
    }
    current.addEventListener('forbidden', function () { unavailable('Administrator access required.'); });
    current.addEventListener('unavailable', function () { unavailable('Log files unavailable. Resume to retry.'); });
    current.onerror = async function () {
      if (source !== current) return;
      status.textContent = 'Connection interrupted · reconnecting…';
      try {
        var check = await fetch('/api/logs?tail=0', { credentials: 'same-origin', cache: 'no-store' });
        if (source !== current) return;
        if (check.status === 401 || check.status === 403) unavailable('Administrator access required. Sign in again.');
        else if (check.status >= 500) unavailable('Log files unavailable. Resume to retry.');
      } catch (_) { /* EventSource reconnects after network failures. */ }
    };
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    applied = new URLSearchParams();
    new FormData(form).forEach(function (value, key) { if (String(value).trim()) applied.set(key, String(value).trim()); });
    stop();
    clear();
    if (!paused) start();
  });
  pause.addEventListener('click', function () {
    paused = !paused;
    pause.textContent = paused ? 'Resume' : 'Pause';
    if (paused) { stop(); status.textContent = 'Paused · resume loads recent logs'; }
    else start();
  });
  document.getElementById('logClear').addEventListener('click', clear);
  new MutationObserver(function () {
    if (section.hidden) stop();
    else start();
  }).observe(section, { attributes: true, attributeFilter: ['hidden'] });
  window.addEventListener('pagehide', stop);
  window.addEventListener('pageshow', start);
  start();
})();
