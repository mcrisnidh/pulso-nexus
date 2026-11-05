"""Motor SINTÉTICO ``synthetic_sum``: agregación por grupo. **No es una regla comercial.**

Existe solo para ejercitar el pipeline, el determinismo y la reproducibilidad en
E1-A. Sus reglas, inventadas y sin significado de negocio, son:

- Se consideran los registros con ``event_date <= fecha(as_of)``.
- Opcionalmente, solo los registros con ``quantity >= min_quantity`` (parámetro).
- Por ``group_code`` se calculan el conteo, la suma decimal exacta de
  ``quantity``, la suma de ``weight`` y las fechas mínima y máxima.

Determinismo:
- La suma decimal la hace Arrow de forma exacta (decimal128), así que es
  independiente del orden.
- La suma en coma flotante usa ``math.fsum`` (redondeo exacto), que es independiente
  del orden y del número de hilos.
- El agrupamiento de Arrow puede usar hilos; el resultado se canonicaliza
  ordenando por clave.
"""

from __future__ import annotations

import datetime as dt
import decimal
import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from nexus.contracts.synthetic import SYNTHETIC_INPUT, SYNTHETIC_OUTPUT
from nexus.errors import ConfigurationError

NAME = "synthetic_sum"
VERSION = "1.0.0"
_ALLOWED_PARAMS = {"min_quantity"}


def _as_of_date(as_of: str) -> dt.date:
    try:
        moment = dt.datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigurationError(f"as_of inválido: {as_of!r}") from exc
    if moment.tzinfo is None:
        raise ConfigurationError("as_of debe incluir zona horaria explícita (p. ej., Z)")
    return moment.astimezone(dt.UTC).date()


def _min_quantity(params: Mapping[str, str]) -> decimal.Decimal | None:
    unknown = set(params) - _ALLOWED_PARAMS
    if unknown:
        raise ConfigurationError(f"parámetros desconocidos: {sorted(unknown)}")
    raw = params.get("min_quantity")
    if raw is None:
        return None
    try:
        value = decimal.Decimal(raw)
    except decimal.InvalidOperation as exc:
        raise ConfigurationError(f"min_quantity inválido: {raw!r}") from exc
    if not value.is_finite():
        raise ConfigurationError("min_quantity debe ser finito")
    return value


class SyntheticSumEngine:
    name = NAME
    version = VERSION
    consumes = (SYNTHETIC_INPUT.ref,)
    produces = (SYNTHETIC_OUTPUT.ref,)

    def run(
        self,
        inputs: Mapping[str, Any],
        params: Mapping[str, str],
        as_of: str,
    ) -> dict[str, Any]:
        if set(inputs) != set(self.consumes):
            raise ConfigurationError(
                f"entradas esperadas {self.consumes}, recibidas {sorted(inputs)}"
            )
        table = inputs[SYNTHETIC_INPUT.ref]
        SYNTHETIC_INPUT.validate(table)

        cutoff = _as_of_date(as_of)
        mask = pc.less_equal(table.column("event_date"), pa.scalar(cutoff, pa.date32()))
        minimum = _min_quantity(params)
        if minimum is not None:
            mask = pc.and_(
                mask,
                pc.greater_equal(
                    table.column("quantity"),
                    pa.scalar(minimum, table.schema.field("quantity").type),
                ),
            )
        selected = table.filter(mask)

        grouped = selected.group_by("group_code", use_threads=True).aggregate(
            [
                ("record_id", "count"),
                ("quantity", "sum"),
                ("event_date", "min"),
                ("event_date", "max"),
            ]
        )

        weights: dict[str, list[float]] = defaultdict(list)
        for group, weight in zip(
            selected.column("group_code").to_pylist(),
            selected.column("weight").to_pylist(),
            strict=True,
        ):
            weights[group].append(weight)
        groups = grouped.column("group_code").to_pylist()
        weight_totals = [math.fsum(weights[g]) for g in groups]

        output = pa.table(
            {
                "group_code": grouped.column("group_code"),
                "record_count": pc.cast(grouped.column("record_id_count"), pa.int64()),
                "quantity_total": pc.cast(grouped.column("quantity_sum"), pa.decimal128(38, 3)),
                "weight_total": pa.array(weight_totals, pa.float64()),
                "first_date": grouped.column("event_date_min"),
                "last_date": grouped.column("event_date_max"),
            }
        )
        return {SYNTHETIC_OUTPUT.ref: SYNTHETIC_OUTPUT.canonicalize(output)}


ENGINE = SyntheticSumEngine()
