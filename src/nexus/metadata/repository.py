"""Contrato del repositorio de metadatos (C1/C2/C3, NX-E1B-03).

C1 Exclusividad de reclamación: como máximo un claim vigente por solicitud.
C2 Reintentos de ejecución: al menos una vez por intento; NO hay "exactamente una ejecución física".
C3 Idempotencia de efectos: como máximo una generación sellada por solicitud; artefactos
   idempotentes por (intento, contrato); la solicitud se completa una sola vez.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class Claim:
    request_id: int
    claim_token: str
    scope: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    claim_expires_at: datetime
    server_ts: datetime


@dataclass(frozen=True)
class Lease:
    acquired: bool
    reason: str | None
    attempt_id: str | None
    fencing_token: int | None
    attempt_no: int | None
    expires_at: datetime | None
    sealed_attempt_id: str | None
    server_ts: datetime


class MetadataRepository(Protocol):
    def enqueue(
        self, key: str, scope: str, trigger: str, payload: dict[str, Any], max_attempts: int
    ) -> tuple[int, bool]: ...

    def claim(self, worker: str, claim_ms: int) -> Claim | None: ...

    def release_claim(self, claim: Claim, backoff_ms: int) -> bool: ...

    def acquire_lease(self, claim: Claim, worker: str, lease_ms: int) -> Lease: ...

    def heartbeat(self, attempt_id: str, token: int, lease_ms: int) -> datetime: ...

    def register_snapshot_ref(
        self, attempt_id: str, token: int, snapshot_id: str, sha: str
    ) -> bool: ...

    def register_artifact(
        self,
        attempt_id: str,
        token: int,
        contract: str,
        key: str,
        sha: str,
        logical: str,
        size: int,
    ) -> bool: ...

    def append_event(self, attempt_id: str, token: int, kind: str, detail: str) -> None: ...

    def seal(
        self, attempt_id: str, token: int, store_run_id: str, input_digest: str, result_digest: str
    ) -> bool: ...

    def complete(self, attempt_id: str, token: int) -> str: ...

    def fail(self, attempt_id: str, token: int, reason: str, backoff_ms: int) -> None: ...

    def recover_expired(self, backoff_ms: int) -> tuple[int, int]: ...

    def mark_artifact_state(
        self, attempt_id: str, contract: str, state: str, detail: str
    ) -> bool: ...

    def list_artifacts(self) -> list[dict[str, Any]]: ...

    def list_snapshot_refs(self) -> list[dict[str, Any]]: ...

    def list_generations(self) -> list[dict[str, Any]]: ...

    def get_request(self, key: str) -> dict[str, Any] | None: ...

    def close(self) -> None: ...
