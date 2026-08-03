import asyncio
import json

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.lifecycle import (
    CallbackApprovalBroker,
    CompositeToolPolicy,
    EventBus,
    JsonlEventSink,
    RedactingEventSink,
    RegistryToolExecutor,
)
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.core.engine import NanoEngine
from nanoharness.core.runtime import RunControl
from nanoharness.core.schema import (
    EventType,
    HarnessEvent,
    LLMResponse,
    PolicyDecision,
    PolicyOutcome,
    PolicyStage,
    ToolCall,
    ToolRequest,
)


class SequenceLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def chat(self, messages, tools=None):
        self.messages.append(messages)
        return self.responses.pop(0)


class StagePolicy:
    def __init__(self, before=None, after=None):
        self.before = before or PolicyDecision(source="test_policy")
        self.after = after or PolicyDecision(source="test_policy")

    def decide(self, stage, request, execution=None):
        return self.before if stage == PolicyStage.BEFORE_TOOL else self.after


def build_engine(tmp_path, llm, *, policy=None, broker=None, executor=None, tools=None):
    return NanoEngine(
        llm_client=llm,
        tools=tools or DictToolRegistry(),
        context=SimpleContextManager(),
        state=JsonStateStore(str(tmp_path / "state.json")),
        hooks=SimpleHookManager(),
        evaluator=TraceEvaluator(),
        max_steps=2,
        policy=policy,
        approval_broker=broker,
        executor=executor,
    )


def tool_then_done(name="echo"):
    return [
        LLMResponse(
            content="use tool",
            tool_calls=[ToolCall(name=name, arguments={"value": "hello"})],
        ),
        LLMResponse(content="done"),
    ]


def test_composite_policy_uses_deny_precedence_and_combines_context():
    request = ToolRequest(
        call_id="call_1",
        name="danger",
        run_id="run_1",
        session_id="session_1",
        step_id=0,
    )
    inject = StagePolicy(
        before=PolicyDecision(
            context_injection="warning",
            source="warning_policy",
        )
    )
    deny = StagePolicy(
        before=PolicyDecision(
            outcome=PolicyOutcome.DENY,
            reason="blocked",
            source="deny_policy",
            metadata={"execution_status": "blocked"},
        )
    )

    decision = CompositeToolPolicy([inject, deny]).decide(
        PolicyStage.BEFORE_TOOL,
        request,
    )

    assert decision.outcome == PolicyOutcome.DENY
    assert decision.context_injection == "warning"
    assert decision.source == "warning_policy+deny_policy"
    assert decision.metadata["execution_status"] == "blocked"


def test_approval_broker_and_executor_are_separate_boundaries(tmp_path):
    seen = []

    class Executor:
        def execute(self, request):
            seen.append(request)
            return "executed"

    policy = StagePolicy(
        before=PolicyDecision(
            outcome=PolicyOutcome.REQUIRE_APPROVAL,
            reason="write operation",
            source="test_policy",
        )
    )
    broker = CallbackApprovalBroker(lambda request, decision: True)
    engine = build_engine(
        tmp_path,
        SequenceLLM(tool_then_done()),
        policy=policy,
        broker=broker,
        executor=Executor(),
    )

    report = engine.run("write it", run_id="run_approval")
    event_types = [event["type"] for event in report["events"]]

    assert report["trajectory"][0]["actions"][0]["status"] == "success"
    assert seen[0].run_id == "run_approval"
    assert EventType.APPROVAL_REQUESTED.value in event_types
    assert EventType.APPROVAL_RESOLVED.value in event_types
    assert EventType.TOOL_EXECUTION_STARTED.value in event_types


def test_missing_approval_broker_fails_closed(tmp_path):
    called = []

    class Executor:
        def execute(self, request):
            called.append(True)
            return "should not run"

    policy = StagePolicy(
        before=PolicyDecision(outcome=PolicyOutcome.REQUIRE_APPROVAL)
    )
    engine = build_engine(
        tmp_path,
        SequenceLLM(tool_then_done()),
        policy=policy,
        executor=Executor(),
    )

    report = engine.run("write it")

    assert called == []
    action = report["trajectory"][0]["actions"][0]
    assert action["status"] == "denied"
    assert "no approval broker" in action["error"].lower()


def test_post_policy_can_augment_observation(tmp_path):
    registry = DictToolRegistry()

    @registry.tool
    def echo(value: str):
        """Echo a value."""
        return value

    policy = StagePolicy(
        after=PolicyDecision(output_suffix="post-policy note")
    )
    engine = build_engine(
        tmp_path,
        SequenceLLM(tool_then_done()),
        policy=policy,
        tools=registry,
    )

    report = engine.run("echo")

    assert report["trajectory"][0]["observation"] == "hello\npost-policy note"


def test_run_control_can_cancel_before_first_step(tmp_path):
    llm = SequenceLLM([LLMResponse(content="must not run")])
    control = RunControl()
    control.cancel("operator stopped run")
    engine = build_engine(tmp_path, llm)

    report = engine.run("stop", control=control)

    assert llm.messages == []
    assert report["run"]["status"] == "cancelled"
    assert report["run"]["stop_reason"] == "operator stopped run"
    assert report["events"][-1]["type"] == "run_cancelled"


def test_run_control_applies_steering_before_model_turn(tmp_path):
    llm = SequenceLLM([LLMResponse(content="done")])
    control = RunControl()
    control.steer("also inspect tests")
    engine = build_engine(tmp_path, llm)

    report = engine.run("inspect code", control=control)

    contents = [message["content"] for message in llm.messages[0]]
    assert contents[-2:] == ["inspect code", "also inspect tests"]
    assert any(event["type"] == "steering_applied" for event in report["events"])


def test_run_control_observes_live_cancellation_at_step_boundary(tmp_path):
    registry = DictToolRegistry()

    @registry.tool
    def echo(value: str):
        """Echo a value."""
        return value

    control = RunControl()

    class CancellingSink:
        def publish(self, event):
            if event.type == EventType.TOOL_COMPLETED:
                control.cancel("cancelled from live event")

    engine = build_engine(
        tmp_path,
        SequenceLLM(tool_then_done()),
        tools=registry,
    )
    engine.max_steps = 1

    report = engine.run("echo", control=control, event_sink=CancellingSink())

    assert report["run"]["status"] == "cancelled"
    assert report["summary"]["total_steps"] == 1
    assert report["events"][-1]["type"] == "run_cancelled"


def test_live_steering_is_applied_to_next_model_turn(tmp_path):
    registry = DictToolRegistry()

    @registry.tool
    def echo(value: str):
        """Echo a value."""
        return value

    control = RunControl()

    class SteeringSink:
        def publish(self, event):
            if event.type == EventType.STEP_COMPLETED:
                control.steer("check the edge case too")

    llm = SequenceLLM(tool_then_done())
    engine = build_engine(tmp_path, llm, tools=registry)

    engine.run("echo", control=control, event_sink=SteeringSink())

    contents = [message["content"] for message in llm.messages[1]]
    assert "check the edge case too" in contents


def test_arun_returns_normal_report(tmp_path):
    engine = build_engine(
        tmp_path,
        SequenceLLM([LLMResponse(content="done")]),
    )

    report = asyncio.run(engine.arun("async task", run_id="run_async"))

    assert report["run"]["run_id"] == "run_async"
    assert report["run"]["status"] == "completed"


def test_astream_yields_ordered_live_events(tmp_path):
    engine = build_engine(
        tmp_path,
        SequenceLLM([LLMResponse(content="done")]),
    )

    async def collect():
        return [event async for event in engine.astream("stream task")]

    events = asyncio.run(collect())

    assert events[0].type == EventType.RUN_STARTED
    assert events[-1].type == EventType.RUN_COMPLETED
    assert [event.sequence for event in events] == list(range(len(events)))


def test_event_pipeline_redacts_and_persists_jsonl(tmp_path):
    path = tmp_path / "events.jsonl"
    bus = EventBus([
        RedactingEventSink(JsonlEventSink(str(path)))
    ])
    event = HarnessEvent(
        run_id="run_1",
        session_id="session_1",
        sequence=0,
        type=EventType.RUN_STARTED,
        data={"token": "secret", "nested": {"password": "hidden", "safe": 1}},
    )

    bus.publish(event)
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert saved["data"]["token"] == "[REDACTED]"
    assert saved["data"]["nested"]["password"] == "[REDACTED]"
    assert saved["data"]["nested"]["safe"] == 1


def test_event_bus_isolates_subscriber_failures():
    seen = []

    def fail(event):
        raise RuntimeError("broken UI")

    bus = EventBus([fail, seen.append])
    event = HarnessEvent(
        run_id="run_1",
        session_id="session_1",
        sequence=0,
        type=EventType.RUN_STARTED,
    )

    bus.publish(event)

    assert seen == [event]
    assert "broken UI" in bus.errors[0]


def test_registry_executor_adapts_existing_tool_registry():
    registry = DictToolRegistry()

    @registry.tool
    def echo(value: str):
        """Echo a value."""
        return value

    request = ToolRequest(
        call_id="call_1",
        name="echo",
        arguments={"value": "hello"},
        run_id="run_1",
        session_id="session_1",
        step_id=0,
    )

    assert RegistryToolExecutor(registry).execute(request) == "hello"
