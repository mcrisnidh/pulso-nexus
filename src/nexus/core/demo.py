"""Universo SINTÉTICO de dos meses para la demostración y las pruebas del Hito A.

ITEM, clientes, pedidos y sistemas son ficticios (``SYN-*``, ``SYNCAT``…). ``source_system`` es
siempre ``SYNTHETIC``: el canal operativo los rechaza por construcción.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from nexus.core.sources import layout

CAT, TUPY, OTHER, REVIEW = "SYNCAT    001", "SYNTUP    000", "SYNOTH    001", "SYNREV    000"
SYS = "SYNTHETIC"

RULES: dict[str, Any] = {
    "schema": "nexus.core.rules/1",
    "version": "core-synthetic-1",
    "approval": "PROVISIONAL_NO_CRC",
    "snapshot_name_pattern": "R_(?P<date>\\d{8})",
    "portal_excluded_statuses": ["Cancelled", "Completed", "Rejected"],
    "usable_fg_stockrooms": ["FG"],
    "customer_classes": {"CAT": [CAT], "CAT_REVIEW": [REVIEW], "TUPY": [TUPY]},
    "substitute_class": "CAT",
    "preserve_classes": ["TUPY", "CAT_REVIEW"],
    "customer_notes": {REVIEW: "pertenencia ambigua (sintético)"},
    "portal_date": {"field": "delivery_date", "approval": "PENDING_COMMERCIAL_CONTRACT"},
    "allocation": {"method": "due_date_fifo", "approval": "PROVISIONAL"},
    "fg_subtract_reserved": False,
    "cs_status_vocabulary": ["EN_TIEMPO", "EN_RIESGO", "ATRASADO", "ESPERA_CLIENTE"],
    "certified_sources": [],
}

USERS = {
    "lector": {"password": "lector-demo", "roles": ["reader"]},
    "editorcs": {"password": "editorcs-demo", "roles": ["reader", "cs_editor"]},
    "editorcs2": {"password": "editorcs2-demo", "roles": ["reader", "cs_editor"]},
    "refresher": {"password": "refresher-demo", "roles": ["reader", "refresher"]},
    "admin": {"password": "admin-demo", "roles": ["reader", "cs_editor", "refresher", "admin"]},
}


def _csv(source_id: str, rows: list[dict[str, str]]) -> bytes:
    cols = layout()[source_id]["required_columns"]
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def oeras(ts: str, rows: list[tuple[str, str, str, str, str, str]]) -> bytes:
    """rows: (item, customer, order, line, due_date, qty_open)."""
    return _csv(
        "oeras_29",
        [
            {
                "extraction_ts": ts,
                "source_system": SYS,
                "report_id": "SYN-29OERAS",
                "item": i,
                "customer_key_exact": c,
                "customer_name": "SINTETICO",
                "order": o,
                "line": ln,
                "due_date": d,
                "qty_ordered": q,
                "qty_open": q,
            }
            for i, c, o, ln, d, q in rows
        ],
    )


def portal(ts: str, rows: list[tuple[str, str, str, str, str, str]]) -> bytes:
    """rows: (item, line_id, status, ordered_raw, shipped_raw, delivery_date)."""
    return _csv(
        "portal_cat",
        [
            {
                "capture_ts": ts,
                "source_system": SYS,
                "item": i,
                "item_portal": i.replace("-", " "),
                "line_id": lid,
                "status": st,
                "ordered_raw": o,
                "shipped_raw": sh,
                "ship_date": d,
                "delivery_date": d,
            }
            for i, lid, st, o, sh, d in rows
        ],
    )


def fg(ts: str, rows: list[tuple[str, str, str]]) -> bytes:
    return _csv(
        "fg_inventory",
        [
            {
                "cut_ts": ts,
                "source_system": SYS,
                "item": i,
                "stockroom": r,
                "qty_physical": q,
                "qty_reserved": "",
            }
            for i, r, q in rows
        ],
    )


def shipments(ts: str, rows: list[tuple[str, str, str, str, str, str, str, str]]) -> bytes:
    """rows: (movement_id, item, customer, order, line, qty, movement_ts, type[:reverses])."""
    out = []
    for mid, i, c, o, ln, q, mts, typ in rows:
        kind, _, rev = typ.partition(":")
        out.append(
            {
                "cut_ts": ts,
                "source_system": SYS,
                "movement_id": mid,
                "item": i,
                "customer_key_exact": c,
                "order": o,
                "line": ln,
                "qty": q,
                "movement_ts": mts,
                "movement_type": kind,
                "reverses_movement_id": rev,
                "uom": "EA",
            }
        )
    return _csv("shipments", out)


# ------------------------------------------------------------------ escenario de dos meses
M1, M2 = "2031-01", "2031-02"
M1_ORDERS = [
    ("SYN-A", TUPY, "000101", "1", "2031-01-10", "300"),
    ("SYN-A", CAT, "000102", "1", "2031-01-15", "999"),  # CAT: sustituido por el Portal
    ("SYN-A", OTHER, "000103", "1", "2030-12-20", "50"),  # vencida: entra
    ("SYN-A", OTHER, "000104", "1", "2031-03-05", "700"),  # futura: solo vista
    ("SYN-B", OTHER, "000201", "1", "2031-01-20", "120"),
    ("SYN-B", REVIEW, "000202", "1", "2031-01-25", "40"),  # en revisión: se conserva
    ("SYN-C", OTHER, "000301", "1", "2031-01-28", "80"),
    ("SYN-D", CAT, "000401", "1", "2031-01-18", "60"),  # CAT sin Portal: no determinable
]
M1_PORTAL = [
    ("SYN-A", "PA-1", "Open", "400", "100", "2031-01-20"),
    ("SYN-A", "PA-2", "Cancelled", "50", "0", "2031-01-21"),
    ("SYN-A", "PA-3", "Open", "200", "", "2031-02-15"),  # futura (otro mes): solo vista
    ("SYN-E", "PE-1", "Open", "8000(0)", "0", "2031-01-22"),  # desconocida: no cero
]


def scenario(root: Path) -> dict[str, Any]:
    """Escribe reglas, usuarios y la bandeja del MES 1 (los siguientes pasos los aplica la
    demostración). Devuelve las rutas."""
    inbox = root / "inbox"
    for sid in ("oeras_29", "portal_cat", "fg_inventory", "shipments"):
        (inbox / sid).mkdir(parents=True, exist_ok=True)
    (root / "rules.json").write_text(json.dumps(RULES, indent=1, ensure_ascii=False), "utf-8")
    (root / "users.json").write_text(json.dumps(USERS, indent=1), "utf-8")
    drop(root, "oeras_29", "R_20310101_0600.csv", oeras("2031-01-01T06:00:00Z", M1_ORDERS))
    drop(root, "portal_cat", "P_20310102_1055.csv", portal("2031-01-02T10:55:00Z", M1_PORTAL))
    drop(
        root,
        "fg_inventory",
        "FG_20310110_1800.csv",
        fg(
            "2031-01-10T18:00:00Z",
            [
                ("SYN-A", "FG", "150"),
                ("SYN-A", "QF", "40"),
                ("SYN-B", "FG", "-5"),
                ("SYN-C", "FG", "30"),
                ("SYN-C", "SA", "10"),
            ],
        ),
    )
    drop(
        root,
        "shipments",
        "S_20310110_1800.csv",
        shipments(
            "2031-01-10T18:00:00Z",
            [
                ("MV-1", "SYN-A", TUPY, "000101", "1", "100", "2031-01-05T09:00:00Z", "SHIP"),
                ("MV-2", "SYN-A", CAT, "", "", "120", "2031-01-06T09:00:00Z", "SHIP"),
                ("MV-3", "SYN-A", OTHER, "000103", "", "20", "2031-01-07T09:00:00Z", "SHIP"),
                ("MV-4", "SYN-C", OTHER, "000301", "1", "30", "2031-01-08T09:00:00Z", "SHIP"),
                (
                    "MV-5",
                    "SYN-C",
                    OTHER,
                    "000301",
                    "1",
                    "10",
                    "2031-01-09T09:00:00Z",
                    "REVERSAL:MV-4",
                ),
                ("MV-6", "SYN-B", OTHER, "999999", "1", "500", "2031-01-09T10:00:00Z", "SHIP"),
                ("MV-7", "SYN-A", TUPY, "000101", "1", "5", "2030-12-30T09:00:00Z", "SHIP"),
            ],
        ),
    )
    return {
        "root": root,
        "inbox": inbox,
        "rules": root / "rules.json",
        "users": root / "users.json",
    }


def drop(root: Path, source_id: str, name: str, content: bytes) -> Path:
    path = root / "inbox" / source_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# ------------------------------------------------------------------ pasos posteriores del escenario
def current_m1(root: Path) -> None:
    """Extracto Current posterior (cambia cantidades y agrega una línea): el Frozen no cambia."""
    rows = [
        *(r for r in M1_ORDERS if r[0] != "SYN-C"),
        ("SYN-C", OTHER, "000301", "1", "2031-01-28", "95"),
        ("SYN-C", OTHER, "000303", "1", "2031-01-30", "15"),
    ]
    drop(root, "oeras_29", "R_20310105_0600.csv", oeras("2031-01-05T06:00:00Z", rows))


def late_earlier_extract(root: Path) -> None:
    """Llega TARDE un extracto válido con instante ANTERIOR al Frozen registrado."""
    drop(root, "oeras_29", "R_20310101_0500.csv", oeras("2031-01-01T05:00:00Z", M1_ORDERS[:3]))


def invalid_extract(root: Path) -> None:
    """Archivo inválido (columna faltante): nunca puede ser Frozen ni Current."""
    path = root / "inbox" / "oeras_29" / "R_20310103_0600.csv"
    path.write_text("extraction_ts,item\n2031-01-03T06:00:00Z,SYN-A\n", "utf-8")


def new_cut_m1(root: Path) -> None:
    """Nuevo corte FG + embarques (mismo instante) dentro del mes 1."""
    drop(
        root,
        "fg_inventory",
        "FG_20310120_1800.csv",
        fg(
            "2031-01-20T18:00:00Z",
            [
                ("SYN-A", "FG", "90"),
                ("SYN-A", "QF", "40"),
                ("SYN-B", "FG", "25"),
                ("SYN-C", "FG", "30"),
            ],
        ),
    )
    drop(
        root,
        "shipments",
        "S_20310120_1800.csv",
        shipments(
            "2031-01-20T18:00:00Z",
            [
                ("MV-1", "SYN-A", TUPY, "000101", "1", "100", "2031-01-05T09:00:00Z", "SHIP"),
                ("MV-2", "SYN-A", CAT, "", "", "120", "2031-01-06T09:00:00Z", "SHIP"),
                ("MV-3", "SYN-A", OTHER, "000103", "", "20", "2031-01-07T09:00:00Z", "SHIP"),
                ("MV-4", "SYN-C", OTHER, "000301", "1", "30", "2031-01-08T09:00:00Z", "SHIP"),
                (
                    "MV-5",
                    "SYN-C",
                    OTHER,
                    "000301",
                    "1",
                    "10",
                    "2031-01-09T09:00:00Z",
                    "REVERSAL:MV-4",
                ),
                ("MV-6", "SYN-B", OTHER, "999999", "1", "500", "2031-01-09T10:00:00Z", "SHIP"),
                ("MV-8", "SYN-A", TUPY, "000101", "1", "150", "2031-01-15T09:00:00Z", "SHIP"),
                ("MV-9", "SYN-A", CAT, "", "", "100", "2031-01-18T09:00:00Z", "SHIP"),
            ],
        ),
    )


def broken_cut_m1(root: Path) -> None:
    """Corte FG sin embarques del mismo instante: el Refresh no debe publicar."""
    drop(
        root,
        "fg_inventory",
        "FG_20310125_1800.csv",
        fg("2031-01-25T18:00:00Z", [("SYN-A", "FG", "10")]),
    )


M2_ORDERS = [
    ("SYN-A", TUPY, "000105", "1", "2031-02-12", "200"),
    ("SYN-A", OTHER, "000104", "1", "2031-03-05", "700"),
    ("SYN-B", OTHER, "000203", "1", "2031-02-14", "90"),
    ("SYN-C", OTHER, "000302", "1", "2031-02-20", "60"),
]


def month2(root: Path) -> None:
    drop(root, "oeras_29", "R_20310201_0600.csv", oeras("2031-02-01T06:00:00Z", M2_ORDERS))
    drop(
        root,
        "portal_cat",
        "P_20310202_1000.csv",
        portal("2031-02-02T10:00:00Z", [("SYN-A", "PA-3", "Open", "200", "0", "2031-02-15")]),
    )
    drop(
        root,
        "fg_inventory",
        "FG_20310208_1800.csv",
        fg(
            "2031-02-08T18:00:00Z",
            [("SYN-A", "FG", "100"), ("SYN-B", "FG", "50"), ("SYN-C", "FG", "0")],
        ),
    )
    drop(
        root,
        "shipments",
        "S_20310208_1800.csv",
        shipments(
            "2031-02-08T18:00:00Z",
            [
                ("MV-20", "SYN-A", TUPY, "000105", "1", "150", "2031-02-05T09:00:00Z", "SHIP"),
                ("MV-21", "SYN-A", CAT, "", "", "50", "2031-02-06T09:00:00Z", "SHIP"),
            ],
        ),
    )
