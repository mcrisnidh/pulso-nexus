"""Conectores aprobados (ver allowlist.toml)."""

from __future__ import annotations

import tomllib
from importlib import resources


def load_allowlist() -> dict[str, dict[str, str]]:
    text = resources.files("nexus.connectors").joinpath("allowlist.toml").read_text("utf-8")
    data = tomllib.loads(text)
    connectors = data.get("connectors", {})
    return {str(k): {str(a): str(b) for a, b in v.items()} for k, v in connectors.items()}
