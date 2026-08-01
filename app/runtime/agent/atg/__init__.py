"""ATG (Atomic Task Graph) — training-free DAG planning/execution layer.

Adapted from the ATG paper (arXiv 2607.01942) for Tomo's async, string-tool
runtime. Public surface consumed by the agent loop:

* :func:`compile_task_graph` — LLM-driven recursive graph compilation.
* :func:`run_dag_execution`   — dependency-aware DAG execution (async).
* :func:`is_atg_eligible`     — explicit opt-in gate check.

Pure-logic graph model lives in :mod:`graph`; LLM prompts in
:mod:`prompts`; the async compiler in :mod:`compiler`; the async executor in
:mod:`executor`.

Default product path does **not** auto-run ATG. Session planning is
prompt-gated via the ``todo`` tool (model decides). Pass ``enable_atg=True``
to front-load a compiled DAG.
"""
from __future__ import annotations

from app.runtime.agent.atg.graph import (  # noqa: F401
    RefinementHistory,
    TaskDAG,
    TaskNode,
)


def run_dag_execution(*args, **kwargs):
    """Lazy import so merely gating on eligibility never loads the executor."""
    from app.runtime.agent.atg.executor import run_dag_execution as _run

    return _run(*args, **kwargs)


def compile_task_graph(*args, **kwargs):
    """Lazy import so merely gating on eligibility never loads the compiler."""
    from app.runtime.agent.atg.compiler import compile_task_graph as _compile

    return _compile(*args, **kwargs)


def is_atg_eligible(
    agent: dict | None = None,
    *,
    enable_atg: bool = False,
) -> bool:
    """True when ATG may run — explicit opt-in only.

    No word/length heuristic on the user message. Callers must pass
    ``enable_atg=True``. ``agent.enable_atg: false`` still opts out.
    """
    if not enable_atg:
        return False
    if agent is not None and agent.get("enable_atg") is False:
        return False
    return True


__all__ = [
    "TaskNode",
    "TaskDAG",
    "RefinementHistory",
    "run_dag_execution",
    "compile_task_graph",
    "is_atg_eligible",
]
