"""Control the running server rather than a separate CLI plugin registry."""

import json
import os
import sys
from urllib.parse import urlsplit, quote

import httpx


def add_parser(sub):
    parser = sub.add_parser(
        "plugins", help="Manage live plugins on the running Tomo server"
    )
    parser.add_argument(
        "--url", default=os.environ.get("TOMO_URL", "http://127.0.0.1:8787")
    )
    actions = parser.add_subparsers(dest="plugin_action", required=True)
    actions.add_parser("list")
    actions.add_parser("outdated", help="Check source commits for available updates")
    install = actions.add_parser("install")
    install.add_argument(
        "path", help="Local path, plugin_id@marketplace, or HTTPS GitHub repository URL"
    )
    install.add_argument("--subdirectory", default="")
    install.add_argument("--ref", default="main")
    search = actions.add_parser("search")
    search.add_argument("query", nargs="?", default="")
    marketplaces = actions.add_parser("marketplaces")
    market_actions = marketplaces.add_subparsers(
        dest="marketplace_action", required=True
    )
    market_actions.add_parser("list")
    market_actions.add_parser("add").add_argument("source")
    for action in ("refresh", "remove"):
        market_actions.add_parser(action).add_argument("identity")
    for action in ("enable", "disable", "reload", "update", "uninstall"):
        child = actions.add_parser(action)
        child.add_argument("id")


def run(args):
    token = os.environ.get("TOMO_API_KEY", "")
    if not token:
        print("Set TOMO_API_KEY to an administrator API key.", file=sys.stderr)
        return 1
    try:
        target = urlsplit(args.url)
        if (
            target.scheme not in {"http", "https"}
            or not target.hostname
            or target.username
            or target.password
            or target.query
            or target.fragment
            or target.path not in {"", "/"}
            or (
                target.scheme == "http"
                and target.hostname not in {"localhost", "127.0.0.1", "::1"}
            )
        ):
            raise ValueError("Use a local HTTP URL or a remote HTTPS URL")
        with httpx.Client(
            base_url=args.url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            if args.plugin_action == "list":
                response = client.get("/api/plugins")
            elif args.plugin_action == "outdated":
                response = client.post(
                    "/api/plugins/check-updates", json={"force": True}
                )
            elif args.plugin_action == "install":
                response = client.post(
                    "/api/plugins/install",
                    json={
                        "path": args.path,
                        "subdirectory": args.subdirectory,
                        "ref": args.ref,
                    },
                )
            elif args.plugin_action == "search":
                response = client.get("/api/plugins/catalog", params={"q": args.query})
            elif args.plugin_action == "marketplaces":
                if args.marketplace_action == "list":
                    response = client.get("/api/marketplaces")
                elif args.marketplace_action == "add":
                    response = client.post(
                        "/api/marketplaces", json={"source": args.source}
                    )
                elif args.marketplace_action == "refresh":
                    response = client.post(
                        f"/api/marketplaces/{quote(args.identity, safe='')}/refresh"
                    )
                else:
                    response = client.delete(
                        f"/api/marketplaces/{quote(args.identity, safe='')}"
                    )
            else:
                response = client.post(
                    f"/api/plugins/{quote(args.id, safe='')}/{args.plugin_action}"
                )
            response.raise_for_status()
            print(json.dumps(response.json(), indent=2))
        return 0
    except (httpx.HTTPError, ValueError) as exc:
        print(f"Plugin command failed: {exc}", file=sys.stderr)
        return 1
