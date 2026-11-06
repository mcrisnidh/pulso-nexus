"""B16: controles de mutación. Cada mutante se instala en un esquema AISLADO (``nexus_mut_*``) o se
construye en Python; las MISMAS sondas que validan producción (B5, B8, B15) deben detectarlo.
Si una sonda no detecta su mutante, la sonda es insuficiente y la prueba FALLA."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
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
from sql_probes import (
    FENCED_CALLS,
    make_zombie,
    probe_claim_exclusivity,
    probe_fencing,
    probe_reconcile,
)
from sql_variants import install_variant, record_evidence, with_claim_hints, without_fence

from conftest import AS_OF, ROOT, VALID_CSV
from nexus.connectors import synthetic_file
from nexus.pipeline.reconcile import ReconcileReport, reconcile
from nexus.snapshots.capture import create_snapshot
from nexus.storage.object_store import LocalObjectStore

pytestmark = pytest.mark.sql

# M1: reclamación en dos pasos (SELECT ... READPAST sin bloqueo de actualización, luego UPDATE por id
# sin revalidar el estado). Es exactamente el patrón prohibido en E0-R2 §2.5.1. El WAITFOR solo
# ensancha la ventana de carrera para que la detección sea determinista.
TWO_STEP_CLAIM = """CREATE OR ALTER PROCEDURE nexus_mut_claim.usp_claim_request
    @worker   nvarchar(128),
    @claim_ms int
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    DECLARE @now datetime2(7) = SYSUTCDATETIME();
    DECLARE @token uniqueidentifier = NEWID();
    DECLARE @id bigint;
    SELECT TOP (1) @id = request_id FROM nexus.run_request WITH (READPAST)
    WHERE state = N'PENDING' AND not_before <= @now ORDER BY not_before, request_id;
    WAITFOR DELAY '00:00:00.050';
    BEGIN TRANSACTION;
    UPDATE nexus.run_request
    SET state = N'CLAIMED', claim_token = @token, claimed_by = @worker,
        claim_expires_at = DATEADD(millisecond, @claim_ms, @now)
    WHERE request_id = @id;
    IF @@ROWCOUNT = 1
        INSERT INTO nexus.run_event (kind, request_id, claim_token, actor)
        VALUES (N'CLAIMED', @id, @token, @worker);
    COMMIT TRANSACTION;
    SELECT request_id, claim_token, scope, payload, attempts, max_attempts, claim_expires_at,
           SYSUTCDATETIME() AS server_ts
    FROM nexus.run_request WHERE request_id = @id AND claim_token = @token;
END"""


def _two_step(name: str, text: str) -> str:
    return TWO_STEP_CLAIM if name == "usp_claim_request" else text


def test_b16_m1_two_step_claim_is_detected(admin: Any, repo_factory: Any) -> None:
    install_variant(admin, "nexus_mut_claim", _two_step)
    evidence = probe_claim_exclusivity(
        repo_factory, admin, workers=16, requests=100, proc_schema="nexus_mut_claim"
    )
    record_evidence(
        "b16_mutation",
        {"B16": "M1_two_step_claim", "detected": bool(evidence["violations"]), **evidence},
    )
    assert evidence["violations"], "la sonda B5 NO detectó la reclamación en dos pasos"


@pytest.mark.parametrize("proc", sorted(FENCED_CALLS))
def test_b16_m2_missing_fence_is_detected(admin: Any, repo_factory: Any, proc: str) -> None:
    schema = "nexus_mut_" + proc.removeprefix("usp_")
    install_variant(admin, schema, without_fence(proc))
    zombie = make_zombie(repo_factory, admin, wait_server_past, proc_schema=schema)
    evidence = probe_fencing(zombie, admin, [proc])
    record_evidence(
        "b16_mutation",
        {"B16": "M2_no_fence", "proc": proc, "detected": bool(evidence["violations"]), **evidence},
    )
    assert evidence["violations"], f"la sonda B8 NO detectó la ausencia de fencing en {proc}"


def _deleting_reconcile(store: LocalObjectStore, repo: Any) -> ReconcileReport:
    """M3: reconciliación que 'limpia' los archivos sin fila (prohibido por NX-E1B-02)."""
    report = reconcile(store, repo)
    for item in report.unregistered_files:
        path = store.root / "objects" / item["key"]
        path.chmod(0o600)
        path.unlink()
    return report


def test_b16_m3_deleting_reconcile_is_detected(
    tmp_path: Path, repo_factory: Any, profile: Any
) -> None:
    store = LocalObjectStore(tmp_path / "store")
    snap = create_snapshot(store, synthetic_file.capture(VALID_CSV))
    payload = {
        "snapshot_id": snap["snapshot_id"],
        "snapshot_sha256": snap["content_sha256"],
        "as_of": AS_OF,
        "params": {},
        "trigger": "manual",
    }
    repo_factory().enqueue("b16-m3", "scope-b16-m3", "manual", payload, profile.max_attempts)
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src"),
        NEXUS_KILL_STAGE="after_execute",
        NEXUS_STORE=str(store.root),
    )
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "sql" / "kill_worker.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == -signal.SIGKILL, proc.stderr[-2000:]
    orphans = {k for k in store.iter_keys("runs/") if k.endswith(".parquet")}
    assert orphans
    evidence = probe_reconcile(
        store, repo_factory(actor="mut"), _deleting_reconcile, expected_unregistered=orphans
    )
    record_evidence(
        "b16_mutation",
        {"B16": "M3_deleting_reconcile", "detected": bool(evidence["violations"]), **evidence},
    )
    assert evidence["violations"], "la sonda B15 NO detectó la reconciliación que borra"


def test_b16_m1b_update_without_updlock_is_recorded_as_equivalent(
    admin: Any, repo_factory: Any
) -> None:
    """Registro (sin aserción de detección): quitar UPDLOCK de la reclamación de UNA sentencia
    (UPDATE sobre CTE con READPAST, ROWLOCK) es un mutante equivalente, porque el propio UPDATE
    toma bloqueos U/X. La variante peligrosa "sin UPDLOCK" es la de dos pasos (M1, detectada).
    Se exige, eso sí, que la variante equivalente NO viole la exclusividad."""
    install_variant(admin, "nexus_mut_noupd", with_claim_hints("READPAST, ROWLOCK"))
    evidence = probe_claim_exclusivity(
        repo_factory, admin, workers=16, requests=100, proc_schema="nexus_mut_noupd"
    )
    record_evidence(
        "b16_mutation",
        {
            "B16": "M1b_update_without_updlock_equivalent",
            "detected": bool(evidence["violations"]),
            **evidence,
        },
    )
    assert evidence["violations"] == [], "la variante equivalente no debe violar C1"
