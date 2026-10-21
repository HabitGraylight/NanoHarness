"""Exercise the real SDK, profile resolver, registry and agent loop over HTTP.

Only the HTTP peer and model responses are deterministic test fixtures.
"""

import asyncio
import json
import threading

import httpx
import pytest
from pydantic import ValidationError
from mcp.shared.exceptions import McpError

from nanoharness import (
    DictToolRegistry, ExtensionContext, JsonStateStore, LLMResponse, RunControl,
    SimpleContextManager, SimpleHookManager, ToolCall, TraceEvaluator,
)
from nanoharness.extensions.mcp import (
    MCPExtensionConfig, MCPServerConfig, parallel_search_server,
)
from nanoharness.profiles import HarnessBuilder, HarnessSpec


SEARCH_URL = "https://search.parallel.ai/mcp"
PAGE_URL = "https://docs.python.org/3/"


@pytest.fixture
def http_peer(monkeypatch):
    """Inspect real MCP HTTP requests without contacting a search service."""
    requests = []
    state = {"error": False, "slow": False, "on_call": None, "http_error": False}
    real_client = httpx.AsyncClient

    async def handle(request):
        requests.append(request)
        if state["http_error"]:
            return httpx.Response(503, text="search unavailable")
        if request.method != "POST":
            return httpx.Response(405)
        message = json.loads(request.content)
        method = message["method"]
        if "id" not in message:
            return httpx.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "search-fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [
                {"name": "web_search", "description": "Search the web",
                 "inputSchema": {"type": "object", "properties": {
                     "objective": {"type": "string"},
                     "search_queries": {"type": "array", "items": {"type": "string"}},
                 }, "required": ["objective", "search_queries"]}},
                {"name": "web_fetch", "description": "Fetch a page",
                 "inputSchema": {"type": "object", "properties": {
                     "urls": {"type": "array", "items": {"type": "string"}},
                 }, "required": ["urls"]}},
            ]}
        elif method == "tools/call":
            if state["on_call"]:
                state["on_call"]()
            if state["slow"]:
                await asyncio.sleep(0.3)
            name = message["params"]["name"]
            if state["error"]:
                result = {"isError": True, "content": [{"type": "text", "text": "search failed"}]}
            else:
                text = (f"Python documentation: {PAGE_URL}" if name == "web_search"
                        else f"Content from {message['params']['arguments']['urls'][0]}: Python reference")
                result = {"content": [{"type": "text", "text": text}]}
        else:
            raise AssertionError(f"Unexpected MCP method: {method}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    class FixtureHTTPClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs.update(transport=httpx.MockTransport(handle), trust_env=False)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", FixtureHTTPClient)
    return requests, state


def calls(requests):
    return [json.loads(r.content) for r in requests
            if r.method == "POST" and json.loads(r.content).get("method") == "tools/call"]


def parallel_config(**overrides):
    return {
        "servers": [parallel_search_server().model_dump(mode="json")],
        **overrides,
    }


class SearchModel:
    """Script model choices, but assert it consumes the actual tool observations."""

    def __init__(self):
        self.turn = 0

    def chat(self, messages, tools=None):
        self.turn += 1
        names = {tool["function"]["name"] for tool in tools}
        assert "mcp__parallel__web_search" in names
        if self.turn == 1:
            return LLMResponse(content="Search", tool_calls=[ToolCall(
                name="mcp__parallel__web_search",
                arguments={"objective": "Find Python docs", "search_queries": ["Python documentation reference"]},
            )])
        observations = [m["content"] for m in messages if m["role"] == "tool"]
        assert PAGE_URL in observations[0]
        if self.turn == 2:
            return LLMResponse(content="Fetch", tool_calls=[ToolCall(
                name="mcp__parallel__web_fetch", arguments={"urls": [PAGE_URL]},
            )])
        assert "Python reference" in observations[-1]
        return LLMResponse(content="Python reference found.")


def build_harness(tmp_path, *, config=None, enabled=True, model=None):
    if config is None:
        config = parallel_config()
    context = ExtensionContext(tools=DictToolRegistry())
    context.services.update({
        "llm": model or SearchModel(),
        "context": SimpleContextManager(system_prompt="Research the task."),
        "state": JsonStateStore(str(tmp_path / "state.json")),
        "hooks": SimpleHookManager(),
        "evaluator": TraceEvaluator(),
    })
    spec = HarnessSpec.model_validate({
        "name": "search-agent", "engine": {},
        "host": {"services": list(context.services)},
        "extensions": [{"name": "mcp.stdio", "enabled": enabled, "config": config}],
    })
    return HarnessBuilder().build(spec, context=context)


def test_explicit_parallel_search_fetch_and_model_consumption(tmp_path, http_peer, monkeypatch):
    requests, _ = http_peer
    monkeypatch.setenv("PARALLEL_API_KEY", "ambient-key-must-not-be-used")
    monkeypatch.setenv("YOU_API_KEY", "existing-other-provider-key")
    build = build_harness(tmp_path)
    try:
        report = build.engine.run("Find Python reference documentation")
        assert report["trajectory"][-1]["thought"] == "Python reference found."
        assert [c["params"]["name"] for c in calls(requests)] == ["web_search", "web_fetch"]
        assert calls(requests)[1]["params"]["arguments"]["urls"] == [PAGE_URL]
        assert all(str(r.url) == SEARCH_URL for r in requests)
        assert all(r.headers["user-agent"].startswith("nanoharness/0.1.0 ") for r in requests)
        assert all("github.com/HabitGraylight/NanoHarness" in r.headers["user-agent"] for r in requests)
        assert all("authorization" not in r.headers for r in requests)
        assert build.manager.installations["mcp.stdio"].metadata["transport"] == "streamable_http"
        pool = build.context.services["mcp"]
    finally:
        build.close()
    assert not pool.clients[0].connected


@pytest.mark.parametrize("config", [{}, {"servers": []}, {"config_path": "missing.json"}])
def test_empty_or_missing_configuration_stays_offline(tmp_path, http_peer, config):
    requests, _ = http_peer
    build = build_harness(tmp_path, config=config)
    try:
        assert build.context.tools.get_tool_schemas() == []
        assert not requests
    finally:
        build.close()


def test_disabled_extension_never_connects(tmp_path, http_peer):
    requests, _ = http_peer
    build = build_harness(tmp_path, enabled=False)
    try:
        assert not requests
        assert not build.manager.installations
    finally:
        build.close()


def test_empty_saved_file_disables_default(tmp_path, http_peer):
    requests, _ = http_peer
    path = tmp_path / "mcp.json"
    path.write_text('{"servers": []}')
    build = build_harness(tmp_path, config={"config_path": str(path)})
    try:
        assert not build.context.tools.get_tool_schemas()
        assert not requests
    finally:
        build.close()


@pytest.mark.parametrize("from_file", [False, True])
def test_explicit_url_credentials_and_inspection_redaction(tmp_path, http_peer, from_file):
    requests, _ = http_peer
    url = (
        "https://search.example.org/mcp?mode=chosen&api_key=query-secret"
        "&X-Amz-Signature=signed-secret"
    )
    servers = [{"name": "chosen", "transport": "streamable_http", "url": url,
                "headers": {"Authorization": "Bearer configured-key", "X-Custom": "private-value"}}]
    config = {"servers": servers}
    if from_file:
        path = tmp_path / "mcp.json"
        path.write_text(json.dumps(config))
        config = {"config_path": str(path)}
    build = build_harness(tmp_path, config=config)
    try:
        assert build.context.tools.call("mcp__chosen__web_fetch", {"urls": [PAGE_URL]}).endswith("Python reference")
        assert all(str(r.url) == url for r in requests)
        assert all(r.headers["authorization"] == "Bearer configured-key" for r in requests)
        assert all(r.headers["x-custom"] == "private-value" for r in requests)
        inspection = json.dumps(build.inspect())
        assert "configured-key" not in inspection
        assert "private-value" not in inspection
        assert "query-secret" not in inspection
        assert "signed-secret" not in inspection
    finally:
        build.close()


def test_explicit_server_failure_does_not_fall_back(tmp_path, http_peer):
    requests, state = http_peer
    state["http_error"] = True
    with pytest.warns(RuntimeWarning, match="chosen.*failed"):
        build = build_harness(tmp_path, config={"servers": [{
            "name": "chosen", "transport": "streamable_http", "url": "https://chosen.example/mcp",
        }]})
    try:
        assert build.context.tools.get_tool_schemas() == []
        assert build.context.services["mcp"].failures.keys() == {"chosen"}
        assert all(r.url.host == "chosen.example" for r in requests)
    finally:
        build.close()


def test_tool_errors_reach_engine_observation(tmp_path, http_peer):
    requests, state = http_peer
    state["error"] = True
    class ErrorModel:
        turn = 0
        def chat(self, messages, tools=None):
            self.turn += 1
            if self.turn == 1:
                return LLMResponse(content="", tool_calls=[ToolCall(name="mcp__parallel__web_search", arguments={
                    "objective": "Find docs", "search_queries": ["Python documentation"],
                })])
            assert "search failed" in messages[-1]["content"]
            return LLMResponse(content="Search unavailable")
    build = build_harness(tmp_path, model=ErrorModel())
    try:
        report = build.engine.run("Find docs")
        assert "search failed" in report["trajectory"][0]["observation"]
        assert len(calls(requests)) == 1
    finally:
        build.close()


def test_read_timeout_reaches_caller_and_connection_closes(tmp_path, http_peer):
    requests, state = http_peer
    build = build_harness(
        tmp_path,
        config=parallel_config(read_timeout_seconds=0.05),
    )
    state["slow"] = True
    try:
        with pytest.raises(McpError, match="[Tt]imed out"):
            build.context.tools.call("mcp__parallel__web_fetch", {"urls": [PAGE_URL]})
        assert len(calls(requests)) == 1
        pool = build.context.services["mcp"]
    finally:
        build.close()
    assert not pool.clients[0].connected


def test_engine_permissions_block_remote_execution(tmp_path, http_peer):
    requests, _ = http_peer
    class Deny:
        def enforce(self, name, args):
            return "Search disabled by host"
    class DeniedModel:
        turn = 0
        def chat(self, messages, tools=None):
            self.turn += 1
            if self.turn == 1:
                return LLMResponse(content="", tool_calls=[ToolCall(name="mcp__parallel__web_fetch", arguments={"urls": [PAGE_URL]})])
            assert "Search disabled by host" in messages[-1]["content"]
            return LLMResponse(content="Denied")
    build = build_harness(tmp_path, model=DeniedModel())
    build.engine.permissions = Deny()
    try:
        build.engine.run("Fetch docs")
        assert not calls(requests)
    finally:
        build.close()


def test_cooperative_cancel_stops_after_current_tool(tmp_path, http_peer):
    requests, state = http_peer
    control = RunControl()
    state["on_call"] = lambda: control.cancel("Caller cancelled")
    build = build_harness(tmp_path)
    try:
        report = build.engine.run("Find docs", control=control)
        assert report["summary"]["run_status"] == "cancelled"
        assert len(calls(requests)) == 1
        assert build.engine.llm.turn == 1
    finally:
        build.close()
    assert not any(t.name == "nanoharness-mcp-parallel" and t.is_alive() for t in threading.enumerate())


@pytest.mark.parametrize("server", [
    {"name": "missing-command"},
    {"name": "missing-url", "transport": "streamable_http"},
    {"name": "bad-url", "transport": "streamable_http", "url": "file:///tmp/mcp"},
    {"name": "credentials", "transport": "streamable_http", "url": "https://key:secret@example.org/mcp"},
    {"name": "mixed", "transport": "streamable_http", "url": SEARCH_URL, "command": "server"},
    {"name": "mixed", "command": "server", "headers": {"Authorization": "secret"}},
])
def test_invalid_transport_configuration_is_rejected(server):
    with pytest.raises(ValidationError):
        MCPServerConfig.model_validate(server)


def test_empty_default_and_parallel_helper_are_stable_across_serialization():
    config = MCPExtensionConfig()
    assert config.servers == []
    assert MCPExtensionConfig.model_validate(config.model_dump()) == config
    assert MCPServerConfig.model_validate(
        parallel_search_server().model_dump()
    ) == parallel_search_server()
