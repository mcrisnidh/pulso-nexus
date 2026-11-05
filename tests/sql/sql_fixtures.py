"""Infraestructura de pruebas contra SQL Server EFÍMERO real (job SQL de CI).

Los módulos de tests/sql/ importan estas fixtures explícitamente y declaran
``pytestmark = pytest.mark.sql``. Si se seleccionan y falta el entorno,
FALLAN (no se omiten): una garantía transaccional no puede darse por probada sin SQL Server.
Credenciales efímeras por variables de entorno (generadas por el job, enmascaradas).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any

import pytest

from nexus.metadata.connection import ConnectionSettings, connect
from nexus.metadata.profile import Profile, load_profile
from nexus.metadata.sqlserver import SqlServerMetadata

REQUIRED = (
    "NEXUS_SQL_HOST",
    "NEXUS_SQL_DATABASE",
    "NEXUS_SQL_NONCE",
    "NEXUS_SQL_ADMIN_PASSWORD",
    "NEXUS_SQL_PRODUCER_PASSWORD",
    "NEXUS_SQL_MIGRATOR_PASSWORD",
)
TABLES_IN_DELETE_ORDER = (
    "nexus.artifact",
    "nexus.generation",
    "nexus.attempt",
    "nexus.snapshot_ref",
    "nexus.scope_lease",
    "nexus.run_request",
)


@pytest.fixture(scope="session")
def sql_env() -> dict[str, str]:
    missing = [v for v in REQUIRED if not os.environ.get(v)]
    if missing:
        pytest.fail(f"entorno SQL efímero ausente ({missing}): las pruebas 'sql' no se omiten")
    return {v: os.environ[v] for v in REQUIRED} | {
        "port": os.environ.get("NEXUS_SQL_PORT", "1433"),
        "isolation": os.environ.get("NEXUS_TEST_SESSION_ISOLATION", "READ COMMITTED"),
    }


@pytest.fixture(scope="session")
def profile() -> Profile:
    return load_profile("e1b-ephemeral")


@pytest.fixture(scope="session")
def base_settings(sql_env: dict[str, str]) -> ConnectionSettings:
    return ConnectionSettings(
        host=sql_env["NEXUS_SQL_HOST"],
        port=int(sql_env["port"]),
        database=sql_env["NEXUS_SQL_DATABASE"],
        user="nexus_producer",
        password=sql_env["NEXUS_SQL_PRODUCER_PASSWORD"],
    )


@pytest.fixture(scope="session")
def admin_settings(
    base_settings: ConnectionSettings, sql_env: dict[str, str]
) -> ConnectionSettings:
    return replace(base_settings, user="sa", password=sql_env["NEXUS_SQL_ADMIN_PASSWORD"])


@pytest.fixture
def admin(admin_settings: ConnectionSettings, profile: Profile) -> Iterator[Any]:
    conn = connect(admin_settings, 120)
    yield conn
    conn.close()


RepoFactory = Callable[..., SqlServerMetadata]


@pytest.fixture
def repo_factory(
    base_settings: ConnectionSettings, profile: Profile, sql_env: dict[str, str]
) -> Iterator[RepoFactory]:
    made: list[SqlServerMetadata] = []
    isolation = None if sql_env["isolation"] == "READ COMMITTED" else sql_env["isolation"]

    def make(**kw: Any) -> SqlServerMetadata:
        kw.setdefault("session_isolation", isolation)
        repo = SqlServerMetadata(base_settings, profile, sql_env["NEXUS_SQL_NONCE"], **kw)
        made.append(repo)
        return repo

    yield make
    for repo in made:
        repo.close()


def server_now(admin_conn: Any) -> Any:
    return admin_conn.cursor().execute("SELECT SYSUTCDATETIME()").fetchone()[0]


def wait_server_past(admin_conn: Any, moment: Any, margin_ms: int = 300) -> None:
    """Espera hasta que el RELOJ DEL SERVIDOR supere ``moment`` + margen (NX-E1B-06)."""
    admin_conn.cursor().execute(
        "DECLARE @t datetime2(7) = DATEADD(millisecond, ?, ?); "
        "WHILE SYSUTCDATETIME() < @t WAITFOR DELAY '00:00:00.100';",
        margin_ms,
        moment,
    )


def count(admin_conn: Any, sql: str, *params: Any) -> int:
    return int(admin_conn.cursor().execute(sql, *params).fetchone()[0])


@pytest.fixture(autouse=True)
def clean_database(admin_settings: ConnectionSettings) -> Iterator[None]:
    conn = connect(admin_settings, 120)
    try:
        wipe(conn)
    finally:
        conn.close()
    yield


def wipe(admin_conn: Any) -> None:
    """Vacía las tablas de la base EFÍMERA entre pruebas (solo con la conexión de administración)."""
    cur = admin_conn.cursor()
    cur.execute("DISABLE TRIGGER nexus.tr_run_event_append_only ON nexus.run_event")
    cur.execute("DELETE FROM nexus.run_event")
    cur.execute("ENABLE TRIGGER nexus.tr_run_event_append_only ON nexus.run_event")
    for table in TABLES_IN_DELETE_ORDER:
        cur.execute(f"DELETE FROM {table}")
