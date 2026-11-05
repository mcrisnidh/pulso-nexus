"""A7 — contratos: cumplimiento, huellas fijadas y cambios sin versión."""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

import pyarrow as pa
import pytest

from nexus.contracts.model import Contract, Field
from nexus.contracts.registry import ContractRegistry, default_registry, load_pinned_fingerprints
from nexus.contracts.synthetic import ALL, SYNTHETIC_INPUT, SYNTHETIC_OUTPUT
from nexus.errors import ContractError


def test_registry_verifies_pinned_fingerprints() -> None:
    registry = default_registry()
    assert registry.refs() == sorted(c.ref for c in ALL)
    assert set(load_pinned_fingerprints()) == {c.ref for c in ALL}


def _mutations(contract: Contract) -> list[Contract]:
    f0 = contract.fields[0]
    other = contract.fields[1]
    return [
        dataclasses.replace(contract, fields=(*contract.fields, Field("extra", pa.int64()))),
        dataclasses.replace(
            contract, fields=(dataclasses.replace(f0, type=pa.large_string()), *contract.fields[1:])
        ),
        dataclasses.replace(
            contract, fields=(f0, dataclasses.replace(other, nullable=True), *contract.fields[2:])
        ),
        dataclasses.replace(
            contract, fields=(f0, dataclasses.replace(other, name="renamed"), *contract.fields[2:])
        ),
        dataclasses.replace(contract, fields=(contract.fields[1], f0, *contract.fields[2:])),
        dataclasses.replace(contract, key=(f0.name, other.name)),
    ]


@pytest.mark.parametrize("index", range(6))
def test_schema_change_without_version_bump_fails(index: int) -> None:
    mutated = _mutations(SYNTHETIC_INPUT)[index]
    assert mutated.ref == SYNTHETIC_INPUT.ref
    registry = ContractRegistry(
        [mutated, *[c for c in ALL if c is not SYNTHETIC_INPUT]], load_pinned_fingerprints()
    )
    with pytest.raises(ContractError, match="sin subir la versión"):
        registry.verify()


def test_tolerance_change_without_version_bump_fails() -> None:
    fields = tuple(
        dataclasses.replace(f, tolerance=1e-3) if f.name == "weight_total" else f
        for f in SYNTHETIC_OUTPUT.fields
    )
    mutated = dataclasses.replace(SYNTHETIC_OUTPUT, fields=fields)
    registry = ContractRegistry(
        [mutated, *[c for c in ALL if c is not SYNTHETIC_OUTPUT]], load_pinned_fingerprints()
    )
    with pytest.raises(ContractError):
        registry.verify()


def test_new_version_must_be_pinned() -> None:
    bumped = dataclasses.replace(SYNTHETIC_INPUT, version="2.0.0")
    registry = ContractRegistry([*ALL, bumped], load_pinned_fingerprints())
    with pytest.raises(ContractError, match="no fijada"):
        registry.verify()


def test_registry_errors() -> None:
    with pytest.raises(ContractError):
        ContractRegistry([SYNTHETIC_INPUT, SYNTHETIC_INPUT], {})
    with pytest.raises(ContractError):
        default_registry().get("nope@1")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fields": (Field("a", pa.int64()), Field("a", pa.int64())), "key": ("a",)},
        {"fields": (Field("a", pa.int64()),), "key": ("b",)},
        {"fields": (Field("a", pa.int64()),), "key": ()},
        {"fields": (Field("a", pa.int64(), tolerance=0.1),), "key": ("a",)},
        {
            "fields": (Field("a", pa.int64()), Field("f", pa.float64(), tolerance=-1.0)),
            "key": ("a",),
        },
        {"fields": (Field("a", pa.int64(), nullable=True),), "key": ("a",)},
    ],
)
def test_invalid_contract_definitions(kwargs: dict[str, object]) -> None:
    with pytest.raises(ContractError):
        Contract(id="x", version="1.0.0", **kwargs)  # type: ignore[arg-type]


def _valid_input() -> pa.Table:
    return pa.table(
        {
            "record_id": ["b", "a"],
            "group_code": ["G", "G"],
            "quantity": [Decimal("1.000"), Decimal("2.000")],
            "weight": [1.0, 2.0],
            "event_date": [dt.date(2026, 1, 1), dt.date(2026, 1, 2)],
        },
        schema=SYNTHETIC_INPUT.schema(),
    )


def test_canonicalize_orders_by_key_and_validates() -> None:
    out = SYNTHETIC_INPUT.canonicalize(_valid_input())
    assert out.column("record_id").to_pylist() == ["a", "b"]
    SYNTHETIC_INPUT.validate(out)
    assert SYNTHETIC_INPUT.field("weight").type == pa.float64()
    with pytest.raises(ContractError):
        SYNTHETIC_INPUT.field("nope")


def test_validation_failures() -> None:
    t = _valid_input()
    with pytest.raises(ContractError, match="esquema"):
        SYNTHETIC_INPUT.validate(
            t.select(["group_code", "record_id", "quantity", "weight", "event_date"])
        )
    dup = pa.concat_tables([t, t])
    with pytest.raises(ContractError, match="duplicada"):
        SYNTHETIC_INPUT.canonicalize(dup)
    with pytest.raises(ContractError, match="ausentes"):
        SYNTHETIC_INPUT.canonicalize(t.drop_columns(["weight"]))
    bad_type = t.set_column(3, "weight", pa.array(["x", "y"]))
    with pytest.raises(ContractError, match="incompatibles"):
        SYNTHETIC_INPUT.canonicalize(bad_type)
    nullable_schema = pa.schema(
        [pa.field(f.name, f.type, nullable=True) for f in SYNTHETIC_INPUT.fields]
    )
    with_null = pa.table(
        {
            **{n: t.column(n) for n in t.column_names},
            "group_code": pa.array([None, "G"], pa.string()),
        },
        schema=nullable_schema,
    )
    with pytest.raises(ContractError):
        SYNTHETIC_INPUT.canonicalize(with_null)
