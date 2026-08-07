#!/usr/bin/env python3
"""Independently runnable persistent-learning NanoHermes host."""

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent))

from app.approvals import TerminalActionDecider, TerminalLearningDecider
from app.host import HermesHost
from app.models import HermesJob
from nanoharness.components import OpenAIChatProvider


def run_demo(root: Path):
    return HermesHost(HermesJob.from_file(_HERE / "jobs" / "demo.yaml"), root).run()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run or resume the NanoHermes persistent-learning host"
    )
    parser.add_argument("--output", default=str(_HERE / ".runs"))
    parser.add_argument("--job", default=str(_HERE / "jobs" / "demo.yaml"))
    parser.add_argument("--resume", help="Resume one interrupted Hermes run ID")
    parser.add_argument("--run-due", action="store_true", help="Run newly due schedules")
    parser.add_argument(
        "--provider",
        choices=["scripted", "openai"],
        default="scripted",
    )
    parser.add_argument("--model", help="Model name for the OpenAI-compatible provider")
    parser.add_argument("--base-url", help="Optional OpenAI-compatible API base URL")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--task", help="Override the query for a real provider")
    parser.add_argument(
        "--approval",
        choices=["auto", "deny", "interactive"],
        help="Workspace/schedule approval; defaults to auto for scripted runs",
    )
    parser.add_argument(
        "--learning-approval",
        choices=["auto", "deny", "interactive"],
        help="Learning promotion approval; defaults to the action approval mode",
    )
    args = parser.parse_args(argv)

    job = HermesJob.from_file(args.job)
    provider_factory = None
    if args.provider == "openai":
        if not args.model:
            parser.error("--model is required with --provider openai")
        if args.task:
            job = job.model_copy(update={"query": args.task})
        provider = OpenAIChatProvider(
            args.model,
            api_key=os.environ.get(args.api_key_env),
            base_url=args.base_url,
        )
        provider_factory = lambda _phase, _responses: provider
    elif args.task:
        parser.error("--task requires a real provider")

    action_mode = args.approval or (
        "interactive" if args.provider == "openai" else "auto"
    )
    learning_mode = args.learning_approval or action_mode
    actions = {
        "auto": True,
        "deny": False,
        "interactive": TerminalActionDecider(),
    }[action_mode]
    learning = {
        "auto": True,
        "deny": False,
        "interactive": TerminalLearningDecider(),
    }[learning_mode]
    host = HermesHost(
        job,
        Path(args.output),
        resume_run_id=args.resume,
        provider_factory=provider_factory,
        approve_actions=actions,
        approve_learning=learning,
    )
    if args.run_due:
        results = host.run_due(job)
        print(json.dumps([item.model_dump(mode="json") for item in results], indent=2))
        return 0 if all(item.success for item in results) else 1
    result = host.run()
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
