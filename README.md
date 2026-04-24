<p align="center">
  <img src="assets/NanoharnessMain.png" alt="NanoHarness" width="640">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/Tests-63%20passed-brightgreen.svg" alt="Tests">
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
| **V** | Evaluation | Step-level trajectory recording and outcome reporting |

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
│   │                    │         │          │                │  │
│   │                    ▼         ▼          ▼                │  │
│   │               LLMProtocol  T: Tools  C: Context         │  │
│   │                                                         │  │
│   │    ON_END ◄── V: Report ◄── S: State ◄────────────────  │  │
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

**Design principle:** The engine has zero knowledge of prompts, memory, permissions, or I/O. All behavior is injected. This makes the kernel safe to share across different agent applications.

---

## Structure

```
nanoharness/
  core/                  # Kernel: interfaces + engine
    schema.py            #   ToolCall, LLMResponse, AgentMessage, StepResult
    base.py              #   ETCSLV ABCs, LLMProtocol, HookStage
    engine.py            #   NanoEngine
    prompt.py            #   PromptManager (YAML template loader)
  components/            # Minimal ETCSLV implementations
    tools/               #   T: DictToolRegistry, ScriptToolRegistry
    context/             #   C: SimpleContextManager
    state/               #   S: JsonStateStore
    hooks/               #   L: SimpleHookManager
    evaluator/           #   V: TraceEvaluator
  utils/                 # get_logger, count_tokens
configs/
  prompts.yaml           # Prompt templates
  scripts/               # Shell-script tools (auto-discovered, 26 tools)
examples/
  coding_agent/          # Full-featured coding agent reference
tests/                   # 63 tests
```

---

## Quick Start

```bash
git clone https://github.com/HabitGraylight/NanoHarness.git
cd NanoHarness
pip install -e .
```

The kernel has no required external dependencies. LLM clients and other integrations are installed by each application as needed.

```bash
# Run the minimal example
python main.py

# Run the coding agent
cd examples/coding_agent && python main.py
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
          │            optional permission gate → T.call(name, args)
          │            C.add_message(observation)
          │
          ├─ S.save_state()
          ├─ V.log_step()
          └─ L.trigger(ON_STEP_END)

     ├─ V.get_report()
     └─ L.trigger(ON_TASK_END)
```

No memory, no prompt rendering, no permission logic inside the engine. All of that flows through injected components and hooks.

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

See `examples/coding_agent/` for a reference that wires together a custom LLM adapter, memory strategy, permission pipeline, subagent delegation, and skill loading — all built on top of the kernel without modifying it.

---

## Testing

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v
```

63 tests covering schema models, engine loop, tool registries, and all ETCSLV components. No external dependencies required.

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
