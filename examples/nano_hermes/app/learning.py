"""Host-owned validation and promotion of staged NanoHermes learning."""

from pathlib import Path
from typing import Optional

import yaml

from nanoharness.extensions.memory import FileMemoryManager

from app.approvals import LearningDecider
from app.models import (
    HermesRunState,
    LearningDecision,
    LearningProposal,
    ProposalKind,
    ProposalStatus,
    content_sha256,
)
from app.store import HermesRunStore


class LearningReviewer:
    def __init__(
        self,
        memory_root: str | Path,
        skills_root: str | Path,
        decider: LearningDecider,
        staging_root: Optional[str | Path] = None,
    ):
        self.memory_root = Path(memory_root).resolve()
        self.skills_root = Path(skills_root).resolve()
        self.memory_root.mkdir(parents=True, exist_ok=True)
        self.skills_root.mkdir(parents=True, exist_ok=True)
        self.decider = decider
        self.staging_root = (
            Path(staging_root).resolve() if staging_root is not None else None
        )

    def review(self, state: HermesRunState, store: HermesRunStore) -> None:
        for proposal in state.proposals:
            if proposal.status in {
                ProposalStatus.PROMOTED,
                ProposalStatus.REJECTED,
                ProposalStatus.INVALID,
            }:
                continue
            if proposal.status == ProposalStatus.STAGED:
                error = self._validate(proposal, state)
                if error:
                    proposal.status = ProposalStatus.INVALID
                    proposal.validation_error = error
                    if error == "proposal content hash does not match content":
                        proposal.content_sha256 = content_sha256(proposal.content)
                    state.decisions.append(self._decision(proposal, False, error))
                    store.save(state)
                    continue
                approved = (
                    bool(self.decider(proposal))
                    if callable(self.decider)
                    else bool(self.decider)
                )
                reason = (
                    "Approved by the NanoHermes learning reviewer"
                    if approved
                    else "Rejected by the NanoHermes learning reviewer"
                )
                state.decisions.append(self._decision(proposal, approved, reason))
                proposal.status = (
                    ProposalStatus.APPROVED if approved else ProposalStatus.REJECTED
                )
                store.save(state)
            if proposal.status == ProposalStatus.APPROVED:
                self._promote(proposal)
                proposal.status = ProposalStatus.PROMOTED
                store.save(state)

    def _validate(
        self,
        proposal: LearningProposal,
        state: HermesRunState,
    ) -> str:
        if proposal.source_run_id != state.run_id:
            return "proposal source run does not match active run"
        if not proposal.content.strip():
            return "proposal content is blank"
        if proposal.content_sha256 != content_sha256(proposal.content):
            return "proposal content hash does not match content"
        if self.staging_root is not None:
            staged_error = self._validate_staged_copy(proposal)
            if staged_error:
                return staged_error
        target = self._target(proposal)
        current = _file_sha256(target)
        if current == proposal.base_sha256:
            return ""
        if self._already_promoted(proposal, target):
            return ""
        return "active learning target changed after proposal staging"

    def _promote(self, proposal: LearningProposal) -> None:
        target = self._target(proposal)
        if self._already_promoted(proposal, target):
            if proposal.kind == ProposalKind.MEMORY:
                FileMemoryManager(str(self.memory_root)).save(
                    proposal.name,
                    proposal.content,
                    description=proposal.description,
                    type=proposal.memory_type,
                )
            return
        if _file_sha256(target) != proposal.base_sha256:
            raise RuntimeError(
                f"learning target changed before promotion: {proposal.kind.value}:{proposal.name}"
            )
        if proposal.kind == ProposalKind.MEMORY:
            FileMemoryManager(str(self.memory_root)).save(
                proposal.name,
                proposal.content,
                description=proposal.description,
                type=proposal.memory_type,
            )
            return
        rendered = _render_skill(proposal)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(target)

    def _validate_staged_copy(self, proposal: LearningProposal) -> str:
        if not proposal.staged_path:
            return "proposal has no staged audit path"
        path = Path(proposal.staged_path).resolve()
        assert self.staging_root is not None
        if path.parent != self.staging_root:
            return "proposal staged audit path escapes the active run"
        if not path.is_file():
            return "proposal staged audit file is missing"
        try:
            staged = LearningProposal.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except ValueError:
            return "proposal staged audit file is invalid"
        fields = (
            "proposal_id",
            "kind",
            "name",
            "content",
            "description",
            "memory_type",
            "trigger",
            "source_run_id",
            "content_sha256",
            "base_sha256",
        )
        if any(getattr(staged, field) != getattr(proposal, field) for field in fields):
            return "proposal staged audit does not match run state"
        return ""

    def _already_promoted(self, proposal: LearningProposal, target: Path) -> bool:
        if not target.is_file():
            return False
        expected = (
            _render_memory(proposal)
            if proposal.kind == ProposalKind.MEMORY
            else _render_skill(proposal)
        )
        return target.read_text(encoding="utf-8") == expected

    def _target(self, proposal: LearningProposal) -> Path:
        root = (
            self.memory_root
            if proposal.kind == ProposalKind.MEMORY
            else self.skills_root
        )
        return root / f"{proposal.name}.md"

    @staticmethod
    def _decision(
        proposal: LearningProposal,
        approved: bool,
        reason: str,
    ) -> LearningDecision:
        return LearningDecision(
            proposal_id=proposal.proposal_id,
            kind=proposal.kind,
            name=proposal.name,
            approved=approved,
            reason=reason,
            content_sha256=proposal.content_sha256,
        )


def _render_memory(proposal: LearningProposal) -> str:
    description = (
        f"\ndescription: {proposal.description}" if proposal.description else ""
    )
    return (
        f"---\nname: {proposal.name}\ntype: {proposal.memory_type}"
        f"{description}\n---\n\n{proposal.content}\n"
    )


def _render_skill(proposal: LearningProposal) -> str:
    metadata = yaml.safe_dump(
        {
            "name": proposal.name,
            "description": proposal.description,
            "trigger": proposal.trigger,
        },
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return f"---\n{metadata}\n---\n\n{proposal.content.strip()}\n"


def _file_sha256(path: Path):
    if not path.is_file():
        return None
    return content_sha256(path.read_text(encoding="utf-8"))
