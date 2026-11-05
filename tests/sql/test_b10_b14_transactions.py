"""B10 (transacciones interrumpidas), B11 (commit ambiguo), B13 (reloj del servidor) y
B14 (deadlock) contra SQL Server real."""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import subprocess
import sys
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

from conftest import ROOT
from nexus.metadata.errors import AmbiguousOutcomeError, FencedError
from nexus.metadata.sqlserver import SqlServerMetadata

pytestmark = pytest.mark.sql

TEST_OBJECTS = [
    "IF SCHEMA_ID(N'nexus_test') IS NULL EXEC(N'CREATE SCHEMA nexus_test AUTHORIZATION dbo')",
    """CREATE OR ALTER PROCEDURE nexus_test.usp_slow_update @request_id bigint, @delay char(8)
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON; SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    BEGIN TRANSACTION;
    UPDATE nexus.run_request SET payload = N'INTERRUMPIDO' WHERE request_id = @request_id;
    WAITFOR DELAY @delay;
    COMMIT TRANSACTION;
    SELECT 1 AS done;
END""",
    "IF OBJECT_ID(N'nexus_test.dl') IS NULL CREATE TABLE nexus_test.dl (name nvarchar(8) PRIMARY KEY, n int NOT NULL)",
    "IF OBJECT_ID(N'nexus_test.dl_effect') IS NULL CREATE TABLE nexus_test.dl_effect (tag nvarchar(8) PRIMARY KEY)",
    """CREATE OR ALTER PROCEDURE nexus_test.usp_lock_pair @first nvarchar(8), @second nvarchar(8), @tag nvarchar(8)
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON; SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    BEGIN TRANSACTION;
    UPDATE nexus_test.dl SET n = n + 1 WHERE name = @first;
    WAITFOR DELAY '00:00:01';
    UPDATE nexus_test.dl SET n = n + 1 WHERE name = @second;
    INSERT INTO nexus_test.dl_effect (tag) VALUES (@tag);
    COMMIT TRANSACTION;
    SELECT 1 AS done;
END""",
    "GRANT EXECUTE ON SCHEMA::nexus_test TO nexus_producer_role",
]

LOCKS_SQL = (
    "SELECT COUNT(*) FROM sys.dm_tran_locks WHERE resource_database_id = DB_ID() "
    "AND request_session_id <> @@SPID AND resource_type IN ('KEY', 'RID', 'PAGE')"
)


@pytest.fixture
def test_objects(admin: Any) -> None:
    for statement in TEST_OBJECTS:
        admin.cursor().execute(statement)


def _payload(admin: Any, rid: int) -> str:
    return str(
        admin.cursor()
        .execute("SELECT payload FROM nexus.run_request WHERE request_id = ?", rid)
        .fetchone()[0]
    )


def _wait_no_locks(admin: Any, seconds: float = 20) -> int:
    deadline = time.monotonic() + seconds
    while True:
        locks = count(admin, LOCKS_SQL)
        if locks == 0 or time.monotonic() > deadline:
            return locks
        time.sleep(0.25)


def test_b10a_client_timeout_attention_rolls_back(
    test_objects: None,
    repo_factory: Any,
    admin: Any,
    base_settings: Any,
    profile: Any,
    sql_env: dict[str, str],
) -> None:
    rid, _ = repo_factory().enqueue("b10a", "s", "manual", {"v": 1}, 1)
    original = _payload(admin, rid)
    short = dataclasses.replace(profile, query_timeout_s=2)
    repo = SqlServerMetadata(
        base_settings, short, sql_env["NEXUS_SQL_NONCE"], proc_schema="nexus_test"
    )
    with pytest.raises(AmbiguousOutcomeError):
        repo._call("usp_slow_update", request_id=rid, delay="00:00:15")
    assert repo._conn is None  # conexión descartada, nunca reutilizada
    assert _wait_no_locks(admin) == 0
    assert _payload(admin, rid) == original


CHILD = """
import os, sys
from nexus.metadata.connection import ConnectionSettings
from nexus.metadata.profile import load_profile
from nexus.metadata.sqlserver import SqlServerMetadata
s = ConnectionSettings(host=os.environ["NEXUS_SQL_HOST"], port=int(os.environ.get("NEXUS_SQL_PORT", "1433")),
    database=os.environ["NEXUS_SQL_DATABASE"], user="nexus_producer",
    password=os.environ["NEXUS_SQL_PRODUCER_PASSWORD"])
r = SqlServerMetadata(s, load_profile("e1b-ephemeral"), os.environ["NEXUS_SQL_NONCE"], proc_schema="nexus_test")
spid = r._connection().cursor().execute("SELECT @@SPID").fetchone()[0]
print(spid, flush=True)
r._call("usp_slow_update", request_id=int(sys.argv[1]), delay="00:00:25")
"""


@pytest.mark.parametrize("how", ["client_sigkill", "server_kill_session"])
def test_b10b_interrupted_transaction_rolls_back(
    test_objects: None, repo_factory: Any, admin: Any, how: str
) -> None:
    rid, _ = repo_factory().enqueue(f"b10-{how}", "s", "manual", {"v": 2}, 1)
    original = _payload(admin, rid)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(rid)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    spid = int(child.stdout.readline().strip())
    deadline = time.monotonic() + 10
    while count(admin, LOCKS_SQL) == 0 and time.monotonic() < deadline:
        time.sleep(0.1)
    held = count(admin, LOCKS_SQL)
    assert held > 0, "la transacción interrumpida debía tener bloqueos activos"
    if how == "client_sigkill":
        child.send_signal(signal.SIGKILL)
    else:
        admin.cursor().execute(f"KILL {spid}")
    child.wait(timeout=60)
    remaining = _wait_no_locks(admin)
    print(json.dumps({"B10": how, "locks_during": held, "locks_after": remaining}))
    assert remaining == 0
    assert _payload(admin, rid) == original


def test_b11_ambiguous_commit_never_duplicates(repo_factory: Any, admin: Any) -> None:
    repo = repo_factory(actor="amb")
    pending = {
        "usp_enqueue_request",
        "usp_release_claim",
        "usp_register_snapshot_ref",
        "usp_register_artifact",
        "usp_seal_generation",
        "usp_complete_run",
    }
    fired: list[str] = []

    def lose_ack(stage: str, proc: str) -> None:
        if stage == "after_commit" and proc in pending:
            pending.discard(proc)
            fired.append(proc)
            repo.discard()
            raise AmbiguousOutcomeError(f"respuesta perdida tras COMMIT de {proc}")

    repo._fault = lose_ack
    _, created = repo.enqueue("b11", "s", "manual", {}, 3)
    assert created is False  # el estado se consultó: ya existía
    claim = repo.claim("amb", 30000)
    assert claim is not None
    repo.release_claim(claim, 0)
    claim = repo.claim("amb", 30000)
    assert claim is not None
    lease = repo.acquire_lease(claim, "amb", 60000)
    att, tok = str(lease.attempt_id), int(lease.fencing_token or 0)
    repo.register_snapshot_ref(att, tok, "snap-b11", "a" * 64)
    repo.register_artifact(att, tok, "c@1", "k", "a" * 64, "a" * 64, 1)
    repo.seal(att, tok, "run-b11", "a" * 64, "a" * 64)
    repo.complete(att, tok)
    assert sorted(fired) == sorted(
        {
            "usp_enqueue_request",
            "usp_release_claim",
            "usp_register_snapshot_ref",
            "usp_register_artifact",
            "usp_seal_generation",
            "usp_complete_run",
        }
    )
    checks = {
        "requests": count(admin, "SELECT COUNT(*) FROM nexus.run_request"),
        "enqueued_events": count(
            admin, "SELECT COUNT(*) FROM nexus.run_event WHERE kind = N'ENQUEUED'"
        ),
        "released_events": count(
            admin, "SELECT COUNT(*) FROM nexus.run_event WHERE kind = N'CLAIM_RELEASED'"
        ),
        "snapshots": count(admin, "SELECT COUNT(*) FROM nexus.snapshot_ref"),
        "artifacts": count(admin, "SELECT COUNT(*) FROM nexus.artifact"),
        "sealed_events": count(
            admin, "SELECT COUNT(*) FROM nexus.run_event WHERE kind = N'SEALED'"
        ),
        "completed_events": count(
            admin, "SELECT COUNT(*) FROM nexus.run_event WHERE kind = N'COMPLETED'"
        ),
    }
    print(json.dumps({"B11": checks}))
    assert set(checks.values()) == {1}
    assert repo.get_request("b11")["state"] == "COMPLETED"  # type: ignore[index]


def test_b13_server_clock_only(
    repo_factory: Any, admin: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    params = count(
        admin,
        "SELECT COUNT(*) FROM sys.parameters p JOIN sys.procedures pr ON pr.object_id = p.object_id "
        "JOIN sys.types t ON t.user_type_id = p.user_type_id WHERE SCHEMA_NAME(pr.schema_id) = N'nexus' "
        "AND t.name IN (N'datetime', N'datetime2', N'smalldatetime', N'date', N'time', N'datetimeoffset')",
    )
    assert params == 0  # ningún procedimiento acepta una hora del cliente
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 3600)  # cliente desfasado +1 h
    repo = repo_factory()
    repo.enqueue("b13", "s", "manual", {}, 3)
    claim = repo.claim("c", 30000)
    assert claim is not None
    lease = repo.acquire_lease(claim, "c", 5000)
    assert repo.recover_expired(0) == (0, 0)  # el servidor decide: aún no vence
    assert count(admin, "SELECT COUNT(*) FROM nexus.run_event WHERE server_ts IS NULL") == 0
    wait_server_past(admin, lease.expires_at)
    assert repo.recover_expired(0) == (1, 0)


def test_b13_lease_lost_by_latency_is_classified(repo_factory: Any, admin: Any) -> None:
    """Un heartbeat que llega tarde (hueco > lease, medido en hora del SERVIDOR) debe provocar la
    pérdida del lease y el rechazo por fencing: es el comportamiento correcto, no un defecto."""
    repo = repo_factory()
    repo.enqueue("b13l", "s", "manual", {}, 3)
    claim = repo.claim("slow", 30000)
    assert claim is not None
    lease = repo.acquire_lease(claim, "slow", 5000)
    last_ok = repo.heartbeat(str(lease.attempt_id), int(lease.fencing_token or 0), 5000)
    wait_server_past(admin, last_ok, margin_ms=5500)  # "latencia" del CI simulada
    with pytest.raises(FencedError):
        repo.heartbeat(str(lease.attempt_id), int(lease.fencing_token or 0), 5000)
    gap_ms = (server_now(admin) - last_ok).total_seconds() * 1000
    print(
        json.dumps(
            {"B13": "LEASE_LOST_BY_LATENCY", "server_gap_ms": round(gap_ms), "lease_ms": 5000}
        )
    )
    assert gap_ms > 5000


def test_b14_deadlock_retried_without_duplicate_effects(
    test_objects: None, repo_factory: Any, admin: Any
) -> None:
    cur = admin.cursor()
    cur.execute(
        "DELETE FROM nexus_test.dl_effect; DELETE FROM nexus_test.dl;"
        " INSERT INTO nexus_test.dl (name, n) VALUES (N'A', 0), (N'B', 0)"
    )
    repos = [repo_factory(proc_schema="nexus_test"), repo_factory(proc_schema="nexus_test")]
    errors: list[str] = []
    barrier = threading.Barrier(2)

    def go(i: int, first: str, second: str, tag: str) -> None:
        barrier.wait()
        try:
            repos[i]._call("usp_lock_pair", first=first, second=second, tag=tag)
        except Exception as exc:
            errors.append(repr(exc))

    threads = [
        threading.Thread(target=go, args=(0, "A", "B", "ab")),
        threading.Thread(target=go, args=(1, "B", "A", "ba")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    retries = sum(r.deadlock_retries for r in repos)
    values = dict(cur.execute("SELECT name, n FROM nexus_test.dl").fetchall())
    effects = sorted(r[0] for r in cur.execute("SELECT tag FROM nexus_test.dl_effect").fetchall())
    print(json.dumps({"B14": {"deadlock_retries": retries, "values": values, "effects": effects}}))
    assert errors == []
    assert retries >= 1, "debía producirse al menos un deadlock 1205"
    assert values == {"A": 2, "B": 2} and effects == ["ab", "ba"]
