# NanoOpenClaw

NanoOpenClaw is an independently runnable, durable conversation gateway. It
reuses the public `channels.durable` Extension for inbox/outbox persistence and
adapters, while the Example owns routing, conversation history, resumable Turn
state, read-only tools, outbound approval, and delivery policy.

```bash
python examples/nano_openclaw/main.py
cd examples/nano_openclaw && pytest -q tests
```

The deterministic demo sends two messages through the same mock route. The
second Turn receives the first delivered exchange as conversation context.
Every response must pass through `response_submit`, a durable outbox record,
and a separate host approval before delivery.

A real OpenAI-compatible provider can be used without changing the harness:

```bash
python examples/nano_openclaw/main.py \
  --provider openai --model gpt-4.1-mini \
  --task "Summarize the project brief"
```

`console` and `webhook` are deterministic mock adapter names in this stage;
there is intentionally no public HTTP server yet. A production transport only
needs to normalize input into `InboundEnvelope` and implement the public
Channel Adapter protocol.

## 中文

NanoOpenClaw 是一个可独立运行、可恢复的多渠道会话 Gateway。公共
`channels.durable` Extension 负责可靠 Inbox/Outbox 与 Adapter 协议；本 Example
负责路由、Conversation 历史、Turn 状态、只读工具、外发审批和交付策略。

默认 Demo 完全离线：同一路由连续发送两条消息，第二个 Turn 会读到第一轮已经
成功交付的会话历史。模型必须显式调用 `response_submit`，Host 才会创建 Outbox、
记录不含正文的审批审计并尝试发送。本阶段的 `console` / `webhook` 仍是 Mock
Adapter 名称，不包含公网 HTTP Server。
