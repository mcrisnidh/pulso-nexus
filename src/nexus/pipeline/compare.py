"""Comparador de reproducibilidad por niveles.

- L3: bytes idénticos (``content_sha256``).
- L2: valores idénticos bit a bit (``logical_hash``).
- L1: equivalencia lógica. Mismo conjunto de claves; igualdad exacta para enteros,
  decimales, cadenas y fechas; coma flotante dentro de la tolerancia absoluta que
  declara el contrato (``None`` = exacta).
- NONE: no equivalentes. Se informa la primera discrepancia en orden determinista.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from nexus.contracts.model import Contract

LEVELS = ("NONE", "L1", "L2", "L3")


def level_rank(level: str) -> int:
    return LEVELS.index(level)


@dataclass(frozen=True)
class Side:
    contract: Contract
    table: Any
    content_sha256: str
    logical_hash: str


@dataclass(frozen=True)
class Comparison:
    level: str
    discrepancy: str | None
    per_artifact: dict[str, str]


def _values_equal(field_type: Any, tolerance: float | None, a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if pa.types.is_floating(field_type):
        fa, fb = float(a), float(b)
        if math.isnan(fa) or math.isnan(fb):
            return math.isnan(fa) and math.isnan(fb)
        if tolerance is None:
            return fa == fb
        return abs(fa - fb) <= tolerance
    return bool(a == b)


def _rows_by_key(contract: Contract, table: Any) -> dict[tuple[Any, ...], dict[str, Any]]:
    rows = table.to_pylist()
    return {tuple(r[k] for k in contract.key): r for r in rows}


def first_l1_discrepancy(contract: Contract, expected: Any, actual: Any) -> str | None:
    """Devuelve la primera discrepancia L1 o ``None`` si son lógicamente equivalentes."""
    exp_rows = _rows_by_key(contract, expected)
    act_rows = _rows_by_key(contract, actual)
    for key in sorted(set(exp_rows) | set(act_rows), key=repr):
        if key not in act_rows:
            return f"{contract.ref}: falta la clave {key} en el resultado"
        if key not in exp_rows:
            return f"{contract.ref}: clave adicional {key} en el resultado"
        for f in contract.fields:
            a, b = exp_rows[key][f.name], act_rows[key][f.name]
            if not _values_equal(f.type, f.tolerance, a, b):
                tol = f" (tolerancia {f.tolerance})" if f.tolerance is not None else ""
                return (
                    f"{contract.ref}: clave {key}, columna {f.name}: "
                    f"esperado={a!r} obtenido={b!r}{tol}"
                )
    return None


def compare(expected: Sequence[Side], actual: Sequence[Side]) -> Comparison:
    exp = {s.contract.ref: s for s in expected}
    act = {s.contract.ref: s for s in actual}
    if set(exp) != set(act):
        return Comparison(
            "NONE",
            f"conjunto de artefactos distinto: esperado={sorted(exp)} obtenido={sorted(act)}",
            {},
        )
    if not exp:
        return Comparison("NONE", "no hay artefactos que comparar", {})
    per: dict[str, str] = {}
    notes: dict[str, str] = {}
    for ref in sorted(exp):
        e, a = exp[ref], act[ref]
        if e.content_sha256 == a.content_sha256:
            per[ref] = "L3"
            continue
        if e.logical_hash == a.logical_hash:
            per[ref] = "L2"
            notes.setdefault("L2", f"{ref}: bytes distintos con valores idénticos")
            continue
        issue = first_l1_discrepancy(e.contract, e.table, a.table)
        if issue is None:
            per[ref] = "L1"
            notes.setdefault("L1", f"{ref}: valores distintos dentro de la tolerancia declarada")
        else:
            per[ref] = "NONE"
            notes.setdefault("NONE", issue)
    overall = min(per.values(), key=level_rank)
    return Comparison(overall, notes.get(overall), per)
