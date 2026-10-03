# Architecture

- **Surface vs runtime** — `app/api` and `app/web` stay thin; agent logic lives in `app/runtime`.
- **Schemas vs models** — Pydantic at the edge; SQL mixins in `app/models` for persistence.
- **Extensions** — `app/extensions` loads skills; `app/plugins` loads live Python plugins. Bundled Token Monitor and Task Board use the same SDK as user plugins.
- **Tools in two places** — JSON contracts in `app/tools/`; Python backends in `app/runtime/tools/`.
- **Agent harness** — `run_turn` is the execution engine: permission-gated tools (HITL/smart/off), parallel read-only tool batches, parallel `delegate` fan-out, loop detection, context compression, LLM retry on transient failures, force-final on max iterations, prompt-gated `todo` planning (optional ATG via `enable_atg=True` only), and active learning (`manage_skill` + post-turn review when Settings → Learning loop is on). Each review appends a `learning_events` row for the **Companion** page (bond, growth log, diary). Metrics are attached to the final event. See `docs/harness-improvement-report.md` and `docs/superpowers/specs/2026-08-04-companion-active-learning-design.md`.
- **Skill curation and usage** — With Learning loop on, eligible skill reviews also consolidate overlapping local-library skills. The reviewer must read both packages before `manage_skill action=merge`; the target receives distilled instructions and non-conflicting support files, agent assignments migrate, and the source is soft-archived (not deleted). Unassigned library skills inactive for 90 days (since creation, load, edit or restore) are automatically soft-archived during those reviews; internal/external skills and assigned skills are protected from inactivity pruning. Restore from the skill detail page retains the original package and usage history. `use_count` / `last_used_at` track successful foreground body loads and explicit `/skill` activations at message ingress, not curator reads, pagination, support-file reads or history replay. Counts are shared library totals, surfaced on Skills and as the top five enabled skills in Companion; they are not a usefulness score. Existing historical counts are retained.
- **Foundation thin vertical (live)** — SQLite store → LLM → tools (bash/files/web/memory) → coordinator turn loop → web chat SSE. Spec: `docs/superpowers/specs/2026-07-26-foundation-thin-vertical-design.md`.
- **Alpha kitchen-sink (complete)** — slices 0→H on top of foundation:

```text
Web / Telegram
    ↓
app/api + app/web          (thin)
    ↓
app/services/store         (facade → SQLite mixins)
    ↓
app/channels/*  →  app/runtime/agent/loop  →  coordinator (delegate / @mention)
    ↓
permissions gate + HITL   (assess → mode → allowlist → smart/HITL)
    ↓
LLM profiles  +  tools (bash/file/recall/…)  +  workplaces (local/SSH)
    ↓
$TOMO_HOME/state/tomo.db   (+ gated platform_data only for unused eval tiles)
```

  Progress: `docs/superpowers/progress/alpha.md`. Master spec: `docs/superpowers/specs/2026-07-26-alpha-kitchen-sink-design.md`.

- **Image input routing** — `app/runtime/llm/vision.py` resolves per-model vision capability (settings `model_capability_overrides` → Ollama `/api/show` probe for local endpoints → models.dev catalog cached in `$TOMO_HOME/state/cache` → static prefix table on vendor-stripped ids). `image_input_mode` (`auto`/`native`/`text`) picks native `image_url` parts vs. describing images into text through the auxiliary vision profile (`vision_profile_id` → capable main profile → first capable enabled profile). Descriptions are disk-cached by content hash (`app/services/vision.py`); the `vision_analyze` tool (`source`: workplace path / `attachment:<id>` / URL / data URL) gives agents on-demand image reads. Codex/Responses clients translate image parts to `input_image` (`codex_responses.py`).
- **Eval / evaluator deferred** — nav + `/evaluate` + `/history` + `/api/eval/*` are off by default (`TOMO_EVAL_UI=1` to re-enable). Seed/stubs remain.
- **UI honesty** — primary nav and System settings only expose wired Alpha surfaces. Stub panels (Safety, Users, Logs, Upload skill) are hidden; plugin UIs that are planned say so on-page.
