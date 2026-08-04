"""Harbor BaseAgent that runs Tomo's real prompt + ``run_turn`` loop.

Uses ``build_system_prompt`` (SOUL.md / agent SYSTEM.md from ``$TOMO_HOME``)
and ``app.runtime.agent.loop.run_turn``. Coding tools are remapped into the
Terminal-Bench container via :mod:`benchmarks.terminal_bench.harbor_tools`.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from benchmarks.terminal_bench.harbor_tools import (
    TB_TOOL_NAMES,
    bind_harbor_tools,
    commands_executed,
)


def _seed_trial_home(home: Path, agent_id: str) -> None:
    """Ensure SOUL.md + agent SYSTEM.md exist under ``home`` (from defaults)."""
    from app.core import config

    defaults = config.REPO_ROOT / "defaults"
    home.mkdir(parents=True, exist_ok=True)
    soul = home / "SOUL.md"
    if not soul.exists():
        src = defaults / "SOUL.md"
        if src.is_file():
            shutil.copy2(src, soul)
        else:
            soul.write_text(
                "You are Tomo, a helpful agent. Use tools when they help.\n",
                encoding="utf-8",
            )
    agent_dir = home / "agents" / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    system = agent_dir / "SYSTEM.md"
    if not system.exists():
        src = defaults / "agents" / agent_id / "SYSTEM.md"
        if src.is_file():
            shutil.copy2(src, system)
        else:
            # Fall back to coordinator prompt file as base instructions.
            coord = defaults / "coordinator_system.md"
            if coord.is_file():
                shutil.copy2(coord, system)


class TomoHarborAgent(BaseAgent):
    """Drive Terminal-Bench tasks with Tomo's real identity + agent loop."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        agent_id: str = "coder",
        max_iterations: int = 40,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        use_live_home: bool = True,
        *args: Any,
        **kwargs: Any,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, *args, **kwargs)
        self._agent_id = (agent_id or "coder").strip() or "coder"
        self._max_iterations = max(1, int(max_iterations))
        self._base_url = (
            (base_url or os.environ.get("OPENAI_BASE_URL") or "").strip() or None
        )
        self._api_key_env = api_key_env
        self._use_live_home = bool(use_live_home)
        self._n_input_tokens = 0
        self._n_output_tokens = 0

    @staticmethod
    def name() -> str:
        return "tomo"

    def version(self) -> str | None:
        return "0.2.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        return

    def _resolve_home(self) -> Path:
        from app.core import config, home

        if self._use_live_home:
            # Prefer the running install's $TOMO_HOME (real SOUL / SYSTEM).
            live = config.TOMO_HOME
            if (live / "SOUL.md").is_file() or (live / "agents").is_dir():
                home.ensure_tomo_home(live)
                _seed_trial_home(live, self._agent_id)
                return live
        trial_home = self.logs_dir / "tomo_home"
        _seed_trial_home(trial_home, self._agent_id)
        return trial_home

    def _llm(self):
        from app.runtime.llm.openai_compat import OpenAICompatClient

        api_key = os.environ.get(self._api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(
                f"Set {self._api_key_env} (or --ak api_key_env=...) before running"
            )
        model = self.model_name or "gpt-4.1"
        if "/" in model:
            model = model.split("/", 1)[1]
        return OpenAICompatClient(
            base_url=self._base_url,
            api_key=api_key,
            model=model,
        )

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        from app.runtime.agent.context import build_system_prompt
        from app.runtime.agent.loop import run_turn
        from app.runtime.tools.registry import get_openai_tools

        home_root = self._resolve_home()
        system_prompt = build_system_prompt(self._agent_id, home_root=home_root)
        tools = get_openai_tools(enabled=TB_TOOL_NAMES)
        llm = self._llm()
        transcript: list[dict[str, Any]] = [
            {
                "home_root": str(home_root),
                "agent_id": self._agent_id,
                "system_prompt_chars": len(system_prompt),
            }
        ]
        meta: dict[str, Any] = {
            "agent_id": self._agent_id,
            "home_root": str(home_root),
        }
        exit_code: int | None = None
        error_message: str | None = None

        try:
            with bind_harbor_tools(environment):
                async for ev in run_turn(
                    instruction,
                    history=None,
                    llm=llm,
                    tools=tools,
                    system_prompt=system_prompt,
                    agent_id=self._agent_id,
                    max_iterations=self._max_iterations,
                    enable_atg=False,
                ):
                    kind = ev.get("kind")
                    transcript.append(ev)
                    if kind == "error":
                        exit_code = 1
                        error_message = str(ev.get("message") or "error")
                    elif kind == "final":
                        exit_code = 0
                        metrics = ev.get("metrics")
                        if isinstance(metrics, dict):
                            meta["turn_metrics"] = metrics
                            self._n_input_tokens = int(
                                metrics.get("prompt_tokens") or 0
                            )
                            self._n_output_tokens = int(
                                metrics.get("completion_tokens") or 0
                            )
                    elif kind == "metrics":
                        meta["turn_metrics"] = ev
            if exit_code is None:
                exit_code = 0
        except Exception as e:
            exit_code = 1
            error_message = str(e)
            self.logger.exception("TomoHarborAgent failed")
            raise
        finally:
            meta["commands_executed"] = commands_executed()
            meta["exit_code"] = exit_code if exit_code is not None else 0
            if error_message:
                meta["error_message"] = error_message
            # Harbor AgentContext only exposes tokens/cost/metadata — stash
            # Tomo-specific outcome fields under metadata.
            context.metadata = meta
            context.n_input_tokens = self._n_input_tokens or None
            context.n_output_tokens = self._n_output_tokens or None
            (self.logs_dir / "tomo-transcript.json").write_text(
                json.dumps(transcript, indent=2, default=str),
                encoding="utf-8",
            )
            (self.logs_dir / "tomo-system-prompt.md").write_text(
                system_prompt, encoding="utf-8"
            )
            (self.logs_dir / "tomo-meta.json").write_text(
                json.dumps(meta, indent=2, default=str),
                encoding="utf-8",
            )
