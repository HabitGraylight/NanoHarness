# Coding Agent Example

A self-contained coding agent built on NanoHarness, with a terminal UI.

## Quick Start

```bash
# 1. Install dependencies (from repo root)
cd ../../..
pip install -e ".[openai,mcp]"

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
  ╔════════════════════════════════════╗
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
  Done. Steps: 3 | Success | Verified: Yes
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
├── main.py                # Entry point (REPL + single-shot)
├── NanoCA.md              # Project instructions loaded into system prompt
├── app/                   # App layer — all coding-agent-specific logic
│   ├── adapters.py        #   OpenAI-compatible LLM adapter
│   ├── background.py      #   Background task executor (thread pool)
│   ├── builder.py         #   Engine assembly — wires all components
│   ├── coding_evaluator.py#   V: error-loop/spinning/stagnation detection + LLM goal verification
│   ├── context.py         #   Three-layer context: spill → compress → summarize
│   ├── dispatch.py        #   Tool registry with path sandboxing
│   ├── handlers.py        #   Script + Python tool registration
│   ├── hooks.py           #   Lifecycle hooks + tool interception (BLOCK/INJECT)
│   ├── mcp.py             #   MCP client — external tool servers via stdio JSON-RPC
│   ├── memory.py          #   File-based memory (.memory/ directory)
│   ├── permissions.py     #   4-step permission pipeline (deny/mode/allow/ask)
│   ├── prompt_builder.py  #   Five-segment system prompt builder
│   ├── resilient_llm.py   #   LLM wrapper: continuation, context compression, retry
│   ├── scheduler.py       #   Cron-based scheduled tasks (recurring + one-shot)
│   ├── skills.py          #   Markdown skill discovery and loading
│   ├── subagent.py        #   Subagent delegation with read-only tool subset
│   ├── task_system.py     #   Task board with dependency chains + worktree binding
│   ├── team.py            #   Long-lived teammate system (daemon threads)
│   ├── tools.py           #   Top-level tool assembly
│   ├── ui.py              #   REPL loop + readline + history
│   └── worktree.py        #   Git worktree task isolation
├── configs/               # Runtime configuration
│   ├── mcp_servers.json   #   MCP server discovery config
│   └── scripts/           #   27 shell script tools (*.sh)
├── skills/                #   Markdown skill definitions
│   ├── code-review.md
│   ├── debugging.md
│   ├── refactoring.md
│   └── test-writing.md
├── sandbox/               # Runtime artifacts (gitignored)
├── nanoharness/           # Symlink → ../../nanoharness (shared kernel)
└── tests/                 # Test suite
    ├── conftest.py        #   Shared fixtures + path setup
    ├── ut/                #   Unit tests (15 files, 291 tests)
    └── st/                #   System/integration tests (11 files, 143 tests)
```

## Architecture

The coding agent follows the NanoHarness **H = (E, T, C, S, L, V)** model. The kernel provides only the six governance components — everything else is in `app/`.

```
NanoEngine
  ├── E (Execution)      — Think→Act→Observe loop (kernel)
  ├── T (Tools)          — DispatchRegistry with 40+ tools (app)
  ├── C (Context)         — ManagedContext: spill/compress/summarize (app)
  ├── S (State)           — JsonStateStore (kernel)
  ├── L (Hooks)           — SimpleHookManager + ToolHookRunner (app)
  └── V (Evaluation)      — CodingAgentEvaluator (app)
         ├── should_stop()       — detect error loops, spinning, stagnation
         └── evaluate_success()  — LLM-based independent goal verification
```

Key subsystems (all app-layer, no kernel changes):

| Subsystem | Module | Purpose |
|---|---|---|
| **Memory** | `memory.py` | File-based `.memory/` directory, YAML frontmatter, keyword search |
| **Tasks** | `task_system.py` | Task board with dependency chains, status transitions, JSON persistence |
| **Worktrees** | `worktree.py` | Git worktree per task — isolated execution lanes |
| **Team** | `team.py` | Spawn teammates with independent Think-Act-Observe loops |
| **Scheduler** | `scheduler.py` | Cron-based recurring + one-shot scheduled tasks |
| **Background** | `background.py` | Run slow commands in background threads |
| **Subagent** | `subagent.py` | Delegate focused subtasks with read-only tool subset |
| **MCP** | `mcp.py` | External tool servers via Model Context Protocol (stdio JSON-RPC) |
| **Skills** | `skills.py` | Markdown skill files with YAML frontmatter |
| **Context** | `context.py` | Three-layer: spill large results → compress old → summarize when long |
| **Resilient LLM** | `resilient_llm.py` | Continuation, context compression, exponential backoff |
| **Evaluation** | `coding_evaluator.py` | Error-loop / spinning / stagnation detection + LLM goal verification |

## Available Tools

40+ tools registered across native scripts, Python, task system, worktree, MCP, and skills:

| Category | Tools |
|---|---|
| **File ops** | `file_read`, `file_write`, `file_edit`, `file_list`, `file_find` |
| **Git ops** | `git_status`, `git_diff`, `git_log`, `git_add`, `git_commit`, `git_push`, `git_branch_*`, `git_stash*`, `git_show`, `git_remote_list`, `git_merge`, `git_pull` |
| **Shell** | `shell_exec`, `sys_info`, `you_search` |
| **Search** | `search_code`, `list_files` |
| **Memory** | `save_memory`, `recall_memory`, `list_memories` |
| **Tasks** | `task_create`, `task_list`, `task_update`, `task_complete` |
| **Worktree** | `worktree_create`, `worktree_enter`, `worktree_run`, `worktree_closeout`, `worktree_list` |
| **Background** | `bg_run`, `bg_poll`, `bg_drain` |
| **Scheduler** | `schedule_create`, `schedule_pause`, `schedule_resume`, `schedule_delete`, `schedule_list` |
| **Team** | `team_spawn`, `team_send`, `team_list`, `team_shutdown` |
| **Subagent** | `task` (delegates subtask with read-only tools) |
| **Skills** | `skill` (discover and load markdown skills) |
| **MCP** | `mcp__{server}__{tool}` (dynamically registered from external servers) |

## Permission Model

4-step pipeline: deny → mode check → allow → user confirmation.

| Level | Tools | Behavior |
|---|---|---|
| DENY | `git_reset`, `git_revert` | Blocked outright |
| ALLOW | `file_read`, `search_code`, `git_status`, `memory_*`, `task`, `skill`, `mcp__filesystem__*` | Executes immediately |
| CONFIRM | `file_write`, `file_edit`, `shell_exec`, `git_commit`, `git_push` | User approval required |

Modes: `interactive` (default, asks user), `auto` (deny unknown), `yolo` (allow all not denied).

## Testing

```bash
# From examples/coding_agent/
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ut/ -v    # Unit tests (291)
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/st/ -v    # Integration tests (143)
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v       # All (434)
```

| Layer | Files | Tests | What's tested |
|---|---|---|---|
| **UT** | 15 | 291 | Pure logic: sandbox, permissions, cron matching, task CRUD, memory I/O, adapter protocol, evaluation detection |
| **ST** | 11 | 143 | Real OS: git worktrees, subprocess MCP, threading, full engine wiring, builder assembly |

UT = no subprocess, no threading, no `time.sleep`. ST = real OS operations, multi-component integration.

## Acknowledgments

This project draws inspiration from [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code). Thanks very much.
