"""Agent-native plugin development and lifecycle controls."""

import json


def run(arguments: dict) -> str:
    from app.plugins.manager import get_manager
    from app.runtime.tools.user_ctx import current_user_id
    from app.services import store

    user = store.get_user(current_user_id())
    if not user or not user.get("enabled") or user.get("role") != "admin":
        return "Error: plugin management requires an administrator"
    from app.runtime.policy import authorize_tool
    authorize_tool("plugin_manager", arguments)
    manager = get_manager()
    action = arguments.get("action", "list")
    try:
        if action in {
            "marketplaces",
            "marketplace_add",
            "marketplace_refresh",
            "marketplace_remove",
            "search",
        }:
            from app.plugins.catalogs import get_marketplaces

            markets = get_marketplaces()
            if action == "marketplaces":
                result = markets.list()
            elif action == "search":
                result = markets.search(arguments.get("query", ""))
            elif action == "marketplace_add":
                result = markets.add(arguments.get("source", ""))
            elif action == "marketplace_refresh":
                result = markets.refresh(arguments.get("id", ""))
            else:
                result = markets.remove(arguments.get("id", ""))
        elif action == "list":
            result = manager.list()
        elif action == "sync_dependencies":
            result = manager.sync_dependencies(arguments.get("id", ""))
        elif action == "outdated":
            result = manager.check_updates()
        elif action == "install":
            result = manager.install(
                arguments.get("path", ""),
                arguments.get("subdirectory", ""),
                arguments.get("ref", "main"),
            )
        else:
            result = manager.change(arguments.get("id", ""), action)
        return json.dumps(result)
    except Exception as exc:
        return f"Error: {exc}"
