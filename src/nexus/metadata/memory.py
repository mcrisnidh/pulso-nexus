"""Doble en memoria del repositorio de metadatos.

Implementa la MISMA semántica que los procedimientos T-SQL para pruebas unitarias y para el
job de Windows. NO certifica garantías transaccionales: esas solo se aceptan contra SQL Server
real (criterios B5–B16). El reloj es inyectable para pruebas deterministas.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from nexus.metadata.errors import (
    AlreadySealedError,
    ClaimInvalidError,
    ConflictError,
    FencedError,
    InvalidOperationError,
    NotSealedError,
)
from nexus.metadata.repository import Claim, Lease

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Request:
    request_id: int
    key: str
    scope: str
    trigger: str
    payload: dict[str, Any]
    max_attempts: int
    state: str = "PENDING"
    attempts: int = 0
    not_before: datetime = field(default_factory=_utcnow)
    claim_token: str | None = None
    claim_expires_at: datetime | None = None
    completed_attempt_id: str | None = None


@dataclass
class _Lease:
    holder: str | None = None
    attempt_id: str | None = None
    token: int = 0
    expires_at: datetime | None = None


@dataclass
class _Attempt:
    attempt_id: str
    request_id: int
    scope: str
    token: int
    state: str = "RUNNING"
    failure: str | None = None


@dataclass
class _Generation:
    request_id: int
    state: str = "OPEN"
    store_run_id: str | None = None
    input_digest: str | None = None
    result_digest: str | None = None
    sealed: bool = False


class InMemoryMetadata:
    def __init__(self, clock: Clock = _utcnow) -> None:
        self._now = clock
        self._lock = threading.RLock()
        self._next_id = 1
        self.requests: dict[int, _Request] = {}
        self.leases: dict[str, _Lease] = {}
        self.attempts: dict[str, _Attempt] = {}
        self.generations: dict[str, _Generation] = {}
        self.artifacts: dict[tuple[str, str], dict[str, Any]] = {}
        self.snapshots: dict[str, str] = {}
        self.events: list[dict[str, Any]] = []

    # ---------------------------------------------------------------- internos
    def _event(self, kind: str, **fields: Any) -> None:
        self.events.append({"kind": kind, "server_ts": self._now(), **fields})

    def _fence(self, attempt_id: str, token: int) -> _Attempt:
        attempt = self.attempts.get(attempt_id)
        lease = self.leases.get(attempt.scope) if attempt else None
        if (
            attempt is None
            or lease is None
            or attempt.state != "RUNNING"
            or lease.attempt_id != attempt_id
            or lease.token != token
            or attempt.token != token
            or lease.expires_at is None
            or lease.expires_at <= self._now()
        ):
            raise FencedError("NEXUS:FENCED token obsoleto o lease vencido")
        return attempt

    def _sealed_for(self, request_id: int) -> str | None:
        for attempt_id, gen in self.generations.items():
            if gen.request_id == request_id and gen.sealed:
                return attempt_id
        return None

    def _after_failure(self, request: _Request, backoff_ms: int) -> None:
        exhausted = (
            request.attempts >= request.max_attempts
            and self._sealed_for(request.request_id) is None
        )
        request.state = "FAILED_PERMANENT" if exhausted else "PENDING"
        request.claim_token = None
        request.claim_expires_at = None
        request.not_before = self._now() + timedelta(milliseconds=backoff_ms)

    # ---------------------------------------------------------------- API
    def enqueue(
        self, key: str, scope: str, trigger: str, payload: dict[str, Any], max_attempts: int
    ) -> tuple[int, bool]:
        if max_attempts < 1 or trigger not in ("manual", "scheduled"):
            raise InvalidOperationError("NEXUS:INVALID parámetros de encolado")
        with self._lock:
            for request in self.requests.values():
                if request.key == key:
                    return request.request_id, False
            rid = self._next_id
            self._next_id += 1
            self.requests[rid] = _Request(
                rid, key, scope, trigger, dict(payload), max_attempts, not_before=self._now()
            )
            self._event("ENQUEUED", request_id=rid)
            return rid, True

    def claim(self, worker: str, claim_ms: int) -> Claim | None:
        with self._lock:
            now = self._now()
            pending = sorted(
                (r for r in self.requests.values() if r.state == "PENDING" and r.not_before <= now),
                key=lambda r: (r.not_before, r.request_id),
            )
            if not pending:
                return None
            request = pending[0]
            request.state = "CLAIMED"
            request.claim_token = str(uuid.uuid4())
            request.claim_expires_at = now + timedelta(milliseconds=claim_ms)
            self._event(
                "CLAIMED",
                request_id=request.request_id,
                claim_token=request.claim_token,
                actor=worker,
            )
            return Claim(
                request.request_id,
                request.claim_token,
                request.scope,
                dict(request.payload),
                request.attempts,
                request.max_attempts,
                request.claim_expires_at,
                now,
            )

    def release_claim(self, claim: Claim, backoff_ms: int) -> bool:
        with self._lock:
            request = self.requests.get(claim.request_id)
            if (
                request is None
                or request.state != "CLAIMED"
                or request.claim_token != claim.claim_token
            ):
                return False
            request.state = "PENDING"
            request.claim_token = None
            request.claim_expires_at = None
            request.not_before = self._now() + timedelta(milliseconds=backoff_ms)
            self._event(
                "CLAIM_RELEASED", request_id=request.request_id, claim_token=claim.claim_token
            )
            return True

    def acquire_lease(self, claim: Claim, worker: str, lease_ms: int) -> Lease:
        with self._lock:
            now = self._now()
            request = self.requests.get(claim.request_id)
            if (
                request is None
                or request.state != "CLAIMED"
                or request.claim_token != claim.claim_token
                or request.claim_expires_at is None
                or request.claim_expires_at <= now
            ):
                raise ClaimInvalidError("NEXUS:CLAIM_INVALID claim inexistente, ajeno o vencido")
            sealed = self._sealed_for(request.request_id)
            if request.attempts >= request.max_attempts and sealed is None:
                request.state = "FAILED_PERMANENT"
                request.claim_token = None
                request.claim_expires_at = None
                self._event("FAILED_PERMANENT", request_id=request.request_id)
                return Lease(False, "MAX_ATTEMPTS", None, None, None, None, None, now)
            lease = self.leases.setdefault(request.scope, _Lease())
            if lease.holder is not None:
                return Lease(False, "LEASE_BUSY", None, None, None, None, None, now)
            attempt_id = f"att-{uuid.uuid4().hex}"
            lease.holder = worker
            lease.attempt_id = attempt_id
            lease.token += 1
            lease.expires_at = now + timedelta(milliseconds=lease_ms)
            request.state = "RUNNING"
            request.attempts += 1
            request.claim_expires_at = None
            self.attempts[attempt_id] = _Attempt(
                attempt_id, request.request_id, request.scope, lease.token
            )
            self.generations[attempt_id] = _Generation(request.request_id)
            self._event(
                "LEASE_ACQUIRED",
                request_id=request.request_id,
                attempt_id=attempt_id,
                fencing_token=lease.token,
            )
            return Lease(
                True, None, attempt_id, lease.token, request.attempts, lease.expires_at, sealed, now
            )

    def heartbeat(self, attempt_id: str, token: int, lease_ms: int) -> datetime:
        with self._lock:
            attempt = self._fence(attempt_id, token)
            self.leases[attempt.scope].expires_at = self._now() + timedelta(milliseconds=lease_ms)
            return self._now()

    def register_snapshot_ref(
        self, attempt_id: str, token: int, snapshot_id: str, sha: str
    ) -> bool:
        with self._lock:
            self._fence(attempt_id, token)
            existing = self.snapshots.get(snapshot_id)
            if existing is None:
                self.snapshots[snapshot_id] = sha
                return True
            if existing != sha:
                raise ConflictError("NEXUS:CONFLICT snapshot registrado con otro hash")
            return False

    def register_artifact(
        self,
        attempt_id: str,
        token: int,
        contract: str,
        key: str,
        sha: str,
        logical: str,
        size: int,
    ) -> bool:
        with self._lock:
            self._fence(attempt_id, token)
            row = {
                "object_key": key,
                "content_sha256": sha,
                "logical_hash": logical,
                "size_bytes": size,
            }
            existing = self.artifacts.get((attempt_id, contract))
            if existing is not None:
                same = all(existing[k] == v for k, v in row.items())
                if not same:
                    raise ConflictError("NEXUS:CONFLICT artefacto registrado con otro contenido")
                return False
            if self.generations[attempt_id].state != "OPEN":
                raise InvalidOperationError(
                    "NEXUS:INVALID la generación no admite nuevos artefactos"
                )
            self.artifacts[(attempt_id, contract)] = {**row, "state": "PRESENT"}
            return True

    def append_event(self, attempt_id: str, token: int, kind: str, detail: str) -> None:
        with self._lock:
            attempt = self._fence(attempt_id, token)
            self._event(
                f"WORKER:{kind}",
                request_id=attempt.request_id,
                attempt_id=attempt_id,
                detail=detail,
            )

    def seal(
        self, attempt_id: str, token: int, store_run_id: str, input_digest: str, result_digest: str
    ) -> bool:
        with self._lock:
            gen = self.generations.get(attempt_id)
            if (
                gen is not None
                and gen.sealed
                and gen.store_run_id == store_run_id
                and gen.input_digest == input_digest
                and gen.result_digest == result_digest
            ):
                return False
            self._fence(attempt_id, token)
            if gen is None:
                raise InvalidOperationError("NEXUS:INVALID generación inexistente")
            if gen.state != "OPEN":
                raise InvalidOperationError("NEXUS:INVALID la generación no está abierta")
            if not any(a == attempt_id for a, _ in self.artifacts):
                raise InvalidOperationError(
                    "NEXUS:INVALID no se sella una generación sin artefactos"
                )
            if self._sealed_for(gen.request_id) is not None:
                raise AlreadySealedError("NEXUS:ALREADY_SEALED")
            gen.state = "SEALED"
            gen.sealed = True
            gen.store_run_id = store_run_id
            gen.input_digest = input_digest
            gen.result_digest = result_digest
            self._event("SEALED", request_id=gen.request_id, attempt_id=attempt_id)
            return True

    def complete(self, attempt_id: str, token: int) -> str:
        with self._lock:
            attempt = self.attempts.get(attempt_id)
            if attempt is not None and attempt.state == "COMPLETED" and attempt.token == token:
                request = self.requests[attempt.request_id]
                if request.state == "COMPLETED" and request.completed_attempt_id:
                    return request.completed_attempt_id
            attempt = self._fence(attempt_id, token)
            sealed = self._sealed_for(attempt.request_id)
            if sealed is None:
                raise NotSealedError("NEXUS:NOT_SEALED")
            attempt.state = "COMPLETED"
            if self.generations[attempt_id].state == "OPEN":
                self.generations[attempt_id].state = "ABANDONED"
            request = self.requests[attempt.request_id]
            if request.state == "RUNNING":
                request.state = "COMPLETED"
                request.completed_attempt_id = sealed
            lease = self.leases[attempt.scope]
            lease.holder = lease.attempt_id = None
            lease.expires_at = None
            self._event("COMPLETED", request_id=attempt.request_id, attempt_id=attempt_id)
            return sealed

    def fail(self, attempt_id: str, token: int, reason: str, backoff_ms: int) -> None:
        with self._lock:
            attempt = self._fence(attempt_id, token)
            attempt.state = "FAILED"
            attempt.failure = reason
            if self.generations[attempt_id].state == "OPEN":
                self.generations[attempt_id].state = "ABANDONED"
            request = self.requests[attempt.request_id]
            if request.state == "RUNNING":
                self._after_failure(request, backoff_ms)
            lease = self.leases[attempt.scope]
            lease.holder = lease.attempt_id = None
            lease.expires_at = None
            self._event(
                "FAILED", request_id=attempt.request_id, attempt_id=attempt_id, detail=reason
            )

    def recover_expired(self, backoff_ms: int) -> tuple[int, int]:
        with self._lock:
            now = self._now()
            leases = claims = 0
            for lease in self.leases.values():
                if (
                    lease.holder is not None
                    and lease.expires_at is not None
                    and lease.expires_at < now
                ):
                    attempt = self.attempts.get(lease.attempt_id or "")
                    lease.holder = lease.attempt_id = None
                    lease.expires_at = None
                    lease.token += 1
                    leases += 1
                    if attempt is not None and attempt.state == "RUNNING":
                        attempt.state = "FAILED"
                        attempt.failure = "LEASE_EXPIRED"
                        if self.generations[attempt.attempt_id].state == "OPEN":
                            self.generations[attempt.attempt_id].state = "ABANDONED"
                        request = self.requests[attempt.request_id]
                        if request.state == "RUNNING":
                            self._after_failure(request, backoff_ms)
                        self._event(
                            "LEASE_RECOVERED",
                            request_id=attempt.request_id,
                            attempt_id=attempt.attempt_id,
                        )
            for request in self.requests.values():
                if (
                    request.state == "CLAIMED"
                    and request.claim_expires_at is not None
                    and request.claim_expires_at < now
                ):
                    self._event(
                        "CLAIM_RECOVERED",
                        request_id=request.request_id,
                        claim_token=request.claim_token,
                    )
                    request.state = "PENDING"
                    request.claim_token = None
                    request.claim_expires_at = None
                    request.not_before = now + timedelta(milliseconds=backoff_ms)
                    claims += 1
            return leases, claims

    def mark_artifact_state(self, attempt_id: str, contract: str, state: str, detail: str) -> bool:
        if state not in ("MISSING", "CORRUPT"):
            raise InvalidOperationError("NEXUS:INVALID solo se admite MISSING o CORRUPT")
        with self._lock:
            row = self.artifacts.get((attempt_id, contract))
            if row is None:
                raise InvalidOperationError("NEXUS:INVALID artefacto inexistente")
            if row["state"] == state:
                return False
            if row["state"] != "PRESENT":
                raise InvalidOperationError("NEXUS:INVALID transición de estado no permitida")
            row["state"] = state
            gen = self.generations[attempt_id]
            if gen.state == "SEALED":
                gen.state = "INELIGIBLE"
            self._event(f"RECONCILE_{state}", attempt_id=attempt_id, detail=f"{contract} {detail}")
            return True

    def list_artifacts(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = []
            for (attempt_id, contract), row in sorted(self.artifacts.items()):
                gen = self.generations[attempt_id]
                rows.append(
                    {
                        "attempt_id": attempt_id,
                        "contract": contract,
                        "object_key": row["object_key"],
                        "content_sha256": row["content_sha256"],
                        "artifact_state": row["state"],
                        "generation_state": gen.state,
                        "store_run_id": gen.store_run_id,
                        "request_id": gen.request_id,
                    }
                )
            return rows

    def list_snapshot_refs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"snapshot_id": sid, "content_sha256": sha}
                for sid, sha in sorted(self.snapshots.items())
            ]

    def list_generations(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "attempt_id": attempt_id,
                    "request_id": gen.request_id,
                    "generation_state": gen.state,
                    "store_run_id": gen.store_run_id,
                    "input_digest": gen.input_digest,
                    "result_digest": gen.result_digest,
                    "sealed_at": "sealed" if gen.sealed else None,
                }
                for attempt_id, gen in sorted(self.generations.items())
            ]

    def get_request(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            for r in self.requests.values():
                if r.key == key:
                    return {
                        "request_id": r.request_id,
                        "state": r.state,
                        "attempts": r.attempts,
                        "max_attempts": r.max_attempts,
                        "completed_attempt_id": r.completed_attempt_id,
                    }
            return None

    def close(self) -> None:
        return None


def monotonic_clock(start: datetime | None = None) -> Callable[[], datetime]:
    """Reloj basado en monotonic para pruebas (no depende de la hora del sistema)."""
    base = start or datetime(2026, 1, 1, tzinfo=UTC)
    origin = time.monotonic()
    return lambda: base + timedelta(seconds=time.monotonic() - origin)
