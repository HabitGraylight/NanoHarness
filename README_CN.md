<p align="center">
  <img src="assets/NanoharnessMain.png" alt="NanoHarness" width="640">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/Tests-973%20passed-brightgreen.svg" alt="Tests">
  <img src="https://img.shields.io/badge/Framework-ETCSLV-purple.svg" alt="ETCSLV">
</p>

<h1 align="center">NanoHarness</h1>

<p align="center">
  <b>基于 H&nbsp;=&nbsp;(E,&nbsp;T,&nbsp;C,&nbsp;S,&nbsp;L,&nbsp;V) 的极简 Agent 框架</b>
</p>

[English](README.md) | 中文

---

## 概述

NanoHarness 是一个极简的 Python Agent 框架，实现了 [Agent Harness Survey](https://github.com/Gloriaameng/Awesome-Agent-Harness) 提出的六组件治理模型：

| | 组件 | 职责 |
|:---:|---|---|
| **E** | 执行循环 | 思考 → 行动 → 观察循环、终止条件、错误恢复 |
| **T** | 工具注册 | 类型化工具目录、路由、Schema 校验 |
| **C** | 上下文管理 | 上下文窗口的组装与压缩 |
| **S** | 状态存储 | 跨轮次持久化与崩溃恢复 |
| **L** | 生命周期钩子 | 横切面插桩：日志、策略、认证 |
| **V** | 评估 | 轨迹记录、循环中早停检测、独立目标验证 |

内核**只**提供这六个接口和一个编排引擎。其余一切——调用哪个 LLM、如何管理记忆、是否执行权限校验——均由应用层决定。

---

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                       NanoHarness 内核                           │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │  E: NanoEngine                                          │  │
│   │                                                         │  │
│   │    ON_START ──► Think ──► Act ──► Observe ──► ON_STEP   │  │
│   │                    │         │          │       │        │  │
│   │                    ▼         ▼          ▼       ▼        │  │
│   │               LLMProtocol  T: Tools  C: Context         │  │
│   │                                              V: Eval    │  │
│   │                                    should_stop? ──► STOP │  │
│   │                                                         │  │
│   │    ON_END ◄── V: Report + evaluate_success              │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
│   接口：  BaseToolRegistry  BaseContextManager                  │
│           BaseStateStore    BaseHookManager                     │
│           BaseEvaluator     LLMProtocol                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                        构造函数注入
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         应用层                                  │
│                                                                 │
│   LLM 适配器  ·  记忆策略  ·  权限策略  ·  工具组装              │
│   Prompt 模板  ·  UI / 输出                                     │
│                                                                 │
│   组装：main.py 或各项目专属 builder                             │
└─────────────────────────────────────────────────────────────────┘
```

**设计原则：** 引擎不包含应用专属的 Prompt、记忆、策略规则、审批界面、沙箱实现或输出 UI。它只编排注入的协议，因此可以安全地在不同 Agent 应用间共享。

---

## 结构

```
nanoharness/
  core/                  # 内核：接口 + 引擎
    schema.py            #   消息、工具执行、Run/Checkpoint、事件与评估协议
    base.py              #   ETCSLV ABCs, LLMProtocol, HookStage
    engine.py            #   NanoEngine（含循环中评估）
    runtime.py           #   RunControl（协作式取消 + Steering）
    prompt.py            #   PromptManager（YAML 模板加载器）
  components/            # ETCSLV 最简实现
    llm/                 #   E：可选 OpenAI-compatible Provider Adapter
    tools/               #   T: DictToolRegistry, ScriptToolRegistry
    context/             #   C: SimpleContextManager
    state/               #   S: JsonStateStore
    hooks/               #   L: SimpleHookManager
    lifecycle/           #   Policy、审批、执行器与事件组件
    evaluator/           #   V: TraceEvaluator（含 should_stop + evaluate_success）
  extensions/            # 可复用能力包
    base.py              #   Manifest、配置、安装、关闭与回执协议
    manager.py           #   依赖/冲突校验、安装清单与生命周期
    background/          #   托管 Shell 进程与完成通知
    channels/            #   与传输无关的持久 Inbox/Outbox 与 Adapter
    memory/              #   FileMemoryManager + MemoryExtension
    mcp/                 #   MCP stdio 客户端与动态 MCP 工具
    scheduler/           #   持久化 cron/延时调度服务
    skills/              #   SkillRegistry + SkillsExtension
    subagents/           #   单次隔离委派运行时
    tasks/               #   持久化依赖型 Task Board
    teams/               #   托管长期队友与 Inbox 协议
    worktrees/           #   与 Task 绑定的 Git worktree lane
  profiles/              # Profile、阶段式装配、Trace、对比与自动矩阵
  testing/               # 确定性 Scenario、脚本 Provider 与 Artifact
  utils/                 # get_logger, count_tokens
configs/
  prompts.yaml           # Prompt 模板
  scripts/               # Shell 脚本工具（自动发现，27 个）
recipes/                 # 声明式 Profile 与 Trace 检查夹具
  coding_team.yaml
  solo_subagent.yaml
  traces/
examples/
  nano_claude_code/      # Provider 驱动的交互式 Coding（151 个测试）
  nano_codex/            # 受控 Coding Harness（96 个测试）
  nano_hermes/           # 持久学习型个人 Agent（86 个测试）
  nano_openclaw/         # 可恢复的多渠道会话 Gateway（58 个测试）
  nano_loop/             # 证据驱动的 Loop Engineering 示例（27 个测试）
tests/                   # 555 个内核/扩展/Profile/Example 测试
```

---

## 快速开始

```bash
git clone https://github.com/HabitGraylight/NanoHarness.git
cd NanoHarness
pip install -e .
```

内核仅依赖 Pydantic 和 PyYAML。LLM 客户端和其他集成由各应用按需安装。

```bash
# 运行最简示例
python main.py

# 运行 Coding Agent
cd examples/nano_claude_code && python main.py

# 运行证据驱动的 Loop
cd examples/nano_loop
python main.py run configs/loops/local_fix.yaml --repo ../.. --task "你的任务"
```

---

## 引擎循环

```
NanoEngine.run(query)
     │
     ├─ L.trigger(ON_TASK_START)
     ├─ C.add_message(user)
     │
     └─ 循环直到终止或达到 max_steps:
          │
          ├─ Think:  E → LLM.chat(C.get_full_context(), T.get_schemas())
          ├─ L.trigger(ON_THOUGHT_READY)
          │
          ├─ Act:    对每个 tool_call:
          │            PolicyDecision → 可选 ApprovalBroker
          │            → ToolExecutor（注册表 / 沙箱 / 远程）
          │            C.add_message(observation)
          │
          ├─ S.save_state()
          ├─ V.log_step()
          ├─ V.should_stop()?  ──► 若陷入循环/停滞则提前终止
          └─ L.trigger(ON_STEP_END)

     ├─ V.get_report()        （包含 evaluate_success 验证结果）
     └─ L.trigger(ON_TASK_END)
```

引擎内部没有应用策略规则、审批 UI 或沙箱逻辑——全部通过注入的协议实现流转。

---

## Core Protocol v2

核心协议 v2 在保持 `NanoEngine.run(query)` 和原有字典报告兼容的同时，增加了：

- 稳定的 `run_id`、`session_id`、协议版本与运行状态；
- Provider ToolCall ID 的保留，以及 Engine 生成的稳定回退 ID；
- `StepResult.actions` 完整多工具执行轨迹；
- 有序 `HarnessEvent` 事件流与可选 `EventSinkProtocol`；
- 包含查询、轨迹、终止原因和错误的 `RunCheckpoint`；
- 每个 Run 独立的评估轨迹，Context 仍可在同一 Session 内持续；
- `EvaluationResult.achieved` 作为正式成功判定。

旧的 `StepResult.action` 和 `observation` 字段暂时保留，并映射到该步最后一次工具执行。

## 生命周期策略与异步 Runtime

协议 v2.1 为工具生命周期增加了明确边界：

- `ToolPolicyProtocol` 在工具执行前后返回类型化 `PolicyDecision`；
- `ApprovalBrokerProtocol` 将交互式或远程审批从策略判定中分离；
- `ToolExecutorProtocol` 成为本地、沙箱或远程执行的可替换边界；
- `CompositeToolPolicy` 以确定的优先级组合权限策略和 Tool Hook；
- `EventBus`、`RedactingEventSink`、`JsonlEventSink` 和 `ConsoleEventSink` 组成可组合的实时观测管线；
- `NanoEngine.arun()` 与 `NanoEngine.astream()` 分别提供异步报告和实时事件接口；
- `RunControl` 在安全的步骤边界提供协作式取消与 Steering。

旧的 `permissions=` 和 `tool_hooks=` 构造参数继续兼容；NanoClaudeCode 通过应用 Builder 绑定类型化策略与审批协议。

---

## 可复用 Extensions

Extension Protocol 1.0 为可复用能力提供统一的白箱结构：

- `ExtensionManifest` 声明带版本的能力、依赖和冲突；
- 每个 Extension 在安装前即可输出 Pydantic 配置 Schema；
- `ExtensionContext` 是显式的工具、服务和能力安装表面；
- `ExtensionInstallation` 是可序列化的工具与服务安装回执；
- `NotificationSourceProtocol` 为长生命周期服务提供统一的 Host `drain()` 契约；
- `ExtensionManager.inspect()` 返回能力、服务、安装回执和解析后的依赖边；
- `ExtensionManager.close()` 按安装顺序的逆序关闭资源型扩展，且只执行一次。

```python
from nanoharness import DictToolRegistry, ExtensionContext, ExtensionManager
from nanoharness.extensions.memory import MemoryExtension

context = ExtensionContext(tools=DictToolRegistry())
extensions = ExtensionManager(context)
extensions.install(MemoryExtension(), {"directory": ".memory"})

print(extensions.inspect())
memory = context.services["memory"]
extensions.close()
```

可复用能力包包括：

- `MemoryExtension` — Markdown 记忆存储，以及 save/recall/list 工具；
- `SkillsExtension` — 目录发现、元数据索引和按需加载完整指令；
- `MCPExtension` — 基于官方 MCP SDK 的 stdio 会话、动态工具发现、配置回执脱敏和子进程托管关闭；
- `BackgroundExtension` — 有并发上限的 Shell 执行、工作目录边界、完成通知和关闭取消；
- `ChannelExtension` — 与传输无关的持久 Ingress/Outbox、消息去重、Claim 恢复、显式投递审批/重试、Adapter 幂等键和确定性 Mock Adapter；其只入队不发送的 Tool 由具体 Host 使用 Run 作用域显式注册；
- `SchedulerExtension` — 持久化 cron/延时 Prompt、托管检查线程和触发通知；
- `TaskExtension` — 持久化依赖任务、claim、角色和 schema-first Task 工具；
- `TeamExtension` — 长期队友循环、持久化 Inbox/Request 协议、Task Board 自动 claim、通知和可等待关闭；
- `SubagentExtension` — 单次只读委派，并可选择 fork 父上下文；
- `WorktreeExtension` — 带审计事件的 Git 执行 lane，通过 `requires=["tasks.board"]` 显式依赖 Task Board。

NanoClaudeCode 通过 `ExtensionManager` 安装九个公共扩展；Team 与 Subagent 还显式声明宿主运行时依赖，因此 `inspect()` 能同时显示扩展提供与宿主提供的依赖边。其本地模块公开应用所需的公共 Extension API。MCP 仍是可选能力，只有需要外部服务器的 Profile 才需安装 `nanoharness[mcp]`。

## Harness Profiles

HarnessSpec 1.0 将 Extension 组合与 ETCSLV 运行时绑定表达为可移植的
YAML、TOML 或 JSON 配方。声明中只记录宿主提供的 Capability 与 Service
名称，不会尝试序列化真实 LLM 或 Context 对象。

`HarnessBuilder` 可以无副作用地校验配置，按能力依赖计算确定性的安装顺序，
解释 Provider、冲突与配置 Schema，安装 Extension，并将最终工具注册表和宿主
Service 绑定为 `NanoEngine`。

`StagedAssembler` 用于处理必须先安装 Bootstrap Extension、之后才能绑定宿主
Service 的应用，并保持唯一显式顺序：Bootstrap Extension → Host Bind → Runtime
Extension。应用随后可以使用完整的 Service 绑定构建 Engine。

```bash
python -m nanoharness.profiles validate recipes/coding_team.yaml
python -m nanoharness.profiles explain recipes/coding_team.yaml
python -m nanoharness.profiles catalog
python -m nanoharness.profiles matrix recipes/solo_subagent.yaml recipes/coding_team.yaml
python -m nanoharness.profiles trace recipes/traces/solo.json
python -m nanoharness.profiles compare recipes/traces/solo.json recipes/traces/team.json
```

```python
from nanoharness import HarnessBuilder, HarnessSpec

spec = HarnessSpec.from_file("recipes/coding_team.yaml")
builder = HarnessBuilder()

validation = builder.validate(spec)  # 不创建文件、工具或线程
explanation = builder.explain(spec)  # Manifest、Schema、顺序与依赖边

# build = builder.build(spec, context=host_extension_context)
# engine = build.engine              # spec.engine 已声明时生成
# build.close()                      # 关闭所有资源型 Extension
```

`explain` 会脱敏常见密钥字段及完整 `env` 映射。离线校验把声明的宿主需求
视为假设；`build()` 会在安装任何 Extension 前检查真实 Capability 与 Service。

`matrix` 会把一个或多个 Profile 转换为 ETCSLV、策略、能力、扩展与宿主 Service
矩阵。`trace` 将 NanoEngine Report、Checkpoint 或 JSONL Event Stream 归一化为
最小内容指标，并省略原始 Thought、工具参数、Observation、输出和评估解释。
`compare` 可以比较两个 Profile 或两个 Trace，只报告事实差异，不判定胜负。

---

## 独立可运行 Harness

每个 Harness 都拥有自己的入口、Profile、应用 Policy、Scenario、测试和文档；
它们复用 NanoHarness 公共包，但不会导入其他 Example 的应用代码：

- **NanoClaudeCode** — 交互式、Session 导向的 Coding Harness，组合 Memory、
  Skills、Task/Team 委派与写入审批；
- **NanoCodex** — 面向托管或既有 Git 仓库的可恢复 Plan → Execute → Review
  Coding Harness，可接真实 Provider，提供有界 Coding 工具、交互式变更/交付审批、
  Host 可信证据以及 keep/commit/apply/merge 交付；
- **NanoHermes** — 可恢复的 Assist → Reflect → Host Review 个人 Agent，组合跨 Run
  Memory/Skill、内容寻址的暂存学习、彼此独立的动作/晋升审批、到期 Schedule 触发与
  隔离委派；
- **NanoOpenClaw** — 可恢复的多渠道会话 Host，提供稳定路由、Turn 恢复、已交付
  历史上下文、只读工具，并通过公共 Channel Extension 执行独立审批的 Outbox 交付；

```bash
python examples/nano_claude_code/profile_demo.py
python examples/nano_codex/main.py
python examples/nano_hermes/main.py
python examples/nano_openclaw/main.py
python examples/nano_loop/main.py --help
```

确定性 Smoke 入口不需要 API Key 或网络，每次运行分别保存敏感的完整 Report
和内容最小化 Trace。NanoClaudeCode 还保留真实 Provider 驱动的 REPL。
跨 Example 的 Matrix 与 Trace 比较直接使用内置的
`python -m nanoharness.profiles matrix/compare` 命令；检查工具不再放入
`examples/`。

---

## 工具

工具满足 `BaseToolRegistry` 接口，提供两个方法：`get_tool_schemas()` 和 `call(name, args)`。

内置两种注册器：

- **DictToolRegistry** — 通过 `@tool` 装饰器注册 Python 函数，JSON Schema 从类型提示自动推断。
- **ScriptToolRegistry** — 自动发现目录中的 `.sh` 文件，参数通过 `@param` 注释头声明，以环境变量传递。

注册器通过 `merge()` 组合。

添加新工具无需修改 Python 代码——将带有正确头部的 Shell 脚本放入 `configs/scripts/` 即可自动可用。

---

## 扩展

内核定义接口，应用提供具体行为：

**LLM** — 实现 `LLMProtocol`：
```python
def chat(self, messages, tools=None) -> LLMResponse: ...
```

**自定义组件** — 继承任意 `Base*` ABC，注入 `NanoEngine`。

`examples/nano_claude_code/` 是完整 NanoClaudeCode 实现，其中组装了自定义 LLM 适配器、记忆策略、权限流水线、子 Agent 委派、技能加载和评估，且无需修改内核。

`examples/nano_loop/` 提供外层 Loop 控制面：反复创建干净的 NanoEngine 运行、验证产物、持久化证据、执行预算策略，并在明确的人工 Gate 停止。

---

## 测试

```bash
# 内核、公共扩展、Profile 与 Example 契约测试（555 个）
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# NanoClaudeCode 应用层测试（151 个：109 UT + 42 ST）
cd examples/nano_claude_code
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# NanoCodex 协议、工具、Host 与交付测试（96 个）
cd ../nano_codex && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# NanoHermes 持久学习测试（86 个）
cd ../nano_hermes && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# NanoOpenClaw 会话、Turn、恢复与交付测试（58 个）
cd ../nano_openclaw && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# NanoLoop 测试（27 个）
cd ../nano_loop
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v
```

**共 973 个测试。** 可复用 Extension 行为统一在根测试集验证，各 Example
测试集中验证自己拥有的组合、策略与集成边界。内核测试只需要内核依赖与 pytest；
真实 MCP stdio 测试使用 `mcp` 可选依赖。

---

## 安全

拥有工具访问权限的 Agent 可能造成实际损害。生产部署应实现权限门控、沙箱执行和 Prompt 注入防御。参见 Coding Agent 示例中的权限流水线参考实现。

---

## 致谢

本项目的理论基础来自 [Agent Harness Survey](https://github.com/Gloriaameng/Awesome-Agent-Harness)。

---

## 引用

```bibtex
@software{nanoharness2026,
  title     = {NanoHarness: A Minimal Agent Harness Based on H=(E,T,C,S,L,V)},
  author    = {Habit},
  year      = {2026},
  url       = {https://github.com/HabitGraylight/NanoHarness},
  license   = {MIT}
}
```

理论基础：

```bibtex
@article{meng2026agentharness,
  title     = {Agent Harness for Large Language Model Agents: A Survey},
  author    = {Meng, Qianyu and Wang, Yanan and Chen, Liyi and others},
  year      = {2026},
  url       = {https://www.preprints.org/manuscript/202604.0428/v2}
}
```

---

## 许可证

MIT
