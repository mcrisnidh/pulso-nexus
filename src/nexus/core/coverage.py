"""C4 — Cobertura CORE (puro: sin E/S, sin reglas por número de pieza ni cliente).

Por ITEM, para el mes M y el instante de corte t:

- Demanda congelada (Dcong): líneas Frozen 29 OERAS de clases NO sustituidas con entrega ≤ fin de
  M (incluye vencidas) + DIF de las líneas Portal Frozen INCLUIDAS con fecha ≤ fin de M (la clase
  sustituida —CAT— nunca se toma del 29 OERAS). Las líneas futuras quedan en la vista.
- Atendida: embarques físicos netos de reversas en [inicio de M, t], atribuidos SOLO con evidencia:
  pedido+línea → pedido → ITEM+cliente exacto (agregado) / ITEM+clase sustituida (pool Portal).
  Cada movimiento se usa una vez; el exceso se informa aparte.
- Pendiente = Dcong − Atendida; FG asignable = FG usable del corte (− reservado si la regla lo
  pide); Cubierta = min(FG, Pendiente); Sin cubrir = Pendiente − Cubierta.

Desconocido nunca es cero. Un KPI es OFICIAL solo con reglas aprobadas, fuentes certificadas y
atribución exacta; en otro caso es PROVISIONAL.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from nexus.core.rules import APPROVED, CoreRules
from nexus.core.sources import REVERSAL, SHIP, FgRecord, OerasLine, Shipment
from nexus.planning.adapters.sources import PortalLine
from nexus.planning.engine import portal_decision
from nexus.planning.model import (
    BLOCKED_CRC,
    NOT_CERTIFIABLE,
    PROVISIONAL,
    UNKNOWN_NOT_ZERO,
    Fact,
    SourceRef,
    jsonable,
)

ENGINE_VERSION = "nexus.core.coverage/1"
OFICIAL, PROVISIONAL_KPI = "OFICIAL", "PROVISIONAL"
LINE, ORDER, ITEM_CUSTOMER, ITEM_CLASS = "LINE", "ORDER", "ITEM_CUSTOMER", "ITEM_CLASS"
EXACT_LEVELS = (LINE, ORDER)
METRICS = ("demand", "attended", "pending", "fg_assignable", "covered", "uncovered")
ZERO = Decimal(0)


def month_bounds(month: str) -> tuple[str, str]:
    year, mon = (int(x) for x in month.split("-"))
    last = calendar.monthrange(year, mon)[1]
    return f"{month}-01", f"{month}-{last:02d}"


@dataclass
class Unit:
    """Unidad de demanda: una línea 29 OERAS o el pool Portal de la clase sustituida por ITEM."""

    key: str
    kind: str  # LINE | POOL
    item: str
    customer_class: str
    customer_key: str
    order: str
    line: str
    due: str | None
    overdue: bool
    demand: Decimal | None
    refs: list[SourceRef]
    remaining: Decimal | None = None
    attended_exact: Decimal = ZERO
    attended_aggregate: Decimal = ZERO
    reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.remaining = self.demand

    @property
    def attended(self) -> Decimal:
        return self.attended_exact + self.attended_aggregate


@dataclass(frozen=True)
class CoverageInput:
    month: str
    cut_ts: str
    oeras: list[OerasLine]
    portal: list[PortalLine]
    fg: list[FgRecord]
    shipments: list[Shipment]
    rules: CoreRules
    certified: bool  # todas las fuentes usadas tienen acta de certificación (falso en el Hito A)
    # B0-7: inicio de la ventana de embarques (medianoche local del día 1, en UTC). ``None`` =
    # medianoche UTC, comportamiento previo idéntico.
    window_start_utc: str | None = None


def _units(inp: CoverageInput) -> tuple[list[Unit], list[dict[str, Any]], dict[str, list[str]]]:
    """Unidades de demanda del horizonte + líneas fuera del horizonte (consultables)."""
    rules = inp.rules.base
    start, end = month_bounds(inp.month)
    sub = rules.substitute_class
    units: list[Unit] = []
    future: list[dict[str, Any]] = []
    notes: dict[str, list[str]] = defaultdict(list)
    sub_lines_in_horizon: dict[str, int] = defaultdict(int)
    for x in sorted(
        inp.oeras, key=lambda r: (r.item, r.due_date or "", r.customer_key, r.order, r.line)
    ):
        cls = rules.customer_class(x.customer_key)
        in_horizon = x.due_date is not None and x.due_date <= end
        if cls == sub:
            if in_horizon:
                sub_lines_in_horizon[x.item] += 1
            continue  # sustituida por el Portal: nunca entra desde 29 OERAS
        if not in_horizon:
            future.append(
                {
                    "item": x.item,
                    "source": "29 OERAS",
                    "customer_class": cls,
                    "customer_key": x.customer_key,
                    "order": x.order,
                    "line": x.line,
                    "due_date": x.due_date,
                    "qty_open": x.qty_open,
                }
            )
            continue
        unit = Unit(
            key=f"L|{x.item}|{x.customer_key}|{x.order}|{x.line}",
            kind="LINE",
            item=x.item,
            customer_class=cls,
            customer_key=x.customer_key,
            order=x.order,
            line=x.line,
            due=x.due_date,
            overdue=bool(x.due_date and x.due_date < start),
            demand=x.qty_open,
            refs=[x.ref],
        )
        if x.qty_open is None:
            unit.reasons.append("qty_open desconocida (no cero)")
        units.append(unit)
    if sub is not None:
        pools: dict[str, list[tuple[PortalLine, Decimal | None, str | None]]] = defaultdict(list)
        unknown_pool: dict[str, list[str]] = defaultdict(list)
        for p in sorted(inp.portal, key=lambda r: (r.item, r.line_id)):
            decision, dif = portal_decision(p, rules)
            when = getattr(p, inp.rules.portal_date_field)
            if decision in ("EXCLUIDO_ESTADO", "EXCLUIDO_DIF"):
                continue
            if when is not None and when > end:
                future.append(
                    {
                        "item": p.item,
                        "source": "Portal CAT",
                        "customer_class": sub,
                        "line_id": p.line_id,
                        "date": when,
                        "decision": decision,
                        "dif": dif,
                    }
                )
                continue
            pools[p.item].append((p, dif if decision == "INCLUIDO" else None, when))
            if decision == "NO_CUANTIFICABLE":
                unknown_pool[p.item].append(f"línea Portal {p.line_id}: cantidad desconocida")
            elif when is None:
                unknown_pool[p.item].append(
                    f"línea Portal {p.line_id}: sin fecha {inp.rules.portal_date_field}"
                )
        for item in sorted(set(pools) | set(sub_lines_in_horizon)):
            lines = pools.get(item, [])
            known = sum((d for _, d, _ in lines if d is not None), ZERO)
            reasons = list(unknown_pool.get(item, []))
            demand: Decimal | None = known
            if reasons:
                demand = None
            if not lines and sub_lines_in_horizon.get(item):
                demand = None
                reasons.append(
                    f"29 OERAS tiene {sub_lines_in_horizon[item]} líneas de clase "
                    f"{sub} en el mes y el Portal Frozen no tiene líneas: demanda "
                    "no determinable"
                )
            dates = sorted(w for _, _, w in lines if w is not None)
            unit = Unit(
                key=f"P|{item}|{sub}",
                kind="POOL",
                item=item,
                customer_class=sub,
                customer_key="",
                order="",
                line="",
                due=dates[0] if dates else None,
                overdue=bool(dates and dates[0] < start),
                demand=demand,
                refs=[p.ref for p, _, _ in lines],
            )
            unit.reasons.extend(reasons)
            units.append(unit)
            notes[item].append(f"pool Portal {sub}: {len(lines)} líneas en el horizonte")
    return units, future, notes


def _net_shipments(inp: CoverageInput) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Embarques netos de reversas dentro de la ventana; lo demás se informa."""
    start, _ = month_bounds(inp.month)
    window_start = inp.window_start_utc or f"{start}T00:00:00Z"
    in_window = [
        s
        for s in inp.shipments
        if s.movement_ts is not None and window_start <= s.movement_ts <= inp.cut_ts
    ]
    excluded = [
        {
            "movement_id": s.movement_id,
            "item": s.item,
            "reason": "fuera de ventana",
            "movement_ts": s.movement_ts,
        }
        for s in inp.shipments
        if s not in in_window
    ]
    reversed_qty: dict[str, Decimal] = defaultdict(Decimal)
    for s in in_window:
        if s.movement_type == REVERSAL and s.qty is not None:
            reversed_qty[s.reverses] += s.qty
    ships = {s.movement_id: s for s in in_window if s.movement_type == SHIP}
    for target in reversed_qty:
        if target not in ships:
            excluded.append(
                {
                    "movement_id": target,
                    "item": None,
                    "reason": "reversa en ventana de un embarque fuera de ventana",
                }
            )
    net = []
    for s in sorted(ships.values(), key=lambda x: (x.movement_ts or "", x.movement_id)):
        qty = (s.qty or ZERO) - reversed_qty.get(s.movement_id, ZERO)
        net.append({"shipment": s, "net": qty})
    return net, excluded


def _consume(
    units: list[Unit], qty: Decimal, level: str, movement_id: str, allocations: list[dict[str, Any]]
) -> Decimal:
    """Asigna ``qty`` a las unidades (vencidas primero, luego fecha, pedido, línea)."""
    for u in sorted(units, key=lambda u: (not u.overdue, u.due or "9999-12-31", u.order, u.line)):
        if qty <= 0:
            break
        if u.remaining is None or u.remaining <= 0:
            continue
        take = min(u.remaining, qty)
        u.remaining -= take
        if level in EXACT_LEVELS:
            u.attended_exact += take
        else:
            u.attended_aggregate += take
        qty -= take
        allocations.append(
            {"movement_id": movement_id, "item": u.item, "unit": u.key, "level": level, "qty": take}
        )
    return qty


def _attribute(inp: CoverageInput, units: list[Unit]) -> dict[str, Any]:
    sub = inp.rules.base.substitute_class
    net, excluded = _net_shipments(inp)
    allocations: list[dict[str, Any]] = []
    excess: list[dict[str, Any]] = []
    unattributed: list[dict[str, Any]] = []
    for entry in net:
        s: Shipment = entry["shipment"]
        qty: Decimal = entry["net"]
        if qty < 0:
            raise ValueError(f"reversas mayores que el embarque {s.movement_id}")
        if qty == 0:
            continue
        cls = inp.rules.base.customer_class(s.customer_key)
        if cls == sub:
            pool = [u for u in units if u.kind == "POOL" and u.item == s.item]
            candidates = [(ITEM_CLASS, pool)]
        else:
            mine = [
                u
                for u in units
                if u.kind == "LINE" and u.item == s.item and u.customer_key == s.customer_key
            ]
            candidates = [
                (LINE, [u for u in mine if u.order == s.order and u.line == s.line]),
                (ORDER, [u for u in mine if u.order == s.order]),
                (ITEM_CUSTOMER, mine),
            ]
        matched = False
        for level, group in candidates:
            usable = [u for u in group if u.remaining is not None and u.remaining > 0]
            if not usable:
                if group:
                    matched = True
                continue
            matched = True
            qty = _consume(usable, qty, level, s.movement_id, allocations)
            if qty <= 0:
                break
        if qty > 0:
            unknown = [u for _, g in candidates for u in g if u.remaining is None]
            target = excess if matched and not unknown else unattributed
            target.append(
                {
                    "movement_id": s.movement_id,
                    "item": s.item,
                    "customer_class": cls,
                    "qty": qty,
                    "reason": (
                        "demanda de la unidad desconocida"
                        if unknown
                        else "excede la demanda congelada"
                        if matched
                        else "sin evidencia de demanda congelada"
                    ),
                }
            )
    return {
        "allocations": allocations,
        "excess": excess,
        "unattributed": unattributed,
        "excluded": excluded,
        "net": [
            {"movement_id": e["shipment"].movement_id, "item": e["shipment"].item, "net": e["net"]}
            for e in net
        ],
    }


def _fg(
    inp: CoverageInput, item: str
) -> tuple[Decimal | None, str, list[str], list[SourceRef], list[dict[str, Any]]]:
    usable_rooms = inp.rules.base.usable_fg_stockrooms
    recs = [r for r in inp.fg if r.item == item]
    detail = [
        {
            "stockroom": r.stockroom,
            "qty_physical": r.qty_physical,
            "qty_reserved": r.qty_reserved,
            "usable": r.stockroom in usable_rooms,
            "line": r.ref.line,
        }
        for r in sorted(recs, key=lambda r: r.stockroom)
    ]
    usable = [r for r in recs if r.stockroom in usable_rooms]
    refs = [r.ref for r in usable]
    if not usable:
        return (
            None,
            UNKNOWN_NOT_ZERO,
            ["ITEM sin registro en stockrooms usables: no implica cero"],
            refs,
            detail,
        )
    total = ZERO
    for r in usable:
        if r.qty_physical is None:
            return None, UNKNOWN_NOT_ZERO, ["qty física desconocida"], refs, detail
        total += r.qty_physical
        if inp.rules.fg_subtract_reserved:
            if r.qty_reserved is None:
                return (
                    None,
                    UNKNOWN_NOT_ZERO,
                    ["qty reservada desconocida y la regla la descuenta"],
                    refs,
                    detail,
                )
            total -= r.qty_reserved
    if total < 0:
        return (
            None,
            NOT_CERTIFIABLE,
            [f"FG asignable negativo ({total}): sin regla aprobada"],
            refs,
            detail,
        )
    return total, PROVISIONAL, [], refs, detail


def compute(inp: CoverageInput) -> dict[str, Any]:
    """Resultado completo por ITEM y total (estructura JSON-serializable vía ``serialize``)."""
    units, future, notes = _units(inp)
    attribution = _attribute(inp, units)
    items = sorted({u.item for u in units})
    base_lim = [
        f"reglas {inp.rules.version} ({inp.rules.base.approval})",
        f"corte {inp.cut_ts}; mes {inp.month}",
    ]
    if inp.rules.portal_date_approval != APPROVED:
        base_lim.append(
            f"fecha Portal '{inp.rules.portal_date_field}' pendiente de contrato comercial"
        )
    per_item: dict[str, Any] = {}
    for item in items:
        mine = [u for u in units if u.item == item]
        refs = [r for u in mine for r in u.refs]
        unknown = [u for u in mine if u.demand is None]
        aggregate = any(u.attended_aggregate > 0 for u in mine)
        lim = list(base_lim)
        facts: dict[str, Fact] = {}
        attended = sum((u.attended for u in mine), ZERO)
        att_lim = list(lim)
        if aggregate:
            att_lim.append(
                "parte de la atendida es agregada (ITEM+cliente o ITEM+clase): "
                "no se presenta por línea"
            )
        if unknown:
            reasons = [r for u in unknown for r in u.reasons] or ["demanda desconocida"]
            att_lim.append(
                "atendida solo sobre unidades de demanda conocida; los embarques a "
                "unidades desconocidas quedan como no atribuidos"
            )
            for name in ("demand", "pending", "covered", "uncovered"):
                facts[name] = Fact(name, None, UNKNOWN_NOT_ZERO, refs, [*lim, *reasons], "EA")
            facts["attended"] = Fact("attended", attended, PROVISIONAL, refs, att_lim, "EA")
        else:
            demand = sum((u.demand or ZERO for u in mine), ZERO)
            pending = demand - attended
            facts["demand"] = Fact("demand", demand, PROVISIONAL, refs, lim, "EA")
            facts["attended"] = Fact("attended", attended, PROVISIONAL, refs, att_lim, "EA")
            facts["pending"] = Fact("pending", pending, PROVISIONAL, refs, att_lim, "EA")
        fg_value, fg_status, fg_lim, fg_refs, fg_detail = _fg(inp, item)
        facts["fg_assignable"] = Fact(
            "fg_assignable", fg_value, fg_status, fg_refs, [*lim, *fg_lim], "EA"
        )
        if not unknown:
            pending_v: Decimal = facts["pending"].value
            if fg_value is None:
                for name in ("covered", "uncovered"):
                    facts[name] = Fact(name, None, fg_status, fg_refs, [*lim, *fg_lim], "EA")
            else:
                covered = min(fg_value, pending_v)
                facts["covered"] = Fact(
                    "covered", covered, PROVISIONAL, [*refs, *fg_refs], lim, "EA"
                )
                facts["uncovered"] = Fact(
                    "uncovered", pending_v - covered, PROVISIONAL, [*refs, *fg_refs], lim, "EA"
                )
        kpi = {name: _kpi(inp, f, aggregate) for name, f in facts.items()}
        per_item[item] = {
            "item": item,
            "facts": facts,
            "kpi": kpi,
            "units": [_unit_view(u, inp) for u in mine],
            "fg_detail": fg_detail,
            "line_coverage": _line_coverage(inp, mine, facts),
            "notes": notes.get(item, []),
        }
    return {
        "engine": ENGINE_VERSION,
        "month": inp.month,
        "cut_ts": inp.cut_ts,
        "substitute_class": inp.rules.base.substitute_class,
        "rules": {
            "version": inp.rules.version,
            "approval": inp.rules.base.approval,
            "fully_approved": inp.rules.fully_approved,
            "sha256": inp.rules.raw_sha256,
        },
        "items": per_item,
        "totals": _totals(per_item),
        "future_lines": future,
        "attribution": attribution,
    }


def _kpi(inp: CoverageInput, f: Fact, aggregate: bool) -> str:
    official = (
        inp.rules.fully_approved
        and inp.certified
        and f.status == PROVISIONAL
        and not (aggregate and f.name in ("attended", "pending", "covered", "uncovered"))
    )
    return OFICIAL if official else PROVISIONAL_KPI


def _unit_view(u: Unit, inp: CoverageInput) -> dict[str, Any]:
    return {
        "key": u.key,
        "kind": u.kind,
        "customer_class": u.customer_class,
        "customer_key": u.customer_key,
        "order": u.order,
        "line": u.line,
        "due": u.due,
        "overdue": u.overdue,
        "demand": u.demand,
        "attended_exact": u.attended_exact,
        "attended_aggregate": u.attended_aggregate,
        "pending": None if u.demand is None else u.demand - u.attended,
        "reasons": list(u.reasons),
        "source_lines": sorted({r.line for r in u.refs}),
    }


def _line_coverage(inp: CoverageInput, units: list[Unit], facts: dict[str, Fact]) -> dict[str, Any]:
    """Asignación del FG cubierto a unidades (vencidas primero, después fecha). Solo se expone
    con regla de asignación aprobada; mientras tanto queda BLOCKED_CRC."""
    if inp.rules.allocation_approval != APPROVED:
        return {
            "status": BLOCKED_CRC,
            "reason": f"regla de asignación '{inp.rules.allocation_method}' no aprobada",
        }
    covered = facts.get("covered")
    if covered is None or covered.value is None:
        return {"status": covered.status if covered else UNKNOWN_NOT_ZERO, "allocations": []}
    left: Decimal = covered.value
    out = []
    for u in sorted(units, key=lambda u: (not u.overdue, u.due or "9999-12-31", u.order, u.line)):
        pend = (u.demand or ZERO) - u.attended
        take = min(pend, left) if pend > 0 else ZERO
        left -= take
        out.append({"unit": u.key, "covered": take, "uncovered": pend - take})
    return {"status": PROVISIONAL, "method": inp.rules.allocation_method, "allocations": out}


def _totals(per_item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in METRICS:
        known = ZERO
        unknown_items = []
        for item, data in per_item.items():
            f: Fact = data["facts"][name]
            if f.value is None:
                unknown_items.append(item)
            else:
                known += f.value
        out[name] = {
            "known_sum": known,
            "unknown_items": unknown_items,
            "complete": not unknown_items,
        }
    out["items"] = len(per_item)
    out["items_with_uncovered"] = sum(
        1
        for d in per_item.values()
        if d["facts"]["uncovered"].value is not None and d["facts"]["uncovered"].value > 0
    )
    return out


def check_invariants(result: dict[str, Any], shipments: list[Shipment]) -> list[str]:
    """Invariantes previos a publicar. Lista vacía = PASS."""
    violations: list[str] = []
    fg_total = ZERO
    covered_total = ZERO
    for item, data in result["items"].items():
        f = data["facts"]
        v = {k: f[k].value for k in METRICS}
        for k in METRICS:
            if f[k].status in (UNKNOWN_NOT_ZERO, NOT_CERTIFIABLE) and v[k] is not None:
                violations.append(f"{item}: {k} desconocido con valor")
        if None not in (v["demand"], v["attended"], v["pending"]):
            if v["demand"] != v["attended"] + v["pending"]:
                violations.append(f"{item}: demanda ≠ atendida + pendiente")
            for k in ("demand", "attended", "pending"):
                if v[k] < 0:
                    violations.append(f"{item}: {k} negativo")
        if None not in (v["pending"], v["covered"], v["uncovered"]):
            if v["pending"] != v["covered"] + v["uncovered"]:
                violations.append(f"{item}: pendiente ≠ cubierta + sin cubrir")
            if v["covered"] < 0 or v["uncovered"] < 0:
                violations.append(f"{item}: cubierta o sin cubrir negativa")
        if v["fg_assignable"] is not None and v["covered"] is not None:
            fg_total += v["fg_assignable"]
            covered_total += v["covered"]
            if v["covered"] > v["fg_assignable"]:
                violations.append(f"{item}: cubierta > FG asignable")
        for u in data["units"]:
            if u["kind"] == "LINE" and u["customer_class"] == result.get("substitute_class"):
                violations.append(f"{item}: línea de clase sustituida en la demanda")
    if covered_total > fg_total:
        violations.append("Σ cubierta > Σ FG asignable")
    att = result["attribution"]
    used: dict[str, Decimal] = defaultdict(Decimal)
    for a in att["allocations"]:
        used[a["movement_id"]] += a["qty"]
    for e in att["excess"] + att["unattributed"]:
        used[e["movement_id"]] += e["qty"]
    for n in att["net"]:
        if n["net"] > 0 and used.get(n["movement_id"], ZERO) != n["net"]:
            violations.append(
                f"embarque {n['movement_id']}: asignado {used.get(n['movement_id'])}"
                f" ≠ neto {n['net']}"
            )
    net_ids = {n["movement_id"] for n in att["net"]}
    if set(used) - net_ids:
        violations.append("asignaciones de movimientos inexistentes")
    ship_ids = [s.movement_id for s in shipments]
    if len(ship_ids) != len(set(ship_ids)):
        violations.append("movement_id duplicado")
    attended_by_item: dict[str, Decimal] = defaultdict(Decimal)
    for a in att["allocations"]:
        attended_by_item[a["item"]] += a["qty"]
    for item, data in result["items"].items():
        value = data["facts"]["attended"].value
        if value is not None and value != attended_by_item.get(item, ZERO):
            violations.append(f"{item}: atendida ≠ Σ asignaciones")
    return violations


def serialize(obj: Any) -> Any:
    if isinstance(obj, Fact):
        return obj.as_dict()
    if isinstance(obj, dict):
        return {str(k): serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize(v) for v in obj]
    return jsonable(obj)
