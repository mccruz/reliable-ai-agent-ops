from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from reliable_agent_ops.notifications import (
    DryRunNotifier,
    NotificationError,
    NotificationEvent,
)
from reliable_agent_ops.updates import UpdateCheckError, check_update


class UpdateAndNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manifest(self, latest: str = "1.2.0") -> Path:
        path = self.root / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "project": "synthetic-agent",
                    "latest_version": latest,
                    "release_url": "https://example.invalid/releases/1.2.0",
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_update_available_is_reported_without_network(self) -> None:
        receipt = check_update("1.0.0", self._manifest())
        self.assertEqual(receipt.status, "passed")
        self.assertTrue(receipt.details["update_available"])
        self.assertEqual(receipt.details["source"], "local-synthetic-manifest")

    def test_invalid_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(UpdateCheckError, "major.minor.patch"):
            check_update("latest", self._manifest())

    def test_release_url_cannot_contain_credentials(self) -> None:
        path = self._manifest()
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["release_url"] = "https://user:pass@example.invalid/release"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(UpdateCheckError, "credential-free"):
            check_update("1.0.0", path)

    def test_dry_run_needs_no_credentials_and_does_not_deliver(self) -> None:
        output = self.root / "notification.json"
        receipt = DryRunNotifier(output).send(
            NotificationEvent("backup-complete", "Synthetic backup is ready.")
        )
        self.assertFalse(receipt.details["credential_required"])
        self.assertFalse(receipt.details["delivered_externally"])
        self.assertEqual(json.loads(output.read_text())["channel"], "dry-run")

    def test_sensitive_metadata_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(NotificationError, "sensitive"):
            NotificationEvent("backup-complete", "Ready", {"api_key": "placeholder"})

    def test_nested_sensitive_metadata_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(NotificationError, "sensitive"):
            NotificationEvent(
                "backup-complete",
                "Ready",
                {"context": {"access_token": "placeholder"}},
            )

    def test_release_url_query_is_rejected(self) -> None:
        path = self._manifest()
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["release_url"] = "https://example.invalid/release?token=placeholder"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(UpdateCheckError, "credential-free"):
            check_update("1.0.0", path)


if __name__ == "__main__":
    unittest.main()
