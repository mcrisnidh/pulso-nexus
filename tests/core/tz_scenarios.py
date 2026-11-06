"""Escenarios sintéticos de B0-7. Este módulo NO importa ``nexus.core.timebase``: el escenario de
compatibilidad UTC se ejecutó primero con el código de la línea base ``f404f96`` para fijar su
huella dorada (``UTC_BASELINE_DIGEST``)."""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable
from pathlib import Path

from nexus.core import demo
from nexus.core.refresh import RefreshService

# Huella del árbol de estado (generaciones, Frozen, publicaciones) producido por
# ``utc_baseline_scenario`` con el código de f404f96, sin operation.json.
UTC_BASELINE_DIGEST = "400e36ce4f48fe7a314f1f68477d86b3378581dfcd611ea9e095163e1de792d1"


class Clock:
    def __init__(self, iso: str) -> None:
        self.set(iso)

    def set(self, iso: str) -> None:
        self.now = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))

    def __call__(self) -> dt.datetime:
        return self.now


def state_digest(store_root: Path, prefixes: tuple[str, ...] = ("gen/", "frozen/", "pub/")) -> str:
    """SHA-256 de rutas y bytes del estado determinista (excluye eventos, con uuid aleatorio)."""
    h = hashlib.sha256()
    objects = store_root / "objects"
    for p in sorted(objects.rglob("*")):
        rel = p.relative_to(objects).as_posix()
        if p.is_file() and rel.startswith(prefixes):
            h.update(rel.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def utc_baseline_scenario(
    root: Path, make_service: Callable[..., RefreshService] | None = None
) -> RefreshService:
    """Dos meses: Frozen M1, Current + inválido + tardío, nuevo corte, corte roto (no publica),
    rollback, reinicio y mes 2. Sin operation.json (o con la configuración que se pase)."""
    demo.scenario(root)
    clock = Clock("2031-01-10T20:00:00Z")

    def svc() -> RefreshService:
        return (make_service or RefreshService)(root, "sintetico", root / "rules.json", clock)

    s = svc()
    s.refresh("t")
    demo.current_m1(root)
    demo.invalid_extract(root)
    demo.late_earlier_extract(root)
    clock.set("2031-01-12T20:00:00Z")
    s.refresh("t")
    demo.new_cut_m1(root)
    clock.set("2031-01-20T20:00:00Z")
    s.refresh("t")
    demo.broken_cut_m1(root)
    clock.set("2031-01-25T20:00:00Z")
    s.refresh("t")
    first = s.state.publication_history("sintetico")[0]["gen_id"]
    s.rollback(first, "admin")
    s = svc()  # reinicio
    demo.month2(root)
    clock.set("2031-02-10T20:00:00Z")
    s.refresh("t")
    return s
