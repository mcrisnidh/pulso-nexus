"""PILOT-20 sobre el dataset REAL (solo local; nunca en CI ni con salidas dentro del repo).

Requiere:
  NEXUS_PILOT20_DATASET  directorio del dataset extraído (fuera del repo)
  NEXUS_PILOT20_RULES    archivo de reglas real (fuera del repo)
  NEXUS_PILOT20_WORKDIR  directorio privado para salidas y copias de prueba (fuera del repo)

Ningún valor esperado está escrito aquí: todos se leen de los oráculos del propio dataset.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from nexus.planning.model import BLOCKED_SOURCE, PROVISIONAL, STATUSES
from nexus.planning.rules import load_rules
from nexus.planning.runner import default_layout, run
from nexus.sealed import unseal

ROOT = Path(__file__).resolve().parents[2]
ENV = {
    k: os.environ.get(k)
    for k in ("NEXUS_PILOT20_DATASET", "NEXUS_PILOT20_RULES", "NEXUS_PILOT20_WORKDIR")
}

pytestmark = pytest.mark.skipif(
    not all(ENV.values()),
    reason="dataset real PILOT-20 no configurado (solo local; datos corporativos fuera de CI)",
)

LAYOUT = json.loads(default_layout().read_text("utf-8"))["sources"]


def _paths() -> tuple[Path, Path, Path]:
    dataset, rules, work = (Path(str(ENV[k])).resolve() for k in ENV)
    for p in (dataset, rules, work):
        assert not p.is_relative_to(ROOT), f"{p} está dentro del repositorio"
    return dataset, rules, work


def _rows(dataset: Path, source: str) -> list[dict[str, str]]:
    spec = LAYOUT[source]
    with (dataset / spec["path"]).open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=spec.get("delimiter", ",")))


@pytest.fixture(scope="module")
def real() -> dict[str, Any]:
    dataset, rules, work = _paths()
    out = work / "test_run_a"
    if out.exists():
        shutil.rmtree(out)
    summary = run(dataset, rules, out)
    docs = {p.stem: unseal(p.read_bytes(), p.name) for p in (out / "items").iterdir()}
    views = {d["view"]["item"]: d for d in docs.values()}
    return {
        "dataset": dataset,
        "rules": rules,
        "work": work,
        "out": out,
        "summary": summary,
        "docs": views,
    }


def _facts(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        if {"name", "status", "provenance"} <= set(node):
            return [node]
        return [f for v in node.values() for f in _facts(v)]
    if isinstance(node, list):
        return [f for v in node for f in _facts(v)]
    return []


def _wos(real: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (w["company"], w["order_no"]): w
        for d in real["docs"].values()
        for w in d["view"]["stages"]["07_operations_progress"]["work_orders"]
    }


def test_r1_items_individual(real: dict[str, Any]) -> None:
    expected = sorted({r["ITEM"] for r in _rows(real["dataset"], "item_universe")})
    assert sorted(real["docs"]) == expected
    assert real["summary"]["items"] == len(expected)
    matrix = list(csv.DictReader((real["out"] / "matrix.csv").open(encoding="utf-8")))
    assert [r["item"] for r in matrix] == expected


def test_r2_sources_connected_and_oracles(real: dict[str, Any]) -> None:
    assert real["summary"]["blocked_sources"] == []
    checks = [o for d in real["docs"].values() for o in d["oracles"]]
    assert checks and all(o["match"] in (True, None) for o in checks), [
        o for o in checks if o["match"] is False
    ]
    kinds = Counter(o["oracle"].split("[")[0].split(".")[0] for o in checks)
    assert kinds["route_end_diagnostic"] == len(_rows(real["dataset"], "route_end_diagnostic"))
    assert kinds["portal_decision"] == sum(
        1 for r in _rows(real["dataset"], "portal_cat") if r.get("Decisión de línea")
    )


def test_r3_no_duplication(real: dict[str, Any]) -> None:
    disc = json.loads((real["out"] / "discrepancies.json").read_text("utf-8"))
    assert all(v == 0 for k, v in disc["integrity"].items() if "duplicate" in k), disc["integrity"]
    labor = _rows(real["dataset"], "labor_groups")
    by_op: dict[tuple[str, str, int], Decimal] = defaultdict(Decimal)
    for r in labor:
        if r["RecordType"] == "D":
            key = (r["Company"], r["WorkOrder"], int(Decimal(r["OperationSequence"])))
            by_op[key] += Decimal(r["OctoberOperationQty"])
    for (company, order), w in _wos(real).items():
        for op in w["operations"]:
            if op["month_d_qty"] is not None and any(
                g["record_type"] == "D" for g in op["month_groups"]
            ):
                assert Decimal(op["month_d_qty"]) == by_op[(company, order, op["seq"])]
    corr = sum(
        len(op["corrections_already_in_production"])
        for w in _wos(real).values()
        for op in w["operations"]
    )
    late = sum(
        len(op["late_adjustments_not_additional"])
        for w in _wos(real).values()
        for op in w["operations"]
    )
    assert corr == len(_rows(real["dataset"], "labor_corrections"))
    assert late == len(_rows(real["dataset"], "late_adjustments"))
    for d in real["docs"].values():
        demand = d["view"]["stages"]["01_demand"]
        for snap in demand["snapshots"]:
            assert snap["duplicate_identities"] == 0


def test_r4_month_activity_wos_traceable(real: dict[str, Any]) -> None:
    expected = {(r["company_cd"], r["order_no"]) for r in _rows(real["dataset"], "wo_oct_activity")}
    traced = {k for k, w in _wos(real).items() if w["month_activity"]}
    assert traced == expected
    for k in traced:
        groups = [g for op in _wos(real)[k]["operations"] for g in op["month_groups"]]
        assert groups and all(g["source"]["line"] > 1 for g in groups)


def test_r5_terminal_candidates_not_certified_fg(real: dict[str, Any]) -> None:
    diag = _rows(real["dataset"], "route_end_diagnostic")
    expected = {
        (r["Company"], r["WorkOrder"]): Decimal(r["OctDQtyAtCurrentLastSeq"])
        for r in diag
        if r["Status"].startswith("OCT_ACTIVITY_AT_ROUTE_END")
    }
    wos = _wos(real)
    got = {k for k, w in wos.items() if w["terminal_candidate"]}
    assert got == set(expected)
    for k in got:
        assert wos[k]["terminal_status"] == "TERMINAL_OPERATION_CANDIDATE_NOT_FG_RECEIPT"
    total = Decimal(0)
    for d in real["docs"].values():
        for f in d["view"]["stages"]["07_operations_progress"]["facts"]:
            if f["name"].startswith("progress.terminal_candidate_qty[") and any(
                f["name"].endswith(f"[{o}]") for _, o in got
            ):
                assert f["status"] == "DIAGNOSTIC_ONLY"
                total += Decimal(f["value"])
        finished = next(
            f
            for f in d["view"]["stages"]["07_operations_progress"]["facts"]
            if f["name"] == "progress.finished_pieces"
        )
        assert finished["value"] is None
    assert total == sum(expected.values())


def test_r6_temporal_cuts(real: dict[str, Any]) -> None:
    for d in real["docs"].values():
        st = d["view"]["stages"]
        frozen = st["01_demand"]["frozen_snapshot"]
        for f in st["05_coverage"]["facts"]:
            assert {p["cut"] for p in f["provenance"]} <= {frozen}
        hist = next(
            f for f in st["05_coverage"]["facts"] if f["name"] == "coverage.wo_coverage_at_frozen"
        )
        assert hist["status"] == "TEMPORAL_MISMATCH"
        for w in st["06_work_orders"]["work_orders"]:
            assert w["status_label"].startswith("ESTADO_ACTUAL@")
        usable = load_rules(real["rules"]).usable_fg_stockrooms
        for r in st["04_fg"]["later_inventory_records"]:
            assert r["usable_fg"] is (r["stockroom"] in usable)
        assert (
            next(
                f for f in st["09_shipments_billing"]["facts"] if f["name"] == "shipments.physical"
            )["status"]
            == BLOCKED_SOURCE
        )
        assert (
            next(f for f in st["08_capacity"]["facts"] if f["name"] == "capacity.delivery_dates")[
                "status"
            ]
            == BLOCKED_SOURCE
        )


def test_r7_status_and_provenance(real: dict[str, Any]) -> None:
    for d in real["docs"].values():
        for f in _facts(d["view"]):
            assert f["status"] in STATUSES
            if f["status"] != PROVISIONAL:
                assert f["limitations"]
            if f["value"] not in (None, {}, []):
                assert f["provenance"] or any("sin registros" in x for x in f["limitations"]), f


@pytest.mark.parametrize("source", ["portal_cat", "labor_groups", "inventory_snapshot"])
def test_r8_missing_source_localized(real: dict[str, Any], source: str) -> None:
    copy = real["work"] / f"test_missing_{source}"
    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(real["dataset"], copy / "dataset")
    (copy / "dataset" / LAYOUT[source]["path"]).unlink()
    try:
        summary = run(copy / "dataset", real["rules"], copy / "out")
        assert summary["blocked_sources"] == [source]
        assert summary["items"] == real["summary"]["items"]
        for p in (copy / "out" / "items").iterdir():
            st = unseal(p.read_bytes(), p.name)["view"]["stages"]
            assert st["01_demand"]["status"] != BLOCKED_SOURCE
            blocked = [
                f
                for s in st.values()
                for f in s["facts"]
                if f["status"] == BLOCKED_SOURCE and source in " ".join(f["limitations"])
            ]
            assert blocked, p.name
    finally:
        shutil.rmtree(copy)  # la copia de datos reales no se conserva


def test_r9_deterministic(real: dict[str, Any]) -> None:
    out = real["work"] / "test_run_b"
    if out.exists():
        shutil.rmtree(out)
    again = run(real["dataset"], real["rules"], out)
    assert again["run_digest"] == real["summary"]["run_digest"]
    for name in ("matrix.csv", "discrepancies.json", "run_manifest.json", "index.html"):
        assert (out / name).read_bytes() == (real["out"] / name).read_bytes()
    shutil.rmtree(out)


def test_r10_same_code_no_manual_exceptions(real: dict[str, Any]) -> None:
    items = {r["ITEM"] for r in _rows(real["dataset"], "item_universe")}
    keys = {r["customer_key_exact"].strip() for r in _rows(real["dataset"], "oe965_backlog")}
    code = "\n".join(p.read_text("utf-8") for p in sorted((ROOT / "src").rglob("*.py")))
    code += "\n".join(p.read_text("utf-8") for p in sorted((ROOT / "src").rglob("*.json")))
    assert not [i for i in items if i in code]
    assert not [k for k in keys if len(k) > 4 and k in code]
    stage_sets = {tuple(d["view"]["stages"]) for d in real["docs"].values()}
    assert len(stage_sets) == 1  # mismas etapas para los 20 ITEM
