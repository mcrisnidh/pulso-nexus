"""Conexión ODBC a SQL Server con TLS VERIFICADO (NX-E1B-05).

- Siempre ``Encrypt=yes`` (o ``strict``) y ``TrustServerCertificate=no``.
- El validador rechaza ``TrustServerCertificate=yes``, ``Encrypt=no`` y ``Encrypt=optional``
  en todos los perfiles; no existe interruptor que lo permita.
- La confianza en la CA del entorno efímero se establece FUERA del código (almacén de
  confianza del runner de CI). La confianza TLS corporativa queda pendiente (P17).
- ``pyodbc`` se importa de forma diferida: el resto de NEXUS no depende del driver.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from nexus.metadata.errors import GuardError, InsecureConnectionError

DRIVER = "ODBC Driver 18 for SQL Server"
_SECURE_ENCRYPT = {"yes", "mandatory", "true", "strict"}
_FORBIDDEN_ATTRS = re.compile(
    r"(trustservercertificate\s*=\s*(yes|true|1))|(encrypt\s*=\s*(no|false|0|optional))", re.I
)


@dataclass(frozen=True)
class ConnectionSettings:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)
    encrypt: str = "yes"
    trust_server_certificate: str = "no"
    login_timeout_s: int = 15

    def validate(self) -> None:
        if self.encrypt.lower() not in _SECURE_ENCRYPT:
            raise InsecureConnectionError(
                f"Encrypt={self.encrypt!r} no permitido (TLS obligatorio)"
            )
        if self.trust_server_certificate.lower() != "no":
            raise InsecureConnectionError(
                "TrustServerCertificate debe ser 'no': el certificado del servidor se verifica"
            )
        if not self.host or any(c in self.host for c in ";{}"):
            raise GuardError(f"host inválido: {self.host!r}")


def _braced(value: str) -> str:
    return "{" + value.replace("}", "}}") + "}"


def odbc_string(settings: ConnectionSettings) -> str:
    settings.validate()
    parts = [
        f"DRIVER={_braced(DRIVER)}",
        f"SERVER={settings.host},{int(settings.port)}",
        f"DATABASE={_braced(settings.database)}",
        f"UID={_braced(settings.user)}",
        f"PWD={_braced(settings.password)}",
        f"Encrypt={settings.encrypt}",
        f"TrustServerCertificate={settings.trust_server_certificate}",
        # Determinista: IPv4 primero (en CI el contenedor solo publica 127.0.0.1).
        "IpAddressPreference=IPv4First",
        "APP=pulso-nexus-e1b",
    ]
    raw = ";".join(parts) + ";"
    validate_odbc_string(raw)
    return raw


def validate_odbc_string(raw: str) -> None:
    """Rechaza cadenas ODBC con TLS no verificado, venga de donde venga la cadena."""
    if _FORBIDDEN_ATTRS.search(raw):
        raise InsecureConnectionError("cadena ODBC con TLS no verificado: rechazada")
    if not re.search(r"encrypt\s*=\s*(yes|mandatory|true|strict)", raw, re.I):
        raise InsecureConnectionError("cadena ODBC sin Encrypt obligatorio: rechazada")


def connect(settings: ConnectionSettings, query_timeout_s: int) -> Any:
    raw = odbc_string(settings)
    import pyodbc  # noqa: PLC0415 - importación diferida: solo la capa de metadatos usa el driver

    # Sin pooling del Driver Manager: una conexión descartada tras error/timeout/KILL no debe
    # volver a entregarse (E0-R2 §2.5.1, "descartar la conexión, nunca devolverla al pool").
    pyodbc.pooling = False
    conn = pyodbc.connect(raw, autocommit=True, timeout=settings.login_timeout_s)
    conn.timeout = query_timeout_s
    return conn


def settings_from_env(
    user_var: str, password_var: str, database: str | None = None
) -> ConnectionSettings:
    """Lee la configuración del entorno (credenciales efímeras de CI). Nunca de archivos."""

    def need(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise GuardError(f"falta la variable de entorno {name}")
        return value

    return ConnectionSettings(
        host=need("NEXUS_SQL_HOST"),
        port=int(os.environ.get("NEXUS_SQL_PORT", "1433")),
        database=database if database is not None else need("NEXUS_SQL_DATABASE"),
        user=need(user_var),
        password=need(password_var),
    )
