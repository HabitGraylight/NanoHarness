# Coding Agent Example

A self-contained coding agent built on NanoHarness, with a terminal UI.

## Quick Start

```bash
# 1. Install dependencies (from repo root)
cd ../../..
pip install -e ".[openai]"

# 2. Set your API key
export DEEPSEEK_API_KEY="sk-..."

# 3. Run
cd examples/coding_agent
python main.py

# Or pass a task directly (runs once, prints report, exits)
python main.py "Add type hints to all public functions in nanoharness/core/"
```

## Why `nanoharness/` is a symlink

`nanoharness/ → ../../nanoharness` — the kernel is **policy-free** and shared across apps. All coding-agent-specific behavior lives in `app/`. No code duplication, no sync burden.

## Two Modes

**Interactive REPL** (default) — keeps running between tasks:

```
  ╔══════════════════════════════════╗
  ║       NanoHarness Coding Agent   ║
  ╚══════════════════════════════════╝

  Type a coding task and press Enter.
  Commands: /quit, /clear, /help

❯ Add a docstring to engine.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Task: Add a docstring to engine.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Thinking: I need to read the file first...
  ▶ file_read(path="nanoharness/core/engine.py")
  ✓ def NanoEngine: ...

  Thinking: Now I'll add the docstring...
  ▶ file_edit(path="nanoharness/core/engine.py", old_text="class NanoEngine:", ...)
  ✓ Replaced 1 occurrence in nanoharness/core/engine.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Done. Steps: 3 | Success
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

❯ _
```

**Single-shot** — pass a task as argument, exits after completion.

## REPL Commands

| Command | Description |
|---|---|
| `/quit` | Exit the agent |
| `/clear` | Clear conversation context |
| `/help` | Show help |
| `Ctrl+C` | Interrupt current task (doesn't exit) |

Features: colored output, readline support (arrow keys, history), persistent input history across sessions.

## Directory Structure

```
coding_agent/
├── main.py              # Entry point (REPL + single-shot)
├── app/                 # App layer — all coding-agent-specific logic
│   ├── builder.py       #   Engine assembly + memory lifecycle hooks
│   ├── hooks.py         #   Colored terminal output hooks
│   ├── ui.py            #   REPL loop + readline + history
│   ├── tools.py         #   Script tools + Python-native search/list
│   ├── permissions.py   #   Permission policy (deny reset, confirm push)
│   └── prompts.yaml     #   Coding-agent system prompt
├── nanoharness/         # Symlink → ../../nanoharness (shared kernel)
├── configs/             # Runtime resources (shell scripts, MCP config)
├── sandbox/             # Runtime artifacts (memory.json, run_state.json, .history)
├── tests/               # Smoke tests (9 tests)
└── README.md            # This file
```

## Available Tools

31 tools registered:

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
| `git_push` | script | Push to remote |
| `git_branch_*` | script | Branch operations |
| `git_stash*` | script | Stash operations |
| `git_show` | script | Show commit details |
| `git_remote_list` | script | List remotes |
| `git_merge` | script | Merge a branch |
| `git_pull` | script | Pull from remote |
| `shell_exec` | script | Run arbitrary shell commands |
| `sys_info` | script | System information |
| `you_search` | script | Web search via you.com Search API |
| `search_code` | python | Regex grep in source files |
| `list_files` | python | List files by glob pattern |
| `memory_store` | python | Store info for cross-session recall |
| `memory_recall` | python | Recall stored memories by keyword |

## Permission Model

| Level | Tools | Behavior |
|---|---|---|
| DENY | `git_reset`, `git_revert` | Blocked outright |
| CONFIRM | `git_push`, `git_commit`, `file_write`, `shell_exec` | User approval required |
| ALLOW | Everything else | Executes immediately |
