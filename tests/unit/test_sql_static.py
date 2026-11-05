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
