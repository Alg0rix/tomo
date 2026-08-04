"""Token Monitor module definition (metadata + hooks).

HTTP wiring lives in :mod:`modules.token_monitor.routes`.
"""

from __future__ import annotations

from typing import Any

from modules.base import ModuleMeta, TurnEndContext

META = ModuleMeta(
    id="token_monitor",
    name="Token Monitor",
    description=(
        "Usage heatmap (turns like commits), agent/session leaderboards, "
        "and recent activity"
    ),
    version="0.3",
    has_ui=True,
    ui_path="/usage",
    nav_label="Usage",
    default_enabled=True,
)


class TokenMonitorModule:
    meta = META

    def on_turn_end(self, ctx: TurnEndContext, conn: Any = None) -> None:
        if conn is None:
            return
        from modules.token_monitor import ledger

        # Prefer real cumulative in/out from the agent loop (all LLM rounds
        # this turn, including nested subagents). Fall back to a rough
        # estimate of the user message only when the runtime reported nothing.
        prompt = int(ctx.prompt_tokens or 0)
        completion = int(ctx.completion_tokens or 0)
        if prompt <= 0 and completion <= 0 and (ctx.message or "").strip():
            try:
                from app.runtime.agent.context_usage import estimate_tokens

                prompt = estimate_tokens((ctx.message or "").strip())
            except Exception:
                prompt = 0

        ledger.record_event(
            conn,
            session_id=ctx.session_id,
            agent_id=ctx.agent_id,
            turns=1,
            prompt_tokens=prompt,
            completion_tokens=completion,
            message_preview=ctx.message,
        )

    def register_routes(self, api_router: Any) -> None:
        from .routes import register_api

        register_api(api_router)

    def register_pages(self, web_router: Any) -> None:
        from .routes import register_pages

        register_pages(web_router)


module = TokenMonitorModule()
