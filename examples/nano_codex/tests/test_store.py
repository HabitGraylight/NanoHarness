import json

import pytest
from pydantic import ValidationError

from app.models import CodexRunState, CodexStatus
from app.store import CodexRunStore


def _state(tmp_path):
    return CodexRunState(
        job_name="store-test",
        job_fingerprint="fingerprint",
        objective="persist state",
        repository=str(tmp_path),
    )


def test_store_round_trip_and_exists(tmp_path):
    store = CodexRunStore(tmp_path / "runtime" / "run.json")
    state = _state(tmp_path)
    assert store.exists() is False
    store.save(state)
    assert store.exists() is True
    assert store.load() == state


def test_store_replaces_existing_state_and_updates_timestamp(tmp_path):
    store = CodexRunStore(tmp_path / "run.json")
    state = _state(tmp_path)
    store.save(state)
    first = state.updated_at
    state.status = CodexStatus.RUNNING
    store.save(state)
    assert store.load().status == CodexStatus.RUNNING
    assert state.updated_at >= first
    assert not (tmp_path / "run.json.tmp").exists()


def test_store_rejects_invalid_or_unknown_state(tmp_path):
    path = tmp_path / "run.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValidationError):
        CodexRunStore(path).load()
    path.write_text(json.dumps({"schema_version": "99"}), encoding="utf-8")
    with pytest.raises(ValidationError):
        CodexRunStore(path).load()
