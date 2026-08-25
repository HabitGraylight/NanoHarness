"""OrcaRouter implementation of the public LLM protocol."""

import os
from typing import Any, Mapping, Optional, Sequence

from nanoharness.components.llm.openai_chat import OpenAIChatProvider


ORCAROUTER_BASE_URL = "https://api.orcarouter.ai/v1"
ORCAROUTER_API_KEY_ENV = "ORCAROUTER_API_KEY"


class OrcaRouterProvider(OpenAIChatProvider):
    """OpenAI-compatible OrcaRouter adapter with explicit routing options.

    ``fallback_models`` contains only fallback entries; the primary ``model``
    is prepended to the ordered chain sent to OrcaRouter.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: Optional[str] = None,
        base_url: str = ORCAROUTER_BASE_URL,
        fallback_models: Optional[Sequence[str]] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
        extra_headers: Optional[Mapping[str, str]] = None,
        client: Any = None,
    ):
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url is required")

        body = dict(extra_body or {})
        fallbacks = _validate_fallback_models(model, fallback_models)
        if fallbacks:
            conflicts = {"models", "route"} & body.keys()
            if conflicts:
                names = ", ".join(sorted(conflicts))
                raise ValueError(
                    f"fallback_models cannot be combined with extra_body {names}"
                )
            body.update({
                "models": [model, *fallbacks],
                "route": "fallback",
            })

        request_options = {}
        if body:
            request_options["extra_body"] = body
        if extra_headers:
            request_options["extra_headers"] = dict(extra_headers)

        resolved_api_key = api_key or os.environ.get(ORCAROUTER_API_KEY_ENV)
        if client is None and not resolved_api_key:
            raise ValueError(
                "api_key is required; pass it explicitly or set "
                f"{ORCAROUTER_API_KEY_ENV}"
            )
        super().__init__(
            model,
            api_key=resolved_api_key,
            base_url=base_url,
            request_options=request_options,
            client=client,
        )


def _validate_fallback_models(
    primary_model: str,
    fallback_models: Optional[Sequence[str]],
) -> list[str]:
    if fallback_models is None:
        return []
    if isinstance(fallback_models, (str, bytes)):
        raise TypeError("fallback_models must be a sequence of model names")
    models = list(fallback_models)
    if len(models) > 4:
        raise ValueError("OrcaRouter supports at most 5 models including the primary")
    for model in models:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("fallback model names must be non-empty strings")
    chain = [primary_model, *models]
    if len(set(chain)) != len(chain):
        raise ValueError("fallback model chain cannot contain duplicates")
    return models
