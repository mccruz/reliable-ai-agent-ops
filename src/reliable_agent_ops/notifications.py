from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol

from .receipts import Receipt, write_bytes_atomic

EVENT_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SENSITIVE_KEY = re.compile(r"(secret|token|password|credential|api[_-]?key)", re.IGNORECASE)


class NotificationError(ValueError):
    """Raised when notification content violates the public demo contract."""


def _validate_metadata(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str) or SENSITIVE_KEY.search(key):
                raise NotificationError("metadata contains a sensitive or invalid key")
            _validate_metadata(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _validate_metadata(nested)


@dataclass(frozen=True)
class NotificationEvent:
    name: str
    message: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not EVENT_NAME.fullmatch(self.name):
            raise NotificationError("event name must be a lowercase slug")
        if not self.message.strip() or len(self.message) > 500:
            raise NotificationError("message must be 1 to 500 characters")
        if not isinstance(self.metadata, Mapping):
            raise NotificationError("metadata must be an object")
        _validate_metadata(self.metadata)
        try:
            json.dumps(self.metadata, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise NotificationError("metadata must contain JSON-safe values") from exc


class Notifier(Protocol):
    def send(
        self,
        event: NotificationEvent,
        *,
        checked_at: datetime | None = None,
    ) -> Receipt: ...


@dataclass
class DryRunNotifier:
    output_path: Path | None = None

    def send(
        self,
        event: NotificationEvent,
        *,
        checked_at: datetime | None = None,
    ) -> Receipt:
        event_record = {
            "channel": "dry-run",
            "event": event.name,
            "message": event.message,
            "metadata": dict(event.metadata),
        }
        if self.output_path is not None:
            data = (
                json.dumps(event_record, sort_keys=True, separators=(",", ":"), allow_nan=False)
                + "\n"
            ).encode("utf-8")
            write_bytes_atomic(Path(self.output_path), data)
        message_sha256 = hashlib.sha256(event.message.encode("utf-8")).hexdigest()
        return Receipt.create(
            "notification",
            "passed",
            {
                "channel": "dry-run",
                "credential_required": False,
                "delivered_externally": False,
                "event": event.name,
                "message_sha256": message_sha256,
                "metadata_keys": sorted(event.metadata),
            },
            checked_at=checked_at,
        )
