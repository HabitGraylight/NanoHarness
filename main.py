import os

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.evaluation import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.llm.openai_adapter import OpenAIAdapter
from nanoharness.components.storage.json_store import JsonStateStore
from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.core.engine import NanoEngine


def build_engine() -> NanoEngine:
    """Wire up all components and return a ready-to-run NanoEngine."""

    # --- LLM (DeepSeek via OpenAI-compatible API) ---
    # Swap adapter here for other providers:
    #   from nanoharness.components.llm.anthropic_adapter import AnthropicAdapter
    #   from nanoharness.components.llm.litellm_adapter import LiteLLMAdapter
    #   from nanoharness.components.llm.vllm_adapter import VLLMAdapter
    llm = OpenAIAdapter(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
    )

    # --- Tools ---
    tools = DictToolRegistry()

    @tools.tool
    def echo(text: str):
        """Echo back the input text."""
        return text

    @tools.tool
    def add(a: int, b: int):
        """Add two numbers together."""
        return a + b

    # --- Assemble engine ---
    return NanoEngine(
        llm_client=llm,
        tools=tools,
        context=SimpleContextManager(system_prompt="You are a helpful assistant. Use tools when appropriate."),
        state=JsonStateStore("run_state.json"),
        hooks=SimpleHookManager(),
        evaluator=TraceEvaluator(),
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
        print(f"  Thought: {step['thought'][:100]}...")
        if step["observation"]:
            print(f"  Observation: {step['observation']}")
