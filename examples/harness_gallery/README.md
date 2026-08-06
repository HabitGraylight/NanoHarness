# Harness Gallery

The Gallery demonstrates that NanoHarness is a white-box environment for
building materially different agent harnesses from the same ETCSLV kernel.

- `nano_claude_code.yaml` describes an interactive, session-oriented coding
  harness. The current implementation baseline remains `examples/coding_agent`.
- `nano_codex.yaml` describes controlled Plan → Execute → Review runs with task,
  worktree, approval, and sandbox boundaries.
- `nano_hermes.yaml` describes a persistent personal agent with curated memory,
  procedural skills, staged learning proposals, scheduling, and delegation.
- `nano_openclaw.yaml` describes a persistent multi-channel gateway host with
  memory, background work, and scheduling.

The Profiles differ in host capabilities, service bindings, policy mode,
context lifetime, and installed extensions. They are not prompt aliases.

## Deterministic smoke demo

No API key, network, Git remote, or external message channel is required.

```bash
python examples/harness_gallery/main.py profiles
python examples/harness_gallery/main.py matrix
python examples/harness_gallery/main.py run nano_claude_code
python examples/harness_gallery/main.py run nano_codex
python examples/harness_gallery/main.py run nano_hermes
python examples/harness_gallery/main.py run nano_openclaw
```

The shared Scenario materializes an isolated fixture and replays
provider-neutral model responses through `ScriptedLLM`. Each run persists:

- `report.json` — complete private run report, including raw trajectory;
- `trace.json` — content-minimized metrics safe for comparison;
- `artifact.json` — paths and run identity.

Runtime output is written below `.runs/`, which is ignored by Git. Raw reports
may contain prompts, thoughts, arguments, and tool output and must not be
published blindly.

## 中文

Harness Gallery 用同一套 ETCSLV 内核展示三种真实不同的 Harness：交互式
NanoClaudeCode、受控 Plan → Execute → Review 的 NanoCodex、持续学习的
NanoHermes，以及常驻多渠道 NanoOpenClaw。差异体现在 Host Capability、Service 绑定、Policy、Context 生命周期
和 Extension 组合，不是只更换 Prompt。

默认 Smoke Demo 使用确定性 ScriptedLLM，不需要 API Key、网络、Git Remote 或
真实消息渠道。完整 Report 属于敏感产物；用于公开比较时应使用最小化 Trace。

NanoHermes 的边界参考 Nous Research 的官方
[Hermes Agent](https://github.com/NousResearch/hermes-agent)、
[Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/)
与 [Skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
文档：长期记忆保持有界且经过整理，程序性经验进入按需 Skill；学习结果先暂存并
经过审批，而不是直接静默修改活动技能目录。
