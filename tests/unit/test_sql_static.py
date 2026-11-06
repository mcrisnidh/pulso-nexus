"""Comprobaciones estáticas de los scripts T-SQL (B12/B13 parte estática; corre sin SQL Server)."""

from __future__ import annotations

import re
from importlib import resources

import pytest

from nexus.metadata.migrations import load_migrations, split_batches

SQL = {m.name: m.text for m in load_migrations()}
PROCS = {
    re.search(r"PROCEDURE nexus\.(\w+)", b).group(1): b  # type: ignore[union-attr]
    for b in split_batches(SQL["0002_procedures.sql"])
    if "CREATE OR ALTER PROCEDURE" in b
}


def _code(text: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def test_sql_files_are_packaged() -> None:
    root = resources.files("nexus.metadata").joinpath("sql")
    assert sorted(e.name for e in root.iterdir() if e.name.endswith(".sql")) == sorted(SQL)


@pytest.mark.parametrize("name", sorted(SQL))
def test_no_dirty_reads(name: str) -> None:
    code = _code(SQL[name]).upper()
    for banned in ("NOLOCK", "READUNCOMMITTED", "READ UNCOMMITTED"):
        assert banned not in code, f"{name}: {banned}"


def test_readpast_only_in_claim_update_cte() -> None:
    hits = [(n, t) for n, t in PROCS.items() if "READPAST" in _code(t).upper()]
    assert [n for n, _ in hits] == ["usp_claim_request"]
    text = hits[0][1]
    block = text[text.index("WITH next_request AS") :]
    assert re.search(r"\)\s*UPDATE next_request\s", block), "READPAST fuera de una modificación"
    assert "-- CLAIM_HINTS" in text and "UPDLOCK" in text.split("-- CLAIM_HINTS", 1)[1][:80]


@pytest.mark.parametrize("name", sorted(SQL))
def test_rowlock_never_combined_with_readcommittedlock(name: str) -> None:
    for hints in re.findall(r"WITH\s*\(([^()]*)\)", _code(SQL[name]), flags=re.I):
        words = {w.strip().upper() for w in hints.split(",")}
        assert not {"ROWLOCK", "READCOMMITTEDLOCK"} <= words, f"{name}: {hints}"


@pytest.mark.parametrize("proc", sorted(PROCS))
def test_procedures_pin_isolation_and_xact_abort(proc: str) -> None:
    code = _code(PROCS[proc])
    if "BEGIN TRANSACTION" in code:
        assert "SET XACT_ABORT ON;" in code
        assert "SET TRANSACTION ISOLATION LEVEL READ COMMITTED;" in code


@pytest.mark.parametrize("proc", sorted(PROCS))
def test_no_client_supplied_time(proc: str) -> None:
    header = _code(PROCS[proc]).split("\nAS\n", 1)[0]
    assert not re.search(
        r"@\w+\s+(datetime2?|smalldatetime|date|time|datetimeoffset)\b", header, flags=re.I
    ), header
    if "expires_at" in _code(PROCS[proc]) or "not_before" in _code(PROCS[proc]):
        assert "GETDATE()" not in _code(PROCS[proc]).upper()


def test_permissions_never_deny_control() -> None:
    """DENY CONTROL implica DENY EXECUTE: rompería el modelo EXECUTE-only."""
    code = _code(SQL["0003_permissions.sql"]).upper()
    for stmt in code.split(";"):
        if stmt.strip().startswith("DENY"):
            assert "CONTROL" not in stmt, stmt
    assert "GRANT EXECUTE ON SCHEMA::NEXUS TO NEXUS_PRODUCER_ROLE" in code


@pytest.mark.parametrize(
    "index",
    [
        "ix_scope_lease_attempt",
        "ix_scope_lease_expiry",
        "ix_attempt_request",
        "ix_generation_request",
    ],
)
def test_concurrency_indexes_present(index: str) -> None:
    """Regresión: sin estos índices, UPDLOCK/HOLDLOCK por attempt_id/request_id recorría tablas."""
    assert f"CREATE INDEX {index} ON" in SQL["0001_schema.sql"]


def test_fence_blocks_lock_only_by_primary_key() -> None:
    """Regresión: un JOIN con UPDLOCK/HOLDLOCK permitía recorrer y bloquear todos los leases."""
    blocks = re.findall(r"-- FENCE:BEGIN(.*?)-- FENCE:END", SQL["0002_procedures.sql"], re.DOTALL)
    assert len(blocks) == 7
    for block in blocks:
        code = _code(block).upper()
        assert " JOIN " not in code
        assert "WHERE SCOPE = @FENCE_SCOPE" in code


def test_lease_and_claim_expiry_computed_at_write_time() -> None:
    """Regresión: @now capturado al inicio + espera por bloqueos = lease emitido ya vencido."""
    acquire = _code(PROCS["usp_acquire_lease"])
    assert "DATEADD(millisecond, @lease_ms, SYSUTCDATETIME())" in acquire
    assert "DATEADD(millisecond, @claim_ms, SYSUTCDATETIME())" in _code(PROCS["usp_claim_request"])


@pytest.mark.parametrize(
    "proc",
    ["usp_register_artifact", "usp_seal_generation", "usp_complete_run", "usp_recover_expired"],
)
def test_no_range_locks_where_lease_row_already_serializes(proc: str) -> None:
    code = _code(PROCS[proc])
    fence = re.sub(r"DECLARE @fence_scope.*?THROW 50010", "", code, flags=re.DOTALL)
    for hint in re.findall(r"(\w+(?:\s+AS\s+\w+)?)\s+WITH\s*\(([^()]*)\)", fence):
        assert "HOLDLOCK" not in hint[1].upper() or "scope_lease" in hint[0], (proc, hint)


def test_scope_lease_written_only_by_primary_key() -> None:
    """Regresión (grafos de interbloqueo reales): toda escritura en scope_lease filtra por scope."""
    code = _code(SQL["0002_procedures.sql"])
    for stmt in re.findall(r"UPDATE nexus\.scope_lease.*?;", code, flags=re.DOTALL):
        assert re.search(r"WHERE\s+scope\s*=", stmt), stmt


def test_acquire_lease_checks_claim_validity_after_lock_waits() -> None:
    """NX-E1B-R1-01: sin @now capturado al inicio; vigencia comprobada bajo bloqueo y de nuevo
    tras el último punto de espera (UPDATE de la fila de lease)."""
    code = _code(PROCS["usp_acquire_lease"])
    assert "@now" not in code
    assert code.count("@claim_until <= SYSUTCDATETIME()") == 2
    lease_update = code.index("UPDATE nexus.scope_lease")
    assert code.rindex("@claim_until <= SYSUTCDATETIME()") > lease_update
