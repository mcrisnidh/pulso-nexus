"""Motor de planificación por ITEM (puro: sin E/S, sin reglas por número de pieza).

Etapas: Demanda → Portal CAT / sustitución → FG → Cobertura → Work Orders → Operaciones → Avance
→ Capacidad → Embarques → Entradas manuales → Faltante final.

Reglas de no-equivalencia aplicadas por construcción:

- OE965 es histórico; su equivalencia con SSRS 29 OERAS no está certificada (limitación explícita).
- No se combinan cortes distintos en un mismo valor: si haría falta, el valor es
  ``TEMPORAL_MISMATCH``. Por ejemplo, FG de un corte con demanda de otro, o WO actual con demanda
  histórica.
- Las cantidades de operaciones distintas nunca se suman como piezas.
- Las correcciones ya representadas en producción y los ajustes tardíos se anotan, nunca se suman.
- La operación final del routing actual con actividad es *candidata*, no recepción en FG.
- FG usable solo en los stockrooms que declaran las reglas; un ITEM no listado no implica cero.
- La facturación nunca se convierte en embarque físico.
- No se calculan fechas de entrega ni capacidad sin calendario y cargas certificadas.
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from nexus.planning.adapters.sources import (
    BacklogLine,
    BillingMove,
    Correction,
    FgSnapshot,
    InventoryRecord,
    LaborGroup,
    LateAdjustment,
    PortalLine,
    RouteOp,
    WorkOrder,
    as_record,
)
from nexus.planning.model import (
    BLOCKED_CRC,
    BLOCKED_SOURCE,
    DIAGNOSTIC,
    NOT_CERTIFIABLE,
    PROVISIONAL,
    TEMPORAL_MISMATCH,
    UNKNOWN_NOT_ZERO,
    Fact,
    parse_decimal,
)
from nexus.planning.rules import PlanningRules

ENGINE_VERSION = "nexus.planning.engine/1"

OE965_LIMIT = (
    "OE965 (JBA 29/OERAS histórico) no es sustituto certificado de SSRS 29 OERAS; "
    "equivalencia pendiente"
)


NO_ROWS = "sin registros en la fuente para el ITEM (vacío observado, no inferido)"


@dataclass
class Bundle:
    """Fuentes ya adaptadas. ``available`` dice qué fuentes existen; las ausentes bloquean solo
    las etapas que dependen de ellas."""

    available: dict[str, bool]
    reasons: dict[str, str | None]
    cuts: dict[str, str | None] = field(default_factory=dict)  # corte declarado por fuente
    backlog: list[BacklogLine] = field(default_factory=list)
    fg: list[FgSnapshot] = field(default_factory=list)
    portal: list[PortalLine] = field(default_factory=list)
    billing: list[BillingMove] = field(default_factory=list)
    inventory: list[InventoryRecord] = field(default_factory=list)
    work_orders: list[WorkOrder] = field(default_factory=list)
    wo_blank_status: set[tuple[str, str]] = field(default_factory=set)
    wo_month_activity: set[tuple[str, str]] = field(default_factory=set)
    routing: list[RouteOp] = field(default_factory=list)
    labor: list[LaborGroup] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    late: list[LateAdjustment] = field(default_factory=list)

    def ok(self, source_id: str) -> bool:
        return self.available.get(source_id, False)

    def blocked(self, source_id: str) -> str:
        return f"fuente {source_id} no disponible: {self.reasons.get(source_id) or 'no cargada'}"


def _sum(values: list[Decimal | None]) -> tuple[Decimal, int]:
    """Suma de los conocidos y número de desconocidos (que NUNCA se tratan como cero)."""
    total = Decimal(0)
    unknown = 0
    for v in values:
        if v is None:
            unknown += 1
        else:
            total += v
    return total, unknown


def _month_end(iso_date: str) -> str:
    d = dt.date.fromisoformat(iso_date)
    return d.replace(day=calendar.monthrange(d.year, d.month)[1]).isoformat()


def _fact_sum(
    name: str,
    rows: list[Any],
    attr: str,
    unit: str,
    limitations: list[str],
    status: str = PROVISIONAL,
) -> Fact:
    total, unknown = _sum([getattr(r, attr) for r in rows])
    refs = [r.ref for r in rows]
    if unknown:
        return Fact(
            name,
            None,
            UNKNOWN_NOT_ZERO,
            refs,
            [*limitations, f"{unknown} valores desconocidos: no se tratan como cero"],
            unit,
        )
    return Fact(name, total, status, refs, [*limitations, *([] if rows else [NO_ROWS])], unit)


# ------------------------------------------------------------------ etapa: demanda (OE965)
def demand_stage(item: str, b: Bundle, rules: PlanningRules) -> dict[str, Any]:
    if not b.ok("oe965_backlog"):
        return {
            "status": BLOCKED_SOURCE,
            "facts": [
                Fact(
                    "demand.frozen_open_due_by_month_end",
                    None,
                    BLOCKED_SOURCE,
                    [],
                    [b.blocked("oe965_backlog")],
                    "EA",
                )
            ],
        }
    lines = [x for x in b.backlog if x.item == item]
    snapshots = sorted({x.snapshot for x in b.backlog if x.snapshot != "UNKNOWN"})
    facts: list[Fact] = []
    snap_views = []
    for snap in snapshots:
        here = [x for x in lines if x.snapshot == snap]
        month_end = _month_end(snap)
        due = [x for x in here if x.due_date is not None and x.due_date <= month_end]
        undated = [x for x in here if x.due_date is None]
        identities = [x.identity for x in here]
        dup = len(identities) - len(set(identities))
        by_class: dict[str, Decimal] = defaultdict(Decimal)
        for x in due:
            if x.qty_open is not None:
                by_class[rules.customer_class(x.customer_key)] += x.qty_open
        snap_views.append(
            {
                "snapshot": snap,
                "lines": len(here),
                "customers_exact": sorted({x.customer_key for x in here}),
                "open_due_by_month_end_by_class": {k: by_class[k] for k in sorted(by_class)},
                "undated_lines": len(undated),
                "duplicate_identities": dup,
            }
        )
        lim = [OE965_LIMIT, f"corte {snap}; fecha de entrega ≤ {month_end} (incluye atrasos)"]
        if undated:
            lim.append(f"{len(undated)} líneas con fecha ilegible: excluidas y señaladas")
        if dup:
            lim.append(f"{dup} identidades duplicadas en el corte")
        facts.append(_fact_sum(f"demand.open_due_by_month_end@{snap}", due, "qty_open", "EA", lim))
    frozen = snapshots[0] if snapshots else None
    detail = []
    if frozen is not None:
        frozen_lines = sorted(
            (x for x in lines if x.snapshot == frozen),
            key=lambda x: (x.due_date or "9999", x.customer_key, x.order, x.line),
        )
        detail = [
            {**as_record(x), "customer_class": rules.customer_class(x.customer_key)}
            for x in frozen_lines
        ]
    evolution = _snapshot_evolution(lines, snapshots)
    return {
        "status": PROVISIONAL,
        "frozen_snapshot": frozen,
        "snapshots": snap_views,
        "facts": facts,
        "frozen_lines": detail,
        "evolution_vs_frozen": evolution,
    }


def _snapshot_evolution(lines: list[BacklogLine], snapshots: list[str]) -> list[dict[str, Any]]:
    """Cambios de cada corte posterior frente al Frozen (identidad sin el corte). El Frozen
    nunca se recalcula con datos posteriores."""
    if not snapshots:
        return []

    def by_id(snap: str) -> dict[tuple[str, str, str, str], BacklogLine]:
        return {x.identity[1:]: x for x in lines if x.snapshot == snap}

    base = by_id(snapshots[0])
    out = []
    for snap in snapshots[1:]:
        cur = by_id(snap)
        changed = sum(
            1
            for k in base.keys() & cur.keys()
            if (base[k].qty_open, base[k].due_date) != (cur[k].qty_open, cur[k].due_date)
        )
        out.append(
            {
                "snapshot": snap,
                "added": len(cur.keys() - base.keys()),
                "removed": len(base.keys() - cur.keys()),
                "changed": changed,
            }
        )
    return out


# ------------------------------------------------------------------ etapa: Portal CAT
def portal_decision(line: PortalLine, rules: PlanningRules) -> tuple[str, Decimal | None]:
    """Regla del Portal (parametrizada): estado excluido → EXCLUIDO_ESTADO; Ordered o Shipped no
    numéricos (vacío, ``8000(0)``) → NO_CUANTIFICABLE (desconocido, no cero); DIF ≤ 0 →
    EXCLUIDO_DIF; si no, INCLUIDO con DIF = Ordered − Shipped."""
    if line.status in rules.portal_excluded_statuses:
        return "EXCLUIDO_ESTADO", None
    ordered = parse_decimal(line.ordered_raw)
    shipped = parse_decimal(line.shipped_raw)
    if ordered is None or shipped is None:
        return "NO_CUANTIFICABLE", None
    dif = ordered - shipped
    if dif <= 0:
        return "EXCLUIDO_DIF", dif
    return "INCLUIDO", dif


def portal_stage(item: str, b: Bundle, rules: PlanningRules) -> dict[str, Any]:
    if not b.ok("portal_cat"):
        return {
            "status": BLOCKED_SOURCE,
            "lines": [],
            "facts": [
                Fact(
                    "portal.included_dif", None, BLOCKED_SOURCE, [], [b.blocked("portal_cat")], "EA"
                )
            ],
        }
    lines = sorted((x for x in b.portal if x.item == item), key=lambda x: x.line_id)
    decided = [(x, *portal_decision(x, rules)) for x in lines]
    included = [(x, d) for x, dec, d in decided if dec == "INCLUIDO"]
    unknown = [x for x, dec, _ in decided if dec == "NO_CUANTIFICABLE"]
    lim = [
        "mapeo ITEM Portal→ERP provisional",
        f"corte declarado de la fuente: {b.cuts.get('portal_cat') or 'no declarado'}",
        f"reglas {rules.version} ({rules.approval})",
    ]
    refs = [x.ref for x, _ in included] or [x.ref for x in lines]
    included_sum = sum((d for _, d in included if d is not None), Decimal(0))
    facts = [
        Fact(
            "portal.included_dif_sum",
            included_sum,
            PROVISIONAL if lines else DIAGNOSTIC,
            refs,
            lim if lines else [*lim, NO_ROWS],
            "EA",
        )
    ]
    if unknown:
        facts.append(
            Fact(
                "portal.cat_demand_complete",
                None,
                UNKNOWN_NOT_ZERO,
                [x.ref for x in unknown],
                [*lim, f"{len(unknown)} líneas con cantidad desconocida (no cero)"],
                "EA",
            )
        )
    elif lines:
        facts.append(
            Fact(
                "portal.cat_demand_complete",
                facts[0].value,
                PROVISIONAL,
                facts[0].sources,
                lim,
                "EA",
            )
        )
    return {
        "status": PROVISIONAL if lines else DIAGNOSTIC,
        "lines": [
            {**as_record(x), "decision": dec, "dif": d, "provided_decision": x.provided_decision}
            for x, dec, d in decided
        ],
        "facts": facts,
    }


def substitution_stage(
    item: str, b: Bundle, rules: PlanningRules, demand: dict[str, Any], portal: dict[str, Any]
) -> dict[str, Any]:
    """Sustituir la demanda OE965 de la clase CAT por el Portal, conservando TUPY y demás
    clientes. Se presenta por componentes; el total combinado nunca se certifica porque mezcla
    cortes (OE965 Frozen y la captura del Portal) y depende de reglas no aprobadas como CRC."""
    if rules.substitute_class is None or not rules.customer_classes.get(rules.substitute_class):
        return {
            "status": BLOCKED_CRC,
            "facts": [
                Fact(
                    "substitution.demand",
                    None,
                    BLOCKED_CRC,
                    [],
                    ["no hay regla aprobada que identifique las claves de cliente CAT"],
                    "EA",
                )
            ],
        }
    if not b.ok("oe965_backlog") or not b.ok("portal_cat"):
        missing = [s for s in ("oe965_backlog", "portal_cat") if not b.ok(s)]
        return {
            "status": BLOCKED_SOURCE,
            "facts": [
                Fact(
                    "substitution.demand",
                    None,
                    BLOCKED_SOURCE,
                    [],
                    [b.blocked(s) for s in missing],
                    "EA",
                )
            ],
        }
    frozen = demand.get("frozen_snapshot")
    views = {v["snapshot"]: v for v in demand.get("snapshots", [])}
    classes = views[frozen]["open_due_by_month_end_by_class"] if frozen in views else {}
    sub = rules.substitute_class
    keep = {k: v for k, v in classes.items() if k != sub}
    due = [
        x
        for x in b.backlog
        if x.item == item
        and x.snapshot == frozen
        and x.due_date is not None
        and frozen is not None
        and x.due_date <= _month_end(frozen)
    ]
    sub_refs = [x.ref for x in due if rules.customer_class(x.customer_key) == sub]
    keep_refs = [x.ref for x in due if rules.customer_class(x.customer_key) != sub]
    lim = [
        OE965_LIMIT,
        f"reglas {rules.version} ({rules.approval})",
        f"OE965 Frozen {frozen} y Portal ({b.cuts.get('portal_cat') or 'sin corte'}) son "
        "cortes distintos",
    ]
    if not rules.crc_approved:
        lim.append("clasificación de clientes por reglas provisional (sin CRC aprobado)")
    has_portal = bool(portal.get("lines"))
    facts = [
        Fact(
            "substitution.oe965_frozen_open_class_to_replace",
            classes.get(sub, Decimal(0)),
            PROVISIONAL,
            sub_refs,
            [*lim, f"clase sustituida: {sub}", *([] if sub_refs else [NO_ROWS])],
            "EA",
        ),
        Fact(
            "substitution.oe965_frozen_open_preserved_classes",
            keep,
            PROVISIONAL,
            keep_refs,
            [*lim, f"se conservan: {sorted(keep) or 'ninguna'}", *([] if keep_refs else [NO_ROWS])],
            "EA",
        ),
        Fact(
            "substitution.combined_demand",
            None,
            TEMPORAL_MISMATCH,
            [],
            [
                *lim,
                "el total sustituido combinaría dos cortes distintos; se entregan los "
                "componentes por separado",
            ],
            "EA",
        ),
    ]
    if not has_portal and classes.get(sub):
        facts.append(
            Fact(
                "substitution.portal_lines_missing_for_class_demand",
                None,
                NOT_CERTIFIABLE,
                [],
                [f"el ITEM tiene demanda OE965 de clase {sub} pero no líneas Portal"],
                "EA",
            )
        )
    return {"status": TEMPORAL_MISMATCH, "facts": facts}


# ------------------------------------------------------------------ etapa: FG
def fg_stage(item: str, b: Bundle, rules: PlanningRules) -> dict[str, Any]:
    facts: list[Fact] = []
    if b.ok("oe965_fg"):
        for s in sorted((x for x in b.fg if x.item == item), key=lambda x: x.snapshot):
            lim = [OE965_LIMIT, f"corte {s.snapshot}"]
            facts.append(
                Fact(
                    f"fg.physical@{s.snapshot}",
                    s.physical,
                    PROVISIONAL if s.physical is not None else UNKNOWN_NOT_ZERO,
                    [s.ref],
                    lim if s.physical is not None else [*lim, "valor ausente"],
                    "EA",
                )
            )
            avail_lim = list(lim)
            if s.available is not None and s.available < 0:
                avail_lim.append("disponible negativo: se conserva, no se convierte en cero")
            facts.append(
                Fact(
                    f"fg.available@{s.snapshot}",
                    s.available,
                    PROVISIONAL if s.available is not None else UNKNOWN_NOT_ZERO,
                    [s.ref],
                    avail_lim if s.available is not None else [*avail_lim, "valor ausente"],
                    "EA",
                )
            )
    else:
        facts.append(
            Fact("fg.available@frozen", None, BLOCKED_SOURCE, [], [b.blocked("oe965_fg")], "EA")
        )
    later: list[dict[str, Any]] = []
    if b.ok("inventory_snapshot"):
        recs = [x for x in b.inventory if x.item == item]
        usable = [x for x in recs if x.stockroom in rules.usable_fg_stockrooms]
        cuts = sorted({x.cut for x in b.inventory if x.cut})
        cut = cuts[-1] if cuts else None
        if usable:
            facts.append(
                _fact_sum(
                    f"fg.usable_inventory@{cut}",
                    usable,
                    "qty",
                    "EA",
                    [
                        f"corte {cut}: instante distinto de los cortes OE965",
                        f"stockrooms usables: {sorted(rules.usable_fg_stockrooms)}",
                    ],
                )
            )
        else:
            facts.append(
                Fact(
                    f"fg.usable_inventory@{cut}",
                    None,
                    UNKNOWN_NOT_ZERO,
                    [],
                    [
                        "el ITEM no aparece en stockrooms usables de este corte: "
                        "no listado no implica cero"
                    ],
                    "EA",
                )
            )
        for x in sorted(recs, key=lambda r: r.stockroom):
            later.append({**as_record(x), "usable_fg": x.stockroom in rules.usable_fg_stockrooms})
    else:
        facts.append(
            Fact(
                "fg.usable_inventory@later",
                None,
                BLOCKED_SOURCE,
                [],
                [b.blocked("inventory_snapshot")],
                "EA",
            )
        )
    return {"status": PROVISIONAL, "facts": facts, "later_inventory_records": later}


# ------------------------------------------------------------------ etapa: cobertura
def coverage_stage(item: str, b: Bundle, demand: dict[str, Any]) -> dict[str, Any]:
    """Cobertura FG al corte Frozen: demanda y FG del MISMO archivo/corte OE965."""
    frozen = demand.get("frozen_snapshot")
    if frozen is None or not b.ok("oe965_fg"):
        reason = (
            [b.blocked("oe965_fg")] if not b.ok("oe965_fg") else ["sin corte Frozen de demanda"]
        )
        return {
            "status": BLOCKED_SOURCE,
            "facts": [Fact("coverage.fg_gap_at_frozen", None, BLOCKED_SOURCE, [], reason, "EA")],
        }
    demand_fact = next(
        f for f in demand["facts"] if f.name == f"demand.open_due_by_month_end@{frozen}"
    )
    fg = next((x for x in b.fg if x.item == item and x.snapshot == frozen), None)
    lim = [
        OE965_LIMIT,
        f"demanda y FG del mismo corte {frozen}",
        "cobertura agregada por ITEM: la asignación por línea requiere CRC de prioridad",
    ]
    facts = []
    if fg is None or fg.available is None or demand_fact.value is None:
        facts.append(
            Fact(
                "coverage.fg_gap_at_frozen",
                None,
                UNKNOWN_NOT_ZERO,
                [*demand_fact.sources, *([fg.ref] if fg else [])],
                [*lim, "FG o demanda desconocidos en el corte"],
                "EA",
            )
        )
    elif fg.available < 0:
        facts.append(
            Fact(
                "coverage.fg_gap_at_frozen",
                None,
                NOT_CERTIFIABLE,
                [*demand_fact.sources, fg.ref],
                [
                    *lim,
                    f"FG disponible negativo ({fg.available}); sin regla aprobada para tratarlo",
                ],
                "EA",
            )
        )
    else:
        demand_qty: Decimal = demand_fact.value
        covered = min(fg.available, demand_qty)
        facts.append(
            Fact(
                "coverage.fg_covered_at_frozen",
                covered,
                PROVISIONAL,
                [*demand_fact.sources, fg.ref],
                lim,
                "EA",
            )
        )
        facts.append(
            Fact(
                "coverage.fg_gap_at_frozen",
                demand_qty - covered,
                PROVISIONAL,
                [*demand_fact.sources, fg.ref],
                lim,
                "EA",
            )
        )
    facts.append(
        Fact(
            "coverage.line_allocation",
            None,
            BLOCKED_CRC,
            [],
            ["no hay CRC de prioridad de asignación por cliente/pedido/fecha"],
            "EA",
        )
    )
    facts.append(
        Fact(
            "coverage.wo_coverage_at_frozen",
            None,
            TEMPORAL_MISMATCH,
            [],
            [
                "las WO disponibles son estado actual (snapshot posterior); no hay versión "
                "histórica de WO al corte Frozen"
            ],
            "EA",
        )
    )
    return {"status": PROVISIONAL, "facts": facts}


# ------------------------------------------------------------------ etapas: WO, operaciones, avance
def wo_stage(item: str, b: Bundle) -> dict[str, Any]:
    if not b.ok("wo_header"):
        return {
            "status": BLOCKED_SOURCE,
            "work_orders": [],
            "facts": [Fact("wo.current", None, BLOCKED_SOURCE, [], [b.blocked("wo_header")])],
        }
    wos = sorted((w for w in b.work_orders if w.item == item), key=lambda w: w.key)
    rows = []
    for w in wos:
        rows.append(
            {
                **as_record(w),
                "status_label": f"ESTADO_ACTUAL@{w.snapshot_date}: {w.status_current or '(vacío)'}",
                "in_blank_status_set": w.key in b.wo_blank_status,
                "in_month_activity_set": w.key in b.wo_month_activity,
            }
        )
    snaps = sorted({w.snapshot_date for w in wos})
    facts = [
        Fact(
            "wo.count_current",
            len(wos),
            DIAGNOSTIC,
            [w.ref for w in wos],
            [
                f"estado actual {snaps or '—'}; no es reconstrucción histórica",
                *([] if wos else [NO_ROWS]),
            ],
        ),
        Fact(
            "wo.open_qty_at_frozen",
            None,
            TEMPORAL_MISMATCH,
            [],
            [
                "el saldo pendiente histórico al corte Frozen no es reconstruible con el estado "
                "actual de las WO"
            ],
            "EA",
        ),
        Fact(
            "wo.rework_orders_current",
            sum(1 for w in wos if w.order_type == "RW"),
            DIAGNOSTIC,
            [w.ref for w in wos],
            ["tipo RW según estado actual; cobertura RW requiere CRC", *([] if wos else [NO_ROWS])],
        ),
    ]
    return {"status": DIAGNOSTIC, "work_orders": rows, "facts": facts}


def _operation_rows(
    ops: list[RouteOp],
    by_seq: dict[int, list[LaborGroup]],
    corr: list[Correction],
    late: list[LateAdjustment],
) -> list[dict[str, Any]]:
    """Una fila por operación (routing actual ∪ operaciones con actividad del mes). Las
    correcciones y ajustes tardíos se anotan junto a la operación; nunca se suman."""
    rows = []
    for seq in sorted({o.seq for o in ops} | set(by_seq)):
        route = next((o for o in ops if o.seq == seq), None)
        gs = by_seq.get(seq, [])
        d_qty, d_unknown = _sum([g.qty for g in gs if g.record_type == "D"])
        scrap, scrap_unknown = _sum([g.scrap for g in gs])
        rows.append(
            {
                "seq": seq,
                "current_route": as_record(route) if route else None,
                "in_current_route": route is not None,
                "month_groups": [as_record(g) for g in gs],
                "month_d_qty": None if d_unknown else d_qty,
                "month_scrap": None if scrap_unknown else scrap,
                "month_record_types": sorted({g.record_type for g in gs}),
                "corrections_already_in_production": [as_record(c) for c in corr if c.seq == seq],
                "late_adjustments_not_additional": [as_record(x) for x in late if x.seq == seq],
            }
        )
    return rows


def operations_and_progress(item: str, b: Bundle) -> dict[str, Any]:
    """Routing actual por WO y actividad del mes por operación. Las cantidades de operaciones
    distintas no se suman; la operación final del routing actual es solo candidata."""
    route_by_wo: dict[tuple[str, str], list[RouteOp]] = defaultdict(list)
    for op in b.routing:
        if op.item == item:
            route_by_wo[(op.company, op.order_no)].append(op)
    labor_by_wo: dict[tuple[str, str], list[LaborGroup]] = defaultdict(list)
    for g in b.labor:
        if g.item == item:
            labor_by_wo[(g.company, g.order_no)].append(g)
    corr_by_wo: dict[str, list[Correction]] = defaultdict(list)
    for c in b.corrections:
        if c.item == item:
            corr_by_wo[c.order_no].append(c)
    late_by_wo: dict[str, list[LateAdjustment]] = defaultdict(list)
    for la in b.late:
        if la.item == item:
            late_by_wo[la.order_no].append(la)
    wo_keys = sorted(set(route_by_wo) | set(labor_by_wo))
    per_wo = []
    facts: list[Fact] = []
    candidates = 0
    candidate_refs: list[Any] = []
    for key in wo_keys:
        ops = sorted(route_by_wo.get(key, []), key=lambda o: o.seq)
        groups = sorted(labor_by_wo.get(key, []), key=lambda g: g.key)
        route_seqs = {o.seq for o in ops}
        last_seq = max(route_seqs) if route_seqs else None
        by_seq: dict[int, list[LaborGroup]] = defaultdict(list)
        for g in groups:
            by_seq[g.seq].append(g)
        op_rows = _operation_rows(
            ops, by_seq, corr_by_wo.get(key[1], []), late_by_wo.get(key[1], [])
        )
        is_candidate = False
        if last_seq is not None:
            at_last = [g for g in by_seq.get(last_seq, []) if g.record_type == "D"]
            qty_last, unknown_last = _sum([g.qty for g in at_last])
            is_candidate = bool(at_last) and not unknown_last and qty_last > 0
            last_route = [o.ref for o in ops if o.seq == last_seq]
            facts.append(
                Fact(
                    f"progress.terminal_candidate_qty[{key[1]}]",
                    qty_last if at_last else Decimal(0),
                    DIAGNOSTIC,
                    [*last_route, *(g.ref for g in at_last)],
                    [
                        "operación máxima del routing ACTUAL: candidata a terminación, "
                        "NO recepción FG",
                        "sin movimiento de inventario FG no hay certificación",
                    ],
                    "EA",
                )
            )
        candidates += int(is_candidate)
        if is_candidate and last_seq is not None:
            candidate_refs.extend(g.ref for g in by_seq[last_seq] if g.record_type == "D")
        missing_in_route = sorted(set(by_seq) - route_seqs)
        lim_wo = []
        if missing_in_route:
            lim_wo.append(
                f"operaciones con actividad del mes ausentes del routing actual: {missing_in_route}"
            )
        if late_by_wo.get(key[1]):
            lim_wo.append(
                "ajustes tardíos (actividad en el mes, registro posterior): el "
                "conocimiento al cierre del mes no es certificable"
            )
        per_wo.append(
            {
                "company": key[0],
                "order_no": key[1],
                "current_route_operations": len(ops),
                "current_last_seq_candidate": last_seq,
                "month_activity": bool(groups),
                "month_max_reported_seq": max(by_seq) if by_seq else None,
                "terminal_candidate": is_candidate,
                "terminal_status": (
                    "TERMINAL_OPERATION_CANDIDATE_NOT_FG_RECEIPT"
                    if is_candidate
                    else "NO_MONTH_ACTIVITY_AT_ROUTE_END"
                ),
                "limitations": lim_wo,
                "operations": op_rows,
            }
        )
    facts.append(
        Fact(
            "progress.wo_with_terminal_candidate",
            candidates,
            DIAGNOSTIC,
            candidate_refs or [r for ops in route_by_wo.values() for r in (o.ref for o in ops)],
            ["candidatas a terminación, no FG certificado", *([] if wo_keys else [NO_ROWS])],
        )
    )
    facts.append(
        Fact(
            "progress.finished_pieces",
            None,
            NOT_CERTIFIABLE,
            [],
            [
                "las operaciones no son piezas terminadas y no hay movimientos FG; no se "
                "suman cantidades de operaciones distintas"
            ],
            "EA",
        )
    )
    status = DIAGNOSTIC
    blocked = [s for s in ("routing", "labor_groups") if not b.ok(s)]
    if blocked:
        facts.append(
            Fact("progress.sources", None, BLOCKED_SOURCE, [], [b.blocked(s) for s in blocked])
        )
        status = BLOCKED_SOURCE if len(blocked) == 2 else DIAGNOSTIC
    return {"status": status, "work_orders": per_wo, "facts": facts}


# ------------------------------------------------------------------ etapa: capacidad
def capacity_stage(item: str, b: Bundle) -> dict[str, Any]:
    ops = [o for o in b.routing if o.item == item]
    facts = [
        Fact(
            "capacity.available_hours",
            None,
            BLOCKED_SOURCE,
            [],
            ["no hay calendario, turnos ni cargas de otros ITEM certificados por recurso"],
            "h",
        ),
        Fact(
            "capacity.delivery_dates",
            None,
            BLOCKED_SOURCE,
            [],
            ["no se calculan fechas deterministas sin capacidad y calendarios certificados"],
        ),
    ]
    if ops:
        hrs, unknown = _sum([o.pln_hrs for o in ops])
        facts.append(
            Fact(
                "capacity.planned_hours_current_routing",
                None if unknown else hrs,
                DIAGNOSTIC if not unknown else UNKNOWN_NOT_ZERO,
                [o.ref for o in ops],
                [
                    "suma de pln_hrs del routing ACTUAL (horas, no piezas); excluye carga de otros "
                    "ITEM",
                    "códigos time_basis sin semántica certificada: estándares no convertidos",
                ],
                "h",
            )
        )
    return {
        "status": BLOCKED_SOURCE,
        "standards": [
            {
                "order_no": o.order_no,
                "seq": o.seq,
                "work_center": o.work_center,
                "pln_hrs": o.pln_hrs,
                "set_std": o.set_std,
                "mch_std": o.mch_std,
                "e_or_s_std": o.e_or_s_std,
                "time_basis_cd": o.time_basis_cd,
            }
            for o in sorted(ops, key=lambda o: (o.order_no, o.seq))
        ],
        "facts": facts,
    }


# ------------------------------------------------------------------ etapa: embarques / facturas
def shipments_stage(item: str, b: Bundle) -> dict[str, Any]:
    facts = [
        Fact(
            "shipments.physical",
            None,
            BLOCKED_SOURCE,
            [],
            ["no hay fuente de salidas físicas; la facturación no es embarque"],
            "EA",
        )
    ]
    if not b.ok("billing"):
        facts.append(
            Fact("billing.net_qty", None, BLOCKED_SOURCE, [], [b.blocked("billing")], "EA")
        )
        return {"status": BLOCKED_SOURCE, "movements": [], "facts": facts}
    moves = sorted(
        (m for m in b.billing if m.item == item), key=lambda m: (m.date or "", m.document)
    )
    by_series: dict[str, Decimal] = defaultdict(Decimal)
    for m in moves:
        if m.qty is not None:
            by_series[m.series] += m.qty
    lim = [
        "movimientos FACTURADOS (contables): no equivalen a salidas físicas",
        "series con signo negativo (p. ej. G) no implican devolución física",
    ]
    facts.append(_fact_sum("billing.net_qty", moves, "qty", "EA", lim))
    facts.append(
        Fact(
            "billing.net_qty_by_series",
            dict(by_series),
            PROVISIONAL,
            [m.ref for m in moves],
            [*lim, *([] if moves else [NO_ROWS])],
            "EA",
        )
    )
    return {"status": PROVISIONAL, "movements": [as_record(m) for m in moves], "facts": facts}


# ------------------------------------------------------------------ vista completa por ITEM
def item_view(item: str, b: Bundle, rules: PlanningRules) -> dict[str, Any]:
    demand = demand_stage(item, b, rules)
    portal = portal_stage(item, b, rules)
    substitution = substitution_stage(item, b, rules, demand, portal)
    fg = fg_stage(item, b, rules)
    coverage = coverage_stage(item, b, demand)
    wos = wo_stage(item, b)
    progress = operations_and_progress(item, b)
    capacity = capacity_stage(item, b)
    shipments = shipments_stage(item, b)
    manual = {
        "status": BLOCKED_SOURCE,
        "facts": [
            Fact(
                "manual_inputs.customer_service",
                None,
                BLOCKED_SOURCE,
                [],
                [
                    "fuente histórica de Customer Service/ManualInputs no entregada: "
                    "no se inventan adelantos, fechas, CSR ni estados"
                ],
            )
        ],
    }
    gap = next((f for f in coverage["facts"] if f.name == "coverage.fg_gap_at_frozen"), None)
    final: dict[str, Any] = {
        "status": BLOCKED_SOURCE,
        "facts": [
            Fact(
                "final.shortage",
                None,
                BLOCKED_SOURCE,
                [],
                [
                    "requiere embarques físicos, WO históricas al corte, capacidad/calendario y "
                    "entradas CS certificados; las fuentes disponibles tienen cortes incompatibles"
                ],
                "EA",
            ),
        ],
    }
    if gap is not None:
        final["facts"].append(
            Fact(
                "final.fg_only_gap_at_frozen",
                gap.value,
                gap.status if gap.status != PROVISIONAL else DIAGNOSTIC,
                gap.sources,
                [*gap.limitations, "brecha SOLO frente a FG del corte: no es el faltante final"],
                "EA",
            )
        )
    stages = {
        "01_demand": demand,
        "02_portal_cat": portal,
        "03_substitution": substitution,
        "04_fg": fg,
        "05_coverage": coverage,
        "06_work_orders": wos,
        "07_operations_progress": progress,
        "08_capacity": capacity,
        "09_shipments_billing": shipments,
        "10_manual_inputs": manual,
        "11_final_shortage": final,
    }
    return {
        "item": item,
        "engine": ENGINE_VERSION,
        "rules": {"version": rules.version, "approval": rules.approval},
        "stages": stages,
    }


def serialize(view: Any) -> Any:
    """Convierte la vista (con ``Fact`` y ``Decimal``) en estructura JSON determinista."""
    from nexus.planning.model import jsonable  # noqa: PLC0415

    if isinstance(view, Fact):
        return view.as_dict()
    if isinstance(view, dict):
        return {str(k): serialize(v) for k, v in view.items()}
    if isinstance(view, (list, tuple)):
        return [serialize(v) for v in view]
    return jsonable(view)
