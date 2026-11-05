"""Bootstrap del entorno EFÍMERO de E1-B (solo CI, login administrativo del contenedor).

Uso (todas las credenciales son efímeras y llegan por variables de entorno):
    python -m nexus.metadata.bootstrap --rcsi on|off

Variables: NEXUS_BOOTSTRAP_PROFILE, NEXUS_SQL_NONCE, NEXUS_SQL_HOST, NEXUS_SQL_PORT,
NEXUS_SQL_ADMIN_USER, NEXUS_SQL_ADMIN_PASSWORD, NEXUS_SQL_MIGRATOR_PASSWORD,
NEXUS_SQL_PRODUCER_PASSWORD.

Orden: guard de bootstrap (sin sentinel) -> CREATE DATABASE -> opciones de aislamiento ->
logins/usuarios -> migraciones (nexus_migrator) -> sentinel -> pertenencia al rol de mínimo
privilegio. Imprime el nombre de la base creada (no imprime secretos).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import replace

from nexus.metadata.connection import connect, settings_from_env
from nexus.metadata.errors import GuardError
from nexus.metadata.guards import bootstrap_guard
from nexus.metadata.migrations import apply_migrations
from nexus.metadata.profile import load_profile

_PASSWORD = re.compile(r"^[A-Za-z0-9_\-!@#%+=.]{16,128}$")
MIGRATOR = "nexus_migrator"
PRODUCER = "nexus_producer"


def _password(var: str) -> str:
    value = os.environ.get(var, "")
    if not _PASSWORD.match(value):
        raise GuardError(
            f"{var} ausente o con caracteres no permitidos (16–128, [A-Za-z0-9_-!@#%+=.])"
        )
    return value


def _quote_password(value: str) -> str:
    return "N'" + value.replace("'", "''") + "'"


def bootstrap(rcsi: bool) -> str:
    profile_name = os.environ.get("NEXUS_BOOTSTRAP_PROFILE")
    if not profile_name:
        raise GuardError("falta NEXUS_BOOTSTRAP_PROFILE: activación explícita obligatoria")
    profile = load_profile(profile_name)
    nonce = os.environ.get("NEXUS_SQL_NONCE")
    admin = settings_from_env("NEXUS_SQL_ADMIN_USER", "NEXUS_SQL_ADMIN_PASSWORD", database="master")
    profile.check_host(admin.host)  # antes de conectar
    migrator_pw = _password("NEXUS_SQL_MIGRATOR_PASSWORD")
    producer_pw = _password("NEXUS_SQL_PRODUCER_PASSWORD")

    conn = connect(admin, profile.query_timeout_s)
    try:
        database = bootstrap_guard(conn, profile, nonce, admin.host)
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE [{database}]")
        cur.execute(f"ALTER DATABASE [{database}] SET ALLOW_SNAPSHOT_ISOLATION ON")
        cur.execute(
            f"ALTER DATABASE [{database}] SET READ_COMMITTED_SNAPSHOT {'ON' if rcsi else 'OFF'} "
            "WITH ROLLBACK IMMEDIATE"
        )
        for login, pw in ((MIGRATOR, migrator_pw), (PRODUCER, producer_pw)):
            cur.execute(
                f"CREATE LOGIN [{login}] WITH PASSWORD = {_quote_password(pw)}, CHECK_POLICY = ON, "
                f"DEFAULT_DATABASE = [{database}]"
            )
        cur.execute(f"USE [{database}]")
        for login in (MIGRATOR, PRODUCER):
            cur.execute(f"CREATE USER [{login}] FOR LOGIN [{login}]")
        cur.execute(f"ALTER ROLE db_owner ADD MEMBER [{MIGRATOR}]")
    finally:
        conn.close()

    mig_conn = connect(
        replace(admin, database=database, user=MIGRATOR, password=migrator_pw),
        profile.query_timeout_s,
    )
    try:
        apply_migrations(mig_conn)
        cur = mig_conn.cursor()
        cur.execute(
            "INSERT INTO nexus.environment (singleton, profile, kind, nonce) VALUES (1, ?, ?, ?)",
            profile.name,
            profile.kind,
            nonce,
        )
        cur.execute(f"ALTER ROLE nexus_producer_role ADD MEMBER [{PRODUCER}]")
    finally:
        mig_conn.close()
    return database


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexus-bootstrap")
    parser.add_argument("--rcsi", choices=["on", "off"], required=True)
    args = parser.parse_args(argv)
    try:
        database = bootstrap(args.rcsi == "on")
    except GuardError as exc:
        sys.stderr.write(f"BOOTSTRAP ABORTADO (guard): {exc}\n")
        return 3
    sys.stdout.write(database + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
