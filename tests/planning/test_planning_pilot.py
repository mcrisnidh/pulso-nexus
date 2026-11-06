"""PILOT-20 (sintético): reglas de integración, bloqueos localizados y determinismo.

Corre en CI y localmente sin datos reales. Los mismos 10 controles se repiten sobre el dataset
real en ``test_planning_real.py`` (solo local, condicionado por variable de entorno).
"""

from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from synthetic_pilot import ITEMS, build

from nexus.cli import main
from nexus.planning.model import (
    BLOCKED_SOURCE,
    PROVISIONAL,
    STATUSES,
    TEMPORAL_MISMATCH,
    UNKNOWN_NOT_ZERO,
    Fact,
)
from nexus.planning.runner import default_layout, run
from nexus.sealed import unseal

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def pilot(tmp_path: Path) -> dict[str, Any]:
    dataset, rules = build(tmp_path)
    out = tmp_path / "out"
    summary = run(dataset, rules, out)
    views = {}
    for item in ITEMS:
        doc = unseal((out / "items" / f"{item}.json").read_bytes(), item)
        views[item] = doc
    return {"dataset": dataset, "rules": rules, "out": out, "summary": summary, "docs": views}


def _view(p: dict[str, Any], item: str) -> dict[str, Any]:
    return p["docs"][item]["view"]  # type: ignore[no-any-return]


def _fact(view: dict[str, Any], stage: str, name: str) -> dict[str, Any]:
    return next(f for f in view["stages"][stage]["facts"] if f["name"] == name)


def _all_facts(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        if {"name", "status", "provenance"} <= set(node):
            return [node]
        return [f for v in node.values() for f in _all_facts(v)]
    if isinstance(node, list):
        return [f for v in node for f in _all_facts(v)]
    return []


# 1. Los ITEM aparecen individualmente -----------------------------------------------------
def test_v1_each_item_has_its_own_view(pilot: dict[str, Any]) -> None:
    assert pilot["summary"]["items"] == len(ITEMS)
    for item in ITEMS:
        view = _view(pilot, item)
        assert view["item"] == item
        assert list(view["stages"]) == [
            "01_demand",
            "02_portal_cat",
            "03_substitution",
            "04_fg",
            "05_coverage",
            "06_work_orders",
            "07_operations_progress",
            "08_capacity",
            "09_shipments_billing",
            "10_manual_inputs",
            "11_final_shortage",
        ]
    matrix = (pilot["out"] / "matrix.csv").read_text("utf-8").splitlines()
    assert [line.split(",")[0] for line in matrix[1:]] == sorted(ITEMS)


# 2. El motor conecta fuentes -----------------------------------------------------------------
def test_v2_sources_connected_per_item(pilot: dict[str, Any]) -> None:
    view = _view(pilot, "SYN-A")
    used = {p["source_id"] for f in _all_facts(view) for p in f["provenance"]}
    assert {
        "oe965_backlog",
        "oe965_fg",
        "portal_cat",
        "inventory_snapshot",
        "wo_header",
        "routing",
        "labor_groups",
        "billing",
    } <= used
    assert pilot["summary"]["oracle_mismatches"] == 0
    assert pilot["summary"]["portal_decision_mismatches"] == 0


# 3. Sin duplicar demanda, WO, operaciones ni correcciones ------------------------------------
def test_v3_demand_cut_month_and_classes(pilot: dict[str, Any]) -> None:
    view = _view(pilot, "SYN-A")
    demand = _fact(view, "01_demand", "demand.open_due_by_month_end@2059-01-01")
    assert demand["status"] == PROVISIONAL and demand["value"] == "200"  # 100+50+30+20
    assert len(demand["provenance"][0]["lines"]) == 4
    sub = view["stages"]["03_substitution"]
    assert (
        _fact(view, "03_substitution", "substitution.oe965_frozen_open_class_to_replace")["value"]
        == "150"
    )
    assert _fact(view, "03_substitution", "substitution.oe965_frozen_open_preserved_classes")[
        "value"
    ] == {"OTHER": "20", "TUPY": "30"}
    combined = _fact(view, "03_substitution", "substitution.combined_demand")
    assert combined["status"] == TEMPORAL_MISMATCH and combined["value"] is None
    assert sub["status"] == TEMPORAL_MISMATCH
    evo = view["stages"]["01_demand"]["evolution_vs_frozen"]
    assert evo == [{"snapshot": "2059-01-05", "added": 0, "removed": 3, "changed": 1}]


def test_v3_operations_never_summed_and_corrections_not_added(pilot: dict[str, Any]) -> None:
    view = _view(pilot, "SYN-A")
    qty = _fact(view, "07_operations_progress", "progress.terminal_candidate_qty[W0001]")
    assert qty["value"] == "80"  # solo la operación 30 (D), no 90+85+80+5, ni +12, ni +10
    finished = _fact(view, "07_operations_progress", "progress.finished_pieces")
    assert finished["value"] is None and finished["status"] == "NOT_CERTIFIABLE"
    wo = view["stages"]["07_operations_progress"]["work_orders"][0]
    ops = {o["seq"]: o for o in wo["operations"]}
    assert ops[20]["month_d_qty"] == "85"
    assert ops[20]["corrections_already_in_production"][0]["corr_id"] == "C1"
    assert ops[30]["late_adjustments_not_additional"][0]["qty"] == "12"
    assert ops[30]["month_d_qty"] == "80"


def test_v3_portal_rules(pilot: dict[str, Any]) -> None:
    view = _view(pilot, "SYN-A")
    lines = {x["line_id"]: x for x in view["stages"]["02_portal_cat"]["lines"]}
    assert {k: v["decision"] for k, v in lines.items()} == {
        "L1": "INCLUIDO",
        "L2": "EXCLUIDO_ESTADO",
        "L3": "NO_CUANTIFICABLE",
        "L4": "NO_CUANTIFICABLE",
        "L5": "EXCLUIDO_DIF",
    }
    assert lines["L1"]["dif"] == "100"
    assert lines["L3"]["dif"] is None and lines["L4"]["dif"] is None  # desconocido ≠ 0
    assert _fact(view, "02_portal_cat", "portal.included_dif_sum")["value"] == "100"
    complete = _fact(view, "02_portal_cat", "portal.cat_demand_complete")
    assert complete["status"] == UNKNOWN_NOT_ZERO and complete["value"] is None


# 4. WO con actividad del mes trazables -------------------------------------------------------
def test_v4_month_activity_wos_traceable(pilot: dict[str, Any]) -> None:
    traced = {
        w["order_no"]: w
        for item in ITEMS
        for w in _view(pilot, item)["stages"]["07_operations_progress"]["work_orders"]
        if w["month_activity"]
    }
    assert set(traced) == {"W0001", "W0002"}
    for w in traced.values():
        for op in w["operations"]:
            for g in op["month_groups"]:
                assert g["source"]["file"].endswith(".tsv") and g["source"]["line"] > 1


# 5. Candidatas en operación final ≠ FG certificado -------------------------------------------
def test_v5_terminal_candidates_are_not_fg(pilot: dict[str, Any]) -> None:
    wos = {
        w["order_no"]: w
        for item in ITEMS
        for w in _view(pilot, item)["stages"]["07_operations_progress"]["work_orders"]
    }
    assert wos["W0001"]["terminal_status"] == "TERMINAL_OPERATION_CANDIDATE_NOT_FG_RECEIPT"
    assert wos["W0002"]["terminal_candidate"] is False
    assert "25" in " ".join(wos["W0001"]["limitations"])  # operación fuera del routing actual
    fg = _view(pilot, "SYN-A")["stages"]["04_fg"]
    names = {f["name"] for f in fg["facts"]}
    assert not any("candidate" in n for n in names)


# 6. Cortes temporales ------------------------------------------------------------------------
def test_v6_temporal_cuts_respected(pilot: dict[str, Any]) -> None:
    a = _view(pilot, "SYN-A")
    cov = _fact(a, "05_coverage", "coverage.fg_gap_at_frozen")
    assert cov["value"] == "140"  # 200 − FG disponible del MISMO corte (60), no 500 ni 70
    assert {p["cut"] for p in cov["provenance"]} == {"2059-01-01"}
    later = _fact(a, "04_fg", "fg.usable_inventory@2059-02-01T10:00:00")
    assert later["value"] == "70"  # solo stockroom FG; QF se lista aparte
    recs = {r["stockroom"]: r["usable_fg"] for r in a["stages"]["04_fg"]["later_inventory_records"]}
    assert recs == {"FG": True, "QF": False}
    wo_hist = _fact(a, "06_work_orders", "wo.open_qty_at_frozen")
    assert wo_hist["status"] == TEMPORAL_MISMATCH
    status = a["stages"]["06_work_orders"]["work_orders"][0]["status_label"]
    assert status.startswith("ESTADO_ACTUAL@2059-02-05")
    b = _view(pilot, "SYN-B")
    assert _fact(b, "05_coverage", "coverage.fg_gap_at_frozen")["status"] == "NOT_CERTIFIABLE"
    c = _view(pilot, "SYN-C")
    unknown = _fact(c, "04_fg", "fg.usable_inventory@2059-02-01T10:00:00")
    assert unknown["status"] == UNKNOWN_NOT_ZERO and unknown["value"] is None
    d = _view(pilot, "SYN-D")
    dd = _fact(d, "01_demand", "demand.open_due_by_month_end@2059-01-01")
    assert dd["status"] == UNKNOWN_NOT_ZERO and dd["value"] is None


def test_v6_billing_is_not_shipment_and_no_dates(pilot: dict[str, Any]) -> None:
    a = _view(pilot, "SYN-A")
    assert _fact(a, "09_shipments_billing", "billing.net_qty")["value"] == "20"
    assert _fact(a, "09_shipments_billing", "shipments.physical")["status"] == BLOCKED_SOURCE
    assert _fact(a, "08_capacity", "capacity.delivery_dates")["status"] == BLOCKED_SOURCE
    assert _fact(a, "11_final_shortage", "final.shortage")["status"] == BLOCKED_SOURCE


# 7. Procedencia y estado de certeza en todo cálculo ------------------------------------------
def test_v7_every_fact_has_status_and_provenance(pilot: dict[str, Any]) -> None:
    for item in ITEMS:
        for f in _all_facts(_view(pilot, item)):
            assert f["status"] in STATUSES
            if f["status"] != PROVISIONAL:
                assert f["limitations"], f
            if f["value"] not in (None, {}, []):
                assert f["provenance"] or any("sin registros" in x for x in f["limitations"]), f
            for p in f["provenance"]:
                assert re.fullmatch(r"[0-9a-f]{64}", p["sha256"]) and p["lines"]


def test_v7_fact_invariants() -> None:
    with pytest.raises(ValueError, match="no puede llevar valor"):
        Fact("x", 1, BLOCKED_SOURCE, [], ["sin fuente"])
    with pytest.raises(ValueError, match="explicación"):
        Fact("x", None, UNKNOWN_NOT_ZERO, [], [])


# 8. Fuentes ausentes → bloqueos localizados --------------------------------------------------
@pytest.mark.parametrize(
    ("source", "blocked_stage", "intact_stage"),
    [
        ("portal_cat", "02_portal_cat", "01_demand"),
        ("labor_groups", None, "05_coverage"),
        ("oe965_backlog", "01_demand", "07_operations_progress"),
        ("billing", "09_shipments_billing", "04_fg"),
    ],
)
def test_v8_missing_source_blocks_only_its_stages(
    tmp_path: Path, source: str, blocked_stage: str | None, intact_stage: str
) -> None:
    dataset, rules = build(tmp_path)
    layout = json.loads(default_layout().read_text("utf-8"))
    (dataset / layout["sources"][source]["path"]).unlink()
    summary = run(dataset, rules, tmp_path / "out")
    assert source in summary["blocked_sources"]
    assert summary["items"] == len(ITEMS)
    doc = unseal((tmp_path / "out" / "items" / "SYN-A.json").read_bytes(), "SYN-A")
    stages = doc["view"]["stages"]
    if blocked_stage:
        assert stages[blocked_stage]["status"] == BLOCKED_SOURCE
        assert any(source in " ".join(f["limitations"]) for f in stages[blocked_stage]["facts"])
    assert stages[intact_stage]["status"] != BLOCKED_SOURCE
    if source == "labor_groups":
        facts = stages["07_operations_progress"]["facts"]
        assert any(f["status"] == BLOCKED_SOURCE for f in facts)


def test_v8_bad_columns_block_source_not_run(tmp_path: Path) -> None:
    dataset, rules = build(tmp_path)
    layout = json.loads(default_layout().read_text("utf-8"))
    path = dataset / layout["sources"]["routing"]["path"]
    text = path.read_text("utf-8").replace("seq_numb", "seq_renamed", 1)
    path.write_text(text, "utf-8")
    summary = run(dataset, rules, tmp_path / "out")
    assert "routing" in summary["blocked_sources"]
    disc = json.loads((tmp_path / "out" / "discrepancies.json").read_text("utf-8"))
    assert "seq_numb" in disc["sources"]["routing"]["reason"]


# 9. Determinismo -----------------------------------------------------------------------------
def test_v9_deterministic_and_reproducible(tmp_path: Path) -> None:
    dataset, rules = build(tmp_path)
    first = run(dataset, rules, tmp_path / "o1")
    moved = tmp_path / "copy"
    shutil.copytree(dataset, moved)
    second = run(moved, rules, tmp_path / "o2")
    assert first["run_digest"] == second["run_digest"]
    for name in ("matrix.csv", "discrepancies.json", "run_manifest.json", "index.html"):
        assert (tmp_path / "o1" / name).read_bytes() == (tmp_path / "o2" / name).read_bytes()
    for f in sorted((tmp_path / "o1" / "items").iterdir()):
        assert f.read_bytes() == (tmp_path / "o2" / "items" / f.name).read_bytes()


# 10. Mismo código para todos los ITEM, sin excepciones manuales ------------------------------
def test_v10_no_item_or_customer_literals_in_engine() -> None:
    """Las reglas por identidad viven en el archivo de reglas; el código no contiene ITEM,
    claves de cliente ni nombres comerciales como literales (docstrings aparte)."""
    src = ROOT / "src" / "nexus" / "planning"
    literals: list[str] = []
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        docstrings = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
        }
        literals += [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
        ]
    joined = "\n".join(literals)
    assert not re.search(r"\b\d{3}-\d{4}\b", joined), "códigos tipo ITEM en el motor"
    assert not re.search(r"^[A-Z0-9]{3,8} {2,}\d{3}$", joined, re.M), "claves de cliente"
    for word in ("CATERPILLAR", "TUPY", "PERK"):
        assert word not in joined.upper(), word


def test_v10_cli_refuses_output_inside_repo(tmp_path: Path) -> None:
    dataset, rules = build(tmp_path)
    code = main(
        [
            "planning",
            "--dataset",
            str(dataset),
            "--rules",
            str(rules),
            "--out",
            str(ROOT / "tmp_planning_out"),
        ]
    )
    assert code == 1
    assert not (ROOT / "tmp_planning_out").exists()
    assert (
        main(
            [
                "planning",
                "--dataset",
                str(dataset),
                "--rules",
                str(rules),
                "--out",
                str(tmp_path / "ok"),
                "--item",
                "SYN-B",
            ]
        )
        == 0
    )
    assert sorted(p.name for p in (tmp_path / "ok" / "items").iterdir()) == ["SYN-B.json"]
