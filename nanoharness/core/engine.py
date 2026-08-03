"""Policy-light orchestration for the NanoHarness ETCSLV kernel."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from nanoharness.core.base import (
    BaseContextManager,
    BaseEvaluator,
    BaseHookManager,
    BaseStateStore,
    BaseToolRegistry,
    EventSinkProtocol,
    HookStage,
    LLMProtocol,
)
from nanoharness.core.schema import (
    AgentMessage,
    EventType,
    HarnessEvent,
    RunCheckpoint,
    RunContext,
    RunStatus,
    StepResult,
    ToolCall,
    ToolExecution,
    ToolExecutionStatus,
)


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

    def run(
        self,
        user_query: str,
        *,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict:
        """Execute one isolated run within this engine's conversation session."""

        run_context = RunContext(
            **({"run_id": run_id} if run_id else {}),
            session_id=session_id or self.session_id,
            query=user_query,
            max_steps=self.max_steps,
        )
        recorder = _RunEventRecorder(run_context, self.event_sink)
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
                step_res = self._execute_step(i, recorder)

                self.evaluator.log_step(step_res)
                trajectory.append(step_res)

                # Mid-loop evaluation is part of the completed step so hooks,
                # state, and events all observe the same stop signal.
                stop_signal = self.evaluator.should_stop(trajectory)
                if stop_signal.should_stop:
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

                if run_status in {RunStatus.STOPPED, RunStatus.COMPLETED}:
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
                EventType.RUN_COMPLETED,
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
        recorder.emit(
            EventType.TOOL_REQUESTED,
            step_id=step_res.step_id,
            data={"call": call.model_dump(mode="json")},
        )

        # Permission gate
        if self.permissions:
            error = self.permissions.enforce(call.name, call.arguments)
            if error:
                observation = str(error)
                execution = ToolExecution(
                    call_id=call_id,
                    name=call.name,
                    arguments=call.arguments,
                    status=ToolExecutionStatus.DENIED,
                    output=observation,
                    error=observation,
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
                    EventType.TOOL_DENIED,
                    step_id=step_res.step_id,
                    data={"execution": execution.model_dump(mode="json")},
                )
                return

        # PreToolUse hook
        inject_msg = None
        if self.tool_hooks:
            decision = self.tool_hooks.run_pre(call.name, call.arguments)
            if decision:
                if decision.action == 1:  # BLOCK
                    observation = decision.message or f"Tool '{call.name}' blocked by hook"
                    execution = ToolExecution(
                        call_id=call_id,
                        name=call.name,
                        arguments=call.arguments,
                        status=ToolExecutionStatus.BLOCKED,
                        output=observation,
                        error=observation,
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
                        EventType.TOOL_BLOCKED,
                        step_id=step_res.step_id,
                        data={"execution": execution.model_dump(mode="json")},
                    )
                    return
                if decision.action == 2 and decision.message:  # INJECT
                    inject_msg = decision.message

        if inject_msg:
            self.context.add_message(
                AgentMessage(role="system", content=inject_msg)
            )

        # Execute tool
        try:
            observation = str(self.tools.call(call.name, call.arguments))

            # PostToolUse hook
            if self.tool_hooks and observation:
                decision = self.tool_hooks.run_post(
                    call.name, call.arguments, observation
                )
                if decision and decision.action == 2 and decision.message:  # INJECT
                    observation += "\n" + decision.message

            execution = ToolExecution(
                call_id=call_id,
                name=call.name,
                arguments=call.arguments,
                status=ToolExecutionStatus.SUCCESS,
                output=observation,
            )
            event_type = EventType.TOOL_COMPLETED
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
