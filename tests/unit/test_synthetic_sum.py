"""Motor sintético: determinismo, as_of inyectado y parámetros (sin reglas comerciales)."""

from __future__ import annotations

import datetime as dt
import math
import random
from decimal import Decimal

import pyarrow as pa
import pytest

from nexus.contracts.synthetic import SYNTHETIC_INPUT, SYNTHETIC_OUTPUT
from nexus.engines.synthetic_sum import ENGINE
from nexus.errors import ConfigurationError, ContractError

REF_IN = SYNTHETIC_INPUT.ref
REF_OUT = SYNTHETIC_OUTPUT.ref


def table(rows: list[tuple[str, str, str, float, dt.date]]) -> pa.Table:
    cols = list(zip(*rows, strict=True))
    return SYNTHETIC_INPUT.canonicalize(
        pa.table(
            {
                "record_id": list(cols[0]),
                "group_code": list(cols[1]),
                "quantity": [Decimal(q) for q in cols[2]],
                "weight": list(cols[3]),
                "event_date": list(cols[4]),
            },
            schema=SYNTHETIC_INPUT.schema(),
        )
    )


ROWS = [
    ("r1", "A", "1.500", 1e16, dt.date(2026, 1, 1)),
    ("r2", "A", "2.250", 1.0, dt.date(2026, 1, 5)),
    ("r3", "A", "-0.750", -1e16, dt.date(2026, 2, 1)),
    ("r4", "B", "10.000", 0.1, dt.date(2026, 3, 1)),
    ("r5", "B", "0.001", 0.2, dt.date(2026, 12, 31)),
]


def test_expected_synthetic_output() -> None:
    out = ENGINE.run({REF_IN: table(ROWS)}, {}, "2026-12-31T23:59:59Z")[REF_OUT]
    rows = {r["group_code"]: r for r in out.to_pylist()}
    assert rows["A"]["record_count"] == 3
    assert rows["A"]["quantity_total"] == Decimal("3.000")
    assert rows["A"]["weight_total"] == 1.0  # fsum exacto: 1e16 + 1 - 1e16
    assert rows["A"]["first_date"] == dt.date(2026, 1, 1)
    assert rows["A"]["last_date"] == dt.date(2026, 2, 1)
    assert rows["B"]["quantity_total"] == Decimal("10.001")
    assert rows["B"]["weight_total"] == math.fsum([0.1, 0.2])
    SYNTHETIC_OUTPUT.validate(out)


def test_as_of_is_injected_not_read_from_clock() -> None:
    out = ENGINE.run({REF_IN: table(ROWS)}, {}, "2026-01-31T00:00:00Z")[REF_OUT]
    assert out.to_pylist() == [
        {
            "group_code": "A",
            "record_count": 2,
            "quantity_total": Decimal("3.750"),
            "weight_total": 1e16 + 1.0,
            "first_date": dt.date(2026, 1, 1),
            "last_date": dt.date(2026, 1, 5),
        }
    ]
    # Una zona horaria distinta en as_of se convierte a UTC antes de cortar.
    shifted = ENGINE.run({REF_IN: table(ROWS)}, {}, "2026-01-04T23:00:00-05:00")[REF_OUT]
    assert shifted.column("record_count").to_pylist() == [2]


def test_min_quantity_param() -> None:
    out = ENGINE.run({REF_IN: table(ROWS)}, {"min_quantity": "2"}, "2026-12-31T00:00:00Z")
    assert out[REF_OUT].column("group_code").to_pylist() == ["A", "B"]
    assert out[REF_OUT].column("record_count").to_pylist() == [1, 1]


def test_order_and_thread_independence() -> None:
    rng = random.Random(7)
    rows = [
        (
            f"r{i}",
            f"G{i % 5}",
            f"{rng.randrange(-9999, 99999) / 1000:.3f}",
            rng.uniform(-1e9, 1e9),
            dt.date(2026, 1, 1) + dt.timedelta(days=i % 300),
        )
        for i in range(3000)
    ]
    shuffled = list(rows)
    rng.shuffle(shuffled)
    results = []
    for data, threads in ((rows, 1), (shuffled, 8)):
        pa.set_cpu_count(threads)
        out = ENGINE.run({REF_IN: table(data)}, {}, "2026-12-31T00:00:00Z")[REF_OUT]
        results.append(SYNTHETIC_OUTPUT.logical_hash(out))
    pa.set_cpu_count(1)
    assert results[0] == results[1]


def test_empty_selection_produces_valid_empty_output() -> None:
    out = ENGINE.run({REF_IN: table(ROWS)}, {}, "2025-01-01T00:00:00Z")[REF_OUT]
    assert out.num_rows == 0
    SYNTHETIC_OUTPUT.validate(out)


@pytest.mark.parametrize(
    ("params", "as_of"),
    [
        ({"unknown": "1"}, "2026-01-01T00:00:00Z"),
        ({"min_quantity": "abc"}, "2026-01-01T00:00:00Z"),
        ({"min_quantity": "Infinity"}, "2026-01-01T00:00:00Z"),
        ({}, "not-a-date"),
        ({}, "2026-01-01T00:00:00"),
    ],
)
def test_invalid_configuration(params: dict[str, str], as_of: str) -> None:
    with pytest.raises(ConfigurationError):
        ENGINE.run({REF_IN: table(ROWS)}, params, as_of)


def test_wrong_inputs_rejected() -> None:
    with pytest.raises(ConfigurationError):
        ENGINE.run({}, {}, "2026-01-01T00:00:00Z")
    bad = pa.table({"x": [1]})
    with pytest.raises(ContractError):
        ENGINE.run({REF_IN: bad}, {}, "2026-01-01T00:00:00Z")


def test_engine_satisfies_protocol() -> None:
    from nexus.engines.base import Engine

    assert isinstance(ENGINE, Engine)
    assert ENGINE.consumes == (REF_IN,)
    assert ENGINE.produces == (REF_OUT,)
