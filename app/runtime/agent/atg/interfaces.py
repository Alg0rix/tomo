"""Tool I/O interface annotations for ATG.

Tomo's tools always return a single string, so every tool's declared output
is the key ``"result"``. Read-only classification is a local set (mirrors the
registry's tool names) so the executor can run independent read-only nodes in
parallel within a wave.
"""
from __future__ import annotations

# Tools whose execution is side-effect-free and safe to parallelise.
_READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "search_files",
        "web_fetch",
        "web_search",
        "recall",
        "session_search",
        "list_skills",
        "todo",
    }
)

# Declared output keys per tool. Tomo tools return a single string, so the
# default interface is {"result": "string"} — only override when a tool's
# result has a more specific semantic label worth surfacing in compiler
# prompts.
TOOL_INTERFACES = {
    "read_file": {"outputs": {"content": "string"}},
    "bash": {"outputs": {"stdout": "string", "exit_code": "integer"}},
    "web_fetch": {"outputs": {"content": "string"}},
    "web_search": {"outputs": {"results": "string"}},
    "recall": {"outputs": {"entry": "string"}},
}

DEFAULT_INTERFACE = {"outputs": {"result": "string"}}


def get_tool_interface(tool_name: str) -> dict:
    return TOOL_INTERFACES.get(tool_name, DEFAULT_INTERFACE)


def is_read_only(tool_name: str) -> bool:
    return tool_name in _READ_ONLY_TOOLS


def get_interface_catalog(tools: list) -> str:
    """Render the condensed tool catalog for compiler prompts.

    ``tools`` is the OpenAI function-def list as passed to ``run_turn``.
    One line per tool: name(param: type, optional?) -> {out: type} [read-only|mutating]
    """
    lines: list[str] = []
    for tool_def in tools or []:
        fn = tool_def.get("function", tool_def) or {}
        name = fn.get("name")
        if not name:
            continue
        params = (fn.get("parameters") or {}).get("properties") or {}
        required = set((fn.get("parameters") or {}).get("required") or [])
        parts: list[str] = []
        for pname, spec in params.items():
            ptype = (spec or {}).get("type", "any")
            suffix = "" if pname in required else "?"
            parts.append(f"{pname}{suffix}: {ptype}")
        outputs = get_tool_interface(name)["outputs"]
        out_str = ", ".join(f"{k}: {v}" for k, v in outputs.items())
        mode = "read-only" if is_read_only(name) else "mutating"
        lines.append(f"- {name}({', '.join(parts)}) -> {{{out_str}}} [{mode}]")
    return "\n".join(lines)


__all__ = [
    "TOOL_INTERFACES",
    "DEFAULT_INTERFACE",
    "get_tool_interface",
    "is_read_only",
    "get_interface_catalog",
]
