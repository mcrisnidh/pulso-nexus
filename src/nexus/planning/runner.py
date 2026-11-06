"""Runner offline: dataset local → vistas por ITEM, matriz, discrepancias y salida navegable.

Todas las salidas se escriben en ``out_dir`` (que debe estar FUERA del repositorio cuando el
dataset es real). No hay marcas de tiempo de ejecución en las salidas: dos corridas con las
mismas entradas producen bytes idénticos (``run_digest`` igual).
"""

from __future__ import annotations

import csv
import html
import io
import json
from collections import Counter
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from nexus.hashing import canonical_json, digest_json, sha256_bytes
from nexus.planning.adapters import sources as adapt
from nexus.planning.adapters.tables import SourceTable, load_layout, read_table
from nexus.planning.engine import ENGINE_VERSION, Bundle, item_view, portal_decision, serialize
from nexus.planning.model import STATUSES, fmt, parse_decimal
from nexus.planning.rules import PlanningRules, load_rules
from nexus.sealed import seal
from nexus.storage.object_store import LocalObjectStore

RUN_SCHEMA = "nexus.planning.run/1"


def default_layout() -> Path:
    return Path(str(resources.files("nexus.planning").joinpath("layouts/pilot20_v1.json")))


@dataclass
class Loaded:
    tables: dict[str, SourceTable]
    bundle: Bundle
    errors: dict[str, str]


def _safe(
    source_id: str, tables: dict[str, SourceTable], errors: dict[str, str], fn: Any, *args: Any
) -> Any:
    """Adapta una fuente; si falla, la fuente queda bloqueada (no detiene las demás)."""
    table = tables.get(source_id)
    if table is None or not table.available:
        return []
    try:
        return fn(table, *args)
    except (KeyError, ValueError) as exc:
        errors[source_id] = f"adaptador rechazó la fuente: {type(exc).__name__}: {exc}"
        return []


def load(dataset: Path, layout: Path, rules: PlanningRules) -> Loaded:
    specs = load_layout(layout)
    tables = {sid: read_table(dataset, spec) for sid, spec in sorted(specs.items())}
    errors: dict[str, str] = {}
    b = Bundle(available={}, reasons={})
    b.backlog = _safe("oe965_backlog", tables, errors, adapt.backlog, rules)
    b.fg = _safe("oe965_fg", tables, errors, adapt.fg_snapshots, rules)
    b.portal = _safe("portal_cat", tables, errors, adapt.portal)
    b.billing = _safe("billing", tables, errors, adapt.billing)
    b.inventory = _safe("inventory_snapshot", tables, errors, adapt.inventory)
    b.work_orders = _safe("wo_header", tables, errors, adapt.work_orders)
    b.routing = _safe("routing", tables, errors, adapt.routing)
    b.labor = _safe("labor_groups", tables, errors, adapt.labor_groups)
    b.corrections = _safe("labor_corrections", tables, errors, adapt.corrections)
    b.late = _safe("late_adjustments", tables, errors, adapt.late_adjustments)
    b.wo_blank_status = set(
        _safe("wo_blank_status", tables, errors, adapt.membership, "company_cd", "order_no")
    )
    b.wo_month_activity = set(
        _safe("wo_oct_activity", tables, errors, adapt.membership, "company_cd", "order_no")
    )
    for sid, t in tables.items():
        b.available[sid] = t.available and sid not in errors
        b.reasons[sid] = errors.get(sid) or t.reason
        b.cuts[sid] = t.spec.declared_cut
    return Loaded(tables, b, errors)


def item_universe(loaded: Loaded) -> tuple[list[str], str]:
    """Universo de ITEM: índice de control si existe; si no, unión de ITEM en las fuentes."""
    t = loaded.tables.get("item_universe")
    if t is not None and t.available:
        return sorted({r["ITEM"] for r in t.rows}), "item_universe"
    b = loaded.bundle
    found = {x.item for x in b.backlog} | {x.item for x in b.fg} | {x.item for x in b.portal}
    found |= {x.item for x in b.work_orders} | {x.item for x in b.labor}
    return sorted(found), "union_de_fuentes"


# ------------------------------------------------------------------ oráculos por ITEM
def _int_or_none(raw: str) -> int | None:
    value = parse_decimal(raw)
    return int(value) if value is not None and value == value.to_integral_value() else None


def _wo_progress(view: dict[str, Any]) -> list[dict[str, Any]]:
    stage = view["stages"]["07_operations_progress"]
    return list(stage.get("work_orders", []))


def computed_counts(
    item: str, loaded: Loaded, view: dict[str, Any], rules: PlanningRules
) -> dict[str, int]:
    b = loaded.bundle
    wo_keys = {w.key for w in b.work_orders if w.item == item}
    wo_keys |= {(o.company, o.order_no) for o in b.routing if o.item == item}
    progress = _wo_progress(view)
    return {
        "OE965_order_rows_all_snapshots": sum(1 for x in b.backlog if x.item == item),
        "Portal_rows_provisional": sum(1 for x in b.portal if x.item == item),
        "Billing_movements": sum(1 for x in b.billing if x.item == item),
        "FG_20261101_positive_records": sum(
            1
            for x in b.inventory
            if x.item == item
            and x.stockroom in rules.usable_fg_stockrooms
            and x.qty is not None
            and x.qty > 0
        ),
        "WO_current_union": len(wo_keys),
        "WO_with_October_activity": len(
            {(g.company, g.order_no) for g in b.labor if g.item == item}
        ),
        "October_operation_aggregate_rows": sum(1 for g in b.labor if g.item == item),
        "WO_with_terminal_candidate_activity": sum(1 for w in progress if w["terminal_candidate"]),
    }


def oracle_checks(
    item: str, loaded: Loaded, view: dict[str, Any], rules: PlanningRules
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    t = loaded.tables.get("item_universe")
    computed = computed_counts(item, loaded, view, rules)
    if t is not None and t.available:
        row = next((r for r in t.rows if r["ITEM"] == item), None)
        for col, value in computed.items():
            expected = _int_or_none(row.get(col)) if row is not None else None
            out.append(
                {
                    "oracle": f"item_universe.{col}",
                    "expected": expected,
                    "computed": value,
                    "match": None if expected is None else expected == value,
                }
            )
    diag = loaded.tables.get("route_end_diagnostic")
    if diag is not None and diag.available:
        mine = {w["order_no"]: w for w in _wo_progress(view)}
        for r in diag.rows:
            if r.get("Item", item) != item:
                continue
            wo = mine.get(r["WorkOrder"])
            exp_candidate = r["Status"].startswith("OCT_ACTIVITY_AT_ROUTE_END")
            exp_seq = _int_or_none(r["CurrentLastSeqCandidate"])
            got_candidate = bool(wo and wo["terminal_candidate"])
            got_seq = wo["current_last_seq_candidate"] if wo else None
            out.append(
                {
                    "oracle": f"route_end_diagnostic[{r['WorkOrder']}]",
                    "expected": {"candidate": exp_candidate, "last_seq": exp_seq},
                    "computed": {"candidate": got_candidate, "last_seq": got_seq},
                    "match": exp_candidate == got_candidate and exp_seq == got_seq,
                }
            )
    portal = view["stages"]["02_portal_cat"].get("lines", [])
    for line in portal:
        provided = line.get("provided_decision") or ""
        if provided:
            out.append(
                {
                    "oracle": f"portal_decision[{line['line_id']}]",
                    "expected": provided,
                    "computed": line["decision"],
                    "match": provided == line["decision"],
                }
            )
    return out


# ------------------------------------------------------------------ resumen y matriz
def _facts(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        if "status" in node and "provenance" in node and "name" in node:
            return [node]
        return [f for v in node.values() for f in _facts(v)]
    if isinstance(node, list):
        return [f for v in node for f in _facts(v)]
    return []


def _fact(view: dict[str, Any], stage: str, name: str) -> dict[str, Any] | None:
    return next((f for f in view["stages"][stage]["facts"] if f["name"] == name), None)


def _cell(f: dict[str, Any] | None) -> tuple[str, str]:
    if f is None:
        return "", "NOT_APPLICABLE"
    value = f["value"]
    if isinstance(value, dict):
        value = ";".join(f"{k}={v}" for k, v in sorted(value.items()))
    return ("" if value is None else str(value)), f["status"]


MATRIX_COLUMNS = [
    ("demand_frozen_due", "01_demand", "demand.open_due_by_month_end@{frozen}"),
    ("portal_included_dif", "02_portal_cat", "portal.included_dif_sum"),
    ("portal_complete", "02_portal_cat", "portal.cat_demand_complete"),
    (
        "oe965_class_to_replace",
        "03_substitution",
        "substitution.oe965_frozen_open_class_to_replace",
    ),
    (
        "oe965_preserved_classes",
        "03_substitution",
        "substitution.oe965_frozen_open_preserved_classes",
    ),
    ("fg_available_frozen", "04_fg", "fg.available@{frozen}"),
    ("fg_gap_frozen", "05_coverage", "coverage.fg_gap_at_frozen"),
    ("wo_current", "06_work_orders", "wo.count_current"),
    ("wo_terminal_candidates", "07_operations_progress", "progress.wo_with_terminal_candidate"),
    ("billing_net", "09_shipments_billing", "billing.net_qty"),
    ("final_shortage", "11_final_shortage", "final.shortage"),
]


def matrix_row(view: dict[str, Any], oracles: list[dict[str, Any]]) -> dict[str, str]:
    frozen = view["stages"]["01_demand"].get("frozen_snapshot") or ""
    row: dict[str, str] = {"item": view["item"], "frozen_snapshot": frozen}
    for col, stage, name in MATRIX_COLUMNS:
        value, status = _cell(_fact(view, stage, name.format(frozen=frozen)))
        row[col] = value
        row[f"{col}__status"] = status
    progress = _wo_progress(view)
    row["wo_with_month_activity"] = str(sum(1 for w in progress if w["month_activity"]))
    row["blocked_or_mismatch_stages"] = ";".join(
        k
        for k, st in view["stages"].items()
        if st["status"] in ("BLOCKED_SOURCE", "BLOCKED_CRC", "TEMPORAL_MISMATCH")
    )
    checked = [o for o in oracles if o["match"] is not None]
    row["oracle_checks"] = str(len(checked))
    row["oracle_mismatches"] = str(sum(1 for o in checked if not o["match"]))
    return row


def _csv(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO(newline="")
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


# ------------------------------------------------------------------ discrepancias globales
def discrepancies(
    loaded: Loaded,
    rules: PlanningRules,
    views: dict[str, dict[str, Any]],
    oracles: dict[str, list[dict[str, Any]]],
    universe_source: str,
) -> dict[str, Any]:
    b = loaded.bundle
    ids = [x.identity for x in b.backlog]
    labor_keys = [g.key for g in b.labor]
    wo_keys = [w.key for w in b.work_orders]
    corr_ids = [c.corr_id for c in b.corrections]
    integrity = {
        "backlog_duplicate_identities": len(ids) - len(set(ids)),
        "labor_duplicate_group_keys": len(labor_keys) - len(set(labor_keys)),
        "wo_duplicate_keys": len(wo_keys) - len(set(wo_keys)),
        "correction_duplicate_ids": len(corr_ids) - len(set(corr_ids)),
        "backlog_unknown_snapshot_rows": sum(1 for x in b.backlog if x.snapshot == "UNKNOWN"),
        "backlog_unparsed_due_dates": sum(1 for x in b.backlog if x.due_date is None),
    }
    all_facts = [f for v in views.values() for f in _facts(v)]
    by_status = Counter(f["status"] for f in all_facts)
    by_name_status: Counter[tuple[str, str]] = Counter(
        (f["name"].split("@")[0].split("[")[0], f["status"])
        for f in all_facts
        if f["status"] != "PROVISIONAL_CALCULATED"
    )
    portal_mismatch = [
        {"item": item, **o}
        for item, os in sorted(oracles.items())
        for o in os
        if o["oracle"].startswith("portal_decision") and o["match"] is False
    ]
    other_mismatch = [
        {"item": item, **o}
        for item, os in sorted(oracles.items())
        for o in os
        if not o["oracle"].startswith("portal_decision") and o["match"] is False
    ]
    customers = sorted({x.customer_key for x in b.backlog})
    unknown_portal = [
        {
            "item": x.item,
            "line_id": x.line_id,
            "ordered_raw": x.ordered_raw,
            "shipped_raw": x.shipped_raw,
            "status": x.status,
        }
        for x in b.portal
        if portal_decision(x, rules)[0] == "NO_CUANTIFICABLE"
    ]
    negative_fg = [
        {"item": x.item, "snapshot": x.snapshot, "available": fmt(x.available)}
        for x in b.fg
        if x.available is not None and x.available < 0
    ]
    route_gaps = []
    for item, v in sorted(views.items()):
        for w in _wo_progress(v):
            for lim in w["limitations"]:
                route_gaps.append({"item": item, "order_no": w["order_no"], "note": lim})
    series = Counter(m.series for m in b.billing)
    late_by_wo = Counter(x.order_no for x in b.late)
    corr_unmatched = [
        c.corr_id
        for c in b.corrections
        if c.matched_labor.upper() not in ("Y", "YES", "SI", "SÍ", "TRUE", "1")
    ]
    return {
        "universe_source": universe_source,
        "sources": {sid: t.describe() for sid, t in sorted(loaded.tables.items())},
        "adapter_errors": dict(sorted(loaded.errors.items())),
        "integrity": integrity,
        "fact_status_counts": dict(sorted(by_status.items())),
        "non_certifiable_fields": [
            {"field": n, "status": s, "items": c} for (n, s), c in sorted(by_name_status.items())
        ],
        "customer_classification": {
            "rules_version": rules.version,
            "approval": rules.approval,
            "keys": [
                {
                    "customer_key_exact": k,
                    "class": rules.customer_class(k),
                    "note": rules.customer_notes.get(k),
                }
                for k in customers
            ],
            "keys_without_class": [k for k in customers if rules.customer_class(k) == "OTHER"],
        },
        "portal": {
            "decision_mismatches_vs_provided": portal_mismatch,
            "unknown_quantity_lines": unknown_portal,
        },
        "fg_negative_available": negative_fg,
        "billing_series": dict(sorted(series.items())),
        "late_adjustments": {
            "rows": len(b.late),
            "work_orders": len(late_by_wo),
            "treatment": "anotación; no se suman como producción adicional",
        },
        "corrections": {
            "rows": len(b.corrections),
            "not_matched_in_labor": corr_unmatched,
            "treatment": "ya representadas en los grupos de producción; no se suman",
        },
        "operations_outside_current_route": route_gaps,
        "oracle_mismatches": other_mismatch,
    }


def discrepancies_md(d: dict[str, Any]) -> str:
    lines = [
        "# Discrepancias y campos no certificables",
        "",
        f"Universo de ITEM: `{d['universe_source']}`",
        "",
        "## Fuentes",
        "",
        "| Fuente | Rol | Disponible | Filas | SHA-256 | Motivo |",
        "|---|---|---|---|---|---|",
    ]
    for s in d["sources"].values():
        lines.append(
            f"| {s['source_id']} | {s['role']} | {s['available']} | {s['rows']} | "
            f"`{(s['sha256'] or '')[:16]}` | {s['reason'] or ''} |"
        )
    lines += ["", "## Integridad (duplicados)", ""]
    lines += [f"- {k}: {v}" for k, v in d["integrity"].items()]
    lines += ["", "## Estados de los hechos", ""]
    lines += [f"- {k}: {v}" for k, v in d["fact_status_counts"].items()]
    lines += [
        "",
        "## Campos no certificables (por campo y estado; nº de hechos)",
        "",
        "| Campo | Estado | Hechos |",
        "|---|---|---|",
    ]
    lines += [
        f"| {x['field']} | {x['status']} | {x['items']} |" for x in d["non_certifiable_fields"]
    ]
    cc = d["customer_classification"]
    lines += [
        "",
        f"## Clasificación de clientes ({cc['rules_version']}, {cc['approval']})",
        "",
        "| Clave exacta | Clase | Nota |",
        "|---|---|---|",
    ]
    lines += [
        f"| `{k['customer_key_exact']}` | {k['class']} | {k['note'] or ''} |" for k in cc["keys"]
    ]
    p = d["portal"]
    lines += [
        "",
        "## Portal CAT",
        "",
        "- Decisiones distintas a la columna provista: "
        f"{len(p['decision_mismatches_vs_provided'])}",
        f"- Líneas con cantidad desconocida (no cero): {len(p['unknown_quantity_lines'])}",
    ]
    lines += [
        f"  - {x['item']} línea {x['line_id']}: Ordered `{x['ordered_raw']}` / Shipped "
        f"`{x['shipped_raw']}` ({x['status']})"
        for x in p["unknown_quantity_lines"]
    ]
    lines += [
        f"  - discrepancia {x['item']} {x['oracle']}: provista {x['expected']} vs "
        f"calculada {x['computed']}"
        for x in p["decision_mismatches_vs_provided"]
    ]
    lines += ["", "## FG disponible negativo (se conserva; cobertura NOT_CERTIFIABLE)", ""]
    lines += [
        f"- {x['item']} @ {x['snapshot']}: {x['available']}" for x in d["fg_negative_available"]
    ]
    lines += ["", "## Facturación por serie (no son embarques físicos)", ""]
    lines += [f"- {k}: {v}" for k, v in d["billing_series"].items()]
    lines += [
        "",
        "## Correcciones y ajustes tardíos",
        "",
        f"- Correcciones: {d['corrections']['rows']} ({d['corrections']['treatment']}); "
        f"sin match en producción: {d['corrections']['not_matched_in_labor']}",
        f"- Ajustes tardíos: {d['late_adjustments']['rows']} en "
        f"{d['late_adjustments']['work_orders']} WO ({d['late_adjustments']['treatment']})",
        "",
        "## Notas por WO (operaciones fuera del routing actual / ajustes tardíos)",
        "",
    ]
    lines += [
        f"- {x['item']} WO {x['order_no']}: {x['note']}"
        for x in d["operations_outside_current_route"]
    ]
    lines += ["", "## Discrepancias frente a oráculos de conteo", ""]
    lines += [
        f"- {x['item']} {x['oracle']}: esperado {x['expected']} vs calculado {x['computed']}"
        for x in d["oracle_mismatches"]
    ] or ["- ninguna"]
    if d["adapter_errors"]:
        lines += ["", "## Errores de adaptador (fuente bloqueada)", ""]
        lines += [f"- {k}: {v}" for k, v in d["adapter_errors"].items()]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ HTML navegable
def _template() -> str:
    path = resources.files("nexus.planning").joinpath("templates/item_view.html")
    return path.read_text("utf-8")


def render_html(
    views: dict[str, Any], oracles: dict[str, Any], digest: str, rules: PlanningRules
) -> bytes:
    data = json.dumps({"items": views, "oracles": oracles}, sort_keys=True, ensure_ascii=False)
    data = data.replace("</", "<\\/")
    page = (
        _template()
        .replace("__DIGEST__", digest)
        .replace("__RULES__", html.escape(f"{rules.version} ({rules.approval})"))
        .replace("__ENGINE__", ENGINE_VERSION)
        .replace("__DATA__", data)
    )
    return page.encode("utf-8")


# ------------------------------------------------------------------ orquestación
def _file_name(item: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in item)


def run(
    dataset: Path,
    rules_path: Path,
    out_dir: Path,
    layout: Path | None = None,
    items: list[str] | None = None,
) -> dict[str, Any]:
    rules = load_rules(rules_path)
    layout = layout or default_layout()
    loaded = load(dataset, layout, rules)
    universe, universe_source = item_universe(loaded)
    selected = [i for i in universe if items is None or i in items]
    views: dict[str, dict[str, Any]] = {}
    oracles: dict[str, list[dict[str, Any]]] = {}
    for item in selected:
        view = serialize(item_view(item, loaded.bundle, rules))
        views[item] = view
        oracles[item] = oracle_checks(item, loaded, view, rules)
    disc = discrepancies(loaded, rules, views, oracles, universe_source)
    matrix = _csv([matrix_row(views[i], oracles[i]) for i in selected])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "items").mkdir(exist_ok=True)
    store = LocalObjectStore(out_dir / "store")
    item_index = {}
    for item in selected:
        doc = {"schema": RUN_SCHEMA, "view": views[item], "oracles": oracles[item]}
        sealed = seal(doc)
        ref = store.put(f"items/{sha256_bytes(item.encode('utf-8'))[:32]}.json", sealed)
        (out_dir / "items" / f"{_file_name(item)}.json").write_bytes(sealed)
        item_index[item] = {"object_key": ref.key, "sha256": ref.sha256}
    disc_bytes = canonical_json(disc) + b"\n"
    results_digest = digest_json({"views": views, "oracles": oracles})
    manifest = {
        "schema": RUN_SCHEMA,
        "engine": ENGINE_VERSION,
        "rules": {
            "version": rules.version,
            "approval": rules.approval,
            "sha256": sha256_bytes(rules_path.read_bytes()),
        },
        "layout_sha256": sha256_bytes(layout.read_bytes()),
        "sources": {
            sid: {
                "sha256": t.sha256,
                "available": loaded.bundle.available[sid],
                "rows": len(t.rows),
            }
            for sid, t in sorted(loaded.tables.items())
        },
        "items": item_index,
        "item_count": len(selected),
        "matrix_sha256": sha256_bytes(matrix),
        "discrepancies_sha256": sha256_bytes(disc_bytes),
        "results_digest": results_digest,
        "statuses": sorted(STATUSES),
    }
    run_digest = digest_json(manifest)
    (out_dir / "matrix.csv").write_bytes(matrix)
    (out_dir / "discrepancies.json").write_bytes(disc_bytes)
    (out_dir / "discrepancies.md").write_text(discrepancies_md(disc), "utf-8")
    (out_dir / "index.html").write_bytes(render_html(views, oracles, run_digest, rules))
    (out_dir / "run_manifest.json").write_bytes(seal({**manifest, "run_digest": run_digest}))
    return {
        "run_digest": run_digest,
        "results_digest": results_digest,
        "items": len(selected),
        "out_dir": str(out_dir),
        "fact_status_counts": disc["fact_status_counts"],
        "oracle_mismatches": len(disc["oracle_mismatches"]),
        "portal_decision_mismatches": len(disc["portal"]["decision_mismatches_vs_provided"]),
        "blocked_sources": sorted(s for s, ok in loaded.bundle.available.items() if not ok),
    }
