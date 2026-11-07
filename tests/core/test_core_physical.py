"""Δ1 — Lectores físicos OE965, Portal CAT e IN092U (fixtures SINTÉTICOS con el formato real)."""

from __future__ import annotations

from pathlib import Path

import physical_fixtures as fx
import pytest

from nexus.core.physical import default_layout, detect_kind, read_in092u, read_oe965, read_portal
from nexus.core.physical.common import BLOCKING, field_at, mask, parse_qty
from nexus.core.sources import read_source
from nexus.core.stage import contract_csv, parse_homologation

L = default_layout()
PORTAL_NAME = "Portal_CAT_2026-11-03_0955.csv"


def codes(result: object) -> set[str]:
    return {e.code for e in result.exceptions}  # type: ignore[attr-defined]


# ------------------------------------------------------------------ utilidades
@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("6516-", "-6516"),
        ("-12", "-12"),
        ("1,234", "1234"),
        ("0", "0"),
        ("12.5", "12.5"),
        ("", None),
        ("12A", None),
        ("-5-", None),
        ("8000(0)", None),
    ],
)
def test_ph01_quantity_parsing_never_turns_text_into_zero(token: str, expected: str | None) -> None:
    value = parse_qty(token)
    assert (None if value is None else format(value, "f")) == expected


def test_ph02_fixed_width_overflow_and_trailing_minus() -> None:
    line = " " * 10 + "123456" + "-" + " " * 3  # 6 dígitos en un ancho de 5 + signo final
    assert field_at(line, 11, 16) == "123456-"
    assert field_at(" " * 20, 11, 16) == ""


def test_ph03_mask_keeps_shape_only() -> None:
    assert mask("QZX-77 a/b") == "AAA-99 A/A"


def test_ph04_kind_by_declared_name_pattern() -> None:
    assert detect_kind("R_20261101.TXT", L) == "oe965"
    assert detect_kind("r_20261101.txt", L) == "oe965"
    assert detect_kind(PORTAL_NAME, L) == "portal"
    assert detect_kind("INV_STA289.TXT", L) == "in092u"
    assert detect_kind("CO 36 inventory 32INAS.xlsx", L) is None
    assert detect_kind("MF_000123.xml", L) is None  # facturación: nunca es embarque


# ------------------------------------------------------------------ OE965
def test_oe01_hierarchical_records_and_document_date() -> None:
    r = read_oe965(fx.oe965_bytes(), L)
    assert r.valid and r.exceptions == []
    assert r.document_time == {
        "value": "2026-11-01",
        "precision": "date",
        "source": "document_header",
    }
    assert len(r.records) == 8 and r.classes["item_header"] == 3
    first = r.records[0]
    assert first["item"] == "QZXITEM1"  # heredado de la cabecera
    assert first["order"] == "7310101" and first["due_date"] == "2026-11-10"
    assert first["extraction_ts"] == "2026-11-01"  # solo fecha: no se inventa hora
    assert {x["customer_key_exact"] for x in r.records} >= {fx.CAT, fx.CAT_SPACED, fx.TUPY}
    assert r.classes["page_header"] == 6 and r.flags["form_feeds"] == 1  # 2 páginas × 3
    assert r.classes["item_total"] == 3 and r.classes["grand_total"] == 2
    assert r.flags["contract_equivalence"] == "NOT_ESTABLISHED"


def test_oe02_leading_zeros_and_customer_spaces_preserved() -> None:
    items = [("QZXZ", "1", "1", [("0000042", "1", "11/02/26", "5", "5", "QZX  A  001")])]
    r = read_oe965(fx.oe965_bytes(items=items), L)
    assert r.records[0]["order"] == "0000042"
    assert r.records[0]["customer_key_exact"] == "QZX  A  001"


@pytest.mark.parametrize("newline", ["\r\n", "\n", "\r"])
def test_oe03_crlf_lf_cr_identical_records(newline: str) -> None:
    base = read_oe965(fx.oe965_bytes(newline="\r\n"), L).records
    assert read_oe965(fx.oe965_bytes(newline=newline), L).records == base


def test_oe04_cp850_name_decoded() -> None:
    items = [
        ("QZXN", "1", "1", [("7300001", "1", "11/02/26", "5", "5", fx.OTHER, "PEÑA SINTÉTICA")])
    ]
    data = fx.oe965_bytes(items=items)
    assert "Ñ".encode("cp850") in data
    assert read_oe965(data, L).records[0]["customer_name"] == "PEÑA SINTÉTICA"


def test_oe05_negative_overflow_zero_and_missing_quantities() -> None:
    items = [
        (
            "QZXQ",
            "6516-",
            "0",
            [
                ("7300001", "1", "11/02/26", "123456", "0", fx.OTHER),  # desborde + cero
                ("7300002", "1", "11/03/26", "10", "5-", fx.OTHER),  # negativo al final
                ("7300003", "1", "11/04/26", "10", "", fx.OTHER),  # abierta faltante
            ],
        )
    ]
    r = read_oe965(fx.oe965_bytes(items=items), L)
    assert [x["qty_ordered"] for x in r.records] == ["123456", "10", "10"]
    assert [x["qty_open"] for x in r.records] == ["0", "-5", ""]  # vacío = desconocido, no 0
    assert {"QTY_NEGATIVE", "QTY_MISSING"} <= codes(r)
    assert r.valid  # advertencias, no bloqueos; el cálculo las propaga como desconocidas


def test_oe06_order_without_item_header_is_invalid() -> None:
    data = (
        "\r\n".join(
            [*fx.page_header(), fx.order_line("7300001", "1", "11/02/26", "1", "1", fx.OTHER)]
        )
    ).encode("cp850")
    r = read_oe965(data, L)
    assert not r.valid and "ORDER_WITHOUT_ITEM" in codes(r)


@pytest.mark.parametrize("due", ["1/2/26", "11/1/26", "13/01/26", "11/31/26", "26-11-01", ""])
def test_oe07_ambiguous_or_unreadable_due_date_is_invalid(due: str) -> None:
    items = [("QZXD", "1", "1", [("7300001", "1", due, "5", "5", fx.OTHER)])]
    r = read_oe965(fx.oe965_bytes(items=items), L)
    assert not r.valid and "DUE_DATE_UNREADABLE_OR_AMBIGUOUS" in codes(r)


def test_oe08_document_date_missing_or_inconsistent_is_invalid() -> None:
    missing = read_oe965(fx.oe965_bytes(pages=False), L)
    assert not missing.valid and "DOCUMENT_DATE_MISSING" in codes(missing)
    text = (
        fx.oe965_bytes()
        .decode("cp850")
        .replace("Date: 11/01/26    Page:    2", "Date: 11/02/26    Page:    2")
    )
    mixed = read_oe965(text.encode("cp850"), L)
    assert not mixed.valid and "DOCUMENT_DATE_INCONSISTENT" in codes(mixed)


def test_oe09_duplicate_identity_and_missing_customer_invalid() -> None:
    o = ("7300001", "1", "11/02/26", "5", "5", fx.OTHER)
    dup = read_oe965(fx.oe965_bytes(items=[("QZXD", "1", "1", [o, o])]), L)
    assert not dup.valid and "DUPLICATE_IDENTITY" in codes(dup)
    blank = read_oe965(
        fx.oe965_bytes(items=[("QZXD", "1", "1", [("7300001", "1", "11/02/26", "5", "5", "")])]),
        L,
    )
    assert not blank.valid and "CUSTOMER_MISSING" in codes(blank)


def test_oe10_unclassified_line_with_digits_is_invalid() -> None:
    text = fx.oe965_bytes().decode("cp850").replace("*** END REPORT ***", "  X 12 Y 9 Z 7 W")
    r = read_oe965(text.encode("cp850"), L)
    assert not r.valid and "UNCLASSIFIED_LINE" in codes(r)


def test_oe11_wrong_encodings_are_rejected() -> None:
    utf16 = b"\xff\xfe" + fx.oe965_bytes().decode("cp850").encode("utf-16-le")
    assert "UNEXPECTED_UTF16" in codes(read_oe965(utf16, L))
    assert "NUL_BYTES" in codes(read_oe965(b"R\x00", L))


def test_oe12_exceptions_carry_no_source_values() -> None:
    o = ("7300001", "1", "11/31/26", "5", "", "")
    r = read_oe965(fx.oe965_bytes(items=[("QZXSECRET", "1", "1", [o])]), L)
    rendered = repr([e.as_dict() for e in r.exceptions])
    assert "QZXSECRET" not in rendered and "7300001" not in rendered and "11/31" not in rendered


def test_oe13_staged_csv_is_valid_contract_with_date_precision(tmp_path: Path) -> None:
    r = read_oe965(fx.oe965_bytes(), L)
    path = tmp_path / "R_20261101__x.csv"
    path.write_bytes(contract_csv("oeras_29", r.records))
    parsed = read_source("oeras_29", path)
    assert parsed.valid and parsed.instant == "2026-11-01" and parsed.precision == "date"
    assert parsed.source_system == "JBA_OE965"


# ------------------------------------------------------------------ Portal
MAPPING = {"QZX PORTAL 1": "QZXITEM1"}  # resolución R1 ya verificada (entrada del lector)


def test_po01_utf16_tab_preamble_and_exact_homologation() -> None:
    mapping = MAPPING
    r = read_portal(fx.portal_bytes(), L, PORTAL_NAME, mapping)
    assert r.valid and r.bom == "UTF-16LE" and r.classes["preamble"] == 4
    assert r.document_time == {"value": "2026-11-03", "precision": "date", "source": "file_name"}
    assert [x["item"] for x in r.records] == ["QZXITEM1"] * 3
    assert r.records[0]["delivery_date"] == "2026-11-20"
    assert r.records[2]["shipped_raw"] == ""  # Shipped vacío: desconocido
    assert "SHIPPED_MISSING" in codes(r)


def test_po02_utf16_be_reads_the_same() -> None:
    mapping = MAPPING
    le = read_portal(fx.portal_bytes(), L, PORTAL_NAME, mapping).records
    assert read_portal(fx.portal_bytes(encoding="utf-16-be"), L, PORTAL_NAME, mapping).records == le


def test_po03_file_name_mark_0955_is_not_used_as_time() -> None:
    r = read_portal(fx.portal_bytes(), L, PORTAL_NAME, {})
    assert r.document_time["value"] == "2026-11-03"
    assert "09:55" not in repr(r.document_time) and "0955" not in repr(r.records)
    assert "DOCUMENT_DATE_FROM_FILE_NAME" in codes(r)


def test_po04_without_bom_or_header_is_invalid() -> None:
    no_bom = read_portal(fx.portal_bytes(encoding="utf-8"), L, PORTAL_NAME, {})
    assert not no_bom.valid and "NOT_UTF16_BOM" in codes(no_bom)
    other = read_portal(fx.portal_bytes(header=["A", "B", "C", "D", "E", "F", "G"]), L, PORTAL_NAME)
    assert not other.valid and "HEADER_NOT_FOUND" in codes(other)
    partial = [*fx.PORTAL_HEADER[:-2], "X", "Y"]
    incomplete = read_portal(fx.portal_bytes(header=partial), L, PORTAL_NAME)
    assert not incomplete.valid and "HEADER_INCOMPLETE" in codes(incomplete)
    double = [*fx.PORTAL_HEADER[:-1], "Status"]
    assert "HEADER_AMBIGUOUS" in codes(read_portal(fx.portal_bytes(header=double), L, PORTAL_NAME))


def test_po05_ambiguous_and_bad_dates_become_unknown_never_chosen() -> None:
    rows = [
        ("PL-1", "QZX PORTAL 1", "Open", "8000(0)", "0", "11/05/2026(11/07/2026)", "13/40/2026"),
    ]
    r = read_portal(fx.portal_bytes(rows=rows), L, PORTAL_NAME, {"QZX PORTAL 1": "QZXITEM1"})
    rec = r.records[0]
    assert rec["ship_date"] == "" and rec["delivery_date"] == ""
    assert rec["ordered_raw"] == "8000(0)"  # NO_CUANTIFICABLE en la regla CORE
    assert {"DATE_AMBIGUOUS", "DATE_UNREADABLE"} <= codes(r)


def test_po06_unmapped_item_is_never_associated_by_similarity() -> None:
    mapping = {"QZX PORTAL 1": "QZXITEM1"}
    rows = [
        ("PL-1", "QZX-PORTAL-1", "Open", "1", "0", "11/05/2026", "11/06/2026"),  # guiones
        ("PL-2", "qzx portal 1", "Open", "1", "0", "11/05/2026", "11/06/2026"),  # minúsculas
        ("PL-3", "QZX PORTAL 1 ", "Open", "1", "0", "11/05/2026", "11/06/2026"),  # espacio
    ]
    r = read_portal(fx.portal_bytes(rows=rows), L, PORTAL_NAME, mapping)
    # el espacio final de la celda se recorta por TAB; guiones y minúsculas NO se asocian
    assert [x["item"] for x in r.records] == ["", "", "QZXITEM1"]
    assert sum(1 for e in r.exceptions if e.code == "UNMAPPED_ITEM") == 2


def test_po07_row_shape_and_duplicate_line_id_invalid() -> None:
    short = read_portal(fx.portal_bytes(rows=[("PL-1", "X", "Open")]), L, PORTAL_NAME)
    assert not short.valid and "ROW_SHAPE" in codes(short)
    dup_rows = [fx.PORTAL_ROWS[0], fx.PORTAL_ROWS[0]]
    dup = read_portal(fx.portal_bytes(rows=dup_rows), L, PORTAL_NAME)
    assert not dup.valid and "LINE_ID_EMPTY_OR_DUPLICATE" in codes(dup)


def test_po08_homologation_table_rules() -> None:
    """[D-D] Tabla R1 v2 + registro autorizado. Detalle adversarial en test_cert_r1_r3.py."""
    erp = frozenset({"QZXITEM1", "QZXITEM2"})
    approvers = frozenset({fx.APPROVER})
    res = parse_homologation(fx.HOMOLOGATION, fx.LEDGER, approvers, "2026-11-07", erp)
    assert res.mapping == {"QZX PORTAL 1": "QZXITEM1"}
    assert res.report["pending"] == {"PROVISIONAL_NOT_APPLIED": 1}
    one_to_many = fx.HOMOLOGATION + "QZX PORTAL 1,QZXITEM2,APPROVED_MAP,R1-SYN-0002\n"
    bad = parse_homologation(one_to_many, fx.LEDGER, approvers, "2026-11-07", erp)
    assert bad.mapping == {} and bad.report["errors"]  # tabla completa rechazada
    v1 = "item_portal,item_erp,status,evidence\nQZX PORTAL 1,QZXITEM1,APPROVED,acta\n"
    old = parse_homologation(v1, fx.LEDGER, approvers, "2026-11-07", erp)
    assert old.mapping == {} and old.report["errors"]  # la tabla v1 ya no se acepta


# ------------------------------------------------------------------ IN092U
def test_in01_cut_from_separate_date_and_time_and_stockroom_from_group() -> None:
    r = read_in092u(fx.in092u_bytes(), L, "America/Mexico_City")
    assert r.valid and r.flags["excludes_zero_physical"] is True
    assert r.document_time["value"] == "2026-11-06T15:48:14Z"
    assert r.document_time["local"] == "2026-11-06T09:48:14"
    assert {(x["item"], x["stockroom"], x["qty_physical"]) for x in r.records} == {
        ("QZXITEM1", "FG", "150.000"),
        ("QZXITEM2", "FG", "-5.000"),  # negativo conservado
        ("QZXITEM1", "QF", "40.000"),
        ("QZXITEM2", "RM", "300.000"),
        ("QZXITEM2", "QH", "7.500"),  # QH/RM se conservan; la regla los deja fuera de FG
    }
    assert all(x["qty_reserved"] == "" for x in r.records)  # no informado: desconocido
    rec = r.flags["reconciliation"]
    assert rec["data_rows"] == rec["rows_assigned_to_stockroom"] == 5
    assert [g["stockroom"] for g in rec["groups"]] == ["FG", "QF", "RM", "QH"]
    assert all(g["qty_total_match"] and g["value_total_match"] for g in rec["groups"])


def test_in02_without_declared_zone_there_is_no_cut() -> None:
    r = read_in092u(fx.in092u_bytes(), L, None)
    assert not r.valid and "BUSINESS_TZ_NOT_DECLARED" in codes(r)


@pytest.mark.parametrize(
    ("date", "time", "code"),
    [
        ("11/01/26", " 1:30:00", "CUT_LOCAL_TIME_AMBIGUOUS"),
        ("03/08/26", " 2:30:00", "CUT_LOCAL_TIME_NONEXISTENT"),
    ],
)
def test_in03_dst_ambiguous_or_nonexistent_local_cut_rejected(
    date: str, time: str, code: str
) -> None:
    r = read_in092u(fx.in092u_bytes(date=date, time=time), L, "America/New_York")
    assert not r.valid and code in codes(r)


def test_in04_duplicates_and_zero_exclusion_flag() -> None:
    dup = read_in092u(
        fx.in092u_bytes(rows=[("A1", "FG", "1.000"), ("A1", "FG", "2.000")]), L, "UTC"
    )
    assert not dup.valid and "DUPLICATE_ITEM_STOCKROOM" in codes(dup)
    nz = read_in092u(fx.in092u_bytes(excludes_zero=False), L, "UTC")
    assert "ZERO_EXCLUSION_NOT_DECLARED" in codes(nz)


def test_in07_group_total_mismatch_is_blocking() -> None:
    """La asignación del stockroom se demuestra con el total del grupo: si no cuadra, no se
    adivina a qué stockroom pertenecen las filas."""
    r = read_in092u(fx.in092u_bytes(group_qty_override={"FG": "146.000"}), L, "UTC")
    assert not r.valid and "GROUP_QTY_TOTAL_MISMATCH" in codes(r)
    fg = next(g for g in r.flags["reconciliation"]["groups"] if g["stockroom"] == "FG")
    assert fg["qty_total_match"] is False


def test_in08_rows_without_group_total_are_blocking() -> None:
    r = read_in092u(fx.in092u_bytes(with_group_totals=False), L, "UTC")
    assert not r.valid and "ROW_WITHOUT_GROUP_TOTAL" in codes(r)
    assert r.records == []  # nunca se asigna stockroom sin su total


def test_in09_repeated_stockroom_group_is_blocking() -> None:
    rows = [("A1", "FG", "1.000"), ("A2", "QF", "1.000"), ("A3", "FG", "1.000")]
    r = read_in092u(fx.in092u_bytes(rows=rows), L, "UTC")
    assert not r.valid and "GROUP_STOCKROOM_REPEATED" in codes(r)


def test_in10_cut_inconsistent_or_missing_across_pages() -> None:
    page2 = fx.in092u_bytes(time="10:00:00").decode("cp850").splitlines()[:2]
    text = fx.in092u_bytes().decode("cp850") + "\r\n".join(page2) + "\r\n"
    r = read_in092u(text.encode("cp850"), L, "UTC")
    assert not r.valid and "CUT_INCONSISTENT" in codes(r)
    no_time = "\r\n".join(
        x for x in fx.in092u_bytes().decode("cp850").splitlines() if "Time:" not in x
    )
    r2 = read_in092u(no_time.encode("cp850"), L, "UTC")
    assert not r2.valid and "CUT_MISSING" in codes(r2)


def test_in11_leading_minus_zero_and_unknown_structure() -> None:
    r = read_in092u(fx.in092u_bytes(rows=[("A1", "FG", "-2.000"), ("A2", "FG", "0.000")]), L, "UTC")
    assert {x["qty_physical"] for x in r.records} == {"-2.000", "0.000"}
    assert {"QTY_NEGATIVE", "QTY_ZERO_DESPITE_EXCLUSION"} <= codes(r)
    odd = fx.in092u_bytes().decode("cp850").replace("*** END OF REPORT ***", "Lote 77 de 88")
    bad = read_in092u(odd.encode("cp850"), L, "UTC")
    assert not bad.valid and "UNCLASSIFIED_LINE" in codes(bad)
    # una fila con la cantidad corrida de columna no se acepta como fila de datos
    shifted = fx.in092u_row("A9", "1.000").replace("     1.000", "1.000     ", 1)
    text = fx.in092u_bytes().decode("cp850").replace("*** END OF REPORT ***", shifted)
    assert "UNCLASSIFIED_LINE" in codes(read_in092u(text.encode("cp850"), L, "UTC"))


def test_in05_staged_csv_is_valid_fg_contract(tmp_path: Path) -> None:
    r = read_in092u(fx.in092u_bytes(), L, "America/Mexico_City")
    path = tmp_path / "INV.csv"
    path.write_bytes(contract_csv("fg_inventory", r.records))
    parsed = read_source("fg_inventory", path)
    assert parsed.valid and parsed.instant == "2026-11-06T15:48:14Z"


def test_in06_fg_and_shipments_never_accept_date_precision(tmp_path: Path) -> None:
    r = read_in092u(fx.in092u_bytes(), L, "UTC")
    rows = [{**x, "cut_ts": "2026-11-06"} for x in r.records]
    path = tmp_path / "INV.csv"
    path.write_bytes(contract_csv("fg_inventory", rows))
    parsed = read_source("fg_inventory", path)
    assert not parsed.valid and parsed.instant is None
    assert any(e.severity == BLOCKING for e in read_in092u(b"", L, "UTC").exceptions)


# ------------------------------------------------------------------ R2: conciliación OE965
def test_oe20_item_and_grand_totals_reconcile_with_orders_read() -> None:
    r = read_oe965(fx.oe965_bytes(), L)
    rec = r.flags["reconciliation"]
    assert rec["item_headers"] == rec["item_totals"] == 3 and rec["grand_totals"] == 2
    assert rec["orders_read"] == len(r.records) == 8
    assert (
        rec["totals_matched"]
        == rec["totals_checked"]
        == {
            "qty_ordered": 5,
            "qty_open": 5,
            "qty_alloc": 5,
            "tail": 5,
        }
    )


@pytest.mark.parametrize(("col", "blocking"), [(0, True), (1, True), (2, False), (3, False)])
def test_oe21_total_mismatch_blocks_contract_columns(col: int, blocking: bool) -> None:
    sums = [2049, 2049, 0, 4800]  # QZXITEM1: ordenada, abierta, asignada, cola
    sums[col] += 1
    data = fx.oe965_bytes(total_override={"QZXITEM1": tuple(sums)})  # type: ignore[dict-item]
    r = read_oe965(data, L)
    hits = [e for e in r.exceptions if e.code == "TOTAL_MISMATCH"]
    assert len(hits) == 1 and (hits[0].severity == BLOCKING) is blocking
    assert r.valid is not blocking


def test_oe22_lost_order_line_is_detected_by_totals() -> None:
    """Una línea de pedido que no se lee como pedido (p. ej. corrida de columna) no puede
    desaparecer en silencio: el total del ITEM deja de cuadrar o la línea queda sin clasificar."""
    text = fx.oe965_bytes().decode("cp850")
    order = fx.order_line("7310103", "1", "10/20/26", "50", "50", fx.OTHER)
    r = read_oe965(text.replace(order, " " + order[:-1]).encode("cp850"), L)
    assert not r.valid
    assert {"TOTAL_MISMATCH", "UNCLASSIFIED_LINE"} & {e.code for e in r.exceptions}


def test_oe23_item_without_total_and_order_after_total_block() -> None:
    no_totals = read_oe965(fx.oe965_bytes(totals=False), L)
    assert not no_totals.valid and "ITEM_TOTAL_MISSING" in codes(no_totals)
    assert no_totals.flags["reconciliation"]["items_without_total"] == 3
    text = fx.oe965_bytes().decode("cp850")
    extra = fx.order_line("7310199", "1", "11/10/26", "1", "1", fx.OTHER)
    first_total = fx.total_line(2049, 2049, 0, 4800)
    late = text.replace(first_total, first_total + "\r\n" + extra, 1)
    r = read_oe965(late.encode("cp850"), L)
    assert not r.valid and "ORDER_AFTER_ITEM_TOTAL" in codes(r)


def test_oe24_flag_tail_and_unreadable_tail() -> None:
    items = [
        ("QZXF", "1", "1", [("7300001", "1", "11/02/26", "5", "5", fx.OTHER, "N", "0", "*", "77")])
    ]
    r = read_oe965(fx.oe965_bytes(items=items), L)
    assert r.valid and r.flags["reconciliation"]["flagged_orders"] == 1
    assert r.records[0]["customer_name"] == "N"
    bad = [
        ("QZXF", "1", "1", [("7300001", "1", "11/02/26", "5", "5", fx.OTHER, "N", "0", "", "7X7")])
    ]
    rb = read_oe965(fx.oe965_bytes(items=bad, totals=False), L)
    assert "ORDER_TAIL_UNREADABLE" in codes(rb) and not rb.valid


def test_oe25_missing_quantity_makes_total_not_verifiable_never_zero() -> None:
    items = [("QZXQ", "1", "1", [("7300001", "1", "11/02/26", "10", "", fx.OTHER)])]
    r = read_oe965(fx.oe965_bytes(items=items), L)
    assert r.records[0]["qty_open"] == ""
    assert r.flags["reconciliation"]["totals_not_verifiable"]["qty_open"] == 3  # ITEM + 2 generales
    assert "TOTAL_MISMATCH" not in codes(r)


# ------------------------------------------------------------------ R2: diagnóstico Portal
def test_po20_header_diagnosis_names_missing_fields_without_values() -> None:
    header = [
        "Line ID",
        "Part Number",
        "Status",
        "Ordered Qty",
        "Shipped Qty",
        "Need Date",
        "Promise Date",
    ]
    r = read_portal(fx.portal_bytes(header=header, preamble=False), L, PORTAL_NAME, {})
    assert not r.valid and "HEADER_INCOMPLETE" in codes(r)
    diag = r.flags["header_diagnosis"]
    assert diag["line"] == 1 and diag["columns"] == 7
    assert diag["missing"] == ["delivery_date", "ship_date"]
    assert diag["fields"]["shipped"]["status"] == "MATCHED"
    assert diag["fields"]["shipped"]["matched_alias"] == "Shipped Qty"
    assert diag["unmatched_columns_masked"] == [
        {"column": 5, "shape": "AAAA AAAA"},
        {"column": 6, "shape": "AAAAAAA AAAA"},
    ]
    assert r.classes["header"] == 1 and r.classes["uninterpreted_row"] == 3
    assert r.flags["rows_by_column_count"] == {7: 3}
    assert r.records == []  # no se interpreta ninguna fila con un encabezado incompleto


def test_po21_probe_shows_raw_header_names_only_on_request(tmp_path: Path) -> None:
    from nexus.core.stage import probe

    header = ["Line ID", "Part Number", "Status", "Ordered Qty", "Shipped Qty", "Need Date", "X"]
    path = tmp_path / PORTAL_NAME
    path.write_bytes(fx.portal_bytes(header=header, preamble=False))
    default = probe(path)["structure"]
    assert "header_names" not in default and "Need Date" not in repr(default)
    assert default["flags"]["header_diagnosis"]["missing"] == ["delivery_date", "ship_date"]
    shown = probe(path, show_headers=True)["structure"]
    assert shown["header_names"] == header
    assert "QZX PORTAL" not in repr(shown) and "PL-1" not in repr(shown)


def test_po22_revised_original_dates_with_spaces_are_unknown() -> None:
    rows = [("PL-1", "QZX PORTAL 1", "Open", "5", "", "11/05/2026 (11/07/2026)", "")]
    r = read_portal(fx.portal_bytes(rows=rows), L, PORTAL_NAME, {"QZX PORTAL 1": "QZXITEM1"})
    rec = r.records[0]
    assert rec["ship_date"] == "" and rec["shipped_raw"] == ""
    assert {"DATE_AMBIGUOUS", "SHIPPED_MISSING", "DATE_MISSING"} <= codes(r)
