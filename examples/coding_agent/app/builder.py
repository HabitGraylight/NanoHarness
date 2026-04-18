"""Coding agent engine builder.

Wires together the vendored nanoharness components with coding-agent-specific
configuration: prompts, tools, permissions, hooks, and memory.
"""

import os

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.llm.openai_adapter import OpenAIAdapter
from nanoharness.components.memory.simple_memory import MemoryToolMixin
from nanoharness.components.memory.simple_memory import SimpleMemoryManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.core.engine import NanoEngine
from nanoharness.core.prompt import PromptManager

from app.hooks import build_hooks
from app.permissions import build_permissions
from app.tools import build_tools


def build_coding_engine(
    api_key: str | None = None,
    model: str = "deepseek-chat",
    base_url: str = "https://api.deepseek.com",
    max_steps: int = 20,
    memory_path: str = "coding_memory.json",
    state_path: str = "coding_run_state.json",
) -> NanoEngine:
    """Build and return a configured NanoEngine for coding tasks.

    Args:
        api_key: LLM API key. Falls back to DEEPSEEK_API_KEY env var.
        model: Model name for the LLM adapter.
        base_url: API endpoint base URL.
        max_steps: Maximum agent loop iterations (coding tasks need more).
        memory_path: Path for persistent memory JSON.
        state_path: Path for run state JSON.
    """
    api_key = api_key or os.environ["DEEPSEEK_API_KEY"]

    # --- LLM ---
    llm = OpenAIAdapter(api_key=api_key, model=model, base_url=base_url)

    # --- Prompts (coding-agent-specific) ---
    prompts = PromptManager.from_file("app/prompts.yaml")

    # --- Tools ---
    tools = build_tools(scripts_dir="configs/scripts")

    # --- Memory ---
    memory = SimpleMemoryManager(persist_path=memory_path, prompts=prompts)
    MemoryToolMixin.register(memory, tools, prompts=prompts)

    # --- Permissions ---
    perms = build_permissions()

    # --- Hooks ---
    hooks = build_hooks()

    # --- System prompt ---
    system_prompt = prompts.get("system.coding_agent")

    # --- Assemble ---
    return NanoEngine(
        llm_client=llm,
        tools=tools,
        context=SimpleContextManager(system_prompt=system_prompt),
        state=JsonStateStore(state_path),
        hooks=hooks,
        evaluator=TraceEvaluator(),
        permissions=perms,
        memory=memory,
        prompts=prompts,
        max_steps=max_steps,
    )
