"""Trusted, shell-free evidence evaluation for NanoCodex."""

import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable, List

from app.models import EvidenceCheck, EvidenceKind, EvidenceRecord


class EvidenceRunner:
    def run(
        self,
        checks: Iterable[EvidenceCheck],
        workspace: str | Path,
    ) -> List[EvidenceRecord]:
        root = Path(workspace).resolve()
        return [self._run_one(check, root) for check in checks]

    def _run_one(self, check: EvidenceCheck, root: Path) -> EvidenceRecord:
        if check.kind == EvidenceKind.COMMAND:
            return self._run_command(check, root)
        assert check.path is not None
        target = (root / PurePosixPath(check.path)).resolve()
        if target != root and root not in target.parents:
            return EvidenceRecord(
                kind=check.kind,
                passed=False,
                description=f"unsafe evidence path rejected: {check.path}",
            )
        if check.kind == EvidenceKind.FILE_EXISTS:
            passed = target.is_file()
            return EvidenceRecord(
                kind=check.kind,
                passed=passed,
                description=f"{check.path} {'exists' if passed else 'is missing'}",
            )
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            return EvidenceRecord(
                kind=check.kind,
                passed=False,
                description=f"cannot read {check.path}: {type(error).__name__}",
            )
        assert check.contains is not None
        passed = check.contains in content
        return EvidenceRecord(
            kind=check.kind,
            passed=passed,
            description=(
                f"{check.path} contains required text"
                if passed
                else f"{check.path} does not contain required text"
            ),
        )

    @staticmethod
    def _run_command(check: EvidenceCheck, root: Path) -> EvidenceRecord:
        try:
            completed = subprocess.run(
                check.command,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=check.timeout_seconds,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return EvidenceRecord(
                kind=check.kind,
                passed=False,
                description=f"command could not run: {type(error).__name__}",
            )
        passed = completed.returncode == 0
        return EvidenceRecord(
            kind=check.kind,
            passed=passed,
            exit_code=completed.returncode,
            description=(
                f"command {check.command[0]!r} passed"
                if passed
                else f"command {check.command[0]!r} exited {completed.returncode}"
            ),
        )
