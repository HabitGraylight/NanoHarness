#!/usr/bin/env python3
"""Network-free runner for NanoHarness Gallery profiles."""

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent))

from app.runner import GalleryRunner
from nanoharness.profiles import build_profile_matrix, load_harness_spec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic Harness Gallery demos")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("profiles", help="List Gallery profile names")
    commands.add_parser("matrix", help="Inspect every Gallery profile")
    run = commands.add_parser("run", help="Run one profile against one scenario")
    run.add_argument("profile", choices=_profile_names())
    run.add_argument(
        "--scenario",
        default=str(_HERE / "scenarios" / "inspect_workspace.yaml"),
    )
    run.add_argument("--workspace")
    run.add_argument("--runtime")
    run.add_argument("--artifacts")
    return parser


def _profile_paths():
    return sorted((_HERE / "profiles").glob("*.yaml"))


def _profile_names():
    return [path.stem for path in _profile_paths()]


def _dump(payload) -> None:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    profiles = _profile_paths()
    if args.command == "profiles":
        _dump([load_harness_spec(str(path)).name for path in profiles])
        return 0
    if args.command == "matrix":
        _dump(build_profile_matrix([
            load_harness_spec(str(path)) for path in profiles
        ]))
        return 0

    run_root = _HERE / ".runs"
    workspace = Path(args.workspace or run_root / "workspaces" / args.profile)
    runtime = Path(args.runtime or run_root / "runtime" / args.profile)
    artifacts = Path(args.artifacts or run_root / "artifacts")
    result = GalleryRunner().run(
        profile_path=_HERE / "profiles" / f"{args.profile}.yaml",
        scenario_path=args.scenario,
        workspace=workspace,
        runtime_root=runtime,
        artifact_root=artifacts,
        skills_root=_HERE / "skills",
    )
    _dump(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
