"""NanoClaudeCode Dispatch sandbox integration for Subagent."""

from app.dispatch import DispatchRegistry, tool_result
from nanoharness.extensions.subagents import build_subagent_context, register_task_tool


class ImmediateAnswer:
    def chat(self, messages, tools=None):
        from nanoharness.core.schema import LLMResponse

        return LLMResponse(content="done")


def _registry(tmp_path):
    registry = DispatchRegistry(workspace_root=str(tmp_path))
    registry.register(
        "file_read",
        lambda args: tool_result(ok=True, output=str(args["path"])),
        schema={
            "type": "function",
            "function": {
                "name": "file_read",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        path_params=["path"],
    )
    return registry


def test_subagent_handler_inherits_dispatch_path_sandbox(tmp_path):
    context = build_subagent_context(_registry(tmp_path))
    result = context.handlers["file_read"]({"path": "../../etc/passwd"})
    assert "Error" in result


def test_task_description_is_not_treated_as_a_path(tmp_path):
    registry = _registry(tmp_path)
    register_task_tool(registry, ImmediateAnswer())
    assert registry._path_params.get("task", []) == []
