"""Contratos SINTÉTICOS de E1-A.

No representan ninguna entidad, regla ni fuente corporativa. Solo sirven para
ejercitar el mecanismo de contratos, el hashing y la reproducibilidad. Los
contratos de dominio llegarán con los CRC validados.
"""

from __future__ import annotations

import pyarrow as pa

from nexus.contracts.model import Contract, Field

SYNTHETIC_INPUT = Contract(
    id="synthetic_input",
    version="1.0.0",
    description="Registro sintético normalizado (no comercial).",
    fields=(
        Field("record_id", pa.string()),
        Field("group_code", pa.string()),
        Field("quantity", pa.decimal128(18, 3)),
        Field("weight", pa.float64()),
        Field("event_date", pa.date32()),
    ),
    key=("record_id",),
)

SYNTHETIC_REJECTION = Contract(
    id="synthetic_rejection",
    version="1.0.0",
    description="Fila sintética rechazada durante la normalización, con su motivo.",
    fields=(
        Field("line_number", pa.int64()),
        Field("reason", pa.string()),
        Field("raw", pa.string()),
    ),
    key=("line_number",),
)

SYNTHETIC_OUTPUT = Contract(
    id="synthetic_output",
    version="1.0.0",
    description="Agregación sintética por grupo (no comercial).",
    fields=(
        Field("group_code", pa.string()),
        Field("record_count", pa.int64()),
        Field("quantity_total", pa.decimal128(38, 3)),
        Field("weight_total", pa.float64(), tolerance=1e-9),
        Field("first_date", pa.date32()),
        Field("last_date", pa.date32()),
    ),
    key=("group_code",),
)

ALL: tuple[Contract, ...] = (SYNTHETIC_INPUT, SYNTHETIC_REJECTION, SYNTHETIC_OUTPUT)
