import os

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.llm.openai_adapter import OpenAIAdapter
from nanoharness.components.memory.simple_memory import SimpleMemoryManager
from nanoharness.components.memory.tool_mixin import MemoryToolMixin
from nanoharness.components.permissions.rule_permission import RulePermissionManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.components.tools.script_tools import ScriptToolRegistry
from nanoharness.core.base import HookStage
from nanoharness.core.engine import NanoEngine
from nanoharness.core.prompt import PromptManager
from nanoharness.core.schema import AgentMessage


def build_engine(enable_mcp: bool = False) -> NanoEngine:
    """Wire up all components and return a ready-to-run NanoEngine."""

    # --- Prompts ---
    prompts = PromptManager.from_file("configs/prompts.yaml")

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
            from nanoharness.components.mcp import MCPClient, MCPToolRegistry
            mcp_client = MCPClient()
            mcp_client.connect_from_config("configs/mcp_servers.json")
            mcp_reg = MCPToolRegistry(mcp_client)
            tools.merge(mcp_reg)
            mcp_reg.close()
        except Exception as e:
            print(f"[MCP] Failed to load MCP tools: {e}")

    # --- Memory ---
    memory = SimpleMemoryManager("memory.json")
    MemoryToolMixin.register(memory, tools)

    # --- Memory hooks (inject before run, persist after run) ---
    hooks = SimpleHookManager()

    def on_task_start(user_query):
        memory.clear_working()
        related = memory.recall(user_query)
        if related:
            entries = "\n".join(
                prompts.render("tool.memory_recall.entry", key=e.key, content=e.content)
                for e in related
            )
            context.add_message(
                AgentMessage(
                    role="system",
                    content=prompts.render("memory.inject", entries=entries),
                )
            )

    def on_task_end(report):
        summary = prompts.render(
            "memory.store_summary",
            query=report.get("trajectory", [{}])[0].get("thought", "")[:50] if report.get("trajectory") else "",
            steps=report["summary"]["total_steps"],
            success=report["summary"]["success"],
        )
        memory.store(
            key=f"run:{id(report)}",
            content=summary,
            total_steps=report["summary"]["total_steps"],
            success=report["summary"]["success"],
        )

    hooks.register(HookStage.ON_TASK_START, on_task_start)
    hooks.register(HookStage.ON_TASK_END, on_task_end)

    # --- Permissions ---
    perms = RulePermissionManager()
    perms.deny("git_reset")
    perms.confirm("git_push", "git_commit", "git_revert")
    perms.confirm("file_write", "file_edit")
    perms.confirm("shell_exec")

    # --- Assemble engine ---
    context = SimpleContextManager(
        system_prompt=prompts.get("system.default")
    )

    return NanoEngine(
        llm_client=llm,
        tools=tools,
        context=context,
        state=JsonStateStore("run_state.json"),
        hooks=hooks,
        evaluator=TraceEvaluator(),
        permissions=perms,
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
