#!/usr/bin/env python3
"""NanoLoop CLI: run, inspect, resume, and approve evidence-gated loops."""

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from app.builder import build_nano_worker
from app.config import load_loop_spec
from app.runner import LoopRunner
from app.schema import LoopSpec, LoopState, LoopStatus
from app.store import JsonLoopStore
from app.verifier import CommandVerifier
from app.workspace import GitWorktreeWorkspace, LocalWorkspace


def build_runner(spec: LoopSpec, runtime_dir: str) -> LoopRunner:
    runtime = Path(runtime_dir).resolve()
    store = JsonLoopStore(str(runtime / "runs"))
    if spec.workspace.type == "git_worktree":
        workspace = GitWorktreeWorkspace(str(runtime / "worktrees"))
    else:
        workspace = LocalWorkspace()
    return LoopRunner(
        store=store,
        workspace_provider=workspace,
        worker=build_nano_worker(spec.worker, str(runtime)),
        verifier=CommandVerifier(spec.verify),
    )


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    runtime_dir = args.runtime_dir
    store = JsonLoopStore(str(Path(runtime_dir).resolve() / "runs"))

    try:
        if args.command == "run":
            spec = load_loop_spec(args.config)
            _validate_worker_environment(spec)
            runner = build_runner(spec, runtime_dir)
            state = runner.start(spec, args.task, args.repo)
            _print_state(state)
            return _exit_code(state)

        if args.command == "resume":
            existing = store.load(args.run_id)
            if (
                existing.status != LoopStatus.WAITING_HUMAN
                and not existing.is_terminal
            ):
                _validate_worker_environment(existing.spec)
            state = build_runner(existing.spec, runtime_dir).resume(args.run_id)
            _print_state(state)
            return _exit_code(state)

        if args.command == "approve":
            existing = store.load(args.run_id)
            state = build_runner(existing.spec, runtime_dir).approve(args.run_id)
            _print_state(state)
            return 0

        if args.command == "reject":
            existing = store.load(args.run_id)
            state = build_runner(existing.spec, runtime_dir).reject(
                args.run_id,
                args.reason,
            )
            _print_state(state)
            return 1

        if args.command == "status":
            _print_state(store.load(args.run_id), verbose=True)
            return 0

        if args.command == "list":
            states = store.list_states()
            if not states:
                print("No loop runs found.")
                return 0
            for state in states:
                print(
                    f"{state.run_id}\t{state.status.value}\t"
                    f"iterations={state.iteration_count}\t{state.task[:80]}"
                )
            return 0
    except (KeyError, ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error("A command is required")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run evidence-gated agent loops on top of NanoHarness",
    )
    parser.add_argument(
        "--runtime-dir",
        default=os.environ.get("NANOLOOP_RUNTIME_DIR", str(_HERE / "sandbox")),
        help="Persistent state and worktree directory",
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Start a new loop")
    run_parser.add_argument("config", help="Loop YAML file")
    run_parser.add_argument("--task", required=True, help="Concrete task for this run")
    run_parser.add_argument("--repo", default=".", help="Git repository to modify")

    resume_parser = subparsers.add_parser("resume", help="Resume an interrupted loop")
    resume_parser.add_argument("run_id")

    approve_parser = subparsers.add_parser(
        "approve",
        help="Approve evidence and mark a waiting run complete",
    )
    approve_parser.add_argument("run_id")

    reject_parser = subparsers.add_parser("reject", help="Reject a waiting run")
    reject_parser.add_argument("run_id")
    reject_parser.add_argument("--reason", default="Human approval rejected")

    status_parser = subparsers.add_parser("status", help="Show one run")
    status_parser.add_argument("run_id")
    subparsers.add_parser("list", help="List stored runs")
    return parser


def _print_state(state: LoopState, verbose: bool = False) -> None:
    print(f"Run:        {state.run_id}")
    print(f"Status:     {state.status.value}")
    print(f"Iterations: {state.iteration_count}")
    print(f"Reason:     {state.stop_reason or '-'}")
    if state.workspace:
        print(f"Workspace:  {state.workspace.path}")
        if state.workspace.branch:
            print(f"Branch:     {state.workspace.branch}")
    if state.pending_gates:
        print("Gates:      " + ", ".join(state.pending_gates))

    if verbose:
        print("\n" + json.dumps(
            state.model_dump(mode="json"),
            indent=2,
            ensure_ascii=False,
        ))


def _exit_code(state: LoopState) -> int:
    if state.status == LoopStatus.COMPLETED:
        return 0
    if state.status == LoopStatus.WAITING_HUMAN:
        return 2
    return 1


def _validate_worker_environment(spec: LoopSpec) -> None:
    if spec.worker.type == "nano_engine" and not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY environment variable is required")


if __name__ == "__main__":
    raise SystemExit(main())
