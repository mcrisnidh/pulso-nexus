"""Aplicador de migraciones T-SQL numeradas e idempotentes (por ``nexus.schema_version``)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

from nexus.metadata.errors import MetadataError

_GO = re.compile(r"^[ \t]*GO[ \t]*$", re.IGNORECASE | re.MULTILINE)
_FILE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

BOOTSTRAP_SQL = (
    "IF SCHEMA_ID(N'nexus') IS NULL EXEC(N'CREATE SCHEMA nexus AUTHORIZATION dbo');",
    "IF OBJECT_ID(N'nexus.schema_version') IS NULL CREATE TABLE nexus.schema_version ("
    " version int NOT NULL CONSTRAINT pk_schema_version PRIMARY KEY,"
    " name nvarchar(200) NOT NULL, checksum char(64) NOT NULL,"
    " applied_at datetime2(7) NOT NULL"
    " CONSTRAINT df_schema_version_applied DEFAULT SYSUTCDATETIME());",
)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    text: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def batches(self) -> list[str]:
        return split_batches(self.text)


def split_batches(text: str) -> list[str]:
    return [b.strip() for b in _GO.split(text) if b.strip() and not _only_comments(b)]


def _only_comments(batch: str) -> bool:
    return all(not line.strip() or line.strip().startswith("--") for line in batch.splitlines())


def load_migrations() -> list[Migration]:
    root = resources.files("nexus.metadata").joinpath("sql")
    found = []
    for entry in root.iterdir():
        match = _FILE.match(entry.name)
        if match:
            found.append(Migration(int(match.group(1)), entry.name, entry.read_text("utf-8")))
    found.sort(key=lambda m: m.version)
    versions = [m.version for m in found]
    if versions != list(range(1, len(found) + 1)):
        raise MetadataError(f"migraciones no consecutivas: {versions}")
    return found


def apply_migrations(conn: Any) -> list[int]:
    """Aplica en orden las migraciones pendientes, cada una en una transacción.

    Una migración ya aplicada con otra suma de verificación es un error (deriva de esquema).
    Devuelve las versiones aplicadas en esta llamada (vacía si no había pendientes).
    """
    cursor = conn.cursor()
    for statement in BOOTSTRAP_SQL:
        cursor.execute(statement)
    applied = {
        int(r[0]): str(r[1])
        for r in cursor.execute("SELECT version, checksum FROM nexus.schema_version").fetchall()
    }
    done: list[int] = []
    for migration in load_migrations():
        if migration.version in applied:
            if applied[migration.version] != migration.checksum:
                raise MetadataError(f"deriva de esquema en la migración {migration.name}")
            continue
        conn.autocommit = False
        try:
            for batch in migration.batches():
                cursor.execute(batch)
            cursor.execute(
                "INSERT INTO nexus.schema_version (version, name, checksum) VALUES (?, ?, ?)",
                migration.version,
                migration.name,
                migration.checksum,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.autocommit = True
        done.append(migration.version)
    return done
