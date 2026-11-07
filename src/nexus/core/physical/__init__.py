"""Δ1 — Lectores FÍSICOS de las fuentes reales (bytes → registros del contrato CORE). Puros.

No leen archivos, no escriben, no consultan el reloj ni la zona del sistema: reciben bytes y
configuración. La E/S (solo lectura del origen, escritura privada) vive en ``nexus.core.stage``.
"""

from __future__ import annotations

import re
from typing import Any

from nexus.core.physical.common import PhysicalResult, default_layout
from nexus.core.physical.in092u import read_in092u
from nexus.core.physical.oe965 import read_oe965
from nexus.core.physical.portal import read_portal

KINDS = ("oe965", "portal", "in092u")

__all__ = [
    "KINDS",
    "PhysicalResult",
    "default_layout",
    "detect_kind",
    "read_in092u",
    "read_oe965",
    "read_portal",
]


def detect_kind(file_name: str, layout: dict[str, Any]) -> str | None:
    """Tipo de fuente por el patrón de nombre declarado (sin mirar el contenido)."""
    for kind, pattern in layout["file_patterns"].items():
        if re.match(str(pattern), file_name, re.IGNORECASE):
            return str(kind)
    return None
