from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reliable_agent_ops.backup import BackupError, create_backup
from reliable_agent_ops.receipts import sha256_file
from reliable_agent_ops.restore import RestoreError, verify_restore


class BackupRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.output = self.root / "backups"
        self.receipts = self.root / "receipts"
        self.source.mkdir()
        (self.source / "state.json").write_text('{"status":"healthy"}\n', encoding="utf-8")
        nested = self.source / "nested"
        nested.mkdir()
        (nested / "queue.txt").write_text("synthetic-item\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_backup_and_isolated_restore_round_trip(self) -> None:
        backup = create_backup(
            self.source,
            self.output,
            self.receipts / "backup.json",
        )
        restored = verify_restore(
            backup.archive_path,
            self.root / "restore",
            self.receipts / "restore.json",
            network_disabled=True,
            preserve_restored=True,
        )
        self.assertEqual(restored.receipt.status, "passed")
        self.assertEqual(restored.receipt.details["archive_sha256"], backup.receipt.details["archive_sha256"])
        restored_text = {
            path.relative_to(path.parents[1]).as_posix(): path.read_text(encoding="utf-8")
            for path in restored.restored_files
        }
        self.assertIn("nested/queue.txt", restored_text)
        self.assertFalse(restored.receipt.details["isolation"]["live_volumes_touched"])

    def test_restore_requires_network_disabled_runtime(self) -> None:
        backup = create_backup(
            self.source,
            self.output,
            self.receipts / "backup.json",
        )
        with self.assertRaisesRegex(RestoreError, "network-disabled"):
            verify_restore(
                backup.archive_path,
                self.root / "restore",
                self.receipts / "restore.json",
                network_disabled=False,
            )

    def test_archive_tampering_is_rejected(self) -> None:
        backup = create_backup(
            self.source,
            self.output,
            self.receipts / "backup.json",
        )
        with backup.archive_path.open("ab") as handle:
            handle.write(b"tampered")
        with self.assertRaisesRegex(RestoreError, "checksum"):
            verify_restore(
                backup.archive_path,
                self.root / "restore",
                self.receipts / "restore.json",
                network_disabled=True,
            )

    def test_symlink_in_source_is_rejected(self) -> None:
        os.symlink(self.source / "state.json", self.source / "linked.json")
        with self.assertRaisesRegex(BackupError, "Symbolic|symbolic"):
            create_backup(
                self.source,
                self.output,
                self.receipts / "backup.json",
            )

    def test_source_and_output_must_not_overlap(self) -> None:
        with self.assertRaisesRegex(BackupError, "overlap"):
            create_backup(
                self.source,
                self.source / "backups",
                self.receipts / "backup.json",
            )

    def test_retention_removes_only_old_archives(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for offset in range(3):
            create_backup(
                self.source,
                self.output,
                self.receipts / "backup.json",
                retention=2,
                created_at=start + timedelta(seconds=offset),
            )
        self.assertEqual(len(list(self.output.glob("agent-ops-*.tar.gz"))), 2)
        self.assertEqual(len(list(self.output.glob("agent-ops-*.tar.gz.sha256"))), 2)
        self.assertTrue((self.output / ".backup.lock").exists())

    def test_path_traversal_archive_is_rejected(self) -> None:
        archive_path = self.output / "agent-ops-malicious.tar.gz"
        self.output.mkdir()
        manifest = {
            "schema_version": "1.0",
            "backup_id": "synthetic",
            "created_at": "2026-01-01T00:00:00Z",
            "source_label": "synthetic-agent-state",
            "files": [],
        }
        with tarfile.open(archive_path, "w:gz") as archive:
            manifest_bytes = json.dumps(manifest).encode("utf-8")
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.size = len(manifest_bytes)
            archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
            unsafe = tarfile.TarInfo("payload/../../outside.txt")
            unsafe.size = 1
            archive.addfile(unsafe, io.BytesIO(b"x"))
        digest = sha256_file(archive_path)
        archive_path.with_name(f"{archive_path.name}.sha256").write_text(
            f"{digest}  {archive_path.name}\n",
            encoding="ascii",
        )
        with self.assertRaisesRegex(RestoreError, "unsafe path"):
            verify_restore(
                archive_path,
                self.root / "restore",
                self.receipts / "restore.json",
                network_disabled=True,
            )

    def test_unmanifested_payload_is_rejected_before_extraction(self) -> None:
        archive_path = self.output / "agent-ops-extra.tar.gz"
        self.output.mkdir()
        manifest = {
            "schema_version": "1.0",
            "backup_id": "synthetic",
            "created_at": "2026-01-01T00:00:00Z",
            "source_label": "synthetic-agent-state",
            "files": [],
        }
        with tarfile.open(archive_path, "w:gz") as archive:
            payload = tarfile.TarInfo("payload")
            payload.type = tarfile.DIRTYPE
            archive.addfile(payload)
            manifest_bytes = json.dumps(manifest).encode("utf-8")
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.size = len(manifest_bytes)
            archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
            extra = tarfile.TarInfo("payload/extra.txt")
            extra.size = 1
            archive.addfile(extra, io.BytesIO(b"x"))
        digest = sha256_file(archive_path)
        archive_path.with_name(f"{archive_path.name}.sha256").write_text(
            f"{digest}  {archive_path.name}\n",
            encoding="ascii",
        )
        with self.assertRaisesRegex(RestoreError, "does not match the manifest"):
            verify_restore(
                archive_path,
                self.root / "restore",
                self.receipts / "restore.json",
                network_disabled=True,
            )
        self.assertFalse((self.root / "restore" / "extra.txt").exists())


if __name__ == "__main__":
    unittest.main()
