# NanoCodex

NanoCodex is an independently runnable controlled coding agent built from the
same NanoHarness kernel and reusable extensions as the other examples. Its host
runs three separate `NanoEngine` sessions and persists every phase transition:

| Phase | Host-owned transition | Controlled effect |
|---|---|---|
| Plan | `plan_submit` | Inspects with bounded read/list/search/status/diff tools, then creates persistent Task Board records and a task-bound Git worktree |
| Execute | `execution_finish` | Uses exact write/patch operations and named host-configured tests only inside the worktree; mutations require approval |
| Review | `review_submit` + `delivery_submit` | Records an advisory review and an approved delivery request; trusted host evidence decides whether delivery may run |

The default job is deterministic, network-free, and requires only local Git:

```bash
python examples/nano_codex/main.py --output /tmp/nano-codex-demo
```

Reusing the same `--output` directory resumes an interrupted or blocked phase.
The approval boundary can be inspected by deliberately denying the Execute
write and then resuming it:

```bash
python examples/nano_codex/main.py \
  --output /tmp/nano-codex-approval --deny-writes
python examples/nano_codex/main.py --output /tmp/nano-codex-approval
```

The first command exits unsuccessfully with the run persisted in `execute`;
the second records a new approval and continues through Review.

For a real OpenAI-compatible provider and an existing clean Git repository, use
the provider-driven job (which contains no fixtures or scripted responses):

```bash
export OPENAI_API_KEY=...
python examples/nano_codex/main.py \
  --job examples/nano_codex/jobs/live.yaml \
  --repo /path/to/project \
  --output /tmp/nano-codex-live \
  --provider openai \
  --model YOUR_MODEL \
  --task "Implement the requested change"
```

Real-provider runs default to interactive approval. The job controls which
delivery modes may be requested: `keep` leaves a dirty isolated worktree,
`commit` commits there, `apply` cherry-picks the approved commit into the source
repository, and `merge` creates a merge commit in the source repository. Source
delivery refuses a dirty source or a source `HEAD` that moved after the run
started.

## White-box boundaries

- `profile.yaml` declares engine service bindings and composes Task, Worktree,
  Skills, and read-only Subagent extensions.
- `app/host.py` owns phase orchestration, clean source cloning, resume behavior,
  trusted completion, and idempotent Git delivery.
- `nanoharness.components.OpenAIChatProvider` supplies the optional reusable
  OpenAI-compatible SDK adapter while the deterministic job uses `ScriptedLLM`.
- `app/policy.py` exposes per-phase tool allowlists. Write, exact patch, and
  delivery requests are approval-gated; raw shell and channel delivery are not
  available.
- `app/tools.py` provides bounded list/search/read, exact write/patch,
  status/diff, and named trusted-test tools. It rejects traversal, symlink
  escapes, and internal `.git`/`.nano_codex` paths.
- `app/approvals.py` supports host callbacks or terminal confirmation and keeps
  content-minimized audit records.
- `app/store.py` atomically persists application state independently of each
  engine checkpoint.
- `jobs/demo.yaml` is a complete Plan/Execute/Review job with scripted provider
  responses, fixture files, and trusted evidence.
- `jobs/live.yaml` is a provider-driven job for real repositories with no
  scripted responses or fixture writes.
- `scenarios/smoke.yaml` remains a small shared-protocol fixture used for
  cross-example Profile and Scenario checks.

The output directory contains `runtime/run.json`, Task Board state, worktree
audit data, per-phase engine checkpoints/events, full reports, and minimized
traces. Approval records retain the tool, call ID, decision, and path but not
the written content. Command evidence uses an argument vector with
`shell=False`; evidence commands are trusted host configuration, not model
arguments.

```bash
cd examples/nano_codex
pytest -q tests
```

The 96 application tests cover job/state contracts, phase policy, approval redaction,
provider adaptation, trusted evidence, bounded tools, interruption/resume,
existing repositories, and all four delivery modes.

## 中文

NanoCodex 是一个独立可运行的受控 Coding Agent。Host 将 Plan、Execute、Review
实现为三次彼此独立的 `NanoEngine` 运行，并原子持久化阶段状态。Plan 使用有界工具
检查仓库并创建持久 Task 与任务绑定的 Git worktree；Execute 只能在活动 worktree
中执行精确写入/替换和 Host 预配置的可信命令；Review 的 Agent 结论只作为建议，
最终是否交付由 Host 可信证据决定。

重复使用同一个 `--output` 目录即可从中断或阻断阶段继续。真实 Provider 可通过
`jobs/live.yaml`、`--repo`、`--provider openai` 与 `--model` 接入，默认使用终端交互
审批。交付支持 keep、commit、apply 和 merge，并在写回源仓库前检查其 clean 状态与
起始 HEAD。`profile.yaml` 负责声明并组合公共 Extension，`app/` 负责 NanoCodex 特有
的阶段编排、策略、工具边界、审批、证据和交付，因此这个 Example 可以整体复制和
独立演化，也不会依赖其他 Example 的应用代码。
