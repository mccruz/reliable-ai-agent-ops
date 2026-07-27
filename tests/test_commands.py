from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reliable_agent_ops.commands import (
    AllowlistedCommandRunner,
    CommandValidationError,
)


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runner = AllowlistedCommandRunner(
            {"validator": ("validator-tool", "--check")},
            working_directory=self.root,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @patch("reliable_agent_ops.commands.subprocess.run")
    def test_option_shaped_identifier_is_rejected_before_execution(self, run) -> None:
        with self.assertRaisesRegex(CommandValidationError, "option marker"):
            self.runner.run("validator", ["--host"])
        run.assert_not_called()

    @patch("reliable_agent_ops.commands.subprocess.run")
    def test_shell_is_never_used(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            ["validator-tool"], 0, stdout="ok\n", stderr=""
        )
        result = self.runner.run("validator", ["synthetic-agent"])
        self.assertEqual(result.returncode, 0)
        _, kwargs = run.call_args
        self.assertFalse(kwargs["shell"])
        self.assertEqual(
            run.call_args.args[0],
            ["validator-tool", "--check", "synthetic-agent"],
        )

    def test_unknown_command_is_rejected(self) -> None:
        with self.assertRaisesRegex(CommandValidationError, "allowlisted"):
            self.runner.run("other", ["synthetic-agent"])


if __name__ == "__main__":
    unittest.main()
