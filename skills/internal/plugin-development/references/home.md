# Home cards and starters

Home (`/`) offers widgets from enabled plugins in its 間 Rooms grid. A plugin
without a card gets a plain door card linking to its first page. Register a card
to show live, per-user status instead. Core renders the card from typed JSON;
plugins never inject HTML, CSS, or scripts into Home.

```python
def setup(api):
    def home_card(user_id):
        month = load_month(api.user_data_dir(user_id))
        if not month.txns:
            return {
                "empty": "No entries yet.",
                "actions": [{"label": "Log one", "prompt": "Help me log a purchase."}],
            }
        return {
            "status": {"text": "1 over budget", "tone": "hot"},
            "metric": {"value": "Rp1.2jt", "label": "spent this month"},
            "caption": "14 transactions · ~Rp40rb/day",
            "chart": [{"label": "1", "value": 120}, {"label": "2", "value": 80}],
            "ring": {"value": "3", "label": "categories", "segments": [
                {"label": "Food", "value": 44, "text": "44%"},
                {"label": "Transport", "value": 23, "text": "23%"},
            ]},
            "actions": [
                {"label": "Where did it go?", "prompt": "Break down my spending."},
                {"label": "Open", "href": api.base_url + "/"},
            ],
        }

    if hasattr(api, "home_card"):  # older Tomo has no Home SDK
        try:
            api.home_card(home_card, id="spending", title="Money", size="m", kanji="金")
        except (TypeError, ValueError):  # Tomo without size "l"/kanji support
            api.home_card(home_card, title="Money")
        api.starter("This month's spending", "How much did I spend this month?")
```

## Multiple widgets

Register each view independently with a stable ID. Keep IDs unchanged across
releases; never conditionally register cards based on one user's preferences.
Tomo saves dashboard choices per user, separately from plugin data.

```python
def setup(api):
    api.home_card(spending_card, id="spending", title="Spending", size="m")
    api.home_card(budget_card, id="budgets", title="Budgets", size="s",
                  default_visible=False)
```

`spending_card(user_id)` and `budget_card(user_id)` return their own typed data.
Users can keep Spending alone, add Budgets later, and remove/restore either
without disabling the plugin. `size` is only the initial size; the user's saved
width and height take precedence. Do not store dashboard preferences in the
plugin or inject your own drag/resize scripts.

## Registration limits

| Call | Contract |
| --- | --- |
| `api.home_card(handler, *, title=None, size="s", kanji=None, id=None, default_visible=None)` | Synchronous `handler(user_id)`; `size` is `s` (1 column), `m` (2 columns), or `l` (full row); `kanji` is one CJK character shown as the room icon (else the manifest Lucide icon); at most 12 cards; title ≤40 chars |
| `api.starter(label, prompt)` | Composer chip; label ≤60, prompt ≤500; at most 4 per plugin; clicking fills the composer, never sends |

The handler runs on every Home load, in parallel with other plugins, with the
user bound as the current user (so `api.user_data_dir()` works without an
argument). All handlers share a 2.5s deadline. A slow handler shows "Timed out";
an exception or a non-dict return shows an error card and is logged. Keep it a
cheap read: no network calls, no LLM calls, no writes. Always scope data to the
`user_id` argument.

## Card fields

Every field is optional. Unknown fields (including `html`) are dropped, text is
truncated, and numbers are clamped. Fields render in the order below. Pick 2–4
that answer "how is this going?" at a glance; a card is not a page.

`tone` is always one of `hot` (red), `warm` (amber accent), `ok` (green),
`info` (blue), or `""` (neutral). `who` is `{"name": "Kai", "agent": "kai"}`:
an avatar initial coloured like that agent elsewhere in Tomo; omit `agent` for
the user or a person (neutral grey).

| Field | Shape | Limits / rendering |
| --- | --- | --- |
| `status` | `{text, tone}` | Pill in the card header, e.g. "2 over budget"; ≤20 chars |
| `notice` | `{text, tone}` | Body banner for a problem or heads-up ("Gmail token expired"); ≤140 chars |
| `metric` | `{value, label, tone?}` | Big number; value ≤24 chars. Format it yourself (`"1.2M"`); `tone` colours the number by threshold |
| `caption` | string | ≤140 chars, line under the metric |
| `trend` | `{text, direction: "up"\|"down", good: bool}` | Prefixed to caption; `good` picks green vs red |
| `image` | `{src, alt?}` | Cover-fit image; `src` must be a local path (e.g. `api.base_url + "/static/…"`); remote URLs are dropped |
| `stats` | `[{label, value, trend?, tone?}]` | ≤4 KPI tiles in a row; `tone` colours the value |
| `chart` | `[{label, value, tone?}]` | Column chart, ≥2 and ≤31 points (last kept); last bar highlighted; labels thinned automatically |
| `spark` | `[number, …]` | Line sparkline, ≥2 points, last 30 kept |
| `series` | `{lines: [{label?, values: [number, …], tone?}], labels?}` | Multi-line chart, ≤4 lines × ≥2 points (last 30 kept); negatives allowed (a zero axis is drawn); `labels` shows first→last underneath |
| `ring` | `{value?, label?, segments: [{label, value, text?, tone?}]}` | Donut + legend, ≤6 segments, values >0 (proportions computed by core); `value`/`label` sit in the centre |
| `gauge` | `{value: 0–1, label?, text?, tone?}` | Semicircle dial; `text` is the centre readout (e.g. "72%"); `tone` colours the arc |
| `heatmap` | `{values: [number, …], label?}` | Activity grid, 7 rows, oldest first, ≤140 values (20 weeks); intensity relative to max |
| `bars` | `[{label, value: 0–1, text, tone}]` | ≤5 progress bars (budgets, quotas) |
| `steps` | `[{label, state: "done"\|"active"}]` | Pipeline stepper, ≤5 steps; no `state` = pending; `active` marks the current step |
| `states` | `[{tone, value?, text?}]` | Status-history band, ≤20 segments; width ∝ `value` (default 1, gaps are neutral); `text` is the hover tip |
| `columns` | `[{title, count, muted?, items: [{title, meta?, who?, tone?}]}]` | Kanban board, ≤4 columns × 3 items; `muted` greys a column (Done); item `tone` adds a left edge |
| `timeline` | `[{time, label, meta?, tone?, href?}]` | Agenda, ≤5 entries; `time` ≤12 chars ("09:30", "Fri") |
| `checklist` | `[{label, done}]` | ≤6 read-only rows; mutate through your tools/pages |
| `list` | `[{label, value?, sub?, who?, tone?, href?}]` | ≤6 rows; `sub` is a second line; `who` avatar or `tone` dot leads the row |
| `table` | `{columns: [label, …], rows: [[cell, …]]}` | Compact grid, ≤4 columns × 5 rows; short rows are padded, long ones truncated |
| `tags` | `[{label, tone?}]` | ≤8 chips |
| `quote` | string | ≤240 chars |
| `code` | string | Monospace block (last log line, command, hash); ≤6 lines and ≤400 chars |
| `meter` | `{value: 0–1, label, text}` | 10-cell progress meter |
| `foot` | string | Dim footnote at the bottom ("updated 10:32 · 3 sources"); ≤80 chars |
| `actions` | `[{label, prompt}\|{label, href}]` | ≤3 buttons; `prompt` prefills the Home composer, never sends |
| `empty` | string | ≤140; shown (with actions) when no other content field is set |

`href` and `image.src` must be local paths starting with `/` (not `//`);
anything else is removed. Use `api.base_url + "/page"` for plugin links and
`api.base_url + "/static/…"` for card images. Send raw numbers in `chart`,
`ring`, and `heatmap`; core scales them.

Good combinations:

- **Money**: `status` + `metric` + `chart` (daily spend) + `ring` (categories) + `bars` (budgets), size `m`; recent transactions fit `table`.
- **Task board**: `columns` with `who` and a muted Done column + a prompt action, size `m`.
- **Calendar**: `status` + `timeline` + `checklist`.
- **Usage**: `metric` + `stats` + `heatmap`.
- **Monitor**: `metric` with a threshold `tone` + `series` (multiple lines) + `gauge` + `states` (uptime band) — a mini dashboard.
- **Pipeline / sync job**: `notice` on failure + `steps` (fetch → parse → import) + `foot` with last-run time; `code` for the last log line.

## Verify

Reload the plugin, open `/`, and check the card in both the empty state and the
populated state, at desktop and 390px widths. Home → Add widget lets each user choose widgets separately, even when a plugin
provides several. Home → Arrange lets users move, remove, and resize widgets;
removed widgets can be restored from the picker, with their saved sizes.
Use stable `id` names (lowercase letters, digits, `_`, `-`, starting with a letter,
up to 64 characters); keys are `<plugin-id>:<id>`. Only the first named widget
is visible initially unless `default_visible` overrides it. Unnamed cards
remain visible by default and use `<plugin-id>:<index>`; keep their registration
order stable. Once users customize selection, new widgets are available in the
picker rather than automatically added. Desktop widths snap to small, medium,
or full row; heights snap to 20px steps (180–900px), or automatic. Mobile stacks
cards with automatic height and preserves desktop sizes. Test the handler directly with two
user IDs to prove isolation.
