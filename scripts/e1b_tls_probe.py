"""Sonda TLS de E1-B (B4c / NX-E1B-05). Solo contra el SQL Server EFÍMERO del job de CI.

Uso: python scripts/e1b_tls_probe.py --expect {untrusted-fails,ok,hostname-fails} [--host H]

- ``untrusted-fails``: antes de confiar en la CA efímera, la conexión con TrustServerCertificate=no
  DEBE fallar por verificación del certificado.
- ``ok``: tras instalar la CA efímera en el almacén del runner, la conexión DEBE establecerse y
  ``sys.dm_exec_connections.encrypt_option`` DEBE ser TRUE.
- ``hostname-fails``: con un nombre que no está en el SAN (p. ej. 127.0.0.1) DEBE fallar.

Credenciales efímeras por entorno (NEXUS_SQL_ADMIN_USER/PASSWORD). Imprime JSON sin secretos.
Código de salida 0 solo si se cumple la expectativa.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace

from nexus.metadata.connection import connect, settings_from_env

CERT_MARKERS = ("certificate", "ssl", "tls", "self signed", "self-signed", "verify")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--expect", required=True, choices=["untrusted-fails", "ok", "hostname-fails"]
    )
    parser.add_argument("--host")
    args = parser.parse_args()
    settings = settings_from_env(
        "NEXUS_SQL_ADMIN_USER", "NEXUS_SQL_ADMIN_PASSWORD", database="master"
    )
    if args.host:
        settings = replace(settings, host=args.host)
    result: dict[str, object] = {
        "expect": args.expect,
        "host": settings.host,
        "encrypt": settings.encrypt,
        "trust_server_certificate": settings.trust_server_certificate,
    }
    try:
        conn = connect(settings, 30)
        try:
            row = (
                conn.cursor()
                .execute(
                    "SELECT encrypt_option, auth_scheme, SYSUTCDATETIME() "
                    "FROM sys.dm_exec_connections WHERE session_id = @@SPID"
                )
                .fetchone()
            )
        finally:
            conn.close()
        result.update(
            connected=True,
            encrypt_option=str(row[0]),
            auth_scheme=str(row[1]),
            server_ts=str(row[2]),
        )
    except Exception as exc:
        text = str(exc)
        password = os.environ.get("NEXUS_SQL_ADMIN_PASSWORD") or "\0"
        result.update(
            connected=False,
            error=text.replace(password, "***")[:500],
            certificate_error=any(m in text.lower() for m in CERT_MARKERS),
        )
    if args.expect == "ok":
        passed = (
            result.get("connected") is True and str(result.get("encrypt_option")).upper() == "TRUE"
        )
    else:
        passed = result.get("connected") is False and result.get("certificate_error") is True
    result["passed"] = passed
    print(json.dumps(result, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
