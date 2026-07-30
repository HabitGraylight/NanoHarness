# NanoLoop

NanoLoop 是构建在 NanoHarness 之上的独立 Loop Engineering 示例。NanoHarness
负责一次 Agent 运行，NanoLoop 负责反复创建干净的运行、验证产物、保存证据，
并在预算终点或人工 Gate 停止。

## 运行模型

```text
手动触发
  → 创建隔离 Git worktree
  → 创建全新 NanoEngine
  → Worker 在 worktree 内修改文件
  → 执行可信验收命令
      ├─ 通过 → WAITING_HUMAN 或 COMPLETED
      ├─ 失败 → 将结构化证据反馈到下一轮
      └─ 超预算/失败阈值 → BLOCKED 或 BUDGET_EXHAUSTED
```

Agent 自己声明“完成”不算成功，只有 Verifier 产生的证据可以推动 Loop 完成。

## 快速开始

```bash
pip install -e ".[openai]"
export DEEPSEEK_API_KEY="sk-..."

cd examples/nano_loop
python main.py run configs/loops/local_fix.yaml \
  --repo ../.. \
  --task "为 evaluator query 传递增加一个回归测试"
```

管理运行：

```bash
python main.py list
python main.py status <run-id>
python main.py resume <run-id>
python main.py approve <run-id>
python main.py reject <run-id> --reason "修改范围过大"
```

`approve` 只记录人工接受了证据。第一版不会自动执行 commit、push、merge 或
其他外部写操作。

Loop 停止后会保留 worktree 和 `nanoloop/<run-id>` 分支，供人工检查、提交或
丢弃结果。NanoLoop 不会自动删除包含 Agent 未提交修改的 workspace。

## 安全边界

- 每轮创建全新的 NanoEngine，避免 Context 和 Evaluator 污染下一轮。
- Worker 只拥有 worktree 内的文件工具，不提供 Shell、Git、push 或 merge。
- 文件路径和 glob 不能逃逸 workspace，并禁止直接访问 `.git`。
- 验收命令只能来自受信任的 YAML，而不是模型输出。
- 每个 Loop 都有迭代次数、总时间和连续失败预算。
- Worker 与 Verifier 阶段前后都会持久化状态。
- 不可逆动作始终保留人工 Gate。

## 测试

```bash
cd examples/nano_loop
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v
```

当前实现是单机应用示例。生产环境还需要分布式租约、身份认证、Secret 管理、
成本预算以及真正的命令沙箱。
