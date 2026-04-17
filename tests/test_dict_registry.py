from typing import Optional

import pytest

from nanoharness.components.tools.dict_registry import DictToolRegistry


class TestToolDecorator:
    def test_basic_registration(self):
        reg = DictToolRegistry()

        @reg.tool
        def greet(name: str):
            """Say hello."""
            return f"Hello, {name}!"

        schemas = reg.get_tool_schemas()
        assert len(schemas) == 1
        func_schema = schemas[0]["function"]
        assert func_schema["name"] == "greet"
        assert func_schema["description"] == "Say hello."
        assert func_schema["parameters"]["properties"]["name"] == {"type": "string"}
        assert func_schema["parameters"]["required"] == ["name"]

    def test_type_inference(self):
        reg = DictToolRegistry()

        @reg.tool
        def search(query: str, limit: int = 10, score: float = 0.5, flag: bool = False):
            """Search."""
            pass

        params = reg.get_tool_schemas()[0]["function"]["parameters"]
        props = params["properties"]
        assert props["query"] == {"type": "string"}
        assert props["limit"] == {"type": "integer"}
        assert props["score"] == {"type": "number"}
        assert props["flag"] == {"type": "boolean"}
        # query is required; the rest have defaults
        assert params["required"] == ["query"]

    def test_optional_param_not_required(self):
        reg = DictToolRegistry()

        @reg.tool
        def lookup(name: str, nickname: Optional[str] = None):
            """Lookup."""
            pass

        params = reg.get_tool_schemas()[0]["function"]["parameters"]
        assert "nickname" not in params["required"]
        assert "name" in params["required"]

    def test_no_annotation_defaults_to_string(self):
        reg = DictToolRegistry()

        @reg.tool
        def foo(x):
            """Foo."""
            pass

        props = reg.get_tool_schemas()[0]["function"]["parameters"]["properties"]
        assert props["x"] == {"type": "string"}


class TestToolCall:
    def test_call_success(self):
        reg = DictToolRegistry()

        @reg.tool
        def add(a: int, b: int):
            """Add."""
            return a + b

        assert reg.call("add", {"a": 1, "b": 2}) == 3

    def test_call_not_found(self):
        reg = DictToolRegistry()
        with pytest.raises(KeyError, match="not found"):
            reg.call("missing", {})

    def test_reset(self):
        reg = DictToolRegistry()

        @reg.tool
        def f():
            """F."""
            pass

        assert len(reg.get_tool_schemas()) == 1
        reg.reset()
        assert len(reg.get_tool_schemas()) == 0
