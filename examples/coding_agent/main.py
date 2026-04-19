#!/usr/bin/env python3
"""Coding Agent — a terminal-based coding assistant.

Usage:
    export DEEPSEEK_API_KEY="sk-..."
    cd examples/coding_agent
    python main.py

    # Or with a single task (runs once, prints report, exits):
    python main.py "Add a docstring to engine.py"
"""

import sys
import os

# Ensure example directory is on sys.path so that both
# nanoharness/ (symlink) and app/ are importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from app.builder import build_coding_engine
from app.context import compress_completed_rounds, trim_to_token_limit, verify_goal
from app.ui import BANNER, read_input

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_RESET = "\033[0m"


def run_repl(engine):
    """Interactive REPL loop — keeps running until /quit."""
    print(BANNER)

    while True:
        query = read_input()

        if query is None:
            print("Bye!")
            break

        if query == "":
            continue

        if query == "/clear":
            engine.context.reset()
            print("  Context cleared.\n")
            continue

        try:
            report = engine.run(query)

            # Post-run: verify goal completion
            _print_goal_verification(engine, query, report)

            # Post-run: compress context for next round
            compress_completed_rounds(engine.context)
            trim_to_token_limit(engine.context)

        except KeyboardInterrupt:
            print(f"\n  Interrupted.")
        except Exception as e:
            print(f"\n  Error: {e}")


def _print_goal_verification(engine, query, report):
    """Run goal verification and print the result."""
    # Only verify if the run terminated normally
    steps = report.get("trajectory", [])
    if not steps or steps[-1].get("status") != "terminated":
        print(f"  {_YELLOW}Run did not complete normally — skipping goal check.{_RESET}")
        return

    try:
        achieved, explanation = verify_goal(engine.llm, query, report)
    except Exception:
        # Verification failure should not break the REPL
        return

    if achieved:
        print(f"  {_GREEN}Goal achieved:{_RESET} {_DIM}{explanation.split(':', 1)[-1].strip()}{_RESET}")
    else:
        print(f"  {_RED}Goal not achieved:{_RESET} {_DIM}{explanation.split(':', 1)[-1].strip()}{_RESET}")


def main():
    try:
        engine = build_coding_engine()
    except KeyError:
        print("Error: DEEPSEEK_API_KEY environment variable not set.")
        print("  export DEEPSEEK_API_KEY=\"sk-...\"")
        sys.exit(1)

    if len(sys.argv) > 1:
        # Single-shot mode
        query = " ".join(sys.argv[1:])
        report = engine.run(query)
        _print_goal_verification(engine, query, report)
        _print_trajectory(report)
    else:
        # Interactive REPL
        run_repl(engine)


def _print_trajectory(report):
    """Print full trajectory (single-shot mode)."""
    print("\n===== Trajectory =====")
    for i, step in enumerate(report["trajectory"]):
        print(f"\n--- Step {i} [{step['status']}] ---")
        print(f"  Thought: {step['thought'][:300]}")
        if step["observation"]:
            print(f"  Observation: {step['observation'][:500]}")


if __name__ == "__main__":
    main()
