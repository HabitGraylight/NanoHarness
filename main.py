import os

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.llm.openai_adapter import OpenAIAdapter
from nanoharness.components.memory.simple_memory import MemoryToolMixin
from nanoharness.components.memory.simple_memory import SimpleMemoryManager
from nanoharness.components.permissions.rule_permission import RulePermissionManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.components.tools.script_tools import ScriptToolRegistry
from nanoharness.core.engine import NanoEngine  # noqa: F401


def build_engine(enable_mcp: bool = False) -> NanoEngine:
    """Wire up all components and return a ready-to-run NanoEngine."""

    # --- LLM (DeepSeek via OpenAI-compatible API) ---
    # Swap adapter for other providers:
    #   from nanoharness.components.llm.anthropic_adapter import AnthropicAdapter
    #   from nanoharness.components.llm.litellm_adapter import LiteLLMAdapter
    #   from nanoharness.components.llm.vllm_adapter import VLLMAdapter
    llm = OpenAIAdapter(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
    )

    # --- Tools (shell scripts from configs/scripts/) ---
    tools = ScriptToolRegistry("configs/scripts")

    # --- Optionally merge MCP tools ---
    # Set NANOHARNESS_MCP=1 or pass enable_mcp=True to load tools
    # from MCP servers defined in configs/mcp_servers.json
    if enable_mcp or os.environ.get("NANOHARNESS_MCP", "").lower() in ("1", "true"):
        try:
            from nanoharness.components.mcp import MCPToolRegistry
            mcp_reg = MCPToolRegistry(config_path="configs/mcp_servers.json")
            tools.merge(mcp_reg)
            mcp_reg.close()
        except Exception as e:
            print(f"[MCP] Failed to load MCP tools: {e}")

    # --- Memory ---
    memory = SimpleMemoryManager("memory.json")
    MemoryToolMixin.register(memory, tools)

    # --- Permissions ---
    perms = RulePermissionManager()
    perms.deny("git_reset")
    perms.confirm("git_push", "git_commit", "git_revert")
    perms.confirm("file_write", "file_edit")
    perms.confirm("shell_exec")

    # --- Assemble engine ---
    return NanoEngine(
        llm_client=llm,
        tools=tools,
        context=SimpleContextManager(
            system_prompt="You are a helpful assistant. Use tools when appropriate."
        ),
        state=JsonStateStore("run_state.json"),
        hooks=SimpleHookManager(),
        evaluator=TraceEvaluator(),
        permissions=perms,
        memory=memory,
    )


if __name__ == "__main__":
    engine = build_engine()

    query = input(">>> ")
    report = engine.run(query)

    print("\n===== Report =====")
    print(f"Success: {report['summary']['success']}")
    print(f"Steps:   {report['summary']['total_steps']}")
    for i, step in enumerate(report["trajectory"]):
        print(f"\n--- Step {i} [{step['status']}] ---")
        thought = step["thought"][:120] + ("..." if len(step["thought"]) > 120 else "")
        print(f"  Thought: {thought}")
        if step["observation"]:
            print(f"  Observation: {step['observation'][:200]}")
