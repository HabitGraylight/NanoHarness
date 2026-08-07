"""NanoClaudeCode adaptation of portable MCP results to tool_result."""

from app.mcp import mcp_handler


class StubClient:
    def __init__(self, error=None):
        self.error = error

    def call_tool(self, name, arguments):
        if self.error:
            raise self.error
        return f"{name}:{arguments['path']}"


def test_mcp_adapter_wraps_success():
    result = mcp_handler(StubClient(), "read_file")({"path": "main.py"})
    assert result.ok is True
    assert result.output == "read_file:main.py"


def test_mcp_adapter_wraps_failure():
    result = mcp_handler(StubClient(RuntimeError("offline")), "read_file")({
        "path": "main.py"
    })
    assert result.ok is False
    assert result.error == "offline"
