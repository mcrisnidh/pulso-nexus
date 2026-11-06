"""Dataset SINTÉTICO con la estructura del layout pilot20_v1 (ITEM, clientes y WO ficticios).

Cubre los casos funcionales del piloto sin datos reales:

- SYN-A: CAT + TUPY + cliente OTRO; Portal con estado excluido, Shipped vacío, ``8000(0)``,
  DIF ≤ 0 e incluido; WO con 3 operaciones y actividad en la última (candidata), corrección y
  ajuste tardío ya representados; una operación con actividad ausente del routing actual.
- SYN-B: FG disponible negativo en el corte Frozen; WO RW sin actividad en la última operación.
- SYN-C: solo demanda; no figura en el inventario posterior (desconocido, no cero); sin WO.
- SYN-D: cantidad abierta no numérica (desconocida) y cliente de revisión (ambiguo).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from nexus.planning.runner import default_layout

ITEMS = ["SYN-A", "SYN-B", "SYN-C", "SYN-D"]
CAT, TUPY, OTHER, REVIEW = "CATX      001", "TUPX      000", "OTHR      001", "AMBG      000"
SNAP1, SNAP2 = "R_20590101.TXT", "R_20590105.TXT"

RULES: dict[str, Any] = {
    "schema": "nexus.planning.rules/1",
    "version": "synthetic-rules-1",
    "approval": "PROVISIONAL_NO_CRC",
    "snapshot_name_pattern": "R_(?P<date>\\d{8})",
    "portal_excluded_statuses": ["Cancelled", "Completed", "Rejected"],
    "usable_fg_stockrooms": ["FG"],
    "customer_classes": {"CAT": [CAT], "CAT_REVIEW": [REVIEW], "TUPY": [TUPY]},
    "substitute_class": "CAT",
    "preserve_classes": ["TUPY", "CAT_REVIEW"],
    "customer_notes": {REVIEW: "pertenencia ambigua (sintético)"},
}


def _bl(
    snap: str, item: str, cust: str, order: str, line: str, due: str, qty: str
) -> dict[str, str]:
    return {
        "source_file": snap,
        "source_sha256": "0" * 64,
        "source_line": "1",
        "item": item,
        "order": order,
        "line": line,
        "date_due_raw": due,
        "qty_order": qty,
        "qty_open": qty,
        "qty_alloc": "0",
        "customer_key_exact": cust,
        "customer_name_sales_raw": "SYNTH",
    }


BACKLOG = [
    _bl(SNAP1, "SYN-A", CAT, "000101", "1", "01/10/59", "100"),
    _bl(SNAP1, "SYN-A", CAT, "000101", "2", "12/20/58", "50"),  # atrasada: entra
    _bl(SNAP1, "SYN-A", TUPY, "000102", "1", "01/15/59", "30"),
    _bl(SNAP1, "SYN-A", OTHER, "000103", "1", "01/20/59", "20"),
    _bl(SNAP1, "SYN-A", CAT, "000104", "1", "02/10/59", "999"),  # fuera del mes: no entra
    _bl(SNAP2, "SYN-A", CAT, "000101", "1", "01/10/59", "80"),
    _bl(SNAP2, "SYN-A", TUPY, "000102", "1", "01/15/59", "30"),
    _bl(SNAP1, "SYN-B", OTHER, "000201", "1", "01/05/59", "40"),
    _bl(SNAP2, "SYN-B", OTHER, "000201", "1", "01/05/59", "40"),
    _bl(SNAP1, "SYN-C", OTHER, "000301", "1", "01/25/59", "7"),
    _bl(SNAP1, "SYN-D", REVIEW, "000401", "1", "01/12/59", "N/A"),
]

FG = [
    {
        "source_file": s,
        "source_sha256": "0" * 64,
        "source_line": "1",
        "item": i,
        "physical_stock": p,
        "available_stock": a,
    }
    for s, i, p, a in [
        (SNAP1, "SYN-A", "60", "60"),
        (SNAP2, "SYN-A", "500", "500"),
        (SNAP1, "SYN-B", "5", "-10"),
        (SNAP2, "SYN-B", "5", "5"),
        (SNAP1, "SYN-C", "0", "0"),
        (SNAP2, "SYN-C", "0", "0"),
        (SNAP1, "SYN-D", "1", "1"),
        (SNAP2, "SYN-D", "1", "1"),
    ]
]


def _portal(line: str, status: str, ordered: str, shipped: str, decision: str) -> dict[str, str]:
    return {
        "ITEM homologado": "SYN-A",
        "ITEM portal original": "SYN A",
        "Line ID": line,
        "Status": status,
        "Ordered raw": ordered,
        "Shipped raw": shipped,
        "Ship date": "01/09/2059",
        "Delivery date": "01/12/2059",
        "Map status": "MAPPED",
        "Decisión de línea": decision,
    }


PORTAL = [
    _portal("L1", "Open", "120", "20", "INCLUIDO"),
    _portal("L2", "Cancelled", "50", "0", "EXCLUIDO_ESTADO"),
    _portal("L3", "Open", "40", "", "NO_CUANTIFICABLE"),
    _portal("L4", "Open", "8000(0)", "0", "NO_CUANTIFICABLE"),
    _portal("L5", "Open", "10", "10", "EXCLUIDO_DIF"),
]

BILLING = [
    {
        "ITEM": "SYN-A",
        "Fecha factura": "2059-01-03",
        "Cliente exacto": CAT,
        "Nombre cliente": "SYNTH",
        "Documento": "D1",
        "Serie": "F",
        "UOM": "EA",
        "Qty firmada": "25",
    },
    {
        "ITEM": "SYN-A",
        "Fecha factura": "2059-01-04",
        "Cliente exacto": CAT,
        "Nombre cliente": "SYNTH",
        "Documento": "D2",
        "Serie": "G",
        "UOM": "EA",
        "Qty firmada": "-5",
    },
]

INVENTORY = [
    {"ITEM": i, "Stockroom": s, "Cantidad física": q, "Corte de inventario": "01-feb-2059 10:00:00"}
    for i, s, q in [
        ("SYN-A", "FG", "70"),
        ("SYN-A", "QF", "9"),
        ("SYN-B", "SA", "4"),
        ("SYN-D", "FG", "2"),
    ]
]


def _wo(order: str, item: str, otype: str, status: str) -> dict[str, str]:
    return {
        "snapshot_date": "2059-02-05",
        "company_cd": "99",
        "order_no": order,
        "order_type": otype,
        "part_number": item,
        "order_status": status,
        "order_qty": "100",
        "cust_code": "",
        "act_date": "NULL",
    }


WO = [_wo("W0001", "SYN-A", "MF", ""), _wo("W0002", "SYN-B", "RW", "C")]


def _route(order: str, item: str, seq: int) -> dict[str, str]:
    return {
        "snapshot_date": "2059-02-05",
        "company_cd": "99",
        "order_no": order,
        "part_number": item,
        "seq_numb": str(seq),
        "wc_id": f"WC{seq}",
        "oper_cd": f"OP{seq}",
        "oper_status": "P",
        "pln_qty": "100",
        "pln_hrs": "2.5",
        "set_std": "0.5",
        "mch_std": "0.01",
        "e_or_s_std": "E",
        "time_basis_cd": "4",
        "act_qty": "",
        "act_hrs": "",
        "scrap_qty": "",
    }


ROUTING = [_route("W0001", "SYN-A", s) for s in (10, 20, 30)] + [
    _route("W0002", "SYN-B", s) for s in (10, 20)
]


def _labor(
    order: str, item: str, seq: int, rtype: str, qty: str, late: str = "0"
) -> dict[str, str]:
    return {
        "Item": item,
        "Company": "99",
        "WorkOrder": order,
        "OperationSequence": str(seq),
        "WorkCenter": f"WC{seq}",
        "RecordType": rtype,
        "OperationStatus": "P",
        "TransactionRows": "2",
        "OctoberOperationQty": qty,
        "OctoberScrapQty": "1",
        "QtyPostedByOct31": qty,
        "LatePostingRows": late,
        "FirstActivity": "2059-01-02 00:00:00.000",
        "LastActivity": "2059-01-30 00:00:00.000",
    }


LABOR = [
    _labor("W0001", "SYN-A", 10, "D", "90"),
    _labor("W0001", "SYN-A", 20, "D", "85"),
    _labor("W0001", "SYN-A", 30, "D", "80"),
    _labor("W0001", "SYN-A", 30, "F", "0"),
    _labor("W0001", "SYN-A", 25, "D", "5"),  # operación ausente del routing actual
    _labor("W0002", "SYN-B", 10, "D", "15"),
]

CORRECTIONS = [
    {
        "CorrID": "C1",
        "Item": "SYN-A",
        "WorkOrder": "W0001",
        "OperationSeq": "20",
        "ActivityDate": "2059-01-08",
        "CorrectionDate": "2059-01-08 17:00:00.000",
        "AddedQty": "10",
        "AddedScrap": "0",
        "CorrectionType": "A",
        "MatchedLabor": "1",
        "MatchedAdjustments": "1",
    },
]

LATE = [
    {
        "Item": "SYN-A",
        "Company": "99",
        "WorkOrder": "W0001",
        "OperationSeq": "30",
        "RecordType": "D",
        "ActivityDate": "2059-01-31 00:00:00.000",
        "AdjustmentTransactionDate": "2059-02-01 00:10:00.000",
        "ActivityQty": "12",
        "ScrapQty": "0",
        "ReversalFlag": "N",
    },
]

ROUTE_END = [
    {
        "Item": "SYN-A",
        "Company": "99",
        "WorkOrder": "W0001",
        "CurrentLastSeqCandidate": "30",
        "OctDQtyAtCurrentLastSeq": "80",
        "Status": "OCT_ACTIVITY_AT_ROUTE_END_DIAGNOSTIC",
    },
    {
        "Item": "SYN-B",
        "Company": "99",
        "WorkOrder": "W0002",
        "CurrentLastSeqCandidate": "20",
        "OctDQtyAtCurrentLastSeq": "0",
        "Status": "NO_OCT_ACTIVITY_AT_ROUTE_END",
    },
]

UNIVERSE = [
    {
        "ITEM": "SYN-A",
        "OE965_order_rows_all_snapshots": "7",
        "Portal_rows_provisional": "5",
        "Billing_movements": "2",
        "FG_20261101_positive_records": "1",
        "WO_current_union": "1",
        "WO_with_October_activity": "1",
        "October_operation_aggregate_rows": "5",
        "WO_with_terminal_candidate_activity": "1",
    },
    {
        "ITEM": "SYN-B",
        "OE965_order_rows_all_snapshots": "2",
        "Portal_rows_provisional": "0",
        "Billing_movements": "0",
        "FG_20261101_positive_records": "0",
        "WO_current_union": "1",
        "WO_with_October_activity": "1",
        "October_operation_aggregate_rows": "1",
        "WO_with_terminal_candidate_activity": "0",
    },
    {"ITEM": "SYN-C"},
    {"ITEM": "SYN-D"},
]


def _write(
    path: Path, rows: list[dict[str, str]], delimiter: str = ",", header: list[str] | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = header or list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=fields, delimiter=delimiter, lineterminator="\n", restval=""
        )
        writer.writeheader()
        writer.writerows(rows)


def build(root: Path) -> tuple[Path, Path]:
    """Escribe el dataset sintético y sus reglas; devuelve (directorio, archivo de reglas)."""
    layout = json.loads(default_layout().read_text("utf-8"))["sources"]
    data: dict[str, tuple[list[dict[str, str]], str]] = {
        "oe965_backlog": (BACKLOG, ","),
        "oe965_fg": (FG, ","),
        "portal_cat": (PORTAL, ","),
        "billing": (BILLING, ","),
        "inventory_snapshot": (INVENTORY, ","),
        "wo_header": (WO, ","),
        "wo_blank_status": ([{"company_cd": "99", "order_no": "W0001"}], ","),
        "wo_oct_activity": (
            [{"company_cd": "99", "order_no": "W0001"}, {"company_cd": "99", "order_no": "W0002"}],
            ",",
        ),
        "routing": (ROUTING, ","),
        "labor_groups": (LABOR, "\t"),
        "labor_corrections": (CORRECTIONS, ","),
        "late_adjustments": (LATE, ","),
        "item_universe": (UNIVERSE, ","),
        "route_end_diagnostic": (ROUTE_END, ","),
        "selection": ([{"ITEM": i} for i in ITEMS], ","),
    }
    dataset = root / "dataset"
    for sid, (rows, delim) in data.items():
        header = list(UNIVERSE[0]) if sid == "item_universe" else None
        _write(dataset / layout[sid]["path"], rows, delim, header)
    rules = root / "rules.json"
    rules.write_text(json.dumps(RULES, ensure_ascii=False, indent=1), "utf-8")
    return dataset, rules
