"""Atomic persistence for recoverable NanoCodex runs."""

import json
from pathlib import Path

from app.models import CodexRunState, utc_now


class CodexRunStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> CodexRunState:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return CodexRunState.model_validate(payload)

    def save(self, state: CodexRunState) -> None:
        state.updated_at = utc_now()
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                state.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)
