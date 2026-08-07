import subprocess
import sys

from app.models import EvidenceCheck
from app.reviewer import EvidenceRunner


def test_file_exists_passes_and_missing_file_fails(tmp_path):
    (tmp_path / "exists.txt").write_text("ok", encoding="utf-8")
    records = EvidenceRunner().run(
        [
            EvidenceCheck(kind="file_exists", path="exists.txt"),
            EvidenceCheck(kind="file_exists", path="missing.txt"),
        ],
        tmp_path,
    )
    assert [record.passed for record in records] == [True, False]


def test_file_contains_passes_and_reports_mismatch(tmp_path):
    (tmp_path / "result.txt").write_text("needle", encoding="utf-8")
    records = EvidenceRunner().run(
        [
            EvidenceCheck(kind="file_contains", path="result.txt", contains="needle"),
            EvidenceCheck(kind="file_contains", path="result.txt", contains="other"),
        ],
        tmp_path,
    )
    assert records[0].passed is True
    assert records[1].passed is False
    assert "does not contain" in records[1].description


def test_file_contains_handles_directory_and_invalid_utf8(tmp_path):
    (tmp_path / "directory").mkdir()
    (tmp_path / "binary").write_bytes(b"\xff")
    checks = [
        EvidenceCheck(kind="file_contains", path="directory", contains="x"),
        EvidenceCheck(kind="file_contains", path="binary", contains="x"),
    ]
    assert [item.passed for item in EvidenceRunner().run(checks, tmp_path)] == [
        False,
        False,
    ]


def test_command_pass_and_failure_are_recorded(tmp_path):
    checks = [
        EvidenceCheck(kind="command", command=[sys.executable, "-c", "print('ok')"]),
        EvidenceCheck(kind="command", command=[sys.executable, "-c", "raise SystemExit(7)"]),
    ]
    records = EvidenceRunner().run(checks, tmp_path)
    assert records[0].passed is True
    assert records[0].exit_code == 0
    assert records[1].passed is False
    assert records[1].exit_code == 7


def test_missing_command_is_a_failed_record(tmp_path):
    record = EvidenceRunner().run(
        [EvidenceCheck(kind="command", command=["definitely-not-a-command"])],
        tmp_path,
    )[0]
    assert record.passed is False
    assert "could not run" in record.description


def test_command_timeout_is_a_failed_record(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    record = EvidenceRunner().run(
        [EvidenceCheck(kind="command", command=["slow"], timeout_seconds=1)],
        tmp_path,
    )[0]
    assert record.passed is False
    assert "TimeoutExpired" in record.description
