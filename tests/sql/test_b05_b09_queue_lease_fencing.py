"""B5–B9 contra SQL Server real: exclusividad (C1), idempotencia de encolado, lease único,
fencing de TODOS los procedimientos con token y caídas reales (SIGKILL) con reintentos (C2/C3)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import uuid
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
from sql_probes import FENCED_CALLS, make_zombie, probe_claim_exclusivity, probe_fencing

from conftest import AS_OF, ROOT, VALID_CSV
from nexus.connectors import synthetic_file
from nexus.metadata.errors import ClaimInvalidError
from nexus.pipeline.worker import work_once
from nexus.snapshots.capture import create_snapshot
from nexus.storage.object_store import LocalObjectStore

REPETITIONS = int(os.environ.get("NEXUS_SQL_REPETITIONS", "20"))

pytestmark = pytest.mark.sql


@pytest.mark.parametrize("rep", range(REPETITIONS))
def test_b05_claim_exclusivity_16x200(repo_factory: Any, admin: Any, rep: int) -> None:
    evidence = probe_claim_exclusivity(repo_factory, admin, workers=16, requests=200)
    print(json.dumps({"B5": rep, **evidence}))
    assert evidence["violations"] == []
    assert evidence["claims"] == 200  # sin fallos inyectados: una reclamación por solicitud


@pytest.mark.parametrize("rep", range(REPETITIONS))
def test_b06_concurrent_enqueue_same_key(repo_factory: Any, admin: Any, rep: int) -> None:
    key = f"b6-{uuid.uuid4().hex}"
    results: list[tuple[int, bool]] = []
    errors: list[str] = []
    barrier = threading.Barrier(16)

    def go(n: int) -> None:
        repo = repo_factory(actor=f"e{n}")
        barrier.wait()
        try:
            results.append(repo.enqueue(key, "s", "manual", {"n": 1}, 3))
        except Exception as exc:
            errors.append(repr(exc))

    threads = [threading.Thread(target=go, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == []
    assert len({rid for rid, _ in results}) == 1
    assert sum(1 for _, created in results if created) == 1
    assert (
        count(admin, "SELECT COUNT(*) FROM nexus.run_request WHERE idempotency_key = ?", key) == 1
    )


@pytest.mark.parametrize("rep", range(REPETITIONS))
def test_b07_single_lease_and_claim_release(repo_factory: Any, admin: Any, rep: int) -> None:
    seed = repo_factory()
    for i in range(16):
        seed.enqueue(f"b7-{rep}-{i}", "shared-scope", "manual", {}, 3)
    claims: list[Any] = []
    leases: list[Any] = []
    barrier = threading.Barrier(16)
    repos = [repo_factory(actor=f"l{n}") for n in range(16)]

    def go(n: int) -> None:
        claim = repos[n].claim(f"l{n}", 30000)
        claims.append((n, claim))
        barrier.wait()
        if claim is not None:
            leases.append((n, claim, repos[n].acquire_lease(claim, f"l{n}", 30000)))

    threads = [threading.Thread(target=go, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    winners = [x for x in leases if x[2].acquired]
    assert len(winners) == 1, [x[2] for x in leases]
    for n, claim, lease in leases:
        if not lease.acquired:
            assert lease.reason == "LEASE_BUSY"
            assert repos[n].release_claim(claim, 0)
            with pytest.raises(ClaimInvalidError):
                repos[n].acquire_lease(claim, f"l{n}", 30000)  # claim liberado: token inválido
    assert count(admin, "SELECT COUNT(*) FROM nexus.run_request WHERE attempts > 0") == 1
    # Tokens estrictamente crecientes al completar secuencialmente las 16 solicitudes.
    _, _, first = winners[0]
    tokens = [int(first.fencing_token)]
    repos[winners[0][0]].register_artifact(
        str(first.attempt_id), tokens[0], "c", "k0", "a" * 64, "a" * 64, 1
    )
    repos[winners[0][0]].seal(str(first.attempt_id), tokens[0], "r0", "a" * 64, "a" * 64)
    repos[winners[0][0]].complete(str(first.attempt_id), tokens[0])
    worker = repo_factory(actor="seq")
    while True:
        claim = worker.claim("seq", 30000)
        if claim is None:
            break
        lease = worker.acquire_lease(claim, "seq", 30000)
        assert lease.acquired
        tok = int(lease.fencing_token or 0)
        tokens.append(tok)
        worker.register_artifact(str(lease.attempt_id), tok, "c", f"k{tok}", "a" * 64, "a" * 64, 1)
        worker.seal(str(lease.attempt_id), tok, f"r{tok}", "a" * 64, "a" * 64)
        worker.complete(str(lease.attempt_id), tok)
    assert len(tokens) == 16 and tokens == sorted(set(tokens))


def test_b07_expired_claim_recovered_without_consuming_attempt(
    repo_factory: Any, admin: Any
) -> None:
    repo = repo_factory()
    repo.enqueue("b7x", "s", "manual", {}, 3)
    claim = repo.claim("dies", 1000)
    assert claim is not None
    wait_server_past(admin, claim.claim_expires_at)
    assert repo.recover_expired(0) == (0, 1)
    row = repo.get_request("b7x")
    assert row is not None and row["state"] == "PENDING" and row["attempts"] == 0
    with pytest.raises(ClaimInvalidError):
        repo.acquire_lease(claim, "dies", 5000)


def test_b08_zombie_rejected_by_every_fenced_procedure(repo_factory: Any, admin: Any) -> None:
    zombie = make_zombie(repo_factory, admin, wait_server_past)
    evidence = probe_fencing(zombie, admin, list(FENCED_CALLS))
    print(json.dumps({"B8": evidence["results"]}))
    assert evidence["violations"] == []
    assert set(evidence["results"]) == set(FENCED_CALLS)


def test_b08_exceptions_have_their_own_controls(repo_factory: Any, admin: Any) -> None:
    repo = repo_factory()
    rid, created = repo.enqueue("b8e", "s", "manual", {}, 3)
    assert created and repo.enqueue("b8e", "s", "manual", {}, 3) == (rid, False)
    claim = repo.claim("x", 30000)
    assert claim is not None
    forged = type(claim)(**{**claim.__dict__, "claim_token": str(uuid.uuid4())})
    assert repo.release_claim(forged, 0) is False
    with pytest.raises(ClaimInvalidError):
        repo.acquire_lease(forged, "x", 5000)
    assert repo.recover_expired(0) == (0, 0)  # nada vencido: no actúa
    assert repo.get_request("b8e")["state"] == "CLAIMED"  # type: ignore[index]


KILL_STAGES = [
    "after_claim",
    "after_lease",
    "after_execute",
    "after_register",
    "after_seal",
    "after_complete",
]
KILLER = ROOT / "tests" / "sql" / "kill_worker.py"


@pytest.mark.parametrize("stage", KILL_STAGES)
def test_b09_sigkill_each_stage_then_bounded_retry(
    repo_factory: Any, admin: Any, profile: Any, tmp_path: Path, stage: str
) -> None:
    store = LocalObjectStore(tmp_path / "store")
    snap = create_snapshot(store, synthetic_file.capture(VALID_CSV))
    payload = {
        "snapshot_id": snap["snapshot_id"],
        "snapshot_sha256": snap["content_sha256"],
        "as_of": AS_OF,
        "params": {},
        "trigger": "scheduled",
    }
    key = f"b9-{stage}"
    repo_factory().enqueue(key, f"scope-{stage}", "scheduled", payload, profile.max_attempts)
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src"),
        NEXUS_KILL_STAGE=stage,
        NEXUS_STORE=str(store.root),
    )
    proc = subprocess.run(
        [sys.executable, str(KILLER)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == -signal.SIGKILL, (proc.returncode, proc.stderr[-2000:])
    # Recuperación: se espera al vencimiento (hora del servidor) y un worker nuevo reintenta.
    wait_server_past(admin, server_now(admin), margin_ms=profile.lease_ms + 500)
    heartbeat = repo_factory(actor="hb")
    worker = repo_factory(actor="retry")
    outcomes = []
    for _ in range(profile.max_attempts + 2):
        out = work_once(store, worker, heartbeat, profile, "retry")
        outcomes.append(out.status)
        if out.status in ("COMPLETED", "IDLE") and worker.get_request(key)["state"] == "COMPLETED":  # type: ignore[index]
            break
        wait_server_past(admin, server_now(admin), margin_ms=profile.backoff_ms + 100)
    row = worker.get_request(key)
    assert row is not None and row["state"] == "COMPLETED", (outcomes, row)
    assert row["attempts"] <= profile.max_attempts
    assert (
        count(
            admin,
            "SELECT COUNT(*) FROM nexus.generation g JOIN nexus.run_request r "
            "ON r.request_id = g.request_id WHERE r.idempotency_key = ? AND g.sealed_at IS NOT NULL",
            key,
        )
        == 1
    )
    assert (
        count(
            admin,
            "SELECT COUNT(*) FROM nexus.run_event WHERE kind = N'COMPLETED' AND request_id = ?",
            row["request_id"],
        )
        == 1
    )
    states = [
        r[0]
        for r in admin.cursor()
        .execute(
            "SELECT a.state FROM nexus.attempt a JOIN nexus.run_request r ON r.request_id = a.request_id "
            "WHERE r.idempotency_key = ? ORDER BY a.attempt_no",
            key,
        )
        .fetchall()
    ]
    print(json.dumps({"B9": stage, "outcomes": outcomes, "attempt_states": states}))
    assert states[-1] == "COMPLETED"
    assert all(s in ("FAILED", "ABANDONED", "COMPLETED") for s in states)
