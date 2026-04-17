from typing import Dict, Optional

from nanoharness.core.base import (
    BaseContextManager,
    BaseEvaluator,
    BaseHookManager,
    BaseMemoryManager,
    BasePermissionManager,
    BaseStateStore,
    BaseToolRegistry,
    HookStage,
    LLMProtocol,
)
from nanoharness.core.prompt import PromptManager
from nanoharness.core.schema import AgentMessage, PermissionLevel, StepResult


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
        permissions: Optional[BasePermissionManager] = None,
        memory: Optional[BaseMemoryManager] = None,
        prompts: Optional[PromptManager] = None,
    ):
        self.llm = llm_client
        self.tools = tools
        self.context = context
        self.state = state
        self.hooks = hooks
        self.evaluator = evaluator
        self.max_steps = max_steps
        self.permissions = permissions
        self.memory = memory
        self.prompts = prompts or PromptManager()

    def run(self, user_query: str) -> Dict:
        # Inject relevant memories into context
        if self.memory:
            self.memory.clear_working()
            related = self.memory.recall(user_query)
            if related:
                entries = "\n".join(
                    self.prompts.render("tool.memory_recall.entry", key=e.key, content=e.content)
                    for e in related
                )
                self.context.add_message(
                    AgentMessage(
                        role="system",
                        content=self.prompts.render("memory.inject", entries=entries),
                    )
                )

        self.hooks.trigger(HookStage.ON_TASK_START, user_query)
        self.context.add_message(AgentMessage(role="user", content=user_query))

        for i in range(self.max_steps):
            step_res = self._execute_step(i)

            self.state.save_state({"current_step": i, "status": step_res.status})
            self.evaluator.log_step(step_res)
            self.hooks.trigger(HookStage.ON_STEP_END, step_res)

            if step_res.status == "terminated":
                break

        report = self.evaluator.get_report()
        self.hooks.trigger(HookStage.ON_TASK_END, report)

        # Persist run summary to memory
        if self.memory:
            summary = self.prompts.render(
                "memory.store_summary",
                query=user_query,
                steps=report["summary"]["total_steps"],
                success=report["summary"]["success"],
            )
            self.memory.store(
                key=f"run:{user_query[:50]}",
                content=summary,
                total_steps=report["summary"]["total_steps"],
                success=report["summary"]["success"],
            )

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
                # Permission check
                if self.permissions:
                    level = self.permissions.check(call.name, call.arguments)
                    if level == PermissionLevel.DENY:
                        step_res.status = "error"
                        step_res.observation = self.prompts.render(
                            "permission.denied", tool_name=call.name
                        )
                        self.context.add_message(
                            AgentMessage(role="tool", content=step_res.observation)
                        )
                        continue
                    if level == PermissionLevel.CONFIRM:
                        if not self.permissions.approve(call.name, call.arguments):
                            step_res.status = "error"
                            step_res.observation = self.prompts.render(
                                "permission.not_approved", tool_name=call.name
                            )
                            self.context.add_message(
                                AgentMessage(role="tool", content=step_res.observation)
                            )
                            continue

                try:
                    obs = self.tools.call(call.name, call.arguments)
                    step_res.action = call.model_dump()
                    step_res.observation = str(obs)
                except Exception as exc:
                    step_res.status = "error"
                    step_res.observation = self.prompts.render(
                        "tool.error", tool_name=call.name, error=exc
                    )
                self.context.add_message(
                    AgentMessage(role="tool", content=step_res.observation)
                )
        else:
            step_res.status = "terminated"

        return step_res
