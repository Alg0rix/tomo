"""Local workplace management using the configured Tomo database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys


def _json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print machine-readable JSON",
    )


def _fields(parser: argparse.ArgumentParser, *, create: bool = False) -> None:
    parser.add_argument(
        "--kind",
        choices=["local", "ssh", "tunnel"],
        default="tunnel" if create else None,
    )
    for field in ("root-path", "ssh-host", "ssh-user"):
        parser.add_argument(f"--{field}")
    parser.add_argument("--ssh-port", type=int)


def add_parser(sub: argparse._SubParsersAction) -> None:
    wp = sub.add_parser(
        "workplaces", help="Manage workplaces in this Tomo installation"
    )
    _json_option(wp)
    actions = wp.add_subparsers(dest="workplaces_cmd", required=True)
    _json_option(actions.add_parser("list", help="List registered workplaces"))
    create = actions.add_parser("create", help="Create a workplace (default: tunnel)")
    create.add_argument("name")
    create.add_argument("--id", dest="workplace_id")
    _fields(create, create=True)
    _json_option(create)
    for action, help_text in (
        ("show", "Show workplace configuration"),
        ("update", "Update workplace configuration"),
        ("pairing-code", "Issue a fresh tunnel pairing code"),
    ):
        parser = actions.add_parser(action, help=help_text)
        parser.add_argument("workplace_id", help="Workplace ID from list/create")
        _json_option(parser)
        if action == "update":
            parser.add_argument("--name")
            _fields(parser)


def _local_view(wp: dict) -> dict:
    """A separate CLI process cannot observe the server's live tunnel hub."""
    if wp.get("kind") == "tunnel":
        wp = dict(wp)
        wp["online"] = None
        wp.pop("connector_connected_at", None)
        wp["status_source"] = "database"
    return wp


def _print_workplace(wp: dict) -> None:
    status = wp.get("status", "")
    if wp.get("kind") == "tunnel":
        status += " (last recorded; live status unavailable in CLI)"
    print(f"{wp['id']}  {wp.get('name', '')}  {wp.get('kind', '')}  {status}")
    if wp.get("host_detail"):
        print(f"  {wp['host_detail']}")
    if wp.get("pairing_code"):
        print(
            f"  Pairing code: {wp['pairing_code']} (expires at {wp['pairing_expires_at']})"
        )


def run(args: argparse.Namespace) -> int:
    from app.core.config import DB_PATH
    from app.models.db import get_connection
    from app.models.mixins import workplaces

    # Do not initialize Store: its server-startup recovery interrupts running jobs.
    # Nor create/seed a new database when invoked on the wrong installation.
    if not DB_PATH.is_file():
        print(
            "Tomo database not found. Run on the coordinator with its TOMO_HOME/TOMO_DB_PATH.",
            file=sys.stderr,
        )
        return 1
    conn = None
    try:
        conn = get_connection(DB_PATH)
        action = args.workplaces_cmd
        if action == "list":
            result = {
                "workplaces": [_local_view(w) for w in workplaces.list_workplaces(conn)]
            }
        elif action == "create":
            fields = ("name", "kind", "root_path", "ssh_host", "ssh_port", "ssh_user")
            data = {
                field: getattr(args, field)
                for field in fields
                if getattr(args, field, None) is not None
            }
            if args.workplace_id:
                data["id"] = args.workplace_id
            from cli.config_cmd import _validate

            data = _validate("workplaces", "create", data)
            result = _local_view(workplaces.create_workplace(conn, data))
        else:
            wp = workplaces.get_workplace(conn, args.workplace_id)
            if wp is None:
                raise ValueError(f"Workplace not found: {args.workplace_id}")
            if action == "update":
                fields = (
                    "name",
                    "kind",
                    "root_path",
                    "ssh_host",
                    "ssh_port",
                    "ssh_user",
                )
                data = {
                    field: getattr(args, field)
                    for field in fields
                    if getattr(args, field, None) is not None
                }
                if not data:
                    raise ValueError("Provide at least one field to update")
                from cli.config_cmd import _validate

                data = _validate("workplaces", "update", data)
                wp = workplaces.update_workplace(conn, args.workplace_id, data)
            elif action == "pairing-code":
                if not wp.get("enabled", True):
                    raise ValueError(
                        "Enable the workplace before issuing a pairing code"
                    )
                wp = workplaces.issue_pairing_code(conn, args.workplace_id)
            result = _local_view(wp)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Workplace command failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
    elif args.workplaces_cmd == "list":
        for wp in result["workplaces"]:
            _print_workplace(wp)
        if not result["workplaces"]:
            print("No workplaces found.")
    else:
        _print_workplace(result)
    return 0
