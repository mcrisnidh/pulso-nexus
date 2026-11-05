"""Worker de E1-B: cola SQL + lease con fencing + pipeline de E1-A (sin cambios en su núcleo).

Orden "archivo durable y verificado antes que fila" (E0-R2 §4.2): ``execute`` publica y verifica
los archivos en el ObjectStore y SOLO después se registran filas en SQL.

Contratos (NX-E1B-03): C1 claim exclusivo; C2 reintentos por intento (puede haber ejecución
física repetida bajo fallos); C3 una sola generación sellada por solicitud y efectos idempotentes.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nexus.metadata.errors import FencedError
from nexus.metadata.profile import Profile
from nexus.metadata.repository import MetadataRepository
from nexus.pipeline.run import execute
from nexus.storage.object_store import LocalObjectStore

StageHook = Callable[[str], None]


@dataclass(frozen=True)
class WorkOutcome:
    status: str  # IDLE | LEASE_BUSY | MAX_ATTEMPTS | COMPLETED | FAILED | LEASE_LOST
    request_id: int | None = None
    attempt_id: str | None = None
    completed_attempt_id: str | None = None
    detail: str | None = None


class _Heartbeat(threading.Thread):
    def __init__(
        self, repo: MetadataRepository, attempt_id: str, token: int, profile: Profile
    ) -> None:
        super().__init__(daemon=True)
        self._repo, self._attempt, self._token, self._profile = repo, attempt_id, token, profile
        self.stop = threading.Event()
        self.lost: Exception | None = None

    def run(self) -> None:
        while not self.stop.wait(self._profile.heartbeat_ms / 1000):
            try:
                self._repo.heartbeat(self._attempt, self._token, self._profile.lease_ms)
            except Exception as exc:  # el lease se perdió: el worker debe detenerse
                self.lost = exc
                return


def work_once(
    store: LocalObjectStore,
    repo: MetadataRepository,
    heartbeat_repo: MetadataRepository,
    profile: Profile,
    worker: str,
    hook: StageHook | None = None,
) -> WorkOutcome:
    def stage(name: str) -> None:
        if hook is not None:
            hook(name)

    repo.recover_expired(profile.backoff_ms)
    claim = repo.claim(worker, profile.claim_ms)
    if claim is None:
        return WorkOutcome("IDLE")
    stage("after_claim")
    lease = repo.acquire_lease(claim, worker, profile.lease_ms)
    if not lease.acquired:
        if lease.reason == "LEASE_BUSY":
            repo.release_claim(claim, profile.backoff_ms)  # no consume intento
        return WorkOutcome(lease.reason or "LEASE_BUSY", claim.request_id)
    attempt_id, token = str(lease.attempt_id), int(lease.fencing_token or 0)
    stage("after_lease")
    if lease.sealed_attempt_id is not None:  # C3: ya hay resultado sellado; no se re-ejecuta
        done = repo.complete(attempt_id, token)
        return WorkOutcome("COMPLETED", claim.request_id, attempt_id, done, "ADOPTED_SEALED")
    beat = _Heartbeat(heartbeat_repo, attempt_id, token, profile)
    beat.start()
    try:
        payload: dict[str, Any] = claim.payload
        record = execute(
            store,
            snapshot_id=str(payload["snapshot_id"]),
            as_of=str(payload["as_of"]),
            params=dict(payload.get("params", {})),
            trigger=str(payload.get("trigger", "manual")),
        )
        stage("after_execute")
        if beat.lost is not None:
            raise beat.lost
        snapshot = record_snapshot(payload)
        if snapshot is not None:
            repo.register_snapshot_ref(attempt_id, token, *snapshot)
        for art in record["artifacts"]:
            repo.register_artifact(
                attempt_id,
                token,
                art["contract"],
                art["key"],
                art["content_sha256"],
                art["logical_hash"],
                int(art["size"]),
            )
        stage("after_register")
        repo.seal(
            attempt_id, token, record["run_id"], record["input_digest"], record["result_digest"]
        )
        stage("after_seal")
        done = repo.complete(attempt_id, token)
        stage("after_complete")
        return WorkOutcome("COMPLETED", claim.request_id, attempt_id, done)
    except FencedError as exc:
        return WorkOutcome("LEASE_LOST", claim.request_id, attempt_id, detail=str(exc))
    except Exception as exc:
        try:
            repo.fail(attempt_id, token, f"{type(exc).__name__}: {exc}", profile.backoff_ms)
        except FencedError:
            return WorkOutcome("LEASE_LOST", claim.request_id, attempt_id, detail=str(exc))
        return WorkOutcome("FAILED", claim.request_id, attempt_id, detail=str(exc))
    finally:
        beat.stop.set()
        beat.join(timeout=5)


def record_snapshot(payload: dict[str, Any]) -> tuple[str, str] | None:
    snapshot_id = payload.get("snapshot_id")
    sha = payload.get("snapshot_sha256")
    if snapshot_id is None or sha is None:
        return None
    return str(snapshot_id), str(sha)
