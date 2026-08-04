<p align="center">
  <img src="assets/NanoharnessMain.png" alt="NanoHarness" width="640">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/Tests-580%20passed-brightgreen.svg" alt="Tests">
  <img src="https://img.shields.io/badge/Framework-ETCSLV-purple.svg" alt="ETCSLV">
</p>

<h1 align="center">NanoHarness</h1>

<p align="center">
  <b>A minimal agent harness based on H&nbsp;=&nbsp;(E,&nbsp;T,&nbsp;C,&nbsp;S,&nbsp;L,&nbsp;V)</b>
</p>

English | [中文](README_CN.md)

---

## What

NanoHarness is a minimal Python framework for building tool-augmented LLM agents. It implements the six-component governance model from the [Agent Harness Survey](https://github.com/Gloriaameng/Awesome-Agent-Harness):

| | Component | Responsibility |
|:---:|---|---|
| **E** | Execution Loop | Think → Act → Observe cycle, termination, error recovery |
| **T** | Tool Registry | Typed tool catalog, routing, schema validation |
| **C** | Context Manager | Context window composition and compaction |
| **S** | State Store | Cross-turn persistence and crash recovery |
| **L** | Lifecycle Hooks | Cross-cutting instrumentation: logging, policy, auth |
| **V** | Evaluation | Trajectory recording, mid-loop early-stop detection, independent goal verification |

The kernel provides **only** these six interfaces and one orchestration engine. Everything else — which LLM to call, how to manage memory, whether to enforce permissions — is determined by the application.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       NanoHarness Kernel                        │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │  E: NanoEngine                                          │  │
│   │                                                         │  │
│   │    ON_START ──► Think ──► Act ──► Observe ──► ON_STEP   │  │
│   │                    │         │          │       │        │  │
│   │                    ▼         ▼          ▼       ▼        │  │
│   │               LLMProtocol  T: Tools  C: Context         │  │
│   │                                              V: Eval    │  │
│   │                                    should_stop? ──► STOP │  │
│   │                                                         │  │
│   │    ON_END ◄── V: Report + evaluate_success              │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
│   Interfaces:  BaseToolRegistry  BaseContextManager             │
│                BaseStateStore    BaseHookManager                │
│                BaseEvaluator     LLMProtocol                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                   constructor injection
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Application Layer                          │
│                                                                 │
│   LLM adapter  ·  memory strategy  ·  permission policy        │
│   tool assembly  ·  prompt templates  ·  UI / output           │
│                                                                 │
│   Wiring: main.py or per-project builder                        │
└─────────────────────────────────────────────────────────────────┘
```

**Design principle:** The engine has no application-specific knowledge of prompts, memory, policy rules, approval UI, sandbox implementation, or output UI. It only coordinates injected protocols, making the kernel safe to share across different agent applications.

---

## Structure

```
nanoharness/
  core/                  # Kernel: interfaces + engine
    schema.py            #   Messages, tool executions, run/checkpoint, events, evaluation
    base.py              #   ETCSLV ABCs, LLMProtocol, HookStage
    engine.py            #   NanoEngine (with mid-loop evaluation)
    runtime.py           #   RunControl (cooperative cancellation + steering)
    prompt.py            #   PromptManager (YAML template loader)
  components/            # Minimal ETCSLV implementations
    tools/               #   T: DictToolRegistry, ScriptToolRegistry
    context/             #   C: SimpleContextManager
    state/               #   S: JsonStateStore
    hooks/               #   L: SimpleHookManager
    lifecycle/           #   Policy, approval, executor, and event components
    evaluator/           #   V: TraceEvaluator (with should_stop + evaluate_success)
  extensions/            # Reusable capability packages
    base.py              #   Manifest, config, install, close, and receipt contracts
    manager.py           #   Dependency/conflict validation + inventory/lifecycle
    memory/              #   FileMemoryManager + MemoryExtension
    mcp/                 #   MCP stdio clients + dynamic MCP tools
    skills/              #   SkillRegistry + SkillsExtension
  utils/                 # get_logger, count_tokens
configs/
  prompts.yaml           # Prompt templates
  scripts/               # Shell-script tools (auto-discovered, 27 tools)
examples/
  coding_agent/          # Full-featured coding agent reference (434 tests)
  nano_loop/             # Evidence-gated Loop Engineering example (27 tests)
tests/                   # 119 kernel tests
```

---

## Quick Start

```bash
git clone https://github.com/HabitGraylight/NanoHarness.git
cd NanoHarness
pip install -e .
```

The kernel depends only on Pydantic and PyYAML. LLM clients and other integrations are installed by each application as needed.

```bash
# Run the minimal example
python main.py

# Run the coding agent
cd examples/coding_agent && python main.py

# Run an evidence-gated loop
cd examples/nano_loop
python main.py run configs/loops/local_fix.yaml --repo ../.. --task "Your task"
```

---

## Engine Loop

```
NanoEngine.run(query)
     │
     ├─ L.trigger(ON_TASK_START)
     ├─ C.add_message(user)
     │
     └─ loop until terminated or max_steps:
          │
          ├─ Think:  E → LLM.chat(C.get_full_context(), T.get_schemas())
          ├─ L.trigger(ON_THOUGHT_READY)
          │
          ├─ Act:    for each tool_call:
          │            PolicyDecision → optional ApprovalBroker
          │            → ToolExecutor (registry / sandbox / remote)
          │            C.add_message(observation)
          │
          ├─ S.save_state()
          ├─ V.log_step()
          ├─ V.should_stop()?  ──► early break if stuck/spinning
          └─ L.trigger(ON_STEP_END)

     ├─ V.get_report()        (includes evaluate_success verdict)
     └─ L.trigger(ON_TASK_END)
```

No application policy rules, approval UI, or sandbox logic live inside the engine. All of that flows through injected protocol implementations.

---

## Core Protocol v2

Core Protocol v2 preserves `NanoEngine.run(query)` and the existing dictionary
report while adding:

- stable run/session identity, protocol version, and terminal run status;
- provider ToolCall IDs with deterministic engine-generated fallback IDs;
- lossless multi-tool traces through `StepResult.actions`;
- ordered `HarnessEvent` streams and an optional `EventSinkProtocol`;
- crash-readable `RunCheckpoint` snapshots containing query, trajectory, stop reason, and errors;
- run-local evaluator trajectories while context may remain session-local;
- `EvaluationResult.achieved` as the official success verdict.

The legacy `StepResult.action` and `observation` fields remain available and
represent the final tool execution in a step.

## Lifecycle Policy & Async Runtime

Protocol v2.1 adds explicit boundaries around the tool lifecycle:

- `ToolPolicyProtocol` returns a typed `PolicyDecision` for pre/post-tool stages;
- `ApprovalBrokerProtocol` handles interactive or remote approval separately from policy;
- `ToolExecutorProtocol` is the replaceable boundary for local, sandboxed, or remote execution;
- `CompositeToolPolicy` combines permission and hook policies with deterministic precedence;
- `EventBus`, `RedactingEventSink`, `JsonlEventSink`, and `ConsoleEventSink` form a composable real-time observability pipeline;
- `NanoEngine.arun()` and `NanoEngine.astream()` provide async report and live-event surfaces;
- `RunControl` provides cooperative cancellation and steering at safe step boundaries.

The legacy `permissions=` and `tool_hooks=` constructor arguments remain supported. The Coding Agent builder now uses the typed policy and approval path.

---

## Reusable Extensions

Extension Protocol 1.0 gives reusable capabilities a common white-box shape:

- `ExtensionManifest` declares versioned capabilities, requirements, and conflicts;
- each extension exposes a Pydantic configuration schema before installation;
- `ExtensionContext` is the explicit tool/service/capability installation surface;
- `ExtensionInstallation` is a serializable receipt of installed tools and services;
- `ExtensionManager.inspect()` returns the resolved capability inventory;
- `ExtensionManager.close()` releases resource-owning extensions once, in reverse installation order.

```python
from nanoharness import DictToolRegistry, ExtensionContext, ExtensionManager
from nanoharness.extensions.memory import MemoryExtension

context = ExtensionContext(tools=DictToolRegistry())
extensions = ExtensionManager(context)
extensions.install(MemoryExtension(), {"directory": ".memory"})

print(extensions.inspect())
memory = context.services["memory"]
extensions.close()
```

Extracted capabilities currently include:

- `MemoryExtension` — Markdown memory store plus save/recall/list tools;
- `SkillsExtension` — directory discovery, metadata index, and on-demand instruction loading;
- `MCPExtension` — official MCP SDK stdio sessions, dynamic tool discovery, redacted config receipts, and managed subprocess cleanup.

The Coding Agent installs all three through `ExtensionManager`. Its old `app.memory`, `app.skills`, `app.mcp`, and tool-registration imports remain compatibility forwarding layers rather than duplicate implementations. MCP remains optional: install `nanoharness[mcp]` only for profiles that need external servers.

---

## Tools

Tools satisfy `BaseToolRegistry` with two methods: `get_tool_schemas()` and `call(name, args)`.

Two built-in registries:

- **DictToolRegistry** — register Python functions via `@tool` decorator. JSON Schema is inferred from type hints.
- **ScriptToolRegistry** — auto-discovers `.sh` files in a directory. Parameters are declared via `@param` comment headers and passed as environment variables.

Registries compose via `merge()`.

Adding a new tool does not require touching any Python code — drop a shell script with the right headers into `configs/scripts/` and it is automatically available to the agent.

---

## Extending

The kernel defines interfaces. Applications provide concrete behavior:

**LLM** — implement `LLMProtocol`:
```python
def chat(self, messages, tools=None) -> LLMResponse: ...
```

**Custom components** — subclass any `Base*` ABC and inject into `NanoEngine`.

See `examples/coding_agent/` for a reference that wires together a custom LLM adapter, memory strategy, permission pipeline, subagent delegation, skill loading, and evaluation — all built on top of the kernel without modifying it.

See `examples/nano_loop/` for an outer-loop control plane that repeatedly creates fresh NanoEngine runs, verifies their artifacts, persists evidence, enforces budgets, and stops at explicit human gates.

---

## Testing

```bash
# Kernel tests (119)
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# Coding agent tests (434: 291 UT + 143 ST)
cd examples/coding_agent
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# NanoLoop tests (27)
cd ../nano_loop
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v
```

**Total: 580 tests.** Kernel tests require only the kernel dependencies and pytest; real MCP stdio tests use the `mcp` optional dependency.

---

## Roadmap

- Streaming LLM output
- Async engine mode
- Multi-agent orchestration
- Context compaction strategies
- Observability integration (OpenTelemetry / LangFuse)
- Harness Completeness Matrix — automated ETCSLV coverage reporting

---

## Security

Agents with tool access can cause real damage. Production deployments should implement permission gates, sandbox execution, and prompt injection defenses. See the coding agent example for a reference permission pipeline.

---

## Acknowledgments

The theoretical foundation of this project is based on the [Agent Harness Survey](https://github.com/Gloriaameng/Awesome-Agent-Harness).

---

## Citation

```bibtex
@software{nanoharness2026,
  title     = {NanoHarness: A Minimal Agent Harness Based on H=(E,T,C,S,L,V)},
  author    = {Habit},
  year      = {2026},
  url       = {https://github.com/HabitGraylight/NanoHarness},
  license   = {MIT}
}
```

Theoretical foundation:

```bibtex
@article{meng2026agentharness,
  title     = {Agent Harness for Large Language Model Agents: A Survey},
  author    = {Meng, Qianyu and Wang, Yanan and Chen, Liyi and others},
  year      = {2026},
  url       = {https://www.preprints.org/manuscript/202604.0428/v2}
}
```

---

## License

MIT
