<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/Tests-89%20passed-brightgreen.svg" alt="Tests">
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
  - [运行](#运行)
- [核心设计](#核心设计)
  - [引擎循环](#引擎循环)
  - [工具系统](#工具系统)
  - [权限系统](#权限系统)
  - [记忆系统](#记忆系统)
  - [MCP 集成](#mcp-集成)
  - [Prompt 管理](#prompt-管理)
- [示例](#示例)
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

1. **极简** — 无重度依赖，无不透明抽象。核心引擎是纯粹的策略无关循环。
2. **可组合** — 每个组件（LLM 适配器、工具注册器、记忆、权限、钩子）均可注入和替换。
3. **可复用** — 清晰的接口（`LLMProtocol`、`BaseToolRegistry` 等）便于扩展或嵌入更大的系统。

框架实现了 **思考 → 行动 → 观察** 的 Agent 循环：

```
用户查询 → [上下文] → LLM 思考 → 工具执行 → 观察结果 → [重复] → 输出报告
```

**内核无策略。** `NanoEngine` 只负责循环编排 — 记忆注入、Prompt 渲染、权限 I/O、输出格式化全部由注入的组件和钩子在 app 层处理。这使得内核可以安全地在不同 Agent 应用之间共享。

---

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                          NanoEngine                              │
│                    (策略无关的循环内核)                            │
│                                                                  │
│   ┌──────────┐                              ┌────────────────┐  │
│   │  Context  │                              │   HookManager  │  │
│   │ Manager   │                              │ ON_START/END   │  │
│   └────┬─────┘                              │ ON_THOUGHT/STEP│  │
│        │                                     └────────────────┘  │
│        ▼                                                           │
│   ┌────────────────────────────┐     ┌───────────────────────┐  │
│   │      LLMProtocol           │     │  PermissionManager    │  │
│   │  ┌──────┐ ┌──────┐        │     │  enforce() → None|err │  │
│   │  │OpenAI│ │Anthro│  ...   │     └───────────────────────┘  │
│   │  └──────┘ └──────┘        │                                 │
│   └────────────┬───────────────┘                                │
│                │                                                  │
│                ▼                                                  │
│   ┌────────────────────────────────┐     ┌───────────────────┐  │
│   │     BaseToolRegistry           │     │    Evaluator       │  │
│   │  ┌──────────┐  ┌───────────┐  │     │  (Trace)           │  │
│   │  │ Script   │  │   MCP     │  │     └───────────────────┘  │
│   │  │ Registry │  │ Registry  │  │                             │
│   │  └──────────┘  └───────────┘  │     ┌───────────────────┐  │
│   │        └────── merge() ──────┘     │  StateStore       │  │
│   └────────────────────────────────┘   │  (JSON)           │  │
│                                        └───────────────────┘  │
└──────────────────────────────────────────────────────────────────┘

    App 层 (main.py / examples/coding_agent/app/)
    ┌─────────────────────────────────────────────────────┐
    │  PromptManager.from_file()    记忆注入 hooks        │
    │  权限规则                      记忆持久化            │
    │  工具装配                      终端 UI / 输出格式化   │
    └─────────────────────────────────────────────────────┘
```

---

## 项目结构

```
NanoHarness/
├── main.py                          # 入口 — 组装组件，启动 Agent
├── pyproject.toml                   # 包元数据与依赖
├── LICENSE                          # MIT 许可证
│
├── nanoharness/                     # 核心包（策略无关内核）
│   ├── core/                        # 框架内核 — 稳定，极少修改
│   │   ├── schema.py                #   Pydantic 模型
│   │   ├── base.py                  #   抽象基类与协议
│   │   ├── engine.py                #   NanoEngine — 纯粹的思考→行动→观察循环
│   │   │                            #   无记忆、无 Prompt、无权限 I/O
│   │   └── prompt.py                #   PromptManager — 通过 from_file() 加载模板
│   │
│   ├── components/                  # 可插拔实现
│   │   ├── llm/                     #   LLM 适配器
│   │   │   ├── openai_adapter.py    #     OpenAI / DeepSeek
│   │   │   ├── anthropic_adapter.py #     Anthropic Claude
│   │   │   ├── litellm_adapter.py   #     LiteLLM 多提供商网关
│   │   │   └── vllm_adapter.py      #     vLLM 本地推理
│   │   ├── tools/                   #   工具注册器
│   │   │   ├── dict_registry.py     #     DictToolRegistry — @tool 装饰器
│   │   │   └── script_tools.py      #     ScriptToolRegistry — 自动发现脚本
│   │   ├── mcp/                     #   MCP 集成
│   │   │   ├── client.py            #     MCPClient
│   │   │   └── registry.py          #     MCPToolRegistry
│   │   ├── context/                 #   SimpleContextManager
│   │   ├── memory/                  #   SimpleMemoryManager + MemoryToolMixin
│   │   ├── permissions/             #   RulePermissionManager（可注入审批回调）
│   │   ├── hooks/                   #   SimpleHookManager
│   │   ├── state/                   #   JsonStateStore
│   │   └── evaluator/               #   TraceEvaluator
│   └── utils/                       # get_logger, count_tokens
│
├── configs/
│   ├── prompts.yaml                 # Prompt 模板
│   ├── mcp_servers.json             # MCP 服务器定义
│   └── scripts/                     # Shell 脚本工具（26 个，自动发现）
│
├── examples/
│   └── coding_agent/                # 自包含的 Coding Agent 示例
│       ├── main.py                  #   终端 UI 入口（REPL 模式）
│       ├── app/                     #   App 层（组装 + 配置）
│       │   ├── builder.py           #     引擎组装 + 记忆 hooks
│       │   ├── hooks.py             #     彩色终端输出 hooks
│       │   ├── ui.py                #     REPL 循环 + readline 支持
│       │   ├── tools.py             #     脚本工具 + Python 原生搜索
│       │   ├── permissions.py       #     Coding 专属权限策略
│       │   └── prompts.yaml         #     Coding agent 专属 prompt
│       ├── nanoharness/             #   软链接 → ../../nanoharness（共享内核）
│       └── tests/                   #   冒烟测试（9 个）
│
└── tests/                           # 根目录测试套件（80 个）
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

# 3. 以可编辑模式安装
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

```bash
# 设置 API Key
export DEEPSEEK_API_KEY="sk-..."

# 可选：启用 MCP 工具
export NANOHARNESS_MCP=1
```

### 运行

```bash
# 根目录交互模式
python main.py

# Coding Agent 示例（带终端 UI）
cd examples/coding_agent
python main.py

# 单次任务模式
python main.py "给 nanoharness/core/engine.py 的所有方法加上类型注解"
```

---

## 核心设计

### 引擎循环

`NanoEngine` 是**策略无关的循环编排器**：

1. **Hooks** — `ON_TASK_START` 触发（app 层处理记忆注入）
2. **思考** — 将完整上下文连同可用工具 schema 发送给 LLM
3. **权限门控** — `permissions.enforce()` 返回 `None`（放行）或错误字符串（跳过）
4. **行动** — 执行工具，将观察结果追加到上下文
5. **评估** — 每个步骤由评估器记录
6. **终止** — LLM 响应不含工具调用时，循环结束并生成报告
7. **Hooks** — `ON_TASK_END` 触发（app 层处理记忆持久化）

引擎**不依赖** PromptManager、MemoryManager 或终端 I/O。所有策略通过注入实现。

### 工具系统

| 实现 | 来源 | 核心特性 |
|---|---|---|
| `DictToolRegistry` | Python `@tool` 装饰器 | 根据类型提示自动推断 JSON Schema |
| `ScriptToolRegistry` | `configs/scripts/*.sh` | 自动发现 Shell 脚本，解析 `@param` 头部 |
| `MCPToolRegistry` | MCP 服务器 | 发现远程工具，转换为 OpenAI schema |

所有注册器可通过 `registry.merge(other)` **合并**。

### 权限系统

三级权限控制，审批 I/O 通过 `approval_callback` 解耦：

```python
# 终端审批（默认）
perms = RulePermissionManager()

# 自定义审批（如 API 服务器）
perms = RulePermissionManager(approval_callback=lambda name, args: auto_approve(name))
```

### 记忆系统

双层记忆架构，由 **app 层**管理（非引擎）：

- **工作记忆** — 每次运行清除的临时 dict
- **持久记忆** — JSON 文件，支持关键词搜索

记忆注入和持久化通过 hooks 实现。

### MCP 集成

[MCP（Model Context Protocol）](https://modelcontextprotocol.io/) 使 Agent 能够使用外部服务器暴露的工具。

### Prompt 管理

通过 `PromptManager.from_file()` 显式加载：

```python
from nanoharness.core.prompt import PromptManager
pm = PromptManager.from_file("configs/prompts.yaml")
pm.render("memory.inject", entries=mem_text)
```

内核不自动加载任何配置文件，每个 app 加载自己的 Prompt 模板。

---

## 示例

### Coding Agent

位于 `examples/coding_agent/` 的自包含 Coding Agent：

- 通过软链接共享内核（`nanoharness/ → ../../nanoharness`）
- 独立的 app 层：prompts、tools、permissions、hooks、UI
- REPL 模式带彩色输出、readline 支持、输入历史
- 运行时文件隔离在 `sandbox/`

```bash
cd examples/coding_agent
export DEEPSEEK_API_KEY="sk-..."
python main.py
```

详见 [examples/coding_agent/README.md](examples/coding_agent/README.md)。

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

自动发现并注册，无需修改 Python 代码。

### Python 函数工具

```python
from nanoharness.components.tools.dict_registry import DictToolRegistry

registry = DictToolRegistry()

@registry.tool
def my_tool(name: str, count: int = 5) -> str:
    """做些有用的事情。"""
    return f"已处理 {name} x{count}"
```

### MCP 服务器工具

```python
from nanoharness.components.mcp import MCPClient, MCPToolRegistry
client = MCPClient()
client.connect_stdio("myserver", "npx", args=["-y", "some-mcp-server"])
mcp_tools = MCPToolRegistry(client)
```

---

## 添加 LLM 提供商

实现 `LLMProtocol`（鸭子类型）：

```python
class MyAdapter:
    def chat(self, messages, tools=None) -> LLMResponse:
        ...
```

传入 `NanoEngine(llm_client=MyAdapter(), ...)`。

---

## 测试

```bash
# 根目录测试（80 个）
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# Coding Agent 示例测试（9 个）
cd examples/coding_agent
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v
```

---

## 路线图

- [ ] **流式输出** — 逐 token 输出 LLM 结果
- [ ] **多 Agent 编排** — 协调多个 NanoEngine 实例
- [ ] **向量记忆后端** — 用嵌入检索替代关键词搜索
- [ ] **ReAct / Reflexion 模式** — 可插拔推理策略
- [ ] **异步引擎模式** — 原生 async 支持
- [ ] **工具结果缓存** — 避免冗余工具调用
- [ ] **可观测性集成** — OpenTelemetry / LangFuse 追踪

---

## 安全提示

> **警告：拥有工具访问权限的 AI Agent 可能造成真实损害。**
>
> - 对破坏性操作使用 `DENY`（如 `git_reset`、`rm`）。
> - 对有外部副作用的操作使用 `CONFIRM`（如 `git_push`、`shell_exec`）。
> - 警惕 **Prompt 注入** 和 **沙箱逃逸** 风险。
> - **切勿在无人监督的情况下授予 Agent 不受限制的工具访问权限。**

---

## 引用

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
