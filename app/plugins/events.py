"""Events exposed to live plugins."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TurnEndContext:
    session_id: str
    agent_id: str
    message: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
