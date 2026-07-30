from pathlib import Path

from app.runner import LoopRunner
from app.schema import (
    Evidence,
    IterationRecord,
    LoopSpec,
    LoopState,
    LoopStatus,
    VerificationResult,
    WorkerResult,
    WorkspaceHandle,
)
from app.store import JsonLoopStore
from app.worker import WorkerConfigurationError


class FakeWorkspace:
    def __init__(self, path):
        self.path = str(path)
        self.calls = 0

    def create(self, run_id, repository, base_ref):
        self.calls += 1
        return WorkspaceHandle(path=self.path, branch=f"test/{run_id}", owned=True)


class FakeWorker:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def run(self, goal, task, feedback, workspace, iteration, run_id):
        self.calls.append(
            {
                "goal": goal,
                "task": task,
                "feedback": feedback,
                "workspace": workspace,
                "iteration": iteration,
                "run_id": run_id,
            }
        )
        if self.fail:
            raise RuntimeError("model unavailable")
        return WorkerResult(success=True, summary=f"iteration {iteration}")


class SequenceVerifier:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def verify(self, workspace):
        self.calls += 1
        return self.results.pop(0)


def verification(passed, feedback=""):
    return VerificationResult(
        passed=passed,
        feedback=feedback,
        evidence=[Evidence(kind="test", passed=passed, summary=feedback or "ok")],
    )


def spec(**overrides):
    data = {
        "name": "test-loop",
        "goal": "test",
        "workspace": {"type": "local"},
        "verify": {"commands": ["true"]},
        "budget": {
            "max_iterations": 3,
            "max_wall_seconds": 100,
            "max_consecutive_failures": 3,
        },
    }
    data.update(overrides)
    return LoopSpec.model_validate(data)


def build_runner(tmp_path, loop_spec, worker, verifier):
    store = JsonLoopStore(str(tmp_path / "states"))
    runner = LoopRunner(
        store=store,
        workspace_provider=FakeWorkspace(tmp_path),
        worker=worker,
        verifier=verifier,
        clock=lambda: 10.0,
        id_factory=lambda _: "run-1",
    )
    return runner, store


def test_runner_completes_only_after_verification(tmp_path):
    worker = FakeWorker()
    runner, _ = build_runner(
        tmp_path,
        spec(),
        worker,
        SequenceVerifier(verification(True)),
    )
    state = runner.start(spec(), "fix it", str(tmp_path))
    assert state.status == LoopStatus.COMPLETED
    assert state.iteration_count == 1
    assert worker.calls[0]["goal"].startswith("test")
    assert "- true" in worker.calls[0]["goal"]
    assert worker.calls[0]["feedback"] == ""


def test_runner_feeds_failed_evidence_into_retry(tmp_path):
    worker = FakeWorker()
    verifier = SequenceVerifier(
        verification(False, "pytest failed"),
        verification(True),
    )
    runner, _ = build_runner(tmp_path, spec(), worker, verifier)
    state = runner.start(spec(), "fix it", str(tmp_path))
    assert state.status == LoopStatus.COMPLETED
    assert state.iteration_count == 2
    assert worker.calls[1]["feedback"] == "pytest failed"


def test_runner_stops_at_iteration_budget(tmp_path):
    loop_spec = spec(
        budget={
            "max_iterations": 2,
            "max_wall_seconds": 100,
            "max_consecutive_failures": 10,
        }
    )
    worker = FakeWorker()
    runner, _ = build_runner(
        tmp_path,
        loop_spec,
        worker,
        SequenceVerifier(
            verification(False, "fail one"),
            verification(False, "fail two"),
        ),
    )
    state = runner.start(loop_spec, "fix it", str(tmp_path))
    assert state.status == LoopStatus.BUDGET_EXHAUSTED
    assert state.iteration_count == 2


def test_runner_blocks_on_consecutive_failures(tmp_path):
    loop_spec = spec(
        budget={
            "max_iterations": 5,
            "max_wall_seconds": 100,
            "max_consecutive_failures": 2,
        }
    )
    runner, _ = build_runner(
        tmp_path,
        loop_spec,
        FakeWorker(),
        SequenceVerifier(
            verification(False, "same failure"),
            verification(False, "same failure"),
        ),
    )
    state = runner.start(loop_spec, "fix it", str(tmp_path))
    assert state.status == LoopStatus.BLOCKED
    assert state.consecutive_failures == 2


def test_human_gate_waits_for_explicit_approval(tmp_path):
    loop_spec = spec(gates={"require_human": ["push", "merge"]})
    runner, store = build_runner(
        tmp_path,
        loop_spec,
        FakeWorker(),
        SequenceVerifier(verification(True)),
    )
    waiting = runner.start(loop_spec, "fix it", str(tmp_path))
    assert waiting.status == LoopStatus.WAITING_HUMAN
    assert waiting.pending_gates == ["push", "merge"]

    completed = runner.approve(waiting.run_id)
    assert completed.status == LoopStatus.COMPLETED
    assert completed.pending_gates == []
    assert store.load(waiting.run_id).status == LoopStatus.COMPLETED


def test_human_can_reject_verified_run(tmp_path):
    loop_spec = spec(gates={"require_human": ["merge"]})
    runner, _ = build_runner(
        tmp_path,
        loop_spec,
        FakeWorker(),
        SequenceVerifier(verification(True)),
    )
    waiting = runner.start(loop_spec, "fix it", str(tmp_path))
    rejected = runner.reject(waiting.run_id, "diff needs review")
    assert rejected.status == LoopStatus.BLOCKED
    assert rejected.stop_reason == "diff needs review"


def test_worker_exception_becomes_evidence_and_blocks(tmp_path):
    loop_spec = spec(
        budget={
            "max_iterations": 3,
            "max_wall_seconds": 100,
            "max_consecutive_failures": 1,
        }
    )
    runner, _ = build_runner(
        tmp_path,
        loop_spec,
        FakeWorker(fail=True),
        SequenceVerifier(),
    )
    state = runner.start(loop_spec, "fix it", str(tmp_path))
    assert state.status == LoopStatus.BLOCKED
    assert state.iterations[0].verification.evidence[0].kind == "worker_error"


def test_worker_configuration_error_fails_without_retry(tmp_path):
    class MisconfiguredWorker(FakeWorker):
        def run(self, **kwargs):
            raise WorkerConfigurationError("missing key")

    loop_spec = spec()
    runner, _ = build_runner(
        tmp_path,
        loop_spec,
        MisconfiguredWorker(),
        SequenceVerifier(),
    )
    state = runner.start(loop_spec, "fix it", str(tmp_path))
    assert state.status == LoopStatus.FAILED
    assert state.iteration_count == 1
    assert state.iterations[0].verification.evidence[0].kind == "worker_configuration"


def test_resume_marks_interrupted_iteration_and_continues(tmp_path):
    loop_spec = spec()
    worker = FakeWorker()
    runner, store = build_runner(
        tmp_path,
        loop_spec,
        worker,
        SequenceVerifier(verification(True)),
    )
    state = LoopState(
        run_id="run-1",
        spec=loop_spec,
        task="fix it",
        repository=str(tmp_path),
        status=LoopStatus.RUNNING,
        workspace=WorkspaceHandle(path=str(tmp_path), owned=False),
        iterations=[
            IterationRecord(number=1, status="running", started_at=1.0)
        ],
        started_at=1.0,
        updated_at=1.0,
    )
    store.save(state)

    resumed = runner.resume("run-1")
    assert resumed.status == LoopStatus.COMPLETED
    assert resumed.iterations[0].status == "interrupted"
    assert worker.calls[0]["iteration"] == 2
