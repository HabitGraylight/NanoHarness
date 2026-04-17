<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/Tests-91%20passed-brightgreen.svg" alt="Tests">
</p>

<h1 align="center">NanoHarness</h1>

<p align="center">
  <b>A minimal, composable AI agent harness framework in Python.</b><br>
  <span style="color:gray">Think → Act → Observe — with pluggable everything.</span>
</p>

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Configuration](#configuration)
  - [Running the Agent](#running-the-agent)
- [Core Design](#core-design)
  - [Engine Loop](#engine-loop)
  - [Tool System](#tool-system)
  - [Permission System](#permission-system)
  - [Memory System](#memory-system)
  - [MCP Integration](#mcp-integration)
  - [Prompt Management](#prompt-management)
- [Adding Custom Tools](#adding-custom-tools)
- [Adding LLM Providers](#adding-llm-providers)
- [Testing](#testing)
- [Roadmap](#roadmap)
- [Security Advisory](#security-advisory)
- [Citation](#citation)
- [License](#license)

---

## Overview

NanoHarness is a lightweight harness engineering framework for building AI agents that interact with tools in a controlled, observable loop. It is designed around three principles:

1. **Minimalism** — no heavy dependencies, no opaque abstractions. The core is ~150 lines of engine code.
2. **Composability** — every component (LLM adapter, tool registry, memory, permissions, hooks) is injectable and swappable.
3. **Reusability** — clean interfaces (`LLMProtocol`, `BaseToolRegistry`, etc.) make it easy to extend or embed in larger systems.

The framework implements a **Think → Act → Observe** agent loop:

```
User Query → [Context] → LLM Think → Tool Act → Observation → [repeat] → Report
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          NanoEngine                              │
│                                                                  │
│   ┌──────────┐   ┌──────────────┐   ┌────────────────────────┐  │
│   │  Context  │   │  PromptMgr   │   │    Evaluator (Trace)   │  │
│   │ Manager   │   │  (YAML)      │   │    log + report        │  │
│   └────┬─────┘   └──────┬───────┘   └────────────────────────┘  │
│        │                 │                                      │
│        ▼                 ▼                                      │
│   ┌────────────────────────────┐     ┌───────────────────────┐  │
│   │      LLMProtocol           │     │   HookManager         │  │
│   │  ┌──────┐ ┌──────┐        │     │  ON_START / ON_END    │  │
│   │  │OpenAI│ │Anthro│  ...   │     │  ON_THOUGHT / ON_STEP │  │
│   │  └──────┘ └──────┘        │     └───────────────────────┘  │
│   └────────────┬───────────────┘                               │
│                │                                               │
│                ▼                                               │
│   ┌────────────────────────────────┐   ┌───────────────────┐  │
│   │     BaseToolRegistry           │   │  PermissionMgr    │  │
│   │  ┌──────────┐  ┌───────────┐  │   │  ALLOW / CONFIRM  │  │
│   │  │ Script   │  │   MCP     │  │   │  DENY (glob)      │  │
│   │  │ Registry │  │ Registry  │  │   └───────────────────┘  │
│   │  │ (*.sh)   │  │ (FastMCP) │  │                          │
│   │  └──────────┘  └───────────┘  │   ┌───────────────────┐  │
│   │        └────── merge() ──────┘   │  MemoryManager     │  │
│   └────────────────────────────────┘   │  Working + Persist│  │
│                                        └───────────────────┘  │
│   ┌────────────────┐                                           │
│   │  StateStore    │             ┌──────────────┐             │
│   │  (JSON file)   │             │  SandboxExec │             │
│   └────────────────┘             └──────────────┘             │
└──────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
NanoHarness/
├── main.py                          # Entry point — wires components, starts agent
├── pyproject.toml                   # Package metadata & dependencies
├── requirements.txt                 # Quick-install dependency list
├── LICENSE                          # MIT License
│
├── nanoharness/                     # Core package
│   ├── core/                        # Framework kernel — stable, rarely modified
│   │   ├── schema.py                #   Pydantic models: ToolCall, LLMResponse, StepResult,
│   │   │                            #   PermissionRule, MemoryEntry, AgentMessage
│   │   ├── base.py                  #   Abstract base classes & protocols
│   │   │                            #   LLMProtocol (duck-typed), BaseToolRegistry,
│   │   │                            #   BaseContextManager, BaseStateStore, BaseEvaluator,
│   │   │                            #   BaseHookManager, BasePermissionManager, BaseMemoryManager
│   │   │                            #   HookStage enum
│   │   ├── engine.py                #   NanoEngine — Think→Act→Observe agent loop
│   │   │                            #   Handles: LLM calls, tool dispatch, permission gates,
│   │   │                            #   memory injection, state persistence, step evaluation
│   │   └── prompt.py                #   PromptManager — loads configs/prompts.yaml,
│   │                                #   provides get() / render() / add() for templates
│   │
│   ├── components/                  # Pluggable implementations
│   │   ├── llm/                     #   LLM adapters (LLMProtocol implementations)
│   │   │   ├── openai_adapter.py    #     OpenAI / DeepSeek (compatible API)
│   │   │   ├── anthropic_adapter.py #     Anthropic Claude
│   │   │   ├── litellm_adapter.py   #     LiteLLM (multi-provider gateway)
│   │   │   └── vllm_adapter.py      #     vLLM (local inference)
│   │   │
│   │   ├── tools/                   #   Tool registries (BaseToolRegistry implementations)
│   │   │   ├── dict_registry.py     #     DictToolRegistry — @tool decorator, JSON Schema
│   │   │   │                        #     inference, merge() for combining registries
│   │   │   └── script_tools.py      #     ScriptToolRegistry — auto-discovers *.sh scripts,
│   │   │                            #     parses @param headers, passes args as env vars
│   │   │
│   │   ├── mcp/                     #   MCP (Model Context Protocol) integration
│   │   │   ├── client.py            #     MCPClient — sync wrapper over async MCP SDK
│   │   │   │                        #     (background event-loop thread + run_coroutine_threadsafe)
│   │   │   └── registry.py          #     MCPToolRegistry — adapts MCP tools to OpenAI schema,
│   │   │                            #     extends DictToolRegistry, mergeable with ScriptToolRegistry
│   │   │
│   │   ├── context/                 #   Context management
│   │   │   └── simple_context.py    #     SimpleContextManager — message list with system prompt
│   │   │
│   │   ├── memory/                  #   Dual-layer memory system
│   │   │   └── simple_memory.py     #     SimpleMemoryManager — working memory (dict per-run)
│   │   │                            #     + persistent memory (JSON file with keyword search)
│   │   │                            #     MemoryToolMixin — exposes memory_store / memory_recall as tools
│   │   │
│   │   ├── permissions/             #   Permission & sandbox
│   │   │   ├── rule_permission.py   #     RulePermissionManager — glob patterns, 3-level control
│   │   │   └── sandbox.py           #     SandboxExecutor — subprocess isolation with timeout
│   │   │
│   │   ├── hooks/                   #   Lifecycle hooks
│   │   │   └── simple_hooks.py      #     SimpleHookManager — register/trigger by HookStage
│   │   │
│   │   ├── state/                   #   State persistence
│   │   │   └── json_store.py        #     JsonStateStore — save/load/reset run state
│   │   │
│   │   └── evaluator/               #   Run evaluation
│   │       └── trace_evaluator.py   #     TraceEvaluator — logs steps, generates summary report
│   │
│   └── utils/                       # Shared utilities
│       ├── logger.py                #   get_logger() — module-level logger factory
│       └── token_counter.py         #   count_tokens() / count_messages_tokens()
│
├── configs/                         # Configuration (no code)
│   ├── prompts.yaml                 #   All prompt templates — centralized, variable-substitutable
│   ├── mcp_servers.json             #   MCP server definitions (transport, command, args)
│   └── scripts/                     #   Shell-script tools (26 tools, auto-discovered)
│       ├── git_*.sh                 #     19 Git operations (status, log, diff, commit, push, ...)
│       ├── file_*.sh                #     5 File I/O (read, write, edit, list, find)
│       ├── sys_info.sh              #     System information
│       └── shell_exec.sh            #     Generic shell command execution
│
└── tests/                           # Test suite (mirrors package structure)
    ├── conftest.py                  #   Shared fixtures (MockLLMClient)
    ├── test_schema.py               #   Schema model tests
    ├── test_engine.py               #   Engine loop tests (terminate, tool call, error, hooks, context)
    ├── test_dict_registry.py        #   @tool decorator & DictToolRegistry tests
    ├── test_script_tools.py         #   Script tool loading + functional tests (git, file, sys)
    ├── test_mcp.py                  #   MCP integration tests (mock FastMCP server)
    ├── test_memory.py               #   Memory store/recall/persistence + MemoryToolMixin
    ├── test_permissions.py          #   Permission rules, glob matching, blocked params
    ├── test_simple_context.py       #   Context manager tests
    ├── test_simple_hooks.py         #   Hook manager tests
    ├── test_json_store.py           #   State store tests
    ├── test_trace_evaluator.py      #   Evaluator tests
    └── test_utils.py                #   Logger & token counter tests
```

---

## Quick Start

### Prerequisites

| Dependency | Version | Notes |
|---|---|---|
| Python | >= 3.10 | Uses `typing.ParamSpec`, `match` syntax |
| pip | latest | For package installation |
| Conda (optional) | any | Recommended for environment isolation |
| An LLM API key | — | DeepSeek, OpenAI, Anthropic, or local vLLM |

### Installation

```bash
# 1. Create and activate environment
conda create -n harness python=3.10 -y
conda activate harness

# 2. Clone the repository
git clone https://github.com/your-org/NanoHarness.git
cd NanoHarness

# 3. Install in editable mode (includes core dependencies)
pip install -e .

# 4. Install optional providers as needed
pip install -e ".[openai]"        # OpenAI / DeepSeek
pip install -e ".[anthropic]"     # Anthropic Claude
pip install -e ".[litellm]"       # LiteLLM multi-provider
pip install -e ".[mcp]"           # MCP integration

# Or install everything at once
pip install -e ".[all-providers]"
```

### Configuration

**1. Set your API key:**

```bash
# DeepSeek (default)
export DEEPSEEK_API_KEY="sk-..."

# Or swap to OpenAI in main.py
export OPENAI_API_KEY="sk-..."
```

**2. (Optional) Enable MCP tools:**

Edit `configs/mcp_servers.json` to register MCP servers:

```json
{
  "servers": [
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "transport": "stdio"
    }
  ]
}
```

Then enable MCP loading via environment variable:

```bash
export NANOHARNESS_MCP=1
```

**3. (Optional) Configure permissions in `main.py`:**

```python
perms = RulePermissionManager()
perms.deny("git_reset")                        # Block outright
perms.confirm("git_push", "git_commit")        # Require user approval
perms.confirm("shell_exec")                    # Require user approval
```

### Running the Agent

```bash
# Interactive mode
python main.py

# With MCP tools enabled
NANOHARNESS_MCP=1 python main.py
```

The agent will prompt for input, enter the Think → Act → Observe loop, and output a run report upon completion.

---

## Core Design

### Engine Loop

`NanoEngine` (`nanoharness/core/engine.py`) orchestrates the agent loop:

1. **Memory injection** — relevant memories are loaded into context before the loop starts.
2. **Think** — the full context is sent to the LLM along with available tool schemas.
3. **Permission gate** — each tool call is checked against permission rules (`DENY` → skip, `CONFIRM` → prompt user).
4. **Act** — the tool is executed, and the observation is appended to context.
5. **Evaluate** — each step is logged by the evaluator.
6. **Terminate** — when the LLM responds without tool calls, the loop ends and a report is generated.

### Tool System

Tools follow the `BaseToolRegistry` interface:

| Implementation | Source | Key Feature |
|---|---|---|
| `DictToolRegistry` | Python `@tool` decorator | Auto-infers JSON Schema from type hints |
| `ScriptToolRegistry` | `configs/scripts/*.sh` | Auto-discovers shell scripts, parses `@param` headers |
| `MCPToolRegistry` | MCP servers via `mcp_servers.json` | Discovers remote tools, converts to OpenAI schema |

All registries produce **OpenAI-compatible tool schemas** and can be **merged** via `registry.merge(other)` — enabling hybrid tool sets (e.g., local scripts + remote MCP tools).

### Permission System

Three permission levels controlled by glob patterns:

| Level | Behavior |
|---|---|
| `ALLOW` | Tool executes immediately |
| `CONFIRM` | User must approve before execution |
| `DENY` | Tool call is blocked |

```python
perms = RulePermissionManager()
perms.deny("git_reset")              # Glob match — also denies git_reset_hard
perms.confirm("git_push", "shell_exec")
# Everything else defaults to ALLOW
```

### Memory System

Dual-layer memory:

- **Working memory** — ephemeral dict, cleared per run. For intra-run state passing.
- **Persistent memory** — JSON file with keyword search. Stores run summaries and explicit `memory_store` calls.

Memory is injected into context before each run and persisted automatically after completion.

### MCP Integration

[MCP (Model Context Protocol)](https://modelcontextprotocol.io/) enables the agent to use tools exposed by external servers. The integration consists of:

- **`MCPClient`** — synchronous wrapper over the async MCP Python SDK. Spawns a background `asyncio` event loop thread and bridges via `run_coroutine_threadsafe`.
- **`MCPToolRegistry`** — discovers tools from connected MCP servers, converts `inputSchema` to OpenAI function-calling format, and delegates `call()` to the client.

MCP tools are fully compatible with the existing tool system — they can be merged with script tools and are governed by the same permission rules.

### Prompt Management

All prompt templates are centralized in `configs/prompts.yaml` and accessed via `PromptManager`:

```python
from nanoharness.core.prompt import PromptManager
pm = PromptManager()
pm.render("memory.inject", entries=mem_text)   # Variable substitution
```

This ensures no hardcoded prompt strings exist in the codebase. Add new prompts by editing the YAML file.

---

## Adding Custom Tools

### Shell Script Tools (recommended)

Add a `.sh` file to `configs/scripts/` with `@param` headers:

```bash
#!/bin/bash
# @param query:str:The search query
# @param limit:int:Max results (default: 10)

echo "Searching for: $query (limit: $limit)"
```

The script is **automatically discovered and registered** — no Python changes needed. Arguments are passed as environment variables.

### Python Function Tools

Use the `@tool` decorator on a `DictToolRegistry`:

```python
from nanoharness.components.tools.dict_registry import DictToolRegistry

registry = DictToolRegistry()

@registry.tool
def my_tool(name: str, count: int = 5) -> str:
    """Do something useful."""
    return f"Processed {name} x{count}"
```

### MCP Server Tools

Add an entry to `configs/mcp_servers.json` and enable with `NANOHARNESS_MCP=1`.

---

## Adding LLM Providers

Implement the `LLMProtocol` (duck-typed):

```python
from nanoharness.core.base import LLMProtocol

class MyAdapter:
    def chat(self, messages, tools=None) -> LLMResponse:
        # Call your LLM API here
        ...
```

Then pass it to `NanoEngine(llm_client=MyAdapter(), ...)`.

---

## Testing

```bash
# Run all tests (91 tests)
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# Run a specific test module
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_mcp.py -v

# Note: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 prevents conflicts with
# ROS pytest plugins that may be present in the environment.
```

Test coverage includes:
- Schema models (Pydantic validation)
- Engine loop (terminate, tool call, error handling, hooks, context)
- Tool registries (decorator, scripts, MCP, merge)
- Permission system (rules, glob matching, blocked params)
- Memory system (store, recall, persistence, tool mixin)
- All other components (context, hooks, state, evaluator, utils)

---

## Roadmap

- [ ] **Streaming support** — token-by-token LLM output for real-time feedback
- [ ] **Multi-agent orchestration** — coordinate multiple NanoEngine instances
- [ ] **Vector memory backend** — replace keyword search with embedding-based retrieval
- [ ] **Web UI** — browser-based interaction panel for agent monitoring
- [ ] **ReAct / Reflexion patterns** — pluggable reasoning strategies beyond Think→Act→Observe
- [ ] **Async engine mode** — native async support alongside the current sync loop
- [ ] **Tool result caching** — avoid redundant tool calls within and across runs
- [ ] **Observability integration** — OpenTelemetry / LangFuse tracing support

---

## Security Advisory

> **Warning: AI agents with tool access can cause real-world damage.**
>
> When operating LLM-driven agents, **always configure permission rules carefully**:
>
> - Use `DENY` for destructive operations (e.g., `git_reset`, `rm`).
> - Use `CONFIRM` for operations with external side effects (e.g., `git_push`, `shell_exec`).
> - Be cautious of **prompt injection** — tool outputs may contain malicious instructions that influence the agent's subsequent behavior.
> - Be aware of **sandbox escape** risks — the agent may attempt to chain tool calls in unexpected ways to bypass permission restrictions.
> - Review and audit tool scripts regularly. Shell scripts in `configs/scripts/` run with the full privileges of the host user.
>
> **Never grant unrestricted tool access to an agent without human oversight.**

---

## Citation

If you use NanoHarness in your research or project, please cite:

```bibtex
@software{nanoharness2026,
  title     = {NanoHarness: A Minimal Composable AI Agent Harness Framework},
  author    = {Habit},
  year      = {2026},
  url       = {https://github.com/HabitGraylight/NanoHarness},
  license   = {MIT}
}
```

---

## License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.

```
MIT License

Copyright (c) 2026 Habit

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
