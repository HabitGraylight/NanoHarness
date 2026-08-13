#!/usr/bin/env python3
"""Independently runnable durable NanoOpenClaw conversation host."""

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent))

from app.approvals import TerminalOutboundDecider
from app.host import GatewayHost
from app.models import GatewayJob
from nanoharness.components import OpenAIChatProvider


def run_demo(root: Path):
    with GatewayHost(GatewayJob.from_file(_HERE / "jobs" / "demo.yaml"), root) as host:
        return host.run_job()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run or resume the NanoOpenClaw conversation gateway"
    )
    parser.add_argument("--output", default=str(_HERE / ".runs"))
    parser.add_argument("--job", default=str(_HERE / "jobs" / "demo.yaml"))
    parser.add_argument("--resume", help="Resume one interrupted turn ID")
    parser.add_argument(
        "--provider",
        choices=["scripted", "openai"],
        default="scripted",
    )
    parser.add_argument("--model", help="Model name for an OpenAI-compatible provider")
    parser.add_argument("--base-url", help="Optional OpenAI-compatible API base URL")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--task", help="Replace the demo with one live inbound message")
    parser.add_argument(
        "--approval",
        choices=["auto", "deny", "interactive"],
        help="Outbound delivery approval; defaults to interactive for real providers",
    )
    args = parser.parse_args(argv)

    job = GatewayJob.from_file(args.job)
    provider_factory = None
    if args.provider == "openai":
        if not args.model:
            parser.error("--model is required with --provider openai")
        provider = OpenAIChatProvider(
            args.model,
            api_key=os.environ.get(args.api_key_env),
            base_url=args.base_url,
        )
        provider_factory = lambda _state, _responses: provider
    elif args.task:
        parser.error("--task requires --provider openai")

    if args.task:
        first = job.messages[0]
        envelope = first.envelope.model_copy(update={
            "message_id": "cli-message",
            "content": args.task,
        })
        job = job.model_copy(update={
            "name": "live-message",
            "fixture_files": {},
            "messages": [first.model_copy(update={
                "envelope": envelope,
                "responses": [],
            })],
        })

    approval_mode = args.approval or (
        "interactive" if args.provider == "openai" else "auto"
    )
    approval = {
        "auto": True,
        "deny": False,
        "interactive": TerminalOutboundDecider(),
    }[approval_mode]
    with GatewayHost(
        job,
        Path(args.output),
        resume_run_id=args.resume,
        provider_factory=provider_factory,
        approve_outbound=approval,
    ) as host:
        result = host.run()
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
