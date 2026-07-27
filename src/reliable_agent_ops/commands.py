from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CommandValidationError(ValueError):
    """Raised before an unsafe value can reach an external process."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class AllowlistedCommandRunner:
    """Runs fixed command prefixes with validated identifier arguments.

    This small adapter is an integration seam for optional validators. The public
    demo does not need an external command, but the boundary is tested so future
    integrations cannot silently fall back to shell interpolation.
    """

    def __init__(
        self,
        commands: Mapping[str, Sequence[str]],
        *,
        working_directory: Path,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not commands:
            raise CommandValidationError("at least one command must be allowlisted")
        validated: dict[str, tuple[str, ...]] = {}
        for name, prefix in commands.items():
            if not IDENTIFIER.fullmatch(name):
                raise CommandValidationError("command names must be safe identifiers")
            argv = tuple(prefix)
            if not argv or any(not isinstance(value, str) or not value for value in argv):
                raise CommandValidationError("command prefixes must contain non-empty strings")
            validated[name] = argv
        working_directory = Path(working_directory).resolve(strict=True)
        if not working_directory.is_dir():
            raise CommandValidationError("working_directory must be a directory")
        if not (0 < timeout_seconds <= 60):
            raise CommandValidationError("timeout_seconds must be between 0 and 60")
        self._commands = validated
        self._working_directory = working_directory
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def validate_identifier(value: str) -> str:
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            raise CommandValidationError(
                "external-command identifiers must be alphanumeric slugs and cannot "
                "start with an option marker"
            )
        return value

    def run(self, command_name: str, identifiers: Sequence[str]) -> CommandResult:
        if command_name not in self._commands:
            raise CommandValidationError("command is not allowlisted")
        safe_identifiers = [self.validate_identifier(value) for value in identifiers]
        completed = subprocess.run(
            [*self._commands[command_name], *safe_identifiers],
            cwd=self._working_directory,
            env={"PATH": os.environ.get("PATH", "")},
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)
