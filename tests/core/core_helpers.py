"""Auxiliares de pruebas CORE (solo datos sintéticos)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from nexus.core import demo
from nexus.core.refresh import RefreshService


class Clock:
    def __init__(self, iso: str) -> None:
        self.set(iso)

    def set(self, iso: str) -> None:
        self.now = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))

    def __call__(self) -> dt.datetime:
        return self.now


def make(
    tmp_path: Path, now: str = "2031-01-10T20:00:00Z", channel: str = "sintetico", hook: Any = None
) -> tuple[Path, Clock, RefreshService]:
    root = tmp_path / "data"
    demo.scenario(root)
    clock = Clock(now)
    return root, clock, RefreshService(root, channel, root / "rules.json", clock, hook)


def summary(svc: RefreshService) -> dict[str, Any]:
    head = svc.state.publication_head(svc.channel)
    assert head is not None
    m = svc.state.get_manifest(svc.channel, head["gen_id"])
    art = m["artifacts"]["core.summary"]
    return svc.state.get_artifact(art["key"], art["sha256"])


def values(svc: RefreshService) -> dict[str, dict[str, Any]]:
    return {
        item: {k: f["value"] for k, f in row["facts"].items()}
        for item, row in summary(svc)["rows"].items()
    }
