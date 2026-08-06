"""Command-line white-box inspection for HarnessSpec files."""

import argparse
import json
import sys
from typing import List, Optional

from pydantic import ValidationError

from nanoharness.profiles.builder import HarnessBuilder
from nanoharness.profiles.io import load_harness_spec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nanoharness",
        description="Validate and explain composable NanoHarness profiles.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "explain"):
        child = subparsers.add_parser(command)
        child.add_argument("spec", help="Path to a YAML, TOML, or JSON HarnessSpec")
        child.add_argument(
            "--compact",
            action="store_true",
            help="Emit compact JSON instead of indented JSON",
        )
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
    return f"HarnessSpec could not be loaded ({type(error).__name__})"


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    builder = HarnessBuilder()
    if args.command == "catalog":
        _dump(builder.catalog.descriptions())
        return 0
    try:
        spec = load_harness_spec(args.spec)
        if args.command == "validate":
            result = builder.validate(spec)
        else:
            result = builder.explain(spec)
    except Exception as error:
        _dump({
            "valid": False,
            "errors": [{
                "code": "spec_load_error",
                "message": _safe_load_error(error),
            }],
        }, compact=args.compact)
        return 2
    _dump(result, compact=args.compact)
    return 0 if result.valid else 1


if __name__ == "__main__":
    sys.exit(main())
