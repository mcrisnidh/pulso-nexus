"""Sondas reutilizables (las usan los criterios B5–B15 y las pruebas de mutación B16).

Cada sonda devuelve un diccionario de evidencia con ``violations`` (lista vacía = invariante OK).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from typing import Any

from nexus.metadata.errors import FencedError
from nexus.metadata.sqlserver import SqlServerMetadata

Factory = Callable[..., SqlServerMetadata]
SHA = "a" * 64
MAX_CONSECUTIVE_ERRORS = 20
DRAIN_DEADLINE_S = 300


def fake_execute(repo: SqlServerMetadata, attempt_id: str, token: int, tag: str) -> None:
    """Efecto persistido sintético (sin almacén): un artefacto, sellado y completado."""
    repo.register_artifact(
        attempt_id, token, "synthetic_output@1.0.0", f"runs/{tag}/x.parquet", SHA, SHA, 1
    )
    repo.seal(attempt_id, token, f"run-{tag}", SHA, SHA)
    repo.complete(attempt_id, token)


HEARTBEAT_S = 1.0


def _heartbeat(
    repo: SqlServerMetadata,
    att: str,
    tok: int,
    lease_ms: int,
    stop: threading.Event,
    latencies: list[float],
) -> None:
    while not stop.wait(HEARTBEAT_S):
        try:
            repo.heartbeat(att, tok, lease_ms)
        except Exception:  # lease perdido: el trabajo principal recibirá FENCED
            return


def drain(
    factory: Factory,
    worker: str,
    stop: threading.Event,
    claims: list[tuple[int, str]],
    errors: list[str],
    proc_schema: str = "nexus",
    lease_ms: int = 5000,
    latencies: list[float] | None = None,
) -> None:
    latencies = latencies if latencies is not None else []
    repo = factory(proc_schema=proc_schema, actor=worker)
    # Como el worker de producción: heartbeat cada HEARTBEAT_S por una conexión propia.
    hb_repo = factory(proc_schema=proc_schema, actor=f"{worker}-hb")
    idle = 0
    failures = 0
    deadline = time.monotonic() + DRAIN_DEADLINE_S
    while not stop.is_set() and idle < 3:
        if failures >= MAX_CONSECUTIVE_ERRORS or time.monotonic() > deadline:
            errors.append(f"{worker}: abandona tras {failures} errores seguidos o plazo agotado")
            return
        try:
            claim = repo.claim(worker, 30000)
            if claim is None:
                idle += 1
                continue
            idle = 0
            claims.append((claim.request_id, claim.claim_token))
            lease = repo.acquire_lease(claim, worker, lease_ms)
            if not lease.acquired:
                repo.release_claim(claim, 0)
                continue
            att, tok = str(lease.attempt_id), int(lease.fencing_token or 0)
            beat_stop = threading.Event()
            beat = threading.Thread(
                target=_heartbeat,
                args=(hb_repo, att, tok, lease_ms, beat_stop, latencies),
                daemon=True,
            )
            beat.start()
            try:
                started = time.monotonic()
                fake_execute(repo, att, tok, uuid.uuid4().hex)
                latencies.append(time.monotonic() - started)
            finally:
                beat_stop.set()
                beat.join(timeout=30)
            failures = 0
        except Exception as exc:  # cualquier error se registra como evidencia
            failures += 1
            errors.append(f"{worker}: {type(exc).__name__}: {exc}"[:300])


def probe_claim_exclusivity(
    factory: Factory, admin: Any, workers: int, requests: int, proc_schema: str = "nexus"
) -> dict[str, Any]:
    """B5/C1: N workers, M solicitudes con ámbitos distintos, sin fallos inyectados."""
    seed = factory()
    tag = uuid.uuid4().hex[:8]
    for i in range(requests):
        seed.enqueue(f"b5-{tag}-{i}", f"b5-{tag}-{i}", "manual", {"i": i}, 3)
    stop = threading.Event()
    claims: list[tuple[int, str]] = []
    errors: list[str] = []
    latencies: list[float] = []
    threads = [
        threading.Thread(
            target=drain,
            args=(factory, f"w{n}", stop, claims, errors, proc_schema, 5000, latencies),
        )
        for n in range(workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=600)
    stop.set()
    per_request = Counter(rid for rid, _ in claims)
    dup = {rid: n for rid, n in per_request.items() if n > 1}
    cur = admin.cursor()
    states = dict(
        cur.execute("SELECT state, COUNT(*) FROM nexus.run_request GROUP BY state").fetchall()
    )
    sealed = cur.execute(
        "SELECT MAX(c) FROM (SELECT COUNT(*) AS c FROM nexus.generation WHERE sealed_at IS NOT NULL "
        "GROUP BY request_id) AS x"
    ).fetchone()[0]
    # Auditoría independiente del cliente (B5): eventos CLAIMED por solicitud en run_event.
    audited_dup = cur.execute(
        "SELECT COUNT(*) FROM (SELECT request_id FROM nexus.run_event WHERE kind = N'CLAIMED' "
        "GROUP BY request_id HAVING COUNT(*) > 1) AS x"
    ).fetchone()[0]
    violations = []
    if audited_dup:
        violations.append(f"run_event: solicitudes con más de un CLAIMED: {audited_dup}")
    if dup:
        violations.append(f"solicitudes reclamadas más de una vez: {len(dup)}")
    if states.get("COMPLETED", 0) != requests:
        violations.append(f"estados finales: {states}")
    if sealed not in (None, 1):
        violations.append(f"más de una generación sellada por solicitud: {sealed}")
    if errors:
        violations.append(f"errores: {errors[:3]}")
    ordered = sorted(latencies)
    return {
        "execute_s_p50": round(ordered[len(ordered) // 2], 3) if ordered else None,
        "execute_s_max": round(ordered[-1], 3) if ordered else None,
        "claims": len(claims),
        "run_event_duplicate_claims": audited_dup,
        "requests": requests,
        "workers": workers,
        "states": states,
        "duplicate_claims": len(dup),
        "violations": violations,
    }


def make_zombie(
    factory: Factory, admin: Any, wait_past: Callable[[Any, Any], None], proc_schema: str = "nexus"
) -> dict[str, Any]:
    """Prepara un zombi: lease vencido y recuperado; otro worker tiene el lease vigente."""
    repo = factory(proc_schema=proc_schema, actor="zombie")
    repo.enqueue(f"z-{uuid.uuid4().hex}", "zscope", "manual", {}, 3)
    claim = repo.claim("zombie", 30000)
    assert claim is not None
    lease = repo.acquire_lease(claim, "zombie", 5000)
    assert lease.acquired
    zatt, ztok = str(lease.attempt_id), int(lease.fencing_token or 0)
    repo.register_artifact(zatt, ztok, "c@1", "runs/z/a.parquet", SHA, SHA, 1)
    wait_past(admin, lease.expires_at)
    other = factory(proc_schema=proc_schema, actor="healthy")
    other.recover_expired(0)
    claim2 = other.claim("healthy", 30000)
    assert claim2 is not None
    lease2 = other.acquire_lease(claim2, "healthy", 60000)
    assert lease2.acquired and int(lease2.fencing_token or 0) > ztok
    return {"repo": repo, "attempt": zatt, "token": ztok}


FENCED_CALLS: dict[str, Callable[[SqlServerMetadata, str, int], Any]] = {
    "usp_heartbeat": lambda r, a, t: r.heartbeat(a, t, 5000),
    "usp_register_snapshot_ref": lambda r, a, t: r.register_snapshot_ref(a, t, "snap-z", SHA),
    "usp_register_artifact": lambda r, a, t: r.register_artifact(
        a, t, "c@2", "runs/z/b.parquet", SHA, SHA, 1
    ),
    "usp_append_run_event": lambda r, a, t: r.append_event(a, t, "ZOMBIE", "x"),
    "usp_seal_generation": lambda r, a, t: r.seal(a, t, "run-z", SHA, SHA),
    "usp_complete_run": lambda r, a, t: r.complete(a, t),
    "usp_fail_run": lambda r, a, t: r.fail(a, t, "zombie", 0),
}

SNAPSHOT_SQL = (
    "SELECT (SELECT COUNT(*) FROM nexus.artifact), (SELECT COUNT(*) FROM nexus.snapshot_ref),"
    " (SELECT COUNT(*) FROM nexus.run_event),"
    " (SELECT CHECKSUM_AGG(CHECKSUM(state, sealed_at, store_run_id)) FROM nexus.generation),"
    " (SELECT CHECKSUM_AGG(CHECKSUM(state, attempts)) FROM nexus.run_request),"
    " (SELECT CHECKSUM_AGG(CHECKSUM(holder, attempt_id, fencing_token, expires_at)) FROM nexus.scope_lease),"
    " (SELECT CHECKSUM_AGG(CHECKSUM(state)) FROM nexus.attempt)"
)


def probe_fencing(zombie: dict[str, Any], admin: Any, procs: list[str]) -> dict[str, Any]:
    """B8: el zombi intenta cada procedimiento que exige token; todos deben rechazar sin cambios."""
    repo, att, tok = zombie["repo"], zombie["attempt"], zombie["token"]
    results: dict[str, str] = {}
    violations = []
    for proc in procs:
        before = tuple(admin.cursor().execute(SNAPSHOT_SQL).fetchone())
        try:
            FENCED_CALLS[proc](repo, att, tok)
            results[proc] = "ACEPTADO"
        except FencedError:
            results[proc] = "FENCED"
        except Exception as exc:
            results[proc] = f"{type(exc).__name__}"
        after = tuple(admin.cursor().execute(SNAPSHOT_SQL).fetchone())
        if results[proc] != "FENCED":
            violations.append(f"{proc}: {results[proc]}")
        if before != after:
            violations.append(f"{proc}: modificó filas")
    return {"results": results, "violations": violations}


def store_hashes(store_root: Any) -> dict[str, str]:
    """sha256 de todos los objetos del almacén (prueba de que la reconciliación no toca nada)."""
    import hashlib
    from pathlib import Path

    base = Path(store_root) / "objects"
    return {
        p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(base.rglob("*"))
        if p.is_file()
    }


def probe_reconcile(
    store: Any,
    repo: SqlServerMetadata,
    reconcile_fn: Callable[..., Any],
    expected_unregistered: set[str],
) -> dict[str, Any]:
    """B15/R-A, R-C: la reconciliación informa archivos sin fila y NO borra, mueve ni altera."""
    before = store_hashes(store.root)
    sql_before = (
        sorted(r["object_key"] for r in repo.list_artifacts()),
        repo.list_snapshot_refs(),
        sorted(g["attempt_id"] for g in repo.list_generations()),
    )
    report = reconcile_fn(store, repo)
    after = store_hashes(store.root)
    sql_after = (
        sorted(r["object_key"] for r in repo.list_artifacts()),
        repo.list_snapshot_refs(),
        sorted(g["attempt_id"] for g in repo.list_generations()),
    )
    reported = {u["key"] for u in report.unregistered_files}
    violations = []
    if before != after:
        gone = sorted(set(before) - set(after))
        changed = sorted(k for k in before if k in after and before[k] != after[k])
        violations.append(f"el almacén cambió: eliminados={gone[:3]} alterados={changed[:3]}")
    if not expected_unregistered <= reported:
        violations.append(f"no informados: {sorted(expected_unregistered - reported)[:3]}")
    if any(u["kind"] != "UNREGISTERED_FILE" for u in report.unregistered_files):
        violations.append("clasificación distinta de UNREGISTERED_FILE")
    # NX-E1B-R1-02: identidad criptográfica exacta de cada archivo informado.
    wrong_identity = [
        u["key"] for u in report.unregistered_files if u.get("sha256") != before.get(u["key"])
    ]
    if wrong_identity:
        violations.append(f"SHA-256 ausente o distinto de los bytes en disco: {wrong_identity[:3]}")
    if sql_before != sql_after:
        violations.append("la reconciliación registró o eliminó filas por su cuenta")
    return {
        "objects": len(before),
        "unregistered": sorted(reported),
        "labels": sorted({u["label"] for u in report.unregistered_files}),
        "run_labels": sorted(
            {u["label"] for u in report.unregistered_files if "store_run_id" in u}
        ),
        "object_types": sorted({u.get("object_type", "?") for u in report.unregistered_files}),
        "sql_reference_issues": [i["kind"] for i in getattr(report, "sql_reference_issues", [])],
        "marked_missing": report.marked_missing,
        "marked_corrupt": report.marked_corrupt,
        "violations": violations,
    }
