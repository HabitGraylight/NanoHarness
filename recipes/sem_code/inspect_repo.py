"""Inspect a local Git checkout through NanoHarness's MCP extension."""

import argparse
from pathlib import Path

from nanoharness import DictToolRegistry, ExtensionContext, ExtensionManager
from nanoharness.extensions.mcp import MCPExtension


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("query", help="Entity name to find")
    args = parser.parse_args(argv)
    try:
        repo = args.repo.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        parser.error(f"Cannot resolve repository: {error}")
    if not repo.is_dir():
        parser.error("repo must be a directory containing a Git checkout")
    if not args.query.strip():
        parser.error("query must not be empty")

    context = ExtensionContext(tools=DictToolRegistry())
    with ExtensionManager(context) as extensions:
        extensions.install(MCPExtension(), {
            "fail_fast": True,
            "read_timeout_seconds": 60,
            "servers": [{
                "name": "sem",
                "command": "sem",
                "args": ["mcp"],
                "cwd": str(repo),
                "env": {
                    "SEM_REPO": str(repo),
                    "SEM_LOCAL": "1",
                    "SEM_MCP_NO_SHARED": "1",
                },
            }],
        })
        print(context.tools.call("mcp__sem__sem_find", {"query": args.query}))


if __name__ == "__main__":
    main()
