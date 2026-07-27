from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "1.0"
ALLOWED_KINDS = {
    "backup",
    "health",
    "notification",
    "readiness",
    "recovery",
    "restore",
    "update",
}
ALLOWED_STATUSES = {"passed", "failed"}


class ReceiptError(ValueError):
    """Raised when receipt evidence is missing, malformed, or stale."""


def _validate_json_value(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ReceiptError(f"{path} object keys must be strings")
            _validate_json_value(nested, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _validate_json_value(nested, path=f"{path}[{index}]")
        return
    if value is None or isinstance(value, (str, int, float, bool)):
        try:
            json.dumps(value, allow_nan=False)
        except ValueError as exc:
            raise ReceiptError(f"{path} contains a non-finite number") from exc
        return
    raise ReceiptError(f"{path} contains a non-JSON value")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise ReceiptError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReceiptError("timestamps must include a timezone")
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ReceiptError("checked_at must be a non-empty string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReceiptError("checked_at is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReceiptError("checked_at must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class Receipt:
    kind: str
    status: str
    checked_at: str
    details: Mapping[str, Any]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.schema_version, str):
            raise ReceiptError("schema_version must be a string")
        if self.schema_version != SCHEMA_VERSION:
            raise ReceiptError(f"unsupported schema_version: {self.schema_version!r}")
        if not isinstance(self.kind, str):
            raise ReceiptError("kind must be a string")
        if self.kind not in ALLOWED_KINDS:
            raise ReceiptError(f"unsupported receipt kind: {self.kind!r}")
        if not isinstance(self.status, str):
            raise ReceiptError("status must be a string")
        if self.status not in ALLOWED_STATUSES:
            raise ReceiptError(f"unsupported receipt status: {self.status!r}")
        parse_utc(self.checked_at)
        if not isinstance(self.details, Mapping):
            raise ReceiptError("details must be a JSON object")
        _validate_json_value(self.details, path="details")

    @classmethod
    def create(
        cls,
        kind: str,
        status: str,
        details: Mapping[str, Any],
        *,
        checked_at: datetime | None = None,
    ) -> "Receipt":
        return cls(
            kind=kind,
            status=status,
            checked_at=format_utc(checked_at or utc_now()),
            details=dict(details),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Receipt":
        if not isinstance(payload, Mapping):
            raise ReceiptError("receipt root must be a JSON object")
        required = {"schema_version", "kind", "status", "checked_at", "details"}
        missing = sorted(required - set(payload))
        extra = sorted(set(payload) - required)
        if missing:
            raise ReceiptError(f"receipt is missing fields: {', '.join(missing)}")
        if extra:
            raise ReceiptError(f"receipt contains unknown fields: {', '.join(extra)}")
        return cls(
            schema_version=payload["schema_version"],
            kind=payload["kind"],
            status=payload["status"],
            checked_at=payload["checked_at"],
            details=payload["details"],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_json(receipt: Receipt) -> bytes:
    return (
        json.dumps(
            receipt.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def receipt_digest(receipt: Receipt) -> str:
    return hashlib.sha256(canonical_json(receipt)).hexdigest()


def write_bytes_atomic(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, mode)
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def write_receipt(path: Path, receipt: Receipt) -> None:
    write_bytes_atomic(Path(path), canonical_json(receipt))


def read_receipt(path: Path) -> Receipt:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReceiptError(f"receipt does not exist: {Path(path).name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"receipt is not valid JSON: {Path(path).name}") from exc
    return Receipt.from_dict(payload)


def require_fresh(
    receipt: Receipt,
    *,
    max_age: timedelta,
    now: datetime | None = None,
    future_tolerance: timedelta = timedelta(seconds=5),
) -> None:
    if max_age.total_seconds() <= 0:
        raise ReceiptError("max_age must be positive")
    reference = now or utc_now()
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ReceiptError("reference time must include a timezone")
    reference = reference.astimezone(timezone.utc)
    checked_at = parse_utc(receipt.checked_at)
    if checked_at > reference + future_tolerance:
        raise ReceiptError(f"{receipt.kind} receipt is dated in the future")
    if reference - checked_at > max_age:
        raise ReceiptError(f"{receipt.kind} receipt is stale")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
