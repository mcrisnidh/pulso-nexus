"""Hechos con estado de certeza y procedencia.

Un valor que no se puede certificar NUNCA se convierte en cero: su ``value`` es ``None`` y su
``status`` explica por qué.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

# Vocabulario cerrado de estados.
PROVISIONAL = "PROVISIONAL_CALCULATED"  # calculado con fuentes de un mismo corte, no certificadas
DIAGNOSTIC = "DIAGNOSTIC_ONLY"  # observación útil que no constituye un resultado de negocio
TEMPORAL_MISMATCH = "TEMPORAL_MISMATCH"  # exigiría combinar cortes incompatibles
BLOCKED_SOURCE = "BLOCKED_SOURCE"  # falta la fuente
BLOCKED_CRC = "BLOCKED_CRC"  # falta la regla comercial aprobada
NOT_CERTIFIABLE = "NOT_CERTIFIABLE"  # la fuente existe pero no permite certificar el valor
UNKNOWN_NOT_ZERO = "UNKNOWN_NOT_ZERO"  # dato ausente o no numérico: desconocido, no cero

STATUSES = frozenset(
    {
        PROVISIONAL,
        DIAGNOSTIC,
        TEMPORAL_MISMATCH,
        BLOCKED_SOURCE,
        BLOCKED_CRC,
        NOT_CERTIFIABLE,
        UNKNOWN_NOT_ZERO,
    }
)


@dataclass(frozen=True)
class SourceRef:
    """Procedencia de un registro: fuente lógica, archivo, SHA-256, línea física y corte."""

    source_id: str
    file: str
    sha256: str
    line: int
    cut: str | None
    granularity: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "file": self.file,
            "sha256": self.sha256,
            "line": self.line,
            "cut": self.cut,
            "granularity": self.granularity,
        }


@dataclass
class Fact:
    """Un valor calculado (o no calculable) con su estado, origen y limitaciones."""

    name: str
    value: Any
    status: str
    sources: list[SourceRef] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    unit: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"estado desconocido: {self.status}")
        no_value = (BLOCKED_SOURCE, BLOCKED_CRC, TEMPORAL_MISMATCH, UNKNOWN_NOT_ZERO)
        if self.status in no_value and self.value not in (None, [], {}):
            raise ValueError(f"{self.name}: un estado {self.status} no puede llevar valor")
        if self.status != PROVISIONAL and not self.limitations:
            raise ValueError(f"{self.name}: todo estado no calculado necesita una explicación")

    def as_dict(self) -> dict[str, Any]:
        refs = sorted({(r.source_id, r.file, r.sha256, r.cut, r.granularity) for r in self.sources})
        lines: dict[str, list[int]] = {}
        for r in self.sources:
            lines.setdefault(r.source_id, []).append(r.line)
        return {
            "name": self.name,
            "value": jsonable(self.value),
            "unit": self.unit,
            "status": self.status,
            "limitations": list(self.limitations),
            "provenance": [
                {
                    "source_id": s,
                    "file": f,
                    "sha256": h,
                    "cut": c,
                    "granularity": g,
                    "lines": sorted(set(lines.get(s, []))),
                }
                for s, f, h, c, g in refs
            ],
        }


def parse_decimal(raw: str | None) -> Decimal | None:
    """Número exacto o ``None`` (desconocido). Nunca convierte un texto inválido en cero."""
    if raw is None:
        return None
    text = raw.strip().replace(",", "")
    if text == "" or text.upper() == "NULL":
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def fmt(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    return f"{normalized:f}"


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return fmt(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value
