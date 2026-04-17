from typing import Optional

from nanoharness.components.llm.openai_adapter import OpenAIAdapter


class VLLMAdapter(OpenAIAdapter):
    """Adapter for vLLM / local OpenAI-compatible servers.

    vLLM exposes an OpenAI-compatible API, so this is a thin wrapper
    over OpenAIAdapter with sensible defaults for local inference.
    """

    def __init__(
        self,
        model: str = "default",
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
    ):
        super().__init__(api_key=api_key, model=model, base_url=base_url)
