"""R2 — Las formas ENMASCARADAS reportadas por el primer probe real (ASMXC1200, noviembre 2026)
se clasifican correctamente.

Las constantes son exactamente las formas saneadas de los informes ``probe_*.json`` (letras → A,
dígitos → 9): no contienen datos. Cada forma se instancia con valores sintéticos (A → Q, 9 → 1) y
se verifica su clase. Así queda fijado que los lectores reconocen la estructura real, y que lo que
no tiene esa forma sigue fallando cerrado.
"""

from __future__ import annotations

import re

import physical_fixtures as fx
import pytest

from nexus.core.physical import default_layout, read_in092u, read_oe965
from nexus.core.physical.in092u import _data_row, _group_total
from nexus.core.physical.oe965 import _total_tokens, _underline

L = default_layout()

# ---- probe_R_20261101.json (structure.shapes_masked)
OE_ORDER = [
    " " * 48 + "9999999  99    9/99/99        9999        9999           9   999999    999  "
    "AA AAAAAAAAAA AAAAAAAAAA            9999",
    " " * 48 + "9999999   9   99/99/99     9999999     9999999           9   99999999  999  "
    "A AAAAA AAAAAAA AAA               999999",
    " " * 48 + "9999999 999   99/99/99        9999        9999           9   99999999  999  "
    "AAAA AAAAAAA AAAAAAAAAAAA           9999",
]
OE_ITEM_HEADER = [
    "A-999-999-9999         9999         9999",
    "A9999999            9999999      9999999",
    "AA9A9A999AAAAA         9999         9999",
]
OE_UNDERLINE = " " * 72 + "----------  ----------  ----------" + " " * 48 + "----------"
OE_TOTALS = [  # las 238 líneas «unclassified» del primer probe
    " " * 78 + "9999        9999           9" + " " * 54 + "9999",
    " " * 75 + "9999999     9999999           9" + " " * 52 + "999999",
    " " * 78 + "9999        9999           9" + " " * 53 + "99999",
]
OE_LABELS = [
    " " * 41
    + "*** AAAAAA AAA AA ***"
    + " " * 10
    + "----------  ----------  ----------"
    + " " * 48
    + "----------",
    " " * 41
    + "**** AAAAAA AAA AAAAAA ****"
    + " " * 4
    + "----------  ----------  ----------"
    + " " * 48
    + "----------",
]
# ---- probe_INV_STA289.json
IN_ROWS = [
    "A-999-999-9999  AAAAAAA AAAAA                        A-99   A    999   999        "
    "9,999.999             .99999           9,999.99",
    "A-999-999-9999  AAAAAAA AAAAA                        A-9    A    999   999          "
    "999.999            9.99999           9,999.99",
]
IN_GROUPS = [
    " " * 53 + "AAAAAAAAA: AA  AAAAA:    9,999,999.999                         99,999,999.99",
    " " * 53 + "AAAAAAAAA: AA  AAAAA:        9,999.999                             99,999.99",
    " " * 53 + "AAAAAAAAA: AA  AAAAA:      999,999.999                         99,999,999.99",
]
IN_TIME = " " * 104 + "AAAA:  9:99:99"
IN_TITLE = (
    "AA999A     AAAAAAAAAA AAAAAA - AAAAAA                 AAAAAAAAA AAAAAA AAAAAA"
    + " " * 27
    + "AAAA: 99/99/99    AAAA:    9"
)


def inst(shape: str) -> str:
    """Instancia sintética de una forma: A → Q, 9 → 1 (nunca valores reales)."""
    return shape.replace("A", "Q").replace("9", "1")


def test_r2_01_real_shapes_have_the_reported_lengths() -> None:
    assert {len(s) for s in OE_ORDER + OE_TOTALS + OE_LABELS + [OE_UNDERLINE]} == {164}
    assert {len(s) for s in OE_ITEM_HEADER} == {40}
    assert {len(s) for s in IN_ROWS + IN_GROUPS} == {129}
    assert len(IN_TIME) == 118 and len(IN_TITLE) == 132


def test_r2_02_oe965_total_lines_require_exact_columns_and_context() -> None:
    cfg = L["oe965"]
    assert _underline(OE_UNDERLINE, cfg["total_underline"])
    for shape in OE_TOTALS:
        assert _total_tokens(inst(shape), cfg) is not None, shape
        moved = " " + inst(shape)[:-1]  # todas las cifras una columna a la derecha
        assert _total_tokens(moved, cfg) is None
    for label in OE_LABELS:
        rest = label[72:]
        assert _underline(" " * 72 + rest, cfg["total_underline"])
    # un total real sin su subrayado previo sigue siendo bloqueante
    lines = [
        *fx.page_header(),
        inst(OE_ITEM_HEADER[0]),
        inst(OE_ORDER[0]).replace("1/11/11", "1/11/26"),
        inst(OE_TOTALS[0]),
    ]
    r = read_oe965(("\r\n".join(lines) + "\r\n").encode("cp850"), L)
    assert "TOTAL_WITHOUT_UNDERLINE" in {e.code for e in r.exceptions} and not r.valid


def test_r2_03_oe965_real_order_shapes_are_orders_with_name_and_tail() -> None:
    for shape in OE_ORDER:
        line = inst(shape)
        doc = [*fx.page_header(), inst(OE_ITEM_HEADER[1]), line]
        r = read_oe965(("\r\n".join(doc) + "\r\n").encode("cp850"), L)
        assert r.classes.get("order_line") == 1 and r.classes.get("item_header") == 1, shape
        rec = r.records[0]
        assert rec["customer_name"] and not re.search(r"\d", rec["customer_name"])  # sin la cola
        assert rec["customer_key_exact"] == line[109:122].rstrip()
        assert "ORDER_TAIL_UNREADABLE" not in {e.code for e in r.exceptions}


def test_r2_04_in092u_real_row_and_group_shapes() -> None:
    cfg = L["in092u"]
    for shape in IN_ROWS:
        row = _data_row(inst(shape), cfg)
        assert row is not None and row[0] == "Q-111-111-1111", shape
    for shape in IN_GROUPS:
        group = _group_total(inst(shape), cfg)
        assert group is not None and group[0] == "QQ", shape
    # la misma fila con la cantidad corrida una columna no es una fila de datos
    shifted = inst(IN_ROWS[0]).replace("1,111.111 ", " 1,111.111", 1)
    assert _data_row(shifted, cfg) is None


def test_r2_05_in092u_date_and_time_lines_form_the_cut() -> None:
    title = inst(IN_TITLE).replace("QQQQ: 11/11/11", "Date: 11/06/26")
    time = inst(IN_TIME).replace("1:11:11", "9:48:14")
    row = inst(IN_ROWS[0])  # cantidad 1,111.111 y valor 1,111.11 en sus columnas reales
    group = inst(IN_GROUPS[1]).replace("QQ  ", "FG  ", 1).replace("11,111.11", " 1,111.11")
    data = "\r\n".join([title, time, "Excludes Zero Physical Quantity", row, group]) + "\r\n"
    r = read_in092u(data.encode("cp850"), L, "America/Mexico_City")
    assert r.valid, r.exception_summary()
    assert r.document_time["value"] == "2026-11-06T15:48:14Z"
    assert r.records[0]["stockroom"] == "FG" and r.records[0]["qty_physical"] == "1111.111"
    assert r.flags["reconciliation"]["groups"][0]["value_total_match"] is True


@pytest.mark.parametrize("bad", ["Lote 77 de 88", " " * 53 + "QQQQ: 12"])
def test_r2_06_unknown_structures_still_fail_closed(bad: str) -> None:
    text = fx.in092u_bytes().decode("cp850").replace("*** END OF REPORT ***", bad)
    assert not read_in092u(text.encode("cp850"), L, "UTC").valid
    oe = fx.oe965_bytes().decode("cp850").replace("*** END REPORT ***", bad)
    assert not read_oe965(oe.encode("cp850"), L).valid
