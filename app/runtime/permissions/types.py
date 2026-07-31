"""Permission assessment types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

FindingKind = Literal["hardline", "user_deny", "escape", "dangerous"]


@dataclass(frozen=True)
class Finding:
    kind: FindingKind
    key: str
    description: str
    paths: tuple[Path, ...] = ()


@dataclass
class Assessment:
    findings: list[Finding] = field(default_factory=list)

    def has_hardline(self) -> bool:
        return any(f.kind == "hardline" for f in self.findings)

    def has_user_deny(self) -> bool:
        return any(f.kind == "user_deny" for f in self.findings)

    def escape_paths(self) -> tuple[Path, ...]:
        out: list[Path] = []
        for f in self.findings:
            if f.kind == "escape":
                out.extend(f.paths)
        return tuple(out)

    def allowlist_keys(self) -> list[str]:
        return [f.key for f in self.findings if f.kind in {"escape", "dangerous"}]


__all__ = ["Finding", "FindingKind", "Assessment"]
