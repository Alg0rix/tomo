"""Marketplace metadata and safe source download. Discovery never executes code."""

from __future__ import annotations

import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import threading
import time
from typing import Literal
from urllib.parse import quote, urlsplit
import zipfile

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

ID = r"^[a-z][a-z0-9_]{0,31}$"
MARKET_ID = r"^[a-z][a-z0-9_-]{0,63}$"
OFFICIAL_URL = (
    "https://raw.githubusercontent.com/Alg0rix/tomo-plugins/main/marketplace.json"
)
COMMUNITY_URL = (
    "https://raw.githubusercontent.com/Alg0rix/tomo-marketplace/main/marketplace.json"
)


class GitSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["git"] = "git"
    url: str = Field(
        pattern=r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$"
    )
    ref: str = Field(
        default="main",
        json_schema_extra={"pattern": r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9_./-]{0,200}$"},
    )
    subdirectory: str = Field(
        default="",
        json_schema_extra={"pattern": r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*\\).*$"},
    )

    @field_validator("url")
    @classmethod
    def github_url(cls, value):
        if not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", value
        ):
            raise ValueError("Plugin sources must be HTTPS GitHub repository URLs")
        return value

    @field_validator("ref")
    @classmethod
    def valid_ref(cls, value):
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,200}", value)
            or ".." in value
        ):
            raise ValueError("Invalid Git ref")
        return value

    @field_validator("subdirectory")
    @classmethod
    def relative_path(cls, value):
        if value and (
            PurePosixPath(value).is_absolute()
            or ".." in PurePosixPath(value).parts
            or "\\" in value
        ):
            raise ValueError("Subdirectory must stay inside the repository")
        return value


class Entry(BaseModel):
    icon: str = "puzzle"

    @field_validator("icon")
    @classmethod
    def valid_icon(cls, value):
        from app.plugins.icons import validate_icon

        return validate_icon(value)

    id: str = Field(pattern=ID)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(max_length=2000)
    version: str = Field(min_length=1, max_length=60)
    sdk_version: Literal[1]
    author: str = Field(min_length=1, max_length=120)
    source: GitSource
    kanji: str = ""
    category: str = Field(default="", max_length=40)

    @field_validator("kanji")
    @classmethod
    def valid_kanji(cls, value):
        from app.plugins.icons import clean_kanji

        return clean_kanji(value)


class Catalog(BaseModel):
    schema_version: Literal[1]
    id: str = Field(pattern=MARKET_ID)
    name: str = Field(min_length=1, max_length=120)
    plugins: list[Entry] = Field(max_length=2000)

    @field_validator("plugins")
    @classmethod
    def unique_ids(cls, value):
        if len({entry.id for entry in value}) != len(value):
            raise ValueError("Duplicate plugin ids in catalog")
        return value


def fetch_bytes(url: str, limit: int = 2_000_000) -> bytes:
    if urlsplit(url).scheme != "https":
        raise ValueError("Remote catalogs require HTTPS")
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        with client.stream(
            "GET",
            url,
            headers={
                "User-Agent": "Tomo-Plugins",
                "Accept": "application/vnd.github+json",
            },
        ) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > limit:
                    raise ValueError("Download exceeds size limit")
            return bytes(body)


def download_repository(
    source: GitSource, destination: Path, *, commit: str | None = None
) -> str:
    """Resolve a ref, then extract that exact commit without running git hooks."""
    owner, repo = urlsplit(source.url).path.strip("/").removesuffix(".git").split("/")
    if commit is None:
        commit = json.loads(
            fetch_bytes(
                f"https://api.github.com/repos/{owner}/{repo}/commits/{quote(source.ref, safe='')}"
            )
        )["sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Invalid resolved commit")
    archive = fetch_bytes(
        f"https://codeload.github.com/{owner}/{repo}/zip/{commit}", limit=32_000_000
    )
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        items = package.infolist()
        if len(items) > 10000 or sum(item.file_size for item in items) > 128_000_000:
            raise ValueError("Repository archive exceeds extraction limits")
        prefix = None
        for item in items:
            parts = PurePosixPath(item.filename).parts
            if (
                not parts
                or PurePosixPath(item.filename).is_absolute()
                or ".." in parts
                or "\\" in item.filename
            ):
                raise ValueError("Unsafe archive path")
            if prefix is None:
                prefix = parts[0]
            if parts[0] != prefix:
                raise ValueError("Unexpected archive root")
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("Repository symlinks are not supported")
            target = destination.joinpath(*parts[1:])
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif len(parts) > 1:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(package.read(item))
    return commit


class Marketplaces:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "plugins/marketplaces.json"
        self._lock = threading.RLock()
        self._rows = json.loads(self.path.read_text()) if self.path.exists() else {}
        for identity, name, source in [
            ("tomo-official", "Tomo Official", OFFICIAL_URL),
            ("tomo-community", "Tomo Community", COMMUNITY_URL),
        ]:
            self._rows.setdefault(
                identity,
                {
                    "id": identity,
                    "name": name,
                    "source": source,
                    "reserved": True,
                    "refreshed_at": None,
                    "catalog": {
                        "schema_version": 1,
                        "id": identity,
                        "name": name,
                        "plugins": [],
                    },
                },
            )

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._rows, indent=2))
        temporary.replace(self.path)

    def list(self):
        with self._lock:
            return [
                {k: v for k, v in row.items() if k != "catalog"}
                | {"plugin_count": len(row["catalog"]["plugins"])}
                for row in self._rows.values()
            ]

    def _read(self, source: str) -> dict:
        if source.startswith("path:"):
            path = Path(source[5:]).expanduser()
            if path.is_dir():
                path /= "marketplace.json"
            if path.stat().st_size > 2_000_000:
                raise ValueError("Catalog exceeds size limit")
            raw = path.read_bytes()
        elif source.startswith("git:"):
            spec = source[4:]
            url, separator, ref = spec.partition("@")
            with tempfile.TemporaryDirectory(prefix="tomo-catalog-") as directory:
                download_repository(
                    GitSource(url=url, ref=ref if separator else "main"),
                    Path(directory),
                )
                return self._read("path:" + directory)
        else:
            raw = fetch_bytes(source)
        return Catalog.model_validate_json(raw).model_dump()

    def add(self, source: str):
        catalog = self._read(source)
        if catalog["name"] in {"Tomo Official", "Tomo Community"}:
            raise ValueError("Default marketplace names are reserved")
        with self._lock:
            if catalog["id"] in self._rows:
                raise ValueError("Marketplace identity already registered or reserved")
            row = {
                "id": catalog["id"],
                "name": catalog["name"],
                "source": source,
                "reserved": False,
                "refreshed_at": time.time(),
                "catalog": catalog,
            }
            self._rows[catalog["id"]] = row
            try:
                self._save()
            except Exception:
                self._rows.pop(catalog["id"])
                raise
        return {k: v for k, v in row.items() if k != "catalog"}

    def refresh(self, identity: str):
        with self._lock:
            old = dict(self._rows[identity])
        catalog = self._read(old["source"])
        if not old["reserved"] and catalog["name"] in {
            "Tomo Official",
            "Tomo Community",
        }:
            raise ValueError("Default marketplace names are reserved")
        if catalog["id"] != identity or (
            old["reserved"] and catalog["name"] != old["name"]
        ):
            raise ValueError("Marketplace identity changed")
        with self._lock:
            if self._rows.get(identity) != old:
                raise RuntimeError("Marketplace changed during refresh; retry")
            self._rows[identity] = {
                **old,
                "name": catalog["name"],
                "catalog": catalog,
                "refreshed_at": time.time(),
            }
            try:
                self._save()
            except Exception:
                self._rows[identity] = old
                raise
        return next(row for row in self.list() if row["id"] == identity)

    def remove(self, identity: str):
        with self._lock:
            row = self._rows[identity]
            if row["reserved"]:
                raise ValueError("Default marketplaces cannot be removed")
            del self._rows[identity]
            try:
                self._save()
            except Exception:
                self._rows[identity] = row
                raise
        return {"removed": identity}

    def search(self, query: str = ""):
        with self._lock:
            return [
                {**entry, "marketplace": row["id"], "publisher": row["name"]}
                for row in self._rows.values()
                for entry in row["catalog"]["plugins"]
                if query.lower()
                in " ".join(
                    str(entry[key]) for key in ("id", "name", "description", "author")
                ).lower()
            ]

    def resolve(self, spec: str):
        plugin_id, _, marketplace = spec.partition("@")
        matches = [
            entry
            for entry in self.search()
            if entry["id"] == plugin_id
            and (not marketplace or entry["marketplace"] == marketplace)
        ]
        if len(matches) != 1:
            raise ValueError(
                "Refresh catalogs first, then choose a unique plugin_id@marketplace"
            )
        return matches[0]


_marketplaces: Marketplaces | None = None
_marketplaces_lock = threading.RLock()


def get_marketplaces():
    global _marketplaces
    with _marketplaces_lock:
        if _marketplaces is None:
            from app.core.config import TOMO_HOME

            _marketplaces = Marketplaces(TOMO_HOME)
        return _marketplaces


def prepare_install(spec: str, root: Path, subdirectory: str = "", ref: str = "main"):
    """Return source path, provenance, expected metadata, and an owned download."""
    if spec.startswith("path:") or Path(spec).expanduser().exists():
        path = Path(spec.removeprefix("path:")).expanduser().resolve(strict=True)
        return path, {"source": "path"}, None, None
    entry = None
    if spec.startswith(("git:", "https://")):
        source = GitSource(
            url=spec.removeprefix("git:"), ref=ref, subdirectory=subdirectory
        )
    else:
        entry = get_marketplaces().resolve(spec)
        source = GitSource.model_validate(entry["source"])
    downloads = root / "plugins/sources"
    downloads.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix="repo-", dir=downloads))
    try:
        commit = download_repository(source, destination)
        path = destination / source.subdirectory
        info = {"source": "git", "origin": source.model_dump(), "commit": commit}
        if entry:
            info.update(marketplace=entry["marketplace"], author=entry["author"])
        return path, info, entry, destination
    except Exception:
        shutil.rmtree(destination)
        raise
