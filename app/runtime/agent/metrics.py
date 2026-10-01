"""Lightweight per-turn metrics for observability and benchmarks."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass
class TurnMetrics:
    """Accumulators for one ``run_turn`` invocation."""

    agent_id: str | None = None
    session_id: str | None = None
    started_at: float = field(default_factory=time.time)
    llm_rounds: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    delegates: int = 0
    parallel_tool_batches: int = 0
    parallel_tool_peak: int = 0
    llm_retries: int = 0
    atg_used: bool = False
    atg_status: str | None = None
    compressed: bool = False
    force_final: bool = False
    ended_kind: str | None = None  # final | error
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_prompt_tokens: int = 0
    cache_reported_rounds: int = 0
    reasoning_tokens: int = 0
    reasoning_reported_rounds: int = 0
    estimated_rounds: int = 0

    def mark_llm_round(self) -> None:
        self.llm_rounds += 1

    def add_usage(
        self, prompt_tokens: int = 0, completion_tokens: int = 0, *,
        cached_tokens: int | None = None, reasoning_tokens: int | None = None,
        estimated: bool = False,
    ) -> None:
        """Accumulate provider (or estimated) tokens from one LLM round."""
        self.prompt_tokens += max(0, int(prompt_tokens or 0))
        self.completion_tokens += max(0, int(completion_tokens or 0))
        self.estimated_rounds += int(estimated)
        if cached_tokens is not None and not estimated and prompt_tokens > 0:
            self.cached_tokens += min(prompt_tokens, max(0, int(cached_tokens)))
            self.cache_prompt_tokens += prompt_tokens
            self.cache_reported_rounds += 1
        if reasoning_tokens is not None and not estimated:
            self.reasoning_tokens += max(0, int(reasoning_tokens))
            self.reasoning_reported_rounds += 1

    def mark_tools(self, n: int, *, errors: int = 0, parallel: int = 0) -> None:
        self.tool_calls += n
        self.tool_errors += errors
        if parallel > 1:
            self.parallel_tool_batches += 1
            self.parallel_tool_peak = max(self.parallel_tool_peak, parallel)

    def elapsed_ms(self) -> int:
        return int((time.time() - self.started_at) * 1000)

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id or "",
            "session_id": self.session_id or "",
            "elapsed_ms": self.elapsed_ms(),
            "llm_rounds": self.llm_rounds,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "delegates": self.delegates,
            "parallel_tool_batches": self.parallel_tool_batches,
            "parallel_tool_peak": self.parallel_tool_peak,
            "llm_retries": self.llm_retries,
            "atg_used": self.atg_used,
            "atg_status": self.atg_status,
            "compressed": self.compressed,
            "force_final": self.force_final,
            "ended_kind": self.ended_kind,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens": self.prompt_tokens + self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_prompt_tokens": self.cache_prompt_tokens,
            "cache_reported_rounds": self.cache_reported_rounds,
            "reasoning_tokens": self.reasoning_tokens,
            "reasoning_reported_rounds": self.reasoning_reported_rounds,
            "estimated_rounds": self.estimated_rounds,
        }

    def log_summary(self) -> None:
        d = self.as_dict()
        _logger.info(
            "turn metrics agent=%s session=%s elapsed_ms=%d rounds=%d "
            "tools=%d errors=%d delegates=%d parallel_peak=%d "
            "retries=%d atg=%s ended=%s prompt_tok=%d completion_tok=%d",
            d["agent_id"],
            d["session_id"],
            d["elapsed_ms"],
            d["llm_rounds"],
            d["tool_calls"],
            d["tool_errors"],
            d["delegates"],
            d["parallel_tool_peak"],
            d["llm_retries"],
            d["atg_status"] or ("on" if d["atg_used"] else "off"),
            d["ended_kind"],
            d["prompt_tokens"],
            d["completion_tokens"],
        )


__all__ = ["TurnMetrics"]


def session_usage(history: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Summarize recorded turns, including delegates, from durable chat history.

    Cache rate uses only input tokens whose provider reported cache details.
    Older history and providers that omit usage cannot supply a cache rate.
    """
    keys = (
        "prompt_tokens", "completion_tokens", "cached_tokens", "cache_prompt_tokens",
        "cache_reported_rounds", "reasoning_tokens", "reasoning_reported_rounds",
        "estimated_rounds", "llm_rounds", "tool_calls",
    )
    totals = dict.fromkeys(keys, 0)
    recorded = 0
    last_elapsed_ms = None
    for entry in history or []:
        if entry.get("type") not in {"final", "subagent_final", "coordination_metrics"}:
            continue
        metrics = entry.get("metrics")
        if not isinstance(metrics, dict):
            continue
        recorded += 1
        for key in keys:
            totals[key] += max(0, int(metrics.get(key) or 0))
        if entry.get("type") == "final":
            last_elapsed_ms = max(0, int(metrics.get("elapsed_ms") or 0))
    denominator = totals["cache_prompt_tokens"]
    return {
        **totals,
        "recorded_turns": recorded,
        "last_elapsed_ms": last_elapsed_ms,
        "cache_hit_rate": round(100 * totals["cached_tokens"] / denominator, 1) if denominator else None,
    }
