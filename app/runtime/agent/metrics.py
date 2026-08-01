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

    def mark_llm_round(self) -> None:
        self.llm_rounds += 1

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
        }

    def log_summary(self) -> None:
        d = self.as_dict()
        _logger.info(
            "turn metrics agent=%s session=%s elapsed_ms=%d rounds=%d "
            "tools=%d errors=%d delegates=%d parallel_peak=%d "
            "retries=%d atg=%s ended=%s",
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
        )


__all__ = ["TurnMetrics"]
