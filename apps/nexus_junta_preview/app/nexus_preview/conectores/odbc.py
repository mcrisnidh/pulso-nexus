"""Acceso ODBC de SOLO LECTURA compartido por FG y Embarques (cada uno con SU credencial).

- La cadena de conexión NUNCA contiene secretos en configuración: UID/PWD se agregan en memoria
  desde el Administrador de credenciales; claves de autenticación en la configuración se rechazan.
- Conexión con ``readonly=True`` (SQL_ATTR_ACCESS_MODE = READ_ONLY), ``autocommit=False`` y
  ``rollback`` al terminar; solo sentencias SELECT constantes con parámetros.
- ``pyodbc`` se importa solo al conectar de verdad; las pruebas inyectan una fábrica simulada.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from . import ConectorError
from .credenciales import Credencial, sanear

Fabrica = Callable[[str, int], Any]  # (cadena, timeout) -> conexión DB-API
PROHIBIDAS = ("uid", "pwd", "password", "user id", "trusted_connection", "integrated security")
IDENT = re.compile(r"^[A-Za-z0-9_#$@]{1,30}$")


def identificador(valor: str, campo: str) -> str:
    if not IDENT.match(str(valor or "")):
        raise ConectorError("CONFIG_IDENTIFICADOR_INVALIDO", f"{campo}={valor!r}")
    return str(valor)


def cadena(cfg: dict[str, Any], cred: Credencial | None) -> str:
    extra = {str(k): str(v) for k, v in (cfg.get("atributos_conexion") or {}).items()}
    for k in extra:
        if k.strip().lower() in PROHIBIDAS:
            raise ConectorError("CONFIG_CON_SECRETOS", f"«{k}» no se admite en configuración")
    parts = []
    if cfg.get("dsn"):
        parts.append(f"DSN={cfg['dsn']}")
    else:
        if not cfg.get("controlador") or not cfg.get("servidor"):
            raise ConectorError("ODBC_NO_CONFIGURADO", "indique dsn, o controlador y servidor")
        parts.append(f"DRIVER={{{cfg['controlador']}}}")
        parts.append(f"SERVER={cfg['servidor']}")
        if cfg.get("base"):
            parts.append(f"DATABASE={cfg['base']}")
    parts += [f"{k}={v}" for k, v in extra.items()]
    if cred is not None:
        parts.append(f"UID={cred.usuario.split(chr(92))[-1]}")
        parts.append("PWD={" + cred.secreto.revelar().replace("}", "}}") + "}")
    return ";".join(parts) + ";"


def fabrica_pyodbc(conn_str: str, timeout: int) -> Any:
    try:
        import pyodbc  # noqa: PLC0415
    except ImportError as exc:  # controlador ODBC o pyodbc no disponible en este equipo
        raise ConectorError("ODBC_NO_DISPONIBLE", str(exc)[:120]) from exc
    return pyodbc.connect(conn_str, autocommit=False, readonly=True, timeout=timeout)


def consultar(
    cfg: dict[str, Any],
    cred: Credencial | None,
    sql: str,
    params: Sequence[Any],
    fabrica: Fabrica | None = None,
    identidad_sql: str | None = None,
) -> tuple[list[tuple[Any, ...]], tuple[Any, ...] | None]:
    """Ejecuta UNA consulta SELECT y devuelve (filas, identidad del servidor)."""
    if not sql.lstrip().upper().startswith("SELECT") or ";" in sql:
        raise ConectorError("SQL_NO_PERMITIDA", "solo una sentencia SELECT")
    timeout = int(cfg.get("timeout_s", 60))
    secret = cred.secreto if cred else None
    try:
        cn = (fabrica or fabrica_pyodbc)(cadena(cfg, cred), timeout)
    except ConectorError:
        raise
    except Exception as exc:  # noqa: BLE001 (mensaje del controlador, saneado)
        raise ConectorError("ODBC_CONEXION_FALLIDA", sanear(str(exc), secret)) from None
    try:
        cur = cn.cursor()
        ident = None
        if identidad_sql:
            cur.execute(identidad_sql)
            ident = tuple(cur.fetchone() or ())
        cur.execute(sql, *params)
        rows = [tuple(r) for r in cur.fetchall()]
        return rows, ident
    except ConectorError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ConectorError("ODBC_CONSULTA_FALLIDA", sanear(str(exc), secret)) from None
    finally:
        try:
            cn.rollback()
        finally:
            cn.close()
