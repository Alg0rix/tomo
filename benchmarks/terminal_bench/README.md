# Terminal-Bench 2.1

Run [Terminal-Bench 2.1](https://www.tbench.ai/docs/run-terminal-bench-2-1) via [Harbor](https://www.harborframework.com/) using **Tomo’s real agent stack** — `SOUL.md` / agent `SYSTEM.md` via `build_system_prompt`, and `app.runtime.agent.loop.run_turn`. Coding tools are remapped into the task container.

## Prerequisites

1. Docker running (`docker info`)
2. Harbor CLI: `uv tool install harbor`
3. Provider API key (e.g. `OPENAI_API_KEY`)
4. Tomo checkout on `PYTHONPATH` (the `run.sh tomo` helper sets this)

## Quick commands

```bash
# Smoke test — oracle solutions on first 5 tasks (harness only, no Tomo/LLM)
./benchmarks/terminal_bench/run.sh smoke

# Tomo (real prompts + run_turn) on a few tasks
export OPENAI_API_KEY=...
./benchmarks/terminal_bench/run.sh tomo -m openai/gpt-4.1 -l 5

# Single task
./benchmarks/terminal_bench/run.sh tomo -m openai/gpt-4.1 -i write-compressor

# Optional: use a specific Tomo agent identity (default: coder)
./benchmarks/terminal_bench/run.sh tomo -m openai/gpt-4.1 -l 1 --ak agent_id=coder
```

Results land under `benchmarks/terminal_bench/jobs/`.

## What “tomo” means here

| Piece | Source |
|-------|--------|
| Global persona | `$TOMO_HOME/SOUL.md` (seeded from `defaults/SOUL.md` if missing) |
| Agent instructions | `$TOMO_HOME/agents/<id>/SYSTEM.md` (default agent: `coder`) |
| Turn loop | `app.runtime.agent.loop.run_turn` |
| LLM | Tomo `OpenAICompatClient` (same stack as the app) |
| bash / files | Harbor `environment.exec` (task container), not host `$TOMO_WORK` |

Pass `--ak use_live_home=false` to force a trial-local home under the job logs (still seeded from `defaults/`).

## Other agents

Harbor’s reference scaffold (not Tomo):

```bash
./benchmarks/terminal_bench/run.sh terminus -m openai/gpt-4.1 -l 5
```

## Leaderboard

See [harbor-framework/terminal-bench-2-1](https://github.com/harbor-framework/terminal-bench-2-1). Typical leaderboard runs use `-k 5`.
