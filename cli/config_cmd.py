"""Non-interactive configuration over Tomo's existing local model functions."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib
import json
from pathlib import Path
import sqlite3
import sys

# module, list/get/create/update/delete function, create/update schema
RESOURCES = {
    "agents": (
        "agents",
        "list_agents",
        "get_agent",
        "create_agent",
        "update_agent",
        "delete_agent",
        "AgentCreate",
        "AgentUpdate",
    ),
    "workplaces": (
        "workplaces",
        "list_workplaces",
        "get_workplace",
        "create_workplace",
        "update_workplace",
        "delete_workplace",
        "WorkplaceCreate",
        "WorkplaceUpdate",
    ),
    "llm-profiles": (
        "llm_profiles",
        "list_profiles",
        "get_public_profile",
        "create_profile",
        "update_profile",
        "delete_profile",
        "LLMProfileCreate",
        "LLMProfileUpdate",
    ),
    "mcp-servers": (
        "mcp",
        "list_servers",
        "get_server",
        "create_server",
        "update_server",
        "delete_server",
        "McpServerCreate",
        "McpServerUpdate",
    ),
    "schedules": (
        "schedules",
        "list_schedules",
        "get_schedule",
        "create_schedule",
        "update_schedule",
        "delete_schedule",
        "ScheduleCreate",
        "ScheduleUpdate",
    ),
    "users": (
        "users",
        "list_users",
        "get_user",
        "create_user",
        "update_user",
        "delete_user",
        "UserCreate",
        "UserUpdate",
    ),
    "api-keys": (
        "api_keys",
        "list_api_keys",
        "get_api_key",
        "create_api_key",
        None,
        "delete_api_key",
        "ApiKeyCreate",
        None,
    ),
    "skills": (
        "skills",
        "list_skills",
        "get_skill",
        None,
        "update_skill",
        None,
        None,
        None,
    ),

}
SPECIAL = ("settings", "agent-tools", "agent-skills", "mcp-items")


@contextmanager
def local_db():
    from app.core.config import DB_PATH
    from app.models.db import get_connection

    if not DB_PATH.is_file():
        raise ValueError(
            "Tomo database not found; use the coordinator's TOMO_HOME/TOMO_DB_PATH"
        )
    conn = get_connection(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def add_parser(sub: argparse._SubParsersAction) -> None:
    config = sub.add_parser(
        "config", help="Configure local Tomo resources without the web UI"
    )
    config.add_argument("resource", choices=[*RESOURCES, *SPECIAL])
    config.add_argument(
        "action",
        choices=[
            "list",
            "show",
            "create",
            "update",
            "delete",
            "schema",
            "default",
            "pause",
            "resume",
            "enable",
            "disable",
            "pairing-code",
        ],
    )
    config.add_argument(
        "id", nargs="?", help="Resource ID (agent ID for agent tools/skills)"
    )
    config.add_argument("--data", help="JSON object, @file, or - to read stdin")
    config.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help="Set a field; JSON values supported; repeat for multiple fields",
    )
    config.add_argument(
        "--json", action="store_true", help="Compact machine-readable output"
    )


def _payload(args) -> dict:
    data = {}
    if args.data is not None:
        raw = args.data
        if raw == "-":
            raw = sys.stdin.read()
        elif raw.startswith("@"):
            raw = Path(raw[1:]).read_text()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("--data must contain a JSON object")
    for assignment in args.set:
        key, sep, value = assignment.partition("=")
        if not sep or not key:
            raise ValueError("--set expects FIELD=VALUE")
        try:
            value = json.loads(value)
        except ValueError:
            pass
        data[key.replace("-", "_")] = value
    return data


def _validate(resource: str, action: str, data: dict) -> dict:
    from app import schemas

    entry = RESOURCES[resource]
    schema_name = entry[6 if action == "create" else 7]
    if schema_name:
        schema = getattr(schemas, schema_name)
        unknown = set(data) - set(schema.model_fields)
        if unknown:
            raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}")
        return schema.model_validate(data).model_dump(
            exclude_unset=action == "update", exclude_none=True
        )
    allowed = {
        "skills": {"enabled", "name", "description", "version"},
    }[resource]
    if set(data) - allowed:
        raise ValueError(f"Allowed fields: {', '.join(sorted(allowed))}")
    if "enabled" in data and not isinstance(data["enabled"], bool):
        raise ValueError("enabled must be true or false")
    return data


def _tool_catalog(conn) -> list[dict]:
    # Read definitions without importing tool backends or starting the runtime.
    from app.core.config import APP_DIR
    from app.models.mixins import mcp

    catalog = []
    for path in sorted((APP_DIR / "tools").glob("*.json")):
        definition = json.loads(path.read_text())
        catalog.append(
            {
                "id": definition.get("id") or definition.get("name") or path.stem,
                "name": definition.get("name") or path.stem,
            }
        )
    for item in [
        item
        for server in mcp.list_servers(conn)
        for item in mcp.list_items(conn, server["id"])
    ]:
        if item.get("kind") == "tool":
            catalog.append({"id": item["runtime_id"], "name": item["name"]})
    return catalog


def _special(conn, args, data):
    from app.models.mixins import agents, mcp, settings, skills, tools

    resource, action, target = args.resource, args.action, args.id
    if resource == "settings":
        if target:
            raise ValueError("settings does not take an ID")
        if action in {"show", "list"}:
            return settings.public_settings(settings.get_settings(conn))
        if action == "update":
            unknown = set(data) - set(settings.get_settings(conn))
            if unknown:
                raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
            if "memory_consolidation_cron" in data:
                from apscheduler.triggers.cron import CronTrigger

                CronTrigger.from_crontab(str(data["memory_consolidation_cron"]))
            return settings.update_settings(conn, data)
    elif resource in {"agent-tools", "agent-skills"}:
        if not target or not agents.get_agent(conn, target, set()):
            raise ValueError("Provide an existing agent ID")
        if resource == "agent-skills":
            if action in {"list", "show"}:
                return skills.list_for_agent(conn, target)
            if action == "update":
                ids = data.get("skill_ids")
                if (
                    set(data) != {"skill_ids"}
                    or not isinstance(ids, list)
                    or not all(isinstance(x, str) for x in ids)
                ):
                    raise ValueError('Provide {"skill_ids": ["skill-id", ...]}')
                known = {s["id"] for s in skills.list_skills(conn)}
                if set(ids) - known:
                    raise ValueError("Unknown skill IDs")
                return skills.set_for_agent(conn, target, ids)
        else:
            catalog = _tool_catalog(conn)
            if action in {"list", "show"}:
                return tools.list_for_agent(conn, target, catalog)
            if action == "update":
                enabled = data.get("enabled")
                if (
                    set(data) != {"enabled"}
                    or not isinstance(enabled, dict)
                    or not all(isinstance(v, bool) for v in enabled.values())
                ):
                    raise ValueError('Provide {"enabled": {"tool-id": true, ...}}')
                known = [t["id"] for t in catalog]
                if set(enabled) - set(known):
                    raise ValueError("Unknown tool IDs")
                tools.set_for_agent(conn, target, enabled, known)
                return tools.list_for_agent(conn, target, catalog)
    elif resource == "mcp-items":
        if action in {"list", "show"}:
            return (
                mcp.list_items(conn, server_id=target)
                if action == "list"
                else mcp.get_item(conn, target)
            )
        if action == "update":
            if set(data) != {"enabled"} or not isinstance(data["enabled"], bool):
                raise ValueError("Provide enabled=true or enabled=false")
            return mcp.set_item_enabled(conn, target, data["enabled"])
    raise ValueError(f"Unsupported action {action} for {resource}")


def execute(conn, args, data):
    resource, action, target = args.resource, args.action, args.id
    if resource in SPECIAL:
        return _special(conn, args, data)
    entry = RESOURCES[resource]
    module = importlib.import_module(f"app.models.mixins.{entry[0]}")
    if action == "list":
        return (
            getattr(module, entry[1])(conn, set())
            if resource == "agents"
            else getattr(module, entry[1])(conn)
        )
    if not target and action not in {"create", "schema"}:
        raise ValueError("Provide a resource ID")
    if action == "create" and target:
        raise ValueError(
            "Pass create fields with --set or --data; use --set id=... for an explicit ID"
        )
    if action == "default" and resource == "llm-profiles":
        module.set_default_model_id(conn, target)
        return module.get_public_profile(conn, target)
    if action in {"pause", "resume"} and resource == "schedules":
        return getattr(module, f"{action}_schedule")(conn, target)
    if action in {"enable", "disable", "pairing-code"} and resource == "workplaces":
        if action == "pairing-code":
            wp = module.get_workplace(conn, target)
            if wp and not wp["enabled"]:
                raise ValueError("Workplace is disabled")
            return module.issue_pairing_code(conn, target)
        return module.set_enabled(conn, target, action == "enable")
    index = {"show": 2, "create": 3, "update": 4, "delete": 5}.get(action)
    if index is None or not entry[index]:
        raise ValueError(f"Unsupported action {action} for {resource}")
    fn = getattr(module, entry[index])
    if action in {"create", "update"}:
        data = _validate(resource, action, data)
        if resource == "agents":
            ids = data.get("workplace_ids", []) + [data.get("workplace_id", "")]
            from app.models.mixins import workplaces

            for wid in ids:
                if (
                    wid
                    and wid not in {"__all__", "__all_tunnels__"}
                    and not workplaces.get_workplace(conn, wid)
                ):
                    raise ValueError(f"Workplace not found: {wid}")
        if resource == "api-keys":
            return fn(conn, data["user_id"], data.get("name", ""))
        if action == "create":
            return fn(conn, data)
        return (
            fn(conn, target, data, set())
            if resource == "agents"
            else fn(conn, target, data)
        )
    if action == "show":
        return fn(conn, target, set()) if resource == "agents" else fn(conn, target)
    if not fn(conn, target):
        raise ValueError(f"Resource not found: {target}")
    return {"deleted": target}


def run(args) -> int:
    try:
        data = _payload(args)
        if args.action not in {"create", "update"} and data:
            raise ValueError("--data and --set are only for create/update")
        if args.action == "update" and not data:
            raise ValueError("Provide fields with --set or --data")
        if args.action == "schema":
            from app import schemas

            entry = RESOURCES.get(args.resource)
            result = {
                action: getattr(schemas, entry[index]).model_json_schema()
                for action, index in (("create", 6), ("update", 7))
                if entry and entry[index]
            }
            if not result:
                raise ValueError(
                    "No request schema for this resource; see tomo config --help"
                )
        else:
            with local_db() as conn:
                result = execute(conn, args, data)
            if result is None:
                raise ValueError(f"Resource not found: {args.id}")
        if args.resource == "workplaces":
            from cli.workplaces_cmd import _local_view

            result = (
                [_local_view(w) for w in result]
                if isinstance(result, list)
                else _local_view(result)
            )
        print(json.dumps(result, ensure_ascii=False, indent=None if args.json else 2))
        return 0
    except (OSError, sqlite3.Error, ValueError) as exc:
        # Validation errors can contain input secrets; print messages without inputs.
        from pydantic import ValidationError

        detail = (
            json.dumps(
                exc.errors(
                    include_input=False, include_url=False, include_context=False
                )
            )
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        print(f"Configuration failed: {detail}", file=sys.stderr)
        return 1
