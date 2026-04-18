<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/Tests-89%20passed-brightgreen.svg" alt="Tests">
</p>

<h1 align="center">NanoHarness</h1>

<p align="center">
  <b>A minimal, composable AI agent harness framework in Python.</b><br>
  <span style="color:gray">Think → Act → Observe — with pluggable everything.</span>
</p>

English | [中文](README_CN.md)

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
- [Examples](#examples)
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

1. **Minimalism** — no heavy dependencies, no opaque abstractions. The core engine is a thin, policy-free loop.
2. **Composability** — every component (LLM adapter, tool registry, memory, permissions, hooks) is injectable and swappable.
3. **Reusability** — clean interfaces (`LLMProtocol`, `BaseToolRegistry`, etc.) make it easy to extend or embed in larger systems.

The framework implements a **Think → Act → Observe** agent loop:

```
User Query → [Context] → LLM Think → Tool Act → Observation → [repeat] → Report
```

**The kernel is policy-free.** `NanoEngine` only coordinates the loop — memory injection, prompt rendering, permission I/O, and output formatting are all handled by injected components and hooks in the app layer. This makes it safe to share the kernel across different agent applications (e.g., `main.py` and `examples/coding_agent/` use the same `nanoharness/`).

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          NanoEngine                              │
│                    (policy-free loop kernel)                     │
│                                                                  │
│   ┌──────────┐                              ┌────────────────┐  │
│   │  Context  │                              │   HookManager  │  │
│   │ Manager   │                              │ ON_START/END   │  │
│   └────┬─────┘                              │ ON_THOUGHT/STEP│  │
│        │                                     └────────────────┘  │
│        ▼                                                           │
│   ┌────────────────────────────┐     ┌───────────────────────┐  │
│   │      LLMProtocol           │     │  PermissionManager    │  │
│   │  ┌──────┐ ┌──────┐        │     │  enforce() → None|err │  │
│   │  │OpenAI│ │Anthro│  ...   │     └───────────────────────┘  │
│   │  └──────┘ └──────┘        │                                 │
│   └────────────┬───────────────┘                                │
│                │                                                  │
│                ▼                                                  │
│   ┌────────────────────────────────┐     ┌───────────────────┐  │
│   │     BaseToolRegistry           │     │    Evaluator       │  │
│   │  ┌──────────┐  ┌───────────┐  │     │  (Trace)           │  │
│   │  │ Script   │  │   MCP     │  │     └───────────────────┘  │
│   │  │ Registry │  │ Registry  │  │                             │
│   │  └──────────┘  └───────────┘  │     ┌───────────────────┐  │
│   │        └────── merge() ──────┘     │  StateStore       │  │
│   └────────────────────────────────┘   │  (JSON)           │  │
│                                        └───────────────────┘  │
└──────────────────────────────────────────────────────────────────┘

    App Layer (main.py / examples/coding_agent/app/)
    ┌─────────────────────────────────────────────────────┐
    │  PromptManager.from_file()    Memory injection hooks │
    │  Permission rules             Memory persistence     │
    │  Tool assembly                Terminal UI / output   │
    └─────────────────────────────────────────────────────┘
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
├── nanoharness/                     # Core package (policy-free kernel)
│   ├── core/                        # Framework kernel — stable, rarely modified
│   │   ├── schema.py                #   Pydantic models: ToolCall, LLMResponse, StepResult,
│   │   │                            #   PermissionRule, MemoryEntry, AgentMessage
│   │   ├── base.py                  #   Abstract base classes & protocols
│   │   │                            #   LLMProtocol, BaseToolRegistry, BaseContextManager,
│   │   │                            #   BaseStateStore, BaseEvaluator, BaseHookManager,
│   │   │                            #   BasePermissionManager, BaseMemoryManager, HookStage
│   │   ├── engine.py                #   NanoEngine — pure Think→Act→Observe loop
│   │   │                            #   No memory, no prompts, no permission I/O
│   │   └── prompt.py                #   PromptManager — template registry via from_file()
│   │
│   ├── components/                  # Pluggable implementations
│   │   ├── llm/                     #   LLM adapters (LLMProtocol implementations)
│   │   │   ├── openai_adapter.py    #     OpenAI / DeepSeek (compatible API)
│   │   │   ├── anthropic_adapter.py #     Anthropic Claude
│   │   │   ├── litellm_adapter.py   #     LiteLLM (multi-provider gateway)
│   │   │   └── vllm_adapter.py      #     vLLM (local inference)
│   │   │
│   │   ├── tools/                   #   Tool registries (BaseToolRegistry implementations)
│   │   │   ├── dict_registry.py     #     DictToolRegistry — @tool decorator, JSON Schema inference
│   │   │   └── script_tools.py      #     ScriptToolRegistry — auto-discovers *.sh scripts
│   │   │
│   │   ├── mcp/                     #   MCP (Model Context Protocol) integration
│   │   │   ├── client.py            #     MCPClient — sync wrapper over async MCP SDK
│   │   │   └── registry.py          #     MCPToolRegistry — adapts MCP tools to OpenAI schema
│   │   │
│   │   ├── context/                 #   SimpleContextManager
│   │   ├── memory/                  #   SimpleMemoryManager + MemoryToolMixin (app utility)
│   │   ├── permissions/             #   RulePermissionManager (injectable approval callback)
│   │   ├── hooks/                   #   SimpleHookManager
│   │   ├── state/                   #   JsonStateStore
│   │   └── evaluator/               #   TraceEvaluator
│   │
│   └── utils/                       # get_logger, count_tokens
│
├── configs/
│   ├── prompts.yaml                 # Prompt templates (centralized)
│   ├── mcp_servers.json             # MCP server definitions
│   └── scripts/                     # Shell-script tools (26 tools, auto-discovered)
│       ├── git_*.sh                 #   19 Git operations
│       ├── file_*.sh                #   5 File I/O operations
│       ├── sys_info.sh              #   System information
│       └── shell_exec.sh            #   Generic shell command execution
│
├── examples/
│   └── coding_agent/                # Self-contained coding agent example
│       ├── main.py                  #   Terminal UI entry point (REPL)
│       ├── app/                     #   App layer (wiring + config)
│       │   ├── builder.py           #     Engine assembly + memory hooks
│       │   ├── hooks.py             #     Colored terminal output hooks
│       │   ├── ui.py                #     REPL loop + readline support
│       │   ├── tools.py             #     Script tools + Python-native search
│       │   ├── permissions.py       #     Coding-specific permission policy
│       │   └── prompts.yaml         #     Coding-agent-specific prompts
│       ├── nanoharness/             #   Symlink → ../../nanoharness (shared kernel)
│       ├── configs/                 #   Scripts + config for the example
│       └── tests/                   #   Smoke tests (9 tests)
│
└── tests/                           # Root test suite (80 tests)
    ├── conftest.py
    ├── test_engine.py
    ├── test_dict_registry.py
    ├── test_script_tools.py
    ├── test_mcp.py
    ├── test_memory.py
    ├── test_permissions.py
    ├── test_simple_context.py
    ├── test_simple_hooks.py
    ├── test_json_store.py
    ├── test_trace_evaluator.py
    └── test_utils.py
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
git clone https://github.com/HabitGraylight/NanoHarness.git
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

Edit `configs/mcp_servers.json` to register MCP servers, then:

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
# Interactive mode (root demo)
python main.py

# Coding agent example (with terminal UI)
cd examples/coding_agent
python main.py

# Single-shot mode
python main.py "Add type hints to all functions in nanoharness/core/"
```

---

## Core Design

### Engine Loop

`NanoEngine` (`nanoharness/core/engine.py`) is a **policy-free loop orchestrator**:

1. **Hooks** — `ON_TASK_START` is triggered (app layer handles memory injection).
2. **Think** — the full context is sent to the LLM along with available tool schemas.
3. **Permission gate** — `permissions.enforce()` returns `None` (proceed) or an error string (skip).
4. **Act** — the tool is executed, and the observation is appended to context.
5. **Evaluate** — each step is logged by the evaluator.
6. **Terminate** — when the LLM responds without tool calls, the loop ends and a report is generated.
7. **Hooks** — `ON_TASK_END` is triggered (app layer handles memory persistence).

The engine has **no knowledge** of PromptManager, MemoryManager, or terminal I/O. All policy is injected.

### Tool System

Tools follow the `BaseToolRegistry` interface:

| Implementation | Source | Key Feature |
|---|---|---|
| `DictToolRegistry` | Python `@tool` decorator | Auto-infers JSON Schema from type hints |
| `ScriptToolRegistry` | `configs/scripts/*.sh` | Auto-discovers shell scripts, parses `@param` headers |
| `MCPToolRegistry` | MCP servers via `MCPClient` | Discovers remote tools, converts to OpenAI schema |

All registries produce **OpenAI-compatible tool schemas** and can be **merged** via `registry.merge(other)`.

### Permission System

Three permission levels controlled by glob patterns. Approval I/O is decoupled via `approval_callback`:

```python
# Terminal approval (default)
perms = RulePermissionManager()

# Custom approval (e.g., for API server)
perms = RulePermissionManager(approval_callback=lambda name, args: auto_approve(name))

perms.deny("git_reset")              # Glob match — also denies git_reset_hard
perms.confirm("git_push", "shell_exec")
```

| Level | Behavior |
|---|---|
| `ALLOW` | Tool executes immediately |
| `CONFIRM` | `approval_callback` is consulted (defaults to terminal `y/N`) |
| `DENY` | Tool call is blocked |

### Memory System

Dual-layer memory managed by the **app layer** (not the engine):

- **Working memory** — ephemeral dict, cleared per run. For intra-run state passing.
- **Persistent memory** — JSON file with keyword search. Stores run summaries and explicit `memory_store` calls.

Memory injection and persistence are implemented as hooks registered by the app layer (e.g., in `main.py` or `app/builder.py`).

### MCP Integration

[MCP (Model Context Protocol)](https://modelcontextprotocol.io/) enables the agent to use tools exposed by external servers:

- **`MCPClient`** — synchronous wrapper over the async MCP Python SDK, with configurable timeout.
- **`MCPToolRegistry`** — discovers tools from a connected MCP client, converts `inputSchema` to OpenAI format.

### Prompt Management

Templates are loaded explicitly via `PromptManager.from_file()`:

```python
from nanoharness.core.prompt import PromptManager
pm = PromptManager.from_file("configs/prompts.yaml")
pm.render("memory.inject", entries=mem_text)   # Variable substitution
```

The kernel does not auto-load any config file. Each app loads its own prompt templates.

---

## Examples

### Coding Agent

A self-contained coding agent with terminal UI, located in `examples/coding_agent/`:

- Uses the kernel via symlink (`nanoharness/ → ../../nanoharness`)
- Provides its own app layer: prompts, tools, permissions, hooks, UI
- Features a REPL with colored output, readline support, and input history
- Runtime artifacts are isolated in `sandbox/`

```bash
cd examples/coding_agent
export DEEPSEEK_API_KEY="sk-..."
python main.py
```

See [examples/coding_agent/README.md](examples/coding_agent/README.md) for details.

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

Connect to an MCP server and create a registry:

```python
from nanoharness.components.mcp import MCPClient, MCPToolRegistry
client = MCPClient()
client.connect_stdio("myserver", "npx", args=["-y", "some-mcp-server"])
mcp_tools = MCPToolRegistry(client)
```

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
# Root tests (80 tests)
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# Coding agent example tests (9 tests)
cd examples/coding_agent
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v
```

Test coverage includes:
- Schema models (Pydantic validation)
- Engine loop (terminate, tool call, error handling, hooks, context)
- Tool registries (decorator, scripts, MCP, merge)
- Permission system (rules, glob matching, enforce)
- Memory system (store, recall, persistence, tool mixin)
- All other components (context, hooks, state, evaluator, utils)
- Example assembly (builder, permissions, prompts, engine run)

---

## Roadmap

- [ ] **Streaming support** — token-by-token LLM output for real-time feedback
- [ ] **Multi-agent orchestration** — coordinate multiple NanoEngine instances
- [ ] **Vector memory backend** — replace keyword search with embedding-based retrieval
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
> - Be cautious of **prompt injection** — tool outputs may contain malicious instructions.
> - Be aware of **sandbox escape** risks — the agent may chain tool calls in unexpected ways.
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
