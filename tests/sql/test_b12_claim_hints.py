"""B12: conformidad de las pistas de bloqueo de la reclamación, determinada con SQL Server REAL
bajo RCSI ON/OFF (matriz del job) y sesión READ COMMITTED/SNAPSHOT.

Candidatos (nunca se combina ROWLOCK con READCOMMITTEDLOCK, decisión del revisor):
  A = UPDLOCK, READPAST, ROWLOCK
  B = UPDLOCK, READPAST, READCOMMITTEDLOCK
  C = UPDLOCK, READPAST
Para cada candidato se registra: compilación/ejecución, salto de filas bloqueadas sin espera
(LOCK_TIMEOUT acotado) y exclusividad 8×100. El candidato instalado en producción DEBE cumplir
todo en cada celda de la matriz; los demás quedan como evidencia comparativa.
"""

from __future__ import annotations

import os
import time
import uuid
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
    wipe,
)
from sql_probes import probe_claim_exclusivity
from sql_variants import (
    claim_hints,
    install_variant,
    production_procs,
    record_evidence,
    with_claim_hints,
)

from nexus.metadata.connection import connect

pytestmark = pytest.mark.sql

CANDIDATES = {
    "A": "UPDLOCK, READPAST, ROWLOCK",
    "B": "UPDLOCK, READPAST, READCOMMITTEDLOCK",
    "C": "UPDLOCK, READPAST",
}
REPS = int(os.environ.get("NEXUS_B12_REPETITIONS", "3"))


def _normalized(hints: str) -> str:
    return ", ".join(h.strip().upper() for h in hints.split(","))


PRODUCTION = _normalized(claim_hints(production_procs()["usp_claim_request"]))


def test_b12_production_candidate_is_declared_and_not_forbidden() -> None:
    assert PRODUCTION in {_normalized(v) for v in CANDIDATES.values()}, PRODUCTION
    assert not {"ROWLOCK", "READCOMMITTEDLOCK"} <= set(PRODUCTION.split(", "))


def _skip_locked(admin_settings: Any, repo: Any, admin: Any) -> dict[str, Any]:
    """Una fila bloqueada (U) por otra transacción debe saltarse sin esperar."""
    seed_tag = uuid.uuid4().hex[:8]
    first, _ = repo.enqueue(f"b12-lock-{seed_tag}", f"b12a-{seed_tag}", "manual", {}, 3)
    second, _ = repo.enqueue(f"b12-free-{seed_tag}", f"b12b-{seed_tag}", "manual", {}, 3)
    locker = connect(admin_settings, 60)
    try:
        locker.cursor().execute(
            "BEGIN TRANSACTION; SELECT request_id FROM nexus.run_request WITH (UPDLOCK, ROWLOCK) "
            "WHERE request_id = ?",
            first,
        ).fetchall()
        repo._connection().cursor().execute("SET LOCK_TIMEOUT 3000")
        started = time.monotonic()
        error = None
        claimed = None
        try:
            claim = repo.claim("b12", 30000)
            claimed = None if claim is None else claim.request_id
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:300]
        elapsed_ms = round((time.monotonic() - started) * 1000)
        locker.cursor().execute("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION")
    finally:
        locker.close()
    ok = error is None and claimed == second and elapsed_ms < 2500
    return {
        "skip_locked_ok": ok,
        "claimed": claimed,
        "expected": second,
        "locked": first,
        "elapsed_ms": elapsed_ms,
        "error": error,
    }


@pytest.mark.parametrize("candidate", sorted(CANDIDATES))
def test_b12_hint_candidate_conformance(
    candidate: str, admin: Any, admin_settings: Any, repo_factory: Any, sql_env: dict[str, str]
) -> None:
    hints = CANDIDATES[candidate]
    schema = f"nexus_hint_{candidate.lower()}"
    rcsi = count(
        admin,
        "SELECT CAST(is_read_committed_snapshot_on AS int) FROM sys.databases "
        "WHERE name = DB_NAME()",
    )
    cell = {"rcsi": "ON" if rcsi else "OFF", "session_isolation": sql_env["isolation"]}
    evidence: dict[str, Any] = {
        "B12": candidate,
        "hints": hints,
        **cell,
        "production": _normalized(hints) == PRODUCTION,
        "server_ts": str(server_now(admin)),
    }
    try:
        install_variant(admin, schema, with_claim_hints(hints))
        evidence["compiled"] = True
    except Exception as exc:
        evidence["compiled"] = False
        evidence["compile_error"] = f"{type(exc).__name__}: {exc}"[:400]
    if evidence["compiled"]:
        try:
            evidence.update(_skip_locked(admin_settings, repo_factory(proc_schema=schema), admin))
            wipe(admin)
            runs = [
                probe_claim_exclusivity(
                    repo_factory, admin, workers=8, requests=100, proc_schema=schema
                )
                for _ in range(REPS)
            ]
            evidence["exclusivity_runs"] = len(runs)
            evidence["exclusivity_violations"] = [v for r in runs for v in r["violations"]]
        except Exception as exc:
            evidence["runtime_error"] = f"{type(exc).__name__}: {exc}"[:400]
    evidence["conforms"] = bool(
        evidence["compiled"]
        and evidence.get("skip_locked_ok")
        and "runtime_error" not in evidence
        and evidence.get("exclusivity_violations") == []
    )
    record_evidence("b12_hint_matrix", evidence)
    if evidence["production"]:
        assert evidence["conforms"], evidence
