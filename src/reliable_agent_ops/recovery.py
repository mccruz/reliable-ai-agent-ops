from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping

from .receipts import Receipt, ReceiptError, receipt_digest, require_fresh


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _freshness_blocker(
    receipt: Receipt,
    *,
    max_age: timedelta,
    now: datetime | None,
) -> str | None:
    try:
        require_fresh(receipt, max_age=max_age, now=now)
    except ReceiptError as exc:
        return str(exc)
    return None


def evaluate_recovery(
    *,
    failed_health: Receipt,
    backup: Receipt,
    restore: Receipt,
    recovered_health: Receipt,
    max_age: timedelta,
    checked_at: datetime | None = None,
) -> Receipt:
    blockers: list[str] = []
    expected = {
        "failed_health": (failed_health, "health", "failed"),
        "backup": (backup, "backup", "passed"),
        "restore": (restore, "restore", "passed"),
        "recovered_health": (recovered_health, "health", "passed"),
    }
    for label, (receipt, kind, status) in expected.items():
        if receipt.kind != kind:
            blockers.append(f"{label} has kind {receipt.kind!r}, expected {kind!r}")
        if receipt.status != status:
            blockers.append(f"{label} has status {receipt.status!r}, expected {status!r}")
        freshness = _freshness_blocker(receipt, max_age=max_age, now=checked_at)
        if freshness:
            blockers.append(f"{label}: {freshness}")

    failure_summary = failed_health.details.get("summary")
    failure_count = (
        failure_summary.get("failed") if isinstance(failure_summary, Mapping) else None
    )
    if (
        not isinstance(failure_count, int)
        or isinstance(failure_count, bool)
        or failure_count < 1
    ):
        blockers.append("failed_health does not prove an observed service failure")
    recovered_summary = recovered_health.details.get("summary")
    recovered_failure_count = (
        recovered_summary.get("failed") if isinstance(recovered_summary, Mapping) else None
    )
    if (
        not isinstance(recovered_failure_count, int)
        or isinstance(recovered_failure_count, bool)
        or recovered_failure_count != 0
    ):
        blockers.append("recovered_health does not prove all services recovered")

    isolation = restore.details.get("isolation")
    if not isinstance(isolation, Mapping):
        blockers.append("restore receipt has no isolation evidence")
    else:
        if isolation.get("network_mode") != "none":
            blockers.append("restore verification was not network-disabled")
        if isolation.get("live_volumes_touched") is not False:
            blockers.append("restore verification did not exclude live volumes")
        if isolation.get("restore_target") != "ephemeral":
            blockers.append("restore verification target was not ephemeral")

    backup_sha = backup.details.get("archive_sha256")
    restore_sha = restore.details.get("archive_sha256")
    if not _is_sha256(backup_sha) or backup_sha != restore_sha:
        blockers.append("restore evidence does not match the backup checksum")
    backup_id = backup.details.get("backup_id")
    restore_id = restore.details.get("backup_id")
    if not isinstance(backup_id, str) or not backup_id or backup_id != restore_id:
        blockers.append("restore evidence does not match the backup identifier")

    status = "passed" if not blockers else "failed"
    return Receipt.create(
        "recovery",
        status,
        {
            "blockers": blockers,
            "decision": "ready-for-human-review" if not blockers else "blocked",
            "automatic_recovery_executed": False,
            "evidence": {
                "failed_health": receipt_digest(failed_health),
                "backup": receipt_digest(backup),
                "restore": receipt_digest(restore),
                "recovered_health": receipt_digest(recovered_health),
            },
        },
        checked_at=checked_at,
    )


def evaluate_readiness(
    required: Mapping[str, tuple[str, Receipt]],
    *,
    max_age: timedelta,
    checked_at: datetime | None = None,
) -> Receipt:
    if not required:
        raise ValueError("at least one readiness control is required")
    blockers: list[str] = []
    controls: list[dict[str, str]] = []
    by_kind: dict[str, Receipt] = {}
    for label, (expected_kind, receipt) in required.items():
        control_status = "passed"
        if receipt.kind != expected_kind:
            blockers.append(f"{label} has kind {receipt.kind!r}, expected {expected_kind!r}")
            control_status = "failed"
        if receipt.status != "passed":
            blockers.append(f"{label} has status {receipt.status!r}")
            control_status = "failed"
        freshness = _freshness_blocker(receipt, max_age=max_age, now=checked_at)
        if freshness:
            blockers.append(f"{label}: {freshness}")
            control_status = "failed"
        controls.append(
            {
                "label": label,
                "kind": receipt.kind,
                "status": control_status,
                "receipt_sha256": receipt_digest(receipt),
            }
        )
        by_kind[expected_kind] = receipt

    backup = by_kind.get("backup")
    restore = by_kind.get("restore")
    if backup is not None and restore is not None:
        if backup.details.get("archive_sha256") != restore.details.get("archive_sha256"):
            blockers.append("backup and restore evidence reference different checksums")
    recovery = by_kind.get("recovery")
    health = by_kind.get("health")
    if recovery is not None and restore is not None:
        evidence = recovery.details.get("evidence")
        if not isinstance(evidence, Mapping) or evidence.get("restore") != receipt_digest(restore):
            blockers.append("recovery evidence does not reference the required restore receipt")
        if health is not None and (
            not isinstance(evidence, Mapping)
            or evidence.get("recovered_health") != receipt_digest(health)
        ):
            blockers.append("recovery evidence does not reference the required health receipt")

    status = "passed" if not blockers else "failed"
    return Receipt.create(
        "readiness",
        status,
        {
            "blockers": blockers,
            "controls": controls,
            "decision": "ready-for-human-review" if not blockers else "blocked",
            "automatic_action_authorized": False,
        },
        checked_at=checked_at,
    )
