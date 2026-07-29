"""LLM-assisted agent draft generation for the New agent dialog."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.models.ids import slugify
from app.runtime.llm.base import LLMClient

logger = logging.getLogger(__name__)

_BRIEF_MAX = 2000
_NAME_MAX = 80
_ROLE_MAX = 80
_DESC_MAX = 500

_SYSTEM = (
    "You design specialized AI agents for a multi-agent swarm platform. "
    "Given a short brief, reply with a single JSON object only (no markdown fences) "
    'with keys: "name" (display name, 2-4 words), "role" (short slug like ops, '
    "coding, research, support), and \"description\" (1-2 sentences on what the "
    "agent specializes in). Use lowercase letters and underscores in role when "
    "appropriate. Do not duplicate existing agents."
)


def _strip_fences(raw: str) -> str:
    s = (raw or "").strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_agent_draft_json(raw: str) -> dict[str, str] | None:
    """Parse model output into name/role/description, or ``None`` if unusable."""
    text = _strip_fences(raw or "").strip()
    if not text:
        return None
    data: Any = None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        cleaned = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            start = text.find("{")
            if start >= 0:
                depth = 0
                for i in range(start, len(text)):
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                data = json.loads(text[start : i + 1])
                            except (json.JSONDecodeError, ValueError):
                                pass
                            break
    if not isinstance(data, dict):
        return None
    name = " ".join(str(data.get("name") or "").split()).strip()[:_NAME_MAX]
    role = " ".join(str(data.get("role") or "").split()).strip()[:_ROLE_MAX]
    description = " ".join(str(data.get("description") or "").split()).strip()[:_DESC_MAX]
    if not name:
        return None
    return {"name": name, "role": role, "description": description}


def _existing_context(agents: list[dict[str, Any]] | None) -> str:
    if not agents:
        return ""
    lines = []
    for a in agents[:24]:
        aid = a.get("id") or "?"
        name = a.get("name") or aid
        role = (a.get("role") or "").strip()
        bit = f"- {name} (id={aid}"
        if role:
            bit += f", role={role}"
        bit += ")"
        lines.append(bit)
    if not lines:
        return ""
    return "Existing agents (avoid duplicates):\n" + "\n".join(lines) + "\n\n"


async def generate_agent_draft(
    brief: str,
    *,
    llm: LLMClient | None = None,
    existing_agents: list[dict[str, Any]] | None = None,
) -> dict[str, str] | None:
    """Ask the default LLM for an agent draft; return fields or ``None`` on failure."""
    text = " ".join((brief or "").split()).strip()
    if not text:
        return None
    text = text[:_BRIEF_MAX]
    try:
        client = llm
        if client is None:
            from app.runtime.llm import LLMConfigError, get_llm

            client = get_llm()
        ctx = _existing_context(existing_agents)
        messages = [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": f"{ctx}Brief:\n{text}\n\nJSON:",
            },
        ]
        logger.info("agent draft LLM request brief_chars=%d", len(text))
        resp = await client.complete(messages, tools=None)
        raw = (resp.content or "").strip()
        parsed = parse_agent_draft_json(raw)
        logger.info(
            "agent draft LLM response raw=%r parsed=%r",
            raw[:160],
            parsed,
        )
        if not parsed:
            return None
        parsed["suggested_id"] = slugify(parsed["name"], fallback="agent")
        return parsed
    except Exception as exc:
        from app.runtime.llm import LLMConfigError

        if isinstance(exc, LLMConfigError):
            raise
        logger.warning("agent draft generation failed: %s", exc, exc_info=True)
        return None


__all__ = ["parse_agent_draft_json", "generate_agent_draft"]
