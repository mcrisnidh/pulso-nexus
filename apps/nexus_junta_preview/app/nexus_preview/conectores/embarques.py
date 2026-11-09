"""Conector Embarques (cuenta ``sm0003``), SOLO LECTURA — FACTURACIÓN CFDI, NO EMBARQUE FÍSICO.

Procedencia: ``commercial/asbg_item_adapter.py`` (``ASBG_SHIPMENTS_ITEM_SQL``) y
``commercial/live_sources.py`` (``capture_asbg``) de PULSO Web; auditoría Power Query de Status
Ventas v3.10 (``Sales_Current_Month_By_Item``):

    SELECT UPPER(LTRIM(RTRIM(c.CODUPC))) AS Item, SUM(COALESCE(c.CANTID, 0)) AS Embarques_Qty
    FROM MF_CFD a JOIN MF_DETCFD c ON a.NUMFOL = c.NUMFOL
    WHERE a.FECEXP >= ? AND a.FECEXP < ? ... GROUP BY UPPER(LTRIM(RTRIM(c.CODUPC)))

Qué representa (según el código; a confirmar con el negocio — ver ``DOCS/EMBARQUES_CFDI.md``):
- evento: partida de un CFDI emitido (``MF_CFD`` cabecera por ``NUMFOL``, ``MF_DETCFD`` detalle);
- fecha: ``FECEXP`` (expedición del comprobante), NO fecha de salida física;
- cantidad: ``CANTID`` facturada; el heredado convertía nulos en 0 (aquí: nulos se cuentan y dejan
  la cantidad del ITEM desconocida);
- ITEM: ``CODUPC`` (código del comprobante); el heredado lo normalizaba (mayúsculas y quitando
  símbolos), lo que NEXUS descarta (X15): aquí se usa el texto exacto recortado;
- cancelaciones, notas de crédito, devoluciones y refacturaciones: la consulta heredada NO las
  distingue (no filtra estatus ni serie) ⇒ bloquea la acreditación;
- duplicados: agrega por ITEM; ``COUNT(DISTINCT NUMFOL)`` y renglones se informan para auditoría.

Mientras no exista una acreditación vigente (``acreditacion_embarques.json`` con evidencia y
aprobador), estas cantidades NUNCA alimentan Embarques, Programa ni Faltante: siguen N/D.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from . import ConectorError
from .credenciales import Proveedor, obtener, sanear
from .odbc import Fabrica, cadena, consultar, fabrica_pyodbc

# Preserve exact ERP item spelling: UPPER applies only to the case-insensitive filters.
_FILTROS = (
    "AND NULLIF(LTRIM(RTRIM(c.CODUPC)), '') IS NOT NULL "
    "AND UPPER(LTRIM(RTRIM(c.CODUPC))) NOT LIKE 'RM%' "
    "AND UPPER(LTRIM(RTRIM(c.CODUPC))) NOT LIKE 'SCRAP%' "
    "AND UPPER(LTRIM(RTRIM(c.CODUPC))) NOT LIKE 'SERV16%' "
)
_DESDE = (
    "FROM MF_CFD AS a INNER JOIN MF_DETCFD AS c ON a.NUMFOL = c.NUMFOL "
    "WHERE a.FECEXP >= ? AND a.FECEXP < ? "
)
SQL = (
    "SELECT LTRIM(RTRIM(c.CODUPC)) AS item, SUM(c.CANTID) AS qty, COUNT(*) AS renglones, "
    "COUNT(DISTINCT a.NUMFOL) AS documentos, SUM(CASE WHEN c.CANTID IS NULL THEN 1 ELSE 0 END) AS nulos "
    + _DESDE + _FILTROS + "GROUP BY LTRIM(RTRIM(c.CODUPC))"
)
# FIX4: tested live on 2026-11-09. Invoice identities are retrieved separately,
# avoiding the slow EXISTS + per-ITEM filters plan. Never use total counts as
# proof of complete relationships: every filtered invoice ID must be present.
SQL_FACTURAS = "SELECT DISTINCT a.NUMFOL " + _DESDE + _FILTROS
_SQL_CUENTAS = "SELECT DISTINCT NUMFOL FROM MF_CUENTAS WHERE NUMFOL IN ({})"
_SQL_RECCFD = "SELECT DISTINCT NUMFOL FROM MF_RECCFD WHERE NUMFOL IN ({})"
_LOTE_FACTURAS = 300  # below SQL Server's 2100-parameter limit

IDENTIDAD = "SELECT DB_NAME(), @@SERVERNAME"
NATURALEZA = "FACTURACION_CFDI_POR_ITEM (no acredita embarque físico)"
CAMPOS = ("item", "facturado_cfdi", "renglones", "documentos", "nulos")


def _guardas(cfg: dict[str, Any]) -> None:
    extra = {str(k).lower(): str(v).lower() for k, v in (cfg.get("atributos_conexion") or {}).items()}
    if extra.get("encrypt", "yes") not in ("yes", "true", "mandatory", "strict"):
        raise ConectorError("EMBARQUES_TLS_DESACTIVADO")
    if extra.get("trustservercertificate", "no") in ("yes", "true", "1"):
        raise ConectorError("EMBARQUES_CERTIFICADO_NO_VERIFICADO")
    if extra.get("applicationintent", "readonly") != "readonly":
        raise ConectorError("EMBARQUES_INTENCION_NO_LECTURA")


def _clave_factura(value: Any) -> str:
    if value is None:
        raise ConectorError("EMBARQUES_NUMFOL_INVALIDO")
    # Numeric and varchar invoice IDs are allowed; avoid changing spelling.
    key = str(value)
    if not key or not key.strip():
        raise ConectorError("EMBARQUES_NUMFOL_INVALIDO")
    return key


def _validar_facturas(
    cfg: dict[str, Any], cred: Any, fechas: list[dt.date], abrir_protegido: Fabrica,
    base: str, rows: list[tuple[Any, ...]],
) -> int:
    """Prove two auxiliary relationships for EVERY invoice in filtered source.

    Query identities and memberships with bound parameters. Do not print or
    persist invoice identifiers; return only verified count for audit metadata.
    A mismatch or ODBC failure rejects the entire capture (fail closed).
    """
    cn = None
    try:
        cn = abrir_protegido(cadena(cfg, cred), int(cfg.get("timeout_s", 60)))
        cur = cn.cursor()
        cur.execute(IDENTIDAD)
        identity = tuple(cur.fetchone() or ())
        if base and (not identity or str(identity[0]).upper() != base.upper()):
            raise ConectorError("EMBARQUES_BASE_DISTINTA", "la validación apunta a otra base")
        cur.execute(SQL_FACTURAS, *fechas)
        invoices = [_clave_factura(r[0]) for r in cur.fetchall()]
        universe = set(invoices)
        if len(universe) != len(invoices):
            raise ConectorError("EMBARQUES_FACTURAS_DUPLICADAS")
        if bool(universe) != bool(rows):
            raise ConectorError("EMBARQUES_UNIVERSOS_INCONSISTENTES")
        # Both tables are consulted only for invoice IDs actually retrieved.
        # Check sets, not just totals: missing invoice IDs are never accepted.
        for table, statement in (("MF_CUENTAS", _SQL_CUENTAS), ("MF_RECCFD", _SQL_RECCFD)):
            found: set[str] = set()
            for i in range(0, len(invoices), _LOTE_FACTURAS):
                batch = invoices[i:i + _LOTE_FACTURAS]
                placeholders = ",".join("?" for _ in batch)
                cur.execute(statement.format(placeholders), *batch)
                found.update(_clave_factura(r[0]) for r in cur.fetchall())
            if found != universe:
                raise ConectorError(
                    "EMBARQUES_RELACIONES_FALTANTES",
                    f"tabla={table}; facturas={len(universe)}; validas={len(found & universe)}; "
                    f"faltantes={len(universe - found)}; inesperadas={len(found - universe)}",
                )
        return len(universe)
    except ConectorError:
        raise
    except Exception as exc:  # sanitized, never include credential or NUMFOL values
        raise ConectorError("ODBC_CONSULTA_FALLIDA", sanear(str(exc), cred.secreto)) from None
    finally:
        if cn is not None:
            try:
                cn.rollback()
            finally:
                cn.close()


def capturar(
    cfg: dict[str, Any],
    month: str,
    hasta: dt.date,
    proveedor: Proveedor | None = None,
    fabrica: Fabrica | None = None,
) -> tuple[bytes, dict[str, Any]]:
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month) or hasta.strftime("%Y-%m") != month:
        raise ConectorError("EMBARQUES_PERIODO_INVALIDO", f"{month} / {hasta}")
    _guardas(cfg)
    cfg = {**cfg, "atributos_conexion": {"Encrypt": "yes", "TrustServerCertificate": "no",
                                         "ApplicationIntent": "ReadOnly", **(cfg.get("atributos_conexion") or {})}}
    cred = obtener(cfg.get("credencial", ""), cfg.get("cuenta_esperada", "sm0003"), proveedor)
    inicio = dt.date.fromisoformat(month + "-01")
    # Query timeout is separate from ODBC login timeout. Apply only to ASBG,
    # not to the shared FG adapter; the driver timeout is not a hard wall-clock watchdog.
    try:
        consulta_timeout = int(cfg.get("timeout_consulta_s", 15))
    except (TypeError, ValueError) as exc:
        raise ConectorError("EMBARQUES_TIMEOUT_INVALIDO") from exc
    if not 5 <= consulta_timeout <= 30:
        raise ConectorError("EMBARQUES_TIMEOUT_INVALIDO")

    def abrir_protegido(conn_str: str, login_timeout: int) -> Any:
        cn = (fabrica or fabrica_pyodbc)(conn_str, login_timeout)
        try:
            cn.timeout = consulta_timeout
        except Exception as exc:  # fail closed instead of silently removing the timeout
            cn.close()
            raise ConectorError("EMBARQUES_TIMEOUT_NO_DISPONIBLE") from exc
        return cn

    fechas = [inicio, hasta + dt.timedelta(days=1)]
    rows, ident = consultar(cfg, cred, SQL, fechas, abrir_protegido, IDENTIDAD)
    base = str(cfg.get("base") or "")
    if base and (not ident or str(ident[0]).upper() != base.upper()):
        raise ConectorError("EMBARQUES_BASE_DISTINTA", "la conexión no apunta a la base configurada")
    # Keep monthly filters and item identities identical to the aggregate.
    # Every invoice must be present in BOTH auxiliary tables. This is an
    # identity check, not a count-equality shortcut.
    facturas_verificadas = _validar_facturas(cfg, cred, fechas, abrir_protegido, base, rows)
    out: dict[str, dict[str, str]] = {}
    for n, r in enumerate(rows, start=1):
        item = "" if r[0] is None else str(r[0]).strip()
        if not item:
            raise ConectorError("EMBARQUES_ITEM_VACIO", f"fila {n}")
        if item in out:
            raise ConectorError("EMBARQUES_ITEM_DUPLICADO", f"fila {n}")
        nulos = int(r[4] or 0)
        try:
            qty = "" if r[1] is None or nulos else format(Decimal(str(r[1])).normalize(), "f")
        except InvalidOperation as exc:
            raise ConectorError("EMBARQUES_CANTIDAD_INVALIDA", f"fila {n}") from exc
        out[item] = {"item": item, "facturado_cfdi": qty, "renglones": str(int(r[2] or 0)),
                     "documentos": str(int(r[3] or 0)), "nulos": str(nulos)}
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=CAMPOS, lineterminator="\n")
    w.writeheader()
    w.writerows(out[k] for k in sorted(out))
    return buf.getvalue().encode("utf-8"), {
        "naturaleza": NATURALEZA,
        "periodo": [inicio.isoformat(), hasta.isoformat()],
        "items": len(out),
        "facturas_verificadas": facturas_verificadas,
        "cuenta": cfg.get("cuenta_esperada", "sm0003"),
    }


def leer_captura(raw: bytes) -> dict[str, dict[str, str]]:
    return {r["item"]: r for r in csv.DictReader(io.StringIO(raw.decode("utf-8")))}


def _sha_file(p: Path) -> str:
    import hashlib  # noqa: PLC0415

    return hashlib.sha256(p.read_bytes()).hexdigest()


def _dentro(data: Path, rel: Any) -> Path | None:
    """Ruta relativa a la carpeta de datos, sin escapar de ella ni seguir enlaces."""
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        return None
    p = (data / rel).resolve()
    base = data.resolve()
    if base not in p.parents or not p.is_file() or (data / rel).is_symlink():
        return None
    return p


def acreditacion_vigente(path: Path | None, aprobadores: frozenset[str], data: Path | None = None) -> tuple[bool, list[str]]:
    """v0.3.1 — la fuente solo alimenta KPIs con una acreditación VERIFICABLE:

    - evidencia de embarques reconocidos por el negocio: archivo existente dentro de la carpeta de
      datos, cuyo SHA-256 coincide;
    - conciliación de ``conciliar.py embarques``: informe existente, SHA-256 coincidente, resultado
      PASS sin diferencias, contra ESA evidencia y contra una captura de embarques producida por el
      conector (``capturas/embarques`` o ``preflight/embarques``);
    - naturaleza física del movimiento, cancelaciones, notas de crédito e identidad EXACTA del ITEM
      declaradas por el negocio, y aprobador autorizado.
    Hashes inventados o archivos alterados ⇒ no acreditado (Embarques/Programa/Faltante N/D)."""
    if path is None or not path.is_file():
        return False, ["sin acreditación funcional de la fuente de embarques (facturación CFDI ≠ embarque físico)"]
    try:
        doc = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return False, ["acreditación ilegible"]
    motivos: list[str] = []
    if doc.get("schema") != "nexus.preview.acreditacion-embarques/1" or doc.get("acreditado") is not True:
        motivos.append("acreditación no vigente")
    if doc.get("aprobado_por") not in aprobadores:
        motivos.append("aprobador no autorizado")
    for k in ("trata_cancelaciones", "trata_notas_credito", "fecha_es_embarque_fisico", "identidad_item_exacta"):
        if doc.get(k) is not True:
            motivos.append(f"criterio no acreditado: {k}")
    data = data or path.parent
    ev = _dentro(data, doc.get("evidencia_ruta"))
    inf = _dentro(data, doc.get("conciliacion_ruta"))
    if ev is None:
        motivos.append("evidencia de embarques inexistente o fuera de la carpeta de datos")
    elif _sha_file(ev) != str(doc.get("evidencia_sha256", "")).lower():
        motivos.append("evidencia alterada: el SHA-256 no coincide")
    if inf is None:
        motivos.append("informe de conciliación inexistente o fuera de la carpeta de datos")
    elif _sha_file(inf) != str(doc.get("conciliacion_sha256", "")).lower():
        motivos.append("conciliación alterada: el SHA-256 no coincide")
    else:
        try:
            rep = json.loads(inf.read_text("utf-8"))
        except ValueError:
            rep = {}
        if rep.get("fuente") != "embarques" or rep.get("resultado") != "PASS" or rep.get("diferencias"):
            motivos.append("la conciliación no es un PASS de embarques sin diferencias")
        if ev is not None and rep.get("evidencia_sha256") != _sha_file(ev):
            motivos.append("la conciliación no corresponde a la evidencia declarada")
        sistema = {
            _sha_file(p)
            for sub in ("capturas", "preflight")
            for p in (data / sub / "embarques").glob("*.raw")
            if (data / sub / "embarques").is_dir()
        }
        if rep.get("directa_sha256") not in sistema:
            motivos.append("la conciliación no se hizo contra una captura de embarques del conector")
    return not motivos, motivos
