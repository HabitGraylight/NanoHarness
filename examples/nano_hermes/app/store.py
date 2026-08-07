"""Atomic persistence for independent, resumable NanoHermes runs."""

import json
from pathlib import Path

from app.models import HermesRunState, LearningProposal, utc_now


class HermesRunStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> HermesRunState:
        return HermesRunState.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

    def save(self, state: HermesRunState) -> None:
        state.updated_at = utc_now()
        _atomic_json(self.path, state.model_dump(mode="json"))

    def stage_proposal(self, proposal: LearningProposal, directory: Path) -> str:
        path = (directory / f"{proposal.proposal_id}.json").resolve()
        directory = directory.resolve()
        if path.parent != directory:
            raise ValueError("proposal path escapes staging directory")
        _atomic_json(path, proposal.model_dump(mode="json"))
        return str(path)


def _atomic_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
