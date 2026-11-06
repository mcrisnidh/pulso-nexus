"""WP1/WP3/WP4/WP5 — contratos de fuentes, FG, embarques y cobertura (puro)."""

from __future__ import annotations

import ast
import copy
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nexus.core import demo
from nexus.core.coverage import (
    OFICIAL,
    CoverageInput,
    check_invariants,
    compute,
)
from nexus.core.rules import core_rules_from_dict
from nexus.core.sources import FgRecord, OerasLine, Shipment, read_source
from nexus.planning.adapters.sources import PortalLine
from nexus.planning.model import SourceRef

ROOT = Path(__file__).resolve().parents[2]
RULES = core_rules_from_dict(demo.RULES, "x" * 64)
REF = SourceRef("t", "t.csv", "0" * 64, 2, "2031-01-10T18:00:00Z", "t")


def _write(tmp: Path, source: str, name: str, data: bytes) -> Path:
    p = tmp / source / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# ------------------------------------------------------------------ G1 contratos
def test_g1_synthetic_fixtures_are_deterministic(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    demo.scenario(a)
    demo.scenario(b)
    files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    assert files and all((a / f).read_bytes() == (b / f).read_bytes() for f in files)


@pytest.mark.parametrize("source", ["oeras_29", "portal_cat", "fg_inventory", "shipments"])
def test_g1_valid_fixture_accepted_missing_column_rejected(tmp_path: Path, source: str) -> None:
    root = tmp_path / "d"
    demo.scenario(root)
    good = sorted((root / "inbox" / source).glob("*.csv"))[0]
    parsed = read_source(source, good)
    assert parsed.valid, parsed.errors
    assert parsed.source_system == "SYNTHETIC" and parsed.instant
    text = good.read_text("utf-8").splitlines()
    header = text[0].split(",")
    bad = _write(
        tmp_path,
        source,
        "bad.csv",
        (
            "\n".join([",".join(header[1:])] + [",".join(r.split(",")[1:]) for r in text[1:]])
            + "\n"
        ).encode(),
    )
    rejected = read_source(source, bad)
    assert not rejected.valid and "columnas requeridas ausentes" in rejected.errors[0]


def test_g1_oeras_duplicate_identity_and_bad_date_rejected(tmp_path: Path) -> None:
    row = ("SYN-A", demo.TUPY, "000101", "1", "2031-01-10", "5")
    dup = read_source(
        "oeras_29",
        _write(tmp_path, "oeras_29", "d.csv", demo.oeras("2031-01-01T06:00:00Z", [row, row])),
    )
    assert not dup.valid and any("duplicada" in e for e in dup.errors)
    bad = read_source(
        "oeras_29",
        _write(
            tmp_path,
            "oeras_29",
            "b.csv",
            demo.oeras("2031-01-01T06:00:00Z", [("SYN-A", demo.TUPY, "1", "1", "31/01/2031", "5")]),
        ),
    )
    assert not bad.valid
    mixed = (
        demo.oeras("2031-01-01T06:00:00Z", [row])
        + demo.oeras("2031-01-02T06:00:00Z", [row]).split(b"\n", 1)[1]
    )
    assert not read_source("oeras_29", _write(tmp_path, "oeras_29", "m.csv", mixed)).valid


def test_g1_customer_key_spaces_and_leading_zeros_preserved(tmp_path: Path) -> None:
    p = read_source(
        "oeras_29",
        _write(
            tmp_path,
            "oeras_29",
            "k.csv",
            demo.oeras(
                "2031-01-01T06:00:00Z",
                [("SYN-A", "SYNOTH    001", "000101", "01", "2031-01-10", "5")],
            ),
        ),
    )
    rec = p.records[0]
    assert rec.customer_key == "SYNOTH    001" and rec.order == "000101" and rec.line == "01"


# ------------------------------------------------------------------ G4 embarques
def test_g4_shipments_contract_rules(tmp_path: Path) -> None:
    ts, t = "2031-01-10T18:00:00Z", "2031-01-05T09:00:00Z"
    base = ("MV-1", "SYN-A", demo.TUPY, "1", "1", "5", t, "SHIP")
    cases = {
        "dup": [base, base],
        "bad_reversal": [base, ("MV-2", "SYN-A", demo.TUPY, "1", "1", "5", t, "REVERSAL:XX")],
        "zero": [("MV-1", "SYN-A", demo.TUPY, "1", "1", "0", t, "SHIP")],
        "type": [("MV-1", "SYN-A", demo.TUPY, "1", "1", "5", t, "INVOICE")],
    }
    for name, rows in cases.items():
        parsed = read_source(
            "shipments", _write(tmp_path, "shipments", f"{name}.csv", demo.shipments(ts, rows))
        )
        assert not parsed.valid, name
    uom = demo.shipments(ts, [base]).replace(b",EA\n", b",KG\n")
    assert not read_source("shipments", _write(tmp_path, "shipments", "u.csv", uom)).valid


def test_g4_shipments_adapter_never_reads_billing() -> None:
    for name in ("sources.py", "coverage.py", "refresh.py"):
        tree = ast.parse((ROOT / "src/nexus/core" / name).read_text("utf-8"))
        text = " ".join(
            n.value.lower()
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )
        names = " ".join(n.id.lower() for n in ast.walk(tree) if isinstance(n, ast.Name))
        for word in ("billing", "factura", "invoice"):
            assert word not in text and word not in names, (name, word)


# ------------------------------------------------------------------ G5 cobertura
def _inp(
    oeras: list[OerasLine],
    portal: list[PortalLine],
    fg: list[FgRecord],
    ships: list[Shipment],
    rules: object = RULES,
    certified: bool = False,
) -> CoverageInput:
    return CoverageInput(
        "2031-01", "2031-01-20T18:00:00Z", oeras, portal, fg, ships, rules, certified
    )  # type: ignore[arg-type]


def _o(item: str, cust: str, order: str, due: str, qty: str | None) -> OerasLine:
    return OerasLine(
        item, cust, "N", order, "1", due, None, None if qty is None else Decimal(qty), REF
    )


def _s(
    mid: str,
    item: str,
    cust: str,
    order: str,
    qty: str,
    typ: str = "SHIP",
    rev: str = "",
    when: str = "2031-01-05T09:00:00Z",
) -> Shipment:
    return Shipment(mid, item, cust, order, "1", Decimal(qty), when, typ, rev, "EA", REF)


def _f(item: str, room: str, qty: str) -> FgRecord:
    return FgRecord(item, room, Decimal(qty), None, REF)


def _p(item: str, lid: str, status: str, o: str, sh: str, date: str) -> PortalLine:
    return PortalLine(item, item, lid, status, o, sh, date, date, "", "", REF)


def _v(res: dict, item: str) -> dict:
    return {k: f.value for k, f in res["items"][item]["facts"].items()}


def test_g5_tupy_preserved_cat_substituted_review_kept() -> None:
    res = compute(
        _inp(
            [
                _o("X", demo.TUPY, "1", "2031-01-10", "30"),
                _o("X", demo.CAT, "2", "2031-01-11", "999"),
                _o("X", demo.REVIEW, "3", "2031-01-12", "7"),
            ],
            [_p("X", "P1", "Open", "100", "40", "2031-01-15")],
            [_f("X", "FG", "0")],
            [],
        )
    )
    v = _v(res, "X")
    assert v["demand"] == Decimal(30 + 60 + 7)  # TUPY + DIF Portal + revisión; CAT de 29 no
    classes = sorted(u["customer_class"] for u in res["items"]["X"]["units"])
    assert classes == ["CAT", "CAT_REVIEW", "TUPY"]
    assert not check_invariants(res, [])


def test_g5_horizon_overdue_in_month_future_kept_in_view() -> None:
    res = compute(
        _inp(
            [
                _o("X", demo.OTHER, "1", "2030-12-01", "5"),
                _o("X", demo.OTHER, "2", "2031-01-31", "6"),
                _o("X", demo.OTHER, "3", "2031-02-01", "700"),
            ],
            [],
            [_f("X", "FG", "1")],
            [],
        )
    )
    assert _v(res, "X")["demand"] == 11
    assert [f["order"] for f in res["future_lines"]] == ["3"]


def test_g5_unknown_is_never_zero() -> None:
    res = compute(
        _inp([_o("X", demo.OTHER, "1", "2031-01-10", None)], [], [_f("X", "FG", "5")], [])
    )
    assert _v(res, "X")["demand"] is None and _v(res, "X")["uncovered"] is None
    res = compute(
        _inp([], [_p("Y", "P", "Open", "8000(0)", "0", "2031-01-10")], [_f("Y", "FG", "5")], [])
    )
    assert _v(res, "Y")["demand"] is None
    res = compute(_inp([_o("Z", demo.OTHER, "1", "2031-01-10", "5")], [], [], []))
    assert _v(res, "Z")["fg_assignable"] is None and _v(res, "Z")["covered"] is None
    res = compute(_inp([_o("W", demo.CAT, "1", "2031-01-10", "5")], [], [_f("W", "FG", "1")], []))
    assert _v(res, "W")["demand"] is None  # CAT en 29 sin Portal: no determinable


def test_g5_fg_only_usable_stockrooms_and_negative_not_certifiable() -> None:
    res = compute(
        _inp(
            [_o("X", demo.OTHER, "1", "2031-01-10", "50")],
            [],
            [
                _f("X", "FG", "10"),
                _f("X", "QF", "100"),
                _f("X", "QH", "100"),
                _f("X", "RM", "100"),
                _f("X", "SA", "100"),
            ],
            [],
        )
    )
    assert _v(res, "X")["fg_assignable"] == 10 and _v(res, "X")["uncovered"] == 40
    res = compute(
        _inp([_o("Y", demo.OTHER, "1", "2031-01-10", "50")], [], [_f("Y", "FG", "-1")], [])
    )
    f = res["items"]["Y"]["facts"]["fg_assignable"]
    assert f.value is None and f.status == "NOT_CERTIFIABLE"


def test_g5_attribution_levels_reversal_excess_and_single_use() -> None:
    oeras = [
        _o("X", demo.OTHER, "1", "2031-01-10", "10"),
        _o("X", demo.OTHER, "2", "2031-01-11", "10"),
    ]
    ships = [
        _s("A", "X", demo.OTHER, "1", "4"),  # pedido+línea
        _s("B", "X", demo.OTHER, "2", "3"),  # pedido (línea 1 = coincide)
        _s("C", "X", demo.OTHER, "", "20"),  # agregado ITEM+cliente + exceso
        _s("D", "X", demo.OTHER, "1", "1", "REVERSAL", "A"),
        _s("E", "X", demo.TUPY, "1", "5"),  # otro cliente: sin evidencia
        _s("F", "X", demo.OTHER, "1", "9", when="2031-01-25T00:00:00Z"),
    ]  # fuera
    res = compute(_inp(oeras, [], [_f("X", "FG", "0")], ships))
    att = res["attribution"]
    assert _v(res, "X")["attended"] == 20 and _v(res, "X")["pending"] == 0
    levels = {(a["movement_id"], a["level"]) for a in att["allocations"]}
    assert ("A", "LINE") in levels and ("C", "ITEM_CUSTOMER") in levels
    assert [e["movement_id"] for e in att["excess"]] == ["C"]
    assert [e["movement_id"] for e in att["unattributed"]] == ["E"]
    assert [e["movement_id"] for e in att["excluded"]] == ["F"]
    assert not check_invariants(res, ships)
    assert res["items"]["X"]["kpi"]["attended"] == "PROVISIONAL"


def test_g5_no_official_kpi_with_provisional_rules_and_official_when_approved() -> None:
    oeras, fg = [_o("X", demo.OTHER, "1", "2031-01-10", "5")], [_f("X", "FG", "1")]
    ships = [_s("A", "X", demo.OTHER, "1", "2")]
    res = compute(_inp(oeras, [], fg, ships, certified=True))
    assert set(res["items"]["X"]["kpi"].values()) == {"PROVISIONAL"}
    approved = {
        **demo.RULES,
        "approval": "CRC_APPROVED",
        "portal_date": {"field": "delivery_date", "approval": "CRC_APPROVED"},
        "allocation": {"method": "due_date_fifo", "approval": "CRC_APPROVED"},
    }
    rules = core_rules_from_dict(approved)
    assert set(
        compute(_inp(oeras, [], fg, ships, rules, certified=False))["items"]["X"]["kpi"].values()
    ) == {"PROVISIONAL"}
    official = compute(_inp(oeras, [], fg, ships, rules, certified=True))
    assert set(official["items"]["X"]["kpi"].values()) == {OFICIAL}
    assert official["items"]["X"]["line_coverage"]["status"] == "PROVISIONAL_CALCULATED"
    assert res["items"]["X"]["line_coverage"]["status"] == "BLOCKED_CRC"


# ------------------------------------------------------------------ propiedades
CUSTS = [demo.TUPY, demo.OTHER, demo.REVIEW, demo.CAT]


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[list, list, list, list]:
    items = ["X", "Y"]
    oeras = [
        _o(
            draw(st.sampled_from(items)),
            draw(st.sampled_from(CUSTS)),
            str(i),
            f"2031-01-{draw(st.integers(1, 28)):02d}",
            draw(st.sampled_from([None, *[str(x) for x in range(0, 60, 7)]])),
        )
        for i in range(draw(st.integers(0, 6)))
    ]
    portal = [
        _p(
            draw(st.sampled_from(items)),
            f"P{i}",
            draw(st.sampled_from(["Open", "Cancelled"])),
            str(draw(st.integers(0, 50))),
            draw(st.sampled_from(["", "0", "10"])),
            f"2031-0{draw(st.integers(1, 2))}-10",
        )
        for i in range(draw(st.integers(0, 3)))
    ]
    fg = [
        _f(i, draw(st.sampled_from(["FG", "QF"])), str(draw(st.integers(-3, 40))))
        for i in items
        if draw(st.booleans())
    ]
    ships = [
        _s(
            f"M{i}",
            draw(st.sampled_from(items)),
            draw(st.sampled_from(CUSTS)),
            str(draw(st.integers(0, 6))),
            str(draw(st.integers(1, 30))),
        )
        for i in range(draw(st.integers(0, 6)))
    ]
    return oeras, portal, fg, ships


@settings(max_examples=150, deadline=None)
@given(scenarios(), st.randoms())
def test_g5_properties_conservation_single_use_order_independent(sc: tuple, rnd: object) -> None:
    oeras, portal, fg, ships = sc
    res = compute(_inp(oeras, portal, fg, ships))
    assert check_invariants(res, ships) == []
    shuffled = [list(x) for x in sc]
    for x in shuffled:
        rnd.shuffle(x)  # type: ignore[attr-defined]
    res2 = compute(_inp(*shuffled))
    assert {i: _v(res2, i) for i in res2["items"]} == {i: _v(res, i) for i in res["items"]}


def test_g5_mutations_are_detected() -> None:
    oeras = [_o("X", demo.OTHER, "1", "2031-01-10", "10")]
    ships = [_s("A", "X", demo.OTHER, "1", "4")]
    base = compute(_inp(oeras, [], [_f("X", "FG", "3")], ships))
    assert check_invariants(base, ships) == []

    def mutated(fn: object) -> list[str]:
        res = copy.deepcopy(base)
        fn(res)  # type: ignore[operator]
        return check_invariants(res, ships)

    facts = lambda r: r["items"]["X"]["facts"]  # noqa: E731
    assert mutated(lambda r: setattr(facts(r)["attended"], "value", Decimal(5)))
    assert mutated(lambda r: setattr(facts(r)["covered"], "value", Decimal(4)))
    assert mutated(lambda r: setattr(facts(r)["uncovered"], "value", Decimal(-1)))
    assert mutated(
        lambda r: r["attribution"]["allocations"].append(dict(r["attribution"]["allocations"][0]))
    )
    assert mutated(lambda r: r["attribution"]["allocations"].clear())
    assert mutated(
        lambda r: r["items"]["X"]["units"].append({"kind": "LINE", "customer_class": "CAT"})
    )
    assert check_invariants(base, [*ships, ships[0]])  # movement_id duplicado


def test_g10_core_does_not_use_wo_routing_capacity_or_forecast() -> None:
    banned = (
        "work_order",
        "routing",
        "labor",
        "capacity",
        "operations_and_progress",
        "wo_stage",
        "forecast",
        "schedul",
    )
    for path in sorted((ROOT / "src/nexus/core").rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        used |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        for name in used:
            assert not any(b in name.lower() for b in banned), (path.name, name)
