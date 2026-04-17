from typing import Dict

from nanoharness.core.base import (
    BaseContextManager,
    BaseEvaluator,
    BaseHookManager,
    BaseStateStore,
    BaseToolRegistry,
    HookStage,
    LLMProtocol,
)
from nanoharness.core.schema import AgentMessage, StepResult


class NanoEngine:
    def __init__(
        self,
        llm_client: LLMProtocol,
        tools: BaseToolRegistry,
        context: BaseContextManager,
        state: BaseStateStore,
        hooks: BaseHookManager,
        evaluator: BaseEvaluator,
        max_steps: int = 5,
    ):
        self.llm = llm_client
        self.tools = tools
        self.context = context
        self.state = state
        self.hooks = hooks
        self.evaluator = evaluator
        self.max_steps = max_steps

    def run(self, user_query: str) -> Dict:
        self.hooks.trigger(HookStage.ON_TASK_START, user_query)
        self.context.add_message(AgentMessage(role="user", content=user_query))

        for i in range(self.max_steps):
            step_res = self._execute_step(i)

            self.state.save_state({"current_step": i, "status": step_res.status})
            self.evaluator.log_step(step_res)
            self.hooks.trigger(HookStage.ON_STEP_END, step_res)

            if step_res.status == "terminated":
                break

        self.hooks.trigger(HookStage.ON_TASK_END, self.evaluator.get_report())
        return self.evaluator.get_report()

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
                try:
                    obs = self.tools.call(call.name, call.arguments)
                    step_res.action = call.model_dump()
                    step_res.observation = str(obs)
                except Exception as exc:
                    step_res.status = "error"
                    step_res.observation = f"ToolError({call.name}): {exc}"
                self.context.add_message(
                    AgentMessage(role="tool", content=step_res.observation)
                )
        else:
            step_res.status = "terminated"

        return step_res
