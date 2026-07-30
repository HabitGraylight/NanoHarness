"""Adapters that run one fresh NanoHarness instance per loop iteration."""

from typing import Callable, Protocol

from nanoharness.core.engine import NanoEngine

from app.schema import WorkerResult


class LoopWorker(Protocol):
    def run(
        self,
        goal: str,
        task: str,
        feedback: str,
        workspace: str,
        iteration: int,
        run_id: str,
    ) -> WorkerResult:
        ...


EngineFactory = Callable[[str, int, str], NanoEngine]


class WorkerConfigurationError(RuntimeError):
    """A non-retryable worker setup error, such as a missing API key."""


class NanoEngineWorker:
    """Build and run a clean NanoEngine for every outer-loop iteration."""

    def __init__(self, engine_factory: EngineFactory):
        self._engine_factory = engine_factory

    def run(
        self,
        goal: str,
        task: str,
        feedback: str,
        workspace: str,
        iteration: int,
        run_id: str,
    ) -> WorkerResult:
        engine = self._engine_factory(workspace, iteration, run_id)
        prompt = _iteration_prompt(goal, task, feedback, workspace, iteration)
        report = engine.run(prompt)
        trajectory = report.get("trajectory", [])
        final_thought = trajectory[-1].get("thought", "") if trajectory else ""
        summary = final_thought.strip() or "NanoEngine returned no final message"
        return WorkerResult(
            success=bool(report.get("summary", {}).get("success", False)),
            summary=summary[:2000],
            report=report,
        )


def _iteration_prompt(
    goal: str,
    task: str,
    feedback: str,
    workspace: str,
    iteration: int,
) -> str:
    sections = [
        f"Loop contract:\n{goal}",
        f"Concrete task:\n{task}",
        f"Iteration: {iteration}",
        f"Workspace: {workspace}",
        (
            "Work only inside the workspace. Inspect the repository, make the "
            "smallest correct change, and finish without claiming success unless "
            "the requested implementation is actually present. Verification is "
            "performed independently after this run."
        ),
    ]
    if feedback:
        sections.append(
            "Evidence from the previous failed verification:\n" + feedback
        )
    return "\n\n".join(sections)
