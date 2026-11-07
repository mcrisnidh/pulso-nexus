"""Contratos de fuentes CORE y sus adaptadores (C1 29 OERAS/Portal, C2 FG, C3 embarques).

Cada archivo entregado se lee con la lectura del Pilot-20 (SHA-256, línea física por fila) y se
valida contra su contrato. Un archivo inválido nunca se usa: queda registrado con sus motivos.
Desconocido nunca es cero. Los embarques se leen solo de la fuente de embarques físicos.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any

from nexus.planning.adapters.sources import PortalLine
from nexus.planning.adapters.tables import SourceSpec, read_table
from nexus.planning.model import SourceRef, parse_decimal

SOURCE_IDS = ("oeras_29", "portal_cat", "fg_inventory", "shipments")
# Δ3: fuentes cuyo documento puede declarar solo una FECHA (sin hora verificable). FG y embarques
# exigen siempre un instante completo (corte y ventana).
DATE_PRECISION_SOURCES = frozenset({"oeras_29", "portal_cat"})
_ISO_DATE_LEN = 10
SHIP, REVERSAL = "SHIP", "REVERSAL"


def layout() -> dict[str, Any]:
    text = resources.files("nexus.core").joinpath("layouts/core_v1.json").read_text("utf-8")
    data: dict[str, Any] = json.loads(text)["sources"]
    return data


def parse_instant(raw: str) -> str | None:
    """Instante ISO-8601 UTC (``YYYY-MM-DDTHH:MM:SSZ``) normalizado; ``None`` si no es válido."""
    text = raw.strip()
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_source_instant(raw: str, allow_date: bool) -> str | None:
    """Instante completo (``…Z``) o, si la fuente lo admite, una FECHA de documento
    ``YYYY-MM-DD`` (precisión fecha: no se le inventa hora)."""
    full = parse_instant(raw)
    if full is not None or not allow_date:
        return full
    text = raw.strip()
    return parse_date(text) if len(text) == _ISO_DATE_LEN else None


def instant_precision(instant: str | None) -> str | None:
    if instant is None:
        return None
    return "date" if len(instant) == _ISO_DATE_LEN else "second"


def parse_date(raw: str) -> str | None:
    try:
        return dt.date.fromisoformat(raw.strip()).isoformat()
    except ValueError:
        return None


@dataclass(frozen=True)
class OerasLine:
    item: str
    customer_key: str  # exacta, con espacios significativos
    customer_name: str
    order: str  # conserva ceros iniciales
    line: str
    due_date: str | None
    qty_ordered: Decimal | None
    qty_open: Decimal | None
    ref: SourceRef

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.item, self.customer_key, self.order, self.line)


@dataclass(frozen=True)
class FgRecord:
    item: str
    stockroom: str
    qty_physical: Decimal | None
    qty_reserved: Decimal | None
    ref: SourceRef


@dataclass(frozen=True)
class Shipment:
    movement_id: str
    item: str
    customer_key: str
    order: str
    line: str
    qty: Decimal | None
    movement_ts: str | None
    movement_type: str
    reverses: str
    uom: str
    ref: SourceRef


@dataclass
class ParsedSource:
    """Un archivo de fuente leído y validado contra su contrato."""

    source_id: str
    file_name: str
    sha256: str | None
    instant: str | None
    source_system: str | None
    valid: bool
    errors: list[str] = field(default_factory=list)
    records: list[Any] = field(default_factory=list)

    @property
    def precision(self) -> str | None:
        return instant_precision(self.instant)

    def describe(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "file": self.file_name,
            "sha256": self.sha256,
            "instant": self.instant,
            "source_system": self.source_system,
            "valid": self.valid,
            "errors": list(self.errors),
            "records": len(self.records),
        }


def _spec(source_id: str, file_name: str) -> SourceSpec:
    contract = layout()[source_id]
    return SourceSpec(
        source_id=source_id,
        path=file_name,
        delimiter=",",
        granularity=str(contract["role"]),
        declared_cut=None,
        required_columns=tuple(contract["required_columns"]),
        role="input",
    )


def read_source(source_id: str, path: Path) -> ParsedSource:
    """Lee y valida un archivo. Nunca lanza por contenido inválido: devuelve ``valid=False``."""
    if source_id not in SOURCE_IDS:
        raise ValueError(f"fuente desconocida: {source_id}")
    table = read_table(path.parent, _spec(source_id, path.name))
    parsed = ParsedSource(source_id, path.name, table.sha256, None, None, False)
    if not table.available:
        parsed.errors.append(table.reason or "fuente no disponible")
        return parsed
    if not table.rows:
        parsed.errors.append("archivo sin filas")
        return parsed
    instant_col = str(layout()[source_id]["instant_column"])
    instants = {r[instant_col] for r in table.rows}
    systems = {r["source_system"] for r in table.rows}
    allow_date = source_id in DATE_PRECISION_SOURCES
    first = parse_source_instant(next(iter(instants)), allow_date)
    if len(instants) != 1 or first is None:
        parsed.errors.append(f"{instant_col} ausente, ilegible o no único en el archivo")
    else:
        parsed.instant = first
    if len(systems) != 1 or not next(iter(systems)).strip():
        parsed.errors.append("source_system ausente o no único en el archivo")
    else:
        parsed.source_system = next(iter(systems)).strip()
    refs = [
        SourceRef(
            source_id, path.name, table.sha256 or "", r.ref.line, parsed.instant, r.ref.granularity
        )
        for r in table.rows
    ]
    parser = {
        "oeras_29": _oeras,
        "portal_cat": _portal,
        "fg_inventory": _fg,
        "shipments": _shipments,
    }[source_id]
    parser(table.rows, refs, parsed)
    parsed.valid = not parsed.errors
    if not parsed.valid:
        parsed.records = []
    return parsed


def _oeras(rows: list[Any], refs: list[SourceRef], out: ParsedSource) -> None:
    seen: set[tuple[str, str, str, str]] = set()
    for r, ref in zip(rows, refs, strict=True):
        due = parse_date(r["due_date"])
        if due is None:
            out.errors.append(f"línea {ref.line}: due_date ilegible {r['due_date']!r}")
        rec = OerasLine(
            r["item"],
            r["customer_key_exact"],
            r["customer_name"],
            r["order"],
            r["line"],
            due,
            parse_decimal(r["qty_ordered"]),
            parse_decimal(r["qty_open"]),
            ref,
        )
        if not rec.item.strip() or not rec.order.strip():
            out.errors.append(f"línea {ref.line}: ITEM o pedido vacío")
        if rec.identity in seen:
            out.errors.append(
                f"línea {ref.line}: identidad duplicada (ITEM, cliente, pedido, línea)"
            )
        seen.add(rec.identity)
        out.records.append(rec)


def _portal(rows: list[Any], refs: list[SourceRef], out: ParsedSource) -> None:
    seen: set[str] = set()
    for r, ref in zip(rows, refs, strict=True):
        if r["line_id"] in seen or not r["line_id"].strip():
            out.errors.append(f"línea {ref.line}: line_id vacío o duplicado")
        seen.add(r["line_id"])
        out.records.append(
            PortalLine(
                item=r["item"],
                item_portal=r["item_portal"],
                line_id=r["line_id"],
                status=r["status"],
                ordered_raw=r["ordered_raw"],
                shipped_raw=r["shipped_raw"],
                ship_date=parse_date(r["ship_date"]),
                delivery_date=parse_date(r["delivery_date"]),
                map_status="",
                provided_decision="",
                ref=ref,
            )
        )


def _fg(rows: list[Any], refs: list[SourceRef], out: ParsedSource) -> None:
    seen: set[tuple[str, str]] = set()
    for r, ref in zip(rows, refs, strict=True):
        key = (r["item"], r["stockroom"])
        if key in seen:
            out.errors.append(f"línea {ref.line}: (ITEM, stockroom) duplicado")
        seen.add(key)
        out.records.append(
            FgRecord(
                r["item"],
                r["stockroom"],
                parse_decimal(r["qty_physical"]),
                parse_decimal(r["qty_reserved"]),
                ref,
            )
        )


def _shipments(rows: list[Any], refs: list[SourceRef], out: ParsedSource) -> None:
    ids: dict[str, Shipment] = {}
    for r, ref in zip(rows, refs, strict=True):
        rec = Shipment(
            r["movement_id"],
            r["item"],
            r["customer_key_exact"],
            r["order"],
            r["line"],
            parse_decimal(r["qty"]),
            parse_instant(r["movement_ts"]),
            r["movement_type"],
            r["reverses_movement_id"],
            r["uom"],
            ref,
        )
        if not rec.movement_id.strip() or rec.movement_id in ids:
            out.errors.append(f"línea {ref.line}: movement_id vacío o duplicado")
        if rec.movement_type not in (SHIP, REVERSAL):
            out.errors.append(f"línea {ref.line}: movement_type no físico {rec.movement_type!r}")
        if rec.qty is None or rec.qty <= 0:
            out.errors.append(f"línea {ref.line}: qty desconocida o no positiva")
        if rec.movement_ts is None:
            out.errors.append(f"línea {ref.line}: movement_ts ilegible")
        if rec.uom != "EA":
            out.errors.append(f"línea {ref.line}: UOM {rec.uom!r} sin conversión aprobada")
        ids[rec.movement_id] = rec
        out.records.append(rec)
    for rec in out.records:
        if rec.movement_type == REVERSAL:
            target = ids.get(rec.reverses)
            if target is None or target.movement_type != SHIP:
                out.errors.append(f"reversa {rec.movement_id} sin embarque original válido")
            elif target.item != rec.item:
                out.errors.append(f"reversa {rec.movement_id} con ITEM distinto del original")
        elif rec.reverses.strip():
            out.errors.append(f"embarque {rec.movement_id} con reverses_movement_id")
