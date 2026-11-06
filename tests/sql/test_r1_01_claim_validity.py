"""NX-E1B-R1-01: un claim que vence MIENTRAS se prepara la adquisición no puede obtener el lease.

La adquisición se retrasa deliberadamente con un bloqueo real de otra sesión hasta después del
vencimiento del claim (hora del SERVIDOR) en dos puntos:

- ``request_row``: la fila de la solicitud (UPDLOCK de otra transacción).
- ``lease_row``: la fila de lease del ámbito (X de otra transacción).

Se ejecuta contra:

- el procedimiento ORIGINAL del candidato ``531107c`` (copia congelada en
  ``tests/regress/procedures_531107c.sql``): la prueba AFIRMA que el defecto se reproduce, es decir,
  que se concede un lease con el claim caducado;
- el procedimiento de producción corregido: no se concede, no quedan efectos y el claim vencido se
  recupera sin consumir intento.
"""

from __future__ import annotations

import threading
import time
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
from sql_variants import install_variant, record_evidence

from conftest import ROOT
from nexus.metadata.connection import connect
from nexus.metadata.errors import ClaimInvalidError

pytestmark = pytest.mark.sql

ORIGINAL = (ROOT / "tests" / "regress" / "procedures_531107c.sql").read_text("utf-8")
CLAIM_MS = 2000
BLOCKERS = ["request_row", "lease_row"]


def _hold_lock(admin_settings: Any, blocker: str, request_id: int, scope: str) -> tuple[Any, int]:
    conn = connect(admin_settings, 120)
    cur = conn.cursor()
    spid = int(cur.execute("SELECT @@SPID").fetchone()[0])
    cur.execute("BEGIN TRANSACTION")
    if blocker == "request_row":
        cur.execute(
            "SELECT request_id FROM nexus.run_request WITH (UPDLOCK, ROWLOCK, INDEX(1)) "
            "WHERE request_id = ?",
            request_id,
        ).fetchall()
    else:
        cur.execute(
            "UPDATE nexus.scope_lease SET fencing_token = fencing_token WHERE scope = ?", scope
        )
    return conn, spid


def _delayed_acquire(
    admin: Any, admin_settings: Any, repo_factory: Any, schema: str, blocker: str, tag: str
) -> dict[str, Any]:
    scope = f"r101-{tag}"
    seeder = repo_factory()
    seeder.enqueue(f"r101-{tag}", scope, "manual", {}, 3)
    if blocker == "lease_row":  # la fila de lease ya existe (p. ej., de un intento anterior)
        admin.cursor().execute("INSERT INTO nexus.scope_lease (scope) VALUES (?)", scope)
    repo = repo_factory(proc_schema=schema, actor="lento")
    claim = repo.claim("lento", CLAIM_MS)
    assert claim is not None
    locker, locker_spid = _hold_lock(admin_settings, blocker, claim.request_id, scope)
    outcome: dict[str, Any] = {}

    def acquire() -> None:
        try:
            lease = repo.acquire_lease(claim, "lento", 60000)
            outcome["acquired"] = lease.acquired
            outcome["reason"] = lease.reason
        except ClaimInvalidError as exc:
            outcome["claim_invalid"] = str(exc)[:200]
        except Exception as exc:
            outcome["error"] = f"{type(exc).__name__}: {exc}"[:300]

    worker = threading.Thread(target=acquire)
    try:
        worker.start()
        deadline = time.monotonic() + 20
        blocked = 0
        while time.monotonic() < deadline and not blocked:
            blocked = count(
                admin,
                "SELECT COUNT(*) FROM sys.dm_exec_requests WHERE blocking_session_id = ?",
                locker_spid,
            )
            time.sleep(0.05)
        wait_server_past(admin, claim.claim_expires_at, margin_ms=500)
        released_at = server_now(admin)
    finally:
        locker.cursor().execute("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION")
        locker.close()
    worker.join(60)
    cur = admin.cursor()
    lease_events = cur.execute(
        "SELECT server_ts FROM nexus.run_event WHERE kind = N'LEASE_ACQUIRED' AND request_id = ?",
        claim.request_id,
    ).fetchall()
    return {
        "blocker": blocker,
        "schema": schema,
        "acquisition_was_blocked": bool(blocked),
        "claim_expires_at": str(claim.claim_expires_at),
        "released_at_server": str(released_at),
        "outcome": outcome,
        "lease_acquired_events": [str(r[0]) for r in lease_events],
        "granted_after_claim_expiry": any(r[0] > claim.claim_expires_at for r in lease_events),
        "attempts_rows": count(
            admin, "SELECT COUNT(*) FROM nexus.attempt WHERE request_id = ?", claim.request_id
        ),
        "generation_rows": count(
            admin, "SELECT COUNT(*) FROM nexus.generation WHERE request_id = ?", claim.request_id
        ),
        "lease_holder": cur.execute(
            "SELECT holder FROM nexus.scope_lease WHERE scope = ?", scope
        ).fetchone(),
        "request": repo.get_request(f"r101-{tag}"),
        "repo": repo,
        "request_id": claim.request_id,
    }


@pytest.mark.parametrize("blocker", BLOCKERS)
def test_r1_01_original_candidate_reproduces_expired_claim_lease(
    admin: Any, admin_settings: Any, repo_factory: Any, blocker: str
) -> None:
    install_variant(admin, "nexus_r1_original", source=ORIGINAL)
    ev = _delayed_acquire(admin, admin_settings, repo_factory, "nexus_r1_original", blocker, "o")
    ev.pop("repo")
    record_evidence("r1_01_claim_validity", {"R1_01": "original_531107c", **ev})
    assert ev["acquisition_was_blocked"], "la adquisición debía quedar retrasada por el bloqueo"
    # Defecto reproducido: el procedimiento original concede el lease con el claim ya vencido.
    assert ev["outcome"].get("acquired") is True, ev
    assert ev["granted_after_claim_expiry"], ev


@pytest.mark.parametrize("blocker", BLOCKERS)
def test_r1_01_expired_claim_never_acquires_lease(
    admin: Any, admin_settings: Any, repo_factory: Any, blocker: str
) -> None:
    ev = _delayed_acquire(admin, admin_settings, repo_factory, "nexus", blocker, "p")
    repo, rid = ev.pop("repo"), ev.pop("request_id")
    recovered = repo.recover_expired(0)
    after = repo.get_request("r101-p")
    record_evidence(
        "r1_01_claim_validity",
        {"R1_01": "corrected", **ev, "recovered": recovered, "request_after_recover": after},
    )
    assert ev["acquisition_was_blocked"], "la adquisición debía quedar retrasada por el bloqueo"
    assert "claim_invalid" in ev["outcome"], ev
    # Sin efectos inconsistentes: ni intento, ni generación, ni evento, ni lease tomado.
    assert ev["lease_acquired_events"] == []
    assert ev["attempts_rows"] == 0 and ev["generation_rows"] == 0
    assert ev["lease_holder"] is None or ev["lease_holder"][0] is None
    assert ev["request"]["state"] == "CLAIMED" and ev["request"]["attempts"] == 0
    # El claim vencido se recupera sin consumir intento.
    assert recovered == (0, 1)
    assert after is not None and after["state"] == "PENDING" and after["attempts"] == 0
    assert (
        count(
            admin,
            "SELECT COUNT(*) FROM nexus.run_event WHERE kind = N'CLAIM_RECOVERED' AND request_id = ?",
            rid,
        )
        == 1
    )
