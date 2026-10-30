"""Explicit, composable tag filters (not image provenance checks)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase

_COMMIT_TOKEN = re.compile(r"(?<![a-zA-Z0-9])[0-9a-fA-F]{7,64}(?![a-zA-Z0-9])")


def commit_value(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", value):
        raise ValueError("commit must contain 7 to 64 hexadecimal characters")
    return value.lower()


@dataclass(frozen=True)
class TagFilters:
    prefix: str = ""
    match: str = "*"
    commit: str = ""
    include_artifacts: bool = False

    def accepts(self, name: str) -> bool:
        if not self.include_artifacts and (
            name.startswith("sha256-") or name.endswith((".sig", ".att", ".sbom", ".git"))
        ):
            return False
        return (
            name.startswith(self.prefix)
            and fnmatchcase(name, self.match)
            and (
                not self.commit
                or any(token.lower().startswith(self.commit) for token in _COMMIT_TOKEN.findall(name))
            )
        )
