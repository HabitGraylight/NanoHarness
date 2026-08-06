"""YAML, TOML, and JSON loading for HarnessSpec."""

import json
from pathlib import Path
from typing import Any, Dict

import yaml

from nanoharness.profiles.models import HarnessSpec


def _load_toml(text: str) -> Dict[str, Any]:
    try:
        import tomllib
    except ImportError:  # Python 3.10
        import tomli as tomllib
    return tomllib.loads(text)


def load_harness_spec(path: str) -> HarnessSpec:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    suffix = source.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif suffix == ".toml":
        data = _load_toml(text)
    elif suffix == ".json":
        data = json.loads(text)
    else:
        raise ValueError(
            f"Unsupported HarnessSpec format {suffix!r}; use YAML, TOML, or JSON"
        )
    if not isinstance(data, dict):
        raise ValueError("HarnessSpec root must be an object")
    return HarnessSpec.model_validate(data)
