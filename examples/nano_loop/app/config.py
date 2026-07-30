"""YAML configuration loading for loop specifications."""

from pathlib import Path

import yaml

from app.schema import LoopSpec


def load_loop_spec(path: str) -> LoopSpec:
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Loop config must contain a mapping: {config_path}")
    return LoopSpec.model_validate(data)
