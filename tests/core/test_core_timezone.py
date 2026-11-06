"""B0-7 — calendario operativo (America/Mexico_City), identidad de generación y rollback seguro.

Solo datos sintéticos. TZ-01…TZ-18 son los casos del diseño; ID-* cubren la identidad
criptográfica de la zona en cada generación no UTC; RB-* el rechazo de cambios de zona que
reinterpretarían el historial y el procedimiento seguro.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from tz_scenarios import UTC_BASELINE_DIGEST, Clock, state_digest, utc_baseline_scenario

from nexus.core import demo
from nexus.core.coverage import CoverageInput, check_invariants, compute
from nexus.core.cs import Association, CsStore, board_state
from nexus.core.refresh import RefreshService, ensure_tz_binding, load_operation
from nexus.core.rules import core_rules_from_dict
from nexus.core.sources import Shipment
from nexus.core.state import CoreState, PublicationRefusedError
from nexus.core.timebase import BusinessCalendar, TimezoneBindingError
from nexus.errors import ConfigurationError
from nexus.hashing import digest_json, sha256_bytes
from nexus.planning.model import SourceRef
from nexus.storage.object_store import LocalObjectStore

ROOT = Path(__file__).resolve().parents[2]
MX = "America/Mexico_City"
ORDERS = [
    ("SYN-A", demo.OTHER, "000901", "1", "2031-01-20", "100"),
    ("SYN-A", demo.TUPY, "000902", "1", "2031-01-25", "50"),
]
FEB_ORDERS = [("SYN-A", demo.OTHER, "000911", "1", "2031-02-20", "80")]


# ------------------------------------------------------------------ auxiliares
def fresh(root: Path, tz: str | None = None) -> Path:
    """Directorio con reglas y usuarios sintéticos, bandeja vacía y operation.json opcional."""
    demo.scenario(root)
    for p in (root / "inbox").rglob("*.csv"):
        p.unlink()
    if tz is not None:
        write_operation(root, tz)
    return root


def write_operation(root: Path, tz: str) -> None:
    (root / "operation.json").write_text(
        json.dumps({"schema": "nexus.core.operation/1", "business_tz": tz}), "utf-8"
    )


def cut(
    root: Path,
    local: str,
    name: str,
    orders: list[Any],
    ships: list[Any],
    portal_date: str = "2031-01-20",
) -> None:
    """Deposita 29 OERAS + Portal + FG + embarques con el mismo instante local ``local``."""
    demo.drop(root, "oeras_29", f"R_{name}.csv", demo.oeras(local, orders))
    demo.drop(
        root,
        "portal_cat",
        f"P_{name}.csv",
        demo.portal(local, [("SYN-A", f"PA-{name}", "Open", "10", "0", portal_date)]),
    )
    demo.drop(root, "fg_inventory", f"FG_{name}.csv", demo.fg(local, [("SYN-A", "FG", "5")]))
    filler = ("FILL-" + name, "SYN-Z", demo.OTHER, "", "", "1", local, "SHIP")  # sin demanda
    demo.drop(root, "shipments", f"S_{name}.csv", demo.shipments(local, [*ships, filler]))


def service(root: Path, clock: Clock, channel: str = "sintetico") -> RefreshService:
    return RefreshService(root, channel, root / "rules.json", clock)


def tree(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def summary(svc: RefreshService) -> dict[str, Any]:
    head = svc.state.publication_head("sintetico")
    assert head is not None
    m = svc.state.get_manifest("sintetico", head["gen_id"])
    art = m["artifacts"]["core.summary"]
    return svc.state.get_artifact(art["key"], art["sha256"])


# ------------------------------------------------------------------ TZ-01…TZ-06 calendario
def test_tz01_month_boundary() -> None:
    mx, utc = BusinessCalendar(MX), BusinessCalendar("UTC")
    instant = "2031-02-01T05:30:00Z"  # 31 ene 2031 23:30 en Ciudad de México
    assert mx.month_of(instant) == "2031-01" and utc.month_of(instant) == "2031-02"
    assert mx.month_of("2031-02-01T06:00:00Z") == "2031-02"


def test_tz02_year_boundary() -> None:
    mx = BusinessCalendar(MX)
    assert mx.month_of("2032-01-01T05:59:59Z") == "2031-12"  # 31 dic 23:59:59 local
    assert mx.month_of("2032-01-01T06:00:00Z") == "2032-01"  # 1 ene 00:00 local
    assert mx.window_utc("2031-12") == ("2031-12-01T06:00:00Z", "2032-01-01T06:00:00Z")


def test_tz03_leap_february_window() -> None:
    assert BusinessCalendar(MX).window_utc("2032-02") == (
        "2032-02-01T06:00:00Z",
        "2032-03-01T06:00:00Z",
    )
    assert BusinessCalendar("UTC").window_utc("2032-02") == (
        "2032-02-01T00:00:00Z",
        "2032-03-01T00:00:00Z",
    )


def test_tz04_historical_dst_rules_are_applied() -> None:
    mx = BusinessCalendar(MX)  # 2021: México aún tenía horario de verano (UTC−5 en mayo)
    assert mx.window_utc("2021-05")[0] == "2021-05-01T05:00:00Z"
    assert mx.window_utc("2021-12")[0] == "2021-12-01T06:00:00Z"
    assert BusinessCalendar("UTC").window_utc("2021-05")[0] == "2021-05-01T00:00:00Z"


@pytest.mark.parametrize(
    "process_tz", ["Pacific/Chatham", "Asia/Kolkata", "America/Mexico_City", "UTC"]
)
def test_tz05_independent_of_process_timezone(process_tz: str) -> None:
    code = (
        "from nexus.core.timebase import BusinessCalendar as B;c=B('America/Mexico_City');"
        "print(c.month_of('2031-02-01T05:30:00Z'), c.window_utc('2031-02')[0],"
        " B('UTC').month_of('2031-02-01T05:30:00Z'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "TZ": process_tz, "PYTHONPATH": str(ROOT / "src")},
    )
    assert out.stdout.split() == ["2031-01", "2031-02-01T06:00:00Z", "2031-02"]


@pytest.mark.parametrize("bad", ["Mars/Olympus", "local", "", "../etc/passwd", "UTC+6"])
def test_tz06_invalid_timezone_fails_closed(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ConfigurationError):
        BusinessCalendar(bad)
    root = fresh(tmp_path / "d")
    write_operation(root, bad)
    with pytest.raises(ConfigurationError):
        service(root, Clock("2031-01-10T20:00:00Z"))


def test_tz06_operation_file_schema_strict(tmp_path: Path) -> None:
    root = fresh(tmp_path / "d")
    for content in (
        '{"business_tz": "UTC"}',
        "no es json",
        '{"schema": "nexus.core.operation/1", "business_tz": "UTC", "x": 1}',
    ):
        (root / "operation.json").write_text(content, "utf-8")
        with pytest.raises(ConfigurationError):
            load_operation(root)
    with pytest.raises(ConfigurationError):
        BusinessCalendar(MX).month_of("2031-02-01T05:30:00")  # instante sin zona


# ------------------------------------------------------------------ TZ-07…TZ-09 Frozen
def test_tz07_frozen_by_local_date_of_the_cut(tmp_path: Path) -> None:
    clock = Clock("2031-02-01T05:50:00Z")  # 31 ene 23:50 local
    for tz, expected in ((MX, "2031-01"), ("UTC", "2031-02")):
        root = fresh(tmp_path / tz.replace("/", "_"), tz)
        cut(root, "2031-01-31T23:30:00-06:00", "a", ORDERS, [])
        svc = service(root, clock)
        assert svc.current_month() == expected
        out = svc.refresh("t")
        assert out["head"] is not None and out["head"]["month"] == expected
        assert svc.state.get_frozen("sintetico", expected, "oeras_29")["file"] == "R_a.csv"


def test_tz08_first_extract_of_new_local_month_and_previous_intact(tmp_path: Path) -> None:
    root = fresh(tmp_path / "d", MX)
    clock = Clock("2031-02-01T05:50:00Z")
    cut(root, "2031-01-31T23:30:00-06:00", "jan", ORDERS, [])
    svc = service(root, clock)
    svc.refresh("t")
    jan = svc.state.get_frozen("sintetico", "2031-01", "oeras_29")
    cut(root, "2031-02-01T00:30:00-06:00", "feb", FEB_ORDERS, [], "2031-02-20")
    clock.set("2031-02-01T07:30:00Z")  # 1 feb 01:30 local
    svc = service(root, clock)  # reinicio
    out = svc.refresh("t")
    assert out["head"]["month"] == "2031-02"
    assert svc.state.get_frozen("sintetico", "2031-02", "oeras_29")["file"] == "R_feb.csv"
    assert svc.state.get_frozen("sintetico", "2031-01", "oeras_29") == jan
    assert jan["business_tz"] == MX


def test_tz09_frozen_never_reinterpreted_even_bypassing_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fresh(tmp_path / "d", MX)
    clock = Clock("2031-01-20T20:00:00Z")
    cut(root, "2031-01-10T12:00:00-06:00", "a", ORDERS, [])
    svc = service(root, clock)
    svc.refresh("t")
    before = tree(root / "store" / "objects" / "frozen")
    head = svc.state.publication_head("sintetico")
    write_operation(root, "America/Bogota")  # misma fecha local, otra zona
    monkeypatch.setattr(CoreState, "effective_tz", lambda self, ch: None)  # defensa en profundidad
    clock.set("2031-01-20T21:00:00Z")
    other = service(root, clock)
    out = other.refresh("t")
    assert out["outcomes"][0]["published"] is False
    assert "ZONA HORARIA" in " ".join(out["outcomes"][0]["validation"]["failures"])
    assert tree(root / "store" / "objects" / "frozen") == before
    assert other.state.publication_head("sintetico") == head


# ------------------------------------------------------------------ TZ-10…TZ-13 embarques
REF = SourceRef("t", "t.csv", "0" * 64, 2, None, "t")


def _ship(mid: str, when: str, qty: str = "10", typ: str = "SHIP", rev: str = "") -> Shipment:
    from decimal import Decimal

    return Shipment(
        mid, "SYN-A", demo.OTHER, "000901", "1", Decimal(qty), when, typ, rev, "EA", REF
    )


def _coverage(ships: list[Shipment], window: str | None, cut_ts: str) -> dict[str, Any]:
    from decimal import Decimal

    from nexus.core.sources import FgRecord, OerasLine

    rules = core_rules_from_dict(demo.RULES)
    oeras = [
        OerasLine("SYN-A", demo.OTHER, "N", "000901", "1", "2031-01-20", None, Decimal(100), REF)
    ]
    fg = [FgRecord("SYN-A", "FG", Decimal(5), None, REF)]
    res = compute(CoverageInput("2031-01", cut_ts, oeras, [], fg, ships, rules, False, window))
    assert check_invariants(res, ships) == []
    return res


def test_tz10_tz11_shipment_window_from_local_midnight() -> None:
    ships = [
        _ship("A", "2031-02-01T01:00:00Z"),  # 31 ene 19:00 local → enero
        _ship("B", "2031-01-01T06:30:00Z"),  # 1 ene 00:30 local → enero
        _ship("C", "2031-01-01T05:30:00Z"),
    ]  # 31 dic 23:30 local → diciembre
    cut_ts = "2031-02-01T05:45:00Z"  # 31 ene 23:45 local
    mx = _coverage(ships, BusinessCalendar(MX).window_utc("2031-01")[0], cut_ts)
    assert sorted(a["movement_id"] for a in mx["attribution"]["allocations"]) == ["A", "B"]
    assert [e["movement_id"] for e in mx["attribution"]["excluded"]] == ["C"]
    utc = _coverage(ships, None, cut_ts)  # comportamiento previo: medianoche UTC
    assert sorted(a["movement_id"] for a in utc["attribution"]["allocations"]) == ["A", "B", "C"]


def test_tz12_reversal_crossing_the_month_boundary(tmp_path: Path) -> None:
    root = fresh(tmp_path / "d", MX)
    ships = [
        ("R1", "SYN-A", demo.OTHER, "000901", "1", "30", "2031-01-31T22:00:00-06:00", "SHIP"),
        (
            "R2",
            "SYN-A",
            demo.OTHER,
            "000901",
            "1",
            "30",
            "2031-02-01T01:00:00-06:00",
            "REVERSAL:R1",
        ),
    ]
    cut(root, "2031-01-31T23:45:00-06:00", "jan", ORDERS, ships)
    clock = Clock("2031-02-01T05:50:00Z")
    svc = service(root, clock)
    svc.refresh("t")
    jan = summary(svc)
    assert jan["month"] == "2031-01"
    assert jan["rows"]["SYN-A"]["facts"]["attended"]["value"] == "30"  # la reversa queda fuera
    feb_orders = [("SYN-A", demo.OTHER, "000901", "1", "2031-02-20", "100")]
    cut(root, "2031-02-01T08:00:00-06:00", "feb", feb_orders, ships, "2031-02-20")
    clock.set("2031-02-01T15:00:00Z")
    svc.refresh("t")
    feb = summary(svc)
    assert feb["month"] == "2031-02"
    assert feb["rows"]["SYN-A"]["facts"]["attended"]["value"] == "0"
    reasons = {e["reason"] for e in feb["attribution"]["excluded"]}
    assert "reversa en ventana de un embarque fuera de ventana" in reasons  # sin doble conteo


def test_tz13_night_cut_assigned_to_local_month_and_aligned(tmp_path: Path) -> None:
    root = fresh(tmp_path / "d", MX)
    cut(
        root,
        "2031-01-31T23:45:00-06:00",
        "a",
        ORDERS,
        [("S1", "SYN-A", demo.OTHER, "000901", "1", "10", "2031-01-31T20:00:00-06:00", "SHIP")],
    )
    svc = service(root, Clock("2031-02-01T05:55:00Z"))
    out = svc.refresh("t")
    m = svc.state.get_manifest("sintetico", out["head"]["gen_id"])
    assert m["validation"]["status"] == "PASS" and m["month"] == "2031-01"
    assert m["sources"]["fg"]["instant"] == m["sources"]["shipments"]["instant"]


# ------------------------------------------------------------------ TZ-14, TZ-15
def test_tz14_cs_monthly_validity_follows_local_month(tmp_path: Path) -> None:
    root = fresh(tmp_path / "d", MX)
    clock = Clock("2031-02-01T05:30:00Z")  # 31 ene 23:30 local
    svc = service(root, clock)
    cs = CsStore(LocalObjectStore(root / "cs"), svc.rules.cs_status_vocabulary)
    a = Association("SYN-A")
    cs.append(
        a,
        "CS_STATUS",
        "EN_RIESGO",
        author="editorcs",
        expected_version=0,
        server_ts="2031-02-01T05:30:00Z",
        month=svc.current_month(),
    )
    assert cs.history(a.thread_id)[0]["valid_month"] == "2031-01"
    clock.set("2031-02-01T06:30:00Z")  # 1 feb 00:30 local
    state = board_state(cs.history(a.thread_id), service(root, clock).current_month())
    assert state["current"] == {} and state["reference"]["CS_STATUS"]["value"] == "EN_RIESGO"
    utc_month = BusinessCalendar("UTC").month_of("2031-02-01T05:30:00Z")
    assert utc_month == "2031-02"  # con UTC habría quedado en febrero


def test_tz14_web_uses_operational_month_without_interface_change(tmp_path: Path) -> None:
    from nexus.web import create_app

    root = fresh(tmp_path / "d", MX)
    app = create_app(
        root, "sintetico", "s" * 40, clock=Clock("2031-02-01T05:30:00Z"), background=False
    )
    assert app.extensions["nexus"]["service"].current_month() == "2031-01"


def test_tz15_operativo_channel_uses_local_month(tmp_path: Path) -> None:
    clock = Clock("2031-02-01T05:00:00Z")  # 31 ene 23:00 local
    mx = service(fresh(tmp_path / "mx", MX), clock, "operativo")
    assert mx.request("t", month="2031-01")["created"]
    with pytest.raises(PublicationRefusedError):
        mx.request("t", month="2031-02")
    utc = service(fresh(tmp_path / "utc"), clock, "operativo")
    assert utc.request("t", month="2031-02")["created"]
    with pytest.raises(PublicationRefusedError):
        utc.request("t", month="2031-01")


# ------------------------------------------------------------------ TZ-16…TZ-18 determinismo
def _gen_tree(svc: RefreshService) -> dict[str, str]:
    base = svc.state.store.root / "objects" / "gen"
    return {
        p.relative_to(base).as_posix(): sha256_bytes(p.read_bytes())
        for p in sorted(base.rglob("*"))
        if p.is_file()
    }


def _run(root: Path, tz: str | None) -> RefreshService:
    fresh(root, tz)
    cut(
        root,
        "2031-01-10T12:00:00-06:00",
        "a",
        ORDERS,
        [("S1", "SYN-A", demo.OTHER, "000901", "1", "10", "2031-01-05T10:00:00-06:00", "SHIP")],
    )
    svc = service(root, Clock("2031-01-20T20:00:00Z"))
    svc.refresh("t")
    return svc


def test_tz16_deterministic_same_zone(tmp_path: Path) -> None:
    a, b = _run(tmp_path / "a", MX), _run(tmp_path / "b", MX)
    assert (
        a.state.publication_head("sintetico")["gen_id"]
        == b.state.publication_head("sintetico")["gen_id"]
    )
    assert _gen_tree(a) == _gen_tree(b)


def test_tz17_zone_changes_generation_identity(tmp_path: Path) -> None:
    ids = {
        tz: _run(
            tmp_path / tz.replace("/", "_"), None if tz == "UTC" else tz
        ).state.publication_head("sintetico")["gen_id"]
        for tz in ("UTC", MX, "America/Bogota")
    }
    assert len(set(ids.values())) == 3  # sin colisiones entre configuraciones


def test_tz18_utc_compatibility_byte_for_byte_with_f404f96(tmp_path: Path) -> None:
    fresh_utc = utc_baseline_scenario(tmp_path / "fresh")  # instalación nueva sin operation.json
    assert state_digest(fresh_utc.state.store.root) == UTC_BASELINE_DIGEST
    for rec in fresh_utc.state.list_frozen("sintetico"):
        assert "business_tz" not in rec
    for pub in fresh_utc.state.publication_history("sintetico"):
        assert "business_tz" not in pub
    # Un almacén nuevo registra su zona (UTC) como único metadato adicional:
    assert sorted(fresh_utc.state.store.iter_keys("calendar/")) == [
        "calendar/sintetico/binding.json"
    ]
    assert fresh_utc.state.get_tz_binding("sintetico")["business_tz"] == "UTC"
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    write_operation(explicit, "UTC")  # UTC explícito = sin archivo
    s = utc_baseline_scenario(explicit)
    assert state_digest(s.state.store.root) == UTC_BASELINE_DIGEST


def test_tz18_legacy_store_without_binding_stays_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Almacén previo a B0-7 (emulado: sin vínculo) que sigue operando con el código nuevo."""
    root = tmp_path / "legacy"
    with monkeypatch.context() as m:
        m.setattr(CoreState, "bind_tz", lambda self, ch, rec: rec)  # f404f96 no escribía vínculo
        utc_baseline_scenario(root)
    clock = Clock("2031-02-11T20:00:00Z")
    svc = service(root, clock)  # código nuevo, sin operation.json
    demo.drop(
        root,
        "shipments",
        "S_extra.csv",
        demo.shipments(
            "2031-02-08T18:00:00Z",
            [("MV-20", "SYN-A", demo.TUPY, "000105", "1", "150", "2031-02-05T09:00:00Z", "SHIP")],
        ),
    )
    svc.refresh("t")
    assert not any(svc.state.store.iter_keys("calendar/"))  # ningún archivo nuevo de zona
    assert svc.state.effective_tz("sintetico") == "UTC"  # ligado implícitamente por historial


# ------------------------------------------------------------------ ID-* identidad de generación
def test_id01_zone_cryptographically_bound_to_generation(tmp_path: Path) -> None:
    svc = _run(tmp_path / "d", MX)
    head = svc.state.publication_head("sintetico")
    m = svc.state.get_manifest("sintetico", head["gen_id"])
    assert m["inputs"]["calendar"] == {
        "business_tz": MX,
        "month": "2031-01",
        "window_utc": ["2031-01-01T06:00:00Z", "2031-02-01T06:00:00Z"],
    }
    assert digest_json(m["inputs"])[:32] == head["gen_id"]  # gen_id = H(entradas con la zona)
    raw = svc.state.store.get_self_verifying(
        f"gen/sintetico/{head['gen_id']}/manifest.json", lambda _b: None
    )
    assert sha256_bytes(raw) == head["manifest_sha256"]  # publicación → manifiesto sellado
    assert head["business_tz"] == MX
    assert svc.state.get_frozen("sintetico", "2031-01", "oeras_29")["business_tz"] == MX
    assert svc.state.get_tz_binding("sintetico")["business_tz"] == MX
    assert svc.state.verify_publication_chain("sintetico") == []


def test_id02_no_reuse_of_generation_from_other_zone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _run(tmp_path / "d", MX)
    gen = svc.state.publication_head("sintetico")["gen_id"]
    monkeypatch.setattr(CoreState, "effective_tz", lambda self, ch: None)
    svc.calendar = BusinessCalendar("America/Bogota")
    with pytest.raises(PublicationRefusedError, match="se calculó con la zona"):
        svc.publish(gen, "admin", action="ROLLBACK")


def test_id03_idempotency_key_includes_zone(tmp_path: Path) -> None:
    mx = service(fresh(tmp_path / "mx", MX), Clock("2031-01-20T20:00:00Z"))
    utc = service(fresh(tmp_path / "utc"), Clock("2031-01-20T20:00:00Z"))
    assert mx.request("t")["key"].endswith(f":tz={MX}")
    assert ":tz=" not in utc.request("t")["key"]


# ------------------------------------------------------------------ RB-* rollback seguro
def test_rb01_removing_operation_json_is_rejected_history_intact(tmp_path: Path) -> None:
    from nexus.web import create_app

    root = tmp_path / "d"
    _run(root, MX)
    cs = CsStore(LocalObjectStore(root / "cs"), tuple(demo.RULES["cs_status_vocabulary"]))
    cs.append(
        Association("SYN-A"),
        "NOTE",
        "seguimiento",
        author="editorcs",
        expected_version=0,
        server_ts="2031-01-20T20:00:00Z",
        month="2031-01",
    )
    before = tree(root)
    (root / "operation.json").unlink()
    with pytest.raises(TimezoneBindingError, match="ligado a la zona America/Mexico_City"):
        service(root, Clock("2031-01-21T20:00:00Z"))
    with pytest.raises(TimezoneBindingError):
        create_app(root, "sintetico", "s" * 40, background=False)
    write_operation(root, MX)  # restaurar la configuración correcta: vuelve a operar
    after_restore = tree(root)
    assert before == after_restore  # nada del historial cambió durante el rechazo
    svc = service(root, Clock("2031-01-21T20:00:00Z"))
    assert svc.state.publication_head("sintetico")["business_tz"] == MX


def test_rb02_legacy_utc_history_rejects_new_zone(tmp_path: Path) -> None:
    root = tmp_path / "legacy"
    utc_baseline_scenario(root)
    before = tree(root)
    write_operation(root, MX)
    with pytest.raises(TimezoneBindingError, match="ligado a la zona UTC"):
        service(root, Clock("2031-02-11T20:00:00Z"))
    (root / "operation.json").unlink()
    assert tree(root) == before
    service(root, Clock("2031-02-11T20:00:00Z"))  # sin cambio de zona: opera normalmente


def test_rb03_change_between_non_utc_zones_rejected(tmp_path: Path) -> None:
    root = tmp_path / "d"
    _run(root, MX)
    write_operation(root, "America/Bogota")
    with pytest.raises(TimezoneBindingError):
        service(root, Clock("2031-01-21T20:00:00Z"))


def test_rb04_restarts_and_year_transition_keep_frozen_immutable(tmp_path: Path) -> None:
    root = fresh(tmp_path / "d", MX)
    dec = [("SYN-A", demo.OTHER, "000921", "1", "2031-12-20", "40")]
    jan = [("SYN-A", demo.OTHER, "000931", "1", "2032-01-20", "60")]
    clock = Clock("2032-01-01T05:50:00Z")  # 31 dic 2031 23:50 local
    cut(root, "2031-12-31T23:30:00-06:00", "dec", dec, [], "2031-12-20")
    service(root, clock).refresh("t")
    f_dec = service(root, clock).state.get_frozen("sintetico", "2031-12", "oeras_29")
    assert f_dec["file"] == "R_dec.csv"
    again = service(root, clock).refresh("t")  # reinicio, mismas fuentes: sin publicación nueva
    assert again["head"]["seq"] == 1
    cut(root, "2032-01-01T00:30:00-06:00", "jan", jan, [], "2032-01-20")
    clock.set("2032-01-01T07:00:00Z")
    out = service(root, clock).refresh("t")
    assert out["head"]["month"] == "2032-01"
    svc = service(root, clock)
    assert svc.state.get_frozen("sintetico", "2031-12", "oeras_29") == f_dec
    assert svc.state.get_frozen("sintetico", "2032-01", "oeras_29")["file"] == "R_jan.csv"
    assert svc.state.verify_publication_chain("sintetico") == []


def test_rb05_safe_procedure_new_data_dir_preserves_history(tmp_path: Path) -> None:
    old = tmp_path / "old"
    utc_baseline_scenario(old)
    CsStore(LocalObjectStore(old / "cs"), tuple(demo.RULES["cs_status_vocabulary"])).append(
        Association("SYN-A"),
        "NOTE",
        "historial",
        author="editorcs",
        expected_version=0,
        server_ts="2031-02-10T20:00:00Z",
        month="2031-02",
    )
    before = tree(old)
    new = fresh(tmp_path / "new", MX)  # procedimiento: directorio NUEVO con la zona nueva
    shutil.copytree(old / "cs", new / "cs")  # historial CS conservado (append-only, sellado)
    cut(
        new,
        "2031-03-01T08:00:00-06:00",
        "mar",
        [("SYN-A", demo.OTHER, "000941", "1", "2031-03-20", "10")],
        [],
        "2031-03-20",
    )
    svc = service(new, Clock("2031-03-01T20:00:00Z"))
    assert svc.refresh("t")["head"]["business_tz"] == MX
    cs = CsStore(LocalObjectStore(new / "cs"), tuple(demo.RULES["cs_status_vocabulary"]))
    assert cs.history(Association("SYN-A").thread_id)[0]["value"] == "historial"
    assert tree(old) == before  # el directorio anterior queda intacto (archivo)


def test_rb06_fresh_store_without_history_may_choose_zone(tmp_path: Path) -> None:
    root = fresh(tmp_path / "d")
    svc = service(root, Clock("2031-01-20T20:00:00Z"))
    assert svc.state.effective_tz("sintetico") is None  # sin estado: nada que reinterpretar
    write_operation(root, MX)
    service(root, Clock("2031-01-20T20:00:00Z"))


def test_rb07_concurrent_binding_single_winner(tmp_path: Path) -> None:
    state = CoreState(LocalObjectStore(tmp_path / "s"))
    barrier = threading.Barrier(6)
    results: list[str] = []

    def go(tz: str) -> None:
        barrier.wait()
        try:
            ensure_tz_binding(
                state, "sintetico", BusinessCalendar(tz), "2031-01", "2031-01-01T06:00:00Z", "t"
            )
            results.append(tz)
        except TimezoneBindingError:
            results.append("REJECTED")

    zones = [MX, "UTC"] * 3  # incluye UTC: también compite por el vínculo
    threads = [threading.Thread(target=go, args=(z,)) for z in zones]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winner = state.get_tz_binding("sintetico")["business_tz"]
    assert results.count("REJECTED") == 3 and results.count(winner) == 3


def test_rb08_tampered_binding_detected(tmp_path: Path) -> None:
    from nexus.errors import IntegrityError

    root = tmp_path / "d"
    _run(root, MX)
    p = root / "store" / "objects" / "calendar" / "sintetico" / "binding.json"
    p.chmod(0o644)
    p.write_bytes(p.read_bytes().replace(b"Mexico_City", b"Bogota_Cityx"))
    with pytest.raises(IntegrityError):
        service(root, Clock("2031-01-21T20:00:00Z"))
