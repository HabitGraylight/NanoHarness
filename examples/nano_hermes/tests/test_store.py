import json

from app.models import HermesRunState, LearningProposal, ProposalKind
from app.store import HermesRunStore


def _state(demo_job, tmp_path):
    return HermesRunState(
        job_name=demo_job.name,
        job_fingerprint=demo_job.fingerprint(),
        query=demo_job.query,
        workspace=str(tmp_path / "workspace"),
    )


def test_store_round_trips_state_atomically(tmp_path, demo_job):
    store = HermesRunStore(tmp_path / "runs" / "run.json")
    state = _state(demo_job, tmp_path)

    store.save(state)
    loaded = store.load()

    assert store.exists()
    assert loaded == state
    assert not store.path.with_suffix(".json.tmp").exists()


def test_store_rejects_corrupt_state(tmp_path):
    store = HermesRunStore(tmp_path / "run.json")
    store.path.write_text("not-json", encoding="utf-8")

    try:
        store.load()
    except ValueError:
        pass
    else:
        raise AssertionError("corrupt state must not load")


def test_stage_proposal_writes_content_addressed_audit(tmp_path, demo_job):
    store = HermesRunStore(tmp_path / "run.json")
    proposal = LearningProposal(
        proposal_id="proposal_test",
        kind=ProposalKind.MEMORY,
        name="topic",
        content="durable",
        source_run_id="run",
    )

    staged = store.stage_proposal(proposal, tmp_path / "staged")

    payload = json.loads(open(staged, encoding="utf-8").read())
    assert payload["content"] == "durable"
    assert payload["content_sha256"] == proposal.content_sha256
