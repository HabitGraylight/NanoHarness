# Coding Agent Example

A self-contained coding agent built on NanoHarness.

## Why this directory contains a copy of `nanoharness/`

This example **vendors** a copy of the NanoHarness framework rather than importing from the repository root. This is intentional:

- The example is **independently runnable** — you don't need to understand the whole repo.
- The vendored copy can **evolve freely** — coding-agent-specific changes don't need to be generic.
- Proven generic improvements can be **back-ported** to the root `nanoharness/`.

For details on what has diverged from the root, see [FORK_NOTES.md](FORK_NOTES.md).

## Quick Start

```bash
# 1. Set your API key
export DEEPSEEK_API_KEY="sk-..."

# 2. Install dependencies (from repo root)
cd ../../../              # back to NanoHarness root
pip install -e ".[all-providers]"

# 3. Run the coding agent
cd examples/coding_agent
python main.py

# Or pass a task directly
python main.py "Add type hints to all public functions in nanoharness/core/"
```

## Directory Structure

```
coding_agent/
├── main.py              # Entry point
├── app/                 # Coding agent application layer
│   ├── builder.py       #   Engine wiring (assembles all components)
│   ├── prompts.yaml     #   Coding-specific prompt templates
│   ├── tools.py         #   Tool assembly (scripts + Python-native tools)
│   ├── permissions.py   #   Permission policy (deny reset, confirm push)
│   └── hooks.py         #   Lifecycle hooks (step-by-step output)
├── nanoharness/         # Vendored framework copy (independently evolvable)
├── configs/             # Runtime resources (prompts, scripts, MCP config)
├── tests/               # Example-specific tests
├── README.md            # This file
└── FORK_NOTES.md        # Divergence tracking from root nanoharness/
```

## What it does

The agent follows a **Think -> Act -> Observe** loop:

1. Reads relevant files to understand the codebase
2. Plans changes before acting
3. Makes targeted edits via `file_edit` (or `file_write` for new files)
4. Runs tests to verify changes
5. Reports what was done

## Available Tools

| Tool | Source | Description |
|---|---|---|
| `file_read` | script | Read file contents (with line range) |
| `file_write` | script | Create or overwrite a file |
| `file_edit` | script | Replace text fragment in a file |
| `file_list` | script | List directory contents |
| `file_find` | script | Find files by name pattern |
| `git_status` | script | Show working tree status |
| `git_diff` | script | Show unstaged/staged changes |
| `git_log` | script | Show commit history |
| `git_add` | script | Stage files |
| `git_commit` | script | Create a commit |
| `shell_exec` | script | Run arbitrary shell commands |
| `search_code` | python | Grep for patterns in source files |
| `list_files` | python | List files by glob pattern |
| `memory_store` | python | Store info for cross-session recall |
| `memory_recall` | python | Recall stored memories by keyword |

## Permission Model

| Level | Tools | Behavior |
|---|---|---|
| DENY | `git_reset`, `git_revert` | Blocked outright |
| CONFIRM | `git_push`, `git_commit`, `file_write`, `shell_exec` | User approval required |
| ALLOW | Everything else | Executes immediately |
