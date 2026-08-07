# NanoHermes

NanoHermes is an independently runnable persistent-learning personal agent. A
run crosses three explicit trust boundaries:

| Stage | Model capability | Host-owned result |
|---|---|---|
| Assist | Recall memory, load skills, read or approved-write the workspace, manage approved schedules, and use isolated delegation | `assist_submit` persists the user-facing response |
| Reflect | Inspect the completed interaction and stage Memory or Skill proposals | `reflection_submit` closes the model-controlled phase |
| Host Review | No model tools | The host validates source Run, content hash, staged audit, and active-target revision before requesting promotion approval |

The network-free demo creates an approved reminder, stages one Memory and one
Skill, and promotes both after host review:

```bash
python examples/nano_hermes/main.py --output /tmp/nano-hermes-demo
```

Every invocation normally creates an independent Run while reusing the same
workspace, Memory catalog, Skill catalog, and Scheduler. Resume an interrupted
Run by ID:

```bash
python examples/nano_hermes/main.py \
  --output /tmp/nano-hermes-demo \
  --resume hermes_RUN_ID
```

For a real OpenAI-compatible provider, use the provider-driven job:

```bash
export OPENAI_API_KEY=...
python examples/nano_hermes/main.py \
  --job examples/nano_hermes/jobs/live.yaml \
  --output /tmp/nano-hermes-live \
  --provider openai \
  --model YOUR_MODEL \
  --task "Help with this task and retain useful durable context"
```

Real-provider runs default to interactive approval. `--approval` controls
workspace and Scheduler mutations; `--learning-approval` independently controls
Memory/Skill promotion. Both accept `auto`, `deny`, or `interactive`.

Run prompts whose persistent schedules are newly due as independent scheduled
Runs:

```bash
python examples/nano_hermes/main.py \
  --job examples/nano_hermes/jobs/live.yaml \
  --output /tmp/nano-hermes-live \
  --provider openai --model YOUR_MODEL --run-due
```

## White-box boundaries

- `app/host.py` owns run creation/resume, Assist/Reflect engine isolation,
  persistent context injection, scheduled triggers, and final completion.
- `app/tools.py` owns bounded workspace access and deterministic,
  content-addressed proposal staging. It cannot write active Memory or Skills.
- `app/learning.py` validates staged proposals and performs crash-recoverable
  promotion. Concurrent target changes invalidate a proposal instead of being
  overwritten.
- `app/approvals.py` keeps action audits and learning decisions separate. Audit
  records contain identifiers and hashes, not workspace content or proposal
  bodies.
- `app/policy.py` denies direct `save_memory` and external delivery. Scheduler
  mutations and workspace writes require action approval.
- `profile.yaml` composes public Memory, Skills, Scheduler, and read-only
  Subagent extensions. It exposes no raw background shell.

Private runtime data is stored below `runtime/`: one state file and phase
checkpoint/event directory per Run, active `memory/` and `skills/` catalogs,
content-bearing `staged/` audits, and `schedules.json`. Full reports remain under
`artifacts/`; minimized traces omit model thoughts and tool arguments.

```bash
cd examples/nano_hermes
pytest -q tests
```

The 86 tests cover protocol validation, atomic state, phase policy, redacted
approvals, proposal integrity and conflicts, promotion recovery, bounded tools,
cross-Run reuse, provider interruption/resume, due schedules, and CLI wiring.

## 中文

NanoHermes 是一个独立可运行的持久学习型个人 Agent。每次 Run 依次经过 Assist、
Reflect 与 Host Review：模型在 Assist 阶段完成任务，在 Reflect 阶段只能暂存 Memory
或 Skill 候选；Host 随后核对来源 Run、内容哈希、暂存审计和活动目标版本，并通过
独立的学习审批决定是否晋升。

同一个 `--output` 下的各次 Run 相互独立，但共享 Workspace、Memory、Skill 与
Scheduler。动作审批和学习晋升审批彼此独立；拒绝学习不会让用户任务失败，也不会
修改活动目录。到期 Schedule 会产生新的 scheduled Run。真实 Provider 使用
`jobs/live.yaml` 与 `--provider openai` 接入，默认启用交互审批。
