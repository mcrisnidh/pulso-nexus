"""Lector físico OE965 «Stock Versus Sales Orders» (ancho fijo, jerárquico). Puro.

Estructura (posiciones 0-based, fin exclusivo; ver ``layouts/physical_v1.json``):

- Cabecera de ITEM (≈40 caracteres): ITEM desde la columna 0; FG físico y disponible con signo
  negativo al final. Se cuenta como traza; NO se usa como FG del corte (el FG sale de IN092U).
- Línea de pedido (ITEM en blanco, heredado de la cabecera anterior): pedido, línea, entrega
  ``M/DD/YY``, ordenada, abierta, asignada, cliente exacto y nombre.
- Encabezados de página repetidos: se cuentan y no se interpretan, salvo ``Date: MM/DD/YY``
  (fecha del documento, PRECISIÓN FECHA: la hora de extracción no existe y no se inventa).

Fallo cerrado: un pedido sin cabecera de ITEM, una línea con dígitos no reconocida, una entrega
ilegible o ambigua, un cliente vacío o una identidad duplicada invalidan el archivo.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from nexus.core.physical.common import (
    BLOCKING,
    WARNING,
    PhysicalResult,
    decode,
    field_at,
    fmt_qty,
    matches_any,
    parse_qty,
    split_lines,
)

_DOC_DATE = re.compile(r"Date\s*:\s*(\d{1,2})/(\d{1,2})/(\d{2})(?!\d)")
_DUE = re.compile(r"^(\d{1,2})/(\d{2})/(\d{2})$")  # M/DD/YY o MM/DD/YY (día SIEMPRE 2 dígitos)
_HEADER = re.compile(r"^(?P<item>\S+)\s+(?P<phys>\S+)\s+(?P<avail>\S+)\s*$")


def _yy(month: int, day: int, yy: int) -> str | None:
    """Fecha de dos dígitos de año → ISO (siglo 2000: el reporte es del año en curso)."""
    try:
        return dt.date(2000 + yy, month, day).isoformat()
    except ValueError:
        return None


def read_oe965(  # noqa: PLR0915 (clasificación lineal de líneas físicas)
    data: bytes, layout: dict[str, Any]
) -> PhysicalResult:
    cfg = layout["oe965"]
    res = PhysicalResult(kind="oe965", source_id="oeras_29")
    text = decode(data, str(cfg["encoding"]), res)
    if text is None:
        return res
    order_s, order_e = cfg["order"]
    blank_until = int(cfg["item_blank_until"])
    doc_dates: set[str] = set()
    current_item: str | None = None
    seen: set[tuple[str, str, str, str]] = set()
    started = False
    for lineno, line in split_lines(text, res):
        body = line.rstrip()
        if not body.strip():
            res.count("blank")
            continue
        m_date = _DOC_DATE.search(body)
        if m_date:
            iso = _yy(int(m_date.group(1)), int(m_date.group(2)), int(m_date.group(3)))
            if iso is None:
                res.add(lineno, "DOCUMENT_DATE_UNREADABLE", BLOCKING)
            else:
                doc_dates.add(iso)
        is_order = len(body) > order_e and not body[:blank_until].strip()
        if is_order and re.fullmatch(r"\d{7}", body[order_s:order_e]):
            started = True
            res.count("order_line", body)
            _order(lineno, line, cfg, current_item, seen, res)
            continue
        if matches_any(cfg["page_patterns"], body):
            res.count("page_header", body)
            continue
        if matches_any(cfg["trailer_patterns"], body):
            res.count("trailer", body)
            continue
        header = _HEADER.match(body)
        if header and not body[0].isspace() and parse_qty(header.group("phys")) is not None:
            if parse_qty(header.group("avail")) is None:
                res.add(lineno, "ITEM_HEADER_UNREADABLE", BLOCKING)
                continue
            started = True
            current_item = header.group("item")
            res.count("item_header", body)
            phys_end = header.end("phys")
            avail_end = header.end("avail")
            if phys_end not in (cfg["header_fg_physical_end"], cfg["header_fg_physical_end"] + 1):
                res.add(lineno, "ITEM_HEADER_POSITION", WARNING, "fg_physical")
            if avail_end not in (
                cfg["header_fg_available_end"],
                cfg["header_fg_available_end"] + 1,
            ):
                res.add(lineno, "ITEM_HEADER_POSITION", WARNING, "fg_available")
            continue
        if not any(c.isdigit() for c in body):
            res.count("page_text", body)  # títulos de columnas y texto sin cifras
            continue
        if not started:
            res.count("preamble", body)
            continue
        res.count("unclassified", body)
        res.add(lineno, "UNCLASSIFIED_LINE", BLOCKING)
    if len(doc_dates) == 1:
        res.document_time = {
            "value": next(iter(doc_dates)),
            "precision": "date",
            "source": "document_header",
        }
    elif len(doc_dates) > 1:
        res.add(None, "DOCUMENT_DATE_INCONSISTENT", BLOCKING)
    else:
        res.add(None, "DOCUMENT_DATE_MISSING", BLOCKING)
    if not res.records:
        res.add(None, "NO_ORDER_LINES", BLOCKING)
    for rec in res.records:
        rec["extraction_ts"] = str(res.document_time.get("value", ""))
    return res


def _order(
    lineno: int,
    line: str,
    cfg: dict[str, Any],
    item: str | None,
    seen: set[tuple[str, str, str, str]],
    res: PhysicalResult,
) -> None:
    if item is None:
        res.add(lineno, "ORDER_WITHOUT_ITEM", BLOCKING)
        return
    order = line[cfg["order"][0] : cfg["order"][1]]
    line_no = line[cfg["line"][0] : cfg["line"][1]].strip()
    due_raw = line[cfg["due_date"][0] : cfg["due_date"][1]].strip()
    m_due = _DUE.match(due_raw)
    due = _yy(int(m_due.group(1)), int(m_due.group(2)), int(m_due.group(3))) if m_due else None
    if due is None:
        res.add(lineno, "DUE_DATE_UNREADABLE_OR_AMBIGUOUS", BLOCKING, "due_date")
    qty: dict[str, str] = {}
    for name in ("qty_ordered", "qty_open", "qty_alloc"):
        token = field_at(line, *cfg[name])
        value = parse_qty(token) if token else None
        if not token:
            res.add(lineno, "QTY_MISSING", WARNING, name)  # desconocida, no cero
        elif value is None:
            res.add(lineno, "QTY_UNREADABLE", WARNING, name)
        elif value < 0:
            res.add(lineno, "QTY_NEGATIVE", WARNING, name)
        qty[name] = fmt_qty(value)
    customer = line[cfg["customer_key"][0] : cfg["customer_key"][1]].rstrip()
    if not customer.strip():
        res.add(lineno, "CUSTOMER_MISSING", BLOCKING, "customer_key")
    if not line_no:
        res.add(lineno, "LINE_MISSING", BLOCKING, "line")
    identity = (item, customer, order, line_no)
    if identity in seen:
        res.add(lineno, "DUPLICATE_IDENTITY", BLOCKING)
    seen.add(identity)
    res.records.append(
        {
            "extraction_ts": "",
            "source_system": str(cfg["source_system"]),
            "report_id": str(cfg["report_id"]),
            "item": item,
            "customer_key_exact": customer,
            "customer_name": line[cfg["customer_name"][0] : cfg["customer_name"][1]].strip(),
            "order": order,
            "line": line_no,
            "due_date": due or "",
            "qty_ordered": qty["qty_ordered"],
            "qty_open": qty["qty_open"],
        }
    )


def distinct_customers(result: PhysicalResult) -> dict[str, int]:
    """Claves de cliente exactas y su número de líneas (SOLO para un archivo local privado de
    clasificación R3; nunca para informes saneados)."""
    out: dict[str, int] = {}
    for rec in result.records:
        out[rec["customer_key_exact"]] = out.get(rec["customer_key_exact"], 0) + 1
    return dict(sorted(out.items()))
