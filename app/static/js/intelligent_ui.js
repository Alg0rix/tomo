/* OpenIntelligentUI sandbox contract adapted for Tomo's chat transport. */
(function (global) {
  'use strict';
  var frames = new WeakMap();
  var imports = {
    three: 'https://esm.sh/three', 'three/': 'https://esm.sh/three/',
    d3: 'https://esm.sh/d3', 'd3/': 'https://esm.sh/d3/',
    gsap: 'https://esm.sh/gsap', 'gsap/': 'https://esm.sh/gsap/',
    'chart.js': 'https://esm.sh/chart.js', 'chart.js/': 'https://esm.sh/chart.js/'
  };

  // This runs inside the opaque-origin iframe, never in the Tomo document.
  function bridge() {
    function post(type, value) { parent.postMessage({ type: type, value: value }, '*'); }
    window.sendPrompt = function (args) {
      post('tomo:prompt', typeof args === 'string' ? args : args && args.text);
    };
    window.openLink = function (args) {
      post('tomo:link', typeof args === 'string' ? args : args && args.url);
    };
    // Also support the upstream Websandbox callback spelling.
    window.Websandbox = { connection: { remote: {
      sendPrompt: window.sendPrompt, openLink: window.openLink
    } } };
    document.addEventListener('click', function (event) {
      var link = event.target.closest && event.target.closest('a[href]');
      if (link) { event.preventDefault(); window.openLink(link.href); }
    });
    window.addEventListener('error', function () { post('tomo:error', 'Interactive answer could not run.'); });
    window.addEventListener('unhandledrejection', function () { post('tomo:error', 'Interactive answer could not run.'); });
    window.addEventListener('message', function (event) {
      if (event.source !== parent || !event.data || event.data.type !== 'tomo:measure') return;
      var content = document.getElementById('content') || document.body;
      post('tomo:resize', Math.ceil(content.getBoundingClientRect().height) + 32);
    });
    window.addEventListener('DOMContentLoaded', function () {
      var content = document.getElementById('content') || document.body;
      var queued = false;
      function resize() {
        if (queued) return;
        queued = true;
        requestAnimationFrame(function () {
          queued = false;
          post('tomo:resize', Math.ceil(content.getBoundingClientRect().height) + 32);
        });
      }
      new ResizeObserver(resize).observe(content);
      resize();
    });
  }

  function documentFor(node) {
    // The CSP precedes all generated content. The iframe's sandbox attribute
    // separately blocks same-origin access, forms, popups, and top navigation.
    var policy = "default-src 'none'; script-src 'unsafe-inline' https://esm.sh https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com; style-src 'unsafe-inline'; img-src https: data: blob:; font-src https: data:; connect-src https://esm.sh https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    var theme = (global.TomoIntelligentUITheme || '').replace(
      '@media (prefers-color-scheme: dark)', dark ? '@media all' : '@media not all'
    );
    var hostStyle = global.getComputedStyle(document.documentElement);
    var colors = {};
    ['--text', '--text-dim', '--text-faint'].forEach(function (token) {
      var value = hostStyle.getPropertyValue(token).trim();
      // Pass resolved color values only, never arbitrary host CSS into srcdoc.
      if (value && global.CSS.supports('color', value)) colors[token] = value;
    });
    var css = theme + '\n' +
      ':root{--color-background-primary:transparent;' +
      (colors['--text'] ? '--color-text-primary:' + colors['--text'] + ';' : '') +
      (colors['--text-dim'] ? '--color-text-secondary:' + colors['--text-dim'] + ';' : '') +
      (colors['--text-faint'] ? '--color-text-tertiary:' + colors['--text-faint'] + ';' : '') +
      '}body{margin:0;padding:0;color:var(--color-text-primary);background:transparent;font-family:var(--font-sans);font-size:14px}*{box-sizing:border-box}#content{display:flow-root;min-width:0}svg,canvas,img{max-width:100%}\n' + (node.css || '');
    // Closing tags in strings must not escape style/script elements.
    var script = String(node.jsFunctions || '') + '\n;\n' + String(node.jsExpressions || '');
    return '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="' + policy + '">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<style>' + css.replace(/<\/style/gi, '<\\/style') + '</style>' +
      '<script type="importmap">' + JSON.stringify({ imports: imports }) + '</script>' +
      (global.TomoTripAnimatorSource ? '<script>window.createTripAnimator = ' + global.TomoTripAnimatorSource + ';</script>' : '') +
      '<script>(' + bridge.toString() + ')();</script></head><body><div id="content">' +
      String(node.html || '') + '</div><script>' + script.replace(/<\/script/gi, '<\\/script') + '</script></body></html>';
  }

  global.addEventListener('message', function (event) {
    // Opaque origins all serialize to "null"; authenticate the exact window.
    var record = event.source && frames.get(event.source);
    if (!record || !record.frame.isConnected) return;
    var data = event.data;
    if (!data || typeof data !== 'object') return;
    if (data.type === 'tomo:resize') {
      if (typeof data.value !== 'number' || !Number.isFinite(data.value)) return;
      record.frame.style.height = Math.max(120, Math.min(1200, data.value)) + 'px';
    } else if (data.type === 'tomo:prompt') {
      if (typeof data.value !== 'string' || !data.value.trim() || data.value.length > 8000) return;
      record.onPrompt(data.value.trim());
    } else if (data.type === 'tomo:link') {
      if (typeof data.value !== 'string' || data.value.length > 2000) return;
      try {
        var url = new URL(data.value);
        if (url.protocol !== 'https:' || url.username || url.password) return;
        global.open(url.href, '_blank', 'noopener,noreferrer');
      } catch (_) { /* invalid URL */ }
    } else if (data.type === 'tomo:error') {
      record.status.hidden = false;
    }
  });

  function mount(node, onPrompt) {
    var wrapper = document.createElement('div');
    wrapper.className = 'gen-ui-sandbox';
    var frame = document.createElement('iframe');
    frame.title = node.title || 'Interactive answer';
    frame.setAttribute('sandbox', 'allow-scripts');
    frame.setAttribute('referrerpolicy', 'no-referrer');
    frame.style.height = Math.max(120, Math.min(1200, Number(node.initialHeight) || 400)) + 'px';
    var status = document.createElement('div');
    status.className = 'gen-ui-error';
    status.setAttribute('role', 'status');
    status.textContent = 'This interactive answer encountered an error. Ask Tomo to revise it.';
    status.hidden = true;
    wrapper.append(frame, status);
    frame.srcdoc = documentFor(node);
    // contentWindow becomes available only after insertion. The load handler
    // is too late for scripts executed during parsing, so registration happens
    // synchronously from the renderer after it appends the tree.
    wrapper._registerSandbox = function () {
      if (frame.contentWindow) frames.set(frame.contentWindow, { frame: frame, status: status, onPrompt: onPrompt });
    };
    // Moving a chat block can recreate its browsing context. Re-register on
    // every load and measure again after the bridge is ready.
    frame.addEventListener('load', function () {
      wrapper._registerSandbox();
      frame.contentWindow.postMessage({ type: 'tomo:measure' }, '*');
    });
    return wrapper;
  }
  global.TomoIntelligentUI = { mount: mount };
})(window);
