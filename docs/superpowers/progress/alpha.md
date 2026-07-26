# Tomo Alpha — Progress Log

**Master spec:** `docs/superpowers/specs/2026-07-26-alpha-kitchen-sink-design.md` (accepted 2026-07-26)  
**Mode:** Cursor plans/reviews · Cline implements · serial slices 0→H · **autonomous B→H (no human go between slices)**

## Status

| Slice | Name | Spec/Plan | Cline | Review | State |
|-------|------|-----------|-------|--------|-------|
| 0 | Tomo Home (`$TOMO_HOME`) | [plan](../plans/2026-07-26-alpha-slice-0-tomo-home.md) · [brief](../handoffs/alpha-slice-0-cline-brief.md) | done | **PASS** | **done** |
| A | Agent identity + multi-model + UI honesty | [plan](../plans/2026-07-26-alpha-slice-a-identity-models.md) · [brief](../handoffs/alpha-slice-a-cline-brief.md) | done | **PASS** | **done** |
| B | Swarm delegation | [plan](../plans/2026-07-26-alpha-slice-b-delegation.md) · [brief](../handoffs/alpha-slice-b-cline-brief.md) | done | — | **done** |
| C | More tools | [plan](../plans/2026-07-26-alpha-slice-c-tools.md) · [brief](../handoffs/alpha-slice-c-cline-brief.md) | done | — | **done** |
| D | Workplaces local/SSH | — | — | — | — |
| E | Memory / KB | — | — | — | — |
| F | Extra channels (Telegram+) | — | — | — | — |
| G | Platform → SQLite + scheduler | — | — | — | — |
| H | Alpha polish | — | — | — | — |

## Log

- 2026-07-26: Master kitchen-sink Alpha spec drafted (approach B).  
- 2026-07-26: Tomo Home §2.1 + Slice 0; `SOUL.md`/`SYSTEM.md` allowed; secrets = SQLite + optional `.env` (no `secrets.env`).  
- 2026-07-26: Spec **accepted**. Slice 0 plan + Cline brief written — ready for Cline.  
- 2026-07-26: Secrets policy updated — master key (`TOMO_SECRET_KEY` / `$TOMO_HOME/.secret_key`); SQLite secret fields encrypted at rest (Slice 0).  
- 2026-07-26: §2.2 multi-model profiles — user configures catalog + default + per-agent (Slice A); Alpha-fresh, no upgrade path required in docs.  
- 2026-07-26: Slice 0 **dispatched to Cline** via CLI (`cline -c …` + `alpha-slice-0-cline-brief.md`).
- 2026-07-26: Slice 0 **implemented** by Cline — `$TOMO_HOME` tree + `ensure_tomo_home`; `defaults/SOUL.md` + `defaults/tomo.yaml`; `app/core/secrets.py` (Fernet at-rest encryption, `enc:v1:` prefix); `llm_api_key` ciphertext in SQLite + decrypted in-memory `get_settings`; `build_system_prompt` (SOUL/SYSTEM from home wired into the loop); optional `.env` loader (`override=False`); `TOMO_SESSION_SECRET` split from `TOMO_SECRET_KEY` (master key). Full suite green (197). Pending Cursor review.  
- 2026-07-26: Slice 0 **Cursor review PASS** — commit `ad8827f`; encryption + `.secret_key` 0600 + SOUL/SYSTEM loading verified. Ready for Slice A when you say go.  
- 2026-07-26: Slice A plan + brief written; **dispatched to Cline** (`alpha-slice-a-cline-brief.md`).
- 2026-07-26: Slice A **implemented** by Cline — `llm_profiles` table (encrypted `api_key` via Slice 0 Fernet, masked public GET, blank-PUT-keeps-key); `default_model_id` setting; agents gain `role` + `model_id` = profile id (empty = default); `get_llm(agent_id=None)` resolves agent → default → first enabled → `LLMConfigError`; loop + session title wired to per-agent resolution; System → Models multi-profile CRUD UI + setup creates first default profile; agent create/config UI with Name/Role/model dropdown (persists via PUT `/api/agents/{id}`); dashboard/sessions copy honest (no false routing/handoff); Tools/Skills/Channels panels labeled "Not wired yet". New `/api/llm-profiles` CRUD + set-default routes. Full suite green (222).  
- 2026-07-26: Slice A **Cursor review PASS** — commit `44dc606`; profiles + role + `get_llm(agent_id)` + honest chat copy verified. Ready for Slice B when you say go.  
- 2026-07-26: Human: **autonomous B→H** (no go between slices). Slice B plan + brief written; **dispatched to Cline**.  
- 2026-07-26: Slice B ClinePass hit monthly limit — **redispatched** with `-P openai-compatible -m kimi-for-coding`. Plans/briefs for C–H pre-written.  
- 2026-07-26: All Cline providers fail membership/limit — **switching implementer to Cursor** for autonomous B→H (briefs still apply).
- 2026-07-26: Slice B **implemented** (Cursor) — `resolve_target` / `parse_leading_mention` in `coordinator/router.py`; `delegate` tool + registry; loop yields `delegate` and stops on success; web channel emits SSE `delegate` then nested member `run_turn` for tool handoff and `@mention` force-handoff (non-members rejected / fall through); history rows stamped with correct `agent_id`; chat/dashboard copy restored for routing. Pending Cursor review.
- 2026-07-26: Slice C **implemented** (Cursor) — `bash` / `read_file` / `write_file` JSON defs + backends with `$TOMO_HOME/agents/<id>/work` cwd jail + timeouts; System → Tools from registry; SQLite `agent_tools` per-agent enablement; Tools panel Save wired; calculator + delegate kept green.
