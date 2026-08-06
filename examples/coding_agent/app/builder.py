"""Coding agent engine builder.

Wires the nanoharness kernel with coding-agent-specific configuration.
The kernel is policy-free — all behavior lives in this app layer.
"""

import os

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.lifecycle import CompositeToolPolicy
from app.adapters import OpenAIAdapter
from app.coding_evaluator import CodingAgentEvaluator
from app.resilient_llm import ResilientLLM
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.core.base import HookStage
from nanoharness.core.engine import NanoEngine
from nanoharness.core.prompt import PromptManager
from nanoharness.core.schema import AgentMessage
from nanoharness.extensions import ExtensionContext, ExtensionManager
from nanoharness.extensions.background import BackgroundExtension
from nanoharness.extensions.memory import MemoryExtension
from nanoharness.extensions.mcp import MCPExtension
from nanoharness.extensions.scheduler import SchedulerExtension
from nanoharness.extensions.skills import SkillsExtension
from nanoharness.extensions.subagents import SubagentExtension
from nanoharness.extensions.teams import TeamExtension

from app.context import ManagedContext
from app.hooks import build_hooks, build_tool_hooks
from app.permissions import TerminalApprovalBroker, build_permissions
from app.prompt_builder import SystemPromptBuilder
from nanoharness.extensions.tasks import TaskBoard, TaskExtension
from app.tools import build_tools
from nanoharness.extensions.worktrees import WorktreeExtension

# Runtime artifacts go here
SANDBOX = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sandbox")
MCP_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "configs",
    "mcp_servers.json",
)


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
    extension_context = ExtensionContext(
        tools=tools,
        services={"llm.raw": raw_llm},
        capabilities={"runtime.llm"},
        metadata={"workspace_root": workspace_root},
    )
    extension_manager = ExtensionManager(extension_context)
    extension_manager.install(
        MemoryExtension(),
        {"directory": memory_dir},
    )
    memory = extension_context.services["memory"]

    # --- Task board ---
    extension_manager.install(
        TaskExtension(),
        {"persist_path": os.path.join(SANDBOX, "tasks.json")},
    )
    task_board = extension_context.services["tasks"]

    # --- Worktree (task isolation lanes) ---
    extension_manager.install(
        WorktreeExtension(),
        {"workspace_root": workspace_root},
    )

    # --- MCP tools (external tool servers) ---
    extension_manager.install(
        MCPExtension(),
        {"config_path": MCP_CONFIG},
    )

    # --- Skills ---
    skills_dir = os.path.join(workspace_root, "skills")
    extension_manager.install(
        SkillsExtension(),
        {"directory": skills_dir},
    )
    skill_reg = extension_context.services["skills"]

    # --- Long-lived notification services ---
    scratch_dir = os.path.join(SANDBOX, "scratch")
    extension_manager.install(
        BackgroundExtension(),
        {
            "workspace_root": workspace_root,
            "scratch_dir": scratch_dir,
        },
    )
    extension_manager.install(
        SchedulerExtension(),
        {"persist_path": os.path.join(SANDBOX, "schedules.json")},
    )
    bg_executor = extension_context.services["background"]
    scheduler = extension_context.services["scheduler"]

    # --- Team (long-lived delegation + notification source) ---
    extension_manager.install(TeamExtension())
    teammate_manager = extension_context.services["team"]

    # --- System prompt (five segments) ---
    prompt_builder = SystemPromptBuilder(
        prompts=prompts,
        skill_registry=skill_reg,
        memory=memory,
        workspace_root=workspace_root,
    )
    system_prompt = prompt_builder.build()

    # --- Context (three-layer managed: spill → compress → summarize) ---
    context = ManagedContext(
        inner=SimpleContextManager(system_prompt=system_prompt),
        scratch_dir=scratch_dir,
        llm_client=raw_llm,
        notification_sources=[bg_executor, scheduler, teammate_manager],
    )

    # --- Wrap LLM with error recovery ---
    def compress_context():
        context.compress_old()
        context.summarize_if_needed()
        return context.get_full_context()

    llm = ResilientLLM(raw_llm, context_compressor=compress_context)

    # --- Subagent (one-shot delegation; host bindings are explicit) ---
    extension_context.provide_service("llm.agent", llm)
    extension_context.provide_service("context.agent", context)
    extension_context.capabilities.update({"runtime.agent_llm", "runtime.context"})
    extension_manager.install(SubagentExtension())

    # --- Hooks ---
    hooks = build_hooks()
    tool_hooks = build_tool_hooks()

    # --- Permissions ---
    perms = build_permissions()

    # --- Wire system prompt refresh (rebuilds dynamic segments per task) ---
    _wire_system_prompt_refresh(hooks, prompt_builder, context)

    # --- Wire task board awareness (compact summary on each task start) ---
    _wire_task_awareness(hooks, task_board, context)

    engine = NanoEngine(
        llm_client=llm,
        tools=tools,
        context=context,
        state=JsonStateStore(os.path.join(SANDBOX, "run_state.json")),
        hooks=hooks,
        evaluator=CodingAgentEvaluator(llm_client=raw_llm),
        policy=CompositeToolPolicy([perms, tool_hooks]),
        approval_broker=TerminalApprovalBroker(),
        max_steps=max_steps,
    )
    # Application-level white-box inventory; the kernel does not depend on
    # the extension system, but callers can inspect what this profile loaded.
    engine.extension_manager = extension_manager
    return engine


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


def _wire_task_awareness(hooks, task_board: TaskBoard, context):
    """Inject a compact task board summary before each user query.

    Only adds a message when there are active tasks.
    Keeps it to a single line — no task dump, just signal.
    Format: [Task Board] ready: #3 Write tests | in_progress: #1 Design
    """
    def on_task_start(query):
        active = [t for t in task_board.list() if t["status"] != "deleted"]
        if not active:
            return

        parts = []
        ready = task_board.ready()
        if ready:
            parts.append("ready: " + ", ".join(f"#{t['id']} {t['subject']}" for t in ready))

        in_progress = [t for t in active if t["status"] == "in_progress"]
        if in_progress:
            parts.append("in_progress: " + ", ".join(
                f"#{t['id']} {t['subject']}" + (f" ({t['owner']})" if t["owner"] else "")
                for t in in_progress
            ))

        pending_blocked = [t for t in active if t["status"] == "pending" and t["blockedBy"]]
        if pending_blocked:
            parts.append("blocked: " + ", ".join(
                f"#{t['id']} {t['subject']} (waiting on {t['blockedBy']})"
                for t in pending_blocked
            ))

        if not parts:
            return

        summary = "[Task Board] " + " | ".join(parts)
        # Insert right before the user query (which hasn't been added yet)
        context._messages.append(AgentMessage(role="system", content=summary))

    hooks.register(HookStage.ON_TASK_START, on_task_start)
