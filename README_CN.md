<p align="center">
  <img src="assets/NanoharnessHeader.png" alt="NanoHarness" width="760">
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/HabitGraylight/NanoHarness?color=2ea44f" alt="MIT License"></a>
  <a href="https://github.com/HabitGraylight/NanoHarness/commits/main"><img src="https://img.shields.io/github/last-commit/HabitGraylight/NanoHarness?color=6f42c1" alt="Last commit"></a>
</p>

<p align="center">
  <strong>一个用于构建白箱 AI Agent 的极简、可组合框架。</strong>
</p>

<p align="center">
  <a href="README.md">English</a> · 中文
</p>

NanoHarness 是一个小型 Python 框架，用明确、可替换的部件构建 LLM Agent。它提供一个执行引擎、类型化协议、可复用 Extension、声明式 Profile 和完整可运行示例，同时避免把状态、策略和控制流藏在庞大的应用框架里。

你可以用同一套基础搭建 Coding Agent、持久个人 Agent、多渠道 Gateway 或证据驱动的工程循环。每个应用可以选择完全不同的 Harness，只共享真正应该复用的部分。

## 为什么选择 NanoHarness

- **极简内核** — 一个引擎通过小型接口协调工具、上下文、状态、Hook 与评估。
- **白箱组合** — 应用的组装方式、权限、Prompt 和生命周期决策都可见、可替换。
- **可复用中间层** — Memory、Skills、MCP、Scheduler、Channel、Task、Team 和 Worktree 等能力统一放在 Extension 中。
- **一种基础，多种 Harness** — 每个 Example 都是完整、可独立运行的 Agent，而不是只有几行配置的演示壳。
- **面向检查与恢复** — 类型化事件、Checkpoint、Trace、持久队列、可恢复 Run 和明确的审批边界都是一等能力。

## 快速开始

```bash
git clone https://github.com/HabitGraylight/NanoHarness.git
cd NanoHarness
pip install -e .

# 无需 API Key 或网络，运行确定性的多来源 Agent。
python examples/nano_openclaw/main.py
```

内核只依赖 Pydantic 和 PyYAML。Provider 与外部集成都采用可选依赖：

```bash
pip install -e ".[openai]"   # OpenAI-compatible Provider
pip install -e ".[mcp]"      # MCP stdio Server
```

根目录 `main.py` 是最小的真实 Provider 组装示例。安装 OpenAI 可选依赖、设置 `DEEPSEEK_API_KEY` 后，可以运行 `python main.py`。

## 完整 Harness 示例

这些 Example 直接展示框架为什么可组合：它们共享内核和公共 Extension，但分别拥有自己的入口、Profile、Host Policy、状态模型、Prompt、Scenario、测试与文档。

| Example | Harness 风格 | 运行方式 |
|---|---|---|
| [NanoClaudeCode](examples/nano_claude_code/) | 带 Memory、Skills、Task、Team、Subagent 和写入审批的交互式 Coding Session | `python examples/nano_claude_code/profile_demo.py` |
| [NanoCodex](examples/nano_codex/) | 可恢复的 Plan → Execute → Review Coding，提供有界工具和受控 Git 交付 | `python examples/nano_codex/main.py` |
| [NanoHermes](examples/nano_hermes/) | 持久 Assist → Reflect → Host Review，支持暂存学习和定时触发 | `python examples/nano_hermes/main.py` |
| [NanoOpenClaw](examples/nano_openclaw/) | 分级信任的 Channel/Scheduler/Background Wakeup、持久会话与独立交付 | `python examples/nano_openclaw/main.py` |
| [NanoLoop](examples/nano_loop/) | 带预算、验证和明确人工 Gate 的证据驱动外层循环 | `python examples/nano_loop/main.py --help` |

确定性入口不需要 API Key 或网络。它们也是持久化、恢复、策略、审批和测试的具体参考实现，而不只是 Happy Path Demo。

## 组合方式

```text
应用 / Example
├── Host Policy、Prompt、UI 与 Provider Adapter
├── Harness Profile：声明组装内容
├── Components：Tools、Context、State、Hooks、Evaluator
└── Extensions：Memory、Channels、Scheduler、Teams、...
                         │
                         ▼
                    NanoEngine
               think → act → observe
                         │
                         ▼
               Event、Checkpoint、Report
```

这些边界是有意设计的：

1. **Kernel** 定义执行协议并协调一次 Run。
2. **Components** 提供这些协议的最小默认实现。
3. **Extensions** 用明确依赖和生命周期管理封装可复用能力。
4. **Applications** 决定 Agent 可以做什么，以及用户如何与其交互。

引擎不知道应用专属的 Prompt、权限规则、审批 UI、沙箱实现或交付策略。

## 小型内核与明确协议

内核协调六类可替换的关注点。使用 NanoHarness 前不需要先学习任何理论模型；这些名称只是在说明应用可以替换哪些边界。

| 关注点 | 协议 | 职责 |
|---|---|---|
| Execution | `NanoEngine` / `LLMProtocol` | Think → Act → Observe 循环与终止 |
| Tools | `BaseToolRegistry` | 类型化工具目录、校验与路由 |
| Context | `BaseContextManager` | Prompt 与会话上下文组装 |
| State | `BaseStateStore` | Checkpoint 与跨 Turn 持久化 |
| Lifecycle | `BaseHookManager` | Run 与 Step 边界的插桩 |
| Evaluation | `BaseEvaluator` | 轨迹记录、提前停止与成功判定 |

```python
from nanoharness import (
    DictToolRegistry,
    JsonStateStore,
    NanoEngine,
    SimpleContextManager,
    SimpleHookManager,
    TraceEvaluator,
)

# my_llm 实现 LLMProtocol。
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

Tool 执行也拥有可替换的独立路径：

```text
ToolCall → PolicyDecision → 可选 Approval → ToolExecutor → Observation
```

因此权限策略、人工审批和本地/沙箱/远程执行可以彼此独立。

## 可复用 Extensions

Extension 把工具和服务安装到明确的 `ExtensionContext`。每个 Extension 都有版本化 Manifest、类型化配置、依赖声明、安装回执和受控关闭流程。

| Extension | 可复用能力 |
|---|---|
| Memory | 基于 Markdown 的保存、回忆与列表 |
| Skills | Metadata 发现与按需指令加载 |
| MCP | 官方 SDK stdio Session 与动态工具发现 |
| Background | 托管 Shell 进程与完成通知 |
| Channels | 与传输无关的持久 Inbox/Outbox、审批、重试与 Adapter |
| Scheduler | 持久 Cron/Delay 触发和触发时通知 |
| Tasks | 依赖型 Task Board、Claim、Role 与工具 |
| Teams | 长期 Teammate 与持久 Inbox/Request 协议 |
| Subagents | 可选 Context Fork 的单次隔离委派 |
| Worktrees | 与 Task 绑定、经过审计的 Git Worktree Lane |

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

## 声明式 Profile 与检查工具

`HarnessSpec` 使用 YAML、TOML 或 JSON 描述 Host Requirement、Extension 和 Engine Service Binding。`HarnessBuilder` 可以无副作用地校验 Profile、解析依赖、解释 Provider 与冲突，并构建声明的组合。

```bash
python -m nanoharness.profiles validate recipes/coding_team.yaml
python -m nanoharness.profiles explain recipes/coding_team.yaml
python -m nanoharness.profiles catalog
python -m nanoharness.profiles matrix \
  recipes/solo_subagent.yaml recipes/coding_team.yaml
python -m nanoharness.profiles compare \
  recipes/traces/solo.json recipes/traces/team.json
```

Profile Matrix 会显示准确的 Component、Policy、Capability、Extension 与 Host Service 差异。Trace Comparison 只报告事实差异，不给不同 Harness 排名，也不会暴露原始 Thought、工具参数、输出或 Evaluator Explanation。

## Runtime 与恢复能力

NanoHarness 把可靠性机制保持为可见能力，而不是埋进某个 Host 应用：

- 稳定 Run/Session Identity 与终态；
- 保留 Provider Call ID 的无损多工具 Step；
- 有序 Event Stream 与 Redacting/JSONL/Console Sink；
- 包含轨迹、停止原因和错误的崩溃可读 Checkpoint；
- 正式 Evaluator 成功判定与循环中提前停止；
- Async Run 与 Event Stream API；
- 在安全 Step 边界协作式取消与 Steering；
- 由对应 Extension 和 Example 提供的持久 Inbox/Outbox、Schedule Notification 与可恢复 Host State。

同步 `NanoEngine.run(query)` 和兼容字段仍然保留。

## 项目结构

```text
nanoharness/
  core/          # 协议、Schema、NanoEngine、Run Control
  components/    # 最小默认实现
  extensions/    # 可复用能力包
  profiles/      # 声明式组合与检查
  testing/       # 确定性 Provider、Scenario、Artifact
examples/        # 五个完整、可独立运行的 Harness
recipes/         # Profile 与 Trace 检查夹具
tests/           # Kernel、Extension、Profile 与 Example Contract
```

## 测试

仓库当前包含 **1,012 个通过的测试**：

| 测试集 | 数量 |
|---|---:|
| Kernel、Extensions、Profiles 与 Contract | 563 |
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

每个 Example 的 `tests/` 都可以独立运行，用于验证该应用自己的策略与集成边界。真实 MCP stdio 测试使用 `mcp` 可选依赖。

## 安全

拥有工具访问权限的 Agent 可以修改文件、运行进程并对外通信。生产应用应组合最小权限工具、显式 Policy、Approval Gate、沙箱执行、Secret Redaction 和 Prompt Injection 防御。NanoHarness 提供边界，具体策略由应用负责。

## 理论来源与引用

内核的六类关注点参考了 [Agent Harness Survey](https://github.com/Gloriaameng/Awesome-Agent-Harness)。使用框架时不需要采用其符号；这篇 Survey 更适合作为比较 Harness 架构的延伸阅读。

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

## 许可证

MIT
