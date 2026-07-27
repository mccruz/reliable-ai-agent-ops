from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reliable_agent_ops.storage import (
    StoragePreparationError,
    prepare_demo_storage,
)


class DemoStorageTests(unittest.TestCase):
    def test_validates_tree_before_assigning_non_root_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            state = root / "state"
            restore = root / "restore"
            artifacts.mkdir()
            state.mkdir()
            restore.mkdir()
            receipts = artifacts / "receipts"
            receipts.mkdir()
            receipt = receipts / "health.json"
            receipt.write_text("{}\n", encoding="utf-8")

            with patch("reliable_agent_ops.storage.os.chown") as chown:
                result = prepare_demo_storage(
                    (artifacts, state, restore),
                    owner_uid=1000,
                    owner_gid=1000,
                    require_mounts=False,
                )

            self.assertEqual(result.entry_count, 5)
            self.assertEqual(result.owner_uid, 1000)
            self.assertEqual(result.owner_gid, 1000)
            changed_paths = {call.args[0] for call in chown.call_args_list}
            self.assertEqual(
                changed_paths,
                {
                    artifacts.resolve(),
                    receipts.resolve(),
                    receipt.resolve(),
                    state.resolve(),
                    restore.resolve(),
                },
            )
            self.assertTrue(
                all(
                    call.kwargs == {"follow_symlinks": False}
                    for call in chown.call_args_list
                )
            )

    def test_rejects_root_owner_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(StoragePreparationError, "non-root"):
                prepare_demo_storage(
                    (root,),
                    owner_uid=0,
                    owner_gid=os.getgid(),
                    require_mounts=False,
                )

    def test_rejects_overlapping_roots_before_chown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "nested"
            nested.mkdir()
            with patch("reliable_agent_ops.storage.os.chown") as chown:
                with self.assertRaisesRegex(StoragePreparationError, "overlap"):
                    prepare_demo_storage(
                        (root, nested),
                        owner_uid=1000,
                        owner_gid=1000,
                        require_mounts=False,
                    )
            chown.assert_not_called()

    def test_rejects_symbolic_links_before_chown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.txt"
            target.write_text("synthetic\n", encoding="utf-8")
            os.symlink(target, root / "linked.txt")
            with patch("reliable_agent_ops.storage.os.chown") as chown:
                with self.assertRaisesRegex(StoragePreparationError, "symbolic"):
                    prepare_demo_storage(
                        (root,),
                        owner_uid=1000,
                        owner_gid=1000,
                        require_mounts=False,
                    )
            chown.assert_not_called()

    def test_rejects_special_files_before_chown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.mkfifo(root / "synthetic.pipe")
            with patch("reliable_agent_ops.storage.os.chown") as chown:
                with self.assertRaisesRegex(StoragePreparationError, "regular files"):
                    prepare_demo_storage(
                        (root,),
                        owner_uid=1000,
                        owner_gid=1000,
                        require_mounts=False,
                    )
            chown.assert_not_called()

    def test_walk_error_stops_before_chown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def fail_walk(*args: object, **kwargs: object) -> object:
                onerror = kwargs["onerror"]
                assert callable(onerror)
                onerror(PermissionError("synthetic denial"))
                return iter(())

            with (
                patch("reliable_agent_ops.storage.os.walk", side_effect=fail_walk),
                patch("reliable_agent_ops.storage.os.chown") as chown,
            ):
                with self.assertRaisesRegex(StoragePreparationError, "completely"):
                    prepare_demo_storage(
                        (root,),
                        owner_uid=1000,
                        owner_gid=1000,
                        require_mounts=False,
                    )
            chown.assert_not_called()

    def test_requires_dedicated_mounts_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                StoragePreparationError,
                "dedicated container mount",
            ):
                prepare_demo_storage(
                    (Path(temporary),),
                    owner_uid=1000,
                    owner_gid=1000,
                )


if __name__ == "__main__":
    unittest.main()
