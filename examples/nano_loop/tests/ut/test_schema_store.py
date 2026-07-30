from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import load_loop_spec
from app.schema import LoopSpec, LoopState, LoopStatus
from app.store import JsonLoopStore


def test_load_example_spec():
    config = Path(__file__).resolve().parents[2] / "configs" / "loops" / "local_fix.yaml"
    spec = load_loop_spec(str(config))
    assert spec.name == "local-code-repair"
    assert spec.workspace.type == "git_worktree"
    assert len(spec.verify.commands) == 2


def test_budget_must_be_positive():
    with pytest.raises(ValidationError):
        LoopSpec(name="bad", goal="x", budget={"max_iterations": 0})


def test_store_round_trip(tmp_path):
    store = JsonLoopStore(str(tmp_path / "runs"))
    spec = LoopSpec(name="demo", goal="verify")
    state = LoopState(
        run_id="demo-1",
        spec=spec,
        task="change code",
        repository=str(tmp_path),
        status=LoopStatus.RETRYING,
        started_at=1.0,
        updated_at=2.0,
        feedback="tests failed",
    )
    store.save(state)
    loaded = store.load("demo-1")
    assert loaded == state
    assert store.list_states() == [state]


def test_store_rejects_unsafe_run_id(tmp_path):
    store = JsonLoopStore(str(tmp_path))
    with pytest.raises(ValueError):
        store.load("../escape")
