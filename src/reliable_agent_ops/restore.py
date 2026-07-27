from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .receipts import Receipt, ReceiptError, parse_utc, sha256_file, write_receipt

MAX_ARCHIVE_MEMBERS = 2_000
MAX_RESTORED_BYTES = 100 * 1024 * 1024
BACKUP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RestoreError(RuntimeError):
    """Raised when an archive or its isolation evidence cannot be trusted."""


@dataclass(frozen=True)
class RestoreResult:
    receipt: Receipt
    restored_files: tuple[Path, ...]


def _safe_relative_path(name: str, *, prefix: str | None = None) -> PurePosixPath:
    if not name or "\\" in name:
        raise RestoreError("archive path is empty or uses backslashes")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise RestoreError("archive contains an unsafe path")
    if prefix is not None:
        if not path.parts or path.parts[0] != prefix:
            raise RestoreError(f"archive member must be under {prefix}/")
        path = PurePosixPath(*path.parts[1:])
        if not path.parts:
            raise RestoreError("payload entry must have a relative path")
    return path


def _read_checksum(checksum_path: Path, archive_name: str) -> str:
    try:
        parts = checksum_path.read_text(encoding="ascii").strip().split()
    except (FileNotFoundError, OSError, UnicodeDecodeError) as exc:
        raise RestoreError("archive checksum file is missing or unreadable") from exc
    if len(parts) != 2 or parts[1] != archive_name:
        raise RestoreError("archive checksum file has an unexpected format")
    digest = parts[0].lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise RestoreError("archive checksum is not SHA-256")
    return digest


def _load_manifest(archive: tarfile.TarFile, members: list[tarfile.TarInfo]) -> dict[str, Any]:
    manifest_members = [member for member in members if member.name == "manifest.json"]
    if len(manifest_members) != 1 or not manifest_members[0].isreg():
        raise RestoreError("archive must contain one regular manifest.json")
    manifest_member = manifest_members[0]
    if manifest_member.size > 1024 * 1024:
        raise RestoreError("manifest is unexpectedly large")
    extracted = archive.extractfile(manifest_member)
    if extracted is None:
        raise RestoreError("manifest could not be read")
    try:
        payload = json.loads(extracted.read())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RestoreError("manifest is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RestoreError("manifest root must be an object")
    required = {"schema_version", "backup_id", "created_at", "source_label", "files"}
    if set(payload) != required:
        raise RestoreError("manifest fields are invalid")
    if payload.get("schema_version") != "1.0":
        raise RestoreError("manifest schema_version is unsupported")
    if not isinstance(payload.get("backup_id"), str) or not BACKUP_ID.fullmatch(
        payload["backup_id"]
    ):
        raise RestoreError("manifest backup_id is missing")
    try:
        parse_utc(payload["created_at"])
    except ReceiptError as exc:
        raise RestoreError("manifest created_at is invalid") from exc
    if (
        not isinstance(payload["source_label"], str)
        or not payload["source_label"]
        or len(payload["source_label"]) > 128
    ):
        raise RestoreError("manifest source_label is invalid")
    files = payload.get("files")
    if not isinstance(files, list):
        raise RestoreError("manifest files must be a list")
    return payload


def _validated_manifest_entries(manifest: dict[str, Any]) -> dict[str, tuple[str, int]]:
    entries: dict[str, tuple[str, int]] = {}
    for raw_entry in manifest["files"]:
        if not isinstance(raw_entry, dict) or set(raw_entry) != {"path", "sha256", "size"}:
            raise RestoreError("manifest file entry has unexpected fields")
        raw_path = raw_entry["path"]
        if not isinstance(raw_path, str):
            raise RestoreError("manifest path must be a string")
        path = _safe_relative_path(raw_path).as_posix()
        digest = raw_entry["sha256"]
        size = raw_entry["size"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest.lower())
        ):
            raise RestoreError("manifest contains an invalid SHA-256 digest")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise RestoreError("manifest contains an invalid file size")
        if path in entries:
            raise RestoreError("manifest contains duplicate paths")
        entries[path] = (digest.lower(), size)
    if sum(size for _, size in entries.values()) > MAX_RESTORED_BYTES:
        raise RestoreError("manifest exceeds the restore size limit")
    return entries


def _preflight_payload(
    members: list[tarfile.TarInfo],
    manifest_entries: dict[str, tuple[str, int]],
) -> None:
    seen_paths: set[str] = set()
    observed_files: dict[str, int] = {}
    payload_roots = 0
    total_size = 0
    for member in members:
        if member.name == "manifest.json":
            continue
        if member.name.rstrip("/") == "payload":
            payload_roots += 1
            if not member.isdir():
                raise RestoreError("payload root must be a directory")
            continue
        relative = _safe_relative_path(member.name, prefix="payload").as_posix()
        if relative in seen_paths:
            raise RestoreError("archive contains duplicate payload paths")
        seen_paths.add(relative)
        if member.isdir():
            continue
        if not member.isreg():
            raise RestoreError("archive contains links or special files")
        if member.size < 0:
            raise RestoreError("archive contains an invalid file size")
        total_size += member.size
        if total_size > MAX_RESTORED_BYTES:
            raise RestoreError("archive exceeds the restore size limit")
        observed_files[relative] = member.size
    if payload_roots != 1:
        raise RestoreError("archive must contain one payload directory")
    expected_sizes = {path: size for path, (_, size) in manifest_entries.items()}
    if observed_files != expected_sizes:
        raise RestoreError("archive payload does not match the manifest")
    file_paths = set(observed_files)
    for path in file_paths:
        parts = PurePosixPath(path).parts
        for length in range(1, len(parts)):
            if PurePosixPath(*parts[:length]).as_posix() in file_paths:
                raise RestoreError("archive contains a file used as a parent directory")


def _extract_payload(
    archive: tarfile.TarFile,
    members: list[tarfile.TarInfo],
    destination: Path,
    manifest_entries: dict[str, tuple[str, int]],
) -> tuple[Path, ...]:
    seen_names: set[str] = set()
    observed_files: dict[str, tuple[str, int]] = {}
    restored: list[Path] = []
    for member in members:
        if member.name == "manifest.json" or member.name.rstrip("/") == "payload":
            continue
        if member.name in seen_names:
            raise RestoreError("archive contains duplicate member names")
        seen_names.add(member.name)
        relative = _safe_relative_path(member.name, prefix="payload")
        destination_path = destination.joinpath(*relative.parts)
        if member.isdir():
            destination_path.mkdir(parents=True, exist_ok=True)
            os.chmod(destination_path, 0o755)
            continue
        if not member.isreg():
            raise RestoreError("archive contains links or special files")
        if member.size < 0 or member.size > MAX_RESTORED_BYTES:
            raise RestoreError("archive member exceeds the restore size limit")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise RestoreError("archive member could not be read")
        digest = hashlib.sha256()
        written = 0
        with destination_path.open("xb") as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                written += len(chunk)
                if written > member.size or written > MAX_RESTORED_BYTES:
                    raise RestoreError("archive member size changed during extraction")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(destination_path, stat.S_IRUSR | stat.S_IWUSR)
        observed_files[relative.as_posix()] = (digest.hexdigest(), written)
        restored.append(destination_path)
    if observed_files != manifest_entries:
        raise RestoreError("restored files do not match the manifest")
    return tuple(restored)


def verify_restore(
    archive_path: Path,
    restore_root: Path,
    receipt_path: Path,
    *,
    network_disabled: bool,
    checked_at: datetime | None = None,
    preserve_restored: bool = False,
) -> RestoreResult:
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise RestoreError("backup archive does not exist")
    checksum_path = archive_path.with_name(f"{archive_path.name}.sha256")
    expected_archive_sha = _read_checksum(checksum_path, archive_path.name)
    actual_archive_sha = sha256_file(archive_path)
    if actual_archive_sha != expected_archive_sha:
        raise RestoreError("backup archive checksum does not match")
    if not network_disabled:
        raise RestoreError("restore verification requires a network-disabled runtime")

    root = Path(restore_root)
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".verified-restore-", dir=root))
    restored_files: tuple[Path, ...] = ()
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise RestoreError("archive member count is outside the allowed range")
            manifest = _load_manifest(archive, members)
            entries = _validated_manifest_entries(manifest)
            _preflight_payload(members, entries)
            restored_files = _extract_payload(archive, members, temporary, entries)
        manifest_digest = hashlib.sha256(
            (
                json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False)
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        receipt = Receipt.create(
            "restore",
            "passed",
            {
                "backup_id": manifest["backup_id"],
                "archive": archive_path.name,
                "archive_sha256": actual_archive_sha,
                "manifest_sha256": manifest_digest,
                "restored_file_count": len(restored_files),
                "restored_bytes": sum(path.stat().st_size for path in restored_files),
                "isolation": {
                    "network_mode": "none",
                    "live_volumes_touched": False,
                    "restore_target": "ephemeral",
                },
            },
            checked_at=checked_at,
        )
        write_receipt(Path(receipt_path), receipt)
        result = RestoreResult(receipt, restored_files)
        if preserve_restored:
            return result
        restored_files = ()
        return RestoreResult(receipt, restored_files)
    finally:
        if not preserve_restored:
            shutil.rmtree(temporary, ignore_errors=True)
