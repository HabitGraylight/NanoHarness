"""Coding agent engine builder.

Wires the nanoharness kernel with coding-agent-specific configuration.
The kernel is policy-free — all behavior lives in this app layer.
"""

import os

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.llm.openai_adapter import OpenAIAdapter
from nanoharness.components.memory.simple_memory import SimpleMemoryManager
from app.handlers import register_memory_tools
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.core.base import HookStage
from nanoharness.core.engine import NanoEngine
from nanoharness.core.prompt import PromptManager
from nanoharness.core.schema import AgentMessage

from app.hooks import build_hooks
from app.permissions import build_permissions
from app.skills import SkillRegistry, register_skill_tool
from app.subagent import register_task_tool
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
    workspace_root = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    scripts_dir = os.path.join(workspace_root, "configs", "scripts")
    tools = build_tools(scripts_dir=scripts_dir, workspace_root=workspace_root)

    # --- Memory ---
    memory = SimpleMemoryManager(persist_path=os.path.join(SANDBOX, "memory.json"))
    register_memory_tools(registry=tools, memory=memory)

    # --- Context (created before subagent so fork can reference it) ---
    system_prompt = prompts.get("system.coding_agent")
    context = SimpleContextManager(system_prompt=system_prompt)

    # --- Subagent (needs llm + context for fork support) ---
    register_task_tool(registry=tools, llm_client=llm, parent_context=context)

    # --- Skills ---
    skills_dir = os.path.join(workspace_root, "skills")
    skill_reg = SkillRegistry(skills_dir)
    register_skill_tool(registry=tools, skill_registry=skill_reg)

    # --- Hooks ---
    hooks = build_hooks()

    # --- Permissions ---
    perms = build_permissions()

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
