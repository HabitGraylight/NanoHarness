# Harness recipes

These files demonstrate that a NanoHarness application is an inspectable
composition rather than a fixed agent implementation.

`coding_team.yaml` intentionally lists Team and Worktree before Task Board.
`validate` resolves the actual safe order as:

1. `subagents.delegate` — its requirements are provided by the host;
2. `tasks.board` — no extension dependency;
3. `teams.runtime` — now both `runtime.llm` and `tasks.board` are available;
4. `worktrees.git` — now `tasks.board` is available.

Run:

```bash
python -m nanoharness.profiles validate recipes/coding_team.yaml
python -m nanoharness.profiles explain recipes/coding_team.yaml
python -m nanoharness.profiles matrix recipes/solo_subagent.yaml recipes/coding_team.yaml
python -m nanoharness.profiles trace recipes/traces/solo.json
python -m nanoharness.profiles compare recipes/traces/solo.json recipes/traces/team.json
```

Validation and explanation are side-effect free. Building the profile requires
the host to bind the three declared runtime capabilities and the `llm.raw`,
`llm.agent`, and `context.agent` services.

`matrix` compares declared ETCSLV bindings, policies, capabilities, extensions,
and host services. `trace` accepts a NanoEngine report, checkpoint, or JSONL
event stream. Its output keeps counts, statuses, timings, and character lengths,
but deliberately omits raw thoughts, tool arguments, observations, and outputs.
`compare` accepts either two profiles or two traces and reports factual deltas;
it does not choose a winner.

## Optional integrations

[Local code inspection with Sem](sem_code/README.md) demonstrates stdio MCP discovery and a tool call against an explicitly selected Git checkout. It requires an optional external binary, uses a dedicated local-mode session, and does not add a default server. Integration recipes stay separate from the complete agents in `examples/`.

## 中文

这些文件用于展示 NanoHarness 应用是可检查、可组合的白箱环境，而不是固定的
Agent 实现。

`coding_team.yaml` 故意把 Team 和 Worktree 写在 Task Board 前面；`validate`
仍会根据能力依赖解析出 Subagent → Task → Team → Worktree 的安全安装顺序。
校验与解释不会创建文件、注册工具或启动线程。真正构建时，宿主必须提供声明的
三个运行时 Capability，以及 `llm.raw`、`llm.agent`、`context.agent` Service。

`matrix` 自动比较 Profile 的 ETCSLV 绑定、策略、能力、扩展和宿主 Service；
`trace` 可读取 NanoEngine Report、Checkpoint 或 JSONL Event Stream，并且只保留
计数、状态、耗时和字符长度，不输出原始 Thought、工具参数、Observation 或输出。
`compare` 可以比较两个 Profile 或两个 Trace，只呈现事实差异，不替用户判定胜负。

### 可选集成

[使用 Sem 检索本地代码](sem_code/README.md)展示通过 stdio MCP 发现工具，并查询显式指定的 Git 仓库。该配方需要可选的外部程序，使用独立的本地模式会话，不添加默认服务器。此类单项集成配方与 `examples/` 中的完整 Agent 分开存放。
