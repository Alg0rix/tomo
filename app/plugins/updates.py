"""Check recorded Git sources without downloading or executing plugin code."""

import json
import re
import time
from urllib.parse import quote, urlsplit

import httpx

from app.plugins.catalogs import GitSource, fetch_bytes


def _ref(url: str):
    try:
        return json.loads(fetch_bytes(url))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise


def check_source(row: dict, validate_manifest) -> dict:
    result = {
        "id": row["id"],
        "installed_commit": row.get("commit", ""),
        "installed_version": row.get("version", ""),
        "checked_at": time.time(),
    }
    if row.get("source") != "git":
        return result | {
            "status": "local",
            "message": "Edit the local source, then reload.",
        }
    source = GitSource.model_validate(row["origin"])
    ref = source.ref
    if re.fullmatch(r"[a-fA-F0-9]{40}", ref) or ref.startswith("refs/tags/"):
        return result | {
            "status": "pinned",
            "message": "This source is pinned to a tag or commit.",
        }
    repository = urlsplit(source.url).path.strip("/").removesuffix(".git")
    base = f"https://api.github.com/repos/{repository}"
    branch = ref.removeprefix("refs/heads/")
    resolved = _ref(base + "/git/ref/heads/" + quote(branch, safe=""))
    if resolved is None:
        tag = _ref(base + "/git/ref/tags/" + quote(ref, safe=""))
        if tag is not None or re.fullmatch(r"[a-fA-F0-9]{7,39}", ref):
            return result | {
                "status": "pinned",
                "message": "This source is pinned to a tag or commit.",
            }
        raise ValueError("Tracked Git branch is unavailable")
    if resolved.get("ref") != "refs/heads/" + branch:
        raise ValueError("Git branch identity changed")
    commit = resolved["object"]["sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Invalid resolved commit")
    result["latest_commit"] = commit
    if commit == row.get("commit"):
        return result | {
            "status": "current",
            "latest_version": row.get("version", ""),
            "message": "Up to date.",
        }
    path = (
        source.subdirectory + "/" if source.subdirectory else ""
    ) + "tomo-plugin.json"
    manifest = validate_manifest(
        json.loads(
            fetch_bytes(
                f"https://raw.githubusercontent.com/{repository}/{commit}/{quote(path, safe='/')}",
                limit=64_000,
            )
        )
    )
    if manifest["id"] != row["id"]:
        raise ValueError("Update plugin identity differs from installed identity")
    return result | {
        "status": "available",
        "latest_version": manifest["version"],
        "message": "A new source revision is available.",
    }
