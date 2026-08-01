"""Microbenchmarks for harness scheduling (no live LLM).

Run:
  uv run python -m benchmarks.harness_microbench
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.runtime.agent.loop import run_turn
from app.runtime.llm.base import LLMResponse, ToolCall
from app.runtime.permissions.gate import Decision
from tests.fakes.llm import ScriptedLLM, text_reply


def _ro_tools() -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "recall"}},
    ]


async def _bench_parallel_readonly(n_tools: int = 6, delay_s: float = 0.05) -> dict:
    """Compare wall time of N read-only tools with artificial latency."""

    def _exec(name, args):
        time.sleep(delay_s)
        return f"ok:{args.get('path') or name}"

    # Patch at module level for this process.
    import app.runtime.agent.loop as loop_mod

    orig_execute = loop_mod.execute
    orig_evaluate = loop_mod.evaluate
    loop_mod.execute = _exec  # type: ignore[assignment]
    loop_mod.evaluate = lambda *a, **k: Decision(allowed=True)  # type: ignore[assignment]
    try:
        calls = [
            ToolCall(
                id=f"c{i}",
                name="read_file",
                arguments={"path": f"f{i}.py"},
            )
            for i in range(n_tools)
        ]
        llm = ScriptedLLM(
            [LLMResponse(content=None, tool_calls=calls), text_reply("done")]
        )
        t0 = time.perf_counter()
        events = [
            ev
            async for ev in run_turn(
                "read many files in parallel please implement",
                llm=llm,
                tools=_ro_tools(),
                enable_atg=False,
            )
        ]
        elapsed = time.perf_counter() - t0
        metrics = next(e for e in events if e["kind"] == "final").get("metrics") or {}
        serial_estimate = n_tools * delay_s
        return {
            "n_tools": n_tools,
            "delay_s": delay_s,
            "elapsed_s": round(elapsed, 3),
            "serial_estimate_s": round(serial_estimate, 3),
            "speedup": round(serial_estimate / elapsed, 2) if elapsed else None,
            "parallel_tool_peak": metrics.get("parallel_tool_peak"),
        }
    finally:
        loop_mod.execute = orig_execute  # type: ignore[assignment]
        loop_mod.evaluate = orig_evaluate  # type: ignore[assignment]


async def main() -> None:
    result = await _bench_parallel_readonly()
    print("parallel_readonly_tools:", result)
    assert result["elapsed_s"] < result["serial_estimate_s"] * 0.7, result
    print("PASS: parallel RO tools beat serial estimate")


if __name__ == "__main__":
    asyncio.run(main())
