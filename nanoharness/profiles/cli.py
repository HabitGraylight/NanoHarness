"""Command-line validation, tracing, and comparison for white-box harnesses."""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from pydantic import ValidationError

from nanoharness.profiles.builder import HarnessBuilder
from nanoharness.profiles.io import load_harness_spec
from nanoharness.profiles.matrix import build_profile_matrix
from nanoharness.profiles.trace import compare_traces, load_trace


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nanoharness",
        description="Validate, explain, trace, and compare NanoHarness profiles.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "explain", "trace"):
        child = subparsers.add_parser(command)
        child.add_argument(
            "source",
            help=(
                "HarnessSpec path" if command != "trace"
                else "NanoEngine report, checkpoint, or event JSON/JSONL path"
            ),
        )
        child.add_argument(
            "--compact",
            action="store_true",
            help="Emit compact JSON instead of indented JSON",
        )
    compare = subparsers.add_parser("compare")
    compare.add_argument("left")
    compare.add_argument("right")
    compare.add_argument(
        "--kind",
        choices=("auto", "profile", "trace"),
        default="auto",
    )
    compare.add_argument("--compact", action="store_true")
    matrix = subparsers.add_parser("matrix")
    matrix.add_argument("specs", nargs="+")
    matrix.add_argument("--compact", action="store_true")
    subparsers.add_parser("catalog", help="List built-in extension manifests")
    return parser


def _dump(value, *, compact: bool = False) -> None:
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    print(json.dumps(
        payload,
        ensure_ascii=False,
        indent=None if compact else 2,
        sort_keys=False,
    ))


def _safe_load_error(error: Exception) -> str:
    if isinstance(error, ValidationError):
        details = []
        for item in error.errors(include_input=False):
            field = ".".join(str(part) for part in item["loc"])
            details.append(f"{field or '<root>'}: {item['msg']}")
        return "HarnessSpec validation failed: " + "; ".join(details)
    if isinstance(error, (FileNotFoundError, PermissionError)):
        return str(error)
    return f"Input could not be loaded ({type(error).__name__})"


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    builder = HarnessBuilder()
    if args.command == "catalog":
        _dump(builder.catalog.descriptions())
        return 0
    try:
        if args.command == "trace":
            result = load_trace(args.source)
            _dump(result, compact=args.compact)
            return 0
        if args.command == "matrix":
            specs = [load_harness_spec(path) for path in args.specs]
            result = build_profile_matrix(specs, builder=builder)
            _dump(result, compact=args.compact)
            return 0 if all(result.valid.values()) else 1
        if args.command == "compare":
            kind = args.kind
            if kind == "auto":
                left_profile = _is_profile_path(args.left)
                right_profile = _is_profile_path(args.right)
                if left_profile != right_profile:
                    raise ValueError(
                        "compare inputs have different kinds; pass --kind explicitly"
                    )
                kind = "profile" if left_profile else "trace"
            if kind == "profile":
                result = build_profile_matrix(
                    [
                        load_harness_spec(args.left),
                        load_harness_spec(args.right),
                    ],
                    builder=builder,
                )
                _dump(result, compact=args.compact)
                return 0 if all(result.valid.values()) else 1
            result = compare_traces(
                load_trace(args.left),
                load_trace(args.right),
                left_label=Path(args.left).name,
                right_label=Path(args.right).name,
            )
            _dump(result, compact=args.compact)
            return 0

        spec = load_harness_spec(args.source)
        if args.command == "validate":
            result = builder.validate(spec)
        else:
            result = builder.explain(spec)
    except Exception as error:
        error_code = (
            "inspection_error"
            if args.command in {"trace", "compare", "matrix"}
            else "spec_load_error"
        )
        _dump({
            "valid": False,
            "errors": [{
                "code": error_code,
                "message": _safe_load_error(error),
            }],
        }, compact=args.compact)
        return 2
    _dump(result, compact=args.compact)
    return 0 if result.valid else 1


def _is_profile_path(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix in {".yaml", ".yml", ".toml"}:
        return True
    if suffix not in {".json"}:
        return False
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict) or "name" not in payload:
        return False
    trace_markers = {"run", "summary", "trajectory", "events", "run_id"}
    return not any(marker in payload for marker in trace_markers)


if __name__ == "__main__":
    sys.exit(main())
