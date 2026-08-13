# NanoOpenClaw

NanoOpenClaw is an independently runnable, durable conversation gateway. It
composes the public `channels.durable` and `scheduler.local` Extensions, while
the Example owns the control plane: Wakeup normalization and trust, stable
conversation routing, resumable Turn state, read-only model tools, outbound
approval, and delivery recovery.

```text
Channel / Schedule / Background / Operator
                    |
              durable Wakeup
                    |
       Conversation -> Generate -> Outbox
                                    |
                              Approve -> Deliver
```

Generation and delivery are deliberately separate. Once `response_submit`
creates a durable Outbox record, an adapter failure can be retried in another
process without calling the model again. Stable source IDs deduplicate replayed
inputs, and persisted claim leases make interrupted Wakeups recoverable.

| Source | Trust | Prompt placement |
|---|---|---|
| Channel | `untrusted` | user context |
| Scheduler | `trusted_system` | system context |
| Background completion | `trusted_system` | system context |
| Manual operator event | `operator` | operator-authorized input |

The trust level is fixed by source type; input metadata cannot promote itself.
Background notifications can be normalized by the host API, but this Profile
does not expose raw shell execution to the model.

## Deterministic demo

```bash
python examples/nano_openclaw/main.py
cd examples/nano_openclaw && pytest -q tests
```

The offline demo processes two Channel messages on one route, replays the first
input to prove deduplication, handles a trusted scheduled Wakeup, and injects one
adapter failure before recovering delivery. It finishes with three delivered
Turns and requires no API key or network.

## Operational CLI

Every command that participates in one durable run must use the same `--output`
and `--job` values. Operations can be invoked separately or combined.

```bash
# Install/collect due schedules, generate responses, then deliver queued output.
python examples/nano_openclaw/main.py --output .openclaw --run-due --deliver

# Inspect pending Wakeups, Turns, and Outbox records without mutating them.
python examples/nano_openclaw/main.py --output .openclaw --list-pending

# Resume interrupted generation, or deliver one waiting Turn.
python examples/nano_openclaw/main.py --output .openclaw --resume RUN_ID
python examples/nano_openclaw/main.py --output .openclaw --delivery-id RUN_ID
```

`--ingest FILE` accepts one `InboundEnvelope` JSON object or a list of objects.
Arbitrary external messages require a real provider for response generation:

```bash
python examples/nano_openclaw/main.py \
  --output .openclaw --ingest inbound.json --run-pending \
  --provider openai --model YOUR_MODEL

# Delivery is independent and does not need to initialize the provider again.
python examples/nano_openclaw/main.py --output .openclaw --deliver
```

Use `--task` for an explicitly operator-authorized manual Wakeup. Real-provider
delivery defaults to interactive approval; pass `--approval auto` only when the
host environment is intended to authorize it.

`console` and `webhook` remain deterministic mock adapter names. There is no
public HTTP server in this Example. A production transport only needs to
normalize input into `InboundEnvelope` and implement the public Channel Adapter
protocol.

## 中文

NanoOpenClaw 是一个可独立运行、可恢复的多来源会话 Gateway。它组合公共
`channels.durable` 与 `scheduler.local` Extension；Wakeup 归一化与信任分级、稳定
Conversation 路由、可恢复 Turn、只读模型工具、外发审批和投递恢复则由 Example 自己
控制。

生成和交付是两个独立阶段。`response_submit` 创建持久 Outbox 后，即使 Adapter 发送
失败，也可以在另一个进程中继续投递而无需重跑模型。稳定 Source ID 负责重复输入去重，
持久 Claim Lease 负责恢复中断的 Wakeup。Channel 固定为 `untrusted`；Scheduler 和
Background Completion 固定为 `trusted_system`；Manual Event 固定为 `operator`，输入
Metadata 不能自行提升权限。可信系统事件进入 System Context，不伪装成 User Message。

默认 Demo 完全离线：同一路由连续处理两条 Channel 消息，重放第一条验证去重，再处理
一次可信 Schedule Wakeup，并注入一次 Adapter 失败后恢复交付；最终得到三个已交付
Turn。CLI 支持 `--ingest`、`--run-pending`、`--run-due`、`--deliver`、
`--delivery-id`、`--resume` 和 `--list-pending`，这些操作既可组合，也可跨进程执行。
本 Profile 不向模型暴露 Raw Shell，`console` / `webhook` 仍只是 Mock Adapter 名称，
不包含公网 HTTP Server。
