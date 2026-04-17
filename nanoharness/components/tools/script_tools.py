import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from nanoharness.components.tools.dict_registry import DictToolRegistry


# @param NAME:TYPE:DESCRIPTION (default: DEFAULT)
_PARAM_RE = re.compile(
    r"^#\s*@param\s+(\w+):(\w+):(.+?)(?:\s*\(default:\s*(.+?)\))?\s*$"
)

_TYPE_MAP = {
    "string": "string",
    "integer": "integer",
    "int": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
}


def _parse_script(path: Path) -> Dict[str, Any]:
    """Parse a shell script's header comments to extract metadata."""
    lines = path.read_text(encoding="utf-8").splitlines()

    description = ""
    params = []

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#"):
            if stripped and not stripped.startswith("#!"):
                break
            continue

        # Skip shebang
        if stripped.startswith("#!"):
            continue

        # Description: first non-param, non-shebang comment
        m = re.match(r"^#\s*(.+)$", stripped)
        if not m:
            continue

        param_match = _PARAM_RE.match(stripped)
        if param_match:
            name, ptype, pdesc, default = param_match.groups()
            params.append({
                "name": name,
                "type": _TYPE_MAP.get(ptype, "string"),
                "description": pdesc.strip(),
                "default": default.strip() if default else None,
                "required": default is None,
            })
        elif not params and not description:
            # First comment line is the description
            description = m.group(1).strip()

    return {"description": description, "params": params}


class ScriptToolRegistry(DictToolRegistry):
    """Tool registry driven by shell scripts.

    Scans a directory of .sh files, parses their @param headers,
    and auto-registers each script as an agent-callable tool.
    Arguments are passed to scripts as environment variables.

    Script header convention:
        #!/bin/bash
        # Human-readable description of this tool
        # @param name:type:description (default: value)
        # @param name:type:description       <- required param

    Usage:
        reg = ScriptToolRegistry("configs/scripts")
        reg.call("git_status", {"repo_path": "/my/repo"})
    """

    def __init__(self, scripts_dir: str = "configs/scripts"):
        super().__init__()
        self._scripts_dir = Path(scripts_dir)
        if self._scripts_dir.is_dir():
            self._load_scripts()

    def _load_scripts(self):
        for script_path in sorted(self._scripts_dir.glob("*.sh")):
            tool_name = script_path.stem  # filename without .sh
            meta = _parse_script(script_path)
            self._register_script(tool_name, script_path, meta)

    def _register_script(
        self, name: str, script_path: Path, meta: Dict[str, Any]
    ):
        properties = {}
        required = []

        for p in meta["params"]:
            properties[p["name"]] = {"type": p["type"]}
            if p["required"]:
                required.append(p["name"])

        self._tools[name] = {
            "func": lambda _path=script_path, **kwargs: self._exec(_path, kwargs),
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": meta["description"],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            },
            "_script_path": script_path,
        }

    @staticmethod
    def _exec(script_path: Path, args: Dict[str, Any]) -> str:
        env = os.environ.copy()
        for k, v in args.items():
            if isinstance(v, bool):
                env[k] = "true" if v else "false"
            else:
                env[k] = str(v)

        result = subprocess.run(
            ["bash", str(script_path)],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Script {script_path.name} failed (exit {result.returncode})")
        return result.stdout.strip()
