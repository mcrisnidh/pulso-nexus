"""Reglas CORE (``nexus.core.rules/1``): extienden las reglas del Pilot-20 sin duplicarlas.

Todo lo comercial vive en el archivo de reglas (versionado y con estado de aprobación). Una regla
no aprobada nunca produce un KPI OFICIAL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexus.planning.rules import RULES_SCHEMA as PLANNING_SCHEMA
from nexus.planning.rules import PlanningRules, rules_from_dict

CORE_RULES_SCHEMA = "nexus.core.rules/1"  # histórico: solo ejecución sintética aislada (D-B)
CORE_RULES_SCHEMA_V2 = "nexus.core.rules/2"  # obligatorio con fuentes reales (V16–V17)
UNKNOWN_CLASS = "UNKNOWN"  # clave sin clasificación comercial explícita (V17)
APPROVED = "CRC_APPROVED"
PORTAL_DATE_FIELDS = ("delivery_date", "ship_date")
ALLOCATION_METHODS = ("due_date_fifo",)


@dataclass(frozen=True)
class CoreRules:
    base: PlanningRules  # clases de cliente por clave exacta, estados Portal, stockrooms FG
    portal_date_field: str
    portal_date_approval: str
    allocation_method: str
    allocation_approval: str
    fg_subtract_reserved: bool
    cs_status_vocabulary: tuple[str, ...]
    certified_sources: frozenset[str]  # fuentes con acta de certificación (vacío en el Hito A)
    raw_sha256: str
    strict: bool = False  # v2: lista cerrada; clave no listada ⇒ UNKNOWN (nunca OTHER)

    def classify(self, customer_key_exact: str) -> str:
        """Clase comercial de CORE. v2: solo clases EXPLÍCITAS; lo demás es ``UNKNOWN``.
        v1 (histórico sintético): comportamiento Pilot-20 sin cambios (D-C)."""
        if not self.strict:
            return self.base.customer_class(customer_key_exact)
        matches = [
            c for c, keys in self.base.customer_classes.items() if customer_key_exact in keys
        ]
        return matches[0] if len(matches) == 1 else UNKNOWN_CLASS

    @property
    def version(self) -> str:
        return self.base.version

    @property
    def fully_approved(self) -> bool:
        return (
            self.base.approval == APPROVED
            and self.portal_date_approval == APPROVED
            and self.allocation_approval == APPROVED
        )


def _validate_classes(data: dict[str, Any]) -> None:
    """D-R3-02/03/04: reglas ambiguas o inválidas se rechazan AL CARGAR."""
    owner: dict[str, str] = {}
    for cls, keys in data["customer_classes"].items():
        if cls == UNKNOWN_CLASS:
            raise ValueError("UNKNOWN no es una clase declarable")
        for raw in keys:
            key = str(raw)
            if not key.strip() or key != key.rstrip() or not key.isprintable():
                raise ValueError(f"clave de cliente inválida en {cls!r}: {key!r}")
            if key in owner and owner[key] != cls:
                raise ValueError(f"clave {key!r} en dos clases: {owner[key]!r} y {cls!r}")
            owner[key] = cls
    sub = data.get("substitute_class")
    if sub is not None and sub in data.get("preserve_classes", []):
        raise ValueError(f"la clase sustituida {sub!r} no puede ser también conservada")


def core_rules_from_dict(data: dict[str, Any], raw_sha256: str = "") -> CoreRules:
    schema = data.get("schema")
    if schema not in (CORE_RULES_SCHEMA, CORE_RULES_SCHEMA_V2):
        raise ValueError(f"esquema de reglas CORE no soportado: {schema!r}")
    strict = schema == CORE_RULES_SCHEMA_V2
    if strict and data.get("unclassified_customers") != UNKNOWN_CLASS:
        raise ValueError("reglas v2: 'unclassified_customers' debe ser 'UNKNOWN' (V17)")
    _validate_classes(data)
    base_keys = (
        "version",
        "approval",
        "snapshot_name_pattern",
        "portal_excluded_statuses",
        "usable_fg_stockrooms",
        "customer_classes",
        "substitute_class",
        "preserve_classes",
        "customer_notes",
    )
    base = rules_from_dict(
        {"schema": PLANNING_SCHEMA, **{k: data[k] for k in base_keys if k in data}}
    )
    portal = data["portal_date"]
    if portal["field"] not in PORTAL_DATE_FIELDS:
        raise ValueError(f"campo de fecha Portal no soportado: {portal['field']!r}")
    allocation = data["allocation"]
    if allocation["method"] not in ALLOCATION_METHODS:
        raise ValueError(f"método de asignación no soportado: {allocation['method']!r}")
    vocabulary = tuple(str(x) for x in data["cs_status_vocabulary"])
    if not vocabulary or len(set(vocabulary)) != len(vocabulary):
        raise ValueError("vocabulario de Estatus CS vacío o con duplicados")
    return CoreRules(
        base=base,
        portal_date_field=str(portal["field"]),
        portal_date_approval=str(portal["approval"]),
        allocation_method=str(allocation["method"]),
        allocation_approval=str(allocation["approval"]),
        fg_subtract_reserved=bool(data.get("fg_subtract_reserved", False)),
        cs_status_vocabulary=vocabulary,
        certified_sources=frozenset(data.get("certified_sources", [])),
        raw_sha256=raw_sha256,
        strict=strict,
    )


def load_core_rules(path: Path) -> CoreRules:
    from nexus.hashing import sha256_bytes  # noqa: PLC0415

    raw = path.read_bytes()
    return core_rules_from_dict(json.loads(raw.decode("utf-8")), sha256_bytes(raw))
