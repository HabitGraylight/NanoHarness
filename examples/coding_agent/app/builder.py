"""Coding agent engine builder.

Wires the nanoharness kernel with coding-agent-specific configuration.
The kernel is policy-free — all behavior lives in this app layer.
"""

import os

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.llm.openai_adapter import OpenAIAdapter
from nanoharness.components.memory.simple_memory import SimpleMemoryManager
from nanoharness.components.memory.tool_mixin import MemoryToolMixin
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.core.base import HookStage
from nanoharness.core.engine import NanoEngine
from nanoharness.core.prompt import PromptManager
from nanoharness.core.schema import AgentMessage

from app.hooks import build_hooks
from app.permissions import build_permissions
from app.tools import build_tools

# Runtime artifacts go here
SANDBOX = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sandbox")


def build_coding_engine(
    api_key: str | None = None,
    model: str = "deepseek-chat",
    base_url: str = "https://api.deepseek.com",
    max_steps: int = 20,
) -> NanoEngine:
    """Build and return a configured NanoEngine for coding tasks.

    Runtime files (memory, state) are stored in sandbox/.
    """
    os.makedirs(SANDBOX, exist_ok=True)

    api_key = api_key or os.environ["DEEPSEEK_API_KEY"]

    # --- LLM ---
    llm = OpenAIAdapter(api_key=api_key, model=model, base_url=base_url)

    # --- Prompts ---
    prompts = PromptManager.from_file(
        os.path.join(os.path.dirname(__file__), "prompts.yaml")
    )

    # --- Tools ---
    scripts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "configs", "scripts")
    tools = build_tools(scripts_dir=scripts_dir)

    # --- Memory ---
    memory = SimpleMemoryManager(persist_path=os.path.join(SANDBOX, "memory.json"))
    MemoryToolMixin.register(memory, tools)

    # --- Hooks ---
    hooks = build_hooks()

    # --- Permissions ---
    perms = build_permissions()

    # --- Context ---
    system_prompt = prompts.get("system.coding_agent")
    context = SimpleContextManager(system_prompt=system_prompt)

    # --- Wire memory lifecycle hooks ---
    _wire_memory_hooks(hooks, memory, prompts, context)

    return NanoEngine(
        llm_client=llm,
        tools=tools,
        context=context,
        state=JsonStateStore(os.path.join(SANDBOX, "run_state.json")),
        hooks=hooks,
        evaluator=TraceEvaluator(),
        permissions=perms,
        max_steps=max_steps,
    )


def _wire_memory_hooks(hooks, memory, prompts, context):
    """Register hooks that inject relevant memories before each run
    and persist a summary after the run completes."""
    def on_task_start(query):
        memory.clear_working()
        related = memory.recall(query)
        if related:
            entries = "\n".join(f"[{e.key}] {e.content}" for e in related)
            context.add_message(
                AgentMessage(
                    role="system",
                    content=prompts.render("memory.inject", entries=entries),
                )
            )

    def on_task_end(report):
        summary = prompts.render(
            "memory.store_summary",
            query="",
            steps=report["summary"]["total_steps"],
            success=report["summary"]["success"],
        )
        memory.store(
            key=f"run:{report['summary']['total_steps']}",
            content=summary,
            total_steps=report["summary"]["total_steps"],
            success=report["summary"]["success"],
        )

    hooks.register(HookStage.ON_TASK_START, on_task_start)
    hooks.register(HookStage.ON_TASK_END, on_task_end)
