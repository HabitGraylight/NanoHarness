# NanoCodex

An independently runnable controlled coding harness. Its Profile binds a
run-scoped Context, evidence evaluator, approval boundary, sandbox-named
executor, Task Board, Worktree lanes, Skills, Background work, and Subagents.

```bash
python examples/nano_codex/main.py
cd examples/nano_codex && pytest -q tests
```

The default run is deterministic and network-free. `app/` owns NanoCodex policy
and future Plan → Execute → Review surface behavior; reusable capabilities come
from `nanoharness.extensions`.

## 中文

NanoCodex 是独立可运行的受控 Coding Harness。默认 Smoke Run 无需网络或 API
Key；Example 自己拥有入口、Profile、Policy、Scenario、测试与文档，可复用能力来自
公共 Extension。后续 Plan → Execute → Review Host 会继续在本目录演进。
