"""Evidence-producing verification for loop outcomes."""

import subprocess
from pathlib import Path
from typing import Protocol

from app.schema import Evidence, VerificationResult, VerifySpec


class Verifier(Protocol):
    def verify(self, workspace: str) -> VerificationResult:
        ...


class CommandVerifier:
    """Run trusted, user-authored acceptance commands in the workspace.

    Commands come from the loop YAML, never from model output. Every command is
    preserved as structured evidence, and an empty verifier fails closed.
    """

    def __init__(self, spec: VerifySpec):
        self.spec = spec

    def verify(self, workspace: str) -> VerificationResult:
        workspace_path = Path(workspace).resolve()
        if not self.spec.commands:
            evidence = Evidence(
                kind="configuration",
                passed=False,
                summary="No verification commands configured",
            )
            return VerificationResult(
                passed=False,
                evidence=[evidence],
                feedback=evidence.summary,
            )

        evidence = []
        for command in self.spec.commands:
            evidence.append(self._run(command, workspace_path))

        passed = all(item.passed for item in evidence)
        failed = [item for item in evidence if not item.passed]
        feedback = ""
        if failed:
            parts = []
            for item in failed:
                detail = item.output.strip()
                parts.append(
                    f"{item.summary}" + (f"\n{detail}" if detail else "")
                )
            feedback = "\n\n".join(parts)

        return VerificationResult(
            passed=passed,
            evidence=evidence,
            feedback=feedback,
        )

    def _run(self, command: str, workspace: Path) -> Evidence:
        try:
            result = subprocess.run(
                command,
                cwd=workspace,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.spec.timeout_seconds,
            )
            output = _truncate(
                result.stdout + result.stderr,
                self.spec.max_output_chars,
            )
            passed = result.returncode == 0
            return Evidence(
                kind="command",
                passed=passed,
                summary=(
                    f"Command passed: {command}"
                    if passed
                    else f"Command failed ({result.returncode}): {command}"
                ),
                command=command,
                exit_code=result.returncode,
                output=output,
            )
        except subprocess.TimeoutExpired as exc:
            output = _timeout_output(exc, self.spec.max_output_chars)
            return Evidence(
                kind="command",
                passed=False,
                summary=(
                    f"Command timed out after {self.spec.timeout_seconds}s: {command}"
                ),
                command=command,
                output=output,
            )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n... output truncated ...\n" + text[-half:]


def _timeout_output(exc: subprocess.TimeoutExpired, limit: int) -> str:
    stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
    stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    return _truncate(stdout + stderr, limit)
