"""Lector físico OE965 «Stock Versus Sales Orders» (ancho fijo, jerárquico). Puro.

Estructura confirmada por el informe saneado del primer probe en ASMXC1200 (R2) y por las 3,796
líneas crudas del Pilot-20. Posiciones 0-based, fin exclusivo; ver ``layouts/physical_v1.json``.

- **Cabecera de ITEM** (≈40 caracteres): ITEM desde la columna 0; FG físico y disponible con signo
  negativo al final. Es traza: NO se usa como FG del corte (el FG sale de IN092U).
- **Línea de pedido** (164 caracteres, ITEM en blanco y heredado de la cabecera): pedido, línea,
  entrega ``M/DD/YY``, ordenada, abierta, asignada, cliente exacto, nombre ``[124:149]``, marca
  opcional ``*`` en la columna 150 y una cifra final que termina en la columna 164 (no se usa).
- **Total de ITEM:** subrayado (guiones en las columnas de cantidad) y, en la línea siguiente,
  las sumas en las mismas columnas. Se CONCILIA con la suma de los pedidos leídos del ITEM.
- **Total general:** rótulo ``*** … ***`` con el mismo subrayado y, debajo, las sumas del archivo.
  Se concilia con la suma de todos los pedidos.
- Encabezados de página repetidos: se cuentan, salvo ``Date: MM/DD/YY`` (fecha del documento,
  PRECISIÓN FECHA: la hora de extracción no existe y no se inventa).

**Equivalencia:** OE965 se entrega con el contrato lógico ``oeras_29`` solo porque comparte la
forma de los campos. NO es el 29 OERAS certificado ni prueba equivalencia entre ambos.

Fallo cerrado: un pedido sin cabecera, una línea con dígitos fuera de estas estructuras, un total
sin su subrayado, un total que no cuadra con lo leído (ordenada o abierta), una entrega ambigua,
un cliente vacío, una cola de pedido ilegible o una identidad duplicada invalidan el archivo.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
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
_TAIL = re.compile(r"^\s*(?P<flag>\*)?\s*(?P<num>\S+)?\s*$")
_TOKEN = re.compile(r"\S+")
COLUMNS = ("qty_ordered", "qty_open", "qty_alloc", "tail")
BLOCKING_TOTALS = frozenset({"qty_ordered", "qty_open"})  # columnas del contrato


def _yy(month: int, day: int, yy: int) -> str | None:
    """Fecha de dos dígitos de año → ISO (siglo 2000: el reporte es del año en curso)."""
    try:
        return dt.date(2000 + yy, month, day).isoformat()
    except ValueError:
        return None


class _Sums:
    """Sumas por columna; ``None`` si alguna cantidad no fue legible (no verificable)."""

    def __init__(self) -> None:
        self.values: dict[str, Decimal | None] = dict.fromkeys(COLUMNS, Decimal(0))
        self.orders = 0

    def add(self, qty: dict[str, Decimal | None]) -> None:
        self.orders += 1
        for col in COLUMNS:
            current, value = self.values[col], qty.get(col)
            self.values[col] = None if current is None or value is None else current + value


def _underline(body: str, groups: list[list[int]]) -> bool:
    """Guiones EXACTAMENTE en las columnas de cantidad (subrayado de un total)."""
    spans = [(m.start(), m.end()) for m in _TOKEN.finditer(body)]
    return spans == [tuple(g) for g in groups] and all(set(body[a:b]) == {"-"} for a, b in spans)


def _total_tokens(body: str, cfg: dict[str, Any]) -> dict[str, Decimal] | None:
    """Cifras de una línea de total: solo números, cada uno terminando en una columna de cantidad
    (admite signo negativo final). ``None`` si la línea no tiene esa forma exacta."""
    if len(body) < min(cfg["total_columns"].values()) or body[:72].strip():
        return None
    ends = {int(v): k for k, v in cfg["total_columns"].items()}
    out: dict[str, Decimal] = {}
    for m in _TOKEN.finditer(body):
        token, end = m.group(), m.end()
        col = ends.get(end) or (ends.get(end - 1) if token.endswith("-") else None)
        value = parse_qty(token)
        if col is None or value is None or col in out:
            return None
        out[col] = value
    return out or None


def read_oe965(  # noqa: PLR0915 (clasificación lineal de líneas físicas)
    data: bytes, layout: dict[str, Any]
) -> PhysicalResult:
    cfg = layout["oe965"]
    res = PhysicalResult(kind="oe965", source_id="oeras_29")
    res.flags["contract_equivalence"] = "NOT_ESTABLISHED"
    text = decode(data, str(cfg["encoding"]), res)
    if text is None:
        return res
    order_s, order_e = cfg["order"]
    blank_until = int(cfg["item_blank_until"])
    doc_dates: set[str] = set()
    current_item: str | None = None
    item_sums = _Sums()
    file_sums = _Sums()
    item_total_seen = False
    pending: str | None = None  # "item" tras un subrayado; "grand" tras un rótulo general
    seen: set[tuple[str, str, str, str]] = set()
    rec: dict[str, Any] = {
        "item_headers": 0,
        "item_totals": 0,
        "grand_totals": 0,
        "items_without_total": 0,
        "flagged_orders": 0,
        "totals_checked": dict.fromkeys(COLUMNS, 0),
        "totals_matched": dict.fromkeys(COLUMNS, 0),
        "totals_not_verifiable": dict.fromkeys(COLUMNS, 0),
    }
    started = False

    def close_item(lineno: int | None) -> None:
        if current_item is not None and not item_total_seen:
            rec["items_without_total"] += 1
            res.add(lineno, "ITEM_TOTAL_MISSING", BLOCKING)

    def reconcile(lineno: int, totals: dict[str, Decimal], sums: _Sums) -> None:
        for col in COLUMNS:
            expected = sums.values[col]
            printed = totals.get(col, Decimal(0))  # columna en blanco = total no impreso (0)
            rec["totals_checked"][col] += 1
            if expected is None:
                rec["totals_not_verifiable"][col] += 1
                res.add(lineno, "TOTAL_NOT_VERIFIABLE", WARNING, col)
            elif expected == printed:
                rec["totals_matched"][col] += 1
            else:
                sev = BLOCKING if col in BLOCKING_TOTALS else WARNING
                res.add(lineno, "TOTAL_MISMATCH", sev, col)

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
        previous, pending = pending, None
        is_order = len(body) > order_e and not body[:blank_until].strip()
        if is_order and re.fullmatch(r"\d{7}", body[order_s:order_e]):
            started = True
            res.count("order_line", body)
            if item_total_seen:
                res.add(lineno, "ORDER_AFTER_ITEM_TOTAL", BLOCKING)
            qty = _order(lineno, line, cfg, current_item, seen, res, rec)
            if qty is not None:
                item_sums.add(qty)
                file_sums.add(qty)
            continue
        if _underline(body, cfg["total_underline"]):
            res.count("total_underline", body)
            pending = "item"
            continue
        label, _, rest = body[:72], None, body[72:]
        if (
            label.strip().startswith("*")
            and label.strip().endswith("*")
            and _underline(" " * 72 + rest, cfg["total_underline"])
        ):
            res.count("grand_total_label", body)
            pending = "grand"
            continue
        totals = _total_tokens(body, cfg)
        if totals is not None and previous == "item":
            res.count("item_total", body)
            if current_item is None or item_total_seen:
                res.add(lineno, "ITEM_TOTAL_WITHOUT_ITEM", BLOCKING)
            else:
                rec["item_totals"] += 1
                item_total_seen = True
                reconcile(lineno, totals, item_sums)
            continue
        if totals is not None and previous == "grand":
            res.count("grand_total", body)
            rec["grand_totals"] += 1
            reconcile(lineno, totals, file_sums)
            continue
        if totals is not None:
            res.count("unclassified", body)
            res.add(lineno, "TOTAL_WITHOUT_UNDERLINE", BLOCKING)
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
            close_item(lineno)
            started = True
            current_item = header.group("item")
            item_sums, item_total_seen = _Sums(), False
            rec["item_headers"] += 1
            res.count("item_header", body)
            if header.end("phys") not in (
                cfg["header_fg_physical_end"],
                cfg["header_fg_physical_end"] + 1,
            ):
                res.add(lineno, "ITEM_HEADER_POSITION", WARNING, "fg_physical")
            if header.end("avail") not in (
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
    close_item(None)
    if rec["grand_totals"] == 0:
        res.add(None, "GRAND_TOTAL_MISSING", WARNING)
    rec["orders_read"] = file_sums.orders
    res.flags["reconciliation"] = rec
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
    for record in res.records:
        record["extraction_ts"] = str(res.document_time.get("value", ""))
    return res


def _order(
    lineno: int,
    line: str,
    cfg: dict[str, Any],
    item: str | None,
    seen: set[tuple[str, str, str, str]],
    res: PhysicalResult,
    rec: dict[str, Any],
) -> dict[str, Decimal | None] | None:
    if item is None:
        res.add(lineno, "ORDER_WITHOUT_ITEM", BLOCKING)
        return None
    order = line[cfg["order"][0] : cfg["order"][1]]
    line_no = line[cfg["line"][0] : cfg["line"][1]].strip()
    due_raw = line[cfg["due_date"][0] : cfg["due_date"][1]].strip()
    m_due = _DUE.match(due_raw)
    due = _yy(int(m_due.group(1)), int(m_due.group(2)), int(m_due.group(3))) if m_due else None
    if due is None:
        res.add(lineno, "DUE_DATE_UNREADABLE_OR_AMBIGUOUS", BLOCKING, "due_date")
    qty: dict[str, str] = {}
    values: dict[str, Decimal | None] = {}
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
        values[name] = value
    tail = _TAIL.match(line[cfg["order_flag"][0] : cfg["order_tail"][1]])
    tail_num = tail.group("num") if tail else None
    if tail is None or (tail_num is not None and parse_qty(tail_num) is None):
        res.add(lineno, "ORDER_TAIL_UNREADABLE", BLOCKING, "tail")
        values["tail"] = None
    else:
        values["tail"] = parse_qty(tail_num) if tail_num else Decimal(0)
        if tail.group("flag"):
            rec["flagged_orders"] += 1
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
    return values


def distinct_customers(result: PhysicalResult) -> dict[str, int]:
    """Claves de cliente exactas y su número de líneas (SOLO para un archivo local privado de
    clasificación R3; nunca para informes saneados)."""
    out: dict[str, int] = {}
    for rec in result.records:
        out[rec["customer_key_exact"]] = out.get(rec["customer_key_exact"], 0) + 1
    return dict(sorted(out.items()))
