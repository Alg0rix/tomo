# Tomo Agent Harness — Improvement Report

**Date:** 2026-07-31 
**Scope:** Continuous improvement of the autonomous agent harness (`app/runtime/agent/*`, ATG, prompts, Terminal-Bench adapter, observability).

---

## 1. Executive summary

Permissions were recently production-wired into `run_turn`, but that change **serialized all tool execution** and left several autonomy features half-finished (ATG dark, no retries, max-iter hard error, no context compression, broken Harbor `AgentContext` fields).

This pass restores and extends harness capability while preserving the new permission/HITL model:

- Parallel read-only tools after gating (measured **~4.5×** wall-time speedup on a 6-tool synthetic batch).
- Parallel multi-`delegate` fan-out (events buffered per child, emitted in call order).
- Transient LLM retry, force-final on max iterations, working-memory compression.
- ATG contract fixes (honest `result`-only interfaces, valid schema example, refine-continue, node retries, parallel gather hardening).
- Prompt-gated `todo` checklist (ATG opt-in via `enable_atg=True` only); Terminal-Bench adapter fixed for Harbor `AgentContext`.
- Turn metrics on `final` events; microbench + unit tests.

**Validation:** `86` agent/permissions unit tests green; microbench PASS (`speedup=4.49`).

---

## 2. Architecture before and after

### Before (post-permissions, pre-this-pass)

```text
run_turn
 ├─ optional ATG (unreachable: enable_atg never set)
 ├─ LLM round (no app-level retry)
 ├─ tools: ALL serial via _run_one_gated_tool ← regression vs earlier gather
 ├─ delegates: serial (parallel_* UI fields only)
 └─ max iter → hard error
```

### After

```text
run_turn
 ├─ prompt-gated todo (model decides); ATG only if enable_atg=True
 ├─ optional ATG compile/execute (fixed contracts + node retry)
 ├─ LLM round with transient retry
 ├─ maybe_compress_messages (soft token budget)
 ├─ authorize tools (HITL serial) → RO gather / mutating serial
 ├─ delegates: asyncio.gather when 2+
 ├─ max iter → force-final (no-tools) synthesis
 └─ final.metrics (TurnMetrics)
```

New modules: `tool_errors.py`, `retry.py`, `compress.py`, `metrics.py`.

---

## 3. Bottlenecks identified

| Bottleneck | Evidence | Impact |
|------------|----------|--------|
| Serial gated tool loop | `loop.py` after permissions commit; `_READ_ONLY_TOOLS` unused | Latency on multi-read rounds |
| Serial delegates | `parallel_index/total` cosmetic | Swarm throughput |
| No LLM retry at loop | single exception → turn death | Reliability under 429/5xx |
| Max-iter hard error | no synthesis chance | Lower completion rate |
| ATG unreachable + bad schema example / multi-key interfaces | compile validation failures; no product wiring | Dead autonomy path |
| Empty-stdout = ATG failure | executor `_is_error_result` | False fallbacks |
| Refine `break` on one failure | compiler queue abort | Weaker plans |
| Harbor `AgentContext` field mismatch | job exception.txt | All Tomo TB trials crash on completion |
| Unbounded history | `context_usage.summarized_conversation=0` | Token burn / context overflow |
| No turn metrics | qualitative logs only | Hard to optimize |

---

## 4. Improvements implemented

1. **Gated parallel RO tools** — authorize/HITL serially; execute auto-allowed read-only tools via `asyncio.gather`; mutating stays serial.
2. **Parallel delegates** — `asyncio.gather` of `_drain_delegate_bundle` when ≥2 delegates.
3. **LLM transient retry** — `_llm_round_with_retry` (one restart on timeout/429/5xx-class errors).
4. **Force-final** — after max iterations, one no-tools synthesis round instead of immediate error.
5. **Context compression** — `maybe_compress_messages` collapses older tool exchanges under a soft budget.
6. **Shared `tool_result_is_error`** — consistent Error:/BLOCKED/exit-code semantics; empty stdout OK.
7. **ATG** — `result`-only interfaces; fixed schema example; refine `continue`; node retries (`MAX_NODE_ATTEMPTS`); harden parallel gather exceptions; settings `enable_atg`.
8. **Eligibility** — `is_atg_eligible(enable_atg=…)` is explicit opt-in only (no goal heuristic).
9. **TurnMetrics** — attached to `final` events; structured log line.
10. **Terminal-Bench** — outcomes in `context.metadata` + `tomo-meta.json` (Harbor has no `exit_code` field).
11. **Prompts** — Coder batches RO reads; Coordinator notes real parallel delegate fan-out.
12. **Tests + microbench** — `test_harness_improvements.py`, updated max-iter tests, `benchmarks/harness_microbench.py`.

---

## 5. Benchmarks

### Microbench (no live LLM)

```text
parallel_readonly_tools: {
 n_tools: 6, delay_s: 0.05,
 elapsed_s: 0.067, serial_estimate_s: 0.3,
 speedup: 4.49, parallel_tool_peak: 6
}
```

Command: `uv run python -m benchmarks.harness_microbench`

### Terminal-Bench

Prior Tomo jobs failed on Harbor pydantic validation (`exit_code` / `error_message` / `commands_executed`). Adapter fixed; **re-run** `./benchmarks/terminal_bench/run.sh tomo …` for live accuracy (not executed in this pass — requires API key + Docker).

---

## 6. Reliability improvements

- Transient LLM failures no longer kill the turn on first blip.
- Max-iter turns can still produce a useful answer.
- Loop detection retained; force-final complements it.
- Permission HITL preserved (authorization remains serial/ordered).
- ATG false failures from empty stdout removed; node retries absorb flaky binds/tools.
- Harbor adapter no longer raises on completion.

---

## 7. Token and latency analysis

| Change | Latency | Tokens |
|--------|---------|--------|
| Parallel RO tools | ↓ wall time (I/O bound) | ≈ same |
| Parallel delegates | ↓ when N>1 independent | ≈ same (parallel cost) |
| Context compression | slight CPU | ↓ on long sessions |
| LLM retry | +0.75s on failure only | +1 failed round cost when needed |
| Force-final | +1 LLM call at cap | replaces abandoned work with answer |
| ATG (when enabled) | +compile/execute upfront | can reduce later thrash; skip trivial goals |

---

## 8. Cost analysis and optimization opportunities

- **ATG off by default** — planning via prompt-gated `todo` tool; ATG only with `enable_atg=True`.
- No word/length heuristic on user text.
- Compression reduces context tokens on long tool trails.
- Still missing: real `resp.usage` plumbing into `LLMResponse` / Harbor counters (tokens still 0 in TB adapter).
- Still missing: cost-aware model routing (cheap model for bind/classify, strong for hard edits).

---

## 9. Remaining limitations

- ATG subgraph **repair** (`REPAIR_USER` / `MAX_REPAIR_ATTEMPTS`) still not called — node retry only.
- ATG UI: `atg_wave` / `atg_summary` lightly surfaced in SSE.
- Knowledge `recall` remains keyword scan (no embeddings).
- `concurrency_limit` setting still unused for global turn throttling.
- Event bus still a stub; metrics are per-turn dicts, not a metrics backend.
- Permission smart-mode still spends an LLM call per flagged tool (by design).
- Parallel delegates buffer child events (UI gets them after each child finishes in gather order of completion, then parent emits in original order — streaming mid-child across siblings is not interleaved live).

---

## 10. Future roadmap (prioritized)

1. **P0 — Re-run Terminal-Bench** after adapter fix; capture accuracy/latency baseline.
2. **P0 — Plumb token usage** from OpenAI SDK into `LLMResponse` + Harbor metadata.
3. **P1 — ATG repair path** using `RefinementHistory.ancestor_chain` + `REPAIR_USER`.
4. **P1 — Optional per-agent ATG opt-out** (agent.enable_atg=false) when ATG is forced on.
5. **P1 — Semantic memory** (embeddings or cheaper lexical BM25 over knowledge + session).
6. **P2 — Honor `concurrency_limit`** for concurrent session turns / tool batches.
7. **P2 — Speculative execution** for read-only probes behind a feature flag.
8. **P2 — Checkpoint/resume** for long ATG DAGs (records already partially support seed outputs).
9. **P3 — Distributed workers** only if single-host concurrency saturates.

---

## How to enable / verify

```bash
# Unit tests
uv run pytest tests/unit/runtime/agent/ -q

# Parallel RO microbench
uv run python -m benchmarks.harness_microbench

# Opt into ATG (caller / test path only)
# run_turn(..., enable_atg=True)

# Terminal-Bench (Docker + API key)
./benchmarks/terminal_bench/run.sh tomo -m openai/gpt-4.1 -l 5
```


## ATG × Todo — 2026-08-01

- **Planning is prompt-gated** via the `todo` tool schema (model decides). No English keyword / length heuristic on the user message.
- **ATG is opt-in only** — pass `enable_atg=True` to front-load a compiled DAG; default product path leaves ATG off.
- When ATG runs, compiled nodes **seed the session todo checklist**; node progress merges status.
- `todo` tool uses replace/merge (`todos` / `merge`, statuses pending/in_progress/completed/cancelled).
- SSE `todos` event + live Todo panel in chat/inspector.
- Nested subagent turns do not run ATG.
