#!/usr/bin/env python3
"""Independently runnable durable NanoOpenClaw operations host."""

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
from app.models import ConversationRoute, GatewayBatchResult, GatewayJob
from nanoharness.components import OpenAIChatProvider
from nanoharness.extensions.channels import InboundEnvelope, MockChannelAdapter, OutboxStatus


def run_demo(root: Path):
    """Exercise split generation/delivery, dedupe, schedule, and retry recovery."""

    job = GatewayJob.from_file(_HERE / "jobs" / "demo.yaml")
    with GatewayHost(job, root) as host:
        for message in job.messages:
            inbox, _ = host.ingest(message.envelope)
            waiting = host.process_inbox(inbox.id, deliver=False)
            host.deliver_turn(waiting.run_id)

        # Replaying the same external message must reuse its completed Turn.
        replay_inbox, created = host.ingest(job.messages[0].envelope)
        if created:
            raise RuntimeError("deterministic demo ingress unexpectedly duplicated")
        host.process_inbox(replay_inbox.id)

        due = host.run_due(deliver=False)
        if due.turns:
            host.gateway.register_adapter(
                MockChannelAdapter("mock", failures_before_success=1),
                replace=True,
            )
            first_attempt = host.deliver_pending()
            if not first_attempt or first_attempt[0].delivery_status != OutboxStatus.FAILED:
                raise RuntimeError("deterministic demo did not exercise delivery failure")
            host.deliver_pending()

        final = host.list_turns()
        return GatewayBatchResult(
            job=job.name,
            success=all(turn.success for turn in final),
            processed=len(final),
            delivered=sum(
                turn.delivery_status == OutboxStatus.SENT for turn in final
            ),
            turns=final,
        )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run, inspect, or resume the NanoOpenClaw conversation gateway"
    )
    parser.add_argument("--output", default=str(_HERE / ".runs"))
    parser.add_argument("--job", default=str(_HERE / "jobs" / "demo.yaml"))
    parser.add_argument("--ingest", help="JSON file containing one or more InboundEnvelope objects")
    parser.add_argument("--run-pending", action="store_true", help="Generate responses for pending wakeups")
    parser.add_argument("--run-due", action="store_true", help="Collect due schedules and generate responses")
    parser.add_argument("--deliver", action="store_true", help="Review and deliver pending Outbox records")
    parser.add_argument("--delivery-id", help="Deliver one queued Turn by run ID")
    parser.add_argument("--list-pending", action="store_true", help="Inspect pending wakeups, turns, and Outbox records")
    parser.add_argument("--resume", help="Resume one interrupted Turn ID")
    parser.add_argument("--limit", type=int, help="Maximum pending items to process")
    parser.add_argument(
        "--provider",
        choices=["scripted", "openai"],
        default="scripted",
    )
    parser.add_argument("--model", help="Model name for an OpenAI-compatible provider")
    parser.add_argument("--base-url", help="Optional OpenAI-compatible API base URL")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--task", help="Create one operator-trusted manual wakeup")
    parser.add_argument("--event-id", default="cli-task")
    parser.add_argument("--channel", default="mock")
    parser.add_argument("--account-id", default="primary")
    parser.add_argument("--conversation-id", default="cli-conversation")
    parser.add_argument("--sender-id", default="cli-user")
    parser.add_argument(
        "--approval",
        choices=["auto", "deny", "interactive"],
        help="Outbound delivery approval; defaults to interactive for real providers",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")

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

    approval_mode = args.approval or (
        "interactive" if args.provider == "openai" else "auto"
    )
    approval = {
        "auto": True,
        "deny": False,
        "interactive": TerminalOutboundDecider(),
    }[approval_mode]
    has_operation = any((
        args.ingest,
        args.run_pending,
        args.run_due,
        args.deliver,
        args.delivery_id,
        args.list_pending,
        args.resume,
        args.task,
    ))
    if (
        not has_operation
        and args.provider == "scripted"
        and args.approval is None
        and Path(args.job).resolve() == (_HERE / "jobs" / "demo.yaml").resolve()
    ):
        result = run_demo(Path(args.output))
        print(json.dumps(result.model_dump(mode="json"), indent=2))
        return 0 if result.success else 1

    output: dict[str, object] = {}
    successful = True
    with GatewayHost(
        job,
        Path(args.output),
        provider_factory=provider_factory,
        approve_outbound=approval,
    ) as host:
        if not has_operation:
            result = host.run_job()
            print(json.dumps(result.model_dump(mode="json"), indent=2))
            return 0 if result.success else 1

        if args.ingest:
            envelopes = _load_envelopes(Path(args.ingest))
            receipts = []
            for envelope in envelopes:
                record, created = host.ingest(envelope)
                receipts.append({
                    "inbox_id": record.id,
                    "created": created,
                    "status": record.status.value,
                })
            output["ingest"] = receipts

        if args.task:
            route = ConversationRoute(
                channel=args.channel,
                account_id=args.account_id,
                conversation_id=args.conversation_id,
                sender_id=args.sender_id,
            )
            wakeup, created = host.ingest_manual(
                args.task,
                route,
                event_id=args.event_id,
            )
            turn = host.process_wakeup(wakeup.id, deliver=args.deliver)
            output["manual"] = {
                "created": created,
                "turn": turn.model_dump(mode="json"),
            }
            successful = successful and turn.success

        if args.run_due:
            result = host.run_due(deliver=False)
            output["due"] = result.model_dump(mode="json")
            successful = successful and result.success

        if args.run_pending:
            turns = host.run_pending(limit=args.limit, deliver=False)
            output["pending"] = [turn.model_dump(mode="json") for turn in turns]
            successful = successful and all(turn.success for turn in turns)

        if args.resume:
            turn = host.resume(args.resume, deliver=False)
            output["resume"] = turn.model_dump(mode="json")
            successful = successful and turn.success

        if args.delivery_id:
            turn = host.deliver_turn(args.delivery_id)
            output["delivery"] = turn.model_dump(mode="json")
            successful = successful and turn.success
        elif args.deliver and not args.task:
            turns = host.deliver_pending(limit=args.limit)
            output["delivery"] = [turn.model_dump(mode="json") for turn in turns]
            successful = successful and all(turn.success for turn in turns)

        if args.list_pending:
            output["inspection"] = host.list_pending()

    output["success"] = successful
    print(json.dumps(output, indent=2))
    return 0 if successful else 1


def _load_envelopes(path: Path) -> list[InboundEnvelope]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload if isinstance(payload, list) else [payload]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError("--ingest JSON must contain an object or list of objects")
    return [InboundEnvelope.model_validate(value) for value in values]


if __name__ == "__main__":
    raise SystemExit(main())
