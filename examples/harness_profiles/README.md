# Harness Profile examples

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
python -m nanoharness.profiles validate examples/harness_profiles/coding_team.yaml
python -m nanoharness.profiles explain examples/harness_profiles/coding_team.yaml
```

Validation and explanation are side-effect free. Building the profile requires
the host to bind the three declared runtime capabilities and the `llm.raw`,
`llm.agent`, and `context.agent` services.

## 中文

这些文件用于展示 NanoHarness 应用是可检查、可组合的白箱环境，而不是固定的
Agent 实现。

`coding_team.yaml` 故意把 Team 和 Worktree 写在 Task Board 前面；`validate`
仍会根据能力依赖解析出 Subagent → Task → Team → Worktree 的安全安装顺序。
校验与解释不会创建文件、注册工具或启动线程。真正构建时，宿主必须提供声明的
三个运行时 Capability，以及 `llm.raw`、`llm.agent`、`context.agent` Service。
