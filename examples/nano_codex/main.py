#!/usr/bin/env python3
"""Independently runnable, resumable NanoCodex example."""

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent))

from app.host import CodexHost
from app.models import CodexJob
from app.approvals import TerminalApprovalDecider
from nanoharness.components import OpenAIChatProvider


def run_demo(root: Path):
    job = CodexJob.from_file(_HERE / "jobs" / "demo.yaml")
    return CodexHost(job, root).run()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run or resume the NanoCodex coding host"
    )
    parser.add_argument("--output", default=str(_HERE / ".runs"))
    parser.add_argument("--job", default=str(_HERE / "jobs" / "demo.yaml"))
    parser.add_argument("--repo", help="Clean existing Git repository to clone and edit")
    parser.add_argument(
        "--provider",
        choices=["scripted", "openai"],
        default="scripted",
    )
    parser.add_argument("--model", help="Model name for the OpenAI-compatible provider")
    parser.add_argument("--base-url", help="Optional OpenAI-compatible API base URL")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--task", help="Override the job objective for a real provider")
    parser.add_argument(
        "--approval",
        choices=["auto", "deny", "interactive"],
        help="Approval mode; defaults to auto for scripted and interactive for real providers",
    )
    parser.add_argument(
        "--deny-writes",
        action="store_true",
        help="Demonstrate an approval-blocked Execute phase",
    )
    args = parser.parse_args(argv)
    job = CodexJob.from_file(args.job)
    provider_factory = None
    if args.provider == "openai":
        if not args.model:
            parser.error("--model is required with --provider openai")
        if args.task:
            job = job.model_copy(update={"objective": args.task})
        provider = OpenAIChatProvider(
            args.model,
            api_key=os.environ.get(args.api_key_env),
            base_url=args.base_url,
        )
        provider_factory = lambda _phase, _responses: provider
    elif args.task:
        parser.error("--task requires a real provider")

    approval_mode = args.approval or (
        "interactive" if args.provider == "openai" else "auto"
    )
    if args.deny_writes:
        approval_mode = "deny"
    approval = {
        "auto": True,
        "deny": False,
        "interactive": TerminalApprovalDecider(),
    }[approval_mode]
    result = CodexHost(
        job,
        Path(args.output),
        repository=args.repo,
        provider_factory=provider_factory,
        approve_writes=approval,
    ).run()
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
