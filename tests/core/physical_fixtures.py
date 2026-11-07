"""Fixtures SINTÉTICOS con el formato físico de OE965, Portal CAT e IN092U.

Todos los valores son ficticios (prefijo ``QZX``, cantidades y pedidos inventados). Las posiciones
siguen ``layouts/physical_v1.json``. Nunca se usan archivos ni filas reales.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexus.core import demo

CAT = "QZXCAT    001"
CAT_SPACED = "QZXCAT   001"  # una clave distinta (espacios): NO es CAT
TUPY = "QZXTUP    000"
APTIV = "QZXAPT    001"
DELPHI = "QZXDEL    001"
OTHER = "QZXOTH    001"
REVIEW = "QZXREV    000"


def _put(buf: list[str], start: int, end: int, text: str, align: str = "left") -> None:
    width = end - start
    if align == "right" and len(text) > width:  # desborde real: más dígitos que el ancho
        buf[end - len(text) : end] = list(text)
        return
    text = text.rjust(width) if align == "right" else text.ljust(width)
    assert len(text) <= width, (text, width)
    buf[start:end] = list(text)


def order_line(
    order: str,
    line: str,
    due: str,
    ordered: str,
    open_qty: str,
    customer: str,
    name: str = "CLIENTE SINTETICO",
    alloc: str = "0",
    flag: str = "",
    tail: str = "1200",
) -> str:
    """Línea de pedido de 164 caracteres con la forma observada en el probe R2."""
    buf = [" "] * 164
    _put(buf, 48, 55, order)
    _put(buf, 56, 59, line, "right")
    _put(buf, 62, 70, due, "right")
    _put(buf, 77, 82, ordered, "right")
    _put(buf, 89, 94, open_qty, "right")
    _put(buf, 101, 106, alloc, "right")
    _put(buf, 109, 122, customer)
    _put(buf, 124, 149, name)
    _put(buf, 150, 151, flag)
    _put(buf, 151, 164, tail, "right")
    return "".join(buf)


def item_header(item: str, physical: str, available: str) -> str:
    buf = [" "] * 41
    _put(buf, 0, len(item), item)
    _put(buf, 27 - len(physical), 27, physical)
    _put(buf, 40 - len(available), 40, available)
    return "".join(buf).rstrip()


def _q(raw: str) -> int:
    """Cantidad sintética del fixture (admite signo final); vacía no suma."""
    raw = raw.strip()
    if not raw:
        return 0
    return -int(raw[:-1]) if raw.endswith("-") else int(raw)


def _fmt(n: int) -> str:
    return f"{-n}-" if n < 0 else str(n)


UNDERLINE = " " * 72 + "-" * 10 + "  " + "-" * 10 + "  " + "-" * 10 + " " * 48 + "-" * 10


def total_line(ordered: int, open_qty: int, alloc: int, tail: int) -> str:
    buf = [" "] * 164
    _put(buf, 72, 82, _fmt(ordered), "right")
    _put(buf, 84, 94, _fmt(open_qty), "right")
    _put(buf, 96, 106, _fmt(alloc), "right")
    _put(buf, 154, 164, _fmt(tail), "right")
    return "".join(buf)


def label_line(label: str) -> str:
    return f"{' ' * 41}{label:<31}" + UNDERLINE[72:]


def page_header(date: str = "11/01/26", page: int = 1) -> list[str]:
    """Encabezado de página con la forma observada (sin valores reales)."""
    title = f"OE965{' ' * 16}36 - SINTETICOS SA - REPORT{' ' * 17}STOCK VERSUS SALES ORDERS"
    title = f"{title:<137}Date: {date}    Page: {page:>4}"
    return [
        title,
        "-" * 165,
        f"{'':<74}Warehouse: *ALL",
        "Selection: AA - Includes all customers",
        f"{'':<64}Item         Ord          Opn        All     Customer             Customer"
        "            >       Value",
        "-" * 165,
    ]


# Escenario OE965 de noviembre (sintético)
OE_ITEMS: list[tuple[str, str, str, list[tuple[str, ...]]]] = [
    (
        "QZXITEM1",
        "150",
        "6516-",
        [
            ("7310101", "1", "11/10/26", "300", "300", TUPY),
            ("7310102", "1", "11/15/26", "999", "999", CAT),  # CAT: sustituido por el Portal
            ("7310103", "1", "10/20/26", "50", "50", OTHER),  # vencida: entra
            ("7310104", "1", "12/05/26", "700", "700", OTHER),  # futura: solo vista
        ],
    ),
    (
        "QZXITEM2",
        "25",
        "25",
        [
            ("7310201", "1", "11/20/26", "120", "120", APTIV),
            ("7310202", "1", "11/21/26", "80", "80", DELPHI),  # DELPHI ≠ APTIV
            ("7310203", "2", "11/25/26", "40", "40", CAT_SPACED),  # no es CAT (clave exacta)
        ],
    ),
    (
        "QZXITEM3",
        "0",
        "0",
        [
            ("7310301", "1", "11/28/26", "80", "80", CAT),  # CAT sin Portal mapeado
        ],
    ),
]


def _sums(orders: list[tuple[str, ...]]) -> tuple[int, int, int, int]:
    o = sum(_q(x[3]) for x in orders)
    p = sum(_q(x[4]) for x in orders)
    a = sum(_q(x[7]) if len(x) > 7 else 0 for x in orders)
    t = sum(_q(x[9]) if len(x) > 9 else 1200 for x in orders)
    return o, p, a, t


def oe965_bytes(
    date: str = "11/01/26",
    items: list[tuple[str, str, str, list[tuple[str, ...]]]] | None = None,
    newline: str = "\r\n",
    pages: bool = True,
    totals: bool = True,
    total_override: dict[str, tuple[int, int, int, int]] | None = None,
    grand: bool = True,
) -> bytes:
    """OE965 sintético con la estructura observada: páginas, cabecera de ITEM, pedidos, subrayado
    y total por ITEM, totales generales con rótulo y fin de reporte."""
    items = items or OE_ITEMS
    lines = [*page_header(date, 1)] if pages else []
    all_orders: list[tuple[str, ...]] = []
    for n, (item, phys, avail, orders) in enumerate(items):
        if pages and n == 2:
            lines += ["\f" + page_header(date, 2)[0], *page_header(date, 2)[1:]]
        lines.append(item_header(item, phys, avail))
        lines += [order_line(*o) for o in orders]
        all_orders += orders
        if totals:
            sums = (total_override or {}).get(item, _sums(orders))
            lines += [UNDERLINE, total_line(*sums)]
    if totals and grand:
        g = _sums(all_orders)
        lines += [label_line("*** TOTALS FOR WH ***"), total_line(*g)]
        lines += [label_line("**** TOTALS FOR REPORT ****"), total_line(*g)]
    lines.append("")
    lines.append(f"{'':<72}*** END REPORT ***")
    return (newline.join(lines) + newline).encode("cp850")


# Portal CAT (UTF-16, TAB, preámbulo)
PORTAL_HEADER = [
    "Line ID",
    "Part Number",
    "Status",
    "Ordered",
    "Shipped",
    "Ship Date",
    "Delivery Date",
]
PORTAL_ROWS = [
    ("PL-1", "QZX PORTAL 1", "Open", "400", "100", "11/18/2026", "11/20/2026"),
    ("PL-2", "QZX PORTAL 1", "Cancelled", "50", "0", "11/19/2026", "11/21/2026"),
    ("PL-3", "QZX PORTAL 1", "Open", "200", "", "12/13/2026", "12/15/2026"),  # futura
]


def portal_bytes(
    rows: list[tuple[str, ...]] | None = None,
    header: list[str] | None = None,
    encoding: str = "utf-16-le",
    preamble: bool = True,
) -> bytes:
    lines = (
        ["Supplier Portal Export", "Supplier: QZX", "Generated by portal", ""] if preamble else []
    )
    lines.append("\t".join(header or PORTAL_HEADER))
    lines += ["\t".join(r) for r in (rows if rows is not None else PORTAL_ROWS)]
    text = "\r\n".join(lines) + "\r\n"
    bom = (
        b"\xff\xfe" if encoding == "utf-16-le" else b"\xfe\xff" if encoding == "utf-16-be" else b""
    )
    return bom + text.encode(encoding)


HOMOLOGATION = (
    "item_portal,item_erp,status,evidence\n"
    "QZX PORTAL 1,QZXITEM1,APPROVED,referencia cruzada sintetica\n"
    "QZX PORTAL 9,QZXITEM3,PROVISIONAL,sin evidencia (no se aplica)\n"
)

# IN092U (estructura observada en el probe R2)
IN_ROWS = [
    ("QZXITEM1", "FG", "150.000"),
    ("QZXITEM2", "FG", "5.000-"),  # negativo con signo al final
    ("QZXITEM1", "QF", "40.000"),
    ("QZXITEM2", "RM", "300.000"),
    ("QZXITEM2", "QH", "7.500"),
]


def _dec(raw: str) -> float:
    raw = raw.strip()
    return -float(raw[:-1]) if raw.endswith("-") else float(raw)


def _amount_put(buf: list[str], start: int, end: int, text: str) -> None:
    """Como en el reporte real: la cifra termina una columna antes del fin del campo; esa última
    columna solo la ocupa el signo negativo final."""
    if text.endswith("-"):
        _put(buf, start, end, text, "right")
    else:
        _put(buf, start, end - 1, text, "right")


def in092u_row(item: str, qty: str, value: str = "1,250.00", desc: str = "PIEZA SINTETICA") -> str:
    buf = [" "] * 129
    _put(buf, 0, 15, item)
    _put(buf, 16, 52, desc)
    _put(buf, 53, 58, "F-01")
    _put(buf, 59, 63, "Y")
    _put(buf, 64, 69, "001", "right")
    _put(buf, 70, 75, "002", "right")
    _amount_put(buf, 76, 92, qty)
    _amount_put(buf, 93, 111, ".12500")
    _amount_put(buf, 113, 130, value)
    return "".join(buf)


def in092u_group_total(room: str, qty: str, value: str) -> str:
    buf = [" "] * 129
    _put(buf, 53, 75, f"Stockroom: {room}  Total:")
    _amount_put(buf, 76, 92, qty)
    _amount_put(buf, 113, 130, value)
    return "".join(buf)


def _fmt3(x: float) -> str:
    return f"{abs(x):,.3f}" + ("-" if x < 0 else "")


def in092u_bytes(
    date: str = "11/06/26",
    time: str = " 9:48:14",
    rows: list[tuple[str, str, str]] | None = None,
    excludes_zero: bool = True,
    group_qty_override: dict[str, str] | None = None,
    with_group_totals: bool = True,
) -> bytes:
    """IN092U sintético: fecha y hora separadas por página, filas sin stockroom y total por
    stockroom al final de cada grupo (cantidad y valor en las columnas de las filas)."""
    title = f"{'IN092U':<11}STOCKROOM STATUS - DETAIL{' ' * 17}INVENTORY STATUS REPORT"
    head = [
        f"{title:<104}Date: {date}    Page: {1:>4}",
        f"{'':<104}Time: {time:>8}",
        f"{'Order: ITM ORDER':<100}Excludes Zero Physical Quantity"
        if excludes_zero
        else "Order: ITM ORDER",
        "Item From: ALL ITEM CLASS                       Stockroom: FG - FINISHED GOODS",
        "Item            Description                          Loc   Flg  Grp   Sub   "
        "Physical Quantity       Unit Cost              Value",
        "-" * 15
        + " "
        + "-" * 36
        + " "
        + "-" * 5
        + " "
        + "-" * 4
        + " "
        + "-" * 5
        + " "
        + "-" * 5
        + " "
        + "-" * 16
        + " "
        + "-" * 18
        + "  "
        + "-" * 17,
    ]
    lines = list(head)
    rows = rows if rows is not None else IN_ROWS
    groups: list[tuple[str, list[tuple[str, str, str]]]] = []
    for r in rows:
        if groups and groups[-1][0] == r[1]:
            groups[-1][1].append(r)
        else:
            groups.append((r[1], [r]))
    for room, members in groups:
        for item, _room, qty in members:
            lines.append(in092u_row(item, qty))
        if with_group_totals:
            total = sum(_dec(q) for _, _, q in members)
            qty = (group_qty_override or {}).get(room, _fmt3(total))
            value = f"{1250 * len(members):,.2f}"
            lines.append(in092u_group_total(room, qty, value))
    lines.append(f"{'':<50}*** END OF REPORT ***")
    return ("\r\n".join(lines) + "\r\n").encode("cp850")


def november_rules() -> dict[str, Any]:
    rules = json.loads(json.dumps(demo.RULES))
    rules["version"] = "core-synthetic-nov-1"
    rules["customer_classes"] = {
        "CAT": [CAT],
        "CAT_REVIEW": [REVIEW],
        "TUPY": [TUPY],
        "APTIV": [APTIV],
        "DELPHI": [DELPHI],
    }
    rules["preserve_classes"] = ["TUPY", "CAT_REVIEW"]
    rules["customer_notes"] = {REVIEW: "pertenencia ambigua (sintético)"}
    return rules


def lab(root: Path, tz: str = "America/Mexico_City") -> dict[str, Path]:
    """Directorio de datos privado + carpeta de origen con los tres archivos de noviembre."""
    data, src = root / "replay-2026-11", root / "inputs" / "2026-11"
    data.mkdir(parents=True)
    src.mkdir(parents=True)
    demo.write_lf(data / "rules.json", json.dumps(november_rules(), indent=1))
    demo.write_lf(data / "users.json", json.dumps(demo.USERS, indent=1))
    demo.write_lf(
        data / "operation.json",
        json.dumps({"schema": "nexus.core.operation/1", "business_tz": tz}),
    )
    (src / "R_20261101.TXT").write_bytes(oe965_bytes())
    (src / "Portal_CAT_2026-11-03_0955.csv").write_bytes(portal_bytes())
    (src / "INV_STA289.TXT").write_bytes(in092u_bytes())
    homologation = root / "private" / "homologacion.csv"
    homologation.parent.mkdir()
    homologation.write_bytes(HOMOLOGATION.encode("utf-8"))
    return {"data": data, "src": src, "homologation": homologation}
