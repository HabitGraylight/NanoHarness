<p align="center">
  <img src="assets/NanoharnessHeader.png" alt="NanoHarness" width="760">
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/HabitGraylight/NanoHarness?color=2ea44f" alt="MIT License"></a>
  <a href="https://github.com/HabitGraylight/NanoHarness/commits/main"><img src="https://img.shields.io/github/last-commit/HabitGraylight/NanoHarness?color=6f42c1" alt="Last commit"></a>
</p>

<p align="center">
  <strong>A minimal, composable framework for building white-box AI agents.</strong>
</p>

<p align="center">
  English · <a href="README_CN.md">中文</a>
</p>

NanoHarness is a small Python framework for building LLM agents from explicit, replaceable parts. It provides one execution engine, typed protocols, reusable extensions, declarative profiles, and complete runnable examples—without hiding state, policy, or control flow behind a large application framework.

Use the same foundation to build a coding agent, a persistent personal agent, a multi-channel gateway, or an evidence-gated engineering loop. Each application can choose a different harness while sharing the pieces that should actually be reused.

## Why NanoHarness

- **Minimal kernel** — one engine coordinates tools, context, state, hooks, and evaluation through small interfaces.
- **White-box composition** — application wiring, permissions, prompts, and lifecycle decisions remain visible and replaceable.
- **Reusable middle layer** — capabilities such as memory, skills, MCP, scheduling, channels, tasks, teams, and worktrees live in Extensions.
- **Different harnesses, one foundation** — each Example is a complete, independently runnable agent rather than a thin configuration sample.
- **Built for inspection and recovery** — typed events, checkpoints, traces, durable queues, resumable runs, and explicit approval boundaries are first-class.

## Quick start

```bash
git clone https://github.com/HabitGraylight/NanoHarness.git
cd NanoHarness
pip install -e .

# Run a deterministic multi-source agent with no API key or network.
python examples/nano_openclaw/main.py
```

The kernel depends only on Pydantic and PyYAML. Provider and integration dependencies are optional:

```bash
pip install -e ".[openai]"   # OpenAI-compatible providers
pip install -e ".[mcp]"      # MCP stdio servers
```

The root `main.py` is a minimal provider-backed assembly example. Set `DEEPSEEK_API_KEY`, install the OpenAI extra, and run `python main.py` to try it.

## Complete harness examples

The examples demonstrate why the framework is composable: they share the kernel and public Extensions, but each owns its entry point, Profile, host policy, state model, prompts, scenarios, tests, and documentation.

| Example | Harness style | Run |
|---|---|---|
| [NanoClaudeCode](examples/nano_claude_code/) | Interactive coding sessions with memory, skills, tasks, teams, subagents, and write approval | `python examples/nano_claude_code/profile_demo.py` |
| [NanoCodex](examples/nano_codex/) | Resumable Plan → Execute → Review coding with bounded tools and controlled Git delivery | `python examples/nano_codex/main.py` |
| [NanoHermes](examples/nano_hermes/) | Persistent Assist → Reflect → Host Review with staged learning and scheduled triggers | `python examples/nano_hermes/main.py` |
| [NanoOpenClaw](examples/nano_openclaw/) | Trust-tiered Channel/Scheduler/Background wakeups with durable conversations and separate delivery | `python examples/nano_openclaw/main.py` |
| [NanoLoop](examples/nano_loop/) | Evidence-gated outer loop with budgets, verification, and explicit human gates | `python examples/nano_loop/main.py --help` |

The deterministic entries require no API key or network. They also provide concrete reference implementations for persistence, recovery, policy, approval, and testing—not just happy-path demos.

## How composition works

```text
Application / Example
├── Host policy, prompts, UI, and provider adapter
├── Harness Profile: what to assemble
├── Components: tools, context, state, hooks, evaluator
└── Extensions: memory, channels, scheduler, teams, ...
                         │
                         ▼
                    NanoEngine
               think → act → observe
                         │
                         ▼
              events, checkpoints, report
```

The boundaries are intentional:

1. **Kernel** defines the execution contracts and coordinates a run.
2. **Components** provide small default implementations of those contracts.
3. **Extensions** package reusable capabilities with explicit dependencies and lifecycle management.
4. **Applications** decide what the agent is allowed to do and how users interact with it.

The engine does not know application-specific prompts, permission rules, approval UI, sandbox implementation, or delivery policy.

## A small kernel with explicit contracts

The kernel coordinates six replaceable concerns. You do not need to learn this model before using NanoHarness; the names simply describe the seams available to an application.

| Concern | Contract | Responsibility |
|---|---|---|
| Execution | `NanoEngine` / `LLMProtocol` | Think → Act → Observe loop and termination |
| Tools | `BaseToolRegistry` | Typed tool catalog, validation, and routing |
| Context | `BaseContextManager` | Prompt and conversation composition |
| State | `BaseStateStore` | Checkpoints and cross-turn persistence |
| Lifecycle | `BaseHookManager` | Instrumentation around run and step boundaries |
| Evaluation | `BaseEvaluator` | Trajectory recording, early stop, and success verdict |

```python
from nanoharness import (
    DictToolRegistry,
    JsonStateStore,
    NanoEngine,
    SimpleContextManager,
    SimpleHookManager,
    TraceEvaluator,
)

# my_llm implements LLMProtocol.
engine = NanoEngine(
    llm_client=my_llm,
    tools=DictToolRegistry(),
    context=SimpleContextManager(system_prompt="You are a careful assistant."),
    state=JsonStateStore("run_state.json"),
    hooks=SimpleHookManager(),
    evaluator=TraceEvaluator(),
)

report = engine.run("Inspect the task and report what you find.")
```

Tool execution has its own replaceable path:

```text
ToolCall → PolicyDecision → optional Approval → ToolExecutor → Observation
```

This keeps permission policy, human approval, and local/sandboxed/remote execution independent from one another.

## Reusable Extensions

Extensions install tools and services into an explicit `ExtensionContext`. Each has a versioned manifest, typed configuration, dependency declarations, installation receipt, and managed shutdown.

| Extension | Reusable capability |
|---|---|
| Memory | Markdown-backed save, recall, and listing |
| Skills | Metadata discovery and on-demand instruction loading |
| MCP | Official SDK stdio sessions and dynamic tool discovery |
| Background | Managed shell processes and completion notifications |
| Channels | Durable transport-neutral Inbox/Outbox, approval, retry, and adapters |
| Scheduler | Persistent cron/delay triggers and fire-time notifications |
| Tasks | Dependency-aware task board, claims, roles, and tools |
| Teams | Long-lived teammates and a persisted inbox/request protocol |
| Subagents | One-shot isolated delegation with optional context forking |
| Worktrees | Audited Git worktree lanes bound to tasks |

```python
from nanoharness import DictToolRegistry, ExtensionContext, ExtensionManager
from nanoharness.extensions.memory import MemoryExtension

context = ExtensionContext(tools=DictToolRegistry())
extensions = ExtensionManager(context)
extensions.install(MemoryExtension(), {"directory": ".memory"})

memory = context.services["memory"]
print(extensions.inspect())
extensions.close()
```

## Declarative Profiles and inspection

`HarnessSpec` describes a harness in YAML, TOML, or JSON: host requirements, Extensions, and engine service bindings. `HarnessBuilder` validates the Profile without side effects, resolves dependencies, explains providers and conflicts, and builds the declared composition.

```bash
python -m nanoharness.profiles validate recipes/coding_team.yaml
python -m nanoharness.profiles explain recipes/coding_team.yaml
python -m nanoharness.profiles catalog
python -m nanoharness.profiles matrix \
  recipes/solo_subagent.yaml recipes/coding_team.yaml
python -m nanoharness.profiles compare \
  recipes/traces/solo.json recipes/traces/team.json
```

Profile matrices expose exact component, policy, capability, Extension, and host service differences. Trace comparison reports factual runtime differences without assigning a winner or exposing raw thoughts, tool arguments, outputs, or evaluator explanations.

## Runtime and recovery surfaces

NanoHarness keeps reliability mechanisms visible instead of burying them in a host application:

- stable run/session identity and terminal run status;
- lossless multi-tool steps with provider call IDs;
- ordered event streams and redacting/JSONL/console sinks;
- crash-readable checkpoints with trajectory, stop reason, and errors;
- official evaluator success verdicts and mid-loop early stopping;
- async run and event-stream APIs;
- cooperative cancellation and steering at safe step boundaries;
- durable Inbox/Outbox, scheduled notifications, and resumable host state in the relevant Extensions and examples.

Compatibility fields and synchronous `NanoEngine.run(query)` remain available.

## Repository layout

```text
nanoharness/
  core/          # Protocols, schemas, NanoEngine, run control
  components/    # Minimal default implementations
  extensions/    # Reusable capability packages
  profiles/      # Declarative composition and inspection
  testing/       # Deterministic providers, scenarios, artifacts
examples/        # Five complete, independently runnable harnesses
recipes/         # Profile and trace inspection fixtures
tests/           # Kernel, Extension, Profile, and example contracts
```

## Testing

The repository currently contains **1,012 passing tests**:

| Suite | Tests |
|---|---:|
| Kernel, Extensions, Profiles, and contracts | 563 |
| NanoClaudeCode | 151 |
| NanoCodex | 96 |
| NanoHermes | 86 |
| NanoOpenClaw | 89 |
| NanoLoop | 27 |

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -q

cd examples/nano_openclaw
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -q
```

Run each example's `tests/` directory independently to verify its application policy and integration boundaries. Real MCP stdio tests use the optional `mcp` dependency.

## Security

Tool-using agents can change files, run processes, and communicate externally. Production applications should combine least-privilege tools, explicit policy, approval gates, sandboxed execution, secret redaction, and prompt-injection defenses. NanoHarness provides the boundaries; the application owns the policy.

## Research foundation and citation

The kernel's six concerns are informed by the [Agent Harness Survey](https://github.com/Gloriaameng/Awesome-Agent-Harness). The framework is usable without adopting its notation; the survey is a useful reference for comparing harness architectures.

```bibtex
@software{nanoharness2026,
  title     = {NanoHarness: A Minimal and Composable Agent Harness Framework},
  author    = {Habit},
  year      = {2026},
  url       = {https://github.com/HabitGraylight/NanoHarness},
  license   = {MIT}
}
```

```bibtex
@article{meng2026agentharness,
  title  = {Agent Harness for Large Language Model Agents: A Survey},
  author = {Meng, Qianyu and Wang, Yanan and Chen, Liyi and others},
  year   = {2026},
  url    = {https://www.preprints.org/manuscript/202604.0428/v2}
}
```

## License

MIT
