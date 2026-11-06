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

CORE_RULES_SCHEMA = "nexus.core.rules/1"
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


def core_rules_from_dict(data: dict[str, Any], raw_sha256: str = "") -> CoreRules:
    if data.get("schema") != CORE_RULES_SCHEMA:
        raise ValueError(f"esquema de reglas CORE no soportado: {data.get('schema')!r}")
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
    )


def load_core_rules(path: Path) -> CoreRules:
    from nexus.hashing import sha256_bytes  # noqa: PLC0415

    raw = path.read_bytes()
    return core_rules_from_dict(json.loads(raw.decode("utf-8")), sha256_bytes(raw))
