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
_DESC_MAX = 600
_SYSTEM_PROMPT_MAX = 12_000

_SYSTEM = (
    "You design specialized AI agents for Tomo, a multi-agent swarm platform. "
    "Given a brief, reply with a single JSON object only (no markdown fences). "
    "Required keys:\n"
    '- "name": display name, 2-5 words\n'
    '- "role": short slug (lowercase, e.g. erpnext, ops, coding, research)\n'
    '- "description": 2-4 sentences for the swarm roster — specialty, when others '
    "should delegate to this agent, and typical outcomes\n"
    '- "system_prompt": markdown for the agent SYSTEM.md file (200-500 words). '
    "Include: # title, identity paragraph, ## Responsibilities (5-8 specific bullets), "
    "## Expertise, ## How you work (tone, constraints, tool usage), "
    "## When to escalate/delegate. Be concrete to the domain in the brief — "
    "not generic filler.\n"
    "Do not duplicate existing agents."
)


def _fallback_system_prompt(name: str, role: str, description: str) -> str:
    """Minimal SYSTEM.md when the model omits ``system_prompt``."""
    return (
        f"# {name}\n\n"
        f"You are **{name}**, a swarm specialist (role: `{role}`).\n\n"
        f"{description}\n\n"
        "## Responsibilities\n"
        "- Own tasks that clearly match your specialty end to end.\n"
        "- Use tools to inspect, change, and verify work — prefer evidence over guesses.\n"
        "- Return concise summaries with concrete next steps or artifacts.\n"
        "- Ask for missing context before risky or irreversible actions.\n"
        "- Coordinate with the swarm when work spans another agent's role.\n\n"
        "## How you work\n"
        "- Be direct, practical, and specific to the user's environment.\n"
        "- Cite files, commands, or config paths when relevant.\n"
        "- Escalate to the coordinator when the task is outside your domain.\n"
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
    system_prompt = str(data.get("system_prompt") or "").strip()[:_SYSTEM_PROMPT_MAX]
    if not name:
        return None
    return {
        "name": name,
        "role": role,
        "description": description,
        "system_prompt": system_prompt,
    }


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
        if not parsed.get("system_prompt"):
            parsed["system_prompt"] = _fallback_system_prompt(
                parsed["name"],
                parsed.get("role") or "specialist",
                parsed.get("description") or parsed["name"],
            )
        parsed["suggested_id"] = slugify(parsed["name"], fallback="agent")
        return parsed
    except Exception as exc:
        from app.runtime.llm import LLMConfigError

        if isinstance(exc, LLMConfigError):
            raise
        logger.warning("agent draft generation failed: %s", exc, exc_info=True)
        return None


__all__ = ["parse_agent_draft_json", "generate_agent_draft"]
