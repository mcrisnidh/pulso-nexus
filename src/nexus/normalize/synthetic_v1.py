"""Normalizador SINTÉTICO v1: CSV sintético → ``synthetic_input@1.0.0`` + rechazos.

Es puro: recibe bytes y devuelve tablas, sin E/S, sin reloj y sin red. Solo aplica
reglas sintácticas y de tipo, no reglas comerciales. Toda fila inválida se registra
en los rechazos con su motivo; ninguna se descarta en silencio.

Invariante: ``filas de datos = aceptadas + rechazadas``.
"""

from __future__ import annotations

import csv
import datetime as dt
import decimal
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from nexus.contracts.synthetic import SYNTHETIC_INPUT, SYNTHETIC_REJECTION
from nexus.errors import NormalizationError

NAME = "synthetic_v1"
VERSION = "1.0.0"
HEADER = ("record_id", "group_code", "quantity", "weight", "event_date")

_RECORD_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DECIMAL = re.compile(r"^-?\d{1,15}(\.\d{1,3})?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_GROUP_LEN = 64


@dataclass(frozen=True)
class NormalizationResult:
    accepted: Any
    rejections: Any
    data_rows: int


class _RejectError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _parse_row(cells: list[str]) -> tuple[str, str, decimal.Decimal, float, dt.date]:
    if len(cells) != len(HEADER):
        raise _RejectError(f"column_count:{len(cells)}")
    record_id, group_code, quantity, weight, event_date = (_nfc(c.strip()) for c in cells)
    if not _RECORD_ID.match(record_id):
        raise _RejectError("invalid_record_id")
    if not group_code or len(group_code) > _MAX_GROUP_LEN or not group_code.isprintable():
        raise _RejectError("invalid_group_code")
    if not _DECIMAL.match(quantity):
        raise _RejectError("invalid_quantity")
    try:
        weight_value = float(weight)
    except ValueError as exc:
        raise _RejectError("invalid_weight") from exc
    if not math.isfinite(weight_value):
        raise _RejectError("non_finite_weight")
    if not _DATE.match(event_date):
        raise _RejectError("invalid_date")
    try:
        date_value = dt.date.fromisoformat(event_date)
    except ValueError as exc:
        raise _RejectError("invalid_date") from exc
    return record_id, group_code, decimal.Decimal(quantity), weight_value, date_value


def normalize(raw: bytes) -> NormalizationResult:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise NormalizationError(f"la captura no es UTF-8 válido: {exc}") from exc
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines()
    if not lines:
        raise NormalizationError("captura vacía")
    header = next(csv.reader([lines[0]], strict=True))
    if tuple(h.strip() for h in header) != HEADER:
        raise NormalizationError(f"cabecera inesperada: {header}")

    accepted: dict[str, list[Any]] = {name: [] for name in HEADER}
    rejected: dict[str, list[Any]] = {"line_number": [], "reason": [], "raw": []}
    seen: set[str] = set()
    data_rows = 0
    for index, line in enumerate(lines[1:], start=2):
        data_rows += 1
        try:
            if not line.strip():
                raise _RejectError("empty_line")
            try:
                cells = next(csv.reader([line], strict=True))
            except csv.Error as exc:
                raise _RejectError("malformed_csv") from exc
            row = _parse_row(cells)
            if row[0] in seen:
                raise _RejectError("duplicate_key")
        except _RejectError as rej:
            rejected["line_number"].append(index)
            rejected["reason"].append(rej.reason)
            rejected["raw"].append(_nfc(line))
            continue
        seen.add(row[0])
        for name, value in zip(HEADER, row, strict=True):
            accepted[name].append(value)

    accepted_table = SYNTHETIC_INPUT.canonicalize(
        pa.table(accepted, schema=SYNTHETIC_INPUT.schema())
    )
    rejection_table = SYNTHETIC_REJECTION.canonicalize(
        pa.table(rejected, schema=SYNTHETIC_REJECTION.schema())
    )
    if accepted_table.num_rows + rejection_table.num_rows != data_rows:  # pragma: no cover
        raise NormalizationError("violación de conservación de filas")
    return NormalizationResult(accepted_table, rejection_table, data_rows)
