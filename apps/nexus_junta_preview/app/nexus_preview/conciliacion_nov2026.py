"""PULSO NEXUS — Conciliación comercial de noviembre 2026 (SOLO LECTURA, local en ASMXC1200).

Usa EXCLUSIVAMENTE las funciones certificadas del núcleo (`feature/core-v1` @ d98e4d3/b4ec5be):
lectores R2, `parse_homologation` v2 (registro autorizado), `portal_decision` y
`frozen_universe`. No invoca `stage()` ni `RefreshService`, no publica, no usa SQL y no escribe
fuera de `--priv` (privado, fuera de git) y `--out` (resumen saneado).

FROZEN DEFINITIVO = (29 OERAS Frozen − demanda CAT ERP) + Portal CAT Frozen depurado
UNIVERSO          = ITEM distintos del FROZEN DEFINITIVO

Sin reglas R3 v2 APROBADAS ni registro R1 verificado, las cifras oficiales se informan como
PENDIENTE_R3 / PENDIENTE_R1 con el subconjunto responsable. Un escenario con la clasificación y
las correspondencias PROVISIONALES se calcula aparte, rotulado NO OFICIAL, solo para priorizar.

Salidas:
  --priv\\R3_matriz_decision_nov2026.csv   (PRIVADO: claves exactas; para el responsable comercial)
  --priv\\R1_matriz_decision_nov2026.csv   (PRIVADO: Item ID / ITEM ERP; ídem)
  --priv\\conciliacion_nov2026_privado.json (PRIVADO: identidades de exclusiones y pendientes)
  --out                                    (SANEADO: solo conteos y estados; compartible)
"""

from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import hashlib
import io
import json
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from nexus.core import demo
from nexus.core.coverage import frozen_universe
from nexus.core.physical import default_layout, detect_kind, read_in092u, read_oe965, read_portal
from nexus.core.rules import UNKNOWN_CLASS, core_rules_from_dict
from nexus.core.sources import read_source
from nexus.core.stage import (
    R1Resolution,
    _with_r1,
    check_private_output,
    contract_csv,
    parse_homologation,
    read_only,
)
from nexus.planning.engine import portal_decision

# Huellas de los informes probe saneados (ASMXC1200, R2). R_20261106 no tiene huella previa.
# 29 OERAS = Backlog = archivos R_YYYYMMDD.TXT (equivalencia de nombres, mandato integral; CD-3 cerrada).
EXPECTED_SHA256 = {
    "R_20261101.TXT": "2b59ebacd873dd7d97eadc0e64715d93831f3beca45679817513d3f59d70d0d6",
    "Portal_CAT_2026-11-03_0955.csv": (
        "0592178d604a15f9507a4f8a0b24ca0be55fc94ea612590c0a14e75f8260efd9"
    ),
    "INV_STA289.TXT": "779f63fc4d5b80683e99f6e171c8f150b1075fc9bb5d68b3770b9b06e6ad3639",
}
DEFAULT_EXCLUDED = ("Cancelled", "Completed", "Rejected")
PROV_COLUMNS = (
    ("item_portal", "item_erp"),
    ("ITEM portal original", "ITEM homologado"),
    ("Item ID", "ITEM ERP"),
)
R3_KEY_COLUMNS = ("customer_key_exact", "clave_exacta", "customer_key", "clave")
R3_CLASS_COLUMNS = ("propuesta", "clase", "class", "decision", "clasificacion")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(read_only(path).decode("utf-8-sig"))))


def pick(row: dict[str, str], names: tuple[str, ...]) -> str | None:
    for n in names:
        if n in row:
            return n
    return None


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    check_private_output(path)
    buf = io.StringIO(newline="")
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    path.write_bytes(("﻿" + buf.getvalue()).encode("utf-8"))  # Excel abre UTF-8 con BOM


# ------------------------------------------------------------------ 1. fuentes y precedencia
def inventory(src: Path, tz: str | None) -> dict[str, Any]:
    layout = default_layout()
    files: list[dict[str, Any]] = []
    parsed: dict[str, Any] = {}
    for path in sorted(p for p in src.iterdir() if p.is_file()):
        kind = detect_kind(path.name, layout)
        if kind is None:
            continue
        data = read_only(path)
        if kind == "oe965":
            res = read_oe965(data, layout)
        elif kind == "portal":
            res = read_portal(data, layout, path.name, {})
        else:
            res = read_in092u(data, layout, tz)
        expected = EXPECTED_SHA256.get(path.name)
        files.append(
            {
                "file": path.name,
                "kind": kind,
                "sha256": sha(data),
                "sha256_expected": expected,
                "sha256_matches": None if expected is None else sha(data) == expected,
                "acceptance": "PASS" if res.valid else "FAIL",
                "document_date": str(res.document_time.get("value", ""))[:10] or None,
                "records": len(res.records),
            }
        )
        parsed[path.name] = res
    precedence: dict[str, Any] = {}
    for kind in ("oe965", "portal"):
        valid = [f for f in files if f["kind"] == kind and f["acceptance"] == "PASS"]
        valid.sort(key=lambda f: (f["document_date"] or "9999", f["file"]))
        first = valid[0] if valid else None
        same_day = [f for f in valid if first and f["document_date"] == first["document_date"]]
        precedence[kind] = {
            "valid_files": [(f["file"], f["document_date"]) for f in valid],
            "frozen": first["file"] if first else None,
            "ambiguous_same_date": len({f["sha256"] for f in same_day}) > 1,
        }
    return {"files": files, "precedence": precedence, "parsed": parsed}


# ------------------------------------------------------------------ reglas y R1
def load_rules(path: Path | None) -> tuple[dict[str, Any] | None, str]:
    if path is None:
        return None, "AUSENTE"
    data = json.loads(read_only(path).decode("utf-8"))
    core_rules_from_dict(data)  # valida al cargar (D-R3-02/03/04); lanza si es inválida
    approved = data.get("schema") == "nexus.core.rules/2" and data.get("approval") == "CRC_APPROVED"
    return data, "APROBADAS_V2" if approved else "NO_APROBADAS"


# D-01 (revisión 3): las propuestas R3 llegan como TRATAMIENTOS (p. ej. CAT_NOMBRE_CANDIDATO), no
# como clases. La revisión 2 los usaba como nombre de clase: ninguna clave quedaba en la clase
# sustituida «CAT» y el escenario provisional no sustituía nada. Mapeo explícito, solo para los
# escenarios NO OFICIALES. Variante A: lo «en revisión» se conserva; variante B: se sustituye.
SCENARIO_CLASSES = ("CAT", "CAT_REVIEW", "TUPY", "APTIV", "DELPHI", "OTHER")
TREATMENT_CLASS: dict[str, dict[str, str]] = {
    "A": {
        "CAT_NOMBRE_CANDIDATO": "CAT",
        "CAT_EN_REVISION": "CAT_REVIEW",
        "PERK2_EN_REVISION": "CAT_REVIEW",
        "TUPY_CANDIDATO": "TUPY",
        "APTIV_CANDIDATO": "APTIV",
        "DELPHI_CANDIDATO": "DELPHI",
        "NO_CAT_EVIDENTE_SIN_APROBAR": "OTHER",
    },
}
TREATMENT_CLASS["B"] = {
    **TREATMENT_CLASS["A"],
    "CAT_EN_REVISION": "CAT",
    "PERK2_EN_REVISION": "CAT",
}


def treatment_to_class(label: str, variant: str = "A") -> str | None:
    """Clase del escenario NO OFICIAL para una propuesta; None = no reconocida (queda UNKNOWN)."""
    value = label.strip().upper()
    if value in SCENARIO_CLASSES:  # matriz que ya trae la clase
        return value
    return TREATMENT_CLASS[variant].get(value)


def provisional_rules(
    base: dict[str, Any] | None, r3_rows: list[dict[str, str]], variant: str = "A"
) -> dict[str, Any]:
    """Reglas v2 SOLO para escenarios NO OFICIALES: clases derivadas de la matriz provisional."""
    r = json.loads(json.dumps(base or demo.RULES))
    classes: dict[str, list[str]] = defaultdict(list)
    for row in r3_rows:
        kc, cc = pick(row, R3_KEY_COLUMNS), pick(row, R3_CLASS_COLUMNS)
        if kc and cc and row[cc].strip():
            cls = treatment_to_class(row[cc], variant)
            if cls is not None:  # no reconocida: la clave queda UNKNOWN (nunca OTHER implícito)
                classes[cls].append(row[kc])
    if "CAT" not in classes:
        classes["CAT"] = []
    r.update(
        schema="nexus.core.rules/2",
        unclassified_customers=UNKNOWN_CLASS,
        version=f"escenario-provisional-{variant}-NO-OFICIAL",
        approval="PROVISIONAL_NO_CRC",
        customer_classes=dict(classes),
        substitute_class="CAT",
        preserve_classes=sorted(c for c in classes if c != "CAT"),
        customer_notes={},
    )
    return r


def to_contract(source_id: str, records: list[dict[str, str]], work: Path) -> list[Any]:
    path = work / f"{source_id}.csv"
    path.write_bytes(contract_csv(source_id, records))
    parsed = read_source(source_id, path)
    if not parsed.valid:
        raise SystemExit(f"contrato {source_id} inválido: {parsed.errors[:3]}")
    return list(parsed.records)


# ------------------------------------------------------------------ principal
def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="copias autorizadas (solo lectura)")
    ap.add_argument("--priv", required=True, help="salida PRIVADA (fuera de git)")
    ap.add_argument("--out", required=True, help="resumen SANEADO (JSON)")
    ap.add_argument("--tz", default="America/Mexico_City")
    ap.add_argument("--month", default="2026-11")
    ap.add_argument("--rules", default=None, help="reglas R3 v2 (privadas)")
    ap.add_argument("--r3-provisional", default=None, help="matriz R3 provisional (CSV)")
    ap.add_argument("--r1-provisional", default=None, help="correspondencias R1 provisionales")
    ap.add_argument("--homologation", default=None, help="tabla R1 v2")
    ap.add_argument("--ledger", default=None, help="registro autorizado R1")
    ap.add_argument("--approvers", default=None, help="lista de aprobadores")
    ap.add_argument("--priority-key", action="append", default=[], help="clave R3 prioritaria")
    ap.add_argument(
        "--priority-group",
        action="append",
        default=[],
        help="grupo R1 prioritario: 'PORTAL1|PORTAL2=>ERP'",
    )
    a = ap.parse_args(argv)
    src, priv = Path(a.src), Path(a.priv)
    check_private_output(priv, src)
    check_private_output(Path(a.out), src)  # el resumen tampoco puede caer en git ni en el origen
    priv.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="_contratos_", dir=priv))
    year, mon = (int(x) for x in a.month.split("-"))
    end = f"{a.month}-{calendar.monthrange(year, mon)[1]:02d}"

    inv = inventory(src, a.tz)
    pre = inv["precedence"]
    oe_frozen = inv["parsed"].get(pre["oe965"]["frozen"])
    po_frozen = inv["parsed"].get(pre["portal"]["frozen"])
    oe_later = [inv["parsed"][f] for f, _ in pre["oe965"]["valid_files"][1:]]
    in092u = [
        inv["parsed"][f["file"]]
        for f in inv["files"]
        if f["kind"] == "in092u" and f["acceptance"] == "PASS"
    ]
    if oe_frozen is None or po_frozen is None:
        raise SystemExit("falta OE965 o Portal Frozen válido: no se puede conciliar")
    rules_data, rules_state = load_rules(Path(a.rules) if a.rules else None)
    r3_prov = read_csv(Path(a.r3_provisional)) if a.r3_provisional else []
    r1_prov = read_csv(Path(a.r1_provisional)) if a.r1_provisional else []

    # ---------------------------------------------------------- 2. matriz R3
    erp = oe_frozen.records
    later_keys: Counter[str] = Counter(r["customer_key_exact"] for o in oe_later for r in o.records)
    prov_class: dict[str, str] = {}
    for row in r3_prov:
        kc, cc = pick(row, R3_KEY_COLUMNS), pick(row, R3_CLASS_COLUMNS)
        if kc and cc:
            prov_class[row[kc]] = row[cc].strip()
    approved_rules = core_rules_from_dict(rules_data) if rules_state == "APROBADAS_V2" else None
    by_key: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in erp:
        by_key[r["customer_key_exact"]].append(r)
    items_keys: dict[str, set[str]] = defaultdict(set)
    for r in erp:
        items_keys[r["item"]].add(r["customer_key_exact"])
    r3_rows = []
    for key in sorted(by_key):
        lines = by_key[key]
        items = sorted({r["item"] for r in lines})
        sole = [i for i in items if items_keys[i] == {key}]
        r3_rows.append(
            {
                "clave_exacta": key,
                "clave_visible": key.replace(" ", "·"),
                "prioritaria": "SI" if key in a.priority_key else "",
                "lineas_frozen": len(lines),
                "lineas_exigibles_mes": sum(
                    1 for r in lines if r["due_date"] and r["due_date"] <= end
                ),
                "item_distintos": len(items),
                "item_donde_es_unica_clave": len(sole),
                "impacto_si_CAT": f"{len(lines)} líneas salen del ERP; {len(sole)} ITEM salen del "
                "universo salvo que el Portal válido los aporte",
                "en_corte_posterior": "SI" if key in later_keys else "NO",
                "nombre_contexto_NO_ES_EVIDENCIA": lines[0]["customer_name"],
                "propuesta_provisional": prov_class.get(key, ""),
                "clase_reglas_aprobadas": approved_rules.classify(key) if approved_rules else "",
                "DECISION_CAT_o_CONSERVAR": "",
                "clase_explicita_si_conservar": "",
                "evidencia_ref": "",
                "evidencia_sha256": "",
                "aprobador": "",
                "fecha_aprobacion": "",
            }
        )
    new_keys = sorted(set(later_keys) - set(by_key))
    for key in new_keys:  # p. ej. PARKER 001: identidad nueva entre cortes
        r3_rows.append(
            {
                **{k: "" for k in r3_rows[0]},
                "clave_exacta": key,
                "clave_visible": key.replace(" ", "·"),
                "lineas_frozen": 0,
                "en_corte_posterior": "SI",
                "impacto_si_CAT": "NUEVA_ENTRE_CORTES: no pertenece al Frozen; no se incorpora",
            }
        )
    write_csv(priv / "R3_matriz_decision_nov2026.csv", r3_rows)

    # ---------------------------------------------------------- 3. matriz R1
    erp_items = {r["item"] for r in erp}
    later_items = {r["item"] for o in oe_later for r in o.records}
    fg_items = {r["item"] for i in in092u for r in i.records if r["stockroom"] == "FG"}
    any_inv = {r["item"] for i in in092u for r in i.records}
    pc = pick(r1_prov[0], tuple(p for p, _ in PROV_COLUMNS)) if r1_prov else None
    ec = dict(PROV_COLUMNS).get(pc or "", None)
    prov_map: dict[str, set[str]] = defaultdict(set)
    for row in r1_prov:
        if pc and ec and row.get(pc) and row.get(ec):
            prov_map[row[pc]].add(row[ec])
    targets: dict[str, set[str]] = defaultdict(set)
    for p, ts in prov_map.items():
        for t in ts:
            targets[t].add(p)
    pl_rules = core_rules_from_dict(provisional_rules(rules_data, [])).base  # solo estados
    lines_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in po_frozen.records:
        lines_by_id[r["item_portal"]].append(r)
    portal_contract = to_contract(
        "portal_cat", [{**r, "item": "X"} for r in po_frozen.records], work
    )
    decisions = {p.line_id: portal_decision(p, pl_rules)[0] for p in portal_contract}
    groups = {
        f"G{n:03d}": (erp, sorted(ps))
        for n, (erp, ps) in enumerate(
            sorted((t, ps) for t, ps in targets.items() if len(ps) > 1), start=1
        )
    }
    group_of = {p: g for g, (_, ps) in groups.items() for p in ps}
    priority = {}
    for spec in a.priority_group:
        left, _, right = spec.partition("=>")
        for p in left.split("|"):
            priority[p.strip()] = right.strip()
    r1_rows = []
    for pid in sorted(lines_by_id):
        lines = lines_by_id[pid]
        dec = Counter(decisions[r["line_id"]] for r in lines)
        valid = dec["INCLUIDO"] + dec["NO_CUANTIFICABLE"]
        cands = sorted(prov_map.get(pid, set()))
        cand = cands[0] if len(cands) == 1 else ""
        if not valid:
            estado = "SIN_DEMANDA_VALIDA (no requiere decisión para el cálculo)"
        elif len(cands) > 1:
            estado = "AMBIGUA: más de un ITEM ERP candidato"
        elif pid in group_of:
            estado = f"REVISIONES_AMBIGUAS: grupo {group_of[pid]} requiere R1_REVISION_EQUIVALENCE"
        elif cand and cand not in (erp_items | later_items | any_inv):
            estado = "DESTINO_FUERA_DEL_PERIODO (D-F): evaluar PORTAL_ONLY o corregir candidato"
        elif cand:
            estado = "CANDIDATA_MAP: requiere evidencia y aprobación"
        else:
            estado = "SIN_CANDIDATO: decidir MAP (con evidencia) o PORTAL_ONLY"
        valid_lines = [
            r for r in lines if decisions[r["line_id"]] in ("INCLUIDO", "NO_CUANTIFICABLE")
        ]
        r1_rows.append(
            {
                "item_portal": pid,
                "prioritario": priority.get(pid, ""),
                "lineas_frozen": len(lines),
                "lineas_validas": valid,
                "excluidas_estado": dec["EXCLUIDO_ESTADO"],
                "excluidas_dif": dec["EXCLUIDO_DIF"],
                "shipped_vacio_en_validas": sum(
                    1 for r in valid_lines if not r["shipped_raw"].strip()
                ),
                "fechas_vacias_o_ambiguas_en_validas": sum(
                    1 for r in valid_lines if not r["delivery_date"] or not r["ship_date"]
                ),
                "candidato_erp_provisional": "|".join(cands),
                "candidato_en_oe965_frozen": "SI" if cand in erp_items else "",
                "candidato_en_oe965_posterior": "SI" if cand in later_items else "",
                "candidato_en_in092u_fg": "SI" if cand in fg_items else "",
                "grupo_muchos_a_uno": group_of.get(pid, ""),
                "estado_propuesto": estado,
                "DECISION_MAP_PORTAL_ONLY_PENDIENTE": "",
                "item_erp_aprobado": "",
                "approval_id_registro": "",
                "evidencia_ref": "",
                "evidencia_sha256": "",
                "aprobador": "",
                "fecha_aprobacion": "",
            }
        )
    write_csv(priv / "R1_matriz_decision_nov2026.csv", r1_rows)

    # ---------------------------------------------------------- 4-5. Frozen definitivo
    oeras = to_contract("oeras_29", erp, work)

    def compose(rules: dict[str, Any], r1: R1Resolution) -> dict[str, Any]:
        cr = core_rules_from_dict(rules)
        portal = to_contract("portal_cat", [_with_r1(r, r1) for r in po_frozen.records], work)
        fu = frozen_universe(oeras, portal, cr)
        unknown_keys = sorted(
            {x.customer_key for x in oeras if cr.classify(x.customer_key) == UNKNOWN_CLASS}
        )
        items = set(fu.items)
        return {
            **fu.counts,
            "keys_unclassified": len(unknown_keys),
            "portal_identities_pending_r1": len(fu.pending_portal),
            "coverage_oe965_posterior": len(items & later_items),
            "coverage_in092u_fg": len(items & fg_items),
            "items_without_in092u_fg": len(items - fg_items),
            "_items": sorted(items),
            "_pending_portal": list(fu.pending_portal),
            "_unknown_keys": unknown_keys,
        }

    empty_r1 = R1Resolution({}, frozenset(), {"errors": [], "pending": {"SIN_REGISTRO": 1}})
    r1_state = "AUSENTE"
    r1_official = empty_r1
    if a.homologation and a.ledger and a.approvers:
        r1_official = parse_homologation(
            read_only(Path(a.homologation)).decode("utf-8-sig"),
            json.loads(read_only(Path(a.ledger)).decode("utf-8")),
            frozenset(
                json.loads(read_only(Path(a.approvers)).decode("utf-8")).get("approvers", [])
            ),
            dt.date.today().isoformat(),
            frozenset(erp_items | later_items | any_inv),
        )
        r1_state = "REGISTRO_INVALIDO" if r1_official.report["errors"] else "VERIFICADO"
    official: dict[str, Any]
    blockers = []
    if rules_state != "APROBADAS_V2":
        blockers.append(
            f"PENDIENTE_R3: {len(by_key)} claves sin decisión aprobada "
            f"({len(erp)} líneas, {len(erp_items)} ITEM ERP Frozen)"
        )
    valid_ids = sum(1 for r in r1_rows if r["lineas_validas"])
    unresolved = valid_ids - len(r1_official.mapping) - len(r1_official.portal_only)
    if unresolved > 0:
        blockers.append(
            f"PENDIENTE_R1: {unresolved} de {valid_ids} Item ID con demanda válida sin "
            "resolución verificada en el registro"
        )
    if blockers:
        official = {"status": "PENDIENTE", "blockers": blockers}
    else:
        official = compose(rules_data or {}, r1_official)
        late = []
        if official["keys_unclassified"]:
            late.append(
                f"PENDIENTE_R3: {official['keys_unclassified']} claves sin clase en las "
                "reglas aprobadas"
            )
        if official["portal_identities_pending_r1"]:
            late.append(
                f"PENDIENTE_R1: {official['portal_identities_pending_r1']} identidades "
                "Portal válidas sin resolución verificada"
            )
        official = (
            {"status": "PENDIENTE", "blockers": late}
            if late
            else {
                "status": "CALCULABLE_CON_APROBACIONES",
                "advertencia": "calculable con aprobaciones; la certificación exige además "
                "acreditar la precedencia del primer corte y no habilita publicación",
                **official,
            }
        )
    prov_r1 = R1Resolution(
        {p: next(iter(t)) for p, t in prov_map.items() if len(t) == 1 and p not in group_of},
        frozenset(),
        {},
    )
    # Diagnóstico NO-GO: lo que el motor certificado calcularía en `replay` HOY, sin aprobaciones.
    # Reglas: las provistas (cualquier estado) o v2 vacías (toda clave UNKNOWN); R1: solo lo
    # verificado en el registro (lo demás PENDING). No requiere aprobaciones; no certifica nada.
    diag_rules = rules_data if rules_data else provisional_rules(None, [])
    diagnostic = compose(diag_rules, r1_official)
    diagnostic["motivos_no_go"] = [
        m
        for m, n in (
            ("UNCLASSIFIED_CUSTOMER", diagnostic["keys_unclassified"]),
            ("PENDING_R1", diagnostic["portal_identities_pending_r1"]),
        )
        if n
    ] + ["BLOCKED_SOURCE: embarques físicos"]
    scenario: dict[str, Any] | None = None
    scenario_error = None
    if r3_prov:
        try:
            scenario = compose(provisional_rules(rules_data, r3_prov), prov_r1)
        except ValueError as exc:  # matriz provisional inválida: se informa, no se corrige
            scenario_error = f"matriz R3 provisional inválida para reglas v2: {exc}"

    def public(d: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if d is None else {k: v for k, v in d.items() if not k.startswith("_")}

    summary = {
        "schema": "nexus.core.conciliacion-nov2026/1",
        "notice": "SANEADO: solo conteos y estados; sin ITEM, claves, pedidos ni cantidades",
        "fuentes": [
            {
                k: f[k]
                for k in (
                    "file",
                    "kind",
                    "sha256",
                    "sha256_matches",
                    "acceptance",
                    "document_date",
                    "records",
                )
            }
            for f in inv["files"]
        ],
        "precedencia": pre,
        "r3": {
            "reglas": rules_state,
            "claves_frozen": len(by_key),
            "claves_con_propuesta_provisional": sum(1 for k in by_key if prov_class.get(k)),
            "claves_prioritarias_presentes": sum(1 for k in a.priority_key if k in by_key),
            "claves_nuevas_en_corte_posterior": len(new_keys),
        },
        "r1": {
            "registro": r1_state,
            "lineas_portal_frozen": len(po_frozen.records),
            "item_id_distintos": len(lines_by_id),
            "item_id_con_demanda_valida": valid_ids,
            "correspondencias_provisionales": len(prov_map),
            "provisionales_que_coinciden_con_portal_frozen": len(set(prov_map) & set(lines_by_id)),
            "grupos_muchos_a_uno": len(groups),
            "estados": dict(Counter(r["estado_propuesto"].split(":")[0] for r in r1_rows)),
            "shipped_vacio_en_lineas_validas": sum(r["shipped_vacio_en_validas"] for r in r1_rows),
            "shipped_vacio_total": sum(
                1 for r in po_frozen.records if not r["shipped_raw"].strip()
            ),
        },
        "frozen_definitivo_oficial": public(official)
        if official["status"] != "PENDIENTE"
        else official,
        "diagnostico_NO_GO_como_replay": {
            "reglas_usadas": "provistas (" + rules_state + ")" if rules_data else "v2 vacías",
            "nota": "viable sin aprobaciones; nunca oficial ni promovible",
            **(public(diagnostic) or {}),
        },
        "escenario_provisional_NO_OFICIAL": public(scenario) or scenario_error,
        "oe965_original": {"item": len(erp_items), "lineas": len(erp), "claves": len(by_key)},
    }
    Path(a.out).write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", "utf-8")
    private = {
        "AVISO": "PRIVADO: identidades reales; no compartir ni subir a GitHub",
        "claves_nuevas_en_corte_posterior": new_keys,
        "escenario_NO_OFICIAL": {k: v for k, v in (scenario or {}).items() if k.startswith("_")},
        "grupos_muchos_a_uno": {g: {"erp": e, "portal": p} for g, (e, p) in groups.items()},
    }
    out = priv / "conciliacion_nov2026_privado.json"
    check_private_output(out)
    out.write_text(json.dumps(private, indent=1, ensure_ascii=False), "utf-8")
    for f in work.iterdir():
        f.unlink()
    work.rmdir()
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
