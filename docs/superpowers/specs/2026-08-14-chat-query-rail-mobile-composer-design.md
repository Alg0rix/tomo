# Chat query rail and mobile composer

## Goal

Make long session threads easier to navigate and make the chat composer usable
on narrow screens. On desktop, show a quiet left-side index of user queries
that expands into a reference-style preview on hover/focus and jumps to the
matching message. On mobile, replace the crowded desktop footer with a compact
composer that keeps the essential controls visible and moves secondary actions
into an accessible overflow panel.

This is a presentation-layer change. It does not change chat APIs, persisted
history entries, or the session data model.

## Scope and constraints

- The query rail is desktop-only at the existing `760px` responsive boundary;
  it is not shown on mobile.
- One rail item represents each `type: "user"` history entry.
- Existing chat behavior remains intact: markdown, attachments, mentions,
  slash commands, queues, steering, permission mode, reasoning effort, and
  send/stop state must continue to use their current hooks.
- The existing dark visual language is retained: low-contrast surfaces,
  hairline borders, restrained accent color, and short transitions.
- No new dependency or browser framework is introduced.

## Query rail

### Source of truth and structure

`renderHistory(entries)` remains the source of truth for persisted messages.
The sessions template adds an empty semantic rail mount inside `.chat-main`.
For each user entry, rendering will:

1. Create the normal `.turn` and assign it a query index/id.
2. Derive a compact preview record from the user prompt and the following
assistant-side entries until the next user entry.
3. Render a keyboard-accessible button in a `nav`/rail container with the same
   query id.

Preview extraction is deterministic and defensive: the prompt title uses the
first non-empty line of the user content, and the context/excerpt uses the
first useful assistant `final`/`subagent_final` content in that turn. If no
assistant content exists yet, the card shows only the prompt and a muted
in-progress/empty state. All visible text is escaped before insertion.

The id/index is stable for the current transcript order, so a rail rebuild
after a history refresh still points to the right turn. A live user message
created by `chat.js` receives the next query index immediately and emits a
lightweight `tomo:user-turn` event so the sessions rail can append it without
waiting for a history poll. A later full history render reconciles the list.

### Resting and expanded states

At rest, the rail is a narrow, low-contrast stack of short horizontal index
marks with a faint vertical guide. The active query uses the accent color and a
slightly longer mark. The rail overlays the left gutter of `.chat-main`, so it
does not reduce or reposition the centered `820px` transcript column.

Hovering or keyboard-focusing one item expands only that item into a dark,
rounded preview card approximately `320px` wide. The card contains:

- a one-line prompt title with ellipsis;
- a muted assistant context line when available; and
- a two-line assistant excerpt or in-progress fallback.

The card opens toward the transcript, uses a short opacity/translate
transition, and stays inside the chat viewport. A long rail can scroll within
its available height. `prefers-reduced-motion` disables the transition.

Each item has an accessible label containing the complete prompt. Clicking or
activating it with Enter/Space smoothly centers the matching `.turn`, marks it
active, and briefly highlights the destination turn. A scroll observer updates
the active marker as the reader moves through the transcript. Focus, Escape,
and pointer interactions must not interfere with the composer.

## Mobile composer

### Markup and behavior

The shared `chat_composer` macro keeps the existing textarea, upload input,
primary controls, and all current control classes/IDs. The existing secondary
`composer-actions` and `composer-meta` are grouped under a new mobile-more
wrapper with:

- a compact button with `aria-expanded`/`aria-controls`;
- a popover container; and
- the existing Files, Agents, Clear, Context, and status controls reused in
  place rather than duplicated.

At mobile widths, the always-visible row contains attachment, compact
permission mode, compact model/reasoning, the more button, and send/stop. The
more popover opens above the composer and contains the secondary controls as
touch-sized rows. It closes on selection, outside pointer, or Escape. The
implementation is per-composer so agent-studio composers do not depend on the
sessions page.

At desktop widths, the more button/popover is hidden and the existing full
toolbar arrangement remains visually available.

### Mobile layout

The sessions composer remains a bottom-floating dock, but mobile styles will:

- use full available width with horizontal insets and safe-area bottom
  padding;
- reduce the default input height and preserve auto-grow up to a bounded
  maximum;
- prevent the toolbar from wrapping or horizontally overflowing;
- hide the permission-mode suffix and tighten the reasoning pill at very
  narrow widths while preserving readable labels/tooltips; and
- keep the send/stop action in a fixed-size primary slot.

Menus and popovers must open within the viewport above the dock, not behind
the on-screen keyboard or outside the viewport at common widths around
`320px`, `375px`, and `430px`.

## Implementation boundaries

- `app/templates/partials/sessions/main.html`: add the query rail mount.
- `app/templates/partials/chat_composer.html`: add the mobile-more wrapper
  and button without removing existing hooks.
- `app/static/js/sessions.js`: render/reconcile rail items, derive previews,
  wire navigation and active state, and consume live user-turn events.
- `app/static/js/chat.js`: emit the live user-turn event and own the
  per-composer mobile-more toggle/close behavior.
- `app/static/css/tomo.css`: add query rail states and desktop breakpoint;
  add the mobile composer dock, toolbar, popover, and safe-area rules.

No Python, API, schema, or persisted-history changes are required.

## Accessibility and failure handling

- Rail items are real buttons with complete prompt labels and visible focus
  states.
- The mobile-more button exposes expanded/collapsed state and the popover is
  dismissible with Escape.
- Missing, empty, malformed, or still-streaming assistant content never
  prevents the user prompt or navigation item from rendering.
- New query items are best-effort UI updates; history reload remains the
  reconciliation path if a live event is missed.
- Reduced-motion users receive instant scroll/state transitions where
  possible.

## Verification

Manual browser verification will cover:

- one and many user queries, long prompts, attachments, and assistant turns
  with missing/streaming previews;
- rail hover, keyboard focus, click navigation, active marker updates, and
  post-refresh reconciliation;
- a newly sent message appearing immediately in the rail;
- mobile widths around `320px`, `375px`, `430px`, and `760px` with no
  horizontal overflow;
- mobile more-menu open/close, Escape/outside dismissal, and working
  attachment, mode, reasoning, context, Files, Agents, Clear, send, and stop
  controls; and
- light/dark theme and reduced-motion behavior.

Automated verification will run JavaScript syntax checks for modified files and
the existing Python/API test suite to confirm there are no regressions in chat
or session behavior.
