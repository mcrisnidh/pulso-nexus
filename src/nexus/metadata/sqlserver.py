"""Repositorio de metadatos sobre SQL Server (T-SQL explícito vía procedimientos).

Reglas del cliente (E0-R2 §2.5.1, NX-E1B-03):
- Toda operación es un procedimiento con autocommit; las transacciones viven en el servidor.
- Ante CUALQUIER error o timeout se DESCARTA la conexión (nunca se reutiliza en estado dudoso).
- Deadlock (1205/40001): reintento acotado con backoff.
- Commit ambiguo (se pierde la respuesta): primero se consulta el estado por clave natural y
  después se repite la operación idempotente; nunca se duplica un efecto.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from nexus.hashing import canonical_json
from nexus.metadata.connection import ConnectionSettings, connect
from nexus.metadata.errors import (
    AlreadySealedError,
    AmbiguousOutcomeError,
    ClaimInvalidError,
    ConflictError,
    FencedError,
    InvalidOperationError,
    MetadataError,
    NotSealedError,
)
from nexus.metadata.guards import runtime_guard
from nexus.metadata.profile import Profile
from nexus.metadata.repository import Claim, Lease

FaultHook = Callable[[str, str], None]

_ERRORS: tuple[tuple[str, type[MetadataError]], ...] = (
    ("NEXUS:FENCED", FencedError),
    ("NEXUS:CLAIM_INVALID", ClaimInvalidError),
    ("NEXUS:CONFLICT", ConflictError),
    ("NEXUS:ALREADY_SEALED", AlreadySealedError),
    ("NEXUS:NOT_SEALED", NotSealedError),
    ("NEXUS:INVALID", InvalidOperationError),
)
_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
DEADLOCK_RETRIES = 5
_SESSION_ISOLATION = {None, "READ COMMITTED", "SNAPSHOT"}


def classify(exc: Exception) -> Exception:
    text = " ".join(str(a) for a in getattr(exc, "args", ()))
    for token, cls in _ERRORS:
        if token in text:
            return cls(text)
    if "uq_generation_one_sealed_per_request" in text:
        return AlreadySealedError(text)
    return exc


def is_deadlock(exc: Exception) -> bool:
    args: tuple[Any, ...] = tuple(getattr(exc, "args", ()))
    sqlstate = str(args[0]) if args else ""
    return sqlstate == "40001" or "(1205)" in " ".join(str(a) for a in args)


def is_link_failure(exc: Exception) -> bool:
    args: tuple[Any, ...] = tuple(getattr(exc, "args", ()))
    return bool(args) and str(args[0]) in {"08S01", "08001", "HYT00", "HYT01", "08003"}


class SqlServerMetadata:
    def __init__(
        self,
        settings: ConnectionSettings,
        profile: Profile,
        nonce: str,
        *,
        actor: str = "nexus",
        session_isolation: str | None = None,
        proc_schema: str = "nexus",
        fault_hook: FaultHook | None = None,
    ) -> None:
        if session_isolation not in _SESSION_ISOLATION:
            raise MetadataError(f"aislamiento de sesión no soportado: {session_isolation}")
        if not _NAME.match(proc_schema):
            raise MetadataError("esquema de procedimientos inválido")
        self._settings = settings
        self._profile = profile
        self._nonce = nonce
        self._actor = actor
        self._isolation = session_isolation
        self._schema = proc_schema
        self._fault = fault_hook
        self._conn: Any = None
        self.deadlock_retries = 0  # evidencia de B14

    # ---------------------------------------------------------------- conexión
    def _connection(self) -> Any:
        if self._conn is None:
            conn = connect(self._settings, self._profile.query_timeout_s)
            try:
                runtime_guard(conn, self._profile, self._nonce, self._settings.host)
                if self._isolation is not None:
                    conn.cursor().execute(f"SET TRANSACTION ISOLATION LEVEL {self._isolation}")
            except Exception:
                conn.close()
                raise
            self._conn = conn
        return self._conn

    def discard(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):  # descartar nunca oculta el error original
                conn.close()

    def close(self) -> None:
        self.discard()

    def _call(self, proc: str, **params: Any) -> list[dict[str, Any]]:
        if not _NAME.match(proc):
            raise MetadataError(f"procedimiento inválido: {proc}")
        names = list(params)
        for name in names:
            if not _NAME.match(name):
                raise MetadataError(f"parámetro inválido: {name}")
        sql = f"EXEC {self._schema}.{proc} " + ", ".join(f"@{n} = ?" for n in names)
        delay = 0.05
        for attempt in range(DEADLOCK_RETRIES + 1):
            try:
                cursor = self._connection().cursor()
                cursor.execute(sql, *[params[n] for n in names])
                rows = _fetch_first_resultset(cursor)
            except Exception as exc:
                self.discard()
                if is_deadlock(exc) and attempt < DEADLOCK_RETRIES:
                    self.deadlock_retries += 1
                    time.sleep(delay)
                    delay *= 2
                    continue
                if is_link_failure(exc):
                    raise AmbiguousOutcomeError(f"{proc}: {exc}") from exc
                raise classify(exc) from exc
            if self._fault is not None:
                self._fault("after_commit", proc)
            return rows
        raise MetadataError("inalcanzable")  # pragma: no cover

    def _resolving(
        self, proc: str, resolve: Callable[[], Any], **params: Any
    ) -> list[dict[str, Any]]:
        """Llama a un procedimiento idempotente; ante commit ambiguo consulta y repite."""
        try:
            return self._call(proc, **params)
        except AmbiguousOutcomeError:
            resolve()
            return self._call(proc, **params)

    # ---------------------------------------------------------------- API
    def enqueue(
        self, key: str, scope: str, trigger: str, payload: dict[str, Any], max_attempts: int
    ) -> tuple[int, bool]:
        try:
            rows = self._call(
                "usp_enqueue_request",
                idempotency_key=key,
                scope=scope,
                trigger_kind=trigger,
                payload=canonical_json(payload).decode("utf-8"),
                max_attempts=max_attempts,
                actor=self._actor,
            )
        except AmbiguousOutcomeError:
            existing = self.get_request(
                key
            )  # consulta por clave de idempotencia ANTES de reintentar
            if existing is not None:
                return int(existing["request_id"]), False
            rows = self._call(
                "usp_enqueue_request",
                idempotency_key=key,
                scope=scope,
                trigger_kind=trigger,
                payload=canonical_json(payload).decode("utf-8"),
                max_attempts=max_attempts,
                actor=self._actor,
            )
        return int(rows[0]["request_id"]), bool(rows[0]["created"])

    def claim(self, worker: str, claim_ms: int) -> Claim | None:
        rows = self._call("usp_claim_request", worker=worker, claim_ms=claim_ms)
        if not rows:
            return None
        r = rows[0]
        return Claim(
            int(r["request_id"]),
            str(r["claim_token"]).lower(),
            str(r["scope"]),
            json.loads(r["payload"]),
            int(r["attempts"]),
            int(r["max_attempts"]),
            r["claim_expires_at"],
            r["server_ts"],
        )

    def release_claim(self, claim: Claim, backoff_ms: int) -> bool:
        rows = self._resolving(
            "usp_release_claim",
            lambda: None,
            request_id=claim.request_id,
            claim_token=claim.claim_token,
            backoff_ms=backoff_ms,
            actor=self._actor,
        )
        return bool(rows[0]["released"])

    def acquire_lease(self, claim: Claim, worker: str, lease_ms: int) -> Lease:
        r = self._call(
            "usp_acquire_lease",
            request_id=claim.request_id,
            claim_token=claim.claim_token,
            worker=worker,
            lease_ms=lease_ms,
        )[0]
        return Lease(
            bool(r["acquired"]),
            r["reason"],
            r["attempt_id"],
            None if r["fencing_token"] is None else int(r["fencing_token"]),
            None if r["attempt_no"] is None else int(r["attempt_no"]),
            r["expires_at"],
            r["sealed_attempt_id"],
            r["server_ts"],
        )

    def heartbeat(self, attempt_id: str, token: int, lease_ms: int) -> datetime:
        rows = self._call(
            "usp_heartbeat", attempt_id=attempt_id, fencing_token=token, lease_ms=lease_ms
        )
        server_ts: datetime = rows[0]["server_ts"]
        return server_ts

    def register_snapshot_ref(
        self, attempt_id: str, token: int, snapshot_id: str, sha: str
    ) -> bool:
        rows = self._resolving(
            "usp_register_snapshot_ref",
            lambda: None,
            attempt_id=attempt_id,
            fencing_token=token,
            snapshot_id=snapshot_id,
            content_sha256=sha,
        )
        return bool(rows[0]["created"])

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
        rows = self._resolving(
            "usp_register_artifact",
            lambda: None,
            attempt_id=attempt_id,
            fencing_token=token,
            contract=contract,
            object_key=key,
            content_sha256=sha,
            logical_hash=logical,
            size_bytes=size,
        )
        return bool(rows[0]["created"])

    def append_event(self, attempt_id: str, token: int, kind: str, detail: str) -> None:
        self._call(
            "usp_append_run_event",
            attempt_id=attempt_id,
            fencing_token=token,
            kind=kind,
            detail=detail,
        )

    def seal(
        self, attempt_id: str, token: int, store_run_id: str, input_digest: str, result_digest: str
    ) -> bool:
        rows = self._resolving(
            "usp_seal_generation",
            lambda: None,
            attempt_id=attempt_id,
            fencing_token=token,
            store_run_id=store_run_id,
            input_digest=input_digest,
            result_digest=result_digest,
        )
        return bool(rows[0]["sealed_now"])

    def complete(self, attempt_id: str, token: int) -> str:
        rows = self._resolving(
            "usp_complete_run", lambda: None, attempt_id=attempt_id, fencing_token=token
        )
        return str(rows[0]["completed_attempt_id"])

    def fail(self, attempt_id: str, token: int, reason: str, backoff_ms: int) -> None:
        self._call(
            "usp_fail_run",
            attempt_id=attempt_id,
            fencing_token=token,
            reason=reason[:400],
            backoff_ms=backoff_ms,
        )

    def recover_expired(self, backoff_ms: int) -> tuple[int, int]:
        r = self._call("usp_recover_expired", backoff_ms=backoff_ms, actor=self._actor)[0]
        return int(r["leases_recovered"]), int(r["claims_recovered"])

    def mark_artifact_state(self, attempt_id: str, contract: str, state: str, detail: str) -> bool:
        rows = self._resolving(
            "usp_mark_artifact_state",
            lambda: None,
            attempt_id=attempt_id,
            contract=contract,
            new_state=state,
            detail=detail[:300],
            actor=self._actor,
        )
        return bool(rows[0]["changed"])

    def list_artifacts(self) -> list[dict[str, Any]]:
        return self._call("usp_list_artifacts")

    def get_request(self, key: str) -> dict[str, Any] | None:
        rows = self._call("usp_get_request", idempotency_key=key)
        return rows[0] if rows else None


def _fetch_first_resultset(cursor: Any) -> list[dict[str, Any]]:
    while True:
        if cursor.description is not None:
            columns = [c[0] for c in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        if not cursor.nextset():
            return []
