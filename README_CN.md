<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/Tests-91%20passed-brightgreen.svg" alt="Tests">
</p>

<h1 align="center">NanoHarness</h1>

<p align="center">
  <b>一个轻量、可组合的 Python AI Agent 框架</b><br>
  <span style="color:gray">思考 → 行动 → 观察 — 所有组件均可插拔</span>
</p>

[English](README.md) | 中文

---

## 目录

- [概述](#概述)
- [架构](#架构)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
  - [前置条件](#前置条件)
  - [安装](#安装)
  - [配置](#配置)
  - [运行 Agent](#运行-agent)
- [核心设计](#核心设计)
  - [引擎循环](#引擎循环)
  - [工具系统](#工具系统)
  - [权限系统](#权限系统)
  - [记忆系统](#记忆系统)
  - [MCP 集成](#mcp-集成)
  - [Prompt 管理](#prompt-管理)
- [添加自定义工具](#添加自定义工具)
- [添加 LLM 提供商](#添加-llm-提供商)
- [测试](#测试)
- [路线图](#路线图)
- [安全提示](#安全提示)
- [引用](#引用)
- [许可证](#许可证)

---

## 概述

NanoHarness 是一个轻量级的 AI Agent 框架，用于构建在受控、可观察的循环中与工具交互的 AI 代理。其设计围绕三个核心原则：

1. **极简** — 无重度依赖，无不透明抽象。核心引擎代码仅约 150 行。
2. **可组合** — 每个组件（LLM 适配器、工具注册器、记忆、权限、钩子）均可注入和替换。
3. **可复用** — 清晰的接口（`LLMProtocol`、`BaseToolRegistry` 等）便于扩展或嵌入更大的系统。

框架实现了 **思考 → 行动 → 观察** 的 Agent 循环：

```
用户查询 → [上下文] → LLM 思考 → 工具执行 → 观察结果 → [重复] → 输出报告
```

---

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                          NanoEngine                              │
│                                                                  │
│   ┌──────────┐   ┌──────────────┐   ┌────────────────────────┐  │
│   │  Context  │   │  PromptMgr   │   │    Evaluator (Trace)   │  │
│   │ Manager   │   │  (YAML)      │   │    日志 + 报告          │  │
│   └────┬─────┘   └──────┬───────┘   └────────────────────────┘  │
│        │                 │                                      │
│        ▼                 ▼                                      │
│   ┌────────────────────────────┐     ┌───────────────────────┐  │
│   │      LLMProtocol           │     │   HookManager         │  │
│   │  ┌──────┐ ┌──────┐        │     │  ON_START / ON_END    │  │
│   │  │OpenAI│ │Anthro│  ...   │     │  ON_THOUGHT / ON_STEP │  │
│   │  └──────┘ └──────┘        │     └───────────────────────┘  │
│   └────────────┬───────────────┘                               │
│                │                                               │
│                ▼                                               │
│   ┌────────────────────────────────┐   ┌───────────────────┐  │
│   │     BaseToolRegistry           │   │  PermissionMgr    │  │
│   │  ┌──────────┐  ┌───────────┐  │   │  ALLOW / CONFIRM  │  │
│   │  │ Script   │  │   MCP     │  │   │  DENY (glob匹配)  │  │
│   │  │ Registry │  │ Registry  │  │   └───────────────────┘  │
│   │  │ (*.sh)   │  │ (FastMCP) │  │                          │
│   │  └──────────┘  └───────────┘  │   ┌───────────────────┐  │
│   │        └────── merge() ──────┘   │  MemoryManager     │  │
│   └────────────────────────────────┘   │  工作记忆 + 持久化 │  │
│                                        └───────────────────┘  │
│   ┌────────────────┐                                           │
│   │  StateStore    │             ┌──────────────┐             │
│   │  (JSON文件)    │             │  SandboxExec │             │
│   └────────────────┘             └──────────────┘             │
└──────────────────────────────────────────────────────────────────┘
```

---

## 项目结构

```
NanoHarness/
├── main.py                          # 入口 — 组装组件，启动 Agent
├── pyproject.toml                   # 包元数据与依赖
├── requirements.txt                 # 快速安装依赖列表
├── LICENSE                          # MIT 许可证
│
├── nanoharness/                     # 核心包
│   ├── core/                        # 框架内核 — 稳定，极少修改
│   │   ├── schema.py                #   Pydantic 模型：ToolCall, LLMResponse, StepResult,
│   │   │                            #   PermissionRule, MemoryEntry, AgentMessage
│   │   ├── base.py                  #   抽象基类与协议
│   │   │                            #   LLMProtocol（鸭子类型）、BaseToolRegistry、
│   │   │                            #   BaseContextManager、BaseStateStore、BaseEvaluator、
│   │   │                            #   BaseHookManager、BasePermissionManager、BaseMemoryManager
│   │   │                            #   HookStage 枚举
│   │   ├── engine.py                #   NanoEngine — 思考→行动→观察 Agent 循环
│   │   │                            #   处理：LLM 调用、工具分发、权限门控、
│   │   │                            #   记忆注入、状态持久化、步骤评估
│   │   └── prompt.py                #   PromptManager — 加载 configs/prompts.yaml，
│   │                                #   提供 get() / render() / add() 模板方法
│   │
│   ├── components/                  # 可插拔实现
│   │   ├── llm/                     #   LLM 适配器（LLMProtocol 实现）
│   │   │   ├── openai_adapter.py    #     OpenAI / DeepSeek（兼容 API）
│   │   │   ├── anthropic_adapter.py #     Anthropic Claude
│   │   │   ├── litellm_adapter.py   #     LiteLLM（多提供商网关）
│   │   │   └── vllm_adapter.py      #     vLLM（本地推理）
│   │   │
│   │   ├── tools/                   #   工具注册器（BaseToolRegistry 实现）
│   │   │   ├── dict_registry.py     #     DictToolRegistry — @tool 装饰器、JSON Schema
│   │   │   │                        #     自动推断、merge() 合并注册器
│   │   │   └── script_tools.py      #     ScriptToolRegistry — 自动发现 *.sh 脚本，
│   │   │                            #     解析 @param 头部，参数以环境变量传递
│   │   │
│   │   ├── mcp/                     #   MCP（Model Context Protocol）集成
│   │   │   ├── client.py            #     MCPClient — 基于 async MCP SDK 的同步包装器
│   │   │   │                        #     （后台 asyncio 事件循环线程 + run_coroutine_threadsafe）
│   │   │   └── registry.py          #     MCPToolRegistry — 将 MCP 工具适配为 OpenAI schema，
│   │   │                            #     继承 DictToolRegistry，可与 ScriptToolRegistry 合并
│   │   │
│   │   ├── context/                 #   上下文管理
│   │   │   └── simple_context.py    #     SimpleContextManager — 消息列表 + 系统 Prompt
│   │   │
│   │   ├── memory/                  #   双层记忆系统
│   │   │   └── simple_memory.py     #     SimpleMemoryManager — 工作记忆（每次运行的 dict）
│   │   │                            #     + 持久记忆（JSON 文件，支持关键词搜索）
│   │   │                            #     MemoryToolMixin — 将 memory_store / memory_recall 暴露为工具
│   │   │
│   │   ├── permissions/             #   权限与沙箱
│   │   │   ├── rule_permission.py   #     RulePermissionManager — glob 模式匹配，三级控制
│   │   │   └── sandbox.py           #     SandboxExecutor — 带超时的子进程隔离
│   │   │
│   │   ├── hooks/                   #   生命周期钩子
│   │   │   └── simple_hooks.py      #     SimpleHookManager — 按 HookStage 注册/触发
│   │   │
│   │   ├── state/                   #   状态持久化
│   │   │   └── json_store.py        #     JsonStateStore — 保存/加载/重置运行状态
│   │   │
│   │   └── evaluator/               #   运行评估
│   │       └── trace_evaluator.py   #     TraceEvaluator — 记录步骤、生成摘要报告
│   │
│   └── utils/                       # 公共工具
│       ├── logger.py                #   get_logger() — 模块级日志工厂
│       └── token_counter.py         #   count_tokens() / count_messages_tokens()
│
├── configs/                         # 配置文件（无代码）
│   ├── prompts.yaml                 #   所有 Prompt 模板 — 集中管理，支持变量替换
│   ├── mcp_servers.json             #   MCP 服务器定义（传输方式、命令、参数）
│   └── scripts/                     #   Shell 脚本工具（26 个工具，自动发现）
│       ├── git_*.sh                 #     19 个 Git 操作（status、log、diff、commit、push...）
│       ├── file_*.sh                #     5 个文件 I/O（read、write、edit、list、find）
│       ├── sys_info.sh              #     系统信息
│       └── shell_exec.sh            #     通用 Shell 命令执行
│
└── tests/                           # 测试套件（与包结构对应）
    ├── conftest.py                  #   共享 fixtures（MockLLMClient）
    ├── test_schema.py               #   Schema 模型测试
    ├── test_engine.py               #   引擎循环测试（终止、工具调用、错误、钩子、上下文）
    ├── test_dict_registry.py        #   @tool 装饰器与 DictToolRegistry 测试
    ├── test_script_tools.py         #   脚本工具加载与功能测试（git、file、sys）
    ├── test_mcp.py                  #   MCP 集成测试（mock FastMCP 服务器）
    ├── test_memory.py               #   记忆存储/召回/持久化 + MemoryToolMixin 测试
    ├── test_permissions.py          #   权限规则、glob 匹配、参数拦截测试
    ├── test_simple_context.py       #   上下文管理器测试
    ├── test_simple_hooks.py         #   钩子管理器测试
    ├── test_json_store.py           #   状态存储测试
    ├── test_trace_evaluator.py      #   评估器测试
    └── test_utils.py                #   日志与 token 计数测试
```

---

## 快速开始

### 前置条件

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | >= 3.10 | 使用了 `typing.ParamSpec`、`match` 语法 |
| pip | 最新版 | 用于包安装 |
| Conda（可选） | 任意版本 | 推荐用于环境隔离 |
| LLM API Key | — | DeepSeek、OpenAI、Anthropic 或本地 vLLM |

### 安装

```bash
# 1. 创建并激活环境
conda create -n harness python=3.10 -y
conda activate harness

# 2. 克隆仓库
git clone https://github.com/HabitGraylight/NanoHarness.git
cd NanoHarness

# 3. 以可编辑模式安装（包含核心依赖）
pip install -e .

# 4. 按需安装可选提供商
pip install -e ".[openai]"        # OpenAI / DeepSeek
pip install -e ".[anthropic]"     # Anthropic Claude
pip install -e ".[litellm]"       # LiteLLM 多提供商
pip install -e ".[mcp]"           # MCP 集成

# 或一次性安装全部
pip install -e ".[all-providers]"
```

### 配置

**1. 设置 API Key：**

```bash
# DeepSeek（默认）
export DEEPSEEK_API_KEY="sk-..."

# 或在 main.py 中切换为 OpenAI
export OPENAI_API_KEY="sk-..."
```

**2.（可选）启用 MCP 工具：**

编辑 `configs/mcp_servers.json` 注册 MCP 服务器：

```json
{
  "servers": [
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "transport": "stdio"
    }
  ]
}
```

通过环境变量启用 MCP 加载：

```bash
export NANOHARNESS_MCP=1
```

**3.（可选）在 `main.py` 中配置权限：**

```python
perms = RulePermissionManager()
perms.deny("git_reset")                        # 直接禁止
perms.confirm("git_push", "git_commit")        # 需要用户确认
perms.confirm("shell_exec")                    # 需要用户确认
```

### 运行 Agent

```bash
# 交互模式
python main.py

# 启用 MCP 工具
NANOHARNESS_MCP=1 python main.py
```

Agent 将提示输入，进入思考 → 行动 → 观察循环，完成后输出运行报告。

---

## 核心设计

### 引擎循环

`NanoEngine`（`nanoharness/core/engine.py`）编排 Agent 循环：

1. **记忆注入** — 循环开始前将相关记忆加载到上下文。
2. **思考** — 将完整上下文连同可用工具 schema 一起发送给 LLM。
3. **权限门控** — 每个工具调用都经过权限规则检查（`DENY` → 跳过，`CONFIRM` → 提示用户确认）。
4. **行动** — 执行工具，将观察结果追加到上下文。
5. **评估** — 每个步骤由评估器记录。
6. **终止** — 当 LLM 响应不含工具调用时，循环结束并生成报告。

### 工具系统

工具遵循 `BaseToolRegistry` 接口：

| 实现 | 来源 | 核心特性 |
|---|---|---|
| `DictToolRegistry` | Python `@tool` 装饰器 | 根据类型提示自动推断 JSON Schema |
| `ScriptToolRegistry` | `configs/scripts/*.sh` | 自动发现 Shell 脚本，解析 `@param` 头部 |
| `MCPToolRegistry` | MCP 服务器（`mcp_servers.json`） | 发现远程工具，转换为 OpenAI schema |

所有注册器均生成 **OpenAI 兼容的工具 schema**，可通过 `registry.merge(other)` **合并** — 支持混合工具集（如本地脚本 + 远程 MCP 工具）。

### 权限系统

三级权限控制，通过 glob 模式匹配：

| 级别 | 行为 |
|---|---|
| `ALLOW` | 工具立即执行 |
| `CONFIRM` | 执行前需用户确认 |
| `DENY` | 工具调用被阻止 |

```python
perms = RulePermissionManager()
perms.deny("git_reset")              # Glob 匹配 — 同时禁止 git_reset_hard
perms.confirm("git_push", "shell_exec")
# 其他工具默认为 ALLOW
```

### 记忆系统

双层记忆架构：

- **工作记忆** — 每次运行清除的临时 dict，用于运行内状态传递。
- **持久记忆** — JSON 文件，支持关键词搜索。存储运行摘要和显式 `memory_store` 调用。

记忆在每次运行前注入上下文，运行结束后自动持久化。

### MCP 集成

[MCP（Model Context Protocol）](https://modelcontextprotocol.io/) 使 Agent 能够使用外部服务器暴露的工具。集成包括：

- **`MCPClient`** — 异步 MCP Python SDK 的同步包装器。创建后台 `asyncio` 事件循环线程，通过 `run_coroutine_threadsafe` 桥接。
- **`MCPToolRegistry`** — 从已连接的 MCP 服务器发现工具，将 `inputSchema` 转换为 OpenAI function-calling 格式，并通过 client 委托 `call()` 执行。

MCP 工具与现有工具系统完全兼容 — 可与脚本工具合并，并遵循相同的权限规则。

### Prompt 管理

所有 Prompt 模板集中存储在 `configs/prompts.yaml`，通过 `PromptManager` 访问：

```python
from nanoharness.core.prompt import PromptManager
pm = PromptManager()
pm.render("memory.inject", entries=mem_text)   # 变量替换
```

这确保了代码中不存在硬编码的 Prompt 字符串。添加新 Prompt 只需编辑 YAML 文件。

---

## 添加自定义工具

### Shell 脚本工具（推荐）

在 `configs/scripts/` 中添加 `.sh` 文件，附带 `@param` 头部：

```bash
#!/bin/bash
# @param query:str:搜索关键词
# @param limit:int:最大结果数（默认：10）

echo "正在搜索：$query（限制：$limit）"
```

该脚本将被**自动发现并注册** — 无需修改 Python 代码。参数通过环境变量传递。

### Python 函数工具

在 `DictToolRegistry` 上使用 `@tool` 装饰器：

```python
from nanoharness.components.tools.dict_registry import DictToolRegistry

registry = DictToolRegistry()

@registry.tool
def my_tool(name: str, count: int = 5) -> str:
    """做些有用的事情。"""
    return f"已处理 {name} x{count}"
```

### MCP 服务器工具

在 `configs/mcp_servers.json` 中添加条目，并设置 `NANOHARNESS_MCP=1` 启用。

---

## 添加 LLM 提供商

实现 `LLMProtocol`（鸭子类型）：

```python
from nanoharness.core.base import LLMProtocol

class MyAdapter:
    def chat(self, messages, tools=None) -> LLMResponse:
        # 在此调用你的 LLM API
        ...
```

然后传入 `NanoEngine(llm_client=MyAdapter(), ...)`。

---

## 测试

```bash
# 运行全部测试（91 个测试用例）
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# 运行指定测试模块
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_mcp.py -v

# 注意：PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 用于防止与
# 环境中可能存在的 ROS pytest 插件冲突。
```

测试覆盖范围包括：
- Schema 模型（Pydantic 验证）
- 引擎循环（终止、工具调用、错误处理、钩子、上下文）
- 工具注册器（装饰器、脚本、MCP、合并）
- 权限系统（规则、glob 匹配、参数拦截）
- 记忆系统（存储、召回、持久化、工具混入）
- 所有其他组件（上下文、钩子、状态、评估器、工具）

---

## 路线图

- [ ] **流式输出** — 逐 token 输出 LLM 结果，提供实时反馈
- [ ] **多 Agent 编排** — 协调多个 NanoEngine 实例
- [ ] **向量记忆后端** — 用嵌入检索替代关键词搜索
- [ ] **Web UI** — 浏览器交互面板，用于 Agent 监控
- [ ] **ReAct / Reflexion 模式** — 超越思考→行动→观察的可插拔推理策略
- [ ] **异步引擎模式** — 在当前同步循环之外增加原生 async 支持
- [ ] **工具结果缓存** — 避免运行内和跨运行的冗余工具调用
- [ ] **可观测性集成** — OpenTelemetry / LangFuse 追踪支持

---

## 安全提示

> **警告：拥有工具访问权限的 AI Agent 可能造成真实损害。**
>
> 在运行 LLM 驱动的 Agent 时，**务必仔细配置权限规则：**
>
> - 对破坏性操作使用 `DENY`（如 `git_reset`、`rm`）。
> - 对有外部副作用的操作使用 `CONFIRM`（如 `git_push`、`shell_exec`）。
> - 警惕 **Prompt 注入** — 工具输出可能包含恶意指令，影响 Agent 后续行为。
> - 注意 **沙箱逃逸** 风险 — Agent 可能以意想不到的方式链接工具调用以绕过权限限制。
> - 定期审查和审计工具脚本。`configs/scripts/` 中的 Shell 脚本以宿主用户的完整权限运行。
>
> **切勿在无人监督的情况下授予 Agent 不受限制的工具访问权限。**

---

## 引用

如果你在研究或项目中使用了 NanoHarness，请引用：

```bibtex
@software{nanoharness2026,
  title     = {NanoHarness: A Minimal Composable AI Agent Harness Framework},
  author    = {Habit},
  year      = {2026},
  url       = {https://github.com/HabitGraylight/NanoHarness},
  license   = {MIT}
}
```

---

## 许可证

本项目基于 **MIT 许可证** 授权。详见 [LICENSE](LICENSE) 文件。
