from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from reliable_agent_ops.receipts import Receipt
from reliable_agent_ops.recovery import evaluate_readiness, evaluate_recovery


class RecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        self.failed_health = Receipt.create(
            "health",
            "failed",
            {"summary": {"checked": 1, "passed": 0, "failed": 1}},
            checked_at=self.now,
        )
        self.backup = Receipt.create(
            "backup",
            "passed",
            {
                "backup_id": "synthetic-1",
                "archive_sha256": "a" * 64,
            },
            checked_at=self.now,
        )
        self.restore = Receipt.create(
            "restore",
            "passed",
            {
                "backup_id": "synthetic-1",
                "archive_sha256": "a" * 64,
                "isolation": {
                    "network_mode": "none",
                    "live_volumes_touched": False,
                    "restore_target": "ephemeral",
                },
            },
            checked_at=self.now,
        )
        self.recovered = Receipt.create(
            "health",
            "passed",
            {"summary": {"checked": 1, "passed": 1, "failed": 0}},
            checked_at=self.now,
        )

    def _recovery(self) -> Receipt:
        return evaluate_recovery(
            failed_health=self.failed_health,
            backup=self.backup,
            restore=self.restore,
            recovered_health=self.recovered,
            max_age=timedelta(minutes=15),
            checked_at=self.now,
        )

    def test_verified_isolated_restore_allows_human_review(self) -> None:
        receipt = self._recovery()
        self.assertEqual(receipt.status, "passed")
        self.assertEqual(receipt.details["decision"], "ready-for-human-review")
        self.assertFalse(receipt.details["automatic_recovery_executed"])

    def test_non_isolated_restore_blocks_recovery(self) -> None:
        restore = Receipt.create(
            "restore",
            "passed",
            {
                **self.restore.details,
                "isolation": {
                    "network_mode": "bridge",
                    "live_volumes_touched": False,
                    "restore_target": "ephemeral",
                },
            },
            checked_at=self.now,
        )
        receipt = evaluate_recovery(
            failed_health=self.failed_health,
            backup=self.backup,
            restore=restore,
            recovered_health=self.recovered,
            max_age=timedelta(minutes=15),
            checked_at=self.now,
        )
        self.assertEqual(receipt.status, "failed")
        self.assertIn("network-disabled", " ".join(receipt.details["blockers"]))

    def test_checksum_mismatch_blocks_recovery(self) -> None:
        restore = Receipt.create(
            "restore",
            "passed",
            {**self.restore.details, "archive_sha256": "b" * 64},
            checked_at=self.now,
        )
        receipt = evaluate_recovery(
            failed_health=self.failed_health,
            backup=self.backup,
            restore=restore,
            recovered_health=self.recovered,
            max_age=timedelta(minutes=15),
            checked_at=self.now,
        )
        self.assertEqual(receipt.status, "failed")
        self.assertIn("checksum", " ".join(receipt.details["blockers"]))

    def test_stale_evidence_blocks_recovery(self) -> None:
        receipt = evaluate_recovery(
            failed_health=self.failed_health,
            backup=self.backup,
            restore=self.restore,
            recovered_health=self.recovered,
            max_age=timedelta(minutes=15),
            checked_at=self.now + timedelta(hours=1),
        )
        self.assertEqual(receipt.status, "failed")
        self.assertIn("stale", " ".join(receipt.details["blockers"]))

    def test_readiness_cross_checks_recovery_evidence(self) -> None:
        recovery = self._recovery()
        notification = Receipt.create(
            "notification", "passed", {}, checked_at=self.now
        )
        update = Receipt.create("update", "passed", {}, checked_at=self.now)
        readiness = evaluate_readiness(
            {
                "health": ("health", self.recovered),
                "backup": ("backup", self.backup),
                "restore": ("restore", self.restore),
                "notification": ("notification", notification),
                "update": ("update", update),
                "recovery": ("recovery", recovery),
            },
            max_age=timedelta(minutes=15),
            checked_at=self.now,
        )
        self.assertEqual(readiness.status, "passed")
        self.assertFalse(readiness.details["automatic_action_authorized"])

    def test_failed_control_blocks_readiness(self) -> None:
        readiness = evaluate_readiness(
            {"failed health": ("health", self.failed_health)},
            max_age=timedelta(minutes=15),
            checked_at=self.now,
        )
        self.assertEqual(readiness.status, "failed")
        self.assertEqual(readiness.details["decision"], "blocked")

    def test_malformed_failure_count_blocks_without_crashing(self) -> None:
        malformed = Receipt.create(
            "health",
            "failed",
            {"summary": {"checked": 1, "passed": 0, "failed": "one"}},
            checked_at=self.now,
        )
        receipt = evaluate_recovery(
            failed_health=malformed,
            backup=self.backup,
            restore=self.restore,
            recovered_health=self.recovered,
            max_age=timedelta(minutes=15),
            checked_at=self.now,
        )
        self.assertEqual(receipt.status, "failed")
        self.assertIn("observed service failure", " ".join(receipt.details["blockers"]))


if __name__ == "__main__":
    unittest.main()
