from typing import Any, Dict, Optional

from nanoharness.core.base import (
    BaseContextManager,
    BaseEvaluator,
    BaseHookManager,
    BaseStateStore,
    BaseToolRegistry,
    HookStage,
    LLMProtocol,
)
from nanoharness.core.schema import AgentMessage, StepResult, StopSignal


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
        permissions: Optional[Any] = None,  # duck-typed: needs enforce(name, args) -> str|None
        tool_hooks=None,
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

    def run(self, user_query: str) -> Dict:
        self.hooks.trigger(HookStage.ON_TASK_START, user_query)
        self.context.add_message(AgentMessage(role="user", content=user_query))

        trajectory: list = []

        for i in range(self.max_steps):
            step_res = self._execute_step(i)

            self.state.save_state({"current_step": i, "status": step_res.status})
            self.evaluator.log_step(step_res)
            trajectory.append(step_res)
            self.hooks.trigger(HookStage.ON_STEP_END, step_res)

            # Mid-loop evaluation: should we stop early?
            stop_signal = self.evaluator.should_stop(trajectory)
            if stop_signal.should_stop:
                step_res.stop_signal = stop_signal
                break

            if step_res.status == "terminated":
                break

        report = self.evaluator.get_report()
        self.hooks.trigger(HookStage.ON_TASK_END, report)
        return report

    def _execute_step(self, step_id: int) -> StepResult:
        # Think
        prompt = self.context.get_full_context()
        response = self.llm.chat(prompt, tools=self.tools.get_tool_schemas())

        self.context.add_message(
            AgentMessage(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
        )
        self.hooks.trigger(HookStage.ON_THOUGHT_READY, response)

        # Act
        step_res = StepResult(step_id=step_id, thought=response.content)

        if response.tool_calls:
            for call in response.tool_calls:
                # Permission gate
                if self.permissions:
                    error = self.permissions.enforce(call.name, call.arguments)
                    if error:
                        step_res.status = "error"
                        step_res.observation = error
                        self.context.add_message(
                            AgentMessage(role="tool", content=error)
                        )
                        continue

                # PreToolUse hook
                inject_msg = None
                if self.tool_hooks:
                    decision = self.tool_hooks.run_pre(call.name, call.arguments)
                    if decision:
                        if decision.action == 1:  # BLOCK
                            obs = decision.message or f"Tool '{call.name}' blocked by hook"
                            step_res.action = call.model_dump()
                            step_res.observation = obs
                            self.context.add_message(
                                AgentMessage(role="tool", content=obs)
                            )
                            continue
                        if decision.action == 2 and decision.message:  # INJECT
                            inject_msg = decision.message

                # Inject pre-tool message if hook requested
                if inject_msg:
                    self.context.add_message(
                        AgentMessage(role="system", content=inject_msg)
                    )

                # Execute tool
                try:
                    obs = self.tools.call(call.name, call.arguments)
                    step_res.action = call.model_dump()
                    step_res.observation = str(obs)
                except Exception as exc:
                    step_res.status = "error"
                    step_res.observation = f"ToolError({call.name}): {exc}"

                # PostToolUse hook
                if self.tool_hooks and step_res.observation:
                    decision = self.tool_hooks.run_post(
                        call.name, call.arguments, step_res.observation
                    )
                    if decision and decision.action == 2 and decision.message:  # INJECT
                        step_res.observation += "\n" + decision.message

                self.context.add_message(
                    AgentMessage(role="tool", content=step_res.observation)
                )
        else:
            step_res.status = "terminated"

        return step_res
