from pathlib import Path

from nanoharness.extensions.memory import FileMemoryManager
from nanoharness.extensions.skills import SkillRegistry

from app.learning import LearningReviewer, _render_memory, _render_skill
from app.models import (
    HermesRunState,
    LearningProposal,
    ProposalKind,
    ProposalStatus,
)
from app.store import HermesRunStore


def _state(demo_job, tmp_path, proposal):
    state = HermesRunState(
        run_id="run",
        job_name=demo_job.name,
        job_fingerprint=demo_job.fingerprint(),
        query=demo_job.query,
        workspace=str(tmp_path / "workspace"),
        proposals=[proposal],
    )
    store = HermesRunStore(tmp_path / "run.json")
    store.save(state)
    return state, store


def _proposal(kind=ProposalKind.MEMORY, **kwargs):
    defaults = {
        "kind": kind,
        "name": "durable-boundary",
        "content": "Keep learning staged until reviewed.",
        "description": "Durable learning boundary.",
        "source_run_id": "run",
    }
    defaults.update(kwargs)
    return LearningProposal(**defaults)


def test_approved_memory_is_promoted_and_indexed(tmp_path, demo_job):
    proposal = _proposal(memory_type="reference")
    state, store = _state(demo_job, tmp_path, proposal)

    LearningReviewer(tmp_path / "memory", tmp_path / "skills", True).review(
        state, store
    )

    assert proposal.status == ProposalStatus.PROMOTED
    entry = FileMemoryManager(str(tmp_path / "memory")).list_all()[0]
    assert entry.name == proposal.name
    assert entry.content == proposal.content
    assert proposal.name in (tmp_path / "memory" / "MEMORY.md").read_text(
        encoding="utf-8"
    )
    assert state.decisions[0].approved is True


def test_rejected_memory_never_changes_active_catalog(tmp_path, demo_job):
    proposal = _proposal()
    state, store = _state(demo_job, tmp_path, proposal)

    LearningReviewer(tmp_path / "memory", tmp_path / "skills", False).review(
        state, store
    )

    assert proposal.status == ProposalStatus.REJECTED
    assert not (tmp_path / "memory" / f"{proposal.name}.md").exists()
    assert state.decisions[0].approved is False


def test_approved_skill_is_parseable_and_discoverable(tmp_path, demo_job):
    proposal = _proposal(
        ProposalKind.SKILL,
        name="review-learning",
        trigger="when durable learning is proposed",
    )
    state, store = _state(demo_job, tmp_path, proposal)

    LearningReviewer(tmp_path / "memory", tmp_path / "skills", True).review(
        state, store
    )

    skills = SkillRegistry(str(tmp_path / "skills"))
    assert skills.list_names() == ["review-learning"]
    assert proposal.content in skills.load("review-learning")


def test_renderers_are_deterministic_and_content_preserving():
    memory = _proposal(memory_type="project")
    skill = _proposal(ProposalKind.SKILL, trigger="when needed")

    assert _render_memory(memory).endswith(memory.content + "\n")
    assert _render_skill(skill).endswith(skill.content + "\n")
    assert _render_skill(skill) == _render_skill(skill)


def test_target_revision_conflict_invalidates_without_asking(tmp_path, demo_job):
    proposal = _proposal()
    target = tmp_path / "memory" / f"{proposal.name}.md"
    target.parent.mkdir(parents=True)
    target.write_text("concurrent change", encoding="utf-8")
    state, store = _state(demo_job, tmp_path, proposal)
    called = []

    LearningReviewer(
        tmp_path / "memory",
        tmp_path / "skills",
        lambda item: called.append(item) or True,
    ).review(state, store)

    assert proposal.status == ProposalStatus.INVALID
    assert "changed after proposal" in proposal.validation_error
    assert called == []


def test_blank_content_is_structurally_invalid(tmp_path, demo_job):
    proposal = _proposal(content="   ")
    state, store = _state(demo_job, tmp_path, proposal)

    LearningReviewer(tmp_path / "memory", tmp_path / "skills", True).review(
        state, store
    )

    assert proposal.status == ProposalStatus.INVALID
    assert proposal.validation_error == "proposal content is blank"


def test_runtime_hash_tamper_is_invalid(tmp_path, demo_job):
    proposal = _proposal()
    state, store = _state(demo_job, tmp_path, proposal)
    state.proposals[0].content_sha256 = "0" * 64

    LearningReviewer(tmp_path / "memory", tmp_path / "skills", True).review(
        state, store
    )

    assert state.proposals[0].status == ProposalStatus.INVALID
    assert "hash" in state.proposals[0].validation_error


def test_approved_state_resumes_promotion_without_second_decision(tmp_path, demo_job):
    proposal = _proposal(status=ProposalStatus.APPROVED)
    state, store = _state(demo_job, tmp_path, proposal)
    called = []

    LearningReviewer(
        tmp_path / "memory",
        tmp_path / "skills",
        lambda item: called.append(item) or True,
    ).review(state, store)

    assert proposal.status == ProposalStatus.PROMOTED
    assert called == []


def test_already_written_target_recovers_before_status_save(tmp_path, demo_job):
    proposal = _proposal(status=ProposalStatus.APPROVED)
    target = tmp_path / "memory" / f"{proposal.name}.md"
    target.parent.mkdir(parents=True)
    target.write_text(_render_memory(proposal), encoding="utf-8")
    state, store = _state(demo_job, tmp_path, proposal)

    LearningReviewer(tmp_path / "memory", tmp_path / "skills", False).review(
        state, store
    )

    assert proposal.status == ProposalStatus.PROMOTED
    assert target.read_text(encoding="utf-8") == _render_memory(proposal)
    assert (tmp_path / "memory" / "MEMORY.md").is_file()


def test_completed_proposals_are_idempotent(tmp_path, demo_job):
    proposal = _proposal(status=ProposalStatus.PROMOTED)
    state, store = _state(demo_job, tmp_path, proposal)
    before = store.path.read_text(encoding="utf-8")

    LearningReviewer(tmp_path / "memory", tmp_path / "skills", True).review(
        state, store
    )

    assert store.path.read_text(encoding="utf-8") == before
    assert state.decisions == []


def test_source_run_mismatch_is_invalid(tmp_path, demo_job):
    proposal = _proposal(source_run_id="another-run")
    state, store = _state(demo_job, tmp_path, proposal)

    LearningReviewer(tmp_path / "memory", tmp_path / "skills", True).review(
        state, store
    )

    assert state.proposals[0].status == ProposalStatus.INVALID
    assert "source run" in state.proposals[0].validation_error


def test_host_mode_requires_matching_staged_audit(tmp_path, demo_job):
    proposal = _proposal()
    staging = tmp_path / "staged" / "run"
    proposal.staged_path = str(staging / f"{proposal.proposal_id}.json")
    state, store = _state(demo_job, tmp_path, proposal)

    LearningReviewer(
        tmp_path / "memory",
        tmp_path / "skills",
        True,
        staging_root=staging,
    ).review(state, store)

    assert state.proposals[0].status == ProposalStatus.INVALID
    assert "missing" in state.proposals[0].validation_error


def test_matching_staged_audit_allows_promotion(tmp_path, demo_job):
    proposal = _proposal()
    staging = tmp_path / "staged" / "run"
    proposal.staged_path = str(staging / f"{proposal.proposal_id}.json")
    state, store = _state(demo_job, tmp_path, proposal)
    store.stage_proposal(state.proposals[0], staging)

    LearningReviewer(
        tmp_path / "memory",
        tmp_path / "skills",
        True,
        staging_root=staging,
    ).review(state, store)

    assert state.proposals[0].status == ProposalStatus.PROMOTED
