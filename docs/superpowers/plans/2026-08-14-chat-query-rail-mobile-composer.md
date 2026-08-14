# Chat query rail and mobile composer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a desktop-only user-query navigator to session chats and make the shared chat composer compact and usable on mobile.

**Architecture:** Keep persisted chat history and APIs unchanged. `sessions.js` derives query records from the existing history entries, maps each record to a transcript turn, and owns rail navigation/active state; `chat.js` emits a live-user-turn event and owns the per-composer mobile overflow toggle. Jinja adds only semantic mount/wrapper markup, while `tomo.css` owns desktop rail states and the `760px` mobile composer layout.

**Tech Stack:** FastAPI/Jinja templates, vanilla JavaScript, existing CSS custom properties, native `IntersectionObserver`/scroll APIs, and the existing pytest + browser/manual verification workflow.

## Global Constraints

- The query rail is desktop-only at the existing `760px` responsive boundary; it is not shown on mobile.
- One rail item represents each `type: "user"` history entry.
- Existing markdown, attachments, mentions, slash commands, queues, steering, permission mode, reasoning effort, and send/stop hooks must continue to work.
- No new dependency or browser framework is introduced.
- No Python, API, schema, or persisted-history changes are required.
- Mobile always-visible controls are attachment, permission mode, model/reasoning, and send/stop; Files, Agents, Clear, Context, and status details move behind the more panel.
- All visible user/assistant text inserted by the new UI is escaped or assigned through `textContent`.
- Preserve unrelated working-tree changes, including the pre-existing `.gitignore` modification.

---

## File map

- **Modify `app/templates/partials/sessions/main.html`** — mount the semantic query rail inside the session chat main column.
- **Modify `app/templates/partials/chat_composer.html`** — group existing secondary controls under a per-composer mobile-more wrapper without duplicating IDs or changing current control hooks.
- **Modify `app/templates/partials/agent_studio/panel_chat.html`** — pass the agent-studio composer a distinct mobile-more panel id.
- **Modify `app/static/js/sessions.js`** — derive query records, render/reconcile rail buttons, map rail buttons to turns, handle jump/active state, and consume live query events.
- **Modify `app/static/js/chat.js`** — mark live user turns with query metadata, emit `tomo:user-turn`, and manage the mobile-more menu lifecycle for every composer instance.
- **Modify `app/static/css/tomo.css`** — style the desktop rail and its hover/focus states plus the mobile composer dock, toolbar, overflow panel, and safe-area rules.
- **No new test file** — this repository has no browser test harness; verification uses JavaScript syntax checks, existing pytest/API regression tests, and a focused browser pass at the specified viewports.

## Interfaces between tasks

The following DOM/event contracts are shared by the implementation tasks:

```text
.chat-query-rail#chatQueryRail
  .chat-query-item[data-query-id="chat-query-N"]
    .chat-query-marker
    .chat-query-card

.turn[data-query-id="chat-query-N"]

CustomEvent "tomo:user-turn" on .chat-wrap:
detail: {
  turn: HTMLElement,
  queryId: string,
  queryIndex: number,
  text: string
}

CustomEvent "tomo:user-turn-removed" on .chat-wrap:
detail: { queryId: string }

.composer-mobile-more
  .composer-mobile-more-btn[aria-expanded]
  .composer-mobile-more-panel[aria-hidden]
    .composer-actions
    .composer-meta
```

The `chat-query-N` index is zero-based within the currently rendered session
transcript. A history render is authoritative and rebuilds the complete rail;
live events append the next index until the next history reconciliation.

---

### Task 1: Add semantic mounts and composer grouping

**Files:**
- Modify: `app/templates/partials/sessions/main.html` near the `.chat-main`/`.chat-scroll` markup and the session composer call.
- Modify: `app/templates/partials/chat_composer.html` around `.composer-toolbar-left` and `.composer-toolbar-right`.
- Modify: `app/templates/partials/agent_studio/panel_chat.html` at the shared composer call.

**Interfaces:**
- Consumes: existing `chatWrap`, `.chat-scroll`, `.composer-actions`, `.composer-meta`, and all existing button classes/IDs.
- Produces: the `#chatQueryRail` mount and the `.composer-mobile-more` DOM contract used by Tasks 2–6.

- [ ] **Step 1: Add the desktop rail mount**

Insert this before `.chat-scroll` in the session chat main column:

```html
<nav id="chatQueryRail" class="chat-query-rail" aria-label="User messages"></nav>
```

Do not put the rail inside `.chat-scroll`; it must remain an overlay sibling so transcript width and scroll padding stay unchanged.

- [ ] **Step 2: Group secondary composer controls without duplicating them**

Keep the textarea, attach button, permission mode, reasoning block, primary submit slot, and current IDs unchanged. Place the wrapper after the reasoning block inside `.composer-toolbar-left`; its desktop `display: contents` styling will keep the existing left-side action flow while exposing both existing secondary groups to the desktop row:

```html
<div class="composer-mobile-more">
  <button class="composer-mobile-more-btn" type="button"
          aria-label="More chat options" aria-expanded="false"
          aria-controls="{{ panel_id }}">
    <span aria-hidden="true">•••</span>
  </button>
  <div id="{{ panel_id }}" class="composer-mobile-more-panel"
       aria-hidden="true">
    <div class="composer-actions">
      <!-- existing Files / Agents / Clear buttons remain exactly here -->
    </div>
    <div class="composer-meta">
      <!-- existing Context and optional status remain exactly here -->
    </div>
  </div>
</div>
```

Add a `panel_id='composer-more-panel'` macro parameter. Pass `panel_id='session-composer-more'` from the sessions call and `panel_id='agent-composer-more'` from the agent-studio call. The JavaScript must still resolve the panel from the nearest `.composer-mobile-more` wrapper rather than relying on the id, so future pages can pass their own unique value.

- [ ] **Step 3: Check the rendered template contract**

Run:

```bash
rg -n "chatQueryRail|composer-mobile-more|composer-actions|composer-meta" app/templates/partials/sessions/main.html app/templates/partials/chat_composer.html
```

Expected: exactly one rail mount in the session main template, one more wrapper in the shared macro, distinct panel ids in the two current composer calls, and the existing action/meta control groups still present once each.

- [ ] **Step 4: Commit the markup-only change**

```bash
git add app/templates/partials/sessions/main.html app/templates/partials/chat_composer.html
git commit -m "feat: add chat navigation and mobile composer hooks"
```

---

### Task 2: Build query records and render the history rail

**Files:**
- Modify: `app/static/js/sessions.js` near the existing history rendering helpers and `renderHistory(entries)`.

**Interfaces:**
- Consumes: the `entries` array returned by `/api/sessions/{id}/chat`, existing `esc`, `Tomo.truncate`, `.chat-scroll`, and `.chat-wrap`.
- Produces: `chat-query-N` ids on turns, populated `#chatQueryRail` items, and a sessions-side `appendLiveQuery(detail)` function for Task 3.

- [ ] **Step 1: Add deterministic preview helpers**

Implement local helpers with these signatures:

```js
function queryId(index) { /* returns "chat-query-" + index */ }
function firstLine(text) { /* trimmed first non-empty line */ }
function previewText(text, maxChars) { /* whitespace-normalized, escaped at insertion */ }
function buildQueryRecords(entries) { /* returns query record objects in user-entry order */ }
```

Each record must contain `{ index, id, prompt, context }`. Start a new record on every `type === 'user'`. While scanning entries after that user and before the next user, use the first non-empty `final` or `subagent_final` content as `context`; leave it empty when the assistant has not produced a usable response. Do not treat tool calls, raw params, or attachments as the preview context.

- [ ] **Step 2: Render the rail from records**

Add a `renderQueryRail(records)` helper that clears `#chatQueryRail`, hides it when `records.length === 0`, and otherwise appends one real `<button>` per record. Build the DOM with `createElement`/`textContent` or escaped HTML; do not inject raw prompt text into attributes. Each button must include:

```html
<button type="button" class="chat-query-item" data-query-id="chat-query-N"
        aria-label="Jump to user message: full prompt">
  <span class="chat-query-marker" aria-hidden="true"></span>
  <span class="chat-query-card" aria-hidden="true">
    <span class="chat-query-title"></span>
    <span class="chat-query-context"></span>
  </span>
</button>
```

The click handler looks up `.turn[data-query-id="..."]`, calls `scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center' })`, applies the active class, and adds/removes a temporary `is-query-target` class on the turn.

- [ ] **Step 3: Attach ids to persisted history turns**

At the top of `renderHistory(entries)`, compute `queryRecords = buildQueryRecords(entries)` and call `renderQueryRail(queryRecords)`. Track a `queryCursor` while processing entries. When processing a `user` entry, pass its record into `startTurn` and set:

```js
turn.dataset.queryId = record.id;
turn.dataset.queryIndex = String(record.index);
```

If history begins with a non-user event and `startTurn()` creates a recovery turn, leave that turn without a query id so the rail still represents only user entries.

- [ ] **Step 4: Add active-query tracking**

Add one scroll observer for `.chat-scroll` that selects the query turn whose center is closest to the scroll viewport center, toggles `.is-active` on the matching rail button, and does nothing when there are no query turns. Use `requestAnimationFrame` or an equivalent guard so scrolling does not recalculate on every raw event. Disconnect/recreate it when `renderHistory()` clears the transcript to avoid stale element references.

- [ ] **Step 5: Verify history rendering with syntax and focused inspection**

Run:

```bash
node --check app/static/js/sessions.js
rg -n "buildQueryRecords|renderQueryRail|chat-query-|is-query-target" app/static/js/sessions.js
```

Expected: syntax passes; query ids are created only in the sessions renderer; no API or Python files change.

- [ ] **Step 6: Commit persisted-history rail behavior**

```bash
git add app/static/js/sessions.js
git commit -m "feat: add clickable session query rail"
```

---

### Task 3: Reconcile live user turns with the rail

**Files:**
- Modify: `app/static/js/chat.js` inside `appendUserBubble`, queue/removal helpers, and the composer lifecycle cleanup.
- Modify: `app/static/js/sessions.js` near the existing `chatWrap` event listeners.

**Interfaces:**
- Consumes: the shared `.chat-wrap`, the new `.turn[data-query-id]` contract, and the `queryId`/`queryIndex` naming from Task 2.
- Produces: a live rail item immediately after send/queue/steer bubbles are painted, without waiting for a history poll.

- [ ] **Step 1: Mark live user turns in `appendUserBubble`**

Before appending a new user turn, count existing `.turn[data-query-id]` nodes in `scroll`, assign the next `chat-query-N` id/index to the new turn, and dispatch:

```js
wrap.dispatchEvent(new CustomEvent('tomo:user-turn', {
  detail: {
    turn: turn,
    queryId: 'chat-query-' + queryIndex,
    queryIndex: queryIndex,
    text: value || ''
  }
}));
```

This applies to ordinary sends and steering because both paths use `appendUserBubble`. Queued messages must receive a rail item when their bubble is visible; if a queued bubble is removed on failed steering or stop, the sessions listener must remove its matching rail item too.

- [ ] **Step 2: Add `appendLiveQuery(detail)` in sessions.js**

Implement a helper that no-ops when the rail is absent/mobile, ignores an already-present `data-query-id`, and appends a preview button with the full user text as its prompt title and an empty context state. Store the element/turn relationship using `data-query-id`; do not retain a stale global array of DOM nodes.

- [ ] **Step 3: Listen for live add/remove events and refresh active state**

Add `chatWrap.addEventListener('tomo:user-turn', ...)` and `chatWrap.addEventListener('tomo:user-turn-removed', ...)` handlers beside the existing `tomo:turn-start`/`tomo:chat-done` listeners. The add handler calls `appendLiveQuery`, updates the active marker, and lets the current scroll observer include the new turn. The remove handler deletes the matching rail item. The existing history re-render remains the reconciliation path after completion.

- [ ] **Step 4: Remove live rail items with removed queued bubbles**

When `chat.js` removes a queued or failed-steering turn, dispatch `tomo:user-turn-removed` with `{ queryId: turn.dataset.queryId }` before/alongside removing the turn. The sessions listener removes the corresponding `.chat-query-item[data-query-id]` scoped to the current `.chat-wrap`, so another composer/session cannot be affected. Use the same event for the queued turns dropped by `stopTurn()`.

- [ ] **Step 5: Run JavaScript syntax checks**

```bash
node --check app/static/js/chat.js
node --check app/static/js/sessions.js
```

Expected: both commands exit 0.

- [ ] **Step 6: Commit live reconciliation**

```bash
git add app/static/js/chat.js app/static/js/sessions.js
git commit -m "feat: sync live chat turns with query rail"
```

---

### Task 4: Style the desktop query rail

**Files:**
- Modify: `app/static/css/tomo.css` near the sessions-chat layout rules and the existing `@media (max-width: 760px)` block.

**Interfaces:**
- Consumes: `.chat-query-rail`, `.chat-query-item`, `.chat-query-marker`, `.chat-query-card`, `.chat-query-title`, `.chat-query-context`, `.turn.is-query-target`, and `.is-active` classes from Tasks 2–3.
- Produces: the reference-style resting marker stack, hover/focus preview, active state, reduced-motion behavior, and mobile rail suppression.

- [ ] **Step 1: Add the desktop rail geometry**

Make `.sessions-chat .chat-main` the containing block and position the rail absolutely in the left gutter with a fixed top/bottom breathing room. Use `pointer-events: none` on the rail container and restore `pointer-events: auto` on buttons so empty gutter space never blocks the transcript or composer. Give the rail a bounded width and `overflow-y: auto` for long conversations.

- [ ] **Step 2: Add resting marker styles**

Style each item as a transparent button with a short marker and visible focus outline. Use `var(--text-faint)` for the guide/idle state, `var(--accent)` for `.is-active`, and existing surface/border/shadow tokens for the expanded card. The card should be hidden or visually collapsed at rest and must not affect rail layout width.

- [ ] **Step 3: Add hover/focus preview states**

On `:hover` and `:focus-visible`, expand the card toward the transcript to about `320px`, clamp the title to one line and context to two lines, and animate only opacity/translate/scale. Keep the button’s accessible label independent of the visual truncation. Add `.turn.is-query-target` styling that is a subtle accent outline/background pulse and does not change message dimensions.

- [ ] **Step 4: Add reduced-motion and mobile rules**

Under `@media (prefers-reduced-motion: reduce)`, remove rail/card transitions and target-turn animation. Under the existing `@media (max-width: 760px)`, set `.chat-query-rail { display: none; }` and remove any extra rail gutter assumptions.

- [ ] **Step 5: Commit rail styling**

```bash
git add app/static/css/tomo.css
git commit -m "style: refine desktop chat query rail"
```

---

### Task 5: Implement the mobile-more composer behavior

**Files:**
- Modify: `app/static/js/chat.js` in `initChat`, setup listeners, and the returned `destroy` method.

**Interfaces:**
- Consumes: `.composer-mobile-more`, `.composer-mobile-more-btn`, `.composer-mobile-more-panel`, existing child controls, and the shared `wrap` lifecycle.
- Produces: per-composer `openMoreMenu()`/`closeMoreMenu()` behavior with no global duplicate-id dependency.

- [ ] **Step 1: Add per-composer menu references**

Resolve the more wrapper from `wrap.querySelector('.composer-mobile-more')`, then find its button and panel. If the wrapper is absent (older markup or another page), leave chat initialization unchanged.

- [ ] **Step 2: Implement open/close/toggle helpers**

Use the button’s `aria-expanded` and panel’s `aria-hidden` as the state source:

```js
function closeMoreMenu() {
  moreBtn.setAttribute('aria-expanded', 'false');
  morePanel.setAttribute('aria-hidden', 'true');
  moreWrap.classList.remove('is-open');
}

function openMoreMenu() {
  moreBtn.setAttribute('aria-expanded', 'true');
  morePanel.setAttribute('aria-hidden', 'false');
  moreWrap.classList.add('is-open');
}
```

Toggle on the button, close on document pointerdown when the target is outside the wrapper, and close on Escape. Do not close the menu when clicking a control that itself opens a nested popover until that control has handled its own event.

- [ ] **Step 3: Close after secondary actions and on lifecycle changes**

Use event delegation on the panel to close after Files, Agents, Clear, or other secondary button activation; Context’s own popover can open first and the outer menu then closes. Call `closeMoreMenu()` before sending, on `destroy`, and when the session chat is hidden/switched so the next composer starts closed.

- [ ] **Step 4: Add cleanup listeners**

Store named document handlers and remove them in the existing `destroy()` method alongside the reasoning menu cleanup. Initialization must be idempotent through the existing `wrap.dataset.chatInit` guard.

- [ ] **Step 5: Run syntax checks**

```bash
node --check app/static/js/chat.js
```

Expected: PASS.

- [ ] **Step 6: Commit mobile menu behavior**

```bash
git add app/static/js/chat.js
git commit -m "feat: add responsive composer overflow menu"
```

---

### Task 6: Style the responsive mobile composer

**Files:**
- Modify: `app/static/css/tomo.css` in the composer block and the existing `@media (max-width: 760px)` block.

**Interfaces:**
- Consumes: the composer grouping from Task 1 and menu state classes/attributes from Task 5.
- Produces: a non-overflowing mobile dock with primary controls visible and a touch-sized secondary popover.

- [ ] **Step 1: Preserve desktop layout using display-contents rules**

Keep the desktop toolbar’s existing visual arrangement by making the grouping wrappers transparent to the desktop flex layout (`display: contents` where appropriate), hiding the mobile-more button/panel chrome, and retaining the current `.composer-actions`/`.composer-meta` sizing.

- [ ] **Step 2: Add mobile dock sizing and safe-area spacing**

At `max-width: 760px`, make the floating composer use `left/right` insets, `padding-bottom: calc(10px + env(safe-area-inset-bottom))`, a full-width shell, and a readable rounded surface. Reduce the thread’s bottom padding to match the actual mobile dock height so the final message is not hidden behind it.

- [ ] **Step 3: Define the primary mobile row**

Hide the desktop-only secondary groups from the row, show the more button, and keep attachment, permission mode, reasoning, and submit in one non-wrapping flex row. Hide `.composer-mode-suffix` at narrow widths, clamp the reasoning model/effort labels, and keep `.composer-submit` at a fixed size. Use `min-width: 0` on all flexible children and never rely on horizontal scrolling for the primary controls.

- [ ] **Step 4: Define the more popover**

Position `.composer-mobile-more-panel` above the dock, align it to the right edge without exceeding `calc(100vw - 24px)`, give it a surface/border/shadow treatment matching existing popovers, and switch it between `aria-hidden="true"`/`false` states. Stack the existing action/meta groups with at least a `40px` touch target. Ensure the panel appears above the composer’s gradient and below any higher-priority modal layer.

- [ ] **Step 5: Add narrow-width and reduced-motion rules**

At approximately `520px` and below, tighten horizontal gaps and padding, hide nonessential text labels in the more button only (keep its accessible label), and keep the input at a minimum usable height. Under reduced motion, disable composer menu animation.

- [ ] **Step 6: Commit responsive styling**

```bash
git add app/static/css/tomo.css
git commit -m "style: make chat composer mobile friendly"
```

---

### Task 7: Verify the integrated feature and regressions

**Files:**
- Modify: none unless verification exposes a concrete defect.

**Interfaces:**
- Consumes: all completed query rail and composer behavior from Tasks 1–6.
- Produces: verified desktop/mobile behavior and a clean working tree apart from the user’s pre-existing `.gitignore` change.

- [ ] **Step 1: Run JavaScript syntax checks**

```bash
node --check app/static/js/chat.js
node --check app/static/js/sessions.js
```

Expected: both PASS.

- [ ] **Step 2: Run the existing Python/API regression suite**

```bash
uv run pytest -q
```

Expected: all existing tests pass; no API/schema snapshots change.

- [ ] **Step 3: Start the app using the repository’s normal development command**

Use the command documented in `README.md`/the current workspace workflow, then open `/sessions` with a seeded or locally created session containing at least three user turns.

- [ ] **Step 4: Verify desktop query navigation**

At a desktop viewport around `1280x720`, confirm:

1. The rail is visible only beside the session transcript.
2. Resting items look like narrow index marks; hovering/focusing one expands only that item.
3. Long prompt text is truncated visually but present in the accessible label.
4. Clicking an item centers its user turn and briefly highlights it.
5. Scrolling changes the active marker.
6. Sending a new message adds a rail item immediately, and completing/reloading the session does not duplicate it.

- [ ] **Step 5: Verify mobile composer behavior**

At `320x720`, `375x812`, `430x900`, and `760px` wide, confirm:

1. The query rail is hidden.
2. The composer has no horizontal overflow and remains above the safe-area inset.
3. Attach, permission mode, reasoning, send, and stop are reachable in the primary row.
4. The more button opens a panel above the dock; Files, Agents, Clear, Context, and status remain usable.
5. Outside tap and Escape close the panel; switching/destroying the composer leaves it closed.
6. Textarea auto-grow, mentions, slash commands, attachment previews, queues, and send/stop behavior still work.

- [ ] **Step 6: Review the final diff and status**

```bash
git diff --check
git status --short
git log -7 --oneline
```

Expected: only the planned template/JS/CSS changes plus the already-existing `.gitignore` modification are present; no generated files or unrelated refactors are included.
