"""Presentación de la junta: formato, etiquetas, excepciones, filtros e indicador de cobertura.

Solo transforma datos YA publicados (resumen sellado) y notas CS ya leídas; no recalcula ni
interpreta reglas comerciales. Una cifra desconocida se presenta como desconocida, nunca como 0.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any

MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
STATUS_LABELS = {
    "PROVISIONAL_CALCULATED": "Calculado",
    "DIAGNOSTIC_ONLY": "Diagnóstico",
    "UNKNOWN_NOT_ZERO": "Desconocido",
    "NOT_CERTIFIABLE": "No certificable",
    "BLOCKED_SOURCE": "Fuente bloqueada",
    "BLOCKED_CRC": "Regla pendiente",
    "TEMPORAL_MISMATCH": "Cortes distintos",
}
KIND_LABELS = {
    "NOTE": "Nota",
    "CS_STATUS": "Estatus CS",
    "DELIVERY_DATE": "Fecha CS",
    "ADVANCE": "Adelanto",
    "RETRACT": "Retiro",
}
EVENT_LABELS = {
    "REFRESH_REQUESTED": "Refresh solicitado",
    "FROZEN_REGISTERED": "Frozen registrado",
    "PUBLISH": "Corte publicado",
    "ROLLBACK": "Rollback de publicación",
    "REFRESH_NOT_PUBLISHED": "Refresh no publicado (validación)",
    "REFRESH_ATTEMPT_FAILED": "Intento de Refresh fallido",
    "PUBLISH_REFUSED": "Publicación rechazada",
}
FAILURE_EVENTS = ("REFRESH_NOT_PUBLISHED", "REFRESH_ATTEMPT_FAILED", "PUBLISH_REFUSED")
METRICS = ("demand", "attended", "pending", "fg_assignable", "covered", "uncovered")


def to_dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def num(value: Any) -> str:
    """Cantidad con separador de miles (formato México: 12,345.5). Desconocido: '—'."""
    d = to_dec(value)
    if d is None:
        return "—"
    d = d.normalize()
    text = f"{d:,f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _parse(value: str) -> dt.datetime | dt.date | None:
    try:
        if "T" in value:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def date_es(value: Any) -> str:
    parsed = _parse(str(value or ""))
    if parsed is None:
        return "—" if not value else str(value)
    return f"{parsed.day} {MONTHS[parsed.month - 1][:3]} {parsed.year}"


def datetime_es(value: Any) -> str:
    parsed = _parse(str(value or ""))
    if not isinstance(parsed, dt.datetime):
        return date_es(value)
    return f"{date_es(parsed.date().isoformat())} · {parsed:%H:%M} UTC"


def month_es(value: Any) -> str:
    text = str(value or "")
    try:
        year, mon = (int(x) for x in text.split("-")[:2])
    except ValueError:
        return text
    return f"{MONTHS[mon - 1].capitalize()} {year}"


def status_label(code: Any) -> str:
    return STATUS_LABELS.get(str(code), str(code))


def kind_label(code: Any) -> str:
    return KIND_LABELS.get(str(code), str(code))


def event_label(code: Any) -> str:
    return EVENT_LABELS.get(str(code), str(code))


def cs_value(kind: str, value: Any) -> str:
    if kind == "DELIVERY_DATE":
        return date_es(value)
    if kind == "ADVANCE" and isinstance(value, dict):
        when = f" · {date_es(value['date'])}" if value.get("date") else ""
        return f"{num(value.get('qty'))}{when}"
    if kind == "RETRACT":
        return f"retira la versión {value}"
    return str(value)


# ------------------------------------------------------------------ indicador de cobertura
def coverage_bar(values: dict[str, Decimal | None]) -> dict[str, Any] | None:
    """Segmentos (embarcado / cubierto FG / sin cubrir) sobre la demanda congelada. Solo si las
    cuatro cifras son conocidas, la demanda es positiva y la suma cuadra exactamente."""
    demand, attended = values.get("demand"), values.get("attended")
    covered, uncovered = values.get("covered"), values.get("uncovered")
    if None in (demand, attended, covered, uncovered) or not demand or demand <= 0:
        return None
    assert demand is not None and attended is not None  # noqa: S101 (estrechamiento de tipos)
    assert covered is not None and uncovered is not None  # noqa: S101
    if attended + covered + uncovered != demand:
        return None
    seg = []
    x = Decimal(0)
    for key, label, part in (
        ("attended", "Embarcado", attended),
        ("covered", "Cubierto FG", covered),
        ("uncovered", "Sin cubrir", uncovered),
    ):
        width = (part / demand * 100).quantize(Decimal("0.01"))
        seg.append(
            {
                "key": key,
                "label": label,
                "qty": part,
                "x": x,
                "width": width,
                "pct": int((part / demand * 100).to_integral_value()),
            }
        )
        x += width
    return {"segments": seg, "demand": demand}


# ------------------------------------------------------------------ filas de la junta
def board_rows(summary: dict[str, Any], cs: dict[str, Any]) -> list[dict[str, Any]]:
    excess_items: dict[str, list[str]] = {}
    for kind, label in (
        ("excess", "Embarque excede demanda"),
        ("unattributed", "Embarque sin evidencia"),
    ):
        for entry in summary.get("attribution", {}).get(kind, []):
            excess_items.setdefault(str(entry.get("item")), []).append(label)
    rows = []
    for item, data in sorted(summary["rows"].items()):
        facts = data["facts"]
        values = {k: to_dec(facts[k]["value"]) for k in METRICS}
        exceptions = []
        if facts["demand"]["status"] in ("UNKNOWN_NOT_ZERO", "NOT_CERTIFIABLE"):
            exceptions.append("Demanda desconocida")
        fg_status = facts["fg_assignable"]["status"]
        if fg_status == "NOT_CERTIFIABLE":
            exceptions.append("FG no certificable")
        elif fg_status == "UNKNOWN_NOT_ZERO":
            exceptions.append("FG desconocido")
        if any("agregada" in lim for lim in facts["attended"]["limitations"]):
            exceptions.append("Embarque atribuido de forma agregada")
        exceptions += sorted(set(excess_items.get(item, [])))
        uncovered = values["uncovered"]
        coverage = "sin_dato" if uncovered is None else "faltante" if uncovered > 0 else "cubierto"
        board = cs.get(item, {"current": {}, "last_note": None, "threads": 0})
        status = board["current"].get("CS_STATUS")
        rows.append(
            {
                "item": item,
                "facts": facts,
                "nums": values,
                "kpi": data["kpi"],
                "official": all(v == "OFICIAL" for v in data["kpi"].values()),
                "exceptions": exceptions,
                "coverage": coverage,
                "bar": coverage_bar(values),
                "cs": board,
                "cs_status": status["value"] if status else None,
            }
        )
    return rows


def apply_filters(rows: list[dict[str, Any]], args: dict[str, str]) -> list[dict[str, Any]]:
    q = args.get("q", "").strip().upper()
    cov = args.get("cobertura", "")
    status = args.get("estatus", "")
    only_exc = args.get("excepciones", "") == "1"
    out = [
        r
        for r in rows
        if (not q or q in r["item"].upper())
        and (not cov or r["coverage"] == cov)
        and (
            not status
            or (status == "__none__" and r["cs_status"] is None)
            or r["cs_status"] == status
        )
        and (not only_exc or r["exceptions"])
    ]
    if args.get("orden", "sin_cubrir") == "item":
        return out
    return sorted(
        out,
        key=lambda r: (
            r["nums"]["uncovered"] is None,
            -(r["nums"]["uncovered"] or 0),
            r["item"],
        ),
    )


def totals_view(totals: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Totales publicados + indicador global: completo, o sobre el subconjunto de ITEM con datos
    completos (rotulado como tal), o no disponible."""
    complete_all = all(totals[m]["complete"] for m in METRICS)
    values = {m: to_dec(totals[m]["known_sum"]) for m in METRICS}
    if complete_all:
        return {
            "bar": coverage_bar(values),
            "scope": "all",
            "n_items": len(rows),
            "subset": len(rows),
        }
    with_bar = [r for r in rows if r["bar"] is not None]
    if not with_bar:
        return {"bar": None, "scope": "none", "n_items": len(rows), "subset": 0}
    sub = {m: sum((r["nums"][m] or Decimal(0) for r in with_bar), Decimal(0)) for m in METRICS}
    return {
        "bar": coverage_bar(sub),
        "scope": "subset",
        "n_items": len(rows),
        "subset": len(with_bar),
    }


def last_refresh_failure(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Último fallo de Refresh posterior a la última publicación o rollback (si lo hay)."""
    for event in reversed(events):
        if event["kind"] in ("PUBLISH", "ROLLBACK"):
            return None
        if event["kind"] in FAILURE_EVENTS:
            return dict(event)
    return None


def failure_reasons(event: dict[str, Any]) -> list[str]:
    detail = event.get("detail", {})
    validation = detail.get("validation") or {}
    reasons = list(validation.get("failures", [])) + list(
        validation.get("invariant_violations", [])
    )
    if detail.get("reason"):
        reasons.append(str(detail["reason"]))
    if detail.get("detail"):
        reasons.append(str(detail["detail"]))
    return reasons or ["sin detalle"]
