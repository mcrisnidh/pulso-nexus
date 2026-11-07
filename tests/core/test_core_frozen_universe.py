"""Frozen definitivo, universo mensual, bloqueo de publicación y separación v1/v2 (T1–T3).

FROZEN DEFINITIVO = (29 OERAS Frozen − demanda CAT ERP) + Portal CAT Frozen depurado.
UNIVERSO = ITEM distintos del FROZEN DEFINITIVO. Solo datos sintéticos, directorios temporales.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from functools import cache
from pathlib import Path
from typing import Any

import frozen_scenarios as fs
import physical_fixtures as fx
import pytest

from nexus.core import demo
from nexus.core.refresh import RefreshService
from nexus.core.stage import stage

NOW = "2026-11-07T18:00:00Z"


class Clock:
    def __init__(self, iso: str = NOW) -> None:
        self.value = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))

    def __call__(self) -> dt.datetime:
        return self.value


@cache
def _universe() -> dict[str, object]:
    return fs.universe_scenarios()


@cache
def _frozen() -> dict[str, object]:
    return fs.frozen_scenarios()


# ------------------------------------------------------------------ 33 controles puros
@pytest.mark.parametrize("control", sorted(fs.universe_scenarios()))
def test_pure_universe_controls(control: str) -> None:
    assert _universe()[control] is True, control


@pytest.mark.parametrize("control", sorted(fs.frozen_scenarios()))
def test_pure_frozen_controls(control: str) -> None:
    assert _frozen()[control] is True, control


# ------------------------------------------------------------------ utilidades Refresh
def v2_rules(**over: Any) -> dict[str, Any]:
    r = json.loads(json.dumps(demo.RULES))
    r.update(
        schema="nexus.core.rules/2",
        unclassified_customers="UNKNOWN",
        version="core-synthetic-v2",
        customer_classes={
            "CAT": [demo.CAT],
            "CAT_REVIEW": [demo.REVIEW],
            "TUPY": [demo.TUPY],
            "OTHER": [demo.OTHER],
        },
    )
    r.update(over)
    return r


def synthetic(root: Path, rules: dict[str, Any]) -> RefreshService:
    demo.scenario(root)
    demo.write_lf(root / "rules.json", json.dumps(rules, indent=1))
    return RefreshService(
        root, "sintetico", root / "rules.json", clock=Clock("2031-01-10T20:00:00Z")
    )


def outcome(svc: RefreshService) -> dict[str, Any]:
    out = svc.refresh("t")
    return {"head": out["head"], **out["outcomes"][0]}


# ------------------------------------------------------------------ T1 bloqueo de publicación
def test_t1_unknown_customer_blocks_official_publication(tmp_path: Path) -> None:
    rules = v2_rules()
    rules["customer_classes"].pop("OTHER")  # SYNOTH queda sin clasificación explícita
    svc = synthetic(tmp_path, rules)
    o = outcome(svc)
    assert o["head"] is None and o["published"] is False
    assert any(f.startswith("UNCLASSIFIED_CUSTOMER") for f in o["validation"]["failures"])


def test_t1_all_classified_publishes(tmp_path: Path) -> None:
    o = outcome(synthetic(tmp_path, v2_rules()))
    assert o["published"] is True and o["head"]["seq"] == 1


def test_t1_pending_r1_blocks_official_publication(tmp_path: Path) -> None:
    svc = synthetic(tmp_path, v2_rules())
    rows = [
        {
            "capture_ts": "2031-01-02T10:55:00Z",
            "source_system": "SYNTHETIC",
            "item": item,
            "item_portal": portal,
            "line_id": lid,
            "status": "Open",
            "ordered_raw": "40",
            "shipped_raw": "0",
            "ship_date": "2031-01-20",
            "delivery_date": "2031-01-20",
        }
        for item, portal, lid in (("SYN-A", "SYN A", "PA-1"), ("", "SYN PEND", "PA-9"))
    ]
    (tmp_path / "inbox/portal_cat/P_20310102_1055.csv").write_bytes(demo._csv("portal_cat", rows))
    o = outcome(svc)
    assert o["head"] is None
    assert any(f.startswith("PENDING_R1") for f in o["validation"]["failures"])


# ------------------------------------------------------------------ T2 separación v1 / v2
def test_t2_v1_rules_rejected_with_real_sources_in_any_channel(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    v1 = json.loads(json.dumps(demo.RULES))
    demo.write_lf(lab["data"] / "rules.json", json.dumps(v1))
    stage(lab["src"], lab["data"], clock=Clock(), **fx.r1(lab))
    for channel in ("replay", "sintetico"):  # el canal no elude la separación
        svc = RefreshService(lab["data"], channel, lab["data"] / "rules.json", clock=Clock())
        o = outcome(svc)
        assert o["head"] is None, channel
        assert any(f.startswith("REGLAS v1") for f in o["validation"]["failures"]), channel


def test_t2_v1_rules_only_in_isolated_synthetic_channel(tmp_path: Path) -> None:
    demo.scenario(tmp_path / "d")
    replay = RefreshService(
        tmp_path / "d", "replay", tmp_path / "d" / "rules.json", clock=Clock("2031-01-10T20:00:00Z")
    )
    o = outcome(replay)
    assert o["head"] is None and any(f.startswith("REGLAS v1") for f in o["validation"]["failures"])
    sint = RefreshService(
        tmp_path / "d",
        "sintetico",
        tmp_path / "d" / "rules.json",
        clock=Clock("2031-01-10T20:00:00Z"),
    )
    assert outcome(sint)["published"] is True  # histórico sintético aislado: intacto


# ------------------------------------------------------------------ T3 fuentes actuales y externos
def _official(svc: RefreshService) -> dict[str, Any]:
    head = svc.state.publication_head("replay")
    m = svc.state.get_manifest("replay", head["gen_id"])
    art = m["artifacts"]["core.summary"]
    s = svc.state.get_artifact(art["key"], art["sha256"])

    def clean(x: Any) -> Any:  # sin la huella de los ARCHIVOS (cambian si cambia el archivo)
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if k not in ("sha256", "file", "lines")}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x

    return {
        "rows": clean(s["rows"]),
        "totals": clean(s["totals"]),
        "frozen": {k: v["sha256"] for k, v in m["sources"]["frozen"].items()},
        "construction": s["universe"]["frozen_construction"],
        "members": s["universe"]["members"],
        "excluded": s["universe"]["excluded_records"],
    }


def _lab_with(tmp: Path, externals: bool) -> RefreshService:
    lab = fx.lab(tmp)
    if externals:
        # externos DENTRO del grupo FG (un grupo repetido es fallo cerrado de IN092U)
        rows_in = [*fx.IN_ROWS[:2], ("QZXEXT01", "FG", "900.000"), ("QZXITEM3", "FG", "77.000")]
        rows_in += fx.IN_ROWS[2:]
        (lab["src"] / "INV_STA289.TXT").write_bytes(fx.in092u_bytes(rows=rows_in))
        later = [  # OE965 ACTUAL: ITEM externo, ITEM nuevo TUPY y línea CAT de un ITEM congelado
            ("QZXEXT02", "1", "1", [("7399001", "1", "11/12/26", "500", "500", fx.TUPY)]),
            ("QZXITEM1", "150", "150", [("7399002", "1", "11/14/26", "777", "777", fx.CAT)]),
            ("QZXITEM3", "0", "0", [("7399003", "1", "11/16/26", "888", "888", fx.CAT)]),
        ]
        (lab["src"] / "R_20261104.TXT").write_bytes(fx.oe965_bytes(date="11/04/26", items=later))
        portal_now = [("PL-77", "QZX EXT PORTAL", "Open", "600", "0", "11/25/2026", "11/26/2026")]
        (lab["src"] / "Portal_CAT_2026-11-05_0800.csv").write_bytes(
            fx.portal_bytes(rows=portal_now)
        )
    stage(lab["src"], lab["data"], clock=Clock(), **fx.r1(lab))
    svc = RefreshService(lab["data"], "replay", lab["data"] / "rules.json", clock=Clock())
    svc.refresh("t")
    return svc


def test_t3_external_items_in_current_sources_never_change_official_results(
    tmp_path: Path,
) -> None:
    base = _official(_lab_with(tmp_path / "a", externals=False))
    ext = _official(_lab_with(tmp_path / "b", externals=True))
    assert base["frozen"] == ext["frozen"]  # el Frozen no cambia
    assert base["members"] == ext["members"] == 2  # el universo no se amplía
    assert base["construction"] == ext["construction"]
    assert base["rows"] == ext["rows"]  # KPI por ITEM idénticos
    assert base["totals"] == ext["totals"]
    assert "QZXEXT01" not in ext["rows"] and "QZXEXT02" not in ext["rows"]
    assert "QZXITEM3" not in ext["rows"]  # solo-CAT retirado: el CAT actual no lo reintroduce
    assert ext["excluded"]["fg_inventory"] == 2  # externos solo en el diagnóstico


def test_t3_current_portal_pending_identity_does_not_block_or_extend(tmp_path: Path) -> None:
    svc = _lab_with(tmp_path, externals=True)
    head = svc.state.publication_head("replay")
    m = svc.state.get_manifest("replay", head["gen_id"])
    assert "no_go" not in m["validation"]  # la identidad pendiente es del Portal ACTUAL
    assert "QZX EXT PORTAL" not in json.dumps(m["sources"]["frozen"])


def test_t3_frozen_item_absent_later_keeps_membership(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    stage(lab["src"], lab["data"], clock=Clock(), **fx.r1(lab))
    svc = RefreshService(lab["data"], "replay", lab["data"] / "rules.json", clock=Clock())
    svc.refresh("t")
    only_item2 = [i for i in fx.OE_ITEMS if i[0] == "QZXITEM2"]
    later = tmp_path / "later"
    shutil.copytree(lab["src"], later)
    (later / "R_20261105.TXT").write_bytes(fx.oe965_bytes(date="11/05/26", items=only_item2))
    for f in later.glob("R_20261101*"):
        f.unlink()
    stage(later, lab["data"], clock=Clock(), **fx.r1(lab))
    svc.refresh("t")
    head = svc.state.publication_head("replay")
    m = svc.state.get_manifest("replay", head["gen_id"])
    assert "QZXITEM1" in m["items"]  # ausente en el actual: sigue en el universo Frozen
    assert head["seq"] == 2
