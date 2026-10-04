/* Mobile edge drawers. Touch intent is locked before taking over a gesture. */
(function () {
  'use strict';
  function init() {
    var media = matchMedia('(max-width: 760px)');
    var rail = document.getElementById('appRail');
    var workspaceBtn = document.getElementById('mobileWorkspaceOpen');
    var newChatBtn = document.getElementById('mobileNewChat');
    var chrome = document.querySelector('.app-mobile-bar');
    var chromeFrame = 0;
    if (!rail) return;
    var drag = null;
    var suppressClickUntil = 0;
    function chat() {
      var wrap = document.querySelector('.chat-wrap');
      return wrap && wrap.getClientRects().length ? wrap : null;
    }
    function panel() { var wrap = chat(); return wrap && wrap.querySelector('.chat-agent-panel'); }
    function railOpen() { return document.documentElement.classList.contains('is-rail-open'); }
    function panelOpen() { var el = panel(); return el && el.dataset.capOpen === '1'; }
    function updateChrome() {
      if (!chrome || chromeFrame) return;
      chromeFrame = requestAnimationFrame(function () {
        chromeFrame = 0;
        var wrap = chat();
        var scroll = wrap && wrap.querySelector('.chat-scroll');
        var opacity = media.matches && scroll && !panelOpen() ? Math.min(1, Math.max(0, scroll.scrollTop / 32)) : 0;
        var value = opacity.toFixed(3);
        if (chrome.style.getPropertyValue('--mobile-chrome-opacity') !== value) chrome.style.setProperty('--mobile-chrome-opacity', value);
      });
    }
    document.addEventListener('scroll', function (e) {
      if (e.target.matches && e.target.matches('.chat-scroll')) updateChrome();
    }, { capture: true, passive: true });
    function sync() {
      updateChrome();
      rail.inert = media.matches && !railOpen();
      if (workspaceBtn) {
        var title = document.getElementById('mobilePageTitle');
        var source = document.getElementById('chatAgentName');
        var label = chat() && source ? source.textContent : 'New chat';
        if (title && title.textContent !== label) title.textContent = label;
        workspaceBtn.disabled = !chat();
        workspaceBtn.setAttribute('aria-expanded', String(!!panelOpen()));
        workspaceBtn.setAttribute('aria-label', panelOpen() ? 'Close workspace' : 'Open workspace');
      }
    }
    function setOpen(side, open) {
      if (side === 'left') {
        if (open && panelOpen() && window.TomoArtifacts) TomoArtifacts.closePanel();
        Tomo.setRailOpen(open);
      } else if (window.TomoArtifacts) {
        if (open) { Tomo.setRailOpen(false); TomoArtifacts.openHome({ wrap: chat() }); }
        else TomoArtifacts.closePanel();
      }
      sync();
    }
    if (newChatBtn) newChatBtn.addEventListener('click', function () {
      Tomo.setRailOpen(false);
      var action = document.getElementById('newChatBtn');
      if (action) action.click();
    });
    if (workspaceBtn) workspaceBtn.addEventListener('click', function () { setOpen('right', !panelOpen()); });
    document.addEventListener('keydown', function (e) {
      if (media.matches && e.key === 'Escape') {
        if (railOpen()) { setOpen('left', false); document.getElementById('railMobileOpen').focus(); }
        else if (panelOpen()) { setOpen('right', false); if (workspaceBtn) workspaceBtn.focus(); }
      }
    });
    function finish(cancelled) {
      if (!drag) return;
      var current = drag;
      drag = null;
      if (!current.active) return;
      var travel = current.delta * current.direction;
      var flick = performance.now() - current.time < 100 && current.velocity * current.direction > .5;
      var complete = !cancelled && (travel > current.width * .28 || (travel > 16 && flick));
      var open = complete ? !current.wasOpen : current.wasOpen;
      // Commit the dragged position before restoring CSS's settle transition.
      current.el.getBoundingClientRect();
      var nowOpen = current.side === 'left' ? railOpen() : !!panelOpen();
      if (nowOpen !== open) setOpen(current.side, open);
      current.el.classList.remove('is-mobile-dragging');
      current.el.style.removeProperty('transform');
      suppressClickUntil = performance.now() + 350;
    }
    document.addEventListener('touchstart', function (e) {
      suppressClickUntil = 0;
      if (!media.matches || e.touches.length !== 1) { finish(true); return; }
      var target = e.target;
      if (target.closest('input, textarea, select, [contenteditable="true"], .composer, .modal-overlay, .xterm, .subagent-inspector')) return;
      // Leave horizontal code/table scrolling and carousels to their owners.
      for (var parent = target; parent && parent !== document.body; parent = parent.parentElement) {
        if (parent.scrollWidth > parent.clientWidth + 4 && /auto|scroll/.test(getComputedStyle(parent).overflowX)) return;
      }
      var touch = e.touches[0];
      var side = railOpen() ? 'left' : panelOpen() ? 'right' : touch.clientX <= 32 ? 'left' : touch.clientX >= innerWidth - 32 ? 'right' : '';
      if (!side || (side === 'right' && !window.TomoArtifacts) || (side === 'right' && !chat())) return;
      // A closed drawer must not steal taps/drags from controls at the edge.
      var wasOpen = side === 'left' ? railOpen() : !!panelOpen();
      if (!wasOpen && target.closest('button, a, video, audio')) return;
      drag = { side: side, wasOpen: wasOpen, x: touch.clientX, y: touch.clientY,
        lastX: touch.clientX, time: performance.now(), velocity: 0, delta: 0,
        direction: (side === 'left' ? 1 : -1) * (wasOpen ? -1 : 1) };
    }, { passive: true });
    document.addEventListener('touchmove', function (e) {
      if (!drag) return;
      if (e.touches.length !== 1) { finish(true); return; }
      var touch = e.touches[0];
      var dx = touch.clientX - drag.x, dy = touch.clientY - drag.y;
      if (!drag.active) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < 12) return;
        if (Math.abs(dx) < Math.abs(dy) * 1.4 || dx * drag.direction <= 0 || !e.cancelable) { drag = null; return; }
        drag.el = drag.side === 'left' ? rail : panel();
        if (!drag.el) { drag = null; return; }
        var transform = getComputedStyle(drag.el).transform;
        var presentedOffset = transform === 'none' ? 0 : new DOMMatrixReadOnly(transform).m41;
        drag.el.classList.add('is-mobile-dragging');
        if (!drag.wasOpen) {
          // Hold the panel offscreen while its contents are restored.
          drag.el.style.transform = 'translateX(' + (drag.side === 'left' ? '-100%' : '100%') + ')';
          setOpen(drag.side, true);
        }
        drag.width = drag.el.getBoundingClientRect().width;
        drag.startOffset = drag.wasOpen ? presentedOffset : drag.width * (drag.side === 'left' ? -1 : 1);
        drag.active = true;
      }
      if (!e.cancelable) { finish(true); return; }
      e.preventDefault();
      var now = performance.now();
      drag.velocity = (touch.clientX - drag.lastX) / Math.max(1, now - drag.time);
      drag.lastX = touch.clientX; drag.time = now;
      drag.delta = dx;
      var offset = drag.startOffset + dx;
      offset = drag.side === 'left' ? Math.max(-drag.width, Math.min(0, offset)) : Math.max(0, Math.min(drag.width, offset));
      drag.el.style.transform = 'translateX(' + offset + 'px)';
    }, { passive: false });
    document.addEventListener('touchend', function () { finish(false); }, { passive: true });
    document.addEventListener('touchcancel', function () { finish(true); }, { passive: true });
    document.addEventListener('click', function (e) {
      if (performance.now() < suppressClickUntil && e.sourceCapabilities && e.sourceCapabilities.firesTouchEvents) {
        e.preventDefault(); e.stopPropagation(); suppressClickUntil = 0;
      }
    }, true);
    media.addEventListener('change', function () { finish(true); if (!media.matches) Tomo.setRailOpen(false); sync(); });
    new MutationObserver(sync).observe(document.querySelector('.app-shell'), {
      subtree: true, childList: true, attributes: true, attributeFilter: ['data-cap-open', 'style'],
    });
    sync();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
