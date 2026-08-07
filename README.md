<p align="center">
  <img src="assets/NanoharnessMain.png" alt="NanoHarness" width="640">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/Tests-913%20passed-brightgreen.svg" alt="Tests">
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
    llm/                 #   E: optional OpenAI-compatible provider adapter
    tools/               #   T: DictToolRegistry, ScriptToolRegistry
    context/             #   C: SimpleContextManager
    state/               #   S: JsonStateStore
    hooks/               #   L: SimpleHookManager
    lifecycle/           #   Policy, approval, executor, and event components
    evaluator/           #   V: TraceEvaluator (with should_stop + evaluate_success)
  extensions/            # Reusable capability packages
    base.py              #   Manifest, config, install, close, and receipt contracts
    manager.py           #   Dependency/conflict validation + inventory/lifecycle
    background/          #   Managed shell processes + completion notifications
    channels/            #   Durable transport-neutral inbox/outbox + adapters
    memory/              #   FileMemoryManager + MemoryExtension
    mcp/                 #   MCP stdio clients + dynamic MCP tools
    scheduler/           #   Persistent cron/delay scheduling service
    skills/              #   SkillRegistry + SkillsExtension
    subagents/           #   One-shot isolated delegation runtime
    tasks/               #   Persistent dependency-aware Task Board
    teams/               #   Managed long-lived teammates + inbox protocol
    worktrees/           #   Git worktree lanes bound to Task records
  profiles/              # Profiles, staged assembly, traces, comparisons, matrices
  testing/               # Deterministic scenarios, scripted provider, artifacts
  utils/                 # get_logger, count_tokens
configs/
  prompts.yaml           # Prompt templates
  scripts/               # Shell-script tools (auto-discovered, 27 tools)
recipes/                 # Declarative Profile and Trace inspection fixtures
  coding_team.yaml
  solo_subagent.yaml
  traces/
examples/
  nano_claude_code/      # Provider-backed interactive coding (151 tests)
  nano_codex/            # Controlled coding harness (96 tests)
  nano_hermes/           # Persistent learning personal agent (86 tests)
  nano_openclaw/         # Minimal gateway-style harness
  nano_loop/             # Evidence-gated Loop Engineering example (27 tests)
tests/                   # 552 kernel/extension/profile/example tests
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
cd examples/nano_claude_code && python main.py

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

The legacy `permissions=` and `tool_hooks=` constructor arguments remain supported. NanoClaudeCode binds the typed policy and approval protocols through its application builder.

---

## Reusable Extensions

Extension Protocol 1.0 gives reusable capabilities a common white-box shape:

- `ExtensionManifest` declares versioned capabilities, requirements, and conflicts;
- each extension exposes a Pydantic configuration schema before installation;
- `ExtensionContext` is the explicit tool/service/capability installation surface;
- `ExtensionInstallation` is a serializable receipt of installed tools and services;
- `NotificationSourceProtocol` gives long-lived services one host-facing `drain()` contract;
- `ExtensionManager.inspect()` returns capabilities, services, receipts, and resolved dependency edges;
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

Reusable capability packages include:

- `MemoryExtension` — Markdown memory store plus save/recall/list tools;
- `SkillsExtension` — directory discovery, metadata index, and on-demand instruction loading;
- `MCPExtension` — official MCP SDK stdio sessions, dynamic tool discovery, redacted config receipts, and managed subprocess cleanup;
- `BackgroundExtension` — bounded shell execution, workspace-confined working directories, completion notifications, and shutdown cancellation;
- `ChannelExtension` — transport-neutral durable ingress/outbox state, message deduplication, claim recovery, explicit delivery approval/retry, adapter idempotency keys, and a deterministic Mock adapter; its queue-only send tool is registered explicitly by a Host with a Run scope;
- `SchedulerExtension` — persistent cron/delay prompts with a managed checker thread and fired notifications;
- `TaskExtension` — persistent dependency-aware tasks, claims, roles, and schema-first task tools;
- `TeamExtension` — long-lived teammate loops, persisted inbox/request protocol, Task Board auto-claiming, notifications, and joined shutdown;
- `SubagentExtension` — one-shot read-only delegation with optional parent-context forking;
- `WorktreeExtension` — audited Git execution lanes with `requires=["tasks.board"]` and task binding.

NanoClaudeCode installs all nine through `ExtensionManager`. Team and Subagent declare their host/runtime dependencies explicitly, so `inspect()` shows both extension-provided and host-provided edges. Its local modules expose the public extension APIs needed by the application. MCP remains optional: install `nanoharness[mcp]` only for profiles that need external servers.

## Harness Profiles

HarnessSpec 1.0 turns extension composition and ETCSLV runtime bindings into a
portable YAML, TOML, or JSON recipe. The declaration names host-provided
capabilities/services without serializing live LLM or Context objects.

`HarnessBuilder` validates configs without side effects, resolves capability
dependencies into a deterministic installation order, explains providers and
conflicts, installs extensions, and can bind the resulting tool registry plus
host services into a `NanoEngine`.

`StagedAssembler` handles applications whose host services can only be bound
after bootstrap extensions are installed. It preserves one explicit sequence:
bootstrap extensions → host bind → runtime extensions. The application can then
construct its Engine from the completed service bindings.

```bash
python -m nanoharness.profiles validate recipes/coding_team.yaml
python -m nanoharness.profiles explain recipes/coding_team.yaml
python -m nanoharness.profiles catalog
python -m nanoharness.profiles matrix recipes/solo_subagent.yaml recipes/coding_team.yaml
python -m nanoharness.profiles trace recipes/traces/solo.json
python -m nanoharness.profiles compare recipes/traces/solo.json recipes/traces/team.json
```

```python
from nanoharness import HarnessBuilder, HarnessSpec

spec = HarnessSpec.from_file("recipes/coding_team.yaml")
builder = HarnessBuilder()

validation = builder.validate(spec)  # no files, tools, or threads are created
explanation = builder.explain(spec)  # manifests, schemas, order, dependency edges

# build = builder.build(spec, context=host_extension_context)
# engine = build.engine              # when spec.engine is declared
# build.close()                      # closes installed resource extensions
```

`explain` redacts common secret fields and complete `env` mappings. Offline
validation treats declared host requirements as assumptions; `build()` checks
the real capability and service bindings before installing anything.

`matrix` turns one or more profiles into ETCSLV, policy, capability, extension,
and host-service rows. `trace` normalizes NanoEngine reports, checkpoints, and
JSONL event streams into content-minimized metrics. It omits raw thoughts, tool
arguments, observations, outputs, and evaluator explanations. `compare` accepts
two profiles or two traces and reports factual differences without assigning a
winner.

---

## Independently Runnable Harnesses

Each harness owns its entry point, Profile, app policy, scenarios, tests, and
documentation. They reuse NanoHarness packages but never import another
example's application code:

- **NanoClaudeCode** — interactive, session-oriented coding with memory,
  skills, task/team delegation, and interactive write approval;
- **NanoCodex** — resumable Plan → Execute → Review coding over managed or
  existing Git repositories, with an optional real provider, bounded coding
  tools, interactive mutation/delivery approval, trusted evidence, and
  keep/commit/apply/merge delivery;
- **NanoHermes** — resumable Assist → Reflect → Host Review personal agent with
  cross-run memory/skills, content-addressed staged learning, independent
  action and promotion approval, due-schedule triggers, and isolated delegation;
- **NanoOpenClaw** — persistent gateway shape with console/webhook/mock channel
  capabilities, memory, scheduling, and background work.

```bash
python examples/nano_claude_code/profile_demo.py
python examples/nano_codex/main.py
python examples/nano_hermes/main.py
python examples/nano_openclaw/main.py
python examples/nano_loop/main.py --help
```

The deterministic smoke entries require no API key or network and save a private
Report plus a content-minimized Trace. NanoClaudeCode additionally keeps its real
provider-backed REPL. Cross-example Matrix and Trace comparison use the built-in
`python -m nanoharness.profiles matrix/compare` commands; inspection utilities
are not placed under `examples/`.

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

See `examples/nano_claude_code/` for the full NanoClaudeCode implementation. It wires together a custom LLM adapter, memory strategy, permission pipeline, subagent delegation, skill loading, and evaluation without modifying the kernel.

See `examples/nano_loop/` for an outer-loop control plane that repeatedly creates fresh NanoEngine runs, verifies their artifacts, persists evidence, enforces budgets, and stops at explicit human gates.

---

## Testing

```bash
# Kernel, public extension, profile, and example-contract tests (552)
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# NanoClaudeCode application tests (151: 109 UT + 42 ST)
cd examples/nano_claude_code
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# NanoCodex protocol/tool/host/delivery tests (96)
cd ../nano_codex && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# NanoHermes persistent-learning suite (86)
cd ../nano_hermes && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# NanoOpenClaw independent smoke suite (1)
cd ../nano_openclaw && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# NanoLoop tests (27)
cd ../nano_loop
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v
```

**Total: 913 tests.** Reusable Extension behavior is tested under the root
suite; each example suite focuses on application-owned composition and policy.
Kernel tests require only the kernel dependencies and pytest; real MCP stdio
tests use the `mcp` optional dependency.

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
