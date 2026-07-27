from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .receipts import Receipt

VERSION_PATTERN = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)$"
)


class UpdateCheckError(ValueError):
    """Raised when update evidence is malformed."""


@dataclass(frozen=True, order=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> "SemanticVersion":
        if not isinstance(value, str):
            raise UpdateCheckError("version must be a string")
        match = VERSION_PATTERN.fullmatch(value)
        if match is None:
            raise UpdateCheckError("version must use major.minor.patch")
        return cls(*(int(match.group(name)) for name in ("major", "minor", "patch")))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def check_update(
    current_version: str,
    manifest_path: Path,
    *,
    checked_at: datetime | None = None,
) -> Receipt:
    current = SemanticVersion.parse(current_version)
    try:
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise UpdateCheckError("update manifest is missing or invalid") from exc
    required = {"schema_version", "project", "latest_version", "release_url"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise UpdateCheckError("update manifest fields are invalid")
    if payload["schema_version"] != "1.0":
        raise UpdateCheckError("update manifest schema_version is unsupported")
    if not isinstance(payload["project"], str) or not payload["project"].strip():
        raise UpdateCheckError("update manifest project is missing")
    latest = SemanticVersion.parse(payload["latest_version"])
    release_url = payload["release_url"]
    if not isinstance(release_url, str):
        raise UpdateCheckError("release_url must be a string")
    parsed = urlsplit(release_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise UpdateCheckError("release_url must be a credential-free HTTPS URL")
    return Receipt.create(
        "update",
        "passed",
        {
            "project": payload["project"],
            "current_version": str(current),
            "latest_version": str(latest),
            "update_available": latest > current,
            "release_url": release_url,
            "source": "local-synthetic-manifest",
        },
        checked_at=checked_at,
    )
