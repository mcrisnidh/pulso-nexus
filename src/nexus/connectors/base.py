"""Interfaz de conectores: solo transporte de bytes, sin reglas de negocio."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CaptureResult:
    """Resultado de una captura: bytes exactos de la fuente más metadatos no sensibles."""

    connector: str
    connector_version: str
    source_id: str
    media_type: str
    content: bytes
    source_metadata: dict[str, str] = field(default_factory=dict)
    consistency_level: str = "single_object"
