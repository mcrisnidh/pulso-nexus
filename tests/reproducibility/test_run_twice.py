"""A9 — datos de negocio vs. metadatos de ejecución.

Dos corridas del mismo input_manifest producen run_id, marcas y rutas distintas,
pero el mismo result_digest y los mismos content_hash (L3, mismo entorno).
"""

from __future__ import annotations

import datetime as dt

import pyarrow.parquet as pq

from conftest import AS_OF, object_path, run_cli
from nexus.pipeline.run import execute
from nexus.storage.object_store import LocalObjectStore


def _clock(start: dt.datetime) -> object:
    moments = iter([start, start + dt.timedelta(seconds=3)])
    return lambda: next(moments)


def test_two_runs_same_manifest_equivalent_results(
    store: LocalObjectStore, snapshot_id: str
) -> None:
    a = execute(
        store,
        snapshot_id=snapshot_id,
        as_of=AS_OF,
        clock=_clock(dt.datetime(2026, 1, 1, tzinfo=dt.UTC)),  # type: ignore[arg-type]
    )
    b = execute(
        store,
        snapshot_id=snapshot_id,
        as_of=AS_OF,
        trigger="scheduled",
        clock=_clock(dt.datetime(2026, 6, 1, 12, tzinfo=dt.UTC)),  # type: ignore[arg-type]
    )
    # Metadatos de ejecución: distintos.
    assert a["run_id"] != b["run_id"]
    assert a["started_at"] != b["started_at"]
    assert a["trigger"] != b["trigger"]
    assert [x["key"] for x in a["artifacts"]] != [x["key"] for x in b["artifacts"]]
    # Identidad e resultado: idénticos.
    assert a["input_digest"] == b["input_digest"]
    assert a["result_digest"] == b["result_digest"]
    assert [(x["contract"], x["content_sha256"]) for x in a["artifacts"]] == [
        (x["contract"], x["content_sha256"]) for x in b["artifacts"]
    ]


def test_artifacts_contain_no_run_metadata(store: LocalObjectStore, snapshot_id: str) -> None:
    record = execute(store, snapshot_id=snapshot_id, as_of=AS_OF)
    for art in record["artifacts"]:
        path = object_path(store, art["key"])
        data = path.read_bytes()
        assert record["run_id"].encode() not in data
        assert record["run_id"][4:].encode() not in data
        assert record["started_at"].encode() not in data
        assert str(store.root).encode() not in data
        metadata = pq.read_metadata(path).metadata or {}
        assert set(metadata) <= {b"ARROW:schema"}


def test_result_digest_sensitive_to_business_inputs(
    store: LocalObjectStore, snapshot_id: str
) -> None:
    base = execute(store, snapshot_id=snapshot_id, as_of=AS_OF)
    earlier = execute(store, snapshot_id=snapshot_id, as_of="2026-06-30T00:00:00Z")
    filtered = execute(store, snapshot_id=snapshot_id, as_of=AS_OF, params={"min_quantity": "100"})
    assert len({base["result_digest"], earlier["result_digest"], filtered["result_digest"]}) == 3
    assert base["input_digest"] != earlier["input_digest"]


def test_cli_two_runs_from_separate_processes(store: LocalObjectStore, snapshot_id: str) -> None:
    st = str(store.root)
    code_a, a = run_cli("run", "--store", st, "--snapshot", snapshot_id, "--as-of", AS_OF)
    code_b, b = run_cli(
        "run", "--store", st, "--snapshot", snapshot_id, "--as-of", AS_OF, "--trigger", "scheduled"
    )
    assert code_a == code_b == 0, (a, b)
    assert a["run_id"] != b["run_id"]
    assert a["input_digest"] == b["input_digest"]
    assert a["result_digest"] == b["result_digest"]
    assert a["artifacts"] == b["artifacts"]
