# Architecture

- **Surface vs runtime** — `app/api` and `app/web` stay thin; agent logic lives in `app/runtime`.
- **Schemas vs models** — Pydantic at the edge; SQL mixins in `app/models` for persistence.
- **Extensions at the root** — `skills/` and `plugins/` are installable trees; `app/extensions` loads them.
- **Tools in two places** — JSON contracts in `app/tools/`; Python backends in `app/runtime/tools/`.
- **Agent harness** — `run_turn` is the execution engine: permission-gated tools (HITL/smart/off), parallel read-only tool batches, parallel `delegate` fan-out, loop detection, context compression, LLM retry on transient failures, force-final on max iterations, prompt-gated `todo` planning (optional ATG via `enable_atg=True` only), and active learning (`manage_skill` + post-turn review when Settings → Learning loop is on). Metrics are attached to the final event. See `docs/harness-improvement-report.md`.
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

- **Eval / evaluator deferred** — nav + `/evaluate` + `/history` + `/api/eval/*` are off by default (`TOMO_EVAL_UI=1` to re-enable). Seed/stubs remain.
- **UI honesty** — primary nav and System settings only expose wired Alpha surfaces. Stub panels (Safety, Users, Logs, Upload skill) are hidden; plugin UIs that are planned say so on-page.
