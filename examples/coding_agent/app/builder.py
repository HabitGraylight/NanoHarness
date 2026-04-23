"""Coding agent engine builder.

Wires the nanoharness kernel with coding-agent-specific configuration.
The kernel is policy-free — all behavior lives in this app layer.
"""

import os

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from app.adapters import OpenAIAdapter
from app.handlers import register_memory_tools
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.core.base import HookStage
from nanoharness.core.engine import NanoEngine
from nanoharness.core.prompt import PromptManager
from nanoharness.core.schema import AgentMessage

from app.context import ManagedContext
from app.hooks import build_hooks, build_tool_hooks
from app.memory import FileMemoryManager
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

    Runtime files (state) are stored in sandbox/.
    Memory files are stored in .memory/ (persisted across sessions).
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

    # --- Memory (file-based: .memory/ directory) ---
    memory_dir = os.path.join(workspace_root, ".memory")
    memory = FileMemoryManager(memory_dir)
    register_memory_tools(registry=tools, memory=memory)

    # --- Context (three-layer managed: spill → compress → summarize) ---
    system_prompt = prompts.get("system.coding_agent")
    scratch_dir = os.path.join(SANDBOX, "scratch")
    context = ManagedContext(
        inner=SimpleContextManager(system_prompt=system_prompt),
        scratch_dir=scratch_dir,
        llm_client=llm,
    )

    # --- Subagent (needs llm + context for fork support) ---
    register_task_tool(registry=tools, llm_client=llm, parent_context=context)

    # --- Skills ---
    skills_dir = os.path.join(workspace_root, "skills")
    skill_reg = SkillRegistry(skills_dir)
    register_skill_tool(registry=tools, skill_registry=skill_reg)

    # --- Hooks ---
    hooks = build_hooks()
    tool_hooks = build_tool_hooks()

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
        tool_hooks=tool_hooks,
        max_steps=max_steps,
    )


def _wire_memory_hooks(hooks, memory, prompts, context):
    """Register hooks that inject memory index at session start."""
    def on_task_start(query):
        index = memory.load_for_injection()
        if index:
            context.add_message(
                AgentMessage(
                    role="system",
                    content=prompts.render("memory.inject", index=index),
                )
            )

    hooks.register(HookStage.ON_TASK_START, on_task_start)
