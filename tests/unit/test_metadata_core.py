"""E1-B, pruebas unitarias SIN SQL Server (también en Windows): validador TLS, perfiles,
guards con conexión simulada, migraciones y semántica C1/C2/C3 del doble en memoria.

Estas pruebas NO certifican garantías transaccionales (eso es tests/sql/ contra SQL Server)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexus.metadata.connection import ConnectionSettings, odbc_string, validate_odbc_string
from nexus.metadata.errors import (
    AlreadySealedError,
    ClaimInvalidError,
    ConflictError,
    FencedError,
    GuardError,
    InsecureConnectionError,
    InvalidOperationError,
    NotSealedError,
)
from nexus.metadata.guards import bootstrap_guard, check_nonce, runtime_guard
from nexus.metadata.memory import InMemoryMetadata
from nexus.metadata.migrations import load_migrations, split_batches
from nexus.metadata.profile import load_profile

PROFILE = load_profile("e1b-ephemeral")


def settings(**kw: Any) -> ConnectionSettings:
    base = {
        "host": "localhost",
        "port": 1433,
        "database": "nexus_e1b_abcdef12",
        "user": "u",
        "password": "p}w",
    }
    base.update(kw)
    return ConnectionSettings(**base)


# --------------------------------------------------------------------------- TLS (B4c)
def test_secure_connection_string() -> None:
    raw = odbc_string(settings())
    assert "Encrypt=yes" in raw and "TrustServerCertificate=no" in raw
    assert "PWD={p}}w}" in raw  # escape de llaves


@pytest.mark.parametrize(
    "kw",
    [
        {"trust_server_certificate": "yes"},
        {"trust_server_certificate": "true"},
        {"encrypt": "no"},
        {"encrypt": "optional"},
    ],
)
def test_insecure_settings_rejected(kw: dict[str, str]) -> None:
    with pytest.raises(InsecureConnectionError):
        odbc_string(settings(**kw))


@pytest.mark.parametrize(
    "raw",
    [
        "Driver=x;Encrypt=yes;TrustServerCertificate=yes;",
        "Driver=x;Encrypt=no;",
        "Driver=x;Encrypt=Optional;",
        "Driver=x;",
    ],
)
def test_raw_odbc_string_validator(raw: str) -> None:
    with pytest.raises(InsecureConnectionError):
        validate_odbc_string(raw)


def test_host_injection_rejected() -> None:
    with pytest.raises(GuardError):
        odbc_string(settings(host="localhost;DATABASE=master"))


# --------------------------------------------------------------------------- perfil (B4)
def test_profile_rules() -> None:
    assert PROFILE.lease_ms == 5000 and PROFILE.heartbeat_ms == 1000
    PROFILE.check_host("localhost")
    for host in ("ASUSMSRV0004\\SQLEXPRESS", "asusmsrv0004", "db.corp.local", "127.0.0.1"):
        with pytest.raises(GuardError):
            PROFILE.check_host(host)
    PROFILE.check_database("nexus_e1b_abcdef12")
    for db in ("PULSO_MAGNUM_STAGE", "pulso_magnum_stage", "master", "nexus_e1b_X", "otra"):
        with pytest.raises(GuardError):
            PROFILE.check_database(db)
    with pytest.raises(GuardError):
        load_profile("../etc")
    with pytest.raises(GuardError):
        load_profile("no-existe")


def test_nonce_rules() -> None:
    assert check_nonce("abcdef12") == "abcdef12"
    for bad in (None, "", "ABC", "abc", "a" * 40, "abc-def12"):
        with pytest.raises(GuardError):
            check_nonce(bad)


class FakeCursor:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.executed: list[str] = []
        self._last: str = ""

    def execute(self, sql: str, *params: Any) -> FakeCursor:
        self.executed.append(sql)
        self._last = sql
        if "usp_get_environment" in sql and isinstance(self.answers.get("env"), Exception):
            raise self.answers["env"]
        return self

    def fetchone(self) -> Any:
        if "SERVERPROPERTY" in self._last:
            return self.answers["server"]
        if "DB_NAME" in self._last:
            return (self.answers["db"],)
        if "usp_get_environment" in self._last:
            return self.answers["env"]
        raise AssertionError(self._last)

    def fetchall(self) -> Any:
        return [(n,) for n in self.answers["user_dbs"]]


class FakeConn:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.cur = FakeCursor(answers)

    def cursor(self) -> FakeCursor:
        return self.cur


GOOD_SERVER = ("Developer Edition (64-bit)", "nexus-sql")


def test_bootstrap_guard() -> None:
    ok = FakeConn({"server": GOOD_SERVER, "user_dbs": []})
    assert bootstrap_guard(ok, PROFILE, "abcdef12", "localhost") == "nexus_e1b_abcdef12"
    cases = [
        (
            {"server": ("Express Edition (64-bit)", "nexus-sql"), "user_dbs": []},
            "abcdef12",
            "localhost",
        ),
        (
            {"server": ("Developer Edition (64-bit)", "otro"), "user_dbs": []},
            "abcdef12",
            "localhost",
        ),
        ({"server": GOOD_SERVER, "user_dbs": ["PULSO_MAGNUM_STAGE"]}, "abcdef12", "localhost"),
        ({"server": GOOD_SERVER, "user_dbs": []}, None, "localhost"),
        ({"server": GOOD_SERVER, "user_dbs": []}, "abcdef12", "ASUSMSRV0004\\SQLEXPRESS"),
    ]
    for answers, nonce, host in cases:
        conn = FakeConn(answers)
        with pytest.raises(GuardError):
            bootstrap_guard(conn, PROFILE, nonce, host)
        assert not any("CREATE" in s for s in conn.cur.executed)


def test_runtime_guard() -> None:
    env = ("e1b-ephemeral", "EPHEMERAL", "abcdef12", 3)
    runtime_guard(
        FakeConn({"db": "nexus_e1b_abcdef12", "env": env}), PROFILE, "abcdef12", "localhost"
    )
    bad = [
        ({"db": "PULSO_MAGNUM_STAGE", "env": env}, "abcdef12"),
        ({"db": "master", "env": env}, "abcdef12"),
        ({"db": "nexus_e1b_abcdef12", "env": ("otro", "EPHEMERAL", "abcdef12", 3)}, "abcdef12"),
        ({"db": "nexus_e1b_abcdef12", "env": env}, "zzzzzzzz"),
        (
            {"db": "nexus_e1b_abcdef12", "env": ("e1b-ephemeral", "EPHEMERAL", "abcdef12", 2)},
            "abcdef12",
        ),
        ({"db": "nexus_e1b_abcdef12", "env": None}, "abcdef12"),
        ({"db": "nexus_e1b_abcdef12", "env": RuntimeError("no proc")}, "abcdef12"),
    ]
    for answers, nonce in bad:
        with pytest.raises(GuardError):
            runtime_guard(FakeConn(answers), PROFILE, nonce, "localhost")


# --------------------------------------------------------------------------- migraciones
def test_migrations_are_consecutive_and_split() -> None:
    migrations = load_migrations()
    assert (
        [m.version for m in migrations] == [1, 2, 3] == list(range(1, PROFILE.schema_version + 1))
    )
    assert all(len(m.checksum) == 64 for m in migrations)
    assert split_batches("SELECT 1;\nGO\n-- solo comentario\nGO\n  go  \nSELECT 2;") == [
        "SELECT 1;",
        "SELECT 2;",
    ]
    procs = migrations[1].text
    # Todo procedimiento con validación de fencing la delimita para las pruebas de mutación.
    assert procs.count("-- FENCE:BEGIN") == procs.count("-- FENCE:END") == 7


# --------------------------------------------------------------------------- doble C1/C2/C3
class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += timedelta(milliseconds=ms)


@pytest.fixture
def mem() -> tuple[InMemoryMetadata, Clock]:
    clock = Clock()
    return InMemoryMetadata(clock), clock


def test_memory_full_cycle_and_c3(mem: tuple[InMemoryMetadata, Clock]) -> None:
    repo, clock = mem
    rid, created = repo.enqueue("k1", "s1", "manual", {"a": 1}, 3)
    assert created and repo.enqueue("k1", "s1", "manual", {"a": 1}, 3) == (rid, False)
    claim = repo.claim("w1", 5000)
    assert claim is not None and repo.claim("w2", 5000) is None  # C1
    lease = repo.acquire_lease(claim, "w1", 5000)
    assert lease.acquired and lease.fencing_token == 1
    att, tok = str(lease.attempt_id), int(lease.fencing_token or 0)
    assert repo.register_snapshot_ref(att, tok, "snap", "a" * 64)
    assert not repo.register_snapshot_ref(att, tok, "snap", "a" * 64)
    with pytest.raises(ConflictError):
        repo.register_snapshot_ref(att, tok, "snap", "b" * 64)
    with pytest.raises(InvalidOperationError):
        repo.seal(att, tok, "run-x", "i" * 64, "r" * 64)  # sin artefactos
    assert repo.register_artifact(att, tok, "c@1", "k", "s" * 64, "l" * 64, 1)
    assert not repo.register_artifact(att, tok, "c@1", "k", "s" * 64, "l" * 64, 1)
    with pytest.raises(ConflictError):
        repo.register_artifact(att, tok, "c@1", "k2", "s" * 64, "l" * 64, 1)
    with pytest.raises(NotSealedError):
        repo.complete(att, tok)
    assert repo.seal(att, tok, "run-x", "i" * 64, "r" * 64)
    assert not repo.seal(att, tok, "run-x", "i" * 64, "r" * 64)  # idempotente
    repo.append_event(att, tok, "NOTE", "ok")
    assert repo.complete(att, tok) == att
    assert repo.complete(att, tok) == att  # idempotente tras commit ambiguo
    assert repo.get_request("k1")["state"] == "COMPLETED"  # type: ignore[index]
    assert repo.get_request("nope") is None
    clock.advance(10)
    assert repo.claim("w3", 5000) is None


def test_memory_claim_without_lease_and_recovery(mem: tuple[InMemoryMetadata, Clock]) -> None:
    repo, clock = mem
    repo.enqueue("a", "shared", "manual", {}, 3)
    repo.enqueue("b", "shared", "manual", {}, 3)
    c1 = repo.claim("w1", 5000)
    c2 = repo.claim("w2", 5000)
    assert c1 and c2
    l1 = repo.acquire_lease(c1, "w1", 5000)
    l2 = repo.acquire_lease(c2, "w2", 5000)
    assert l1.acquired and not l2.acquired and l2.reason == "LEASE_BUSY"
    assert repo.release_claim(c2, 100)
    assert not repo.release_claim(c2, 100)
    assert repo.get_request("b")["attempts"] == 0  # type: ignore[index]  # no consume intento
    with pytest.raises(ClaimInvalidError):
        repo.acquire_lease(c2, "w2", 5000)
    # zombi: el lease de w1 vence y se recupera; el token viejo queda fenced
    clock.advance(6000)
    assert repo.recover_expired(100) == (1, 0)
    att, tok = str(l1.attempt_id), int(l1.fencing_token or 0)
    for call in (
        lambda: repo.heartbeat(att, tok, 5000),
        lambda: repo.register_snapshot_ref(att, tok, "s", "a" * 64),
        lambda: repo.register_artifact(att, tok, "c", "k", "s" * 64, "l" * 64, 1),
        lambda: repo.append_event(att, tok, "x", "y"),
        lambda: repo.seal(att, tok, "r", "i" * 64, "r" * 64),
        lambda: repo.complete(att, tok),
        lambda: repo.fail(att, tok, "x", 1),
    ):
        with pytest.raises(FencedError):
            call()
    # claim vencido sin lease
    clock.advance(200)
    c3 = repo.claim("w3", 1000)
    assert c3 is not None
    clock.advance(1500)
    assert repo.recover_expired(0) == (0, 1)


def test_memory_attempts_bounded_and_sealed_adoption(mem: tuple[InMemoryMetadata, Clock]) -> None:
    repo, clock = mem
    repo.enqueue("x", "s", "manual", {}, 2)
    for _ in range(2):
        claim = repo.claim("w", 5000)
        assert claim is not None
        lease = repo.acquire_lease(claim, "w", 5000)
        repo.fail(str(lease.attempt_id), int(lease.fencing_token or 0), "boom", 0)
    claim = repo.claim("w", 5000)
    assert claim is None
    assert repo.get_request("x")["state"] == "FAILED_PERMANENT"  # type: ignore[index]
    # adopción de generación sellada (C3): un intento sella y "muere" antes de completar
    repo.enqueue("y", "s2", "manual", {}, 3)
    claim = repo.claim("w", 5000)
    assert claim
    lease = repo.acquire_lease(claim, "w", 5000)
    att, tok = str(lease.attempt_id), int(lease.fencing_token or 0)
    repo.register_artifact(att, tok, "c", "k", "s" * 64, "l" * 64, 1)
    repo.seal(att, tok, "run", "i" * 64, "r" * 64)
    clock.advance(6000)
    repo.recover_expired(0)
    claim2 = repo.claim("w2", 5000)
    assert claim2
    lease2 = repo.acquire_lease(claim2, "w2", 5000)
    assert lease2.sealed_attempt_id == att
    with pytest.raises(AlreadySealedError):
        a2, t2 = str(lease2.attempt_id), int(lease2.fencing_token or 0)
        repo.register_artifact(a2, t2, "c", "k", "s" * 64, "l" * 64, 1)
        repo.seal(a2, t2, "run2", "i" * 64, "r" * 64)
    assert repo.complete(str(lease2.attempt_id), int(lease2.fencing_token or 0)) == att


def test_memory_reconcile_states(mem: tuple[InMemoryMetadata, Clock]) -> None:
    repo, _ = mem
    repo.enqueue("r", "s", "manual", {}, 3)
    claim = repo.claim("w", 5000)
    assert claim
    lease = repo.acquire_lease(claim, "w", 5000)
    att, tok = str(lease.attempt_id), int(lease.fencing_token or 0)
    repo.register_artifact(att, tok, "c", "k", "s" * 64, "l" * 64, 1)
    repo.seal(att, tok, "run", "i" * 64, "r" * 64)
    assert repo.mark_artifact_state(att, "c", "MISSING", "x")
    assert not repo.mark_artifact_state(att, "c", "MISSING", "x")
    with pytest.raises(InvalidOperationError):
        repo.mark_artifact_state(att, "c", "CORRUPT", "x")
    with pytest.raises(InvalidOperationError):
        repo.mark_artifact_state(att, "c", "PRESENT", "x")
    with pytest.raises(InvalidOperationError):
        repo.mark_artifact_state(att, "zz", "MISSING", "x")
    rows = repo.list_artifacts()
    assert rows[0]["artifact_state"] == "MISSING" and rows[0]["generation_state"] == "INELIGIBLE"
    with pytest.raises(InvalidOperationError):
        repo.enqueue("bad", "s", "manual", {}, 0)
