"""Lectura genérica de tablas delimitadas con procedencia por fila."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus.planning.model import SourceRef

LAYOUT_SCHEMA = "nexus.planning.layout/1"


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    path: str  # relativa al directorio del dataset
    delimiter: str
    granularity: str
    declared_cut: str | None  # corte declarado por el proveedor del dato cuando no viene por fila
    required_columns: tuple[str, ...]
    role: str  # "input" | "oracle"


@dataclass(frozen=True)
class Row:
    values: dict[str, str]
    ref: SourceRef

    def __getitem__(self, key: str) -> str:
        return self.values[key]

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)


@dataclass
class SourceTable:
    spec: SourceSpec
    available: bool
    reason: str | None = None
    sha256: str | None = None
    rows: list[Row] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        return {
            "source_id": self.spec.source_id,
            "path": self.spec.path,
            "role": self.spec.role,
            "granularity": self.spec.granularity,
            "declared_cut": self.spec.declared_cut,
            "available": self.available,
            "reason": self.reason,
            "sha256": self.sha256,
            "rows": len(self.rows),
        }


def load_layout(path: Path) -> dict[str, SourceSpec]:
    data = json.loads(path.read_text("utf-8"))
    if data.get("schema") != LAYOUT_SCHEMA:
        raise ValueError(f"esquema de layout no soportado: {data.get('schema')!r}")
    specs = {}
    for source_id, s in data["sources"].items():
        specs[source_id] = SourceSpec(
            source_id=source_id,
            path=str(s["path"]),
            delimiter=str(s.get("delimiter", ",")),
            granularity=str(s["granularity"]),
            declared_cut=s.get("declared_cut"),
            required_columns=tuple(s.get("required_columns", [])),
            role=str(s.get("role", "input")),
        )
    return specs


def read_table(root: Path, spec: SourceSpec) -> SourceTable:
    path = root / spec.path
    if not path.is_file():
        return SourceTable(spec, available=False, reason=f"archivo ausente: {spec.path}")
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return SourceTable(spec, False, f"codificación no UTF-8: {exc}", sha)
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=spec.delimiter)
    header = reader.fieldnames or []
    missing = [c for c in spec.required_columns if c not in header]
    if missing:
        return SourceTable(spec, False, f"columnas requeridas ausentes: {missing}", sha)
    rows = []
    for record in reader:
        values = {k: (v if v is not None else "") for k, v in record.items() if k is not None}
        ref = SourceRef(
            source_id=spec.source_id,
            file=spec.path,
            sha256=sha,
            line=reader.line_num,
            cut=spec.declared_cut,
            granularity=spec.granularity,
        )
        rows.append(Row(values, ref))
    return SourceTable(spec, True, None, sha, rows)


def with_cut(row: Row, cut: str | None) -> Row:
    """Misma fila con el corte resuelto por fila (p. ej., fecha del snapshot OE965)."""
    ref = SourceRef(
        row.ref.source_id, row.ref.file, row.ref.sha256, row.ref.line, cut, row.ref.granularity
    )
    return Row(row.values, ref)
