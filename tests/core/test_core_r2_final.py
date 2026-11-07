"""R2 final — segundo probe real (ASMXC1200, noviembre 2026): IN092U y Portal CAT.

Todo es SINTÉTICO (prefijo ``QZX``). Reproduce la ESTRUCTURA confirmada por el probe y la
conciliación independiente del equipo, sin ningún valor real:

- IN092U: 553 filas en 5 grupos — FG 341, QF 1, QH 6, RM 188, SA 17 —, cada grupo conciliado
  EXACTAMENTE en cantidad y valor. Siete patrones antes rechazados:
  4 ITEM FG con espacios internos y 3 cifras RM con decimal sin entero (``.999``).
- Portal: encabezado de 12 columnas con ``Item ID`` en la columna 2 y 257 filas.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import physical_fixtures as fx
import pytest

from nexus.core.physical import default_layout, read_in092u, read_portal
from nexus.core.physical.common import parse_qty
from nexus.core.physical.in092u import _data_row
from nexus.core.stage import probe

L = default_layout()
GROUPS = {"FG": 341, "QF": 1, "QH": 6, "RM": 188, "SA": 17}
SPACED_ITEMS = ["QZX 100 A", "QZX 100  A", "QZX A 7 B", "QZX-9 X 1234"]  # 4 filas FG
LEADING_POINT = [  # 3 filas RM: (qty, valor)
    (".999", "12.50"),
    ("4.000", ".75"),
    (".500", ".25"),
]


def codes(r: object) -> set[str]:
    return {e.code for e in r.exceptions}  # type: ignore[attr-defined]


def november_rows() -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    for room, count in GROUPS.items():
        for n in range(count):
            qty = f"{(n % 97) + 1:,}.{n % 1000:03d}"
            value = f"{(n % 89) + 3:,}.{n % 100:02d}"
            if n % 53 == 7:
                qty += "-"  # negativo con signo al final (se conserva)
            rows.append((f"QZX{room}{n:05d}", room, qty, value))
    fg = [i for i, r in enumerate(rows) if r[1] == "FG"]
    for k, item in enumerate(SPACED_ITEMS):
        r = rows[fg[10 + k * 40]]
        rows[fg[10 + k * 40]] = (item, *r[1:])
    rm = [i for i, r in enumerate(rows) if r[1] == "RM"]
    for k, (qty, value) in enumerate(LEADING_POINT):
        r = rows[rm[20 + k * 60]]
        rows[rm[20 + k * 60]] = (r[0], r[1], qty, value)
    return rows


def november_in092u(**kw: object) -> bytes:
    return fx.in092u_bytes(rows=november_rows(), page_size=45, **kw)  # type: ignore[arg-type]


# ------------------------------------------------------------------ IN092U: conciliación completa
def test_rf01_553_rows_five_groups_reconcile_exactly_in_qty_and_value() -> None:
    r = read_in092u(november_in092u(), L, "America/Mexico_City")
    assert r.valid, r.exception_summary()
    rec = r.flags["reconciliation"]
    assert rec["data_rows"] == rec["rows_assigned_to_stockroom"] == len(r.records) == 553
    assert {g["stockroom"]: g["rows"] for g in rec["groups"]} == GROUPS
    assert all(g["qty_total_match"] and g["value_total_match"] for g in rec["groups"])
    assert r.classes.get("unclassified", 0) == 0
    assert r.classes["cut_time"] == 13  # 553 filas a 45 por página: 13 páginas
    assert r.document_time["value"] == "2026-11-06T15:48:14Z"


def test_rf02_internal_spaces_preserved_literally_never_merged() -> None:
    r = read_in092u(november_in092u(), L, "UTC")
    items = {x["item"] for x in r.records if x["stockroom"] == "FG"}
    for item in SPACED_ITEMS:
        assert item in items, item  # literal, con sus espacios internos
    assert "QZX 100 A" in items and "QZX 100  A" in items  # dos identidades DISTINTAS
    assert "QZX100A" not in items  # nunca se eliminan espacios
    spaced = [e for e in r.exceptions if e.code == "ITEM_INTERNAL_SPACES_PRESERVED"]
    assert len(spaced) == 4 and all(e.severity == "WARNING" for e in spaced)


def test_rf03_leading_point_decimals_are_exact_values() -> None:
    r = read_in092u(november_in092u(), L, "UTC")
    rm = {x["qty_physical"] for x in r.records if x["stockroom"] == "RM"}
    assert {"0.999", "4.000", "0.500"} <= rm
    assert parse_qty(".999") == Decimal("0.999") and parse_qty(".75") == Decimal("0.75")
    assert parse_qty(".500-") == Decimal("-0.500") and parse_qty("-.250") == Decimal("-0.250")


def test_rf04_negatives_kept_and_qh_rm_present_but_not_fg() -> None:
    r = read_in092u(november_in092u(), L, "UTC")
    assert any(x["qty_physical"].startswith("-") for x in r.records)
    rooms = {x["stockroom"] for x in r.records}
    assert rooms == set(GROUPS)  # QH/RM se conservan; la regla CORE usa solo FG


@pytest.mark.parametrize(
    "token", ["..5", "1.2.3", ".", "1,23.000", "-.5-", "1.", "1,2345.0", ",123.0", "1 .5", ".5.5"]
)
def test_rf05_malformed_decimals_are_rejected(token: str) -> None:
    assert parse_qty(token) is None
    assert _data_row(fx.in092u_row("QZXB", token), L["in092u"]) is None  # fila rechazada
    rows = [("QZXA", "FG", "1.000"), ("QZXB", "FG", token)]
    data = fx.in092u_bytes(rows=rows, group_qty_override={"FG": "2.000"})
    r = read_in092u(data, L, "UTC")
    assert not r.valid  # la fila mal formada no se acepta y el grupo no cuadra
    assert {"UNCLASSIFIED_LINE", "GROUP_QTY_TOTAL_MISMATCH"} & codes(r)


def test_rf06_zero_unknown_and_invalid_are_distinct() -> None:
    zero = read_in092u(fx.in092u_bytes(rows=[("QZXZ", "FG", ".000")]), L, "UTC")
    assert zero.valid and zero.records[0]["qty_physical"] == "0.000"  # cero real impreso
    assert "QTY_ZERO_DESPITE_EXCLUSION" in codes(zero)
    missing = fx.in092u_row("QZXM", "1.000").replace("     1.000", "          ", 1)
    text = fx.in092u_bytes(rows=[("QZXA", "FG", "1.000")]).decode("cp850")
    text = text.replace(
        fx.in092u_row("QZXA", "1.000"), missing + "\r\n" + fx.in092u_row("QZXA", "1.000")
    )
    r = read_in092u(text.encode("cp850"), L, "UTC")
    assert not r.valid and "UNCLASSIFIED_LINE" in codes(r)  # sin cantidad: nunca 0
    assert all(x["item"] != "QZXM" for x in r.records)


@pytest.mark.parametrize(
    "bad_item",
    [
        "QZX-ITEM-TOO-LONG",  # invade la columna separadora 15
        "QZX\tTAB",  # tabulador dentro del ITEM
        " QZXLEADING",  # no empieza en la columna 0
    ],
)
def test_rf07_ambiguous_item_fields_are_rejected(bad_item: str) -> None:
    line = fx.in092u_row("PLACEHOLDER", "1.000")
    line = (
        (bad_item + line[len(bad_item) :])
        if len(bad_item) > 15
        else (f"{bad_item:<15}" + line[15:])
    )
    assert _data_row(line, L["in092u"]) is None


def test_rf08_exact_duplicates_are_blocking() -> None:
    rows = [("QZX 1 A", "FG", "1.000"), ("QZX 1 A", "FG", "1.000")]
    r = read_in092u(fx.in092u_bytes(rows=rows), L, "UTC")
    assert not r.valid and "DUPLICATE_ITEM_STOCKROOM" in codes(r)


@pytest.mark.parametrize("kind", ["qty", "value"])
def test_rf09_altered_group_total_is_blocking(kind: str) -> None:
    rows = november_rows()
    rm_qty = sum((fx._dec(r[2]) for r in rows if r[1] == "RM"), Decimal(0))
    rm_val = sum((fx._dec(r[3]) for r in rows if r[1] == "RM"), Decimal(0))
    override = (
        {"group_qty_override": {"RM": fx._fmt3(rm_qty + Decimal("0.001"))}}
        if kind == "qty"
        else {"group_value_override": {"RM": fx._fmt2(rm_val + Decimal("0.01"))}}
    )
    r = read_in092u(november_in092u(**override), L, "UTC")
    code = "GROUP_QTY_TOTAL_MISMATCH" if kind == "qty" else "GROUP_VALUE_TOTAL_MISMATCH"
    hits = [e for e in r.exceptions if e.code == code]
    assert not r.valid and len(hits) == 1 and hits[0].severity == "BLOCKING"
    rm = next(g for g in r.flags["reconciliation"]["groups"] if g["stockroom"] == "RM")
    assert rm[f"{kind}_total_match"] is False


@pytest.mark.parametrize("shift", [-1, 1])
def test_rf10_column_shift_of_leading_point_value_is_rejected(shift: int) -> None:
    line = fx.in092u_row("QZXR", ".999", ".75")
    moved = line.replace(" .999", ".999 ", 1) if shift < 0 else line.replace(" .999 ", "  .999", 1)
    assert moved != line and _data_row(moved, L["in092u"]) is None
    assert _data_row(line, L["in092u"]) == ("QZXR", Decimal("0.999"), Decimal("0.75"))


# ------------------------------------------------------------------ Portal: 12 columnas, Item ID
PORTAL12 = [
    "Line ID",
    "PO Number",
    "Item ID",
    "Item Description",
    "Status",
    "Ordered Qty",
    "Shipped Qty",
    "UOM",
    "Ship Date",
    "Delivery Date",
    "Plant",
    "Buyer",
]


def portal12_rows(n: int = 257) -> list[tuple[str, ...]]:
    statuses = [
        "Open",
        "Pending",
        "In Process",
        "Supplier Requested Change",
        "Planned",
        "Cancelled",
    ]
    rows = []
    for i in range(n):
        shipped = "" if i % 29 == 0 else str(i % 5)
        ship = "11/05/2026(11/07/2026)" if i % 31 == 0 else f"11/{(i % 28) + 1}/2026"
        rows.append(
            (
                f"PL-{i:04d}",
                f"QZXPO{i % 17:03d}",
                f"QZX PORTAL {i % 40}",
                "DESCRIPCION SINTETICA",
                statuses[i % len(statuses)],
                str(10 + i),
                shipped,
                "EA",
                ship,
                f"11/{(i % 28) + 2}/2026",
                "P1",
                "B1",
            )
        )
    return rows


def test_rf20_item_id_header_parses_all_257_rows() -> None:
    data = fx.portal_bytes(rows=portal12_rows(), header=PORTAL12, preamble=False)
    r = read_portal(data, L, "Portal_CAT_2026-11-03_0955.csv", {})
    assert r.flags["header_diagnosis"]["missing"] == []
    assert r.flags["header_diagnosis"]["fields"]["item_portal"] == {
        "status": "MATCHED",
        "column": 2,
        "matched_alias": "Item ID",
        "aliases_tried": 5,
    }
    assert r.valid and len(r.records) == r.classes["data_row"] == 257
    assert r.flags["header_columns"] == 12


def test_rf21_item_id_is_portal_identity_not_erp_homologation() -> None:
    data = fx.portal_bytes(rows=portal12_rows(), header=PORTAL12, preamble=False)
    r = read_portal(data, L, "Portal_CAT_2026-11-03_0955.csv", {})
    assert all(x["item"] == "" for x in r.records)  # sin tabla aprobada: sin ITEM ERP
    assert sum(1 for e in r.exceptions if e.code == "UNMAPPED_ITEM") == 257
    mapped = read_portal(data, L, "Portal_CAT_2026-11-03_0955.csv", {"QZX PORTAL 1": "QZXITEM1"})
    assert {x["item"] for x in mapped.records if x["item_portal"] == "QZX PORTAL 1"} == {"QZXITEM1"}
    assert all(x["item"] == "" for x in mapped.records if x["item_portal"] == "QZX PORTAL 11")


def test_rf22_shipped_unknown_ambiguous_dates_and_status_preserved() -> None:
    data = fx.portal_bytes(rows=portal12_rows(), header=PORTAL12, preamble=False)
    r = read_portal(data, L, "Portal_CAT_2026-11-03_0955.csv", {})
    blank = [x for x in r.records if x["shipped_raw"] == ""]
    assert len(blank) == 9  # filas 0, 29, …, 232: desconocido, no 0
    amb = [x for x in r.records if x["ship_date"] == ""]
    assert len(amb) == 9 and "DATE_AMBIGUOUS" in codes(r)
    assert {x["status"] for x in r.records} >= {"Open", "Cancelled", "Supplier Requested Change"}


def test_rf23_short_row_is_explicit_exception() -> None:
    rows = portal12_rows(3)
    rows[1] = rows[1][:10]
    r = read_portal(
        fx.portal_bytes(rows=rows, header=PORTAL12, preamble=False),
        L,
        "Portal_CAT_2026-11-03_0955.csv",
        {},
    )
    assert not r.valid
    shape = [e for e in r.exceptions if e.code == "ROW_SHAPE"]
    assert len(shape) == 1 and shape[0].line == 3


def test_rf24_probe_acceptance_for_the_three_formats(tmp_path: Path) -> None:
    (tmp_path / "INV_STA289.TXT").write_bytes(november_in092u())
    s = probe(tmp_path / "INV_STA289.TXT", business_tz="America/Mexico_City")["structure"]
    assert s["acceptance"]["verdict"] == "PASS" and s["acceptance"]["records_accepted"] == 553
    assert s["line_accounting"]["unclassified_lines"] == 0
    groups = {g["stockroom"]: g for g in s["flags"]["reconciliation"]["groups"]}
    assert {k: g["rows"] for k, g in groups.items()} == GROUPS
    portal = tmp_path / "Portal_CAT_2026-11-03_0955.csv"
    portal.write_bytes(fx.portal_bytes(rows=portal12_rows(), header=PORTAL12, preamble=False))
    p = probe(portal)["structure"]
    assert p["acceptance"]["verdict"] == "PASS" and p["acceptance"]["records_read"] == 257
    assert "QZX PORTAL" not in repr(p) and "QZXRM" not in repr(s)
