"""Seed data for platform entities (tools, skills, workplaces, etc.)."""

from __future__ import annotations

import time
from typing import Any


def _ts(offset: float = 0) -> float:
    return time.time() + offset


def seed_tools() -> list[dict[str, Any]]:
    return [
        {"id": "shell", "name": "Shell", "description": "Run commands on connected workplaces", "backend": "local", "enabled": True, "agent_count": 3},
        {"id": "recall", "name": "Recall", "description": "Search agent memory and session history", "backend": "builtin", "enabled": True, "agent_count": 4},
        {"id": "web_fetch", "name": "Web Fetch", "description": "Fetch and extract readable content from URLs", "backend": "builtin", "enabled": True, "agent_count": 2},
        {"id": "file_read", "name": "File Read", "description": "Read files from agent workspace", "backend": "local", "enabled": True, "agent_count": 3},
        {"id": "delegate", "name": "Delegate", "description": "Hand a sub-task to another agent in the swarm", "backend": "builtin", "enabled": True, "agent_count": 1},
    ]


def seed_skills() -> list[dict[str, Any]]:
    # Catalog placeholders for the Skills UI — not auto-linked to seeded agents.
    return [
        {"id": "onboarding", "name": "Vendor Onboarding", "description": "Structured Q3 vendor intake workflow", "version": "1.2", "enabled": True, "tool_count": 4, "agent_count": 0},
        {"id": "deploy", "name": "Deploy Pipeline", "description": "Staging → production deploy checklist", "version": "2.0", "enabled": True, "tool_count": 6, "agent_count": 0},
        {"id": "research_brief", "name": "Research Brief", "description": "Competitive research summarization", "version": "1.0", "enabled": True, "tool_count": 3, "agent_count": 0},
    ]


def seed_plugins() -> list[dict[str, Any]]:
    """Deprecated alias — modules are discovered from ``modules/`` packages."""
    from modules.registry import all_metas

    return [
        {
            "id": m.id,
            "name": m.name,
            "description": m.description,
            "version": m.version,
            "enabled": m.default_enabled,
            "has_ui": m.has_ui,
            "ui_path": m.ui_path,
        }
        for m in all_metas()
    ]


def seed_workplaces() -> list[dict[str, Any]]:
    return [
        {"id": "wp_local", "name": "Local Dev", "kind": "local", "status": "connected", "host": "127.0.0.1", "agent_count": 2, "updated_at": _ts(-300)},
        {"id": "wp_staging", "name": "Staging VM", "kind": "tunnel", "status": "connected", "host": "staging.internal", "agent_count": 1, "updated_at": _ts(-3600)},
        {"id": "wp_prod", "name": "Production", "kind": "ssh", "status": "offline", "host": "prod.example.com", "agent_count": 0, "updated_at": _ts(-86400)},
    ]


def seed_schedules() -> list[dict[str, Any]]:
    return [
        {"id": "sch_001", "name": "Morning standup digest", "agent_id": "main", "cron": "0 9 * * 1-5", "enabled": True, "last_run": _ts(-43200), "next_run": _ts(3600)},
        {"id": "sch_002", "name": "Staging health check", "agent_id": "ops", "cron": "*/30 * * * *", "enabled": True, "last_run": _ts(-1800), "next_run": _ts(1800)},
        {"id": "sch_003", "name": "Weekly research roundup", "agent_id": "research", "cron": "0 8 * * 1", "enabled": False, "last_run": _ts(-604800), "next_run": None},
    ]


def seed_providers() -> list[dict[str, Any]]:
    return [
        {"id": "openai", "name": "OpenAI", "enabled": True, "model_count": 3},
        {"id": "anthropic", "name": "Anthropic", "enabled": True, "model_count": 2},
        {"id": "local", "name": "Local (Ollama)", "enabled": False, "model_count": 0},
    ]


def seed_models() -> list[dict[str, Any]]:
    return [
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "provider_id": "openai", "context": 128000, "is_default": True},
        {"id": "gpt-4o", "name": "GPT-4o", "provider_id": "openai", "context": 128000, "is_default": False},
        {"id": "claude-3.5-sonnet", "name": "Claude 3.5 Sonnet", "provider_id": "anthropic", "context": 200000, "is_default": False},
    ]


def seed_settings() -> dict[str, Any]:
    return {
        "theme": "dark",
        "default_model": "gpt-4o-mini",
        "default_model_id": "",
        "llm_base_url": "https://api.openai.com/v1",
        "llm_api_key": "",
        "llm_model": "gpt-4o-mini",
        "telegram_bot_token": "",
        "telegram_enabled": False,
        "max_tool_iterations": 12,
        "concurrency_limit": 4,
        "learning_enabled": True,
        "learning_memory_nudge_turns": 3,
        "learning_skill_nudge_iters": 3,
        "public_history": False,
        "setup_complete": True,
        "eval_parallel_workers": 2,
        "eval_two_pass": False,
        "approvals_mode": "smart",
        "approvals_timeout": 300,
        "approvals_deny": [],
    }


def seed_safety_rules() -> list[dict[str, Any]]:
    return [
        {"id": "rule_001", "name": "Block DROP TABLE", "pattern": r"\bDROP\s+TABLE\b", "weight": 15, "category": "sql", "scope": "global", "enabled": True},
        {"id": "rule_002", "name": "Warn rm -rf", "pattern": r"rm\s+-rf", "weight": 8, "category": "shell", "scope": "global", "enabled": True},
        {"id": "rule_003", "name": "Ops agent curl pipe", "pattern": r"curl\s+.*\|\s*bash", "weight": 10, "category": "network", "scope": "specific", "agent_id": "ops", "enabled": True},
    ]


def seed_users() -> list[dict[str, Any]]:
    return [
        {"id": "usr_web", "name": "web", "status": "approved", "last_active": _ts(-3600), "channel": "web"},
        {"id": "usr_tg_01", "name": "Alice", "status": "approved", "last_active": _ts(-7200), "channel": "telegram"},
        {"id": "usr_wa_02", "name": "Bob", "status": "pending", "last_active": _ts(-86400), "channel": "whatsapp"},
        {"id": "usr_blocked", "name": "spammer", "status": "blocked", "last_active": _ts(-604800), "channel": "web", "block_reason": "abuse"},
    ]


def seed_shared_channels() -> list[dict[str, Any]]:
    return [
        {
            "id": "sc_main",
            "name": "Tomo Support Line",
            "type": "whatsapp",
            "status": "disconnected",
            "routes": [
                {"contact": "Alice", "identifier": "+15551234001", "agent_id": "main", "agent_name": "Tomo"},
                {"contact": "Vendor desk", "identifier": "+15551234002", "agent_id": "ops", "agent_name": "Ops"},
            ],
            "inbox": [{"contact": "Unknown", "identifier": "+15559998888"}],
        },
        {
            "id": "sc_tg",
            "name": "Telegram Bot",
            "type": "telegram",
            "status": "needs_token",
            "routes": [],
            "inbox": [],
        },
    ]


def seed_eval_domains() -> list[dict[str, Any]]:
    return [
        {"id": "routing", "name": "Routing", "levels": 3, "test_count": 12},
        {"id": "tools", "name": "Tool use", "levels": 4, "test_count": 18},
        {"id": "memory", "name": "Memory", "levels": 2, "test_count": 8},
    ]


def seed_evaluators() -> list[dict[str, Any]]:
    return [
        {"id": "exact", "name": "Exact match", "description": "String equality on expected output", "enabled": True},
        {"id": "llm_judge", "name": "LLM judge", "description": "Model grades response against rubric", "enabled": True},
        {"id": "regex", "name": "Regex", "description": "Pattern match on response", "enabled": False},
    ]


def seed_eval_runs() -> list[dict[str, Any]]:
    return [
        {
            "id": "run_001",
            "model": "gpt-4o-mini",
            "status": "complete",
            "passed": 28,
            "failed": 2,
            "total": 30,
            "started_at": _ts(-86400),
            "domains": {"routing": {"L1": "pass", "L2": "pass", "L3": "warn"}, "tools": {"L1": "pass", "L2": "pass"}},
        },
        {
            "id": "run_002",
            "model": "claude-3.5-sonnet",
            "status": "incomplete",
            "passed": 14,
            "failed": 1,
            "total": 30,
            "started_at": _ts(-3600),
            "domains": {},
        },
    ]
