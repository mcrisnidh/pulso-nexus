"""Conector FG por ODBC (cuenta ``remcb``), SOLO LECTURA, con la semántica de IN092U.

Procedencia: consulta ODBC original del libro «CO 36 inventory 32INAS.xlsx» (paquete de relevo
de noviembre): ``DB2LOCAL`` → ``AULT2F2PRD.STKBAL`` con ``PNUM60`` (ITEM), ``STRC60`` (stockroom),
``SPHY60`` (cantidad física) y filtro ``CONO60 = '36'`` y ``SPHY60 > 0``. PULSO Web no tenía un
conector FG por ODBC (``stage50e_fg`` leía el FG de la cabecera OE965).

Semántica reproducida de IN092U («Excludes Zero Physical Quantity»): por defecto el filtro es
``SPHY60 <> 0`` (conserva negativos, que NEXUS trata como NO CERTIFICABLE). El filtro heredado
``> 0`` es configurable; la conciliación contra el IN092U manual decide cuál reproduce el reporte.

Reglas: ITEM exacto (solo se quita el relleno a la derecha de un campo CHAR; nunca espacios
internos ni iniciales, ni mayúsculas); (ITEM, stockroom) duplicado = ERROR (no se suma);
cantidad nula = desconocida (nunca 0); un ITEM ausente sigue siendo desconocido en NEXUS.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation
from typing import Any

from . import ConectorError
from .credenciales import Proveedor, obtener
from .odbc import Fabrica, consultar, identificador

DEFAULT = {
    "biblioteca": "AULT2F2PRD",
    "tabla": "STKBAL",
    "columnas": {"item": "PNUM60", "stockroom": "STRC60", "fisico": "SPHY60", "compania": "CONO60"},
    "compania": "36",
    "filtro_cantidad": "<>",
    "sufijo_sql": "FOR READ ONLY",
}
CAMPOS = ("item", "stockroom", "qty_physical")


def sql(cfg: dict[str, Any]) -> str:
    c = {**DEFAULT["columnas"], **(cfg.get("columnas") or {})}
    lib = identificador(cfg.get("biblioteca", DEFAULT["biblioteca"]), "biblioteca")
    tab = identificador(cfg.get("tabla", DEFAULT["tabla"]), "tabla")
    cols = {k: identificador(v, f"columnas.{k}") for k, v in c.items()}
    op = cfg.get("filtro_cantidad", DEFAULT["filtro_cantidad"])
    if op not in ("<>", ">"):
        raise ConectorError("CONFIG_FILTRO_INVALIDO", "filtro_cantidad debe ser '<>' o '>'")
    suffix = cfg.get("sufijo_sql", DEFAULT["sufijo_sql"])
    if suffix not in ("", "FOR READ ONLY", "FOR FETCH ONLY"):
        raise ConectorError("CONFIG_SUFIJO_INVALIDO", suffix)
    return (
        f"SELECT {cols['item']}, {cols['stockroom']}, {cols['fisico']} FROM {lib}.{tab} "
        f"WHERE {cols['compania']} = ? AND {cols['fisico']} {op} 0 {suffix}"
    ).strip()


def _item(raw: Any) -> str:
    s = "" if raw is None else str(raw)
    return s.rstrip(" ")  # relleno CHAR a la derecha; espacios internos e iniciales se conservan


def normalizar(rows: list[tuple[Any, ...]]) -> list[dict[str, str]]:
    out: dict[tuple[str, str], dict[str, str]] = {}
    for n, r in enumerate(rows, start=1):
        if len(r) < 3:
            raise ConectorError("FG_FILA_INCOMPLETA", f"fila {n}")
        item, room = _item(r[0]), _item(r[1]).strip()
        if not item.strip() or not room:
            raise ConectorError("FG_IDENTIDAD_VACIA", f"fila {n}")
        if r[2] is None or str(r[2]).strip() == "":
            qty = ""  # desconocida: NUNCA 0
        else:
            try:
                d = Decimal(str(r[2]).strip())
            except InvalidOperation as exc:
                raise ConectorError("FG_CANTIDAD_INVALIDA", f"fila {n}") from exc
            qty = format(d.normalize(), "f") if d != d.to_integral() else str(int(d))
        key = (item, room)
        if key in out:
            raise ConectorError("FG_DUPLICADO", f"(ITEM, stockroom) repetido en fila {n}: no se suma")
        out[key] = {"item": item, "stockroom": room, "qty_physical": qty}
    if not out:
        raise ConectorError("FG_VACIO", "la consulta no devolvió filas")
    return [out[k] for k in sorted(out)]


def capturar(cfg: dict[str, Any], proveedor: Proveedor | None = None, fabrica: Fabrica | None = None) -> tuple[bytes, dict[str, Any]]:
    cred = obtener(cfg.get("credencial", ""), cfg.get("cuenta_esperada", "remcb"), proveedor)
    query = sql(cfg)
    rows, _ = consultar(cfg, cred, query, [str(cfg.get("compania", DEFAULT["compania"]))], fabrica)
    records = normalizar(rows)
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=CAMPOS, lineterminator="\n")
    w.writeheader()
    w.writerows(records)
    return buf.getvalue().encode("utf-8"), {
        "filas": len(records),
        "cuenta": cfg.get("cuenta_esperada", "remcb"),
        "consulta": query,
        "desconocidas": sum(1 for r in records if r["qty_physical"] == ""),
    }


def registros_contrato(raw: bytes) -> list[dict[str, str]]:
    """Captura (CSV canónico) → registros del contrato ``fg_inventory``."""
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    return [
        {"cut_ts": "", "source_system": "ODBC_STKBAL", "item": r["item"], "stockroom": r["stockroom"],
         "qty_physical": r["qty_physical"], "qty_reserved": ""}
        for r in rows
    ]
