import pytest

from app.builder import build_nano_worker
from app.schema import WorkerSpec
from app.worker import NanoEngineWorker, WorkerConfigurationError


class FakeEngine:
    def __init__(self):
        self.query = ""

    def run(self, query):
        self.query = query
        return {
            "summary": {"success": True},
            "trajectory": [{"thought": "implemented"}],
        }


def test_nano_worker_builds_fresh_engine_and_structured_prompt():
    engines = []

    def factory(workspace, iteration, run_id):
        engine = FakeEngine()
        engines.append((engine, workspace, iteration, run_id))
        return engine

    worker = NanoEngineWorker(factory)
    worker.run("contract", "task", "first failure", "/work", 1, "run-1")
    worker.run("contract", "task", "", "/work", 2, "run-1")

    assert engines[0][0] is not engines[1][0]
    assert engines[0][1:] == ("/work", 1, "run-1")
    assert "Loop contract:\ncontract" in engines[0][0].query
    assert "Concrete task:\ntask" in engines[0][0].query
    assert "first failure" in engines[0][0].query


def test_default_worker_reports_missing_api_key_as_configuration_error(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    worker = build_nano_worker(WorkerSpec(), str(tmp_path / "runtime"))
    with pytest.raises(WorkerConfigurationError, match="DEEPSEEK_API_KEY"):
        worker.run("contract", "task", "", str(tmp_path), 1, "run-1")
