"""Policy-light orchestration for the NanoHarness ETCSLV kernel."""

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Optional
from uuid import uuid4

from nanoharness.core.base import (
    ApprovalBrokerProtocol,
    BaseContextManager,
    BaseEvaluator,
    BaseHookManager,
    BaseStateStore,
    BaseToolRegistry,
    EventSinkProtocol,
    HookStage,
    LLMProtocol,
    ToolExecutorProtocol,
    ToolPolicyProtocol,
)
from nanoharness.core.runtime import RunControl
from nanoharness.core.schema import (
    AgentMessage,
    ApprovalResult,
    ApprovalStatus,
    EventType,
    HarnessEvent,
    PolicyDecision,
    PolicyOutcome,
    PolicyStage,
    RunCheckpoint,
    RunContext,
    RunStatus,
    StepResult,
    ToolCall,
    ToolExecution,
    ToolExecutionStatus,
    ToolRequest,
)


class _FanoutEventSink:
    """Publish to every sink even when one observer fails."""

    def __init__(self, *sinks: EventSinkProtocol):
        self._sinks = [sink for sink in sinks if sink is not None]

    def publish(self, event: HarnessEvent) -> None:
        errors = []
        for sink in self._sinks:
            try:
                sink.publish(event)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))


class _RunEventRecorder:
    """Collect ordered run-local events and optionally publish them."""

    def __init__(
        self,
        run_context: RunContext,
        sink: Optional[EventSinkProtocol] = None,
    ):
        self.run_context = run_context
        self.sink = sink
        self.events: list[HarnessEvent] = []
        self.sink_errors: list[str] = []
        self._sequence = 0

    def emit(
        self,
        event_type: EventType,
        *,
        step_id: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> HarnessEvent:
        event = HarnessEvent(
            run_id=self.run_context.run_id,
            session_id=self.run_context.session_id,
            sequence=self._sequence,
            type=event_type,
            step_id=step_id,
            data=data or {},
        )
        self._sequence += 1
        self.events.append(event)

        if self.sink:
            try:
                self.sink.publish(event)
            except Exception as exc:
                # Observability must not change agent behavior. Surface sink
                # failures in the report instead of failing the run.
                self.sink_errors.append(f"{type(exc).__name__}: {exc}")

        return event

    def dump(self) -> list[Dict[str, Any]]:
        return [event.model_dump(mode="json") for event in self.events]


class NanoEngine:
    """Minimal agent loop orchestrator: Think -> Act -> Observe.

    The engine is a thin coordination layer. It does NOT contain:
    - Memory strategies (inject / persist) — wire via hooks
    - Prompt template rendering — belongs in app layer
    - Permission I/O (interactive confirm) — belongs in permission manager
    - Tool error formatting — belongs in tool registry or app layer

    All policy is injected through components and hooks.
    """

    def __init__(
        self,
        llm_client: LLMProtocol,
        tools: BaseToolRegistry,
        context: BaseContextManager,
        state: BaseStateStore,
        hooks: BaseHookManager,
        evaluator: BaseEvaluator,
        max_steps: int = 10,
        permissions: Optional[Any] = None,  # duck-typed: enforce(name, args) -> str|None
        tool_hooks=None,
        event_sink: Optional[EventSinkProtocol] = None,
        session_id: Optional[str] = None,
        policy: Optional[ToolPolicyProtocol] = None,
        approval_broker: Optional[ApprovalBrokerProtocol] = None,
        executor: Optional[ToolExecutorProtocol] = None,
    ):
        self.llm = llm_client
        self.tools = tools
        self.context = context
        self.state = state
        self.hooks = hooks
        self.evaluator = evaluator
        self.max_steps = max_steps
        self.permissions = permissions
        self.tool_hooks = tool_hooks
        self.event_sink = event_sink
        self.session_id = session_id or f"session_{uuid4().hex}"
        self.policy = policy
        self.approval_broker = approval_broker
        self.executor = executor

    def run(
        self,
        user_query: str,
        *,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        control: Optional[RunControl] = None,
        event_sink: Optional[EventSinkProtocol] = None,
    ) -> Dict:
        """Execute one isolated run within this engine's conversation session."""

        run_context = RunContext(
            **({"run_id": run_id} if run_id else {}),
            session_id=session_id or self.session_id,
            query=user_query,
            max_steps=self.max_steps,
        )
        active_sink = self._combine_event_sinks(self.event_sink, event_sink)
        recorder = _RunEventRecorder(run_context, active_sink)
        trajectory: list[StepResult] = []
        run_status = RunStatus.RUNNING
        stop_reason = ""

        # Evaluations and reports are run-local even when the context is kept
        # across multiple REPL tasks in the same session.
        self.evaluator.reset()
        recorder.emit(
            EventType.RUN_STARTED,
            data={
                "query": user_query,
                "max_steps": self.max_steps,
                "protocol_version": run_context.protocol_version,
            },
        )

        try:
            self.hooks.trigger(HookStage.ON_TASK_START, user_query)
            self.context.add_message(AgentMessage(role="user", content=user_query))

            for i in range(self.max_steps):
                if control and control.cancelled:
                    run_status = RunStatus.CANCELLED
                    stop_reason = control.cancel_reason
                    break

                if control:
                    for steering_message in control.drain_steering():
                        self.context.add_message(
                            AgentMessage(role="user", content=steering_message)
                        )
                        recorder.emit(
                            EventType.STEERING_APPLIED,
                            step_id=i,
                            data={"message": steering_message},
                        )

                step_res = self._execute_step(i, recorder)

                self.evaluator.log_step(step_res)
                trajectory.append(step_res)

                # Mid-loop evaluation is part of the completed step so hooks,
                # state, and events all observe the same stop signal.
                stop_signal = self.evaluator.should_stop(trajectory)
                if control and control.cancelled:
                    run_status = RunStatus.CANCELLED
                    stop_reason = control.cancel_reason
                elif stop_signal.should_stop:
                    step_res.stop_signal = stop_signal
                    run_status = RunStatus.STOPPED
                    stop_reason = stop_signal.reason
                elif step_res.status == "terminated":
                    run_status = RunStatus.COMPLETED

                recorder.emit(
                    EventType.STEP_COMPLETED,
                    step_id=i,
                    data={"step": step_res.model_dump(mode="json")},
                )
                self.hooks.trigger(HookStage.ON_STEP_END, step_res)
                self._save_checkpoint(
                    run_context,
                    trajectory,
                    status=run_status,
                    current_step=i,
                    stop_reason=stop_reason,
                )

                if run_status in {
                    RunStatus.STOPPED,
                    RunStatus.COMPLETED,
                    RunStatus.CANCELLED,
                }:
                    break

            if run_status == RunStatus.RUNNING:
                run_status = RunStatus.MAX_STEPS
                stop_reason = "Maximum steps reached"

            report = self.evaluator.get_report(user_query)
            evaluation = report.get("summary", {}).get("evaluation", {})
            recorder.emit(
                EventType.EVALUATION_COMPLETED,
                data={"evaluation": evaluation},
            )

            report["run"] = {
                **run_context.model_dump(mode="json"),
                "status": run_status.value,
                "stop_reason": stop_reason,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            report.setdefault("summary", {})["run_id"] = run_context.run_id
            report["summary"]["run_status"] = run_status.value
            report["events"] = recorder.dump()
            if recorder.sink_errors:
                report["event_sink_errors"] = list(recorder.sink_errors)

            self.hooks.trigger(HookStage.ON_TASK_END, report)
            recorder.emit(
                (
                    EventType.RUN_CANCELLED
                    if run_status == RunStatus.CANCELLED
                    else EventType.RUN_COMPLETED
                ),
                data={
                    "status": run_status.value,
                    "success": bool(report["summary"].get("success", False)),
                    "stop_reason": stop_reason,
                },
            )
            # Include the terminal event emitted after the ON_TASK_END hook.
            report["events"] = recorder.dump()
            if recorder.sink_errors:
                report["event_sink_errors"] = list(recorder.sink_errors)
            self._save_checkpoint(
                run_context,
                trajectory,
                status=run_status,
                current_step=len(trajectory) - 1,
                stop_reason=stop_reason,
            )
            return report

        except Exception as exc:
            recorder.emit(
                EventType.RUN_FAILED,
                data={"error": f"{type(exc).__name__}: {exc}"},
            )
            # Preserve the original exception if the failure itself came from
            # persistence or the state backend is also unavailable.
            try:
                self._save_checkpoint(
                    run_context,
                    trajectory,
                    status=RunStatus.FAILED,
                    current_step=len(trajectory) - 1,
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
            raise

    async def arun(
        self,
        user_query: str,
        *,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        control: Optional[RunControl] = None,
        event_sink: Optional[EventSinkProtocol] = None,
    ) -> Dict:
        """Run the synchronous engine in a worker thread.

        The same cooperative ``RunControl`` works for both sync and async
        callers. A single engine instance should still execute one run at a
        time because its Context and Evaluator are intentionally stateful.
        """

        return await asyncio.to_thread(
            self.run,
            user_query,
            run_id=run_id,
            session_id=session_id,
            control=control,
            event_sink=event_sink,
        )

    async def astream(
        self,
        user_query: str,
        *,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        control: Optional[RunControl] = None,
    ) -> AsyncIterator[HarnessEvent]:
        """Yield ordered events live while an async run executes."""

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        finished = object()
        active_control = control or RunControl()

        class QueueSink:
            def publish(self, event: HarnessEvent) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, event)

        task = asyncio.create_task(
            self.arun(
                user_query,
                run_id=run_id,
                session_id=session_id,
                control=active_control,
                event_sink=QueueSink(),
            )
        )
        task.add_done_callback(
            lambda _task: loop.call_soon_threadsafe(queue.put_nowait, finished)
        )

        try:
            while True:
                item = await queue.get()
                if item is finished:
                    break
                yield item
            await task
        finally:
            if not task.done():
                active_control.cancel("Event stream closed by caller")
                try:
                    await task
                except (Exception, asyncio.CancelledError):
                    pass

    @staticmethod
    def _combine_event_sinks(
        first: Optional[EventSinkProtocol],
        second: Optional[EventSinkProtocol],
    ) -> Optional[EventSinkProtocol]:
        if first is None:
            return second
        if second is None or first is second:
            return first
        return _FanoutEventSink(first, second)

    def _execute_step(
        self,
        step_id: int,
        recorder: _RunEventRecorder,
    ) -> StepResult:
        recorder.emit(EventType.STEP_STARTED, step_id=step_id)

        # Think
        prompt = self.context.get_full_context()
        tool_schemas = self.tools.get_tool_schemas()
        recorder.emit(
            EventType.MODEL_REQUESTED,
            step_id=step_id,
            data={
                "message_count": len(prompt),
                "tool_count": len(tool_schemas),
            },
        )
        response = self.llm.chat(prompt, tools=tool_schemas)

        normalized_calls = None
        if response.tool_calls is not None:
            normalized_calls = [
                self._with_call_id(call, recorder.run_context.run_id, step_id, index)
                for index, call in enumerate(response.tool_calls)
            ]
            response = response.model_copy(update={"tool_calls": normalized_calls})

        self.context.add_message(
            AgentMessage(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
        )
        recorder.emit(
            EventType.MODEL_RESPONDED,
            step_id=step_id,
            data={
                "content": response.content,
                "tool_calls": [
                    call.model_dump(mode="json") for call in (response.tool_calls or [])
                ],
                "model": response.model,
                "finish_reason": response.finish_reason,
                "usage": response.usage.model_dump(mode="json") if response.usage else None,
            },
        )
        self.hooks.trigger(HookStage.ON_THOUGHT_READY, response)

        # Act
        step_res = StepResult(step_id=step_id, thought=response.content)

        if response.tool_calls:
            for call in response.tool_calls:
                self._execute_tool(call, step_res, recorder)
        else:
            step_res.status = "terminated"

        return step_res

    def _execute_tool(
        self,
        call: ToolCall,
        step_res: StepResult,
        recorder: _RunEventRecorder,
    ) -> None:
        call_id = call.call_id or f"call_{uuid4().hex}"
        legacy_action = {"name": call.name, "arguments": call.arguments}
        request = ToolRequest(
            call_id=call_id,
            name=call.name,
            arguments=call.arguments,
            run_id=recorder.run_context.run_id,
            session_id=recorder.run_context.session_id,
            step_id=step_res.step_id,
        )
        recorder.emit(
            EventType.TOOL_REQUESTED,
            step_id=step_res.step_id,
            data={"request": request.model_dump(mode="json")},
        )

        decision = self._before_tool(request)
        recorder.emit(
            EventType.POLICY_EVALUATED,
            step_id=step_res.step_id,
            data={
                "stage": PolicyStage.BEFORE_TOOL.value,
                "request": request.model_dump(mode="json"),
                "decision": decision.model_dump(mode="json"),
            },
        )

        if decision.outcome == PolicyOutcome.REQUIRE_APPROVAL:
            recorder.emit(
                EventType.APPROVAL_REQUESTED,
                step_id=step_res.step_id,
                data={
                    "request": request.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json"),
                },
            )
            approval = self._request_approval(request, decision)
            recorder.emit(
                EventType.APPROVAL_RESOLVED,
                step_id=step_res.step_id,
                data={
                    "request": request.model_dump(mode="json"),
                    "approval": approval.model_dump(mode="json"),
                },
            )
            if not approval.approved:
                decision = decision.model_copy(
                    update={
                        "outcome": PolicyOutcome.DENY,
                        "reason": approval.reason or "Approval denied",
                        "metadata": {
                            **decision.metadata,
                            "approval": approval.model_dump(mode="json"),
                        },
                    }
                )

        if decision.outcome == PolicyOutcome.DENY:
            observation = decision.reason or f"Tool '{call.name}' denied by policy"
            blocked = decision.metadata.get("execution_status") == "blocked"
            execution = ToolExecution(
                call_id=call_id,
                name=call.name,
                arguments=call.arguments,
                status=(
                    ToolExecutionStatus.BLOCKED
                    if blocked
                    else ToolExecutionStatus.DENIED
                ),
                output=observation,
                error=observation,
                metadata={"policy": decision.model_dump(mode="json")},
            )
            self._record_tool_execution(step_res, execution, legacy_action)
            step_res.status = "error"
            self.context.add_message(
                AgentMessage(
                    role="tool",
                    content=observation,
                    tool_call_id=call_id,
                )
            )
            recorder.emit(
                EventType.TOOL_BLOCKED if blocked else EventType.TOOL_DENIED,
                step_id=step_res.step_id,
                data={"execution": execution.model_dump(mode="json")},
            )
            return

        if decision.context_injection:
            self.context.add_message(
                AgentMessage(role="system", content=decision.context_injection)
            )

        # Execute tool
        recorder.emit(
            EventType.TOOL_EXECUTION_STARTED,
            step_id=step_res.step_id,
            data={"request": request.model_dump(mode="json")},
        )
        try:
            if self.executor:
                observation = str(self.executor.execute(request))
            else:
                observation = str(self.tools.call(call.name, call.arguments))

            execution = ToolExecution(
                call_id=call_id,
                name=call.name,
                arguments=call.arguments,
                status=ToolExecutionStatus.SUCCESS,
                output=observation,
            )

            post_decision = self._after_tool(request, execution)
            recorder.emit(
                EventType.POLICY_EVALUATED,
                step_id=step_res.step_id,
                data={
                    "stage": PolicyStage.AFTER_TOOL.value,
                    "request": request.model_dump(mode="json"),
                    "decision": post_decision.model_dump(mode="json"),
                },
            )
            if post_decision.context_injection:
                self.context.add_message(
                    AgentMessage(
                        role="system",
                        content=post_decision.context_injection,
                    )
                )
            if post_decision.output_suffix:
                observation += "\n" + post_decision.output_suffix
                execution.output = observation
            if post_decision.outcome != PolicyOutcome.ALLOW:
                observation = (
                    post_decision.reason
                    or f"Result from '{call.name}' blocked by post-tool policy"
                )
                execution.status = ToolExecutionStatus.BLOCKED
                execution.output = observation
                execution.error = observation
                event_type = EventType.TOOL_BLOCKED
                step_res.status = "error"
            else:
                event_type = EventType.TOOL_COMPLETED
            execution.metadata["policy"] = post_decision.model_dump(mode="json")
        except Exception as exc:
            observation = f"ToolError({call.name}): {exc}"
            step_res.status = "error"
            execution = ToolExecution(
                call_id=call_id,
                name=call.name,
                arguments=call.arguments,
                status=ToolExecutionStatus.ERROR,
                output=observation,
                error=f"{type(exc).__name__}: {exc}",
            )
            event_type = EventType.TOOL_FAILED

        self._record_tool_execution(step_res, execution, legacy_action)
        self.context.add_message(
            AgentMessage(
                role="tool",
                content=observation,
                tool_call_id=call_id,
            )
        )
        recorder.emit(
            event_type,
            step_id=step_res.step_id,
            data={"execution": execution.model_dump(mode="json")},
        )

    def _before_tool(self, request: ToolRequest) -> PolicyDecision:
        if self.policy:
            return self.policy.decide(PolicyStage.BEFORE_TOOL, request)

        # Compatibility adapter for v1 app-layer permission managers and
        # integer ToolHookRunner decisions.
        if self.permissions:
            error = self.permissions.enforce(request.name, request.arguments)
            if error:
                return PolicyDecision(
                    outcome=PolicyOutcome.DENY,
                    reason=str(error),
                    source="legacy_permissions",
                )

        if self.tool_hooks:
            legacy = self.tool_hooks.run_pre(request.name, request.arguments)
            if legacy:
                if legacy.action == 1:  # BLOCK
                    return PolicyDecision(
                        outcome=PolicyOutcome.DENY,
                        reason=(
                            legacy.message
                            or f"Tool '{request.name}' blocked by hook"
                        ),
                        source="legacy_tool_hook",
                        metadata={"execution_status": "blocked"},
                    )
                if legacy.action == 2 and legacy.message:  # INJECT
                    return PolicyDecision(
                        outcome=PolicyOutcome.ALLOW,
                        source="legacy_tool_hook",
                        context_injection=legacy.message,
                    )

        return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="engine_default")

    def _after_tool(
        self,
        request: ToolRequest,
        execution: ToolExecution,
    ) -> PolicyDecision:
        if self.policy:
            return self.policy.decide(
                PolicyStage.AFTER_TOOL,
                request,
                execution,
            )

        if self.tool_hooks and execution.output:
            legacy = self.tool_hooks.run_post(
                request.name,
                request.arguments,
                execution.output,
            )
            if legacy:
                if legacy.action == 1:  # BLOCK result exposure
                    return PolicyDecision(
                        outcome=PolicyOutcome.DENY,
                        reason=(
                            legacy.message
                            or f"Result from '{request.name}' blocked by hook"
                        ),
                        source="legacy_tool_hook",
                    )
                if legacy.action == 2 and legacy.message:  # INJECT
                    return PolicyDecision(
                        outcome=PolicyOutcome.ALLOW,
                        source="legacy_tool_hook",
                        output_suffix=legacy.message,
                    )

        return PolicyDecision(outcome=PolicyOutcome.ALLOW, source="engine_default")

    def _request_approval(
        self,
        request: ToolRequest,
        decision: PolicyDecision,
    ) -> ApprovalResult:
        if self.approval_broker is None:
            return ApprovalResult(
                status=ApprovalStatus.DENIED,
                reason="Approval required but no approval broker is configured",
            )

        result = self.approval_broker.request_approval(request, decision)
        if isinstance(result, ApprovalResult):
            return result
        if isinstance(result, bool):
            return ApprovalResult(
                status=(
                    ApprovalStatus.APPROVED
                    if result
                    else ApprovalStatus.DENIED
                ),
                reason=(
                    "Approved by broker"
                    if result
                    else "Denied by broker"
                ),
            )
        raise TypeError("Approval broker must return ApprovalResult or bool")

    @staticmethod
    def _record_tool_execution(
        step_res: StepResult,
        execution: ToolExecution,
        legacy_action: Dict[str, Any],
    ) -> None:
        step_res.actions.append(execution)
        # v1 compatibility view: the old implementation overwrote these
        # fields for each tool call, so they represented the final call.
        step_res.action = legacy_action
        step_res.observation = execution.output

    @staticmethod
    def _with_call_id(
        call: ToolCall,
        run_id: str,
        step_id: int,
        index: int,
    ) -> ToolCall:
        if call.call_id:
            return call
        run_fragment = run_id.removeprefix("run_")[:12]
        return call.model_copy(
            update={"call_id": f"call_{run_fragment}_{step_id}_{index}"}
        )

    def _save_checkpoint(
        self,
        run_context: RunContext,
        trajectory: list[StepResult],
        *,
        status: RunStatus,
        current_step: int,
        stop_reason: str = "",
        error: str = "",
    ) -> None:
        checkpoint = RunCheckpoint(
            run_id=run_context.run_id,
            session_id=run_context.session_id,
            query=run_context.query,
            current_step=current_step,
            status=status,
            trajectory=trajectory,
            stop_reason=stop_reason,
            error=error,
        )
        self.state.save_state(checkpoint.model_dump(mode="json"))
