# NanoOpenClaw

An independently runnable minimal gateway-style harness. Its Example owns the
Gateway policy and mock channel adapter while reusing Memory, Scheduler, and
Background extensions.

```bash
python examples/nano_openclaw/main.py
cd examples/nano_openclaw && pytest -q tests
```

The default run is deterministic and network-free. Workspace mutation is
denied by the local Gateway Policy; message delivery is exposed through the
Example-owned mock channel.

## 中文

NanoOpenClaw 是独立可运行的最小 Gateway 风格 Harness。Gateway Policy 与 Mock
Channel 属于本 Example；Memory、Scheduler 和 Background 来自公共 Extension。
入口、Profile、Policy、Scenario、测试和文档均由本 Example 自己维护。默认运行
不需要网络，并禁止 Gateway Session 修改工作区。
