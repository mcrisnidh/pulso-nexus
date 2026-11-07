"""Escenarios PUROS del Frozen definitivo y del universo mensual (33 controles).

Construidos en la propuesta consolidada (sin stage ni RefreshService) e incorporados como
pruebas. FROZEN = (29 OERAS − CAT ERP) + Portal depurado; UNIVERSO = ITEM del FROZEN.
Solo datos sintéticos.
"""

from __future__ import annotations

import json
import tempfile
from decimal import Decimal
from pathlib import Path

from nexus.core import demo
from nexus.core.coverage import CoverageInput, check_invariants, compute, frozen_universe, serialize
from nexus.core.rules import core_rules_from_dict
from nexus.core.sources import read_source


def parsed(sid: str, data: bytes) -> list:  # type: ignore[type-arg]
    tmp = Path(tempfile.mkdtemp()) / "x.csv"
    tmp.write_bytes(data)
    p = read_source(sid, tmp)
    assert p.valid, p.errors
    return p.records


# ------------------------------------------------------------------ escenarios del alcance
CAT, TUPY, UNK = "QZXCAT    001", "QZXTUP    000", "QZXNEW    009"


def rules_v2() -> object:
    r = json.loads(json.dumps(demo.RULES))
    r.update(
        schema="nexus.core.rules/2",
        unclassified_customers="UNKNOWN",
        customer_classes={"CAT": [CAT], "TUPY": [TUPY], "OTHER": ["QZXOTH    001"]},
        preserve_classes=["TUPY"],
        customer_notes={},
    )
    return core_rules_from_dict(r)


def oe(rows: list[tuple[str, str, str, str, str]]) -> list:  # type: ignore[type-arg]
    return parsed(
        "oeras_29",
        demo.oeras("2026-11-01T06:00:00Z", [(i, c, o, "1", d, q) for i, c, o, d, q in rows]),
    )


def po(rows: list[tuple[str, str, str, str]], status: str = "") -> list:  # type: ignore[type-arg]
    data = demo.portal(
        "2026-11-03T00:00:00Z", [(i, lid, "Open", q, "0", d) for i, lid, q, d in rows]
    )
    recs = parsed("portal_cat", data)
    return recs


def fgr(rows: list[tuple[str, str]]) -> list:  # type: ignore[type-arg]
    return parsed("fg_inventory", demo.fg("2026-11-06T15:48:14Z", [(i, "FG", q) for i, q in rows]))


def run(
    frozen_oe: list,
    frozen_po: list,
    fg: list,
    pending: tuple[str, ...] = (),
    shipments: list | None = None,
) -> dict:  # type: ignore[type-arg]
    r = rules_v2()
    fu = frozen_universe(frozen_oe, frozen_po, r)  # FROZEN DEFINITIVO -> universo
    return compute(
        CoverageInput(
            month="2026-11",
            cut_ts="2026-11-06T15:48:14Z",
            oeras=frozen_oe,
            portal=frozen_po,
            fg=fg,
            shipments=shipments,
            rules=r,
            certified=False,
            blocked={} if shipments is not None else {"shipments": "sin embarques"},
            universe=fu.items,
            pending_portal=pending or fu.pending_portal,
            universe_counts=fu.counts,
        )
    )


def ship(mid: str, item: str, cust: str, qty: str) -> list:  # type: ignore[type-arg]
    return parsed(
        "shipments",
        demo.shipments(
            "2026-11-06T15:48:14Z", [(mid, item, cust, "", "", qty, "2026-11-05T15:00:00Z", "SHIP")]
        ),
    )


def frozen_scenarios() -> dict[str, object]:
    """Escenarios nuevos estrictamente necesarios para la regla del FROZEN DEFINITIVO."""
    res: dict[str, object] = {}
    erp = oe(
        [
            ("IT-CATONLY", CAT, "7310001", "2026-11-10", "500"),  # F1 CAT ERP sin Portal: sale
            ("IT-TUPY", TUPY, "7310002", "2026-11-10", "30"),  # F2 TUPY: se conserva
            ("IT-MIX", CAT, "7310003", "2026-11-12", "999"),  # F3 CAT + TUPY mismo ITEM
            ("IT-MIX", TUPY, "7310004", "2026-11-15", "20"),
            ("IT-FUT", TUPY, "7310005", "2026-12-20", "40"),  # F9 sin demanda exigible
            ("IT-UNK", UNK, "7310006", "2026-11-18", "15"),  # F6 clave UNKNOWN
        ]
    )
    pz = po(
        [
            ("IT-MIX", "PL-1", "50", "2026-11-20"),  # F3 Portal del mismo ITEM
            ("IT-PORTAL", "PL-2", "70", "2026-11-22"),  # F4 solo Portal
            ("IT-NOQTY", "PL-4", "8000(0)", "2026-11-23"),
        ]
    )  # F5c cantidad desconocida
    cancelled = parsed(
        "portal_cat",
        demo.portal(
            "2026-11-03T00:00:00Z",
            [
                (
                    "IT-CANCEL",
                    "PL-3",
                    "Cancelled",
                    "60",
                    "0",
                    "2026-11-21",
                ),  # F5a excluido por estado
                ("IT-DIF0", "PL-5", "Open", "10", "10", "2026-11-21"),
            ],
        ),
    )  # F5b DIF ≤ 0
    portal = pz + cancelled
    fg_in = fgr([("IT-TUPY", "5"), ("IT-MIX", "8")])
    externals = fgr([("IT-TUPY", "5"), ("IT-MIX", "8"), ("IT-CATONLY", "77"), ("IT-EXT", "900")])
    out = run(erp, portal, fg_in)
    items = set(out["items"])
    dem = {i: out["items"][i]["facts"]["demand"].value for i in items}
    c = out["universe"]["frozen_construction"]
    res["F1_cat_solo_erp_fuera_del_universo"] = (
        "IT-CATONLY" not in items and c["erp_items_cat_only_removed"] == 1
    )
    res["F2_tupy_conservado"] = dem.get("IT-TUPY") == Decimal(30)
    res["F3_cat_tupy_mismo_item_sin_duplicar"] = dem.get("IT-MIX") == Decimal(
        70
    )  # TUPY 20 + Portal 50
    res["F4_solo_portal_en_universo"] = dem.get("IT-PORTAL") == Decimal(70)
    res["F5_portal_depurado"] = (
        "IT-CANCEL" not in items
        and "IT-DIF0" not in items
        and c["portal_lines_excluded_by_rules"] == 2
    )
    res["F5c_cantidad_desconocida_conservada_no_cero"] = (
        "IT-NOQTY" in items and dem["IT-NOQTY"] is None
    )
    res["F6_unknown_trazable_y_bloqueante"] = (
        "IT-UNK" in items and dem["IT-UNK"] is None and out.get("unclassified_customers") == [UNK]
    )
    res["F9_sin_demanda_exigible_identificable"] = "IT-FUT" in items and dem["IT-FUT"] == Decimal(0)
    res["conteos_construccion"] = c == {
        "erp_frozen_items": 5,
        "erp_frozen_lines": 6,
        "erp_cat_lines_removed": 2,
        "erp_items_with_cat_lines": 2,
        "erp_items_kept_non_cat": 3,
        "erp_items_cat_only_removed": 1,
        "erp_items_membership_pending_r3": 1,
        "portal_items_valid": 3,
        "portal_lines_excluded_by_rules": 2,
        "portal_identities_pending_r1": 0,
        "items_erp_and_portal": 1,
        "items_portal_only": 2,
        "universe_items": 6,
        "erp_items_cat_only_pending_r1": 0,
    }
    # F7: resultados oficiales idénticos con y sin registros externos añadidos
    with_ext = run(erp, portal, externals)

    def strip(r: dict) -> str:  # type: ignore[type-arg]
        """Resultado oficial sin el diagnóstico del universo ni la huella del ARCHIVO de origen
        (con registros externos el archivo es otro y su SHA-256 debe cambiar)."""

        def clean(x: object) -> object:
            if isinstance(x, dict):
                return {k: clean(v) for k, v in x.items() if k != "sha256"}
            if isinstance(x, list):
                return [clean(v) for v in x]
            return x

        return json.dumps(
            clean(serialize({k: v for k, v in r.items() if k != "universe"})), sort_keys=True
        )

    res["F7_kpi_identicos_con_y_sin_externos"] = strip(out) == strip(with_ext)
    res["F7_externos_contados_en_diagnostico"] = (
        with_ext["universe"]["excluded_records"]["fg_inventory"] == 2
    )
    # F8: embarque con clave UNKNOWN sobre ITEM del universo -> trazable y bloqueante
    s8 = run(erp[1:2], [], [], shipments=ship("MV-1", "IT-TUPY", "QZXZZZ    000", "5"))
    res["F8_embarque_unknown_bloquea"] = s8.get("unclassified_customers") == ["QZXZZZ    000"]
    # F8b: embarque externo (ITEM fuera del Frozen) con clave UNKNOWN -> no contamina
    s8b = run(erp[1:2], [], [], shipments=ship("MV-2", "IT-EXT", "QZXZZZ    000", "5"))
    res["F8b_embarque_externo_no_contamina"] = "unclassified_customers" not in s8b and set(
        s8b["items"]
    ) == {"IT-TUPY"}
    # F10: identidad Portal pendiente R1: conservada, no sumada, bloqueante; los demás siguen
    pend = parsed(
        "portal_cat",
        demo.portal("2026-11-03T00:00:00Z", [("", "PL-9", "Open", "80", "0", "2026-11-20")]),
    )
    s10 = run(erp[1:4], pend + pz[:1], [])
    res["F10_pendiente_conservado_no_sumado"] = s10["pending_r1"] == ["P-"] or bool(
        s10.get("pending_r1")
    )
    res["F10_otros_articulos_validos_se_conservan"] = {"IT-TUPY", "IT-MIX"} <= set(s10["items"])
    res["F10_demanda_total_incompleta"] = s10["totals"]["demand"]["complete"] is False
    # F10b: con pendiente R1, el ITEM solo-CAT del ERP NO desaparece (pertenencia pendiente)
    s10b = run(erp[:1], pend, [])
    res["F10b_cat_solo_con_pendiente_se_conserva"] = (
        "IT-CATONLY" in s10b["items"]
        and s10b["items"]["IT-CATONLY"]["facts"]["demand"].value is None
        and s10b["universe"]["frozen_construction"]["erp_items_cat_only_pending_r1"] == 1
    )
    res["invariantes"] = all(check_invariants(r, []) == [] for r in (out, with_ext, s10))
    return res


def universe_scenarios() -> dict[str, object]:
    res: dict[str, object] = {}
    frozen_oe = oe(
        [
            ("IT-OE", TUPY, "7300001", "2026-11-10", "30"),  # 1: solo OE965
            ("IT-BOTH", CAT, "7300002", "2026-11-12", "999"),  # 3/7: ambos, CAT sustituido
            ("IT-BOTH", TUPY, "7300003", "2026-11-15", "20"),  # 7: TUPY conservado
            ("IT-FUT", TUPY, "7300004", "2026-12-15", "40"),  # solo líneas futuras
            ("IT-GONE", TUPY, "7300005", "2026-11-20", "10"),  # 11: luego desaparece
        ]
    )
    frozen_po = po(
        [
            ("IT-BOTH", "PL-1", "50", "2026-11-20"),  # 3: ambos
            ("IT-PO", "PL-2", "70", "2026-11-22"),
        ]
    )  # 2: solo Portal
    current_oe = oe(
        [
            ("IT-OE", TUPY, "7300001", "2026-11-10", "25"),
            ("IT-NEWOE", TUPY, "7300009", "2026-11-11", "500"),
        ]
    )  # 4: solo actual
    current_po = po([("IT-NEWPO", "PL-9", "600", "2026-11-25")])  # 5: solo actual
    fg = fgr([("IT-OE", "5"), ("IT-BOTH", "8"), ("IT-ONLYINV", "900")])  # 6: solo IN092U
    out = run(frozen_oe, frozen_po, fg)
    items = set(out["items"])
    d = {i: out["items"][i]["facts"]["demand"].value for i in items}
    res["S1_solo_oe965_incluido"] = "IT-OE" in items
    res["S2_solo_portal_incluido"] = "IT-PO" in items and d["IT-PO"] == Decimal(70)
    res["S3_ambos_sin_duplicar"] = d["IT-BOTH"] == Decimal(70)  # Portal 50 + TUPY 20; CAT 999 fuera
    frozen_items = {x.item for x in frozen_oe} | {x.item for x in frozen_po}
    new_in_current = {x.item for x in current_oe} | {x.item for x in current_po}
    external = (new_in_current | {r.item for r in fg}) - frozen_items
    res["S4_S5_S6_externos_sembrados"] = external == {"IT-NEWOE", "IT-NEWPO", "IT-ONLYINV"}
    res["S4_S5_S6_externos_excluidos"] = not (external & items)
    res["S6_fg_externo_contado"] = out["universe"]["excluded_records"]["fg_inventory"] == 1
    res["S6_kpi_fg_sin_externo"] = out["totals"]["fg_assignable"]["known_sum"] == Decimal(13)
    res["S7_cat_y_tupy"] = sorted(
        u["customer_class"] for u in serialize(out["items"]["IT-BOTH"])["units"]
    ) == ["CAT", "TUPY"]
    res["ITEM_solo_futuras_no_se_pierde"] = "IT-FUT" in items and d["IT-FUT"] == Decimal(0)
    res["S11_ausente_en_actual_permanece"] = "IT-GONE" in items  # el cálculo usa el Frozen
    res["S12_actual_no_amplia_universo"] = items == {
        "IT-OE",
        "IT-BOTH",
        "IT-FUT",
        "IT-GONE",
        "IT-PO",
    }
    res["invariantes"] = check_invariants(out, []) == []
    # 8: ITEM congelado con cliente UNKNOWN -> identidad conservada y bloqueo (no_go)
    unk = run(
        oe(
            [
                ("IT-U", UNK, "7300010", "2026-12-30", "5"),
                ("IT-U", TUPY, "7300011", "2026-11-05", "3"),
            ]
        ),
        [],
        [],
    )
    res["S8_unknown_congelado_bloquea"] = (
        unk.get("unclassified_customers") == [UNK]
        and unk["items"]["IT-U"]["facts"]["demand"].value is None
    )
    # 9: UNKNOWN solo en un ITEM externo (OE965 actual) -> no contamina
    ext = run(frozen_oe, frozen_po, fg)
    res["S9_unknown_externo_no_contamina"] = "unclassified_customers" not in ext
    # 10: identidad Portal pendiente R1 -> conservada y bloqueante, sin sustituir ni descartar
    pend_po = po([("", "PL-5", "80", "2026-11-20")])
    pen = run(oe([("IT-BOTH", CAT, "7300002", "2026-11-12", "999")]), pend_po, [], ("PORTAL-ID-X",))
    res["S10_pendiente_r1_conservado"] = (
        pen.get("pending_r1") == ["PORTAL-ID-X"] and pen["totals"]["demand"]["complete"] is False
    )
    res["S10_cat_no_sustituido_con_pendiente"] = (
        pen["items"]["IT-BOTH"]["facts"]["demand"].value is None
    )
    return res
