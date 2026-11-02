"""Inspect a local Git checkout through NanoHarness's MCP extension."""
import argparse
from pathlib import Path

from nanoharness import DictToolRegistry, ExtensionContext, ExtensionManager
from nanoharness.extensions.mcp import MCPExtension


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("query", help="Entity name to find")
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve(strict=True)
    context = ExtensionContext(tools=DictToolRegistry())
    with ExtensionManager(context) as extensions:
        extensions.install(MCPExtension(), {"servers": [{
            "name": "sem", "command": "sem", "args": ["mcp"],
            "env": {"SEM_REPO": str(repo), "SEM_CLOUD": "0"},
        }]})
        print(context.tools.call("mcp__sem__sem_find", {"query": args.query}))


if __name__ == "__main__":
    main()
