"""Certificación adversarial R1 (homologación Portal→ERP) y R3 (clasificación comercial).

Batería de 62 casos (CERT_R1_R3, sobre 7200fcf) incorporada al repositorio. Cada prueba afirma el
comportamiento REQUERIDO (fail-closed). SOLO datos sintéticos (prefijo QZX).

Adaptaciones registradas (solo donde la especificación cambió por decisiones aprobadas; las
aserciones de rechazo se conservan):
- [D-D] R1-01…R1-06, R1-09…R1-11, R1-16, R1-17: tabla v2 (``approval_id``) verificada contra el
  registro autorizado y la lista de aprobadores; los estados v1 (``APPROVED``) ya no existen.
- [D-E] R1-15: «muchos-a-uno con advertencia» pasa a «pendiente salvo equivalencia de revisiones».
- [K-4/D-B] las reglas de la batería pasan a ``nexus.core.rules/2`` (estricta); R3-v1 documenta
  que el comportamiento histórico v1 solo queda en la ejecución sintética aislada.
- [D-R3-02] R3-10: la clave en dos clases se rechaza AL CARGAR (antes, al usarse).
"""

from __future__ import annotations

import json
import unicodedata
from decimal import Decimal
from typing import Any

import physical_fixtures as fx
import pytest

from nexus.core import demo
from nexus.core.coverage import CoverageInput, check_invariants, compute
from nexus.core.physical import default_layout, read_portal
from nexus.core.rules import core_rules_from_dict
from nexus.core.sources import FgRecord, OerasLine, Shipment
from nexus.core.stage import R1Resolution
from nexus.core.stage import parse_homologation as parse_v2
from nexus.planning.adapters.sources import PortalLine
from nexus.planning.model import SourceRef

L = default_layout()
NAME = "Portal_CAT_2026-11-03_0955.csv"
HDR = "item_portal,item_erp,status,approval_id\n"
ERP_UNIVERSE = frozenset({"QZXITEM1", "QZXITEM2", "QZXITEM3", "QZXITEM4", "QZXITEM9"})


def ledger(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "nexus.core.approvals-ledger/1", "entries": list(entries)}


def entry(aid: str, portal: str, erp: str, kind: str = "R1_MAP", **kw: object) -> dict[str, Any]:
    return fx.ledger_entry(aid, kind, [portal], erp, **kw)


# Registro por defecto: aprobaciones verificables para las filas que usan las pruebas
DEFAULT_LEDGER = ledger(
    entry("A1", "QZX P1", "QZXITEM1"),
    entry("A2", "QZX P2", "QZXITEM2"),
    entry("A3", "QZX P3", "QZXITEM3"),
    entry("A9", "QZX P1", "QZXITEM9"),
    entry("AC", "QZX, P1", "QZXITEM1"),
)


def parse_homologation(
    text: str, led: dict[str, Any] | None = None
) -> tuple[dict[str, str], dict[str, Any]]:
    res: R1Resolution = parse_v2(
        text,
        DEFAULT_LEDGER if led is None else led,
        frozenset({fx.APPROVER}),
        "2026-11-07",
        ERP_UNIVERSE,
    )
    return res.mapping, res.report


CAT, TUPY, APTIV, DELPHI = "QZXCAT    001", "QZXTUP    000", "QZXAPT    001", "QZXDEL    001"
REVIEW, OTHER = "QZXREV    000", "QZXOTH    001"


def ref(n: int = 1, src: str = "oeras_29") -> SourceRef:
    return SourceRef(src, "f.csv", "0" * 64, n, "2026-11-01", "x")


def rules(**over: object) -> dict[str, object]:
    r = json.loads(json.dumps(demo.RULES))
    r["schema"] = "nexus.core.rules/2"  # [K-4] reglas estrictas (fuentes reales)
    r["unclassified_customers"] = "UNKNOWN"
    r["customer_classes"] = {
        "CAT": [CAT],
        "CAT_REVIEW": [REVIEW],
        "TUPY": [TUPY],
        "APTIV": [APTIV],
        "DELPHI": [DELPHI],
        "OTHER_EXPLICIT": [OTHER],
    }
    r["preserve_classes"] = ["TUPY", "CAT_REVIEW"]
    r["customer_notes"] = {REVIEW: "revisión"}
    r.update(over)
    return r


def oeras(item: str, cust: str, order: str, qty: str, due: str = "2026-11-20") -> OerasLine:
    return OerasLine(item, cust, "SINTETICO", order, "1", due, Decimal(qty), Decimal(qty), ref())


def portal(item: str, line_id: str, ordered: str, shipped: str = "0") -> PortalLine:
    return PortalLine(
        item=item,
        item_portal=f"P-{item}",
        line_id=line_id,
        status="Open",
        ordered_raw=ordered,
        shipped_raw=shipped,
        ship_date="2026-11-18",
        delivery_date="2026-11-20",
        map_status="",
        provided_decision="",
        ref=ref(1, "portal_cat"),
    )


def ship(mid: str, item: str, cust: str, order: str, qty: str) -> Shipment:
    return Shipment(
        mid,
        item,
        cust,
        order,
        "1",
        Decimal(qty),
        "2026-11-05T15:00:00Z",
        "SHIP",
        "",
        "EA",
        ref(1, "shipments"),
    )


def run(
    o: list[OerasLine],
    p: list[PortalLine] | None,
    s: list[Shipment] | None = None,
    r: dict[str, object] | None = None,
):
    return compute(
        CoverageInput(
            month="2026-11",
            cut_ts="2026-11-06T15:48:14Z",
            oeras=o,
            portal=p,
            fg=[FgRecord(x.item, "FG", Decimal(0), None, ref(1, "fg_inventory")) for x in o],
            shipments=s if s is not None else [],
            rules=core_rules_from_dict(r or rules()),
            certified=False,
        )
    )


def attended(unit: dict) -> Decimal:  # type: ignore[type-arg]
    return Decimal(unit["attended_exact"]) + Decimal(unit["attended_aggregate"])


def demand(result: dict, item: str) -> object:  # type: ignore[type-arg]
    return result["items"][item]["facts"]["demand"].value


# =================================================================== R1 homologación
@pytest.mark.parametrize("rule", ["R1-01"])
def test_r1_01_only_approved_rows_are_applied(rule: str) -> None:
    m, rep = parse_homologation(
        HDR + "QZX P1,QZXITEM1,APPROVED_MAP,A1\nQZX P2,QZXITEM2,PROVISIONAL,\n"
    )
    assert m == {"QZX P1": "QZXITEM1"} and rep["pending"] == {"PROVISIONAL_NOT_APPLIED": 1}


def test_r1_02_one_portal_item_to_two_erp_items_rejects_whole_table() -> None:
    m, rep = parse_homologation(
        HDR + "QZX P1,QZXITEM1,APPROVED_MAP,A1\nQZX P1,QZXITEM2,APPROVED_MAP,A2\n"
        "QZX P3,QZXITEM3,APPROVED_MAP,A3\n"
    )
    assert m == {} and rep["errors"]


def test_r1_03_approved_plus_provisional_conflict_rejects_table() -> None:
    m, rep = parse_homologation(
        HDR + "QZX P1,QZXITEM1,APPROVED_MAP,A1\nQZX P1,QZXITEM9,PROVISIONAL,\n"
    )
    assert m == {} and rep["errors"]


@pytest.mark.parametrize(
    "status", ["approved", "Approved", "APPROVED_MAP ", "OK", "", "APROBADO", "APPROVED"]
)
def test_r1_04_non_canonical_status_rejects_table(status: str) -> None:
    m, rep = parse_homologation(HDR + f"QZX P1,QZXITEM1,{status},A1\n")
    assert m == {} and rep["errors"]


@pytest.mark.parametrize(
    "header",
    [
        "item_erp,item_portal,status,approval_id\n",
        "item_portal,item_erp,status\n",
        "item_portal,item_erp,status,approval_id,extra\n",
        "\ufeffitem_portal,item_erp,status,approval_id\n",
        "item_portal,item_erp,status,evidence\n",  # tabla v1: ya no se acepta (D-D)
    ],
)
def test_r1_05_unexpected_header_rejects_table(header: str) -> None:
    m, rep = parse_homologation(header + "QZX P1,QZXITEM1,APPROVED_MAP,A1\n")
    assert m == {} and rep["errors"]


@pytest.mark.parametrize("row", [",QZXITEM1,APPROVED_MAP,A1", "QZX P1,,APPROVED_MAP,A1"])
def test_r1_06_empty_identity_rejects_table(row: str) -> None:
    m, rep = parse_homologation(HDR + row + "\n")
    assert m == {} and rep["errors"]


VARIANTS = {
    "minúsculas": "qzx portal 1",
    "guiones": "QZX-PORTAL-1",
    "doble espacio": "QZX  PORTAL 1",
    "sin espacios": "QZXPORTAL1",
    "cero inicial": "QZX PORTAL 01",
    "NBSP": "QZX PORTAL 1",
    "ancho completo": "ＱＺＸ PORTAL 1",
    "cirílico": "QZХ PORTAL 1",  # X cirílica
    "NFD": unicodedata.normalize("NFD", "QZX PORTÁL 1"),
    "revisión": "QZX PORTAL 1 REV B",
}


@pytest.mark.parametrize("label", list(VARIANTS))
def test_r1_07_no_fuzzy_association_on_portal_identity(label: str) -> None:
    mapping = {"QZX PORTAL 1": "QZXITEM1", unicodedata.normalize("NFC", "QZX PORTÁL 1"): "QZXITEM4"}
    rows = [("PL-1", VARIANTS[label], "Open", "5", "0", "11/18/2026", "11/20/2026")]
    r = read_portal(fx.portal_bytes(rows=rows), L, NAME, mapping)
    assert r.records[0]["item"] == "", label  # nunca se asocia por parecido
    assert "UNMAPPED_ITEM" in {e.code for e in r.exceptions}


def test_r1_08_erp_revisions_are_distinct_identities() -> None:
    o = [oeras("QZXITEM1", CAT, "7300001", "10"), oeras("QZXITEM1-A", CAT, "7300002", "20")]
    res = run(o, [portal("QZXITEM1", "PL-1", "40")])
    assert demand(res, "QZXITEM1") == Decimal(40)
    assert demand(res, "QZXITEM1-A") is None  # su CAT no se toma del ITEM sin revisión
    assert set(res["items"]) == {"QZXITEM1", "QZXITEM1-A"}


@pytest.mark.parametrize("erp", [" QZXITEM1", "QZXITEM1 ", "QZXITEM1\t"])
def test_r1_09_erp_identity_with_surrounding_whitespace_is_rejected(erp: str) -> None:
    """Una identidad ERP con espacios exteriores no existe en OE965/IN092U (rstrip del ancho
    fijo): aplicarla crearía un ITEM fantasma con demanda CAT. Debe rechazarse."""
    m, rep = parse_homologation(HDR + f'QZX P1,"{erp}",APPROVED_MAP,A1\n')
    assert m == {} and rep["errors"]


@pytest.mark.parametrize("cell", ['"QZX\nP1"', '"QZX\rP1"', '"QZX\x00P1"'])
def test_r1_10_control_characters_in_identity_rejected(cell: str) -> None:
    m, rep = parse_homologation(HDR + f"{cell},QZXITEM1,APPROVED_MAP,A1\n")
    assert m == {} and rep["errors"]


@pytest.mark.parametrize(
    "bad",
    [
        {"evidence_ref": ""},
        {"evidence_sha256": ""},
        {"evidence_sha256": "abc"},
        {"approved_by": "alguien.no.autorizado"},
        {"approved_at": ""},
        {"approved_at": "2026-11-30"},  # posterior a la recepción
        {"approved_at": "05/11/2026"},
    ],
)
def test_r1_11_approved_row_requires_verifiable_evidence(bad: dict[str, str]) -> None:
    """[D-D] Una homologación sin evidencia verificable en el registro autorizado no es una
    aprobación: el registro se rechaza y la tabla no aplica ninguna fila."""
    led = ledger(entry("A1", "QZX P1", "QZXITEM1", **bad))
    m, rep = parse_homologation(HDR + "QZX P1,QZXITEM1,APPROVED_MAP,A1\n", led)
    assert m == {} and rep["errors"]


def test_r1_11b_approved_text_alone_is_not_evidence() -> None:
    """[D-D] La palabra APPROVED en la tabla editable sin entrada coincidente en el registro no
    homologa: la fila queda PENDIENTE."""
    for row in ("QZX P1,QZXITEM1,APPROVED_MAP,NO-EXISTE", "QZX P1,QZXITEM2,APPROVED_MAP,A1"):
        m, rep = parse_homologation(HDR + row + "\n")
        assert m == {} and rep["pending"] == {"APPROVAL_NOT_IN_LEDGER": 1}


def test_r1_12_item_id_is_not_homologation() -> None:
    rows = [("PL-1", "QZXITEM1", "Open", "5", "0", "11/18/2026", "11/20/2026")]  # Item ID = ERP
    r = read_portal(fx.portal_bytes(rows=rows), L, NAME, {})
    assert r.records[0]["item"] == ""  # aunque el texto coincida con un ITEM ERP


def test_r1_13_portal_unavailable_keeps_cat_unknown_not_zero() -> None:
    o = [oeras("QZXITEM1", CAT, "7300001", "10"), oeras("QZXITEM1", TUPY, "7300002", "5")]
    res = compute(
        CoverageInput(
            month="2026-11",
            cut_ts="2026-11-06T15:48:14Z",
            oeras=o,
            portal=None,
            fg=[],
            shipments=None,
            rules=core_rules_from_dict(rules()),
            certified=False,
            blocked={"portal_cat": "R1", "shipments": "x"},
        )
    )
    f = res["items"]["QZXITEM1"]["facts"]["demand"]
    assert f.value is None and f.status == "UNKNOWN_NOT_ZERO"
    assert check_invariants(res, []) == []


def test_r1_14_homologation_never_reclassifies_customers() -> None:
    o = [oeras("QZXITEM1", TUPY, "7300001", "30"), oeras("QZXITEM1", APTIV, "7300002", "7")]
    res = run(o, [portal("QZXITEM1", "PL-1", "40")])
    units = res["items"]["QZXITEM1"]["units"]
    kinds = sorted((u["kind"], u["customer_class"]) for u in units)
    assert kinds == [("LINE", "APTIV"), ("LINE", "TUPY"), ("POOL", "CAT")]
    assert demand(res, "QZXITEM1") == Decimal(77)


def test_r1_15_many_to_one_requires_revision_equivalence() -> None:
    """[D-E] Dos aprobaciones individuales no bastan: sin equivalencia expresa de revisiones,
    el grupo completo queda PENDIENTE; con ella, se homologa."""
    led = ledger(entry("B1", "QZX P1", "QZXITEM1"), entry("B2", "QZX P2", "QZXITEM1"))
    table = HDR + "QZX P1,QZXITEM1,APPROVED_MAP,B1\nQZX P2,QZXITEM1,APPROVED_MAP,B2\n"
    m, rep = parse_homologation(table, led)
    assert m == {} and rep["pending"] == {"MANY_TO_ONE_WITHOUT_REVISION_EQUIVALENCE": 2}
    eq = fx.ledger_entry("E1", "R1_REVISION_EQUIVALENCE", ["QZX P1", "QZX P2"], "QZXITEM1")
    m2, _ = parse_homologation(table, ledger(*led["entries"], eq))
    assert m2 == {"QZX P1": "QZXITEM1", "QZX P2": "QZXITEM1"}


def test_r1_16_duplicate_identical_row_is_idempotent() -> None:
    m, rep = parse_homologation(
        HDR + "QZX P1,QZXITEM1,APPROVED_MAP,A1\nQZX P1,QZXITEM1,APPROVED_MAP,A1\n"
    )
    assert m == {"QZX P1": "QZXITEM1"} and not rep["errors"]


def test_r1_17_quoted_comma_identity_preserved_exactly() -> None:
    m, _ = parse_homologation(HDR + '"QZX, P1",QZXITEM1,APPROVED_MAP,AC\n')
    assert m == {"QZX, P1": "QZXITEM1"}


# =================================================================== R3 clasificación comercial
def test_r3_01_exact_key_with_internal_spaces() -> None:
    res = run([oeras("QZXITEM1", CAT, "7300001", "10")], [portal("QZXITEM1", "PL-1", "4")])
    assert demand(res, "QZXITEM1") == Decimal(4)  # la línea CAT se sustituye por el Portal


@pytest.mark.parametrize(
    "variant",
    ["QZXCAT   001", "QZXCAT     001", "qzxcat    001", "QZXCAT    01", "QZXCAT    001"],
)
def test_r3_02_key_variants_are_not_cat(variant: str) -> None:
    cls = core_rules_from_dict(rules()).base.customer_class(variant)
    assert cls != "CAT"


@pytest.mark.parametrize(
    "variant",
    ["QZXCAT   001", "qzxcat    001", "QZXNEW    001"],
)
def test_r3_03_unclassified_key_is_not_silently_inferred(variant: str) -> None:
    """Condición 3: una clave sin clasificación explícita NO puede recibir una clase comercial
    inferida (hoy: «OTHER», contada como demanda conservada). Debe quedar desconocida o
    bloquear: si es una clave CAT faltante, su demanda se duplica (29 OERAS + Portal)."""
    o = [oeras("QZXITEM1", variant, "7300001", "10")]
    res = run(o, [portal("QZXITEM1", "PL-1", "4")])
    assert demand(res, "QZXITEM1") is None


def test_r3_04_missing_cat_key_double_counts_demand_evidence() -> None:
    """Evidencia del impacto de R3-03: la misma línea CAT con una clave no clasificada."""
    good = run([oeras("QZXITEM1", CAT, "7300001", "10")], [portal("QZXITEM1", "PL-1", "4")])
    unlisted = rules(customer_classes={"CAT": [], "TUPY": [TUPY]})
    bad = run(
        [oeras("QZXITEM1", CAT, "7300001", "10")], [portal("QZXITEM1", "PL-1", "4")], r=unlisted
    )
    assert demand(good, "QZXITEM1") == Decimal(4)
    assert demand(bad, "QZXITEM1") != Decimal(14), "demanda duplicada en silencio (10 + 4)"


def test_r3_05_name_never_drives_classification() -> None:
    line = OerasLine(
        "QZXITEM1",
        OTHER,
        "CATERPILLAR TUPY APTIV DELPHI",
        "7300001",
        "1",
        "2026-11-20",
        Decimal(10),
        Decimal(10),
        ref(),
    )
    res = run([line], [portal("QZXITEM1", "PL-1", "4")])
    classes = {u["customer_class"] for u in res["items"]["QZXITEM1"]["units"]}
    assert classes == {"OTHER_EXPLICIT", "CAT"} and demand(res, "QZXITEM1") == Decimal(14)


def test_r3_06_aptiv_and_delphi_independent_in_demand_and_attribution() -> None:
    o = [oeras("QZXITEM2", APTIV, "7300001", "100"), oeras("QZXITEM2", DELPHI, "7300002", "50")]
    s = [ship("MV-1", "QZXITEM2", APTIV, "9999999", "120")]  # excede APTIV: no salta a DELPHI
    res = run(o, [], s)
    units = {u["customer_key"]: u for u in res["items"]["QZXITEM2"]["units"]}
    assert units[APTIV]["customer_class"] == "APTIV"
    assert units[DELPHI]["customer_class"] == "DELPHI"
    assert attended(units[APTIV]) == Decimal(100) and attended(units[DELPHI]) == Decimal(0)
    assert res["attribution"]["excess"][0]["qty"] == Decimal(20)
    assert check_invariants(res, s) == []


def test_r3_07_tupy_preserved_when_item_has_cat() -> None:
    o = [oeras("QZXITEM1", TUPY, "7300001", "30"), oeras("QZXITEM1", CAT, "7300002", "999")]
    res = run(o, [portal("QZXITEM1", "PL-1", "40")])
    assert demand(res, "QZXITEM1") == Decimal(70)  # TUPY 30 + Portal 40; CAT 999 sustituido


def test_r3_08_cat_shipment_never_consumes_tupy_lines() -> None:
    o = [oeras("QZXITEM1", TUPY, "7300001", "30")]
    s = [ship("MV-1", "QZXITEM1", CAT, "", "25")]
    res = run(o, [portal("QZXITEM1", "PL-1", "10")], s)
    tupy = next(u for u in res["items"]["QZXITEM1"]["units"] if u["customer_class"] == "TUPY")
    assert attended(tupy) == Decimal(0)


def test_r3_09_review_class_preserved_not_substituted() -> None:
    res = run([oeras("QZXITEM1", REVIEW, "7300001", "12")], [portal("QZXITEM1", "PL-1", "4")])
    assert demand(res, "QZXITEM1") == Decimal(16)


def test_r3_10_key_in_two_classes_used_in_data_fails_closed() -> None:
    r = rules(customer_classes={"CAT": [CAT], "TUPY": [TUPY, CAT]})
    with pytest.raises(ValueError, match="dos clases"):  # [D-R3-02] ahora al cargar
        run([oeras("QZXITEM1", CAT, "7300001", "10")], [], r=r)


def test_r3_11_key_in_two_classes_rejected_at_load() -> None:
    """Una regla ambigua debe rechazarse al cargar, aunque la clave no aparezca en los datos."""
    with pytest.raises(ValueError):
        core_rules_from_dict(rules(customer_classes={"CAT": [CAT], "TUPY": [TUPY, CAT]}))


def test_r3_12_substitute_class_must_exist() -> None:
    with pytest.raises(ValueError):
        core_rules_from_dict(rules(substitute_class="CATX"))


def test_r3_13_substitute_class_cannot_be_preserved() -> None:
    with pytest.raises(ValueError):
        core_rules_from_dict(rules(preserve_classes=["TUPY", "CAT"]))


@pytest.mark.parametrize("key", ["", "   ", "QZXCAT    001 "])
def test_r3_14_invalid_key_in_rules_rejected(key: str) -> None:
    """Clave vacía o con espacio final: nunca coincide con OE965 (rstrip del ancho fijo) y deja
    la clase incompleta en silencio."""
    with pytest.raises(ValueError):
        core_rules_from_dict(rules(customer_classes={"CAT": [CAT, key], "TUPY": [TUPY]}))


def test_r3_15_unapproved_rules_never_official_kpi() -> None:
    res = compute(
        CoverageInput(
            month="2026-11",
            cut_ts="2026-11-06T15:48:14Z",
            oeras=[oeras("QZXITEM1", TUPY, "7300001", "10")],
            portal=[],
            fg=[],
            shipments=[],
            rules=core_rules_from_dict(rules()),
            certified=True,
        )
    )
    assert set(res["items"]["QZXITEM1"]["kpi"].values()) == {"PROVISIONAL"}


def test_r1_18_destination_outside_period_erp_is_pending() -> None:
    """[D-F] Sin coincidencia exacta con OE965/IN092U del periodo, no se homologa."""
    led = ledger(entry("F1", "QZX P8", "QZXITEM8"))
    m, rep = parse_homologation(HDR + "QZX P8,QZXITEM8,APPROVED_MAP,F1\n", led)
    assert m == {} and rep["pending"] == {"HOMOLOGATION_TARGET_NOT_IN_ERP": 1}


def test_r1_19_portal_only_requires_ledger_and_keeps_identity() -> None:
    """[K-2] Una identidad solo-Portal acreditada no inventa ITEM ERP; sin registro, pendiente."""
    led = ledger(fx.ledger_entry("P1", "R1_PORTAL_ONLY", ["QZX SOLO"], ""))
    approvers = frozenset({fx.APPROVER})
    ok = parse_v2(
        HDR + "QZX SOLO,,APPROVED_PORTAL_ONLY,P1\n", led, approvers, "2026-11-07", ERP_UNIVERSE
    )
    assert ok.portal_only == frozenset({"QZX SOLO"}) and ok.mapping == {}
    no = parse_v2(
        HDR + "QZX SOLO,,APPROVED_PORTAL_ONLY,NO\n", led, approvers, "2026-11-07", ERP_UNIVERSE
    )
    assert no.portal_only == frozenset()
    assert no.report["pending"] == {"APPROVAL_NOT_IN_LEDGER": 1}


def test_r3_v1_history_confined_to_isolated_synthetic_execution() -> None:
    """[D-B] v1 conserva OTHER implícito SOLO para el histórico sintético; la separación se
    impone en el Refresh (test_core_frozen_universe::test_t2_*)."""
    v1 = core_rules_from_dict(demo.RULES)
    assert v1.strict is False and v1.classify("QZXNEW    001") == "OTHER"
    assert core_rules_from_dict(rules()).classify("QZXNEW    001") == "UNKNOWN"
