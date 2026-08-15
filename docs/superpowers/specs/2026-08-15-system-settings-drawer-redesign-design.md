# System Settings CRUD Redesign — Slide-Over Drawers

## Context

`/system` (`app/templates/system.html`) is a single hash-routed page with 8
sections (General, Models, Memory, Tools, MCP, Modules, Channels, Accounts)
switched by `app/static/js/system.js` + `partials/settings/nav.html`. Five of
those sections are list+create+edit+delete CRUD UIs built on an inline
"form card" pattern: an `#xFormCard` with `class="hidden"` toggled visible in
place, pushing the list down. Destructive actions use the browser's native
`confirm()`. Loading state is a static "Loading…" string. Empty state is a
plain text line. There is no dirty-state warning and no inline field
validation — errors only surface via toast after submit.

The existing design system (`app/static/css/tomo.css`) already has the
primitives this redesign builds on: `.card`, `.modal` (centered dialog),
`.toast` (via `Tomo.toast()`), and an unused `.skeleton` shimmer class.

Scope is deliberately narrowed to `/system` only — not a whole-app rewrite,
not a framework migration. Stack stays FastAPI + Jinja + vanilla JS/CSS.
General settings, Telegram (shared_channel), Tools, and Modules stay
untouched: General/Telegram are single persistent-save forms with no
list/create/delete pattern, and Tools/Modules are read-only listings.

## Goals

Bring the 5 CRUD sections up to modern ergonomics within the current stack:

1. **LLM Profiles** (`partials/settings/models.html`, `system.js`)
2. **Knowledge entries** (`partials/settings/memory.html`, `system.js`)
3. **MCP servers** (`partials/settings/mcp.html`, `mcp.js`)
4. **Accounts** (`partials/settings/users.html`, `system.js`)
5. **API keys** (`partials/settings/users.html`, `system.js`)

## Non-goals

- No backend/API/DB changes. Pure frontend.
- No soft-delete or undo-on-delete. All settings deletes stay hard-delete,
  confirmed via modal, no recovery window. (Confirmed with user: matches
  current backend reality, avoids new backend work for a settings page
  where delete is already rare and deliberate.)
- No changes to General, Telegram, Tools, or Modules sections.
- No global-shell changes (sidebar, breadcrumbs, command palette) — those
  don't exist yet app-wide and are out of scope for a single-page redesign.

## Design

### 1. Shared drawer component

New CSS in `tomo.css`: `.drawer`, `.drawer-backdrop`, `.drawer-head`,
`.drawer-body`, `.drawer-foot`. Slides from the right edge, `position:fixed`,
480px wide, scrollable body, sticky footer with Cancel/Save. Same visual
language as `.modal` (reuses `--surface`, `--border-strong`, `--shadow-2`)
but anchored right instead of centered, for a less disruptive add/edit flow
than a full-screen-feeling centered modal.

New JS in `tomo.js`:

- `Tomo.openDrawer(id, { mode, data })` — shows `#<id>Drawer`, populates
  fields from `data` when `mode === 'edit'`, snapshots initial field values
  for dirty tracking, traps focus, binds Esc.
- `Tomo.closeDrawer(id, { force })` — if dirty and not `force`, shows an
  inline "Discard changes?" confirm before closing; otherwise closes
  immediately.
- `Tomo.confirmModal({ title, body, danger, typedWord })` → `Promise<bool>`.
  Replaces all `confirm()` calls. Two flavors:
  - **Low-risk** (LLM profile, MCP server, knowledge entry): Cancel / Delete
    buttons, red Delete.
  - **High-risk** (Account, API key — access-affecting, irreversible): same
    modal plus a text input that must exactly match the literal word
    `DELETE` before the Delete button enables.
- `Tomo.skeletonRows(container, n)` — renders `n` shimmer rows shaped like a
  real `.row` list item, used while a list is loading.

Each entity's existing `#xFormCard` markup (the `<form>` and its fields)
moves as-is into `#xDrawer` — field markup, ids, and validation attributes
are unchanged, only the open/close mechanism and container change.

### 2. States

- **Loading:** `Tomo.skeletonRows()` (3 rows) replaces the static
  "Loading…" text for Profiles, Knowledge, MCP servers, Accounts, API keys.
- **Empty:** each list's empty branch gets a small muted inline SVG icon +
  one-line explainer + a primary CTA button that calls the same
  `openDrawer('x', { mode: 'add' })` as the section's "+ Add" button.
- **Error:** if the initial `Tomo.api()` fetch for a list rejects, render an
  inline row with the error message and a "Retry" button that re-invokes the
  same load function. Replaces the current behavior of failing silently into
  an empty-looking list.
- **Dirty state:** drawer diffs current field values against the snapshot
  taken on open. Save button shows a small dot indicator while dirty.
  Closing (Cancel, Esc, backdrop click) while dirty routes through the
  discard-confirm above instead of closing silently.
- **Inline validation:** required fields (Name, Username, Password, Title,
  etc.) validate on blur — red border + short error text under the field.
  Toast remains the fallback channel for server-side/duplicate-name style
  errors returned on submit.
- **Delete flow:** `Tomo.confirmModal(...)` → on confirm, call the existing
  DELETE endpoint, remove the row from the list, toast "Deleted". No undo.

### 3. Files touched

- `app/static/css/tomo.css` — drawer component, skeleton-row variant,
  empty-state-icon layout, field-error styles.
- `app/static/js/tomo.js` — `openDrawer` / `closeDrawer` / `confirmModal` /
  `skeletonRows` additions to the `Tomo` global.
- `app/static/js/system.js` — Profiles, Knowledge, Accounts, API keys: swap
  form-card toggling for drawer calls, swap `confirm()` for
  `Tomo.confirmModal`, add dirty tracking + blur validation, add
  error/retry branch to each `loadX()`.
- `app/static/js/mcp.js` — same swap for the MCP server form; the
  capabilities (Tools/Resources/Prompts) discovery panel stays inside the
  drawer body, scrolling with it.
- `app/templates/partials/settings/models.html`, `users.html`, `mcp.html`,
  `memory.html` — `<div class="card hidden" id="xFormCard">` becomes
  `<div class="drawer" id="xDrawer">` with the same fields inside.

No changes to `app/templates/system.html`, `nav.html`,
`general.html`, `shared_channel.html`, `tools.html`, or `modules.html`.

## Testing

No JS test suite exists in this repo today. Verification is manual, per
entity, after implementation: create, edit, delete (with and without
confirming), cancel-with-dirty-changes, and simulated load-error/retry — run
through the browser via the `run` skill.
