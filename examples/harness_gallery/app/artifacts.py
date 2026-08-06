"""Run report and minimized trace persistence for Gallery scenarios."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from nanoharness.profiles import HarnessTrace, summarize_trace


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str
    scenario: str
    run_id: str
    report_path: str
    trace_path: str


class RunArtifactStore:
    """Persist raw reports separately from content-minimized public traces."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        *,
        profile: str,
        scenario: str,
        report: dict,
    ) -> tuple[ArtifactRecord, HarnessTrace]:
        trace = summarize_trace(report)
        run_id = trace.run_id or "run_unknown"
        target = self.root / scenario / profile / run_id
        target.mkdir(parents=True, exist_ok=False)
        report_path = target / "report.json"
        trace_path = target / "trace.json"
        self._write_json(report_path, report)
        self._write_json(trace_path, trace.model_dump(mode="json"))
        record = ArtifactRecord(
            profile=profile,
            scenario=scenario,
            run_id=run_id,
            report_path=str(report_path),
            trace_path=str(trace_path),
        )
        self._write_json(target / "artifact.json", record.model_dump(mode="json"))
        return record, trace

    @staticmethod
    def _write_json(path: Path, payload) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
