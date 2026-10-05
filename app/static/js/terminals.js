/* Local PTY views. Processes live on the server, never in panel DOM. */
(function (global) {
  'use strict';
  var sessions = new Map();
  var view = null;
  var observer = null;

  function api(sid, tail, options) {
    return Tomo.api('/api/sessions/' + encodeURIComponent(sid) + '/terminals' + (tail || ''), options);
  }
  function session(sid) {
    if (!sessions.has(sid)) sessions.set(sid, { terminals: new Map(), active: null });
    return sessions.get(sid);
  }
  function dispose(entry) {
    entry.disposed = true;
    clearTimeout(entry.retry);
    if (entry.socket) entry.socket.close();
    if (entry.term) entry.term.dispose();
    if (entry.screen) entry.screen.remove();
  }
  function detach() {
    if (observer) observer.disconnect();
    observer = null;
    view = null;
  }
  function send(entry, message) {
    if (entry.socket && entry.socket.readyState === WebSocket.OPEN) entry.socket.send(JSON.stringify(message));
  }
  function fit(entry) {
    if (!entry.fit || !entry.screen.isConnected || entry.screen.hidden || !entry.screen.clientWidth) return;
    try {
      entry.fit.fit();
      send(entry, { type: 'resize', cols: entry.term.cols, rows: entry.term.rows });
    } catch (_) {}
  }
  function connect(sid, entry) {
    if (entry.disposed || entry.stopped) return;
    var url = new URL('/api/sessions/' + encodeURIComponent(sid) + '/terminals/' + entry.info.id + '/ws', location.href);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var socket = new WebSocket(url);
    socket.binaryType = 'arraybuffer';
    entry.socket = socket;
    entry.connection = 'connecting';
    socket.onmessage = function (event) {
      if (entry.disposed) return;
      if (event.data instanceof ArrayBuffer) {
        entry.info.last_activity = Date.now() / 1000;
        entry.term.write(new Uint8Array(event.data));
        return;
      }
      var data = JSON.parse(event.data);
      if (data.type === 'ready') {
        // Reset before replay, so reconnect never duplicates the scrollback.
        entry.term.reset();
        entry.info = data;
        entry.connection = 'connected';
        entry.attempt = 0;
        fit(entry);
      } else if (data.type === 'status') {
        entry.info = data;
      } else if (data.type === 'exit') {
        entry.info.running = false;
        entry.info.exit_code = data.exit_code;
        entry.term.options.disableStdin = true;
      } else if (data.type === 'closed') {
        entry.stopped = true;
        entry.info.running = false;
        entry.connection = data.reason === 'idle' ? 'expired' : 'closed';
        entry.term.options.disableStdin = true;
        entry.term.writeln('\r\n[Terminal closed' + (data.reason === 'idle' ? ' after 30 minutes idle' : '') + ']');
        socket.close();
        // A closed PTY in another/hidden chat no longer needs a renderer cache.
        if (!view || view.sid !== sid) {
          dispose(entry);
          var state = session(sid);
          state.terminals.delete(entry.info.id);
          if (state.active === entry.info.id) state.active = null;
        }
      }
      updateStatus();
    };
    socket.onclose = function (event) {
      if (entry.disposed || entry.stopped) return;
      entry.connection = 'disconnected';
      if ([4403, 4404, 4429].indexOf(event.code) !== -1) {
        entry.stopped = true;
        entry.info.running = false;
        entry.connection = event.code === 4404 ? 'closed' : 'denied';
        entry.term.options.disableStdin = true;
      } else {
        entry.retry = setTimeout(function () { connect(sid, entry); }, Math.min(10000, 1000 * Math.pow(2, entry.attempt++ || 0)));
      }
      updateStatus();
    };
    socket.onerror = function () { socket.close(); };
  }
  function initialize(sid, entry, stage) {
    if (entry.term) return;
    entry.screen = document.createElement('div');
    entry.screen.className = 'terminal-screen';
    stage.appendChild(entry.screen);
    var styles = getComputedStyle(document.documentElement);
    entry.term = new Terminal({
      cursorBlink: true, fontSize: 13, scrollback: 5000, convertEol: false,
      fontFamily: styles.getPropertyValue('--font-mono').trim() || 'monospace',
      theme: { background: '#111417', foreground: '#dce1e5', cursor: '#dce1e5', selectionBackground: '#354751' },
    });
    entry.fit = new FitAddon.FitAddon();
    entry.term.loadAddon(entry.fit);
    entry.term.open(entry.screen);
    entry.term.onData(function (data) {
      if (!entry.info.running || entry.connection !== 'connected') return;
      entry.info.last_activity = Date.now() / 1000;
      send(entry, { type: 'input', data: data });
    });
    connect(sid, entry);
  }
  function updateStatus() {
    if (!view || !view.host.isConnected) return;
    var state = session(view.sid);
    var entry = state.terminals.get(state.active);
    var label = view.host.querySelector('[data-terminal-status]');
    var timer = view.host.querySelector('[data-terminal-idle]');
    if (!entry) { label.textContent = ''; timer.textContent = ''; return; }
    var info = entry.info;
    label.textContent = entry.connection === 'expired' ? 'Closed · idle timeout' :
      entry.connection === 'closed' ? 'Closed' : entry.connection === 'denied' ? 'Access denied' :
      !info.running ? 'Exited · ' + (info.exit_code == null ? '—' : info.exit_code) :
      entry.connection === 'connected' ? (info.busy ? 'Running command' : 'Shell ready') :
      entry.connection === 'disconnected' ? 'Reconnecting…' : 'Connecting…';
    if (entry.stopped || !info.running) timer.textContent = '';
    else if (info.busy) timer.textContent = 'Idle timer paused';
    else {
      var left = Math.max(0, Math.ceil(info.last_activity + info.idle_timeout - Date.now() / 1000));
      timer.textContent = 'Idle close in ' + Math.floor(left / 60) + ':' + String(left % 60).padStart(2, '0');
    }
    view.host.querySelector('[data-terminal-cwd]').textContent = info.cwd;
    view.host.querySelectorAll('[data-terminal-select]').forEach(function (button) {
      var item = state.terminals.get(button.dataset.terminalSelect);
      button.classList.toggle('is-exited', !item || !item.info.running);
    });
  }
  function draw() {
    if (!view || !view.host.isConnected) return;
    var state = session(view.sid);
    var tabs = view.host.querySelector('[data-terminal-tabs]');
    var stage = view.host.querySelector('[data-terminal-stage]');
    tabs.replaceChildren();
    stage.replaceChildren();
    if (!state.terminals.size) {
      var emptyBackend = view.backend === 'host' ? 'host' : 'container';
      stage.innerHTML = '<div class="workspace-empty"><span class="workspace-empty-symbol" aria-hidden="true">&gt;_</span>' +
        '<h3>' + (emptyBackend === 'host' ? 'Your granted host shell, in this chat' : 'Your container shell, in this chat') + '</h3><p>' + (emptyBackend === 'host'
          ? 'Unrestricted execution at the granted destination. The OS account may reach beyond this chat; platform checks still apply.'
          : 'Run commands with this chat\'s authorized folders and limits. Read-only inputs cannot be modified.') + '</p>' +
        '<button type="button" class="btn" data-terminal-create>Open terminal</button></div>';
      stage.querySelector('[data-terminal-create]').onclick = create;
    }
    var number = 0;
    state.terminals.forEach(function (entry, id) {
      number++;
      var group = document.createElement('div');
      group.className = 'terminal-tab' + (id === state.active ? ' active' : '');
      var select = document.createElement('button');
      select.type = 'button';
      select.dataset.terminalSelect = id;
      select.textContent = entry.info.shell + ' ' + number;
      select.setAttribute('aria-pressed', String(id === state.active));
      select.onclick = function () { state.active = id; draw(); };
      var close = document.createElement('button');
      close.type = 'button';
      close.className = 'terminal-tab-close';
      close.textContent = '×';
      close.setAttribute('aria-label', 'Close ' + select.textContent);
      close.onclick = async function () {
        var sid = view.sid;
        if (entry.info.running && !global.confirm('Close this terminal? Running commands will be stopped.')) return;
        close.disabled = true;
        try {
          if (!entry.stopped) await api(sid, '/' + id, { method: 'DELETE' });
          dispose(entry);
          state.terminals.delete(id);
          if (state.active === id) state.active = state.terminals.keys().next().value || null;
          if (view && view.sid === sid) draw();
        } catch (error) { Tomo.toast(error.message, 'err'); close.disabled = false; }
      };
      group.append(select, close);
      tabs.appendChild(group);
      if (id === state.active) {
        initialize(view.sid, entry, stage);
        stage.appendChild(entry.screen);
        entry.term.focus();
        requestAnimationFrame(function () { fit(entry); });
      }
    });
    updateStatus();
  }
  async function create() {
    if (!view || view.creating || view.loading) return;
    var current = view;
    current.creating = true;
    current.host.querySelectorAll('[data-terminal-create]').forEach(function (button) { button.disabled = true; });
    try {
      var info = await api(current.sid, '', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cols: 80, rows: 24 }) });
      var state = session(current.sid);
      state.terminals.set(info.id, { info: info, attempt: 0 });
      state.active = info.id;
      if (view && view.sid === current.sid && !view.loading) draw();
    } catch (error) { Tomo.toast(error.message, 'err'); }
    finally {
      current.creating = false;
      current.host.querySelectorAll('[data-terminal-create]').forEach(function (button) { button.disabled = false; });
    }
  }
  async function mount(host, sid) {
    detach();
    host.classList.add('terminal-workspace');
    if (!sid) {
      host.innerHTML = '<div class="workspace-empty"><h3>Select a chat first</h3><p>Each chat has its own terminals.</p></div>';
      return;
    }
    if (!global.Terminal || !global.FitAddon) {
      host.innerHTML = '<div class="workspace-empty">Terminal renderer could not load. Reload the page to retry.</div>';
      return;
    }
    var current = view = { host: host, sid: sid, creating: false, loading: true };
    host.innerHTML = '<div class="terminal-toolbar"><span class="terminal-local" data-terminal-backend>Restricted container</span>' +
      '<button type="button" class="btn sm" data-terminal-create disabled>+ New terminal</button></div>' +
      '<div class="terminal-tabs" data-terminal-tabs aria-label="Terminal instances"></div>' +
      '<div class="terminal-location" data-terminal-cwd title="Terminal starting directory"></div>' +
      '<div class="terminal-stage" data-terminal-stage><div class="cap-msg">Loading terminals…</div></div>' +
      '<div class="terminal-footer"><span data-terminal-status role="status"></span><span data-terminal-idle title="Input/output reset the timer. Foreground commands pause it."></span></div>';
    host.querySelector('[data-terminal-create]').onclick = create;
    observer = new ResizeObserver(function () {
      var state = session(sid);
      var entry = state.terminals.get(state.active);
      if (entry) fit(entry);
    });
    observer.observe(host);
    var knownIds = new Set(session(sid).terminals.keys());
    try {
      var data = await api(sid);
      if (view !== current || !host.isConnected) return;
      var state = session(sid);
      var ids = new Set(data.terminals.map(function (info) { return info.id; }));
      state.terminals.forEach(function (entry, id) {
        if (knownIds.has(id) && !ids.has(id)) { dispose(entry); state.terminals.delete(id); }
      });
      data.terminals.forEach(function (info) {
        if (state.terminals.has(info.id)) state.terminals.get(info.id).info = info;
        else state.terminals.set(info.id, { info: info, attempt: 0 });
      });
      if (!state.terminals.has(state.active)) state.active = state.terminals.keys().next().value || null;
      host.querySelector('[data-terminal-cwd]').textContent = data.cwd;
      view.backend = data.backend;
      var backendLabel = host.querySelector('[data-terminal-backend]');
      if (backendLabel) backendLabel.textContent = data.backend === 'host' ? 'Granted host · unrestricted' : 'Restricted container';
      draw();
    } catch (error) {
      if (view === current) host.querySelector('[data-terminal-stage]').textContent = 'Could not load terminals: ' + error.message;
    } finally {
      current.loading = false;
      if (view === current) host.querySelectorAll('[data-terminal-create]').forEach(function (button) { button.disabled = false; });
    }
  }
  setInterval(updateStatus, 1000);
  global.addEventListener('pagehide', function () {
    sessions.forEach(function (state) { state.terminals.forEach(dispose); });
    sessions.clear();
    detach();
  });
  global.addEventListener('pageshow', function (event) {
    if (!event.persisted) return;
    var host = document.querySelector('.terminal-workspace');
    var wrap = host && host.closest('.chat-wrap');
    if (wrap && wrap.querySelector('.chat-agent-panel[data-cap-open="1"]')) mount(host, wrap.dataset.sessionId);
  });
  global.TomoTerminals = { mount: mount, detach: detach };
})(window);
