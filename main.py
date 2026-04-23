"""Minimal usage example: wire up the ETCSLV kernel and run one query.

This file demonstrates how to assemble the NanoEngine with only core
components. Memory, permissions, MCP, and LLM adapters are application-layer
concerns — see examples/coding_agent/ for a full-featured implementation.
"""

import os

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.components.evaluator.trace_evaluator import TraceEvaluator
from nanoharness.components.hooks.simple_hooks import SimpleHookManager
from nanoharness.components.state.json_store import JsonStateStore
from nanoharness.components.tools.script_tools import ScriptToolRegistry
from nanoharness.core.engine import NanoEngine
from nanoharness.core.prompt import PromptManager


def build_engine() -> NanoEngine:
    """Wire up core ETCSLV components and return a ready-to-run NanoEngine.

    LLM adapter must be provided by the application.
    This example requires DEEPSEEK_API_KEY and a compatible OpenAI client.
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("Install openai: pip install openai")

    # --- Prompts ---
    prompts = PromptManager.from_file("configs/prompts.yaml")

    # --- LLM (application-provided adapter satisfying LLMProtocol) ---
    llm = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
    )

    # Wrap OpenAI client to satisfy LLMProtocol
    from nanoharness.core.schema import LLMResponse, ToolCall

    class DeepSeekAdapter:
        def __init__(self, client, model="deepseek-chat"):
            self._client = client
            self._model = model

        def chat(self, messages, tools=None):
            kwargs = {"model": self._model, "messages": messages}
            if tools:
                kwargs["tools"] = tools
            resp = self._client.chat.completions.create(**kwargs)
            choice = resp.choices[0]
            tool_calls = None
            if choice.message.tool_calls:
                tool_calls = [
                    ToolCall(
                        name=tc.function.name,
                        arguments=__import__("json").loads(tc.function.arguments),
                    )
                    for tc in choice.message.tool_calls
                ]
            return LLMResponse(content=choice.message.content or "", tool_calls=tool_calls)

    adapter = DeepSeekAdapter(llm)

    # --- Tools ---
    tools = ScriptToolRegistry("configs/scripts")

    # --- Assemble engine (ETCSLV only) ---
    context = SimpleContextManager(
        system_prompt=prompts.get("system.default")
    )

    return NanoEngine(
        llm_client=adapter,
        tools=tools,
        context=context,
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
        thought = step["thought"][:120] + ("..." if len(step["thought"]) > 120 else "")
        print(f"  Thought: {thought}")
        if step["observation"]:
            print(f"  Observation: {step['observation'][:200]}")
