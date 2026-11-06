"""B1 (parte SQL): el result_digest es idéntico con y sin persistencia SQL.

Se ejecuta el pipeline de E1-A directamente (sin SQL) y a través del worker con cola/lease/fencing
en SQL Server; el result_digest sellado en nexus.generation debe ser el mismo."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sql_fixtures import (
    admin,
    admin_settings,
    base_settings,
    clean_database,
    count,
    profile,
    repo_factory,
    server_now,
    sql_env,
    wait_server_past,
)
from sql_variants import record_evidence

from conftest import AS_OF, VALID_CSV
from nexus.connectors import synthetic_file
from nexus.pipeline.run import execute
from nexus.pipeline.worker import work_once
from nexus.snapshots.capture import create_snapshot
from nexus.storage.object_store import LocalObjectStore

pytestmark = pytest.mark.sql


def test_b01_result_digest_identical_with_and_without_sql(
    tmp_path: Path, repo_factory: Any, admin: Any, profile: Any
) -> None:
    direct_store = LocalObjectStore(tmp_path / "direct")
    snap_direct = create_snapshot(direct_store, synthetic_file.capture(VALID_CSV))
    direct = execute(
        direct_store,
        snapshot_id=str(snap_direct["snapshot_id"]),
        as_of=AS_OF,
        params={},
        trigger="manual",
    )

    sql_store = LocalObjectStore(tmp_path / "sql")
    snap = create_snapshot(sql_store, synthetic_file.capture(VALID_CSV))
    assert snap["snapshot_id"] == snap_direct["snapshot_id"]
    payload = {
        "snapshot_id": snap["snapshot_id"],
        "snapshot_sha256": snap["content_sha256"],
        "as_of": AS_OF,
        "params": {},
        "trigger": "manual",
    }
    repo_factory().enqueue("b01", "scope-b01", "manual", payload, profile.max_attempts)
    out = work_once(sql_store, repo_factory(actor="w"), repo_factory(actor="w-hb"), profile, "w")
    assert out.status == "COMPLETED", out
    sealed = (
        admin.cursor()
        .execute(
            "SELECT g.result_digest, g.input_digest FROM nexus.generation AS g "
            "JOIN nexus.run_request AS r ON r.request_id = g.request_id "
            "WHERE r.idempotency_key = N'b01' AND g.sealed_at IS NOT NULL"
        )
        .fetchone()
    )
    evidence = {
        "B1": "result_digest",
        "direct": direct["result_digest"],
        "sql": sealed[0],
        "input_digest_direct": direct["input_digest"],
        "input_digest_sql": sealed[1],
        "server_ts": str(server_now(admin)),
    }
    record_evidence("b01_digest", evidence)
    assert sealed[0] == direct["result_digest"]
    assert sealed[1] == direct["input_digest"]
