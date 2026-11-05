"""B3 (migraciones y permisos) y B4 (guards de bootstrap/runtime) contra SQL Server real."""

from __future__ import annotations

import dataclasses
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

from nexus.metadata.connection import ConnectionSettings, connect
from nexus.metadata.errors import GuardError
from nexus.metadata.guards import bootstrap_guard, runtime_guard
from nexus.metadata.migrations import apply_migrations
from nexus.metadata.profile import Profile

pytestmark = pytest.mark.sql


def test_b03_migrations_idempotent_and_versioned(
    base_settings: ConnectionSettings, sql_env: dict[str, str], admin: Any
) -> None:
    migrator = dataclasses.replace(
        base_settings, user="nexus_migrator", password=sql_env["NEXUS_SQL_MIGRATOR_PASSWORD"]
    )
    conn = connect(migrator, 120)
    try:
        assert apply_migrations(conn) == []  # segunda aplicación: nula
    finally:
        conn.close()
    rows = (
        admin.cursor()
        .execute("SELECT version FROM nexus.schema_version ORDER BY version")
        .fetchall()
    )
    assert [r[0] for r in rows] == [1, 2, 3]


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE nexus.intrusa (x int)",
        "CREATE PROCEDURE nexus.usp_intrusa AS SELECT 1",
        "INSERT INTO nexus.run_request (idempotency_key, scope, trigger_kind, payload, max_attempts)"
        " VALUES (N'x', N's', N'manual', N'{}', 1)",
        "UPDATE nexus.scope_lease SET fencing_token = 0",
        "DELETE FROM nexus.run_request",
        "UPDATE nexus.run_event SET detail = N'x'",
        "DELETE FROM nexus.run_event",
        "DROP TABLE nexus.artifact",
    ],
)
def test_b03_producer_denied_direct_ddl_dml(
    base_settings: ConnectionSettings, statement: str
) -> None:
    conn = connect(base_settings, 30)
    try:
        with pytest.raises(Exception) as info:
            conn.cursor().execute(statement)
        text = str(info.value).lower()
        assert "permission" in text or "denied" in text or "(229)" in text or "(262)" in text
    finally:
        conn.close()


def test_b03_run_event_append_only_even_for_owner(admin: Any, repo_factory: Any) -> None:
    repo_factory().enqueue("ao", "s", "manual", {}, 1)
    with pytest.raises(Exception, match="APPEND_ONLY"):
        admin.cursor().execute("UPDATE nexus.run_event SET detail = N'x'")
    with pytest.raises(Exception, match="APPEND_ONLY"):
        admin.cursor().execute("DELETE FROM nexus.run_event")


def test_b04a_bootstrap_guard_refuses_non_empty_or_foreign_server(
    admin_settings: ConnectionSettings, profile: Profile, sql_env: dict[str, str]
) -> None:
    conn = connect(dataclasses.replace(admin_settings, database="master"), 60)
    try:
        dbs_before = count(conn, "SELECT COUNT(*) FROM sys.databases")
        with pytest.raises(GuardError, match="no está vacío"):
            bootstrap_guard(conn, profile, sql_env["NEXUS_SQL_NONCE"], sql_env["NEXUS_SQL_HOST"])
        express = dataclasses.replace(profile, allowed_editions=("Express Edition (64-bit)",))
        with pytest.raises(GuardError, match="edición"):
            bootstrap_guard(conn, express, sql_env["NEXUS_SQL_NONCE"], sql_env["NEXUS_SQL_HOST"])
        with pytest.raises(GuardError):
            bootstrap_guard(conn, profile, None, sql_env["NEXUS_SQL_HOST"])
        with pytest.raises(GuardError):
            bootstrap_guard(conn, profile, sql_env["NEXUS_SQL_NONCE"], "ASUSMSRV0004\\SQLEXPRESS")
        assert count(conn, "SELECT COUNT(*) FROM sys.databases") == dbs_before  # nada creado
    finally:
        conn.close()


def test_b04b_runtime_guard_aborts_before_any_write(
    repo_factory: Any,
    admin: Any,
    base_settings: ConnectionSettings,
    profile: Profile,
    sql_env: dict[str, str],
) -> None:
    events = count(admin, "SELECT COUNT(*) FROM nexus.run_event")
    wrong_nonce = repo_factory()
    wrong_nonce._nonce = "zzzzzzzz"  # simulación: ejecución con otro nonce
    with pytest.raises(GuardError, match="nonce"):
        wrong_nonce.enqueue("guard-1", "s", "manual", {}, 1)
    future = dataclasses.replace(profile, schema_version=99)
    with pytest.raises(GuardError, match="versión de esquema"):
        runtime_guard(
            connect(base_settings, 30),
            future,
            sql_env["NEXUS_SQL_NONCE"],
            sql_env["NEXUS_SQL_HOST"],
        )
    other_kind = dataclasses.replace(profile, kind="CERTIFICATION")
    with pytest.raises(GuardError, match="perfil"):
        runtime_guard(
            connect(base_settings, 30),
            other_kind,
            sql_env["NEXUS_SQL_NONCE"],
            sql_env["NEXUS_SQL_HOST"],
        )
    assert count(admin, "SELECT COUNT(*) FROM nexus.run_event") == events
    assert count(admin, "SELECT COUNT(*) FROM nexus.run_request") == 0


def test_b04b_profile_rejects_simulated_pulso_magnum_stage(
    admin_settings: ConnectionSettings, profile: Profile, sql_env: dict[str, str]
) -> None:
    """Base LOCAL con el nombre PULSO_MAGNUM_STAGE (simulación en el contenedor efímero): el perfil
    e1b-ephemeral la rechaza antes de cualquier escritura, incluso con un sentinel copiado."""
    master = connect(dataclasses.replace(admin_settings, database="master"), 120)
    try:
        master.cursor().execute(
            "IF DB_ID(N'PULSO_MAGNUM_STAGE') IS NULL CREATE DATABASE PULSO_MAGNUM_STAGE"
        )
        sim = connect(dataclasses.replace(admin_settings, database="PULSO_MAGNUM_STAGE"), 60)
        try:
            with pytest.raises(GuardError, match="prohibida"):
                runtime_guard(sim, profile, sql_env["NEXUS_SQL_NONCE"], sql_env["NEXUS_SQL_HOST"])
            tables = count(sim, "SELECT COUNT(*) FROM sys.tables")
            assert tables == 0
        finally:
            sim.close()
    finally:
        master.cursor().execute(
            "IF DB_ID(N'PULSO_MAGNUM_STAGE') IS NOT NULL BEGIN "
            "ALTER DATABASE PULSO_MAGNUM_STAGE SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
            "DROP DATABASE PULSO_MAGNUM_STAGE; END"
        )
        master.close()


def test_b04c_tls_settings_enforced(base_settings: ConnectionSettings) -> None:
    """El cliente real usa Encrypt=yes/TrustServerCertificate=no; el validador impide degradar."""
    conn = connect(base_settings, 30)
    try:
        encrypted = (
            conn.cursor()
            .execute("SELECT encrypt_option FROM sys.dm_exec_connections WHERE session_id = @@SPID")
            .fetchone()[0]
        )
        assert str(encrypted).upper() == "TRUE"
    finally:
        conn.close()
    for bad in ({"trust_server_certificate": "yes"}, {"encrypt": "no"}, {"encrypt": "optional"}):
        with pytest.raises(Exception, match="(TrustServerCertificate|Encrypt)"):
            connect(dataclasses.replace(base_settings, **bad), 30)
