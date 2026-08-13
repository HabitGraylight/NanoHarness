"""Read-only workspace and explicit response-transition tools."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from nanoharness.components import DictToolRegistry

from app.models import GatewayTurnState, TurnPhase
from app.store import GatewayTurnStore


class GatewayToolRuntime:
    def __init__(
        self,
        state: GatewayTurnState,
        store: GatewayTurnStore,
        workspace: Path,
    ):
        self.state = state
        self.store = store
        self.workspace = workspace.resolve()

    def resolve_read_path(self, raw_path: str) -> Path:
        logical = PurePosixPath(raw_path.replace("\\", "/"))
        if logical.is_absolute() or ".." in logical.parts or not logical.parts:
            raise ValueError("path must stay inside the NanoOpenClaw workspace")
        if logical.parts[0] in {".git", ".nano_openclaw"}:
            raise ValueError("internal runtime paths are not readable")
        target = (self.workspace / logical).resolve()
        if target != self.workspace and self.workspace not in target.parents:
            raise ValueError("resolved path escapes the NanoOpenClaw workspace")
        if not target.is_file():
            raise ValueError(f"workspace file not found: {logical.as_posix()}")
        return target


def register_gateway_tools(
    tools: DictToolRegistry,
    runtime: GatewayToolRuntime,
) -> list[str]:
    @tools.tool
    def workspace_read(path: str) -> str:
        """Read one UTF-8 file inside the isolated workspace."""

        return runtime.resolve_read_path(path).read_text(encoding="utf-8")

    @tools.tool
    def response_submit(answer: str) -> str:
        """Submit the final channel response for independent host review."""

        normalized = answer.strip()
        if not normalized:
            raise ValueError("response cannot be blank")
        if len(normalized) > 100_000:
            raise ValueError("response exceeds the channel content limit")
        state = runtime.state
        if state.response:
            if state.response != normalized:
                raise ValueError("a different response was already submitted")
            return "response already submitted"
        if state.phase != TurnPhase.RESPOND:
            raise ValueError("turn is not accepting a response")
        state.response = normalized
        state.phase = TurnPhase.DELIVERY
        runtime.store.save(state)
        return "response submitted for host approval"

    return ["workspace_read", "response_submit"]
