"""Capa de servicio de PULSO NEXUS Preview: calcula una generación de la Junta Operativa.

NO es un motor nuevo. Reutiliza sin cambios:
  - lectores físicos certificados (OE965 = backlog R_YYYYMMDD.TXT = 29 OERAS, Portal CAT, IN092U);
  - ``nexus.core.coverage.frozen_universe`` y ``compute`` (las mismas reglas del replay);
  - el mapeo provisional R3/R1 ya entregado (``conciliacion_nov2026``, con la corrección D-01).

Por escenario se ejecuta ``compute`` dos veces con el MISMO universo (el del Frozen):
  Frozen    = backlog R Frozen + Portal Frozen  (inmutable en el mes)
  Actual    = último backlog R válido + último Portal válido (Current: nunca amplía el universo)
FG: último IN092U válido. Embarques: no hay fuente autorizada ⇒ embarcado/faltante oficial N/D.

Lo único propio de esta capa (pieza mínima, sin reglas comerciales): presentar los hechos del
motor por ITEM, la brecha DIAGNÓSTICA ``max(Frozen − FG, 0)`` (no es el faltante oficial) y el
reparto por semana de las líneas del pendiente actual según SU fecha de entrega (sin inventar
fechas: la demanda Portal agregada no se reparte).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import zoneinfo
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from nexus.core.coverage import CoverageInput, compute, frozen_universe
from nexus.core.rules import UNKNOWN_CLASS, core_rules_from_dict
from nexus.core.sources import read_source
from nexus.core.stage import R1Resolution, _with_r1, contract_csv, parse_homologation, read_only
from nexus.core.timebase import BusinessCalendar
from nexus.hashing import digest_json

from . import conciliacion_nov2026 as conc
from .conectores import acreditacion_embarques

SCHEMA = "nexus.preview.generacion/1"
# FIX5: la facturación CFDI ES el embarque (regla ASM). El CORE atribuye embarques por MOVIMIENTO
# (movement_id, SHIP/REVERSAL); la captura CFDI es un agregado por ITEM, así que esa atribución no
# aplica. Embarques, Programa, Sobreembarque y Faltante se calculan con el contrato v3.10 vigente.
SHIPMENTS_REASON = (
    "captura CFDI agregada por ITEM: la atribución por movimiento del CORE no aplica "
    "(Embarques/Programa/Faltante: contrato v3.10)"
)
ESCENARIOS = {
    "A": "Provisional A — «en revisión» se conserva; grupos R1 de revisiones pendientes",
    "B": "Provisional B — «en revisión» se sustituye; grupos R1 de revisiones homologados",
    "DIAG": "Diagnóstico NO-GO — sin clasificaciones R3 ni homologaciones R1 (lo que hay aprobado)",
    "OFICIAL": "Oficial — reglas R3 aprobadas y registro R1 verificado",
}
ND = {
    "embarques": (
        "N/D: sin acreditación vigente de la fuente CFDI (regla: factura emitida = embarque)"
    ),
    "faltante": "N/D: Faltante = max(0, Programa − FG) requiere Programa, y Programa requiere Embarques autorizados (v3.10)",
    "programa": "N/D: Programa = max(0, Frozen + Adelanto − Embarques) requiere Embarques autorizados (v3.10)",
    "sobreembarque": "N/D: Sobreembarque = max(0, Embarques − Frozen − Adelanto) requiere Embarques (v3.10)",
    "cobertura_v310": "N/D: «Cobertura FG hasta» v3.10 descuenta primero los Embarques acreditados",
    "wo": "N/D: sin contrato ni fuente autorizada de órdenes de trabajo para esta vista",
    "forecast": "N/D: sin fuente de forecast entregada",
    "recuperacion": "N/D: sin contrato de recuperación",
    "estado_pulso": "N/D: la aplicación PULSO no se consulta (sin autorización)",
}


class RefreshError(Exception):
    """Validación fallida: la generación vigente se conserva."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass
class Config:
    month: str
    tz: str
    src: Path
    r3_provisional: Path | None = None
    r1_provisional: Path | None = None
    rules: Path | None = None
    homologation: Path | None = None
    ledger: Path | None = None
    approvers: Path | None = None
    asignaciones: Path | None = None
    escenario_por_defecto: str = "A"
    # FIX6: se activa una vez por migración controlada, sin modificar el Frozen de noviembre.
    ssrs_automatico: bool = False
    data_dir: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Config:
        raw = json.loads(path.read_text("utf-8"))
        if raw.get("schema") != "nexus.preview.config/1":
            raise ValueError("preview.json: esquema esperado nexus.preview.config/1")

        def p(key: str) -> Path | None:
            v = raw.get(key)
            if not v:
                return None
            q = Path(v)
            return q if q.is_absolute() else (path.parent / q)

        src = p("src")
        if src is None:
            raise ValueError("preview.json: falta 'src'")
        auto = raw.get("ssrs_automatico", False)
        if not isinstance(auto, bool):
            raise ValueError("preview.json: ssrs_automatico debe ser booleano")
        return cls(
            month=raw["month"],
            tz=raw.get("tz", "America/Mexico_City"),
            src=src,
            r3_provisional=p("r3_provisional"),
            r1_provisional=p("r1_provisional"),
            rules=p("rules"),
            homologation=p("homologation"),
            ledger=p("ledger"),
            approvers=p("approvers"),
            asignaciones=p("asignaciones"),
            escenario_por_defecto=raw.get("escenario_por_defecto", "A"),
            ssrs_automatico=auto,
            data_dir=path.parent,
        )

    def private_files(self) -> dict[str, Path]:
        return {
            k: v
            for k, v in (
                ("r3_provisional", self.r3_provisional),
                ("r1_provisional", self.r1_provisional),
                ("rules", self.rules),
                ("homologation", self.homologation),
                ("ledger", self.ledger),
                ("approvers", self.approvers),
                ("asignaciones", self.asignaciones),
            )
            if v is not None
        }


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _contract(source_id: str, records: list[dict[str, str]], work: Path) -> Any:
    path = work / f"{source_id}_{len(list(work.iterdir()))}.csv"
    path.write_bytes(contract_csv(source_id, records))
    parsed = read_source(source_id, path)
    if not parsed.valid:
        raise RefreshError([f"contrato {source_id} inválido: {parsed.errors[:2]}"])
    return parsed


# ------------------------------------------------------------------ 1. fuentes y precedencia
def scan_sources(
    cfg: Config, frozen_lock: dict[str, Any] | None, capturas: list[tuple[dict[str, Any], Any]] | None = None,
    fg_directo: str | None = None,
    backlog_directo: str | None = None,
) -> dict[str, Any]:
    """Valida TODOS los archivos reconocidos de la carpeta y elige Frozen / Current / FG.
    Cualquier archivo reconocido e inválido detiene el Refresh (se conserva la vista vigente)."""
    if not cfg.src.is_dir():
        raise RefreshError([f"carpeta de fuentes no disponible: {cfg.src.name}"])
    inv = conc.inventory(cfg.src, cfg.tz)
    zone = zoneinfo.ZoneInfo(cfg.tz)
    for f in inv["files"]:
        f.setdefault("origen", "MANUAL")
        f.setdefault("instante", "")
        if f["kind"] == "in092u" and f["acceptance"] == "PASS":
            # v0.3.1: la fecha documental del IN092U es la fecha LOCAL del corte (el lector la da en
            # UTC); el instante UTC se conserva en document_time para trazabilidad y orden.
            utc = str(inv["parsed"][f["file"]].document_time.get("value") or "")
            try:
                moment = dt.datetime.fromisoformat(utc.replace("Z", "+00:00"))
                f["document_date"] = moment.astimezone(zone).date().isoformat()
            except ValueError:
                pass
    for desc, parsed_capture in capturas or []:  # v0.3: consultas directas (SSRS / ODBC) ya validadas
        inv["files"].append({**desc, "acceptance": "PASS" if parsed_capture.valid else "FAIL",
                             "sha256_expected": None, "sha256_matches": None})
        inv["parsed"][desc["file"]] = parsed_capture
    errors: list[str] = []
    warnings: list[str] = []
    files = inv["files"]
    if cfg.ssrs_automatico:
        # FIX6: el TXT ERP solo puede participar como Frozen del lock heredado (noviembre).
        # Ningún R manual adicional ni IN092U manual entra en el Refresh automático.
        legacy_frozen = str((frozen_lock or {}).get("oe965", {}).get("file", ""))
        ignored = [f for f in files if f["origen"] == "MANUAL" and (
            f["kind"] == "in092u" or (f["kind"] == "oe965" and f["file"] != legacy_frozen)
        )]
        files = [f for f in files if f not in ignored]
        inv["files"] = files
        if ignored:
            warnings.append(f"FIX6: {len(ignored)} extracto(s) ERP/FG manuales ignorados; "
                            "no son fuentes operativas")
    for f in files:
        if f["acceptance"] != "PASS":
            errors.append(f"{f['file']}: lectura estructural inválida (archivo rechazado)")
    in_month = [
        f for f in files if f["acceptance"] == "PASS" and (f["document_date"] or "").startswith(cfg.month)
    ]
    out_month = [f["file"] for f in files if f["acceptance"] == "PASS" and f not in in_month]
    if out_month:
        warnings.append(f"{len(out_month)} archivo(s) fuera del mes {cfg.month}: no se usan")

    def by_kind(kind: str) -> list[dict[str, Any]]:
        rows = [f for f in in_month if f["kind"] == kind]
        # Mismo día: el manual precede a la consulta directa (Frozen) y la consulta directa más
        # reciente es la última (Current). Entre capturas, el instante de consulta ordena.
        return sorted(rows, key=lambda f: (f["document_date"] or "", f["origen"] != "MANUAL", f["instante"], f["file"]))

    r_files, p_files, fg_files = by_kind("oe965"), by_kind("portal"), by_kind("in092u")
    if not r_files:
        errors.append(f"sin Backlog SSRS válido del mes {cfg.month}" if cfg.ssrs_automatico
                      else f"sin backlog R válido del mes {cfg.month}")
    if not p_files:
        errors.append(f"sin Portal CAT válido del mes {cfg.month}")
    if not fg_files:
        errors.append(f"sin FG ODBC válido del mes {cfg.month}" if cfg.ssrs_automatico
                      else f"sin IN092U válido del mes {cfg.month}")
    if errors:
        raise RefreshError(errors)

    def pick_frozen(kind: str, rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
        if frozen_lock and kind in frozen_lock:
            lock = frozen_lock[kind]
            match = [f for f in rows if f["file"] == lock["file"]]
            if not match or match[0]["sha256"] != lock["sha256"]:
                raise RefreshError(
                    [f"{label} Frozen bloqueado ({lock['file']}) ausente o alterado: no se sustituye"]
                )
            earlier = [f["file"] for f in rows if (f["document_date"] or "") < (lock["document_date"] or "")]
            if earlier:
                warnings.append(
                    f"{label}: {len(earlier)} archivo(s) con fecha anterior al Frozen bloqueado; "
                    "el Frozen NO se sustituye (requiere decisión del dueño del proceso)"
                )
            return match[0]
        first = rows[0]
        same_day = [f for f in rows if f["document_date"] == first["document_date"]]
        manual = {f["sha256"] for f in same_day if f["origen"] == "MANUAL"}
        if len(manual) > 1:
            raise RefreshError(
                [f"{label}: precedencia ambigua (dos archivos distintos con la fecha {first['document_date']})"]
            )
        if len(same_day) > 1:
            warnings.append(
                f"{label}: {len(same_day)} cortes el {first['document_date']}; Frozen = "
                f"{'archivo manual' if first['origen'] == 'MANUAL' else 'primera consulta directa del día'}"
            )
        return first

    # ERP excepcional jamás puede construir ni reemplazar un Frozen.
    frozen_candidates = [f for f in r_files if f["origen"] != "ERP_EXCEPCIONAL"]
    if not frozen_candidates:
        raise RefreshError(["FIX6: sin captura SSRS elegible para Frozen"])
    if cfg.ssrs_automatico and not frozen_lock:
        from .conectores import confirmacion_ssrs  # noqa: PLC0415

        frozen_r, evidencia = confirmacion_ssrs.confirmar_frozen(
            cfg.data_dir, cfg.month, frozen_candidates
        )
        frozen_r["confirmacion_ssrs"] = evidencia
    else:
        frozen_r = pick_frozen("oe965", frozen_candidates, "Backlog Frozen")
        if cfg.ssrs_automatico and frozen_r["origen"] == "CONSULTA_DIRECTA":
            from .conectores import confirmacion_ssrs  # noqa: PLC0415

            confirmacion_ssrs.verificar_bloqueo(cfg.data_dir, cfg.month, frozen_lock)
    frozen_p = pick_frozen("portal", p_files, "Portal CAT")
    ssrs_current = [f for f in r_files if f["origen"] == "CONSULTA_DIRECTA"]
    exception_present = bool(backlog_directo and backlog_directo.startswith("erp_urgente:"))
    if cfg.ssrs_automatico and not ssrs_current and not exception_present:
        raise RefreshError(["FIX6: no hay Backlog Current SSRS para el mes; "
                            "prohibido usar TXT ERP como contingencia"])
    current_r = (ssrs_current[-1] if ssrs_current else r_files[-1]) if cfg.ssrs_automatico else r_files[-1]
    current_p = p_files[-1]
    if current_r["document_date"] < frozen_r["document_date"] and not cfg.ssrs_automatico:
        current_r = frozen_r
    if current_p["document_date"] < frozen_p["document_date"]:
        current_p = frozen_p
    if backlog_directo:  # FIX6: consulta SSRS de este Refresh u override ERP explícito
        forzado = _forzar_current(r_files, backlog_directo, warnings, cfg.month)
        current_r = forzado if forzado is not None else current_r
    if cfg.ssrs_automatico and current_r["origen"] not in ("CONSULTA_DIRECTA", "ERP_EXCEPCIONAL"):
        raise RefreshError(["FIX6: Current no proviene de SSRS ni de excepción ERP explícita"])

    def fg_key(f: dict[str, Any]) -> tuple[str, bool, str]:
        t = str(inv["parsed"][f["file"]].document_time.get("value") or "")
        return (t.replace("+00:00", "Z"), f["origen"] != "MANUAL", f["file"])

    fg = max(fg_files, key=fg_key)
    if fg_directo:
        # v0.3.2: con una consulta FG exitosa en ESTE Refresh, el FG es esa consulta; ningún IN092U
        # manual (aunque tenga un instante igual o posterior) la sustituye.
        directo = [f for f in fg_files if f["file"] == fg_directo]
        if directo:
            ignorados = [f["file"] for f in fg_files if f["origen"] == "MANUAL" and fg_key(f)[0] >= fg_key(directo[0])[0]]
            if ignorados:
                warnings.append(f"FG: {len(ignorados)} IN092U manual(es) con corte igual o posterior a la consulta directa "
                                f"({', '.join(ignorados)}): no se usan; FG = consulta directa de este Refresh")
            fg = directo[0]
        else:
            warnings.append(f"FG: la consulta directa de este Refresh no pertenece al mes {cfg.month}: se usa el último corte válido del mes")
    if current_p["file"] == frozen_p["file"]:
        warnings.append("sin Portal posterior al Frozen: el pendiente actual conserva el último corte Portal disponible")
    if current_r["file"] == frozen_r["file"]:
        warnings.append("sin backlog posterior al Frozen: Current utiliza el mismo corte")
    if cfg.ssrs_automatico:
        warnings.append("SSRS: el Refresh lee el último corte publicado; el origen se actualiza overnight")

    def describe(f: dict[str, Any]) -> dict[str, Any]:
        t = inv["parsed"][f["file"]].document_time
        return {
            "file": f["file"],
            "origen": f.get("origen", "MANUAL"),
            "sha256": f["sha256"],
            "document_date": f["document_date"],
            "document_time": t.get("value"),
            "instante": f.get("instante") or None,
            "records": f["records"],
            **({"confirmacion_ssrs": f["confirmacion_ssrs"]} if f.get("confirmacion_ssrs") else {}),
        }

    return {
        "inv": inv,
        "frozen_r": describe(frozen_r),
        "frozen_p": describe(frozen_p),
        "current_r": describe(current_r),
        "current_p": describe(current_p),
        "fg": describe(fg),
        "all_files": [
            {k: f.get(k) for k in ("file", "kind", "origen", "sha256", "acceptance", "document_date", "records")}
            for f in files
        ],
        "warnings": warnings,
    }


# ------------------------------------------------------------------ 2. reglas y R1 por escenario
def _read_private_csv(path: Path | None) -> list[dict[str, str]]:
    return conc.read_csv(path) if path is not None and path.is_file() else []


def scenario_inputs(cfg: Config, erp_items: frozenset[str], received: str) -> dict[str, Any]:
    rules_data, rules_state = conc.load_rules(cfg.rules if cfg.rules and cfg.rules.is_file() else None)
    r3 = _read_private_csv(cfg.r3_provisional)
    r1 = _read_private_csv(cfg.r1_provisional)
    pcol = conc.pick(r1[0], tuple(p for p, _ in conc.PROV_COLUMNS)) if r1 else None
    ecol = dict(conc.PROV_COLUMNS).get(pcol or "")
    prov: dict[str, set[str]] = defaultdict(set)
    for row in r1:
        if pcol and ecol and row.get(pcol) and row.get(ecol):
            prov[row[pcol]].add(row[ecol])
    targets: dict[str, set[str]] = defaultdict(set)
    for p, ts in prov.items():
        for t in ts:
            targets[t].add(p)
    grouped = {p for ps in targets.values() if len(ps) > 1 for p in ps}
    single = {p: next(iter(t)) for p, t in prov.items() if len(t) == 1}
    official_r1 = R1Resolution({}, frozenset(), {})
    r1_state = "AUSENTE"
    if cfg.homologation and cfg.ledger and cfg.approvers and cfg.homologation.is_file():
        official_r1 = parse_homologation(
            read_only(cfg.homologation).decode("utf-8-sig"),
            json.loads(read_only(cfg.ledger).decode("utf-8")),
            frozenset(json.loads(read_only(cfg.approvers).decode("utf-8")).get("approvers", [])),
            received,
            erp_items,
        )
        r1_state = "REGISTRO_INVALIDO" if official_r1.report.get("errors") else "VERIFICADO"
    out: dict[str, Any] = {}
    if rules_data:
        diag = rules_data
    else:
        diag = conc.provisional_rules(None, [])
        diag["version"] = "diagnostico-v2-sin-clasificaciones-NO-OFICIAL"
    if rules_state == "APROBADAS_V2" and r1_state == "VERIFICADO":
        out["OFICIAL"] = (rules_data, official_r1)
    if r3:
        out["A"] = (
            conc.provisional_rules(rules_data, r3, "A"),
            R1Resolution({p: t for p, t in single.items() if p not in grouped}, frozenset(), {}),
        )
        out["B"] = (conc.provisional_rules(rules_data, r3, "B"), R1Resolution(dict(single), frozenset(), {}))
    out["DIAG"] = (diag, official_r1)
    meta = {
        "reglas_r3": rules_state,
        "registro_r1": r1_state,
        "matriz_r3_filas": len(r3),
        "matriz_r1_correspondencias": len(prov),
        "grupos_revision_r1": sum(1 for ps in targets.values() if len(ps) > 1),
        "candidatos_r1": {p: "|".join(sorted(t)) for p, t in sorted(prov.items())},
        "parametros": {
            "estados_portal_excluidos": diag["portal_excluded_statuses"],
            "stockrooms_fg_usables": diag["usable_fg_stockrooms"],
            "fecha_portal": diag["portal_date"],
        },
    }
    return {"scenarios": out, "meta": meta}


# ------------------------------------------------------------------ 3. hechos por ITEM
def _fact(f: Any) -> dict[str, Any]:
    return {
        "value": None if f.value is None else str(f.value),
        "status": f.status,
        "limitations": list(f.limitations)[-3:],
    }


def iso_weeks(anchor: str, n: int = 6) -> list[dict[str, str]]:
    d = dt.date.fromisoformat(anchor)
    monday = d - dt.timedelta(days=d.weekday())
    out = []
    for i in range(n):
        s = monday + dt.timedelta(weeks=i)
        e = s + dt.timedelta(days=6)
        out.append({"wk": f"WK{s.isocalendar()[1]:02d}", "start": s.isoformat(), "end": e.isoformat(), "pos": str(i + 1)})
    return out


def _weekly(units: list[dict[str, Any]], weeks: list[dict[str, str]], fg: Decimal | None, month_end: str) -> dict[str, Any]:
    """Reparto del pendiente ACTUAL por la fecha de entrega de cada línea ERP (cantidad) y
    cobertura FG DIAGNÓSTICA (vencidas primero, después por fecha; asignación PROVISIONAL)."""
    qty_reasons: list[str] = []
    if any(u["kind"] == "POOL" for u in units):
        qty_reasons.append("demanda Portal agregada: sin desglose semanal (no se inventan fechas)")
    if any(u["demand"] is None for u in units):
        qty_reasons.append("demanda desconocida en alguna línea")
    cov_reasons = list(qty_reasons)
    if fg is None:
        cov_reasons.append("FG desconocido o no certificable: cobertura no calculable")
    qty = [Decimal(0)] * len(weeks)
    overdue = Decimal(0)
    for u in units:
        if u["kind"] != "LINE" or u["demand"] is None or not u["due"]:
            continue
        if u["overdue"]:
            overdue += u["demand"]
            continue
        for i, w in enumerate(weeks):
            if w["start"] <= u["due"] <= w["end"]:
                qty[i] += u["demand"]
    cells = []
    left = None if cov_reasons else (fg or Decimal(0)) - overdue
    for i, w in enumerate(weeks):
        cell: dict[str, Any] = {"pos": w["pos"], "qty": None if qty_reasons else str(qty[i])}
        if w["start"] > month_end:
            cell["estado"] = "FUERA_DEL_MES"
        elif qty_reasons:
            cell["estado"] = "ND"
        elif qty[i] == 0:
            cell["estado"] = "SIN_DEMANDA"
        elif left is None:
            cell["estado"] = "SIN_COBERTURA_CALCULABLE"
        else:
            cell["estado"] = "CUBIERTA" if left >= qty[i] else "PARCIAL" if left > 0 else "SIN_COBERTURA"
            left -= qty[i]
        cells.append(cell)
    return {
        "cells": cells,
        "vencido": None if qty_reasons else str(overdue),
        "motivos": cov_reasons,
    }


def _rows(
    res_f: dict[str, Any],
    res_c: dict[str, Any],
    cr: Any,
    oeras_f: list[Any],
    portal_f: list[Any],
    weeks: list[dict[str, str]],
    month_end: str,
    assign: dict[str, Any],
) -> list[dict[str, Any]]:
    sub = cr.base.substitute_class
    names = {x.customer_key: x.customer_name for x in oeras_f}
    lines: dict[str, Counter[str]] = defaultdict(Counter)
    for x in oeras_f:
        cls = cr.classify(x.customer_key)
        lines[x.item]["UNKNOWN" if cls == UNKNOWN_CLASS else "CAT" if cls == sub else "KEPT"] += 1
    portal_items = {p.item for p in portal_f if p.item}
    rows = []
    for item in sorted(res_f["items"]):
        f = res_f["items"][item]
        c = res_c["items"].get(item)
        ff, cf = f["facts"], (c["facts"] if c else None)
        frozen = _fact(ff["demand"])
        actual = _fact(cf["demand"]) if cf else {"value": "0", "status": "PROVISIONAL_CALCULATED", "limitations": ["sin líneas en el corte actual"]}
        fg = _fact(ff["fg_assignable"])
        var = None
        if frozen["value"] is not None and actual["value"] is not None:
            var = str(Decimal(frozen["value"]) - Decimal(actual["value"]))
        gap = None
        if frozen["value"] is not None and fg["value"] is not None:
            gap = str(max(Decimal(frozen["value"]) - Decimal(fg["value"]), Decimal(0)))
        units_f = f["units"]
        cust: Counter[str] = Counter()
        detail: dict[str, dict[str, Any]] = {}
        for u in units_f:
            name = u["customer_key"] or f"{u['customer_class']} (Portal)"
            cust[name] += u["demand"] if u["demand"] is not None else 0
            d = detail.setdefault(
                name,
                {"clave": name, "nombre": names.get(u["customer_key"], "Portal CAT" if not u["customer_key"] else ""),
                 "clase": u["customer_class"], "lineas": 0, "frozen": Decimal(0), "desconocido": False},
            )
            d["lineas"] += max(1, len(u.get("source_lines") or []))
            if u["demand"] is None:
                d["desconocido"] = True
            else:
                d["frozen"] += u["demand"]
        principal = cust.most_common(1)[0][0] if cust else ""
        clientes_detalle = [
            {**d, "frozen": None if d["desconocido"] else str(d["frozen"])}
            for d in sorted(detail.values(), key=lambda d: (-d["frozen"], d["clave"]))
        ]
        sem_frozen, sem_frozen_ok = [Decimal(0)] * len(weeks), True
        for u in units_f:
            if u["kind"] == "POOL" or u["demand"] is None:
                sem_frozen_ok = False
                continue
            for i, w in enumerate(weeks):
                if u["due"] and w["start"] <= u["due"] <= w["end"]:
                    sem_frozen[i] += u["demand"]
        dues = sorted(u["due"] for u in units_f if u["due"])
        k = lines.get(item, Counter())
        origin = []
        if k["KEPT"]:
            origin.append("ERP")
        if item in portal_items:
            origin.append("PORTAL_CAT")
        if k["UNKNOWN"]:
            origin.append("PENDIENTE_R3")
        if k["CAT"] and not (k["KEPT"] or k["UNKNOWN"] or item in portal_items):
            origin.append("PENDIENTE_R1")
        weekly = _weekly(
            (c["units"] if c else []),
            weeks,
            Decimal(fg["value"]) if fg["value"] is not None else None,
            month_end,
        )
        a = resolve_assignment(assign, principal)
        unknown = any(x["value"] is None for x in (frozen, actual, fg))
        pending = any(o.startswith("PENDIENTE") for o in origin)
        rows.append(
            {
                "item": item,
                "cliente": principal,
                "cliente_nombre": names.get(principal, "Portal CAT" if principal.endswith("(Portal)") else ""),
                "clientes": len(cust),
                "clientes_detalle": clientes_detalle,
                "semanas_frozen": [str(q) for q in sem_frozen],
                "semanas_frozen_completas": sem_frozen_ok,
                "embarques": None,
                "cs": a.get("cs", ""),
                "grupo": a.get("grupo", ""),
                "cs_origen": a["cs_origen"],
                "origen": "+".join(origin) or "SIN_ORIGEN",
                "frozen": frozen,
                "actual": actual,
                "variacion": var,
                "fg": fg,
                "brecha_diag": gap,
                "semanas": weekly,
                "fecha_solicitada": dues[0] if dues else None,
                "flags": {
                    "desconocido": unknown,
                    "pendiente": pending,
                    "brecha": gap is not None and Decimal(gap) > 0,
                    "sin_cobertura": any(cl["estado"] in ("SIN_COBERTURA", "PARCIAL") for cl in weekly["cells"]),
                },
                "review": "REVISAR" if (unknown or pending) else "OK",
            }
        )
    return rows


def _total(res: dict[str, Any], metric: str) -> dict[str, Any]:
    t = res["totals"].get(metric, {})
    complete = bool(t.get("complete", False)) and t.get("value") is not None
    known = t.get("known_sum", t.get("value"))
    return {
        "value": str(t["value"]) if complete else None,
        "known_sum": None if known is None else str(known),
        "complete": complete,
        "unknown_items": t.get("unknown_items", t.get("items_unknown")),
        "blocked_by": t.get("blocked_by", []),
    }


def _kpis(res_f: dict[str, Any], res_c: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    def total(field_: str) -> dict[str, Any]:
        vals = [r[field_]["value"] for r in rows]
        known = [Decimal(v) for v in vals if v is not None]
        missing = sum(1 for v in vals if v is None)
        return {
            "value": str(sum(known, Decimal(0))) if missing == 0 else None,
            "known_sum": str(sum(known, Decimal(0))),
            "unknown_items": missing,
            "causa": None if missing == 0 else f"{missing} artículo(s) con valor desconocido o pendiente de homologación",
        }

    frozen, actual, fg = total("frozen"), total("actual"), total("fg")
    var = None
    if frozen["value"] is not None and actual["value"] is not None:
        var = str(Decimal(frozen["value"]) - Decimal(actual["value"]))
    return {
        "frozen": frozen,
        "actual": actual,
        "variacion": {
            "value": var,
            "causa": None if var is not None else "requiere Frozen y pendiente actual completos",
            "nota": "informativa: no es embarque ni faltante",
        },
        "fg": fg,
        "faltante": {"value": None, "causa": ND["faltante"]},
        "desconocidos_o_pendientes": sum(1 for r in rows if r["flags"]["desconocido"] or r["flags"]["pendiente"]),
        "items": len(rows),
        "motor_totales_frozen": _total(res_f, "demand"),
        "motor_totales_actual": _total(res_c, "demand"),
    }


def load_assignments(path: Path | None) -> dict[str, Any]:
    """Asignación privada (opcional) de responsable CS y grupo comercial, con identidad EXACTA.

    Formato simple: ``clave_cliente,cs,grupo``. Formato de reglas de PULSO Web (REIMPLEMENTADO de
    ``index_csr``/``csr_for``): ``Match Type`` (CUSTOMERNO | CUSTOMER_GROUP), ``Match Value``,
    ``CSR``, ``Active``. La regla por cuenta prevalece sobre la de grupo; dos responsables
    distintos para la misma regla = conflicto (sin asignar). Sin normalizar claves (X15)."""
    cuentas: dict[str, dict[str, str]] = {}
    reglas: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in _read_private_csv(path):
        key = row.get("clave_cliente") or row.get("customer_key")
        if key:
            cuentas[key] = {"cs": (row.get("cs") or "").strip(), "grupo": (row.get("grupo") or "").strip()}
        mt = (row.get("Match Type") or "").strip().upper()
        mv = row.get("Match Value") or ""
        csr = (row.get("CSR") or "").strip()
        active = (row.get("Active") or "").strip().upper() in ("TRUE", "1", "YES", "SI", "SÍ")
        if active and mt in ("CUSTOMERNO", "CUSTOMER_GROUP") and mv and csr:
            reglas[(mt, mv)].add(csr)
    return {"cuentas": cuentas, "reglas": {k: (next(iter(v)) if len(v) == 1 else None) for k, v in reglas.items()}}


def resolve_assignment(assign: dict[str, Any], customer_key: str) -> dict[str, str]:
    base = dict(assign.get("cuentas", {}).get(customer_key, {}))
    grupo = base.get("grupo", "")
    reglas = assign.get("reglas", {})
    cs = base.get("cs", "")
    origen = "archivo de cuentas" if cs else ""
    if ("CUSTOMERNO", customer_key) in reglas:
        cs, origen = reglas[("CUSTOMERNO", customer_key)] or "", "regla por cuenta"
        if not cs:
            origen = "CONFLICTO en regla por cuenta"
    elif grupo and ("CUSTOMER_GROUP", grupo) in reglas:
        cs, origen = reglas[("CUSTOMER_GROUP", grupo)] or "", "regla por grupo"
        if not cs:
            origen = "CONFLICTO en regla por grupo"
    return {"cs": cs, "grupo": grupo, "cs_origen": origen or "sin asignación"}


# ------------------------------------------------------------------ 4. generación completa
def build_generation(
    cfg: Config,
    frozen_lock: dict[str, Any] | None,
    work: Path,
    code_version: str,
    capturas: list[tuple[dict[str, Any], Any]] | None = None,
    embarques: dict[str, Any] | None = None,
    fg_conector: dict[str, Any] | None = None,
    backlog_conector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fgx = dict(fg_conector or {"estado": "MANUAL"})
    bkx = dict(backlog_conector or {"estado": "MANUAL"})
    fg_dir = fgx.get("archivo") if fgx.get("estado") == "CONSULTA_DIRECTA" else None
    bk_dir = bkx.get("archivo") if bkx.get("estado") in BACKLOG_DIRECTO else None
    scan = scan_sources(cfg, frozen_lock, capturas, fg_dir, bk_dir)
    scan["fg"]["conector"] = fgx = _fg_traza(fgx, scan)
    scan["current_r"]["conector"] = bkx = _backlog_traza(bkx, scan)
    emb = embarques or {"acreditado": False, "motivos": ["conector de embarques no configurado"]}
    inv = scan["inv"]
    parsed = inv["parsed"]
    oeras_f = _contract("oeras_29", parsed[scan["frozen_r"]["file"]].records, work).records
    oeras_c = _contract("oeras_29", parsed[scan["current_r"]["file"]].records, work).records
    fg = _contract("fg_inventory", parsed[scan["fg"]["file"]].records, work)
    erp_items = frozenset({x.item for x in oeras_f} | {r.item for r in fg.records})
    received = scan["frozen_r"]["document_date"] or f"{cfg.month}-01"
    sc = scenario_inputs(cfg, erp_items, received)
    if fgx["estado"] == "CONSULTA_DIRECTA" and scan["fg"]["origen"] == "CONSULTA_DIRECTA":
        usables = {s for rules, _ in sc["scenarios"].values() for s in rules.get("usable_fg_stockrooms", [])}
        fuera = sorted(usables - set(fgx.get("alcance") or []))
        if fuera:
            raise RefreshError([f"FG: la acreditación (alcance {fgx.get('alcance')}) no cubre los stockrooms usables {fuera}: "
                                "no se usa la consulta directa; se conserva la generación vigente"])
    calendar = BusinessCalendar(cfg.tz)
    window = None if calendar.is_utc else calendar.window_utc(cfg.month)[0]
    year, mon = (int(x) for x in cfg.month.split("-"))
    month_end = (dt.date(year + mon // 12, mon % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()
    anchor = scan["current_r"]["document_date"] or f"{cfg.month}-01"
    weeks = iso_weeks(anchor)
    assign = load_assignments(cfg.asignaciones)
    scenarios: dict[str, Any] = {}
    for name, (rules_dict, r1) in sc["scenarios"].items():
        cr = core_rules_from_dict(rules_dict)
        portal_f = _contract("portal_cat", [_with_r1(r, r1) for r in parsed[scan["frozen_p"]["file"]].records], work).records
        portal_c = _contract("portal_cat", [_with_r1(r, r1) for r in parsed[scan["current_p"]["file"]].records], work).records
        fu = frozen_universe(oeras_f, portal_f, cr)
        fu_c = frozen_universe(oeras_c, portal_c, cr)

        def run(oeras: list[Any], portal: list[Any], pending: tuple[str, ...], cr: Any = cr, fu: Any = fu) -> dict[str, Any]:
            return compute(
                CoverageInput(
                    month=cfg.month,
                    cut_ts=str(fg.instant),
                    oeras=oeras,
                    portal=portal,
                    fg=fg.records,
                    shipments=None,
                    rules=cr,
                    certified=False,
                    window_start_utc=window,
                    blocked={"shipments": SHIPMENTS_REASON},
                    universe=fu.items,
                    pending_portal=pending,
                    universe_counts=fu.counts,
                )
            )

        res_f = run(oeras_f, portal_f, fu.pending_portal)
        res_c = run(oeras_c, portal_c, fu_c.pending_portal)
        rows = _rows(res_f, res_c, cr, oeras_f, portal_f, weeks, month_end, assign)
        # FIX5.1: solo fuente ACREDITADA; clasificación explícita (nulo, identidad no exacta,
        # ausente, fuera del universo, sin consulta): ningún desconocido se convierte en 0
        por_item = emb.get("por_item") if emb.get("acreditado") else None
        valores, emb.setdefault("clasificacion", {})[name] = acreditacion_embarques.asignar(
            [r["item"] for r in rows], por_item, emb.get("ausencia_es_cero") is True
        )
        for r in rows:
            r["embarques"] = valores[r["item"]]
        week_summary = []
        for i, w in enumerate(weeks):
            cells = [r["semanas"]["cells"][i] for r in rows]
            known = [c for c in cells if c["estado"] not in ("ND", "FUERA_DEL_MES")]
            covered = [c for c in known if c["estado"] in ("CUBIERTA", "PARCIAL", "SIN_COBERTURA")]
            week_summary.append(
                {
                    **w,
                    "fuera_del_mes": w["start"] > month_end,
                    "items_con_demanda": sum(1 for c in known if c["estado"] != "SIN_DEMANDA"),
                    "frozen_qty": str(sum((Decimal(r["semanas_frozen"][i]) for r in rows), Decimal(0))),
                    "frozen_items": sum(1 for r in rows if Decimal(r["semanas_frozen"][i]) > 0),
                    "frozen_items_sin_desglose": sum(1 for r in rows if not r["semanas_frozen_completas"]),
                    "qty_lineas_erp": str(sum((Decimal(c["qty"]) for c in known if c["qty"]), Decimal(0))),
                    "items_sin_desglose": sum(1 for c in cells if c["estado"] == "ND"),
                    "cobertura": dict(Counter(c["estado"] for c in covered)),
                    "items_sin_cobertura_calculable": sum(
                        1 for c in known if c["estado"] == "SIN_COBERTURA_CALCULABLE"
                    ),
                }
            )
        unknown_keys = sorted(set(res_f.get("unclassified_customers", [])))
        by_key: dict[str, list[Any]] = defaultdict(list)
        for x in oeras_f:
            by_key[x.customer_key].append(x)
        portal_lines: dict[str, int] = defaultdict(int)
        for p in portal_f:
            if not p.item:
                portal_lines[p.item_portal] += 1
        pendientes = {
            "claves_r3": [
                {
                    "clave": k,
                    "lineas": len(by_key[k]),
                    "items": len({x.item for x in by_key[k]}),
                    "causa": "clave de cliente sin clasificación R3 aprobada (UNKNOWN, V17)",
                }
                for k in unknown_keys
            ],
            "portal_r1": [
                {
                    "item_portal": pid,
                    "lineas": portal_lines.get(pid, 0),
                    "candidato_provisional": sc["meta"]["candidatos_r1"].get(pid, ""),
                    "causa": "Item ID del Portal sin homologación R1 aplicable en este escenario",
                }
                for pid in fu.pending_portal
            ],
            "items_no_determinables": [
                {"item": r["item"], "causa": " · ".join(r["frozen"]["limitations"][-2:]) or r["frozen"]["status"]}
                for r in rows
                if r["frozen"]["value"] is None
            ],
        }
        scenarios[name] = {
            "nombre": ESCENARIOS[name],
            "pendientes": pendientes,
            "completo": not unknown_keys and not fu.pending_portal,
            "reglas": {"version": cr.version, "approval": cr.base.approval},
            "construccion_frozen": dict(sorted(fu.counts.items())),
            "items_current_fuera_del_universo": len({x.item for x in oeras_c} - set(fu.items)),
            "pendientes_r1": len(fu.pending_portal),
            "claves_sin_clase": len(res_f.get("unclassified_customers", [])),
            "kpis": _kpis(res_f, res_c, rows),
            "semanas": week_summary,
            "rows": rows,
        }
    inputs = {
        "month": cfg.month,
        "tz": cfg.tz,
        "sources": {k: scan[k]["sha256"] for k in ("frozen_r", "frozen_p", "current_r", "current_p", "fg")},
        "embarques": (emb.get("captura") or {}).get("sha256") if emb.get("acreditado") else None,
        # FIX5: estado y corte (instante) de la captura de Embarques usada
        "embarques_conector": {
            "estado": emb.get("estado"),
            "corte": (emb.get("captura") or {}).get("instante_utc")
            if emb.get("acreditado")
            else None,
        },
        "embarques_acreditacion": emb.get("acreditacion_sha256"),
        # v0.3.2: estado del conector FG y corte (instante) de la consulta directa
        "fg_conector": {"estado": fgx["estado"], "acreditacion_sha256": fgx.get("acreditacion_sha256"),
                        "corte": scan["fg"]["document_time"] if scan["fg"]["origen"] == "CONSULTA_DIRECTA" else None},
        "private": {k: sha(read_only(v)) for k, v in sorted(cfg.private_files().items()) if v.is_file()},
        "code": code_version,
        # v0.3.3: estado del conector Backlog SSRS y corte (instante) de la consulta directa
        "backlog_conector": {
            "estado": bkx["estado"],
            "acreditacion_sha256": bkx.get("acreditacion_sha256"),
            "corte": scan["current_r"]["instante"] if bkx["estado"] in BACKLOG_DIRECTO else None,
        },
    }
    vista = elegir_vista(scenarios, sc["meta"])
    aprob = {
        "reglas_r3": sc["meta"]["reglas_r3"],
        "registro_r1": sc["meta"]["registro_r1"],
        "huellas": {k: v for k, v in inputs["private"].items() if k in ("rules", "homologation", "ledger", "approvers")},
    }
    return {
        "schema": SCHEMA,
        "vista_operativa": vista,
        # intento_utc es del Refresh, no de la generación: no entra al contenido inmutable
        "embarques": {k: v for k, v in emb.items() if k not in ("por_item", "intento_utc")},
        "aprobaciones": aprob,
        "gen_id": digest_json(inputs)[:24],
        "inputs": inputs,
        "month": cfg.month,
        "sources": {k: scan[k] for k in ("frozen_r", "frozen_p", "current_r", "current_p", "fg")},
        "archivos": scan["all_files"],
        "warnings": scan["warnings"]
        + fgx.pop("_avisos", [])
        + bkx.pop("_avisos", [])
        + _avisos_embarques(emb)
        + (
            [f"preview.json pide escenario «{cfg.escenario_por_defecto}»: se ignora; la vista operativa la fija la regla de aprobaciones"]
            if cfg.escenario_por_defecto not in ("A", vista["escenario"])
            else []
        ),
        "meta": sc["meta"],
        "semanas": weeks,
        "escenarios": scenarios,
        # v0.2.1: la vista operativa NO se elige por configuración; se conserva el campo por
        # compatibilidad (rollback a v0.2) con el MISMO escenario que la vista operativa.
        "escenario_por_defecto": vista["escenario"],
        "certificacion": "PROVISIONAL / NO-GO",
        "nd": ND,
    }


BACKLOG_DIRECTO = ("CONSULTA_DIRECTA", "IMPORTACION_INTERACTIVA", "ERP_EXCEPCIONAL")


def _forzar_current(
    r_files: list[dict[str, Any]], archivo: str, warnings: list[str], month: str
) -> dict[str, Any] | None:
    """v0.3.3: con una consulta SSRS exitosa en ESTE Refresh, el Current ERP es esa consulta;
    ningún R manual (aunque tenga fecha igual o posterior) ni captura histórica la sustituye.
    El Frozen no se toca (se elige antes, por la regla del primer corte y el bloqueo)."""
    directo = [f for f in r_files if f["file"] == archivo]
    if not directo:
        warnings.append(
            f"Backlog: la consulta SSRS de este Refresh no pertenece al mes {month}: "
            "Current = último corte válido del mes"
        )
        return None
    fecha = directo[0]["document_date"] or ""
    manuales = [f for f in r_files if f["origen"] == "MANUAL"]
    ignorados = [f["file"] for f in manuales if (f["document_date"] or "") >= fecha]
    if ignorados:
        warnings.append(
            f"Backlog: {len(ignorados)} R manual(es) con fecha igual o posterior a la consulta "
            f"SSRS ({', '.join(ignorados)}): no se usan como Current; Current = consulta de "
            "este Refresh"
        )
    return directo[0]


def _antiguedad_min(corte: str, intento: str) -> int | None:
    try:
        a = dt.datetime.fromisoformat(corte.replace("Z", "+00:00"))
        b = dt.datetime.fromisoformat(intento.replace("Z", "+00:00"))
    except ValueError:
        return None
    if a.tzinfo is None or b.tzinfo is None:
        return None
    return max(0, round((b - a).total_seconds() / 60))


def _avisos_embarques(emb: dict[str, Any]) -> list[str]:
    """FIX5: alcance real de la fuente CFDI y captura retenida (nunca como recién consultada)."""
    avisos = []
    cap = emb.get("captura") or {}
    if emb.get("acreditado") and emb.get("estado") == "CONTINGENCIA":
        corte, intento = str(cap.get("instante_utc") or ""), str(emb.get("intento_utc") or "")
        edad = _antiguedad_min(corte, intento)
        texto = "" if edad is None else f" (antigüedad {edad // 60} h {edad % 60:02d} min)"
        avisos.append(
            f"EMBARQUES ANTERIOR — el conector de Embarques falló; se usa el último corte válido "
            f"del {cap.get('instante_utc')}{texto}. Política CONSERVAR_ULTIMO_CORTE."
        )
    if emb.get("acreditado") and emb.get("alcance_ajustes"):
        avisos.append(f"Embarques (CFDI): {emb['alcance_ajustes']}")
    cls = (emb.get("clasificacion") or {}).get("A") or {}  # escenario operativo de referencia
    for clase, texto in (
        ("CANTIDAD_NULA", "con cantidad nula en la factura: desconocidos, no 0"),
        ("IDENTIDAD_NO_EXACTA", "con identidad no exacta en la factura: desconocidos, no 0"),
    ):
        if emb.get("acreditado") and cls.get(clase):
            avisos.append(f"Embarques (CFDI): {cls[clase]} ITEM {texto}")
    return avisos


def _backlog_traza(bkx: dict[str, Any], scan: dict[str, Any]) -> dict[str, Any]:
    """v0.3.3: trazabilidad del Current ERP; en contingencia, antigüedad y límite autorizado."""
    cur = scan["current_r"]
    out = {**bkx, "_avisos": []}
    if bkx["estado"] in BACKLOG_DIRECTO and cur["origen"] not in (
        "CONSULTA_DIRECTA", "ERP_EXCEPCIONAL"
    ):
        out["estado"] = "CONTINGENCIA"
        out.setdefault("codigo", "BACKLOG_CONSULTA_FUERA_DE_MES")
    if out["estado"] == "CONTINGENCIA":
        corte = str(cur.get("instante") or cur.get("document_date") or "")
        intento = str(bkx.get("intento_utc") or bkx.get("instante_consulta_utc") or "")
        out["antiguedad_min"] = _antiguedad_min(corte, intento)
        limite = bkx.get("max_horas")
        sin_tope = not isinstance(limite, (int, float)) or isinstance(limite, bool)
        dentro = out["antiguedad_min"] is not None and out["antiguedad_min"] <= (limite or 0) * 60
        if not sin_tope and not dentro:
            raise RefreshError(
                [
                    f"Backlog: el último corte válido ({corte or 'sin instante'}) supera la "
                    f"contingencia autorizada ({limite} h) tras el fallo del conector SSRS "
                    f"({bkx.get('codigo')}): se conserva la generación vigente"
                ]
            )
        edad = out["antiguedad_min"]
        texto = "" if edad is None else f" (antigüedad {edad // 60} h {edad % 60:02d} min)"
        out["_avisos"].append(
            f"BACKLOG ANTERIOR — el conector SSRS falló ({bkx.get('codigo')}); Current usa el "
            f"último corte válido {cur['file']} del {corte}{texto}. "
            "Política CONSERVAR_ULTIMO_CORTE."
        )
    elif out["estado"] == "ERP_EXCEPCIONAL":
        out["_avisos"].append(
            "ERP URGENTE — Current de UNA sola generación, con aprobación y huella; "
            "el siguiente Refresh ordinario vuelve a SSRS. Frozen sin cambios."
        )
    elif out["estado"] == "NO_ACREDITADO":
        out["_avisos"].append(
            f"Backlog: conector SSRS activo SIN acreditación válida ({bkx.get('motivo', '')}); "
            f"Current usa {cur['file']} ({cur['origen']})."
        )
    return out


def _fg_traza(fgx: dict[str, Any], scan: dict[str, Any]) -> dict[str, Any]:
    """Trazabilidad del corte FG; en contingencia, antigüedad del dato y límite autorizado."""
    fg = scan["fg"]
    out = {**fgx, "_avisos": []}
    corte = str(fg.get("document_time") or "")
    if fgx["estado"] == "CONSULTA_DIRECTA" and fg["origen"] != "CONSULTA_DIRECTA":
        out["estado"] = "CONTINGENCIA"
        out.setdefault("codigo", "FG_CONSULTA_FUERA_DE_MES")
    if out["estado"] == "CONTINGENCIA":
        try:
            a = dt.datetime.fromisoformat(corte.replace("Z", "+00:00"))
            b = dt.datetime.fromisoformat(str(fgx.get("intento_utc") or fgx.get("instante_consulta_utc") or "").replace("Z", "+00:00"))
            out["antiguedad_min"] = max(0, round((b - a).total_seconds() / 60))
        except ValueError:
            out["antiguedad_min"] = None
        limite = fgx.get("max_horas")
        if isinstance(limite, (int, float)) and not isinstance(limite, bool) and (out["antiguedad_min"] is None or out["antiguedad_min"] > limite * 60):
            raise RefreshError([f"FG: el último corte válido ({corte or 'sin instante'}) supera la contingencia autorizada "
                                f"({limite} h) tras el fallo del conector ({fgx.get('codigo')}): se conserva la generación vigente"])
        horas = "" if out["antiguedad_min"] is None else f" (antigüedad {out['antiguedad_min'] // 60} h {out['antiguedad_min'] % 60:02d} min)"
        out["_avisos"].append(f"FG ANTERIOR — el conector FG falló ({fgx.get('codigo')}); se usa el último corte válido "
                              f"{fg['file']} del {corte}{horas}. Política CONSERVAR_ULTIMO_CORTE.")
    elif out["estado"] == "NO_ACREDITADO":
        out["_avisos"].append(f"FG: conector activo SIN acreditación válida ({fgx.get('motivo', '')}); se usa {fg['file']} ({fg['origen']}).")
    return out


# ------------------------------------------------------------------ 5. una sola vista operativa
PROVISIONAL_LABEL = "PROVISIONAL — R1/R3 PENDIENTES — NO PUBLICABLE"
VERIFICADA_LABEL = "VERIFICADA (R1/R3 APROBADAS) — NO PUBLICADA: la publicación requiere autorización independiente"


def elegir_vista(scenarios: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Regla ÚNICA y determinista de la vista operativa (no depende de la configuración ni de
    qué escenario «parece más completo»):
      - VERIFICADA solo si existe el escenario OFICIAL (reglas R3 v2 CRC_APPROVED + registro R1
        verificado) y está COMPLETO (ninguna clave sin clase, ninguna identidad Portal pendiente);
      - en cualquier otro caso, PROVISIONAL con el escenario conservador A (o DIAG si no hay matriz
        R3 provisional). B nunca es la vista operativa.
    Nunca equivale a certificación ni a publicación."""
    oficial = scenarios.get("OFICIAL")
    if oficial is not None and oficial["completo"]:
        return {"modo": "VERIFICADA", "escenario": "OFICIAL", "etiqueta": VERIFICADA_LABEL, "motivos": []}
    motivos = []
    if meta["reglas_r3"] != "APROBADAS_V2":
        motivos.append(f"reglas R3: {meta['reglas_r3']} (se requieren reglas v2 CRC_APPROVED)")
    if meta["registro_r1"] != "VERIFICADO":
        motivos.append(f"registro R1: {meta['registro_r1']} (se requiere registro verificado con aprobadores)")
    if oficial is not None and not oficial["completo"]:
        motivos.append(
            f"aprobaciones incompletas: {len(oficial['pendientes']['claves_r3'])} claves R3 sin clase y "
            f"{len(oficial['pendientes']['portal_r1'])} identidades R1 pendientes"
        )
    esc = "A" if "A" in scenarios else "DIAG"
    return {"modo": "PROVISIONAL", "escenario": esc, "etiqueta": PROVISIONAL_LABEL, "motivos": motivos}


def vista_de(gen: dict[str, Any]) -> dict[str, Any]:
    """Vista operativa de una generación (compatible con generaciones de v0.2, que no la guardaban)."""
    if "vista_operativa" in gen:
        return gen["vista_operativa"]
    escs = gen["escenarios"]
    meta = gen.get("meta", {})
    for e in escs.values():  # v0.2 no guardaba «completo»: se deriva de sus conteos
        e.setdefault("completo", not e.get("claves_sin_clase") and not e.get("pendientes_r1"))
    return elegir_vista(escs, {"reglas_r3": meta.get("reglas_r3", "AUSENTE"), "registro_r1": meta.get("registro_r1", "AUSENTE")})


def evaluar_certificacion(gen: dict[str, Any]) -> dict[str, Any]:
    """Evalúa si una generación podría PRESENTARSE a certificación comercial. Nunca certifica ni
    publica: eso exige controles y autorizaciones independientes (canal oficial, fuera de Preview)."""
    vista = vista_de(gen)
    motivos = list(vista.get("motivos", []))
    if vista["modo"] != "VERIFICADA":
        motivos.insert(0, "la vista operativa es PROVISIONAL: faltan aprobaciones R1/R3")
    if not (gen.get("embarques") or {}).get("acreditado"):
        motivos.append("Embarques CFDI sin acreditación vigente: Programa y Faltante N/D")
    motivos.append("precedencia del primer corte del mes sin acreditación externa")
    return {
        "certificable": False,
        "apta_para_revision": vista["modo"] == "VERIFICADA",
        "publicacion": "NO_HABILITADA",
        "motivos": motivos,
    }
