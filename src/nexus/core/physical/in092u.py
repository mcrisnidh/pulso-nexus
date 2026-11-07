"""Lector físico IN092U (inventario por stockroom). Puro.

- Corte: ``DD-mmm-YYYY HH:MM:SS`` del encabezado, hora LOCAL de la planta. Se convierte a UTC con
  la zona declarada explícitamente (``business_tz``); sin zona no hay corte (no se asume ninguna).
  Una hora local ambigua o inexistente (cambio de horario) invalida el archivo.
- Filas: ITEM, stockroom, cantidad física (signo al inicio o al final), valor.
- «Excludes Zero Physical Quantity»: un ITEM ausente NO es cero; CORE ya lo trata como desconocido.
- Todos los stockrooms se conservan; la regla CORE decide cuáles son usables (FG).
"""

from __future__ import annotations

import datetime as dt
import re
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

_CUT = re.compile(r"(\d{2})-([A-Za-zÁÉÍÓÚáéíóú]{3})\.?-(\d{4})\s+(\d{2}):(\d{2}):(\d{2})")
_ROW = re.compile(r"^(?P<item>\S+)\s+(?P<room>[A-Z0-9]{1,4})\s+(?P<qty>\S+)\s+(?P<value>\S+)\s*$")
MONTHS = {
    **{m: i for i, m in enumerate(("ene", "feb", "mar", "abr", "may", "jun"), start=1)},
    **{m: i for i, m in enumerate(("jul", "ago", "sep", "oct", "nov", "dic"), start=7)},
    "jan": 1,
    "apr": 4,
    "aug": 8,
    "dec": 12,
    "set": 9,
}


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


def read_in092u(  # noqa: PLR0915 (clasificación lineal de líneas físicas)
    data: bytes, layout: dict[str, Any], business_tz: str | None
) -> PhysicalResult:
    cfg = layout["in092u"]
    res = PhysicalResult(kind="in092u", source_id="fg_inventory")
    text = decode(data, str(cfg["encoding"]), res)
    if text is None:
        return res
    cuts: set[dt.datetime] = set()
    seen: set[tuple[str, str]] = set()
    for lineno, line in split_lines(text, res):
        body = line.rstrip()
        if not body.strip():
            res.count("blank")
            continue
        if re.search(r"Excludes\s+Zero", body, re.IGNORECASE):
            res.flags["excludes_zero_physical"] = True
        m_cut = _CUT.search(body)
        if m_cut:
            month = MONTHS.get(m_cut.group(2).lower())
            try:
                if month is None:
                    raise ValueError(m_cut.group(2))
                cuts.add(
                    dt.datetime(
                        int(m_cut.group(3)),
                        month,
                        int(m_cut.group(1)),
                        int(m_cut.group(4)),
                        int(m_cut.group(5)),
                        int(m_cut.group(6)),
                    )
                )
            except ValueError:
                res.add(lineno, "CUT_UNREADABLE", BLOCKING)
            res.count("page_header", body)
            continue
        if matches_any(cfg["page_patterns"], body):
            res.count("page_header", body)
            continue
        if matches_any(cfg["trailer_patterns"], body):
            res.count("trailer", body)
            continue
        row = _ROW.match(body)
        if row and parse_qty(row.group("qty")) is not None:
            res.count("data_row", body)
            qty = parse_qty(row.group("qty"))
            if qty is not None and qty < 0:
                res.add(lineno, "QTY_NEGATIVE", WARNING, "qty_physical")
            key = (row.group("item"), row.group("room"))
            if key in seen:
                res.add(lineno, "DUPLICATE_ITEM_STOCKROOM", BLOCKING)
            seen.add(key)
            res.records.append(
                {
                    "cut_ts": "",
                    "source_system": str(cfg["source_system"]),
                    "item": row.group("item"),
                    "stockroom": row.group("room"),
                    "qty_physical": fmt_qty(qty),
                    "qty_reserved": "",  # no informado por IN092U: desconocido, no cero
                }
            )
            continue
        if not any(c.isdigit() for c in body):
            res.count("page_text", body)
            continue
        res.count("unclassified", body)
        res.add(lineno, "UNCLASSIFIED_LINE", BLOCKING)
    if len(cuts) > 1:
        res.add(None, "CUT_INCONSISTENT", BLOCKING)
    elif not cuts:
        res.add(None, "CUT_MISSING", BLOCKING)
    else:
        local = next(iter(cuts))
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
