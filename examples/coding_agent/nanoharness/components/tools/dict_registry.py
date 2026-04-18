import inspect
from typing import Any, Callable, Dict, List, get_args, get_origin

from nanoharness.core.base import BaseToolRegistry

# Python type hint -> JSON Schema type mapping
_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _resolve_param_schema(annotation) -> Dict[str, str]:
    """Resolve a Python type annotation to a JSON Schema property dict."""
    origin = get_origin(annotation)

    # Handle Optional[X] — which is Union[X, None]
    if origin is type(None):
        return {"type": "string"}

    if origin is not None:
        args = get_args(annotation)
        if args:
            inner = args[0]
            schema = _resolve_param_schema(inner)
            return schema

    json_type = _TYPE_MAP.get(annotation, "string")
    return {"type": json_type}


def _is_optional(annotation) -> bool:
    """Check if a type annotation is Optional[X] (i.e. Union[X, None])."""
    origin = get_origin(annotation)
    if origin is not None:
        args = get_args(annotation)
        return type(None) in args
    return False


class DictToolRegistry(BaseToolRegistry):
    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}

    def tool(self, func: Callable):
        """
        Decorator: register a plain Python function as a tool.

        Usage:
            @registry.tool
            def get_weather(city: str, days: int = 3):
                '''Get weather forecast for a city.'''
                return "Sunny"
        """
        name = func.__name__
        sig = inspect.signature(func)
        doc = (func.__doc__ or "No description provided.").strip()

        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            annotation = param.annotation
            if annotation is inspect.Parameter.empty:
                annotation = str

            properties[param_name] = _resolve_param_schema(annotation)

            # Required if: no default value AND not Optional
            has_default = param.default is not inspect.Parameter.empty
            is_opt = _is_optional(annotation)
            if not has_default and not is_opt:
                required.append(param_name)

        self._tools[name] = {
            "func": func,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": doc,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            },
        }
        return func

    def get_tool_schemas(self) -> List[Dict]:
        return [info["schema"] for info in self._tools.values()]

    def call(self, name: str, args: Dict) -> Any:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found. Available: {list(self._tools)}")
        return self._tools[name]["func"](**args)

    def merge(self, other: "DictToolRegistry") -> None:
        """Merge tools from another registry into this one.

        Tools from `other` with names that already exist here will overwrite.
        """
        self._tools.update(other._tools)

    def reset(self):
        self._tools.clear()
