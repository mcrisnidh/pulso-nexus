"""Reglas comerciales parametrizadas (versionadas, con estado de aprobación).

El motor nunca contiene reglas por número de pieza ni claves de cliente: todo lo que dependa de
la identidad comercial (por ejemplo, qué claves exactas son CAT o TUPY) se carga de un archivo de
reglas. Si la regla no está aprobada como CRC, los resultados que dependen de ella lo indican.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RULES_SCHEMA = "nexus.planning.rules/1"


@dataclass(frozen=True)
class PlanningRules:
    version: str
    approval: str  # p. ej. PROVISIONAL_NO_CRC | CRC_APPROVED
    snapshot_name_pattern: str  # regex con grupo 'date' YYYYMMDD sobre el nombre de archivo
    portal_excluded_statuses: frozenset[str]
    usable_fg_stockrooms: frozenset[str]
    customer_classes: dict[str, frozenset[str]]  # clase -> claves de cliente EXACTAS
    substitute_class: str | None  # clase cuya demanda OE965 se sustituye por el Portal
    preserve_classes: frozenset[str]  # clases que se conservan siempre (p. ej. TUPY)
    customer_notes: dict[str, str] = field(default_factory=dict)  # clave exacta -> observación

    @property
    def crc_approved(self) -> bool:
        return self.approval == "CRC_APPROVED"

    def customer_class(self, customer_key_exact: str) -> str:
        """Clase por clave EXACTA (con espacios significativos); nunca por similitud de nombre."""
        classes = self.customer_classes.items()
        matches = sorted(c for c, keys in classes if customer_key_exact in keys)
        if len(matches) > 1:
            raise ValueError(f"clave de cliente en varias clases: {customer_key_exact!r} {matches}")
        return matches[0] if matches else "OTHER"

    def snapshot_date(self, file_name: str) -> str | None:
        match = re.search(self.snapshot_name_pattern, file_name)
        if not match:
            return None
        raw = match.group("date")
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def rules_from_dict(data: dict[str, Any]) -> PlanningRules:
    if data.get("schema") != RULES_SCHEMA:
        raise ValueError(f"esquema de reglas no soportado: {data.get('schema')!r}")
    re.compile(str(data["snapshot_name_pattern"]))
    classes = {str(k): frozenset(str(x) for x in v) for k, v in data["customer_classes"].items()}
    substitute = data.get("substitute_class")
    if substitute is not None and substitute not in classes:
        raise ValueError(f"clase a sustituir no definida: {substitute!r}")
    return PlanningRules(
        version=str(data["version"]),
        approval=str(data["approval"]),
        snapshot_name_pattern=str(data["snapshot_name_pattern"]),
        portal_excluded_statuses=frozenset(data["portal_excluded_statuses"]),
        usable_fg_stockrooms=frozenset(data["usable_fg_stockrooms"]),
        customer_classes=classes,
        substitute_class=substitute,
        preserve_classes=frozenset(data.get("preserve_classes", [])),
        customer_notes={str(k): str(v) for k, v in data.get("customer_notes", {}).items()},
    )


def load_rules(path: Path) -> PlanningRules:
    return rules_from_dict(json.loads(path.read_text("utf-8")))
