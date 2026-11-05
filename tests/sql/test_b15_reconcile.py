"""B15: reconciliación SQL ↔ ObjectStore con SQL Server real (R-A con BACKUP/RESTORE real, R-B,
R-C con caída real entre archivo y fila). La reconciliación nunca borra, mueve ni altera archivos."""

from __future__ import annotations

import dataclasses
import os
import signal
import stat
import subprocess
import sys
import time
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
from sql_probes import probe_reconcile, store_hashes
from sql_variants import record_evidence

from conftest import AS_OF, ROOT, VALID_CSV
from nexus.connectors import synthetic_file
from nexus.metadata.connection import connect
from nexus.pipeline.reconcile import reconcile
from nexus.pipeline.worker import work_once
from nexus.snapshots.capture import create_snapshot
from nexus.storage.object_store import LocalObjectStore

pytestmark = pytest.mark.sql

BACKUP_FILE = "/var/opt/mssql/data/nexus_b15_r_a.bak"  # dentro del contenedor efímero


def _enqueue_run(
    repo: Any, store: LocalObjectStore, key: str, profile: Any, params: dict[str, Any]
) -> None:
    snap = create_snapshot(store, synthetic_file.capture(VALID_CSV))
    payload = {
        "snapshot_id": snap["snapshot_id"],
        "snapshot_sha256": snap["content_sha256"],
        "as_of": AS_OF,
        "params": params,
        "trigger": "manual",
    }
    repo.enqueue(key, f"scope-{key}", "manual", payload, profile.max_attempts)


def _run_all(store: LocalObjectStore, repo_factory: Any, profile: Any, key: str) -> list[str]:
    """Trabaja hasta que la solicitud ``key`` sea terminal (respetando backoff con hora del servidor)."""
    worker, beat = repo_factory(actor="w"), repo_factory(actor="w-hb")
    outcomes = []
    for _ in range(4 * profile.max_attempts):
        out = work_once(store, worker, beat, profile, "w")
        outcomes.append(out.status)
        if worker.get_request(key)["state"] in ("COMPLETED", "FAILED_PERMANENT"):
            return outcomes
        time.sleep((profile.backoff_ms + 100) / 1000)
    return outcomes


def _drain_messages(conn: Any, cur: Any = None) -> None:
    """Consume los mensajes informativos de BACKUP/RESTORE para que la sentencia termine."""
    cur = cur if cur is not None else conn.cursor()
    while cur.nextset():
        pass


def _artifact_keys(repo: Any) -> set[str]:
    return {str(r["object_key"]) for r in repo.list_artifacts()}


def test_b15_r_a_sql_restored_to_earlier_point(
    tmp_path: Path, repo_factory: Any, admin_settings: Any, profile: Any, sql_env: dict[str, str]
) -> None:
    store = LocalObjectStore(tmp_path / "store")
    database = sql_env["NEXUS_SQL_DATABASE"]
    master = connect(dataclasses.replace(admin_settings, database="master"), 600)
    try:
        _enqueue_run(repo_factory(), store, "b15-ra-1", profile, {})
        assert _run_all(store, repo_factory, profile, "b15-ra-1")[-1] == "COMPLETED"
        backup = master.cursor()
        backup.execute(
            f"BACKUP DATABASE [{database}] TO DISK = N'{BACKUP_FILE}' WITH INIT, COPY_ONLY, FORMAT"
        )
        _drain_messages(master, backup)
        _enqueue_run(repo_factory(), store, "b15-ra-2", profile, {})
        assert _run_all(store, repo_factory, profile, "b15-ra-2")[-1] == "COMPLETED"
        later = _artifact_keys(repo_factory())
        cur = master.cursor()
        cur.execute(f"ALTER DATABASE [{database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
        cur.execute(f"RESTORE DATABASE [{database}] FROM DISK = N'{BACKUP_FILE}' WITH REPLACE")
        _drain_messages(master, cur)
        master.cursor().execute(f"ALTER DATABASE [{database}] SET MULTI_USER")
    finally:
        master.close()
    fresh = repo_factory(actor="reconciler")
    restored = _artifact_keys(fresh)
    lost = later - restored
    assert lost, "la restauración debía dejar archivos posteriores sin fila"
    evidence = probe_reconcile(store, fresh, reconcile, expected_unregistered=lost)
    record_evidence("b15_reconcile", {"B15": "R-A", **evidence})
    assert evidence["violations"] == []
    assert evidence["marked_missing"] == [] and evidence["marked_corrupt"] == []
    assert evidence["labels"] == ["COMPLETE_STORE_RUN"]
    # Segunda pasada: idéntica (solo informe, sin efectos acumulados).
    again = probe_reconcile(store, fresh, reconcile, expected_unregistered=lost)
    assert again["unregistered"] == evidence["unregistered"] and again["violations"] == []


def test_b15_r_b_files_missing_or_corrupt(
    tmp_path: Path, repo_factory: Any, admin: Any, profile: Any
) -> None:
    store = LocalObjectStore(tmp_path / "store")
    _enqueue_run(repo_factory(), store, "b15-rb", profile, {})
    assert _run_all(store, repo_factory, profile, "b15-rb")[-1] == "COMPLETED"
    repo = repo_factory(actor="reconciler")
    keys = sorted(_artifact_keys(repo))
    assert len(keys) >= 2, keys
    missing_key, corrupt_key = keys[0], keys[1]
    (store.root / "objects" / missing_key).unlink()
    corrupt = store.root / "objects" / corrupt_key
    corrupt.chmod(stat.S_IRUSR | stat.S_IWUSR)
    with corrupt.open("r+b") as fh:
        first = fh.read(1)
        fh.seek(0)
        fh.write(bytes([first[0] ^ 0xFF]))
    before = store_hashes(store.root)
    report = reconcile(store, repo)
    assert store_hashes(store.root) == before
    states = dict(admin.cursor().execute("SELECT object_key, state FROM nexus.artifact").fetchall())
    gen = [
        r[0]
        for r in admin.cursor()
        .execute("SELECT state FROM nexus.generation WHERE sealed_at IS NOT NULL")
        .fetchall()
    ]
    audit = count(admin, "SELECT COUNT(*) FROM nexus.run_event WHERE kind LIKE N'RECONCILE_%'")
    record_evidence(
        "b15_reconcile",
        {
            "B15": "R-B",
            "missing": report.marked_missing,
            "corrupt": report.marked_corrupt,
            "artifact_states": states,
            "generation_states": gen,
            "audit_events": audit,
            "server_ts": str(server_now(admin)),
        },
    )
    assert report.marked_missing == [missing_key]
    assert report.marked_corrupt == [corrupt_key]
    assert states[missing_key] == "MISSING" and states[corrupt_key] == "CORRUPT"
    assert gen == ["INELIGIBLE"]
    assert audit == 2
    second = reconcile(store, repo)  # idempotente: no vuelve a marcar
    assert second.marked_missing == [] and second.marked_corrupt == []


def test_b15_r_c_crash_between_file_and_row(
    tmp_path: Path, repo_factory: Any, admin: Any, profile: Any
) -> None:
    store = LocalObjectStore(tmp_path / "store")
    _enqueue_run(repo_factory(), store, "b15-rc", profile, {})
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
    repo = repo_factory(actor="reconciler")
    assert _artifact_keys(repo) == set()  # archivos durables, ninguna fila
    orphans = {k for k in store.iter_keys("runs/") if k.endswith(".parquet")}
    assert orphans
    evidence = probe_reconcile(store, repo, reconcile, expected_unregistered=orphans)
    record_evidence(
        "b15_reconcile", {"B15": "R-C", **evidence, "server_ts": str(server_now(admin))}
    )
    assert evidence["violations"] == []
    # Recuperación: vence el lease, otro worker completa; los huérfanos siguen informados, intactos.
    wait_server_past(admin, server_now(admin), margin_ms=profile.lease_ms + 500)
    _run_all(store, repo_factory, profile, "b15-rc")
    assert repo.get_request("b15-rc")["state"] == "COMPLETED"  # type: ignore[index]
    after = probe_reconcile(store, repo, reconcile, expected_unregistered=orphans)
    assert after["violations"] == []
