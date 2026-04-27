"""Coding agent engine builder.

Wires the nanoharness kernel with coding-agent-specific configuration.
The kernel is policy-free — all behavior lives in this app layer.
"""

import os

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from app.adapters import OpenAIAdapter
from app.handlers import register_memory_tools
from app.resilient_llm import ResilientLLM
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.core.base import HookStage
from nanoharness.core.engine import NanoEngine
from nanoharness.core.prompt import PromptManager
from nanoharness.core.schema import AgentMessage

from app.context import ManagedContext
from app.hooks import build_hooks, build_tool_hooks
from app.memory import FileMemoryManager
from app.permissions import build_permissions
from app.prompt_builder import SystemPromptBuilder
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
    raw_llm = OpenAIAdapter(api_key=api_key, model=model, base_url=base_url)

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

    # --- Skills ---
    skills_dir = os.path.join(workspace_root, "skills")
    skill_reg = SkillRegistry(skills_dir)
    register_skill_tool(registry=tools, skill_registry=skill_reg)

    # --- System prompt (five segments) ---
    prompt_builder = SystemPromptBuilder(
        prompts=prompts,
        skill_registry=skill_reg,
        memory=memory,
        workspace_root=workspace_root,
    )
    system_prompt = prompt_builder.build()

    # --- Context (three-layer managed: spill → compress → summarize) ---
    scratch_dir = os.path.join(SANDBOX, "scratch")
    context = ManagedContext(
        inner=SimpleContextManager(system_prompt=system_prompt),
        scratch_dir=scratch_dir,
        llm_client=raw_llm,
    )

    # --- Wrap LLM with error recovery ---
    def compress_context():
        context.compress_old()
        context.summarize_if_needed()
        return context.get_full_context()

    llm = ResilientLLM(raw_llm, context_compressor=compress_context)

    # --- Subagent (needs llm + context for fork support) ---
    register_task_tool(registry=tools, llm_client=llm, parent_context=context)

    # --- Hooks ---
    hooks = build_hooks()
    tool_hooks = build_tool_hooks()

    # --- Permissions ---
    perms = build_permissions()

    # --- Wire system prompt refresh (rebuilds dynamic segments per task) ---
    _wire_system_prompt_refresh(hooks, prompt_builder, context)

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


def _wire_system_prompt_refresh(hooks, prompt_builder, context):
    """Register hook that refreshes the system prompt at each task start.

    Replaces the first system message with a freshly built prompt,
    ensuring memory, environment, and NanoCA.md are current.
    """
    def on_task_start(query):
        refreshed = prompt_builder.build()
        if context._messages and context._messages[0].role == "system":
            context._messages[0] = AgentMessage(role="system", content=refreshed)
        else:
            context._messages.insert(0, AgentMessage(role="system", content=refreshed))

    hooks.register(HookStage.ON_TASK_START, on_task_start)
