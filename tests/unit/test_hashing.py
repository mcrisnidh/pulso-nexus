"""A8 — propiedades de logical_hash y utilidades de hashing."""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import io
import math
import struct
import unicodedata

import pyarrow as pa
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nexus.errors import ContractError
from nexus.hashing import canonical_json, logical_hash, sha256_bytes, sha256_stream

COLUMNS = ("k", "s", "f", "d", "t", "b")
SCHEMA = pa.schema(
    [
        pa.field("k", pa.int64()),
        pa.field("s", pa.string(), nullable=True),
        pa.field("f", pa.float64()),
        pa.field("d", pa.decimal128(18, 3)),
        pa.field("t", pa.date32()),
        pa.field("b", pa.bool_()),
    ]
)

row_strategy = st.tuples(
    st.integers(min_value=-(2**62), max_value=2**62),
    st.one_of(st.none(), st.text(max_size=12)),
    st.floats(allow_nan=False, allow_infinity=True, width=64),
    st.decimals(
        min_value=-(10**14), max_value=10**14, places=3, allow_nan=False, allow_infinity=False
    ),
    st.dates(min_value=dt.date(1970, 1, 1), max_value=dt.date(2100, 12, 31)),
    st.booleans(),
)


def make_table(rows: list[tuple[object, ...]]) -> pa.Table:
    cols = list(zip(*rows, strict=True)) if rows else [[] for _ in COLUMNS]
    return pa.table({n: list(c) for n, c in zip(COLUMNS, cols, strict=True)}, schema=SCHEMA)


def h(rows: list[tuple[object, ...]]) -> str:
    return logical_hash(make_table(rows), COLUMNS, "test@1")


@settings(max_examples=150, deadline=None)
@given(st.lists(row_strategy, min_size=1, max_size=20), st.randoms(use_true_random=False))
def test_invariant_under_row_permutation(rows: list[tuple[object, ...]], rnd: object) -> None:
    shuffled = list(rows)
    rnd.shuffle(shuffled)  # type: ignore[attr-defined]
    assert h(rows) == h(shuffled)


@settings(max_examples=150, deadline=None)
@given(st.lists(row_strategy, min_size=1, max_size=10), st.data())
def test_sensitive_to_one_ulp(rows: list[tuple[object, ...]], data: st.DataObject) -> None:
    index = data.draw(st.integers(min_value=0, max_value=len(rows) - 1))
    original = rows[index]
    f = float(original[2])  # type: ignore[arg-type]
    if not math.isfinite(f):
        return
    bumped = math.nextafter(f, math.inf)
    mutated = list(rows)
    mutated[index] = (*original[:2], bumped, *original[3:])
    assert h(rows) != h(mutated)


@settings(max_examples=100, deadline=None)
@given(st.lists(row_strategy, min_size=1, max_size=10), row_strategy)
def test_sensitive_to_extra_and_missing_rows(
    rows: list[tuple[object, ...]], extra: tuple[object, ...]
) -> None:
    assert h(rows) != h([*rows, extra])
    assert h(rows) != h(rows[:-1])


def test_sensitive_to_type_change() -> None:
    t64 = pa.table({"x": pa.array([1, 2], pa.int64())})
    t32 = pa.table({"x": pa.array([1, 2], pa.int32())})
    assert logical_hash(t64, ["x"], "c@1") != logical_hash(t32, ["x"], "c@1")
    d18 = pa.table({"x": pa.array([decimal.Decimal("1.000")], pa.decimal128(18, 3))})
    d38 = pa.table({"x": pa.array([decimal.Decimal("1.000")], pa.decimal128(38, 3))})
    assert logical_hash(d18, ["x"], "c@1") != logical_hash(d38, ["x"], "c@1")
    s = pa.table({"x": pa.array(["1"], pa.string())})
    i = pa.table({"x": pa.array([1], pa.int64())})
    assert logical_hash(s, ["x"], "c@1") != logical_hash(i, ["x"], "c@1")


def test_sensitive_to_domain_and_column_selection() -> None:
    t = pa.table({"x": [1], "y": [2]})
    assert logical_hash(t, ["x"], "a@1") != logical_hash(t, ["x"], "b@1")
    assert logical_hash(t, ["x"], "a@1") != logical_hash(t, ["x", "y"], "a@1")
    assert logical_hash(t, ["x", "y"], "a@1") != logical_hash(t, ["y", "x"], "a@1")


def test_physical_column_order_irrelevant() -> None:
    t1 = pa.table({"x": [1, 2], "y": ["a", "b"]})
    t2 = pa.table({"y": ["a", "b"], "x": [1, 2]})
    assert logical_hash(t1, ["x", "y"], "c@1") == logical_hash(t2, ["x", "y"], "c@1")


def test_nfc_applied() -> None:
    text = "ÁREA-Ñ"
    nfd = unicodedata.normalize("NFD", text)
    nfc = unicodedata.normalize("NFC", text)
    assert nfd != nfc
    a = pa.table({"x": [nfd]})
    b = pa.table({"x": [nfc]})
    assert logical_hash(a, ["x"], "c@1") == logical_hash(b, ["x"], "c@1")


def test_nan_canonical_and_null_distinct() -> None:
    nan_a = struct.unpack(">d", bytes.fromhex("7ff8000000000001"))[0]
    nan_b = float("nan")
    a = pa.table({"x": pa.array([nan_a], pa.float64())})
    b = pa.table({"x": pa.array([nan_b], pa.float64())})
    assert logical_hash(a, ["x"], "c@1") == logical_hash(b, ["x"], "c@1")
    empty = pa.table({"x": pa.array([""], pa.string())})
    null = pa.table({"x": pa.array([None], pa.string())})
    assert logical_hash(empty, ["x"], "c@1") != logical_hash(null, ["x"], "c@1")


def test_negative_zero_is_distinct_bitwise() -> None:
    a = pa.table({"x": pa.array([0.0], pa.float64())})
    b = pa.table({"x": pa.array([-0.0], pa.float64())})
    assert logical_hash(a, ["x"], "c@1") != logical_hash(b, ["x"], "c@1")


def test_unsupported_type_and_missing_column() -> None:
    t = pa.table({"x": pa.array([b"bytes"], pa.binary())})
    with pytest.raises(ContractError):
        logical_hash(t, ["x"], "c@1")
    with pytest.raises(ContractError):
        logical_hash(t, ["nope"], "c@1")


def test_sha256_helpers_match_hashlib() -> None:
    data = bytes(range(256)) * 9000
    expected = hashlib.sha256(data).hexdigest()
    assert sha256_bytes(data) == expected
    assert sha256_stream(io.BytesIO(data)) == expected


def test_canonical_json_rejects_nan_and_is_stable() -> None:
    assert canonical_json({"b": 1, "a": "ñ"}) == '{"a":"ñ","b":1}'.encode()
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_empty_table_hash_is_stable() -> None:
    assert h([]) == h([])
