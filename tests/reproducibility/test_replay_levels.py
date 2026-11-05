"""A11 / A12 — replay por niveles, fallos cerrados y prueba de mutación del comparador."""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pyarrow as pa
import pytest

from conftest import AS_OF, force_rmtree, object_path, run_cli, tamper
from nexus.contracts.synthetic import SYNTHETIC_INPUT, SYNTHETIC_OUTPUT
from nexus.engines import synthetic_sum
from nexus.errors import IntegrityError, NotReproducibleError
from nexus.pipeline import compute
from nexus.pipeline import replay as replay_mod
from nexus.pipeline.compare import Side, compare, first_l1_discrepancy
from nexus.pipeline.replay import replay
from nexus.pipeline.run import execute
from nexus.storage.object_store import LocalObjectStore


@pytest.fixture
def record(store: LocalObjectStore, snapshot_id: str) -> dict[str, Any]:
    return execute(store, snapshot_id=snapshot_id, as_of=AS_OF)


def test_replay_same_environment_is_l3(store: LocalObjectStore, record: dict[str, Any]) -> None:
    report = replay(store, record["run_id"])
    assert report.level == "L3"
    assert report.discrepancy is None
    assert report.environment_match and report.code_match
    assert report.replayed_result_digest == record["result_digest"]


def test_writer_change_degrades_to_l2(
    store: LocalObjectStore, record: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(compute.PARQUET_WRITER_OPTIONS, "compression_level", 9)
    report = replay(store, record["run_id"])
    assert report.level == "L2"
    assert report.discrepancy is not None and "bytes distintos" in report.discrepancy
    assert report.replayed_result_digest == record["result_digest"]


def _perturb_engine(monkeypatch: pytest.MonkeyPatch, change: Callable[[float], float]) -> None:
    original = synthetic_sum.SyntheticSumEngine.run

    def perturbed(self: Any, inputs: Any, params: Any, as_of: str) -> dict[str, Any]:
        out = original(self, inputs, params, as_of)
        table = out[SYNTHETIC_OUTPUT.ref]
        idx = table.schema.get_field_index("weight_total")
        values = [change(v) for v in table.column("weight_total").to_pylist()]
        shifted = pa.array(values, pa.float64())
        return {SYNTHETIC_OUTPUT.ref: table.set_column(idx, "weight_total", shifted)}

    monkeypatch.setattr(synthetic_sum.SyntheticSumEngine, "run", perturbed)


def test_float_change_within_tolerance_is_l1(
    store: LocalObjectStore, record: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # +1 ulp solo donde el ulp es menor que la tolerancia absoluta (1e-9).
    _perturb_engine(monkeypatch, lambda v: math.nextafter(v, math.inf) if math.ulp(v) < 1e-9 else v)
    report = replay(store, record["run_id"])
    assert report.level == "L1"
    assert report.per_artifact[SYNTHETIC_OUTPUT.ref] == "L1"
    assert report.per_artifact[SYNTHETIC_INPUT.ref] == "L3"


def test_float_change_outside_tolerance_is_none_with_first_discrepancy(
    store: LocalObjectStore, record: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _perturb_engine(monkeypatch, lambda v: v + 1e-6)
    report = replay(store, record["run_id"])
    assert report.level == "NONE"
    assert report.discrepancy is not None
    assert "weight_total" in report.discrepancy and "('G00',)" in report.discrepancy


def test_environment_difference_reported(
    store: LocalObjectStore, record: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_env() -> dict[str, str]:
        return {"python": "9.9.9", "machine": "otra"}

    monkeypatch.setattr(replay_mod, "environment_fingerprint", fake_env)
    report = replay(store, record["run_id"])
    assert not report.environment_match
    assert report.environment_differences["python"][1] == "9.9.9"
    assert report.level == "L3"


# --------------------------------------------------------------- fallos cerrados
def test_tampered_snapshot_fails_integrity(
    store: LocalObjectStore, record: dict[str, Any], snapshot_id: str
) -> None:
    tamper(object_path(store, f"snapshots/{snapshot_id}/content.bin"))
    with pytest.raises(IntegrityError):
        replay(store, record["run_id"])
    code, out = run_cli("replay", "--store", str(store.root), "--run-id", record["run_id"])
    assert code == 3 and out["error"] == "integrity"
    assert "level" not in out


def test_tampered_artifact_fails_integrity(store: LocalObjectStore, record: dict[str, Any]) -> None:
    tamper(object_path(store, record["artifacts"][1]["key"]))
    with pytest.raises(IntegrityError):
        replay(store, record["run_id"])


def test_tampered_run_record_fails_integrity(
    store: LocalObjectStore, record: dict[str, Any]
) -> None:
    path = object_path(store, f"runs/{record['run_id']}/run_record.json")
    doc = json.loads(path.read_text("utf-8"))
    doc["result_digest"] = "0" * 64
    tamper(path, json.dumps(doc).encode())
    with pytest.raises(IntegrityError):
        replay(store, record["run_id"])


def test_missing_artifact_fails_integrity(store: LocalObjectStore, record: dict[str, Any]) -> None:
    path = object_path(store, record["artifacts"][2]["key"])
    path.chmod(0o600)
    path.unlink()
    with pytest.raises(IntegrityError, match="ausente"):
        replay(store, record["run_id"])


def test_missing_snapshot_is_not_reproducible_without_partial_result(
    store: LocalObjectStore, record: dict[str, Any], snapshot_id: str
) -> None:
    force_rmtree(object_path(store, f"snapshots/{snapshot_id}"))
    with pytest.raises(NotReproducibleError):
        replay(store, record["run_id"])
    code, out = run_cli("replay", "--store", str(store.root), "--run-id", record["run_id"])
    assert code == 4
    assert out["error"] == "not_reproducible"
    assert set(out) <= {"error", "detail"}


def test_missing_run_is_not_reproducible(store: LocalObjectStore) -> None:
    with pytest.raises(NotReproducibleError):
        replay(store, "run-" + "0" * 32)


def test_cli_level_requirement_exit_code(
    store: LocalObjectStore, record: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out = run_cli(
        "replay", "--store", str(store.root), "--run-id", record["run_id"], "--level", "L3"
    )
    assert code == 0 and out["level"] == "L3"


# --------------------------------------------------------------- A12: mutación
def _out_table(**overrides: Any) -> pa.Table:
    row = {
        "group_code": "G",
        "record_count": 2,
        "quantity_total": Decimal("1.500"),
        "weight_total": 10.0,
        "first_date": dt.date(2026, 1, 1),
        "last_date": dt.date(2026, 1, 2),
    }
    row.update(overrides)
    return SYNTHETIC_OUTPUT.canonicalize(
        pa.table({k: [v] for k, v in row.items()}, schema=SYNTHETIC_OUTPUT.schema())
    )


def _side(table: pa.Table, content: str = "c") -> Side:
    return Side(SYNTHETIC_OUTPUT, table, content, SYNTHETIC_OUTPUT.logical_hash(table))


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({}, "L2"),
        ({"weight_total": math.nextafter(10.0, 11.0)}, "L1"),
        ({"weight_total": 10.0 + 5e-10}, "L1"),
        ({"weight_total": 10.0 + 2e-9}, "NONE"),
        ({"weight_total": float("nan")}, "NONE"),
        ({"quantity_total": Decimal("1.501")}, "NONE"),
        ({"record_count": 3}, "NONE"),
        ({"first_date": dt.date(2025, 12, 31)}, "NONE"),
        ({"group_code": "H"}, "NONE"),
    ],
)
def test_comparator_mutations(override: dict[str, Any], expected: str) -> None:
    base = _out_table()
    mutated = _out_table(**override)
    result = compare([_side(base, "a")], [_side(mutated, "b")])
    assert result.level == expected, result


def test_comparator_detects_extra_and_missing_rows() -> None:
    one = _out_table()
    two = pa.concat_tables([one, _out_table(group_code="Z")])
    extra = first_l1_discrepancy(SYNTHETIC_OUTPUT, one, two)
    missing = first_l1_discrepancy(SYNTHETIC_OUTPUT, two, one)
    assert extra is not None and "adicional" in extra
    assert missing is not None and "falta" in missing
    assert compare([_side(one)], [_side(two, "x")]).level == "NONE"


def test_comparator_nan_and_identity() -> None:
    nan = _out_table(weight_total=float("nan"))
    assert first_l1_discrepancy(SYNTHETIC_OUTPUT, nan, nan) is None
    same = _side(_out_table(), "same")
    assert compare([same], [same]).level == "L3"


def test_comparator_artifact_set_mismatch_and_empty() -> None:
    side = _side(_out_table())
    assert compare([side], []).level == "NONE"
    assert compare([], []).level == "NONE"


def test_exact_float_columns_have_no_tolerance() -> None:
    a = SYNTHETIC_INPUT
    assert a.field("weight").tolerance is None  # entrada: igualdad exacta
    assert SYNTHETIC_OUTPUT.field("weight_total").tolerance == 1e-9


def test_values_equal_handles_nulls() -> None:
    from nexus.pipeline.compare import _values_equal

    assert _values_equal(pa.float64(), None, None, None)
    assert not _values_equal(pa.float64(), None, None, 1.0)
    assert not _values_equal(pa.string(), None, "a", None)
    assert _values_equal(pa.float64(), None, 1.5, 1.5)
    assert not _values_equal(pa.float64(), None, 1.5, math.nextafter(1.5, 2.0))
