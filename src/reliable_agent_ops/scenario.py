from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path

from .backup import create_backup
from .health import ServiceSpec, check_services
from .notifications import DryRunNotifier, NotificationEvent
from .receipts import ReceiptError, read_receipt, write_bytes_atomic, write_receipt
from .recovery import evaluate_readiness, evaluate_recovery
from .updates import check_update


class ScenarioError(RuntimeError):
    """Raised when the deterministic recovery scenario does not reach its gate."""


def set_synthetic_state(state_file: Path, status: str) -> None:
    if status not in {"healthy", "unhealthy"}:
        raise ScenarioError("synthetic state must be healthy or unhealthy")
    payload = {"service": "synthetic-agent", "status": status}
    write_bytes_atomic(
        Path(state_file),
        (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"),
    )


def run_prepare(
    *,
    service_url: str,
    state_file: Path,
    artifacts_dir: Path,
    update_manifest: Path,
) -> None:
    artifacts = Path(artifacts_dir)
    receipts = artifacts / "receipts"
    backups = artifacts / "backups"
    receipts.mkdir(parents=True, exist_ok=True)
    backups.mkdir(parents=True, exist_ok=True)
    service = ServiceSpec("synthetic-agent", service_url)

    set_synthetic_state(state_file, "healthy")
    initial_health = check_services([service])
    if initial_health.status != "passed":
        raise ScenarioError("synthetic service did not start healthy")
    write_receipt(receipts / "health-initial.json", initial_health)

    create_backup(
        Path(state_file).parent,
        backups,
        receipts / "backup.json",
        retention=3,
    )

    update = check_update("1.0.0", update_manifest)
    write_receipt(receipts / "update.json", update)

    notification = DryRunNotifier(artifacts / "notification-dry-run.json").send(
        NotificationEvent(
            "update-check-complete",
            "Synthetic agent update evidence is ready for review.",
            {"update_available": update.details["update_available"]},
        )
    )
    write_receipt(receipts / "notification.json", notification)

    set_synthetic_state(state_file, "unhealthy")
    failed_health = check_services([service])
    if failed_health.status != "failed":
        raise ScenarioError("fault injection did not produce failed health evidence")
    write_receipt(receipts / "health-failed.json", failed_health)


def run_finish(
    *,
    service_url: str,
    state_file: Path,
    artifacts_dir: Path,
) -> None:
    artifacts = Path(artifacts_dir)
    receipts = artifacts / "receipts"
    service = ServiceSpec("synthetic-agent", service_url)
    max_age = timedelta(minutes=15)

    failed_health = read_receipt(receipts / "health-failed.json")
    backup = read_receipt(receipts / "backup.json")
    restore = read_receipt(receipts / "restore.json")
    update = read_receipt(receipts / "update.json")
    notification = read_receipt(receipts / "notification.json")
    if restore.status != "passed":
        raise ScenarioError("verified restore evidence is required before recovery simulation")

    # This represents a reviewed remediation step. The verified restore never writes
    # to this live synthetic volume.
    set_synthetic_state(state_file, "healthy")
    recovered_health = None
    for _ in range(10):
        candidate = check_services([service])
        if candidate.status == "passed":
            recovered_health = candidate
            break
        time.sleep(0.1)
    if recovered_health is None:
        raise ScenarioError("synthetic service did not recover")
    write_receipt(receipts / "health-recovered.json", recovered_health)

    recovery = evaluate_recovery(
        failed_health=failed_health,
        backup=backup,
        restore=restore,
        recovered_health=recovered_health,
        max_age=max_age,
    )
    write_receipt(receipts / "recovery.json", recovery)

    readiness = evaluate_readiness(
        {
            "recovered service health": ("health", recovered_health),
            "atomic backup": ("backup", backup),
            "isolated restore": ("restore", restore),
            "update check": ("update", update),
            "credential-free notification": ("notification", notification),
            "recovery evidence": ("recovery", recovery),
        },
        max_age=max_age,
    )
    write_receipt(receipts / "readiness.json", readiness)
    if recovery.status != "passed" or readiness.status != "passed":
        blockers = [*recovery.details["blockers"], *readiness.details["blockers"]]
        raise ScenarioError(f"scenario remained blocked: {'; '.join(blockers)}")

    summary = {
        "scenario": "synthetic-ai-agent-recovery",
        "status": "passed",
        "automatic_recovery_executed": False,
        "human_review_required": True,
        "controls": [
            "fault-isolated health checks",
            "atomic checksummed backup",
            "network-disabled restore verification",
            "credential-free update notification",
            "freshness-checked recovery evidence",
        ],
    }
    write_bytes_atomic(
        artifacts / "summary.json",
        (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        mode=0o644,
    )
    write_bytes_atomic(artifacts / "scenario-complete", b"passed\n", mode=0o644)


def summarize(artifacts_dir: Path) -> str:
    artifacts = Path(artifacts_dir)
    try:
        summary = json.loads((artifacts / "summary.json").read_text(encoding="utf-8"))
        readiness = read_receipt(artifacts / "receipts" / "readiness.json")
    except (FileNotFoundError, OSError, json.JSONDecodeError, ReceiptError) as exc:
        raise ScenarioError("demo output is missing or malformed") from exc
    return json.dumps(
        {
            "scenario": summary["scenario"],
            "status": summary["status"],
            "decision": readiness.details["decision"],
            "controls": summary["controls"],
        },
        indent=2,
        sort_keys=True,
    )
