"""Application wiring for the default NanoLoop coding worker."""

import os
from pathlib import Path

from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.core.engine import NanoEngine

from app.adapters import OpenAICompatibleAdapter
from app.context import LoopContextManager
from app.schema import WorkerSpec
from app.tools import build_workspace_tools
from app.worker import NanoEngineWorker, WorkerConfigurationError


_SYSTEM_PROMPT = """You are the worker inside an evidence-gated coding loop.
Inspect the repository before editing. Use only the available workspace tools.
Make focused changes that satisfy the goal. You cannot run tests yourself;
an independent verifier will run the configured acceptance commands after you
finish. If previous verification evidence is provided, address that evidence.
Do not access .git or paths outside the workspace."""


def build_nano_worker(worker_spec: WorkerSpec, runtime_root: str) -> NanoEngineWorker:
    runtime_path = Path(runtime_root).resolve()

    def engine_factory(workspace: str, iteration: int, run_id: str) -> NanoEngine:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise WorkerConfigurationError(
                "DEEPSEEK_API_KEY environment variable is required"
            )

        state_path = runtime_path / "engine_states" / run_id
        state_path.mkdir(parents=True, exist_ok=True)

        try:
            llm_client = OpenAICompatibleAdapter(
                api_key=api_key,
                model=worker_spec.model,
                base_url=worker_spec.base_url,
            )
        except ImportError as exc:
            raise WorkerConfigurationError(str(exc)) from exc

        return NanoEngine(
            llm_client=llm_client,
            tools=build_workspace_tools(workspace),
            context=LoopContextManager(system_prompt=_SYSTEM_PROMPT),
            state=JsonStateStore(str(state_path / f"iteration-{iteration}.json")),
            hooks=SimpleHookManager(),
            evaluator=TraceEvaluator(),
            max_steps=worker_spec.max_steps,
        )

    return NanoEngineWorker(engine_factory)
