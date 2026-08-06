#!/usr/bin/env python3
"""Independently runnable deterministic NanoHermes example."""

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent))

from app.policy import HermesPolicy, contribute_hermes_tools
from nanoharness.testing import run_profile_scenario


def run_demo(root: Path):
    return run_profile_scenario(
        profile_path=_HERE / "profile.yaml",
        scenario_path=_HERE / "scenarios" / "smoke.yaml",
        workspace=root / "workspace",
        runtime_root=root / "runtime",
        artifact_root=root / "artifacts",
        skills_root=_HERE / "skills",
        policy=HermesPolicy(),
        contribute_tools=contribute_hermes_tools,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the NanoHermes smoke harness")
    parser.add_argument("--output", default=str(_HERE / ".runs"))
    args = parser.parse_args(argv)
    print(json.dumps(run_demo(Path(args.output)).model_dump(mode="json"), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
