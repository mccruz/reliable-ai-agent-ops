from __future__ import annotations

import fcntl
import gzip
import io
import json
import os
import secrets
import stat
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

from .receipts import Receipt, format_utc, sha256_file, utc_now, write_bytes_atomic, write_receipt

ARCHIVE_PREFIX = "agent-ops-"
ARCHIVE_SUFFIX = ".tar.gz"


class BackupError(RuntimeError):
    """Raised when a backup cannot be created safely."""


class BackupLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: BinaryIO | None = None

    def __enter__(self) -> "BackupLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            self.handle = None
            raise BackupError("another backup is already running") from exc
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


@dataclass(frozen=True)
class BackupResult:
    archive_path: Path
    checksum_path: Path
    receipt: Receipt


def _is_relative_to(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_non_overlapping(source: Path, output: Path) -> None:
    if source == output or _is_relative_to(output, source) or _is_relative_to(source, output):
        raise BackupError("source and output directories must not overlap")


def _collect_source(source: Path) -> tuple[list[Path], list[Path]]:
    directories: list[Path] = []
    files: list[Path] = []
    for root, directory_names, file_names in os.walk(source, followlinks=False):
        root_path = Path(root)
        directory_names.sort()
        file_names.sort()
        for directory_name in directory_names:
            path = root_path / directory_name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise BackupError(f"symbolic links are not allowed: {path.relative_to(source)}")
            if not stat.S_ISDIR(metadata.st_mode):
                raise BackupError(f"non-directory entry found in directory list: {path.name}")
            directories.append(path)
        for file_name in file_names:
            path = root_path / file_name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise BackupError(f"symbolic links are not allowed: {path.relative_to(source)}")
            if not stat.S_ISREG(metadata.st_mode):
                raise BackupError(f"only regular files may be backed up: {path.name}")
            files.append(path)
    return sorted(directories), sorted(files)


def _tar_info(name: str, *, directory: bool, size: int = 0) -> tarfile.TarInfo:
    normalized_name = f"{name.rstrip('/')}/" if directory else name
    info = tarfile.TarInfo(normalized_name)
    info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    info.mode = 0o755 if directory else 0o644
    info.size = size
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def _build_archive(
    archive_path: Path,
    *,
    source: Path,
    directories: list[Path],
    files: list[Path],
    backup_id: str,
    created_at: datetime,
) -> dict[str, object]:
    manifest_files = [
        {
            "path": path.relative_to(source).as_posix(),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
        for path in files
    ]
    manifest: dict[str, object] = {
        "schema_version": "1.0",
        "backup_id": backup_id,
        "created_at": format_utc(created_at),
        "source_label": "synthetic-agent-state",
        "files": manifest_files,
    }
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")

    with archive_path.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0) as gzip_handle:
            with tarfile.open(fileobj=gzip_handle, mode="w", format=tarfile.PAX_FORMAT) as archive:
                archive.addfile(_tar_info("payload", directory=True))
                for directory in directories:
                    relative = directory.relative_to(source).as_posix()
                    archive.addfile(_tar_info(f"payload/{relative}", directory=True))
                for path in files:
                    relative = path.relative_to(source).as_posix()
                    with path.open("rb") as source_handle:
                        archive.addfile(
                            _tar_info(
                                f"payload/{relative}",
                                directory=False,
                                size=path.stat().st_size,
                            ),
                            source_handle,
                        )
                archive.addfile(
                    _tar_info("manifest.json", directory=False, size=len(manifest_bytes)),
                    io.BytesIO(manifest_bytes),
                )
        raw_handle.flush()
        os.fsync(raw_handle.fileno())
    return manifest


def _apply_retention(output: Path, *, keep: int) -> list[str]:
    archives = sorted(output.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}"), reverse=True)
    removed: list[str] = []
    for archive in archives[keep:]:
        checksum = archive.with_name(f"{archive.name}.sha256")
        archive.unlink()
        checksum.unlink(missing_ok=True)
        removed.append(archive.name)
    return removed


def create_backup(
    source_dir: Path,
    output_dir: Path,
    receipt_path: Path,
    *,
    retention: int = 3,
    created_at: datetime | None = None,
) -> BackupResult:
    if retention < 1 or retention > 100:
        raise BackupError("retention must be between 1 and 100")
    try:
        source = Path(source_dir).resolve(strict=True)
    except FileNotFoundError as exc:
        raise BackupError("source directory does not exist") from exc
    if not source.is_dir():
        raise BackupError("source must be a directory")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    output = output.resolve(strict=True)
    _validate_non_overlapping(source, output)
    directories, files = _collect_source(source)

    timestamp = (created_at or utc_now()).astimezone(timezone.utc)
    backup_id = f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"
    archive_name = f"{ARCHIVE_PREFIX}{backup_id}{ARCHIVE_SUFFIX}"
    final_archive = output / archive_name
    final_checksum = output / f"{archive_name}.sha256"
    staged_archive = output / f".{archive_name}.{secrets.token_hex(4)}.tmp"

    with BackupLock(output / ".backup.lock"):
        published = False
        try:
            manifest = _build_archive(
                staged_archive,
                source=source,
                directories=directories,
                files=files,
                backup_id=backup_id,
                created_at=timestamp,
            )
            archive_sha256 = sha256_file(staged_archive)
            os.replace(staged_archive, final_archive)
            published = True
            write_bytes_atomic(
                final_checksum,
                f"{archive_sha256}  {archive_name}\n".encode("ascii"),
            )
            removed = _apply_retention(output, keep=retention)
            receipt = Receipt.create(
                "backup",
                "passed",
                {
                    "backup_id": backup_id,
                    "archive": archive_name,
                    "archive_sha256": archive_sha256,
                    "file_count": len(manifest["files"]),
                    "retention": retention,
                    "removed_archives": removed,
                },
                checked_at=timestamp,
            )
            write_receipt(Path(receipt_path), receipt)
        except BaseException:
            staged_archive.unlink(missing_ok=True)
            if published:
                final_archive.unlink(missing_ok=True)
                final_checksum.unlink(missing_ok=True)
            raise

    return BackupResult(final_archive, final_checksum, receipt)
