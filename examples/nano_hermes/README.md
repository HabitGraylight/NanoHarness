# NanoHermes

An independently runnable persistent-learning personal agent inspired by Nous
Research Hermes Agent. It combines curated memory, procedural Skills,
scheduling, background work, isolated delegation, and review-gated skill
proposals.

```bash
python examples/nano_hermes/main.py
cd examples/nano_hermes && pytest -q tests
```

The deterministic default does not require an API key. `skill_propose` writes to
a pending area and its Policy requires approval before persistent learning or
external delivery.

## 中文

NanoHermes 是独立可运行的持久学习型个人 Agent。它组合有界 Memory、程序性
Skill、Scheduler、Background 与 Subagent，并把学习结果先写入待审核区；入口、
Profile、Policy、Scenario、测试和文档均由本 Example 自己维护。默认
Smoke Run 无需网络或 API Key。
