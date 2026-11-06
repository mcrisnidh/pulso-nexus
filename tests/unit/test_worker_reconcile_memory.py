"""E1-B: worker y reconciliación con el doble en memoria y el ObjectStore REAL (sin SQL).

Las garantías transaccionales se certifican en tests/sql/; aquí se comprueba la integración del
pipeline (orden archivo→fila, B1 result_digest, adopción C3, reconciliación sin borrar)."""

from __future__ import annotations

import contextlib
import dataclasses
import os
from pathlib import Path

from conftest import AS_OF, VALID_CSV, force_rmtree, object_path, tamper
from nexus.connectors import synthetic_file
from nexus.hashing import sha256_bytes
from nexus.metadata.memory import InMemoryMetadata
from nexus.metadata.profile import load_profile
from nexus.pipeline.reconcile import reconcile
from nexus.pipeline.run import execute
from nexus.pipeline.worker import work_once
from nexus.snapshots.capture import create_snapshot
from nexus.storage.object_store import LocalObjectStore

PROFILE = load_profile("e1b-ephemeral")
NO_BACKOFF = dataclasses.replace(PROFILE, backoff_ms=0)


def _setup(tmp_path: Path) -> tuple[LocalObjectStore, InMemoryMetadata, dict[str, str]]:
    store = LocalObjectStore(tmp_path / "store")
    snap = create_snapshot(store, synthetic_file.capture(VALID_CSV))
    repo = InMemoryMetadata()
    payload = {
        "snapshot_id": snap["snapshot_id"],
        "snapshot_sha256": snap["content_sha256"],
        "as_of": AS_OF,
        "params": {},
        "trigger": "scheduled",
    }
    return store, repo, payload


def _files(store: LocalObjectStore) -> dict[str, str]:
    root = store.root / "objects"
    return {
        p.relative_to(root).as_posix(): sha256_bytes(p.read_bytes())
        for p in root.rglob("*")
        if p.is_file()
    }


def test_worker_completes_and_result_digest_matches_e1a(tmp_path: Path) -> None:
    store, repo, payload = _setup(tmp_path)
    repo.enqueue("job-1", "scope-a", "scheduled", payload, 3)
    outcome = work_once(store, repo, repo, PROFILE, "w1")
    assert outcome.status == "COMPLETED", outcome
    reference = execute(store, snapshot_id=payload["snapshot_id"], as_of=AS_OF)  # E1-A directo
    gen = repo.generations[str(outcome.attempt_id)]
    assert gen.sealed and gen.result_digest == reference["result_digest"]  # B1
    assert all(
        store.exists(r["object_key"]) for r in repo.list_artifacts()
    )  # archivo antes que fila
    assert work_once(store, repo, repo, PROFILE, "w1").status == "IDLE"


def test_worker_failure_and_lease_busy(tmp_path: Path) -> None:
    store, repo, payload = _setup(tmp_path)
    bad = dict(payload, as_of="no-es-fecha")
    repo.enqueue("job-bad", "s", "manual", bad, 2)
    assert work_once(store, repo, repo, PROFILE, "w").status == "FAILED"
    repo.enqueue("job-2", "busy", "manual", payload, 3)
    repo.enqueue("job-3", "busy", "manual", payload, 3)
    c = repo.claim("other", 60000)
    assert c is not None
    repo.acquire_lease(c, "other", 60000)  # ocupa el ámbito "busy"
    seen = {work_once(store, repo, repo, PROFILE, "w").status for _ in range(3)}
    assert "LEASE_BUSY" in seen


def test_worker_adopts_sealed_generation(tmp_path: Path) -> None:
    store, repo, payload = _setup(tmp_path)
    repo.enqueue("job-s", "s", "manual", payload, 3)

    def die_after_seal(stage: str) -> None:
        if stage == "after_seal":
            raise SystemExit("caída simulada")

    with contextlib.suppress(SystemExit):
        work_once(store, repo, repo, NO_BACKOFF, "w1", hook=die_after_seal)
    for lease in repo.leases.values():  # fuerza el vencimiento del lease del intento caído
        lease.expires_at = lease.expires_at.replace(year=2000) if lease.expires_at else None
    outcome = work_once(store, repo, repo, NO_BACKOFF, "w2")
    assert outcome.status == "COMPLETED" and outcome.detail == "ADOPTED_SEALED"
    sealed = [g for g in repo.generations.values() if g.sealed]
    assert len(sealed) == 1  # C3


def test_reconcile_directions_never_touch_files(tmp_path: Path) -> None:
    store, repo, payload = _setup(tmp_path)
    repo.enqueue("job-r", "s", "manual", payload, 3)
    out = work_once(store, repo, repo, PROFILE, "w")
    assert out.status == "COMPLETED"
    # R-A: archivos sin fila (corrida en el almacén que SQL no conoce)
    execute(store, snapshot_id=payload["snapshot_id"], as_of=AS_OF)
    before = _files(store)
    rep = reconcile(store, repo)
    assert rep.marked_missing == [] and rep.marked_corrupt == []
    # NX-E1B-R1-02: 3 artefactos + run_record + input_manifest, cada uno con identidad SHA-256.
    types = sorted(u["object_type"] for u in rep.unregistered_files)
    assert types == ["ARTIFACT", "ARTIFACT", "ARTIFACT", "INPUT_MANIFEST", "RUN_RECORD"]
    assert {u["label"] for u in rep.unregistered_files} == {"COMPLETE_STORE_RUN"}
    assert all(u["sha256"] == before[u["key"]] for u in rep.unregistered_files)
    assert all(u["integrity"] == "VALID" for u in rep.unregistered_files)
    assert _files(store) == before  # no borra ni mueve
    # R-B: filas sin archivo / con otro hash
    rows = repo.list_artifacts()
    victim, other = (
        object_path(store, rows[0]["object_key"]),
        object_path(store, rows[1]["object_key"]),
    )
    os.chmod(victim, 0o600)
    victim.unlink()
    tamper(other)
    before = _files(store)
    rep = reconcile(store, repo)
    assert rep.marked_missing == [rows[0]["object_key"]]
    assert rep.marked_corrupt == [rows[1]["object_key"]]
    assert _files(store) == before
    assert reconcile(store, repo).marked_missing == []  # idempotente
    # R-C: corrida incompleta en el almacén (sin run_record)
    run_dir = next(iter(u["store_run_id"] for u in rep.unregistered_files))
    record = object_path(store, f"runs/{run_dir}/run_record.json")
    os.chmod(record, 0o600)
    record.unlink()
    labels = {u["label"] for u in reconcile(store, repo).unregistered_files}
    assert labels == {"INCOMPLETE_RUN"}
    force_rmtree(store.root / "staging")
