"""Guards de entorno (NX-E1B-04).

* Bootstrap (crea el entorno efímero; no puede usar el sentinel porque aún no existe):
  activación explícita (perfil + nonce), host permitido, edición y nombre de servidor
  esperados, y SERVIDOR VACÍO (sin bases de usuario ajenas). Todo antes de cualquier CREATE.
* Runtime (worker/CLI, antes de CUALQUIER escritura operativa en cada conexión nueva):
  host, base (allowlist/denylist del perfil), sentinel (perfil, tipo, nonce) y versión de esquema.
"""

from __future__ import annotations

import re
from typing import Any

from nexus.metadata.errors import GuardError
from nexus.metadata.profile import Profile

_NONCE = re.compile(r"^[a-z0-9]{8,32}$")


def check_nonce(nonce: str | None) -> str:
    if not nonce or not _NONCE.match(nonce):
        raise GuardError("falta el nonce de ejecución o tiene un formato inválido")
    return nonce


def database_name_for(nonce: str) -> str:
    return f"nexus_e1b_{check_nonce(nonce)}"


def bootstrap_guard(admin_conn: Any, profile: Profile, nonce: str | None, host: str) -> str:
    """Valida un servidor recién creado antes de crear nada. Devuelve el nombre de la base."""
    profile.check_host(host)
    nonce = check_nonce(nonce)
    database = database_name_for(nonce)
    profile.check_database(database)
    cursor = admin_conn.cursor()
    edition, server_name = cursor.execute(
        "SELECT CAST(SERVERPROPERTY('Edition') AS nvarchar(128)), @@SERVERNAME"
    ).fetchone()
    if str(edition) not in profile.allowed_editions:
        raise GuardError(f"edición no permitida por el perfil {profile.name}: {edition!r}")
    if not re.fullmatch(profile.server_name_pattern, str(server_name or "")):
        raise GuardError(f"nombre de servidor inesperado: {server_name!r}")
    user_dbs = [
        str(r[0])
        for r in cursor.execute(
            "SELECT name FROM sys.databases WHERE database_id > 4 ORDER BY name"
        ).fetchall()
    ]
    if user_dbs:
        raise GuardError(
            f"el servidor no está vacío ({user_dbs}): "
            "el bootstrap solo actúa sobre una instancia nueva"
        )
    return database


def runtime_guard(conn: Any, profile: Profile, nonce: str | None, host: str) -> None:
    """Debe pasar antes de cualquier escritura operativa. Si falla, no se escribe nada."""
    profile.check_host(host)
    nonce = check_nonce(nonce)
    cursor = conn.cursor()
    (database,) = cursor.execute("SELECT DB_NAME()").fetchone()
    profile.check_database(str(database))
    try:
        row = cursor.execute("EXEC nexus.usp_get_environment").fetchone()
    except Exception as exc:  # procedimiento ausente: no es un entorno NEXUS
        raise GuardError(f"la base {database!r} no tiene sentinel NEXUS: {exc}") from exc
    if row is None:
        raise GuardError(f"la base {database!r} no tiene sentinel")
    env_profile, kind, env_nonce, schema_version = (
        str(row[0]),
        str(row[1]),
        str(row[2]),
        int(row[3]),
    )
    if env_profile != profile.name or kind != profile.kind:
        raise GuardError(f"sentinel de otro perfil/tipo: {env_profile}/{kind}")
    if env_nonce != nonce:
        raise GuardError("el nonce del sentinel no coincide con el de la ejecución")
    if schema_version != profile.schema_version:
        raise GuardError(
            f"versión de esquema {schema_version}, el código espera {profile.schema_version}"
        )
