from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

MAX_OWNER_ID = (2**31) - 1
MAX_STORAGE_ENTRIES = 10_000


class StoragePreparationError(RuntimeError):
    """Raised when the demo storage initializer receives an unsafe target."""


@dataclass(frozen=True)
class StoragePreparationResult:
    roots: tuple[str, ...]
    entry_count: int
    owner_uid: int
    owner_gid: int


def _validate_owner_id(value: int, *, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoragePreparationError(f"{label} must be an integer")
    if value < 1 or value > MAX_OWNER_ID:
        raise StoragePreparationError(
            f"{label} must identify a non-root user between 1 and {MAX_OWNER_ID}"
        )


def _is_relative_to(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_roots(
    roots: Sequence[Path],
    *,
    require_mounts: bool,
) -> tuple[Path, ...]:
    if not roots:
        raise StoragePreparationError("at least one storage root is required")
    if len(roots) > 8:
        raise StoragePreparationError("too many storage roots")

    validated: list[Path] = []
    for raw_root in roots:
        root = Path(raw_root)
        try:
            metadata = root.lstat()
        except FileNotFoundError as exc:
            raise StoragePreparationError(
                f"storage root does not exist: {root}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise StoragePreparationError(
                f"storage root cannot be a symbolic link: {root}"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise StoragePreparationError(f"storage root must be a directory: {root}")
        resolved = root.resolve(strict=True)
        if resolved == Path(resolved.anchor):
            raise StoragePreparationError("filesystem roots cannot be storage targets")
        if require_mounts and not os.path.ismount(resolved):
            raise StoragePreparationError(
                f"storage root must be a dedicated container mount: {root}"
            )
        validated.append(resolved)

    if len(set(validated)) != len(validated):
        raise StoragePreparationError("storage roots must be unique")
    for index, candidate in enumerate(validated):
        for other in validated[index + 1 :]:
            if _is_relative_to(candidate, other) or _is_relative_to(other, candidate):
                raise StoragePreparationError("storage roots must not overlap")
    return tuple(validated)


def _raise_walk_error(error: OSError) -> None:
    raise StoragePreparationError("could not inspect demo storage completely") from error


def _collect_entries(root: Path) -> tuple[Path, ...]:
    entries: list[Path] = [root]
    for current, directory_names, file_names in os.walk(
        root,
        followlinks=False,
        onerror=_raise_walk_error,
    ):
        current_path = Path(current)
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            path = current_path / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise StoragePreparationError(
                    f"symbolic links are not allowed in demo storage: {path.name}"
                )
            if not stat.S_ISDIR(metadata.st_mode):
                raise StoragePreparationError(
                    f"unexpected non-directory in demo storage: {path.name}"
                )
            entries.append(path)
        for name in file_names:
            path = current_path / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise StoragePreparationError(
                    f"symbolic links are not allowed in demo storage: {path.name}"
                )
            if not stat.S_ISREG(metadata.st_mode):
                raise StoragePreparationError(
                    f"only regular files are allowed in demo storage: {path.name}"
                )
            entries.append(path)
        if len(entries) > MAX_STORAGE_ENTRIES:
            raise StoragePreparationError(
                f"demo storage exceeds the {MAX_STORAGE_ENTRIES}-entry limit"
            )
    return tuple(entries)


def prepare_demo_storage(
    roots: Sequence[Path],
    *,
    owner_uid: int,
    owner_gid: int,
    require_mounts: bool = True,
) -> StoragePreparationResult:
    """Assign dedicated synthetic mounts to the non-root demo owner.

    The caller is expected to run inside the short-lived Compose initializer with
    only CHOWN and DAC_OVERRIDE available. Targets are validated completely before
    ownership changes begin, and roots are changed last to reduce race exposure.
    """

    _validate_owner_id(owner_uid, label="owner UID")
    _validate_owner_id(owner_gid, label="owner GID")
    validated_roots = _validate_roots(roots, require_mounts=require_mounts)

    entries: list[Path] = []
    for root in validated_roots:
        entries.extend(_collect_entries(root))
        if len(entries) > MAX_STORAGE_ENTRIES:
            raise StoragePreparationError(
                f"demo storage exceeds the {MAX_STORAGE_ENTRIES}-entry limit"
            )

    for path in reversed(entries):
        os.chown(path, owner_uid, owner_gid, follow_symlinks=False)

    return StoragePreparationResult(
        roots=tuple(str(root) for root in validated_roots),
        entry_count=len(entries),
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )
