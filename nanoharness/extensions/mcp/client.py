"""Synchronous white-box client over the official async MCP SDK."""

import asyncio
import concurrent.futures
import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Dict, List, Optional

from nanoharness.extensions.mcp.models import MCPServerConfig


class MCPDependencyError(ImportError):
    pass


def _sdk_types():
    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.types import Implementation
    except ImportError as error:
        raise MCPDependencyError(
            "MCP support requires the optional dependency; "
            "install it with `pip install 'nanoharness[mcp]'`."
        ) from error
    return ClientSession, StdioServerParameters, stdio_client, Implementation


class MCPClient:
    """Own one MCP connection and expose a synchronous tool API.

    The official SDK is asynchronous. A dedicated thread owns both its event
    loop and async context managers, while Harness tool calls submit work to
    that thread. This keeps the ordinary schema-first registry synchronous and
    makes process ownership explicit.
    """

    def __init__(
        self,
        name: str,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        *,
        transport: str = "stdio",
        url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        read_timeout_seconds: float = 15.0,
        client_name: str = "nanoharness",
        client_version: str = "0.1.0",
    ):
        self.name = name
        self._server = MCPServerConfig(
            name=name,
            command=command,
            args=args or [],
            env=env or {},
            cwd=cwd,
            transport=transport,
            url=url,
            headers=headers or {},
        )
        self._read_timeout_seconds = read_timeout_seconds
        self._client_name = client_name
        self._client_version = client_version
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._requests: Optional[asyncio.Queue] = None
        self._session: Any = None
        self._tools: List[Dict[str, Any]] = []
        self._ready = threading.Event()
        self._startup_error: Optional[BaseException] = None

    @property
    def tools(self) -> List[Dict[str, Any]]:
        return [dict(tool) for tool in self._tools]

    @property
    def connected(self) -> bool:
        return (
            self._session is not None
            and self._thread is not None
            and self._thread.is_alive()
        )

    def connect(self) -> None:
        if self.connected:
            return
        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"nanoharness-mcp-{self.name}",
            daemon=True,
        )
        self._thread.start()
        startup_timeout = self._read_timeout_seconds + 5.0
        if not self._ready.wait(timeout=startup_timeout):
            raise TimeoutError(f"MCP server '{self.name}' initialize timeout")
        if self._startup_error is not None:
            error = self._startup_error
            self._thread.join(timeout=1.0)
            if isinstance(error, Exception):
                raise error
            raise RuntimeError(str(error))

    def list_tools(self) -> List[Dict[str, Any]]:
        result = self._submit("list_tools")
        self._tools = [
            tool.model_dump(mode="json", by_alias=True)
            for tool in result.tools
        ]
        return self.tools

    def call_tool(self, tool_name: str, arguments: Optional[Dict] = None) -> str:
        result = self._submit("call_tool", tool_name, arguments or {})
        payload = result.model_dump(mode="json", by_alias=True)
        text_parts = [
            item.get("text", "")
            for item in payload.get("content", [])
            if item.get("type") == "text"
        ]
        text = "\n".join(part for part in text_parts if part)
        if payload.get("isError"):
            raise RuntimeError(text or f"MCP tool '{tool_name}' failed")
        if text:
            return text
        if payload.get("structuredContent") is not None:
            return json.dumps(payload["structuredContent"], ensure_ascii=False)
        return json.dumps(payload, ensure_ascii=False)

    def disconnect(self) -> None:
        if not self.connected:
            self._session = None
            return
        try:
            self._submit("close")
        finally:
            if self._thread is not None:
                self._thread.join(timeout=self._read_timeout_seconds + 5.0)
            self._session = None
            self._loop = None
            self._requests = None

    close = disconnect

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as error:
            if not self._ready.is_set():
                self._startup_error = error
                self._ready.set()
        finally:
            self._session = None

    @asynccontextmanager
    async def _transport(self):
        if self._server.transport == "streamable_http":
            try:
                from mcp.client import streamable_http
            except ImportError as error:
                raise MCPDependencyError(
                    "Streamable HTTP requires mcp>=1.10; "
                    "install `pip install 'nanoharness[mcp]'`."
                ) from error
            headers = {"User-Agent": (
                f"{self._client_name}/{self._client_version} "
                "(+https://github.com/HabitGraylight/NanoHarness)"
            )}
            # HTTP header names are case-insensitive. Explicit headers win.
            explicit_names = {name.lower() for name in self._server.headers}
            headers = {
                key: value for key, value in headers.items()
                if key.lower() not in explicit_names
            }
            headers.update(self._server.headers)
            modern_client = getattr(streamable_http, "streamable_http_client", None)
            if modern_client is not None:
                import httpx

                async with httpx.AsyncClient(
                    headers=headers,
                    timeout=httpx.Timeout(self._read_timeout_seconds),
                    follow_redirects=True,
                    trust_env=False,
                ) as http_client:
                    async with modern_client(
                        self._server.url,
                        http_client=http_client,
                    ) as (read_stream, write_stream, _):
                        yield read_stream, write_stream
            else:
                legacy_client = streamable_http.streamablehttp_client
                async with legacy_client(
                    self._server.url,
                    headers=headers,
                    timeout=timedelta(seconds=self._read_timeout_seconds),
                    sse_read_timeout=timedelta(seconds=self._read_timeout_seconds),
                ) as (read_stream, write_stream, _):
                    yield read_stream, write_stream
            return

        _, StdioServerParameters, stdio_client, _ = _sdk_types()
        environment = None
        if self._server.env:
            environment = dict(os.environ)
            environment.update(self._server.env)
        parameters = StdioServerParameters(
            command=self._server.command,
            args=self._server.args,
            env=environment,
            cwd=self._server.cwd,
        )
        async with stdio_client(parameters) as streams:
            yield streams

    async def _serve(self) -> None:
        ClientSession, _, _, Implementation = _sdk_types()
        self._loop = asyncio.get_running_loop()
        self._requests = asyncio.Queue()
        timeout = timedelta(seconds=self._read_timeout_seconds)
        client_info = Implementation(
            name=self._client_name,
            version=self._client_version,
        )
        try:
            async with self._transport() as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timeout,
                    client_info=client_info,
                ) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    while True:
                        operation, args, future = await self._requests.get()
                        if operation == "close":
                            if not future.done():
                                future.set_result(None)
                            break
                        try:
                            result = await getattr(session, operation)(*args)
                        except BaseException as error:
                            if not future.done():
                                future.set_exception(error)
                        else:
                            if not future.done():
                                future.set_result(result)
        except BaseException as error:
            if not self._ready.is_set():
                self._startup_error = error
                self._ready.set()
                return
            raise

    def _submit(self, operation: str, *args: Any) -> Any:
        self._require_connected()
        assert self._loop is not None
        assert self._requests is not None
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._loop.call_soon_threadsafe(
            self._requests.put_nowait,
            (operation, args, future),
        )
        return future.result(timeout=self._read_timeout_seconds + 5.0)

    def _require_connected(self) -> None:
        if not self.connected:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")
