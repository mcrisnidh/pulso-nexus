"""Lector físico IN092U (inventario por stockroom). Puro.

Estructura confirmada por el informe saneado del primer probe en ASMXC1200 (R2):

- **Corte:** fecha y hora SEPARADAS en el encabezado de cada página: ``Date: MM/DD/YY`` en la
  línea de título y ``<rótulo>: H:MM:SS`` sola en otra línea. Es hora LOCAL de la planta: se
  convierte a UTC con la zona declarada (``business_tz``); sin zona no hay corte. Todas las páginas
  deben coincidir. Una hora local ambigua o inexistente (cambio de horario) invalida el archivo.
- **Fila de datos** (129 caracteres): ITEM ``[0:15]``, descripción ``[16:52]``, columnas
  auxiliares, cantidad física con 3 decimales terminando en la columna 91, costo unitario y valor
  terminando en la columna 129. La fila NO trae el stockroom.
- **Total de grupo:** ``<rótulo>: <stockroom>  <rótulo>: <cantidad> <valor>`` en las mismas
  columnas. Las filas anteriores (desde el total previo) pertenecen a ese stockroom. La asignación
  se DEMUESTRA: la suma de cantidades de las filas debe ser igual al total impreso; si no, el
  archivo es inválido (fallo cerrado).
- «Excludes Zero Physical Quantity»: un ITEM ausente NO es cero; CORE lo trata como desconocido.
- Todos los stockrooms se conservan con su cantidad (negativos incluidos). La regla CORE decide
  cuáles son usables (FG); QH, RM y los demás quedan separados y no asignables.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nexus.core.physical.common import (
    BLOCKING,
    WARNING,
    PhysicalResult,
    decode,
    fmt_qty,
    matches_any,
    parse_qty,
    split_lines,
)

_TOKEN = re.compile(r"\S+")
_GROUP = re.compile(r"^(?P<label>\S+)\s*:\s+(?P<room>[A-Z0-9]{1,4})\s+(?P<label2>\S+)\s*:\s+\S")


def local_to_utc(local: dt.datetime, tz_name: str) -> tuple[str | None, str]:
    """Hora local → instante UTC ``…Z``. Rechaza horas ambiguas o inexistentes."""
    try:
        tz = dt.UTC if tz_name == "UTC" else ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None, "TZ_UNAVAILABLE"
    a = local.replace(tzinfo=tz, fold=0)
    b = local.replace(tzinfo=tz, fold=1)
    for candidate in (a, b):
        if candidate.astimezone(dt.UTC).astimezone(tz).replace(tzinfo=None) != local:
            return None, "CUT_LOCAL_TIME_NONEXISTENT"  # hueco del cambio de horario
    if a.utcoffset() != b.utcoffset():
        return None, "CUT_LOCAL_TIME_AMBIGUOUS"  # la hora local ocurre dos veces
    utc = a.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return utc, ""


def _amount(line: str, span: list[int]) -> Decimal | None:
    """Cifra del campo ``span`` (columnas de guiones del encabezado). Observado en el probe R2:
    la cifra se alinea a la derecha y termina UNA columna antes del fin del campo (la última queda
    para el signo negativo final). Una cifra corrida de columna no se acepta."""
    start, stop = int(span[0]), int(span[1])
    for m in _TOKEN.finditer(line):
        token = m.group()
        if m.end() == stop - 1 or (m.end() == stop and token.endswith("-")):
            if m.start() < start - 4:  # desborde a la izquierda acotado
                return None
            return parse_qty(token)
    return None


def _data_row(line: str, cfg: dict[str, Any]) -> tuple[str, Decimal, Decimal] | None:
    """(ITEM, cantidad física, valor) si la línea tiene la forma exacta de una fila de datos."""
    i0, i1 = cfg["item"]
    if len(line.rstrip()) < cfg["qty_physical"][1] or not line[:1].strip():
        return None
    item = line[i0:i1].strip()
    if not item or " " in item or line[i1 : i1 + 1].strip():
        return None
    qty, value = _amount(line, cfg["qty_physical"]), _amount(line, cfg["value"])
    if qty is None or value is None:
        return None
    return item, qty, value


def _group_total(line: str, cfg: dict[str, Any]) -> tuple[str, Decimal, Decimal] | None:
    """(stockroom, cantidad total, valor total) si la línea es un total de grupo."""
    start = int(cfg["group_label_start"])
    if line[:start].strip():
        return None
    m = _GROUP.match(line[start:])
    if not m:
        return None
    qty, value = _amount(line, cfg["qty_physical"]), _amount(line, cfg["value"])
    if qty is None or value is None:
        return None
    return m.group("room"), qty, value


def read_in092u(  # noqa: PLR0915 (clasificación lineal de líneas físicas)
    data: bytes, layout: dict[str, Any], business_tz: str | None
) -> PhysicalResult:
    cfg = layout["in092u"]
    res = PhysicalResult(kind="in092u", source_id="fg_inventory")
    text = decode(data, str(cfg["encoding"]), res)
    if text is None:
        return res
    date_re, time_re = re.compile(cfg["date_pattern"]), re.compile(cfg["time_pattern"])
    dates: set[dt.date] = set()
    times: set[dt.time] = set()
    pending: list[tuple[int, str, Decimal, Decimal]] = []
    groups: list[dict[str, Any]] = []
    seen_rooms: set[str] = set()
    seen: set[tuple[str, str]] = set()
    rows_total = 0
    for lineno, line in split_lines(text, res):
        body = line.rstrip()
        if not body.strip():
            res.count("blank")
            continue
        if re.search(r"Excludes\s+Zero", body, re.IGNORECASE):
            res.flags["excludes_zero_physical"] = True
        m_date = date_re.search(body)
        if m_date:
            try:
                dates.add(
                    dt.date(2000 + int(m_date.group(3)), int(m_date.group(1)), int(m_date.group(2)))
                )
            except ValueError:
                res.add(lineno, "CUT_DATE_UNREADABLE", BLOCKING)
            res.count("page_header", body)
            continue
        m_time = time_re.match(body)
        if m_time:
            try:
                times.add(dt.time(int(m_time.group(1)), int(m_time.group(2)), int(m_time.group(3))))
            except ValueError:
                res.add(lineno, "CUT_TIME_UNREADABLE", BLOCKING)
            res.count("cut_time", body)
            continue
        group = _group_total(line, cfg)
        if group is not None:
            res.count("group_total", body)
            room, qty_total, value_total = group
            qty_sum = sum((r[2] for r in pending), Decimal(0))
            value_sum = sum((r[3] for r in pending), Decimal(0))
            info = {
                "stockroom": room,
                "rows": len(pending),
                "qty_total_match": qty_sum == qty_total,
                "value_total_match": value_sum == value_total,
            }
            groups.append(info)
            if room in seen_rooms:
                res.add(lineno, "GROUP_STOCKROOM_REPEATED", BLOCKING)
            seen_rooms.add(room)
            if not pending:
                res.add(lineno, "GROUP_WITHOUT_ROWS", WARNING)
            if not info["qty_total_match"]:
                res.add(lineno, "GROUP_QTY_TOTAL_MISMATCH", BLOCKING, "qty_physical")
            if not info["value_total_match"]:
                res.add(lineno, "GROUP_VALUE_TOTAL_MISMATCH", WARNING, "value")
            for row_line, item, qty, _value in pending:
                if (item, room) in seen:
                    res.add(row_line, "DUPLICATE_ITEM_STOCKROOM", BLOCKING)
                seen.add((item, room))
                if qty < 0:
                    res.add(row_line, "QTY_NEGATIVE", WARNING, "qty_physical")
                elif qty == 0:
                    res.add(row_line, "QTY_ZERO_DESPITE_EXCLUSION", WARNING, "qty_physical")
                res.records.append(
                    {
                        "cut_ts": "",
                        "source_system": str(cfg["source_system"]),
                        "item": item,
                        "stockroom": room,
                        "qty_physical": fmt_qty(qty),
                        "qty_reserved": "",  # no informado por IN092U: desconocido, no cero
                    }
                )
            pending = []
            continue
        row = _data_row(line, cfg)
        if row is not None:
            res.count("data_row", body)
            rows_total += 1
            pending.append((lineno, *row))
            continue
        if matches_any(cfg["page_patterns"], body):
            res.count("page_header", body)
            continue
        if matches_any(cfg["trailer_patterns"], body):
            res.count("trailer", body)
            continue
        if not any(c.isdigit() for c in body):
            res.count("page_text", body)
            continue
        res.count("unclassified", body)
        res.add(lineno, "UNCLASSIFIED_LINE", BLOCKING)
    for row_line, *_ in pending:
        res.add(row_line, "ROW_WITHOUT_GROUP_TOTAL", BLOCKING)
    res.flags["reconciliation"] = {
        "data_rows": rows_total,
        "rows_assigned_to_stockroom": len(res.records),
        "rows_without_group_total": len(pending),
        "groups": groups,
        "cut_dates_distinct": len(dates),
        "cut_times_distinct": len(times),
    }
    if len(dates) > 1 or len(times) > 1:
        res.add(None, "CUT_INCONSISTENT", BLOCKING)
    elif not dates or not times:
        res.add(None, "CUT_MISSING", BLOCKING)
    else:
        local = dt.datetime.combine(next(iter(dates)), next(iter(times)))
        if not business_tz:
            res.add(None, "BUSINESS_TZ_NOT_DECLARED", BLOCKING)
        else:
            utc, code = local_to_utc(local, business_tz)
            if utc is None:
                res.add(None, code, BLOCKING)
            else:
                res.document_time = {
                    "value": utc,
                    "precision": "second",
                    "source": "document_header",
                    "local": local.isoformat(),
                    "business_tz": business_tz,
                }
    if not res.flags.get("excludes_zero_physical"):
        res.add(None, "ZERO_EXCLUSION_NOT_DECLARED", WARNING)
    if not res.records:
        res.add(None, "NO_DATA_ROWS", BLOCKING)
    for rec in res.records:
        rec["cut_ts"] = str(res.document_time.get("value", ""))
    return res
