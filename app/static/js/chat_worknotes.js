/* chat_worknotes.js — fold a turn's reasoning notes + tool calls into one
 * "working notes" block.
 *
 * Both render paths (live stream in chat_turn_stream.js, history replay in
 * sessions.js) append reasoning notes (.si-think) and tool cards (.tool)
 * straight into the turn. This module watches for those and regroups them:
 *
 *   .work
 *     button.work-head          "Worked through 4 steps · 9 tools · 1 failed"
 *     ol.work-steps
 *       li.wn-step               one reasoning note + the tools it led to
 *         .si-think              (note)
 *         .wn-tools              (tool chips)
 *
 * The same grouping runs inside the subagent inspector timeline, so a
 * delegated agent's work reads exactly like the coordinator's.
 *
 * Grouping only moves nodes; card lookups (findToolCard, resume counters)
 * use querySelectorAll on the turn, which still sees them in document order.
 */
(function () {
  'use strict';

  var HOST_SEL = '.turn, .si-timeline';

  function isWorkItem(el) {
    return el.nodeType === 1 &&
      (el.classList.contains('si-think') || el.classList.contains('tool'));
  }

  // Transient elements that sit at the end of a turn without ending the block.
  // A still-streaming assistant bubble is neutral too: mid-turn it usually
  // turns into the next reasoning note, so it must not fold the block early.
  function isNeutral(el) {
    return el.nodeType === 1 &&
      (el.classList.contains('turn-pending') || el.classList.contains('tloading') ||
       (el.classList.contains('msg') && el.classList.contains('streaming')));
  }

  function createGroup(host) {
    var g = document.createElement('section');
    g.className = 'work is-open';
    g.innerHTML =
      '<button type="button" class="work-head" aria-expanded="true">' +
        '<span class="work-chev" aria-hidden="true"></span>' +
        '<span class="work-title"></span>' +
      '</button>' +
      '<ol class="work-steps"></ol>';
    g.querySelector('.work-head').addEventListener('click', function () {
      var open = !g.classList.contains('is-open');
      host.dataset.workUserOpen = open ? '1' : '0';
      g.dataset.userToggled = '1';
      setOpen(g, open);
    });
    // Later thinking rounds can create a new block in the same turn.
    // Carry the user's choice forward instead of opening that block again.
    if (host.dataset.workUserOpen !== undefined) {
      g.dataset.userToggled = '1';
      setOpen(g, host.dataset.workUserOpen === '1');
    }
    return g;
  }

  function setOpen(g, open) {
    g.classList.toggle('is-open', open);
    var head = g.querySelector(':scope > .work-head');
    if (head) head.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function createStep(group) {
    var li = document.createElement('li');
    li.className = 'wn-step';
    var tools = document.createElement('div');
    tools.className = 'wn-tools';
    li.appendChild(tools);
    group.querySelector(':scope > .work-steps').appendChild(li);
    return li;
  }

  function lastStep(group) {
    var steps = group.querySelectorAll(':scope > .work-steps > .wn-step');
    return steps.length ? steps[steps.length - 1] : null;
  }

  function place(item, group, step) {
    if (item.classList.contains('si-think')) {
      // A note opens a new step unless the current step is still empty.
      if (!step || step.querySelector(':scope > .si-think') ||
          step.querySelector(':scope > .wn-tools > .tool')) {
        step = createStep(group);
      }
      step.insertBefore(item, step.querySelector(':scope > .wn-tools'));
      return step;
    }
    if (!step) step = createStep(group);
    step.querySelector(':scope > .wn-tools').appendChild(item);
    return step;
  }

  function groupHost(host) {
    var group = null;
    var step = null;
    var kids = Array.prototype.slice.call(host.children);
    for (var i = 0; i < kids.length; i++) {
      var el = kids[i];
      if (el.classList.contains('work')) {
        group = el;
        step = lastStep(el);
        continue;
      }
      if (isNeutral(el)) continue;
      if (!isWorkItem(el)) {
        group = null;
        step = null;
        continue;
      }
      if (!group) {
        group = createGroup(host);
        host.insertBefore(group, el);
      }
      step = place(el, group, step);
    }
    host.querySelectorAll(':scope > .work').forEach(summarize);
  }

  function generating(group) {
    var wrap = group.closest('.chat-wrap');
    var composer = wrap && wrap.querySelector('.composer-shell.is-generating, .composer.is-generating, .is-generating');
    return !!composer;
  }

  // Anything meaningful after the block means the agent moved on.
  function hasFollower(group) {
    var n = group.nextElementSibling;
    while (n) {
      if (!isNeutral(n)) return true;
      n = n.nextElementSibling;
    }
    return false;
  }

  function firstLine(text, n) {
    var t = String(text || '').replace(/[#*_`>]/g, '').replace(/\s+/g, ' ').trim();
    var m = t.match(/^(.{12,}?[.!?])(\s|$)/);
    t = m ? m[1] : t;
    return t.length > n ? t.slice(0, n) + '…' : t;
  }

  // textContent glues block elements together ("<p>A.</p><p>B.</p>" → "A.B."),
  // which breaks sentence detection. Pad blocks with a space first.
  function noteText(el) {
    if (!el) return '';
    var c = el.cloneNode(true);
    c.querySelectorAll('p, li, br, div, pre, blockquote, h1, h2, h3, h4, h5, h6').forEach(function (b) {
      b.after(' ');
    });
    return c.textContent;
  }

  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  function summarize(group) {
    var steps = group.querySelectorAll(':scope > .work-steps > .wn-step');
    var tools = group.querySelectorAll('.tool:not([data-tool-name="delegate"]), .tool.error[data-tool-name="delegate"]');
    var failed = group.querySelectorAll('.tool.error').length;
    var running = group.querySelectorAll('.tool.loading, .tool.running').length;
    var inInspector = !!group.closest('.si-timeline');
    var live = running > 0 || (!hasFollower(group) && generating(group) && !inInspector);

    // Step state dots.
    steps.forEach(function (st, idx) {
      var stRunning = st.querySelector('.tool.loading, .tool.running');
      var stFailed = st.querySelector('.tool.error') && !st.querySelector('.tool.ok');
      var isLast = idx === steps.length - 1;
      st.classList.toggle('is-now', !!stRunning || (live && isLast));
      st.classList.toggle('is-fail', !!stFailed && !stRunning);
      st.classList.toggle('is-done', !stRunning && !stFailed && !(live && isLast));
    });

    var visible = group.querySelectorAll('.si-think').length + tools.length;
    group.classList.toggle('is-empty', visible === 0 && running === 0);

    var title = group.querySelector(':scope > .work-head .work-title');
    if (!title) return;
    var html;
    if (live) {
      var notes = group.querySelectorAll('.si-think');
      var lastNote = notes.length ? noteText(notes[notes.length - 1]) : '';
      var runningTool = group.querySelector('.tool.loading .tname, .tool.running .tname');
      var findingTools = runningTool && runningTool.closest('.tool').dataset.toolName === 'search_tools';
      var allTools = group.querySelectorAll('.tool');
      var lastTool = allTools.length ? allTools[allTools.length - 1] : null;
      var discoveredTools = !runningTool && lastTool && lastTool.dataset.toolName === 'search_tools' &&
        lastTool.classList.contains('has-output') ? lastTool.querySelector('.tname') : null;
      var status = freshStatus(group);
      // Open block already shows the notes; the title only adds what isn't
      // on screen yet (a heartbeat or the tool in flight). Folded by the
      // user, it falls back to the latest note.
      var folded = !group.classList.contains('is-open');
      var what = findingTools ? runningTool.textContent : discoveredTools ? discoveredTools.textContent :
        (status || (runningTool ? 'Running ' + runningTool.textContent : (folded ? firstLine(lastNote, 90) : '')));
      html = '<span class="work-live" aria-hidden="true"></span><span>Working</span>' +
        (what ? '<span class="work-what"> · ' + escapeHtml(what) + '</span>' : '');
    } else {
      var parts = [];
      if (steps.length > 1) parts.push('Worked through ' + plural(steps.length, 'step', 'steps'));
      if (tools.length) parts.push((parts.length ? '' : 'Used ') + plural(tools.length, 'tool', 'tools'));
      if (!parts.length) {
        var onlyNote = group.querySelector('.si-think');
        parts.push(onlyNote ? firstLine(noteText(onlyNote), 110) : 'Thought it through');
      }
      html = escapeHtml(parts.join(' · ')) +
        (failed ? ' · <span class="work-fail">' + plural(failed, 'failed', 'failed') + '</span>' : '');
    }
    // Compare against what we last wrote: innerHTML re-serializes entities,
    // so comparing to it would re-trigger the observer forever.
    if (title._html !== html) {
      title._html = html;
      title.innerHTML = html;
    }
    group.classList.toggle('is-live', live);

    if (!group.dataset.userToggled) {
      // Open while it's happening; fold once the agent moves on. Inspector
      // blocks stay open — there the steps are the point.
      setOpen(group, live || inInspector);
    }
  }

  // Runtime heartbeats ("waiting on gpt-5 — 48s, model is thinking").
  var STATUS_TTL_MS = 45000;
  function freshStatus(el) {
    var wrap = el.closest('.chat-wrap');
    var st = wrap && wrap._turnStatus;
    if (!st || Date.now() - st.at > STATUS_TTL_MS) return '';
    return st.message;
  }

  function onTurnStatus(ev) {
    var wrap = ev.target.closest ? ev.target.closest('.chat-wrap') : null;
    var d = ev.detail || {};
    if (!wrap || !d.message) return;
    wrap._turnStatus = { message: String(d.message), at: Date.now() };
    // Before the first note/tool the only thing on screen is the pending row.
    var pendings = wrap.querySelectorAll('.turn-pending .name');
    var pending = pendings.length ? pendings[pendings.length - 1] : null;
    if (pending) pending.textContent = d.message;
    wrap.querySelectorAll('.turn').forEach(function (t) { queue(t); });
  }

  function escapeHtml(s) {
    return window.Tomo && Tomo.escapeHtml ? Tomo.escapeHtml(s) : String(s);
  }

  // "[From Ops]" / "[From Ops — tool run]" are coordinator context relays.
  // Show the answer relay with a small attribution, fold the raw tool log.
  function tidyRelays(root) {
    root.querySelectorAll('.msg.assistant:not([data-relay-checked])').forEach(function (msg) {
      if (msg.classList.contains('streaming')) return;
      var body = msg.querySelector('.bubble-body');
      if (!body) return;
      var text = (body.textContent || '').trim();
      if (!text) return;
      msg.dataset.relayChecked = '1';
      var m = text.match(/^\[From ([^\]—]+?)(\s+—\s+tool run)?\]/);
      if (!m) return;
      var from = m[1].trim();
      var first = body.firstElementChild;
      if (first && first.textContent.trim().indexOf('[From') === 0) {
        first.textContent = first.textContent.replace(/^\s*\[From [^\]]+\]\s*/, '');
        if (!first.textContent.trim()) first.remove();
      }
      msg.classList.add('is-relay');
      var tag = document.createElement('div');
      tag.className = 'relay-tag';
      tag.textContent = m[2] ? from + ' · tool log' : 'From ' + from;
      body.parentNode.insertBefore(tag, body);
      if (m[2]) {
        msg.classList.add('is-relay-log', 'is-folded');
        tag.setAttribute('role', 'button');
        tag.tabIndex = 0;
        var toggle = function () { msg.classList.toggle('is-folded'); };
        tag.addEventListener('click', toggle);
        tag.addEventListener('keydown', function (ev) {
          if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggle(); }
        });
      }
    });
  }

  // Pasted walls of text in user messages fold to a few lines.
  var LONG_USER_PX = 260;
  function foldLongUser(root) {
    root.querySelectorAll('.msg.user:not([data-fold-checked])').forEach(function (msg) {
      var body = msg.querySelector('.bubble-body');
      if (!body || !body.textContent.trim()) return;
      msg.dataset.foldChecked = '1';
      if (body.scrollHeight <= LONG_USER_PX) return;
      msg.classList.add('is-long');
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'msg-more';
      btn.textContent = 'Show more';
      btn.addEventListener('click', function () {
        var folded = msg.classList.toggle('is-long');
        btn.textContent = folded ? 'Show more' : 'Show less';
      });
      body.insertAdjacentElement('afterend', btn);
    });
  }

  var pending = new Set();
  var scheduled = false;

  function flush() {
    scheduled = false;
    pending.forEach(function (host) {
      if (host.isConnected) groupHost(host);
    });
    pending.clear();
    document.querySelectorAll('.chat-scroll').forEach(function (sc) {
      tidyRelays(sc);
      foldLongUser(sc);
    });
  }

  function queue(host) {
    if (!host) return;
    pending.add(host);
    if (!scheduled) {
      scheduled = true;
      // Microtask, not rAF: group before the browser paints the raw cards.
      Promise.resolve().then(flush);
    }
  }

  function hostOf(node) {
    if (!node || node.nodeType !== 1) return null;
    if (node.matches(HOST_SEL)) return node;
    return node.closest(HOST_SEL);
  }

  var observer = new MutationObserver(function (records) {
    for (var i = 0; i < records.length; i++) {
      var r = records[i];
      if (r.type === 'attributes') {
        // Turn started/finished: live blocks fold or unfold.
        if (r.target.matches && r.target.matches('.composer, .composer-shell')) {
          var w = r.target.closest('.chat-wrap');
          if (w) w.querySelectorAll('.turn').forEach(queue);
          continue;
        }
        // Tool finished / failed: refresh its block summary.
        var g = r.target.closest && r.target.closest('.work');
        if (g) queue(g.parentElement);
        continue;
      }
      // Our own label/clock writes are not structure changes.
      if (r.target.closest && r.target.closest('.work-head, .sw-state, .sw-live, .relay-tag')) continue;
      queue(hostOf(r.target));
      r.addedNodes.forEach(function (n) {
        if (n.nodeType !== 1) return;
        if (n.matches(HOST_SEL)) queue(n);
        if (n.querySelectorAll) n.querySelectorAll(HOST_SEL).forEach(queue);
      });
    }
  });

  // The composer floats over the thread. Reserve exactly its height below the
  // last message so plan strips / tall drafts never cover the latest turn.
  function reserveComposer() {
    if (!window.ResizeObserver) return;
    document.querySelectorAll('.chat-wrap').forEach(function (wrap) {
      var comp = wrap.querySelector('.composer.composer-float');
      var scroll = wrap.querySelector('.chat-scroll');
      if (!comp || !scroll || comp._reserved) return;
      comp._reserved = true;
      new ResizeObserver(function () {
        var nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
        wrap.style.setProperty('--composer-h', Math.ceil(comp.getBoundingClientRect().height) + 'px');
        if (nearBottom) scroll.scrollTop = scroll.scrollHeight;
      }).observe(comp);
    });
  }

  function start() {
    reserveComposer();
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['class'],
    });
    document.querySelectorAll(HOST_SEL).forEach(queue);
    // Composer flips .is-generating at turn end; re-summarize so live blocks fold.
    document.addEventListener('tomo:turn-status', onTurnStatus);
    document.addEventListener('tomo:turn-idle', function () {
      document.querySelectorAll(HOST_SEL).forEach(queue);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();

  window.TomoWorknotes = { regroup: groupHost };
})();
