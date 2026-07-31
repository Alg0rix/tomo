"""CLI entry: python -m cli or `tomo` after install."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tomo", description="Tomo agent swarm CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("update", help="Update managed install from git")
    up.add_argument("-y", "--yes", action="store_true", help="Assume yes for prompts")

    un = sub.add_parser("uninstall", help="Remove managed install and user service")
    un.add_argument(
        "--purge",
        action="store_true",
        help="Also delete TOMO_HOME and TOMO_WORK",
    )
    un.add_argument("-y", "--yes", action="store_true", help="Skip confirmations")

    svc = sub.add_parser("service", help="Control systemd --user tomo.service")
    svc.add_argument(
        "action",
        choices=["status", "start", "stop", "restart"],
        help="systemctl --user action",
    )
    return p


def _run(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "update":
        from cli.update_cmd import cmd_update

        return cmd_update(assume_yes=bool(args.yes))
    if args.cmd == "uninstall":
        from cli.uninstall_cmd import uninstall

        uninstall(purge=bool(args.purge), assume_yes=bool(args.yes))
        return 0
    if args.cmd == "service":
        from cli.service import service_action

        return service_action(args.action)
    parser.error(f"unknown command {args.cmd}")
    return 2


def main(argv: list[str] | None = None) -> None:
    """Console-script entry (exit code via SystemExit)."""
    raise SystemExit(_run(argv))


if __name__ == "__main__":
    main()
