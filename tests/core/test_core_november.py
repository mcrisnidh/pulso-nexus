"""Noviembre 2026 — Δ3 (precisión fecha), Δ4 (replay sin embarques) y condiciones C1–C4.

Todo es SINTÉTICO (``physical_fixtures``): mismos formatos físicos que las fuentes reales, con
valores ficticios. El flujo es el de ASMXC1200: ``stage`` → ``refresh --channel replay``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import physical_fixtures as fx
import pytest
from tz_scenarios import state_digest

from nexus.core import demo
from nexus.core.coverage import CoverageInput, check_invariants, compute
from nexus.core.refresh import RefreshService
from nexus.core.rules import core_rules_from_dict
from nexus.core.stage import stage
from nexus.core.state import (
    CoreState,
    FrozenExistsError,
    PublicationRefusedError,
    store_root,
)
from nexus.errors import IntegrityError
from nexus.planning.model import BLOCKED_SOURCE, NOT_CERTIFIABLE, UNKNOWN_NOT_ZERO
from nexus.storage.object_store import LocalObjectStore

NOW = "2026-11-07T18:00:00Z"
UNKNOWN_STATUSES = {BLOCKED_SOURCE, UNKNOWN_NOT_ZERO, NOT_CERTIFIABLE}
SHIPMENT_METRICS = ("attended", "pending", "covered", "uncovered")


class Clock:
    def __init__(self, iso: str = NOW) -> None:
        self.value = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))

    def __call__(self) -> dt.datetime:
        return self.value


def staged(tmp_path: Path, homologation: bool = True) -> dict[str, Path]:
    lab = fx.lab(tmp_path)
    stage(lab["src"], lab["data"], lab["homologation"] if homologation else None, clock=Clock())
    return lab


def svc(data: Path, channel: str = "replay", clock: Clock | None = None) -> RefreshService:
    return RefreshService(data, channel, data / "rules.json", clock=clock or Clock())


def published(service: RefreshService) -> dict[str, Any]:
    head = service.state.publication_head(service.channel)
    assert head is not None
    manifest = service.state.get_manifest(service.channel, head["gen_id"])
    art = manifest["artifacts"]["core.summary"]
    summary = service.state.get_artifact(art["key"], art["sha256"])
    items = {
        item: service.state.get_artifact(e["key"], e["sha256"])
        for item, e in manifest["items"].items()
    }
    return {"head": head, "manifest": manifest, "summary": summary, "items": items}


def restage_extra(lab: dict[str, Path], name: str, data: bytes) -> None:
    (lab["src"] / name).write_bytes(data)
    stage(lab["src"], lab["data"], lab["homologation"], clock=Clock())


# ------------------------------------------------------------------ Δ4 replay parcial
def test_nv01_replay_publishes_informative_view_without_shipments(tmp_path: Path) -> None:
    lab = staged(tmp_path)
    service = svc(lab["data"])
    out = service.refresh("analista")
    assert out["outcomes"][0]["published"] is True
    pub = published(service)
    m, s = pub["manifest"], pub["summary"]
    assert m["publication_scope"] == "INFORMATIVE_NOT_CERTIFIED"
    assert m["validation"] == {
        "status": "PASS",
        "failures": [],
        "invariant_violations": [],
        "partial": ["shipments"],
    }
    assert m["inputs"]["blocked"] == {"shipments": m["inputs"]["blocked"]["shipments"]}
    assert m["sources"]["shipments"] is None
    assert s["blocked_sources"].keys() == {"shipments"}
    rows = s["rows"]
    assert rows["QZXITEM1"]["facts"]["demand"]["value"] == "650"  # TUPY 300 + vencida 50 + DIF 300
    assert rows["QZXITEM1"]["facts"]["fg_assignable"]["value"] == "150"
    for item, row in rows.items():
        for name in SHIPMENT_METRICS:
            assert row["facts"][name]["value"] is None, (item, name)
            assert row["facts"][name]["status"] == BLOCKED_SOURCE
        assert set(row["kpi"].values()) == {"PROVISIONAL"}  # nunca oficial en replay


def test_nv02_operational_channel_gate_intact_with_same_sources(tmp_path: Path) -> None:
    lab = staged(tmp_path)
    svc(lab["data"]).refresh("analista")
    op = svc(lab["data"], "operativo")
    out = op.refresh("operador")
    assert out["head"] is None and out["outcomes"][0]["published"] is False
    failures = out["outcomes"][0]["validation"]["failures"]
    assert any("no hay embarques físicos válidos" in f for f in failures)
    # y el operativo tampoco publica sin Portal (no hay rama parcial fuera de replay)
    shutil.rmtree(lab["data"] / "inbox" / "portal_cat")
    out2 = svc(lab["data"], "operativo").refresh("operador")
    assert out2["head"] is None


def test_nv03_full_branch_unchanged_by_partial_code(tmp_path: Path) -> None:
    """La rama con embarques produce el mismo resultado que antes: mismas invariantes y la
    huella dorada (TZ/portabilidad) cubre los bytes."""
    rules = core_rules_from_dict(demo.RULES)
    base = dict(month="2031-01", cut_ts="2031-01-10T18:00:00Z", oeras=[], portal=[], fg=[])
    full = compute(CoverageInput(**base, shipments=[], rules=rules, certified=False))
    assert "blocked_sources" not in full
    blocked = compute(
        CoverageInput(
            **base, shipments=None, rules=rules, certified=False, blocked={"shipments": "x"}
        )
    )
    assert blocked["blocked_sources"] == {"shipments": "x"}
    assert check_invariants(blocked, []) == []


# ------------------------------------------------------------------ C3 desconocido ≠ 0
def _walk_facts(doc: Any) -> list[dict[str, Any]]:
    out = []
    if isinstance(doc, dict):
        if {"name", "value", "status"} <= doc.keys():
            out.append(doc)
        for v in doc.values():
            out += _walk_facts(v)
    elif isinstance(doc, list):
        for v in doc:
            out += _walk_facts(v)
    return out


def test_nv10_no_unknown_becomes_zero_in_contract(tmp_path: Path) -> None:
    lab = staged(tmp_path)
    service = svc(lab["data"])
    service.refresh("analista")
    pub = published(service)
    facts = _walk_facts(pub["summary"]) + _walk_facts(pub["items"])
    assert facts
    for f in facts:
        if f["status"] in UNKNOWN_STATUSES:
            assert f["value"] is None, f
    for name in SHIPMENT_METRICS:
        total = pub["summary"]["totals"][name]
        assert total["known_sum"] is None and total["complete"] is False
        assert total["blocked_by"] == ["shipments"]
    assert pub["summary"]["totals"]["demand"].get("blocked_by") is None  # no depende de embarques
    assert pub["summary"]["totals"]["items_with_uncovered"] is None
    for item in pub["items"].values():
        for u in item["coverage"]["units"]:
            assert u["attended_exact"] is None and u["pending"] is None
        assert item["coverage"]["line_coverage"]["status"] in ("BLOCKED_CRC", BLOCKED_SOURCE)


def test_nv11_missing_fg_item_and_negative_fg_are_unknown(tmp_path: Path) -> None:
    lab = staged(tmp_path)
    service = svc(lab["data"])
    service.refresh("analista")
    rows = published(service)["summary"]["rows"]
    assert rows["QZXITEM3"]["facts"]["fg_assignable"] == {
        **rows["QZXITEM3"]["facts"]["fg_assignable"],
        "value": None,
        "status": UNKNOWN_NOT_ZERO,
    }  # ausente en IN092U («Excludes Zero»): no es cero
    assert rows["QZXITEM2"]["facts"]["fg_assignable"]["value"] is None  # FG −5
    assert rows["QZXITEM2"]["facts"]["fg_assignable"]["status"] == NOT_CERTIFIABLE


def test_nv12_cat_without_mapped_portal_lines_is_unknown_demand(tmp_path: Path) -> None:
    lab = staged(tmp_path)
    service = svc(lab["data"])
    service.refresh("analista")
    rows = published(service)["summary"]["rows"]
    assert rows["QZXITEM3"]["facts"]["demand"]["value"] is None  # CAT sin Portal homologado
    assert rows["QZXITEM3"]["facts"]["demand"]["status"] == UNKNOWN_NOT_ZERO


def test_nv13_portal_quarantined_blocks_only_cat_demand(tmp_path: Path) -> None:
    lab = staged(tmp_path, homologation=False)  # R1 sin resolver: Portal en cuarentena
    service = svc(lab["data"])
    out = service.refresh("analista")
    assert out["outcomes"][0]["published"] is True
    pub = published(service)
    s = pub["summary"]
    assert s["blocked_sources"].keys() == {"portal_cat", "shipments"}
    assert pub["manifest"]["sources"]["frozen"].get("portal_cat") is None
    item1 = s["rows"]["QZXITEM1"]["facts"]
    assert item1["demand"]["value"] is None  # pool CAT desconocido ⇒ demanda del ITEM desconocida
    item2 = s["rows"]["QZXITEM2"]["facts"]
    assert item2["demand"]["value"] == "240"  # sin líneas CAT: completo
    assert s["totals"]["demand"]["complete"] is False
    assert s["totals"]["demand"]["blocked_by"] == ["portal_cat"]
    limitations = " ".join(item1["demand"]["limitations"])
    assert "Portal CAT no disponible" in limitations


def test_nv14_shipped_blank_and_ambiguous_dates_propagate_unknown(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    rows = [
        ("PL-1", "QZX PORTAL 1", "Open", "400", "", "11/18/2026", "11/20/2026"),
        (
            "PL-4",
            "QZX PORTAL 1",
            "Open",
            "10",
            "0",
            "11/05/2026(11/07/2026)",
            "11/05/2026(11/07/2026)",
        ),
    ]
    (lab["src"] / "Portal_CAT_2026-11-03_0955.csv").write_bytes(fx.portal_bytes(rows=rows))
    stage(lab["src"], lab["data"], lab["homologation"], clock=Clock())
    service = svc(lab["data"])
    service.refresh("analista")
    item = published(service)["items"]["QZXITEM1"]["coverage"]
    assert item["facts"]["demand"]["value"] is None
    reasons = " ".join(r for u in item["units"] for r in u["reasons"])
    assert "cantidad desconocida" in reasons and "sin fecha" in reasons


def test_nv15_missing_open_quantity_propagates_unknown(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    items = [("QZXITEM2", "25", "25", [("7310201", "1", "11/20/26", "120", "", fx.APTIV)])]
    (lab["src"] / "R_20261101.TXT").write_bytes(fx.oe965_bytes(items=items))
    stage(lab["src"], lab["data"], lab["homologation"], clock=Clock())
    service = svc(lab["data"])
    service.refresh("analista")
    facts = published(service)["summary"]["rows"]["QZXITEM2"]["facts"]
    assert facts["demand"]["value"] is None and facts["demand"]["status"] == UNKNOWN_NOT_ZERO


# ------------------------------------------------------------------ C4 identidades exactas
def test_nv20_exact_customer_identity_cat_tupy_aptiv_delphi(tmp_path: Path) -> None:
    lab = staged(tmp_path)
    service = svc(lab["data"])
    service.refresh("analista")
    items = published(service)["items"]
    units1 = items["QZXITEM1"]["coverage"]["units"]
    classes1 = {(u["kind"], u["customer_class"], u["customer_key"]) for u in units1}
    assert ("LINE", "TUPY", fx.TUPY) in classes1  # TUPY se conserva aunque el ITEM tenga CAT
    assert not any(u["kind"] == "LINE" and u["customer_class"] == "CAT" for u in units1)
    assert ("POOL", "CAT", "") in classes1  # CAT solo desde el Portal homologado
    units2 = items["QZXITEM2"]["coverage"]["units"]
    by_key = {u["customer_key"]: u["customer_class"] for u in units2}
    assert by_key[fx.APTIV] == "APTIV" and by_key[fx.DELPHI] == "DELPHI"  # entidades distintas
    assert by_key[fx.CAT_SPACED] != "CAT"  # otra clave (espacios): sin coincidencia aproximada
    assert len(units2) == 3


def test_nv21_provisional_homologation_never_applied(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    rows = [("PL-9", "QZX PORTAL 9", "Open", "80", "0", "11/18/2026", "11/20/2026")]
    (lab["src"] / "Portal_CAT_2026-11-03_0955.csv").write_bytes(fx.portal_bytes(rows=rows))
    report = stage(lab["src"], lab["data"], lab["homologation"], clock=Clock())
    portal = next(f for f in report["files"] if f["kind"] == "portal")
    assert portal["status"] == "QUARANTINED"  # la fila PROVISIONAL no se convierte en regla
    assert report["homologation"]["provisional_ignored"] == 1


# ------------------------------------------------------------------ C1 Frozen inmutable
def test_nv30_frozen_identity_and_first_file_evidence(tmp_path: Path) -> None:
    lab = staged(tmp_path)
    service = svc(lab["data"])
    service.refresh("analista")
    oe = service.state.get_frozen("replay", "2026-11", "oeras_29")
    portal = service.state.get_frozen("replay", "2026-11", "portal_cat")
    assert oe is not None and portal is not None
    raw_oe = (lab["src"] / "R_20261101.TXT").read_bytes()
    raw_portal = (lab["src"] / "Portal_CAT_2026-11-03_0955.csv").read_bytes()
    assert oe["provenance"]["original"]["sha256"] == hashlib.sha256(raw_oe).hexdigest()
    assert portal["provenance"]["original"]["sha256"] == hashlib.sha256(raw_portal).hexdigest()
    assert oe["sha256"] != portal["sha256"]  # identidades independientes
    for rec, day in ((oe, "2026-11-01"), (portal, "2026-11-03")):
        assert rec["instant"] == day and rec["instant_precision"] == "date"
        assert rec["frozen_status"] == "PROVISIONAL"  # precedencia no certificable con solo fecha
        ev = rec["precedence_evidence"]
        assert ev["certified"] is False
        assert ev["observed_valid_files"][0]["sha256"] == rec["sha256"]
        assert rec["provenance"]["times"]["extraction"]["value"] is None
    sidecar = lab["data"] / "provenance" / "oeras_29" / f"{oe['file']}.json"
    assert oe["provenance"]["sidecar_sha256"] == hashlib.sha256(sidecar.read_bytes()).hexdigest()


def test_nv31_later_extract_never_replaces_frozen(tmp_path: Path) -> None:
    lab = staged(tmp_path)
    service = svc(lab["data"])
    service.refresh("analista")
    first = service.state.get_frozen("replay", "2026-11", "oeras_29")
    later_items = [
        (i, p, a, [(*o[:4], "1", o[5]) for o in orders]) for i, p, a, orders in fx.OE_ITEMS
    ]
    restage_extra(lab, "R_20261103.TXT", fx.oe965_bytes(date="11/03/26", items=later_items))
    out = service.refresh("analista")
    assert service.state.get_frozen("replay", "2026-11", "oeras_29") == first
    pub = published(service)
    assert pub["manifest"]["inputs"]["frozen"]["oeras_29"] == first["sha256"]
    assert pub["items"]["QZXITEM1"]["current_vs_frozen"]["changed"] > 0  # Current informativo
    assert out["outcomes"][0]["published"] is True


def test_nv32_same_date_different_content_after_registration_alerts(tmp_path: Path) -> None:
    lab = staged(tmp_path)
    service = svc(lab["data"])
    service.refresh("analista")
    first = service.state.get_frozen("replay", "2026-11", "oeras_29")
    other = fx.oe965_bytes(items=fx.OE_ITEMS[:1])  # misma fecha 11/01/26, otro contenido
    restage_extra(lab, "R_20261101 (2).TXT", other)
    service.refresh("analista")
    assert service.state.get_frozen("replay", "2026-11", "oeras_29") == first
    alerts = service.state.alerts("replay", "2026-11")
    assert [a["kind"] for a in alerts] == ["FROZEN_PRECEDENCE_AMBIGUOUS"]
    assert "no se reemplaza" in alerts[0]["action"]


def test_nv33_ambiguous_precedence_before_registration_blocks(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    (lab["src"] / "R_20261101 (2).TXT").write_bytes(fx.oe965_bytes(items=fx.OE_ITEMS[:1]))
    stage(lab["src"], lab["data"], lab["homologation"], clock=Clock())
    service = svc(lab["data"])
    out = service.refresh("analista")
    assert out["head"] is None
    failures = out["outcomes"][0]["validation"]["failures"]
    assert any("PRECEDENCIA AMBIGUA" in f for f in failures)
    assert service.state.get_frozen("replay", "2026-11", "oeras_29") is None


def test_nv34_identical_duplicates_are_not_ambiguous(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    raw = (lab["src"] / "R_20261101.TXT").read_bytes()
    (lab["src"] / "R_20261101(3).TXT").write_bytes(raw)
    stage(lab["src"], lab["data"], lab["homologation"], clock=Clock())
    assert svc(lab["data"]).refresh("analista")["head"] is not None


def test_nv35_substitution_of_frozen_rejected(tmp_path: Path) -> None:
    lab = staged(tmp_path)
    service = svc(lab["data"])
    service.refresh("analista")
    record = service.state.get_frozen("replay", "2026-11", "oeras_29")
    assert record is not None
    with pytest.raises(FrozenExistsError):
        service.state.register_frozen(
            "replay", "2026-11", "oeras_29", {**record, "sha256": "0" * 64}
        )
    # alterar los bytes del Frozen registrado: falla cerrado, nunca se acepta otro contenido
    key = service.state.frozen_key("replay", "2026-11", "oeras_29")
    path = service.state.store.root / "objects" / key
    path.chmod(0o644)
    path.write_bytes(path.read_bytes().replace(b"PROVISIONAL", b"CERTIFIED__"))
    with pytest.raises(IntegrityError):
        service.state.get_frozen("replay", "2026-11", "oeras_29")


def test_nv36_future_document_date_is_rejected(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    (lab["src"] / "R_20261101.TXT").write_bytes(fx.oe965_bytes(date="11/09/26"))
    stage(lab["src"], lab["data"], lab["homologation"], clock=Clock())
    out = svc(lab["data"]).refresh("analista")
    assert out["head"] is None
    rejected = out["outcomes"][0]["validation"]["failures"]
    assert any("oeras_29" in f for f in rejected)


# ------------------------------------------------------------------ C2 aislamiento del replay
def test_nv40_replay_has_separate_physical_store(tmp_path: Path) -> None:
    lab = staged(tmp_path)
    demo_root = lab["data"]
    # mismo directorio de datos: operativo/sintético en store/, replay en replay/store
    main_state = CoreState(LocalObjectStore(store_root(demo_root, "operativo")))
    before = state_digest(store_root(demo_root, "operativo"), ("",))
    service = svc(demo_root)
    service.refresh("analista")
    assert store_root(demo_root, "replay") != store_root(demo_root, "operativo")
    assert state_digest(store_root(demo_root, "operativo"), ("",)) == before
    assert not list(main_state.store.iter_keys("frozen/"))
    assert not list(main_state.store.iter_keys("gen/"))


def test_nv41_store_lanes_reject_foreign_channels(tmp_path: Path) -> None:
    replay = CoreState.for_channel(tmp_path, "replay")
    main = CoreState.for_channel(tmp_path, "operativo")
    for call in (
        lambda: replay.frozen_key("operativo", "2026-11", "oeras_29"),
        lambda: replay.append_event("operativo", NOW, "X", {}),
        lambda: replay.append_publication("sintetico", 0, {}),
        lambda: replay.publication_head("operativo"),
        lambda: main.gen_prefix("replay", "x"),
        lambda: main.append_publication("replay", 0, {}),
        lambda: main.get_frozen("replay", "2026-11", "oeras_29"),
        lambda: main.put_alert("replay", "2026-11", {"k": 1}),
    ):
        with pytest.raises(ValueError, match="no pertenece a este almacén"):
            call()


def test_nv42_replay_generation_never_publishable_outside_replay(tmp_path: Path) -> None:
    lab = staged(tmp_path)
    service = svc(lab["data"])
    service.refresh("analista")
    gen_id = service.state.publication_head("replay")["gen_id"]
    op = svc(lab["data"], "operativo")
    with pytest.raises(PublicationRefusedError, match="inexistente"):
        op.publish(gen_id, "atacante")
    # adversarial: copiar byte a byte la generación replay al almacén operativo
    src = store_root(lab["data"], "replay") / "objects" / "gen" / "replay" / gen_id
    dst = store_root(lab["data"], "operativo") / "objects" / "gen" / "operativo" / gen_id
    shutil.copytree(src, dst)
    with pytest.raises(PublicationRefusedError):
        op.publish(gen_id, "atacante")
    assert op.state.publication_head("operativo") is None
    sint = svc(lab["data"], "sintetico")
    dst2 = store_root(lab["data"], "sintetico") / "objects" / "gen" / "sintetico" / gen_id
    shutil.copytree(src, dst2)
    with pytest.raises(PublicationRefusedError):
        sint.publish(gen_id, "atacante")


def test_nv43_replay_rollback_does_not_touch_other_channels(tmp_path: Path) -> None:
    lab = staged(tmp_path)
    data = lab["data"]
    # el MISMO directorio de datos con una publicación sintética previa en store/
    demo.scenario(tmp_path / "d")
    RefreshService(
        tmp_path / "d",
        "sintetico",
        tmp_path / "d" / "rules.json",
        clock=Clock("2031-01-10T20:00:00Z"),
    ).refresh("demo")
    shutil.copytree(tmp_path / "d" / "store", data / "store")
    sint_state = CoreState.for_channel(data, "sintetico")
    history_before = sint_state.publication_history("sintetico")
    assert len(history_before) == 1
    main_before = state_digest(store_root(data, "sintetico"), ("",))
    service = svc(data)
    service.refresh("analista")
    first = service.state.publication_head("replay")["gen_id"]
    restage_extra(lab, "R_20261103.TXT", fx.oe965_bytes(date="11/03/26", items=fx.OE_ITEMS[:2]))
    second = service.refresh("analista")["head"]
    assert second["gen_id"] != first and second["seq"] == 2
    service.rollback(first, "admin")
    head = service.state.publication_head("replay")
    assert head["action"] == "ROLLBACK" and head["gen_id"] == first and head["seq"] == 3
    assert service.state.verify_publication_chain("replay") == []
    assert sint_state.publication_history("sintetico") == history_before
    assert state_digest(store_root(data, "sintetico"), ("",)) == main_before


def test_nv44_partial_generation_refused_by_operational_publish(tmp_path: Path) -> None:
    """Defensa en profundidad: aunque una generación parcial apareciera en el almacén
    operativo con canal 'operativo', ``publish`` la rechaza."""
    lab = staged(tmp_path)
    service = svc(lab["data"])
    service.refresh("analista")
    m = published(service)["manifest"]
    assert m["inputs"]["blocked"] and m["publication_scope"] == "INFORMATIVE_NOT_CERTIFIED"
    op = svc(lab["data"], "operativo")
    forged = {**m, "channel": "operativo", "inputs": {**m["inputs"], "channel": "operativo"}}
    op.state.put_sealed(f"gen/operativo/{m['gen_id']}/manifest.json", forged)
    with pytest.raises(PublicationRefusedError):
        op.publish(m["gen_id"], "atacante")


def test_nv45_cli_replay_refresh_and_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from nexus.cli import main

    lab = staged(tmp_path)
    code = main(
        ["core", "refresh", "--data", str(lab["data"]), "--channel", "replay", "--now", NOW]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["head"]["seq"] == 1
    code = main(["core", "status", "--data", str(lab["data"]), "--channel", "replay"])
    status = json.loads(capsys.readouterr().out)
    assert code == 0 and status["chain_problems"] == []
    assert not (lab["data"] / "store" / "objects" / "pub").exists()
