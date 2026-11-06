"""Lectura verificada de lo PUBLICADO (única API de datos de la web).

Toda lectura verifica el SHA-256 registrado en el manifiesto de la generación: si un artefacto
fue alterado, la vista de ese ITEM falla cerrada (error de integridad, sin cifras).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nexus.core.state import CoreState
from nexus.errors import IntegrityError


@dataclass
class Published:
    head: dict[str, Any] | None
    manifest: dict[str, Any] | None
    summary: dict[str, Any] | None
    error: str | None = None


class PublishedReader:
    def __init__(self, state: CoreState, channel: str) -> None:
        self.state = state
        self.channel = channel

    def current(self) -> Published:
        head = self.state.publication_head(self.channel)
        if head is None:
            return Published(None, None, None)
        try:
            manifest = self.state.get_manifest(self.channel, head["gen_id"])
            art = manifest["artifacts"]["core.summary"]
            summary = self.state.get_artifact(art["key"], art["sha256"])
        except (IntegrityError, FileNotFoundError, KeyError) as exc:
            return Published(head, None, None, f"integridad: {type(exc).__name__}")
        return Published(head, manifest, summary)

    def item(self, manifest: dict[str, Any], item: str) -> dict[str, Any]:
        entry = manifest["items"].get(item)
        if entry is None:
            raise KeyError(item)
        try:
            return self.state.get_artifact(entry["key"], entry["sha256"])
        except (IntegrityError, FileNotFoundError) as exc:
            raise IntegrityError(f"artefacto del ITEM {item!r} no verificable") from exc

    def history(self) -> list[dict[str, Any]]:
        return self.state.publication_history(self.channel)

    def events(self) -> list[dict[str, Any]]:
        return self.state.events(self.channel)
