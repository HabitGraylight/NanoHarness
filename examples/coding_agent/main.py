#!/usr/bin/env python3
"""Coding Agent — an example app built on NanoHarness.

Usage:
    export DEEPSEEK_API_KEY="sk-..."
    cd examples/coding_agent
    python main.py

    # Or with a single task:
    python main.py "Add a docstring to nanoharness/core/engine.py"
"""

import sys
import os

# Ensure the example directory is on sys.path so that both
# `nanoharness/` (vendored copy) and `app/` are importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from app.builder import build_coding_engine


def main():
    engine = build_coding_engine()

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        print("Coding Agent (type a task, or Ctrl+C to exit)")
        print("-" * 40)
        query = input("Task: ")

    report = engine.run(query)

    # Print full trajectory
    print("\n===== Trajectory =====")
    for i, step in enumerate(report["trajectory"]):
        print(f"\n--- Step {i} [{step['status']}] ---")
        thought = step["thought"][:200]
        print(f"  Thought: {thought}")
        if step["observation"]:
            print(f"  Observation: {step['observation'][:300]}")


if __name__ == "__main__":
    main()
