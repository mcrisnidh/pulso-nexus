"""Contrato Status Ventas AMFT v3.10 — REIMPLEMENTADO (funciones puras, sin E/S).

Procedencia: ``PULSO Web App/commercial/status_v310.py`` (contrato PULSO_ONECLICK_COMMERCIAL_QA_
V3_10_RC2) y la auditoría del libro ``Status Ventas AMFT_v3_10.xlsm``:

    Programa      = max(0, Frozen + Adelanto − Embarques)
    Sobreembarque = max(0, Embarques − Frozen − Adelanto)
    Faltante FG   = max(0, Programa − FG)
    Cobertura FG  = embarques compensan la demanda semanal; el FG cubre semanas completas

Diferencias deliberadas frente al módulo heredado (prevalecen las decisiones NEXUS):
- Frozen o Embarques ausentes NUNCA se convierten en 0 (V19); el vacío de Adelanto CS
  sí es 0 por decisión comercial (2026-11-10) y se resuelve en ``webapp.enrich`` antes
  de llamar estas funciones puras. Un ``None`` explícito sigue siendo dato inválido.
- FG negativo NO se recorta a 0 (el heredado hacía ``max(0, FG)``): NEXUS lo trata como
  NO_CERTIFICABLE ⇒ Faltante ``None``.
- Sin normalización de claves (X15): las identidades llegan exactas desde el motor.
- No aplica alias CAT→JBA codificados: la homologación es R1 del motor.

La fuente CFDI puede estar acreditada como Embarques. Si Frozen o Embarques son
realmente desconocidos, Programa y Faltante siguen N/D; el vacío de Adelanto CS no los bloquea.
"""

from __future__ import annotations

from decimal import Decimal

ZERO = Decimal(0)


def programa(frozen: Decimal | None, adelanto: Decimal | None, embarques: Decimal | None) -> Decimal | None:
    if frozen is None or adelanto is None or embarques is None:
        return None
    return max(ZERO, frozen + adelanto - embarques)


def sobreembarque(frozen: Decimal | None, adelanto: Decimal | None, embarques: Decimal | None) -> Decimal | None:
    if frozen is None or adelanto is None or embarques is None:
        return None
    return max(ZERO, embarques - frozen - adelanto)


def faltante(prog: Decimal | None, fg: Decimal | None) -> Decimal | None:
    if prog is None or fg is None or fg < 0:
        return None
    return max(ZERO, prog - fg)


def cobertura_fg(
    semanas: list[tuple[str, Decimal]] | None, embarques: Decimal | None, fg: Decimal | None
) -> tuple[str | None, str | None]:
    """Semántica literal de ``Frozen_With_Embarques`` v3.10: los embarques compensan primero la
    demanda semanal; después el FG cubre semanas COMPLETAS. Devuelve (cobertura_hasta,
    primera_semana_con_faltante); ``(None, None)`` si falta un dato."""
    if semanas is None or embarques is None or fg is None or fg < 0:
        return None, None
    source = [(wk, q) for wk, q in semanas if q > 0]
    if not source:
        return None, None
    remaining = max(ZERO, embarques)
    after = []
    for wk, q in source:
        applied = min(remaining, q)
        remaining -= applied
        if q - applied > 0:
            after.append((wk, q - applied))
    if not after:
        return "SIN PENDIENTES", None
    stock = fg
    last = None
    for wk, q in after:
        if q <= stock:
            stock -= q
            last = wk
        else:
            return (last or "SIN COBERTURA"), wk
    return "MES COMPLETO", None


def orden(row: dict[str, object]) -> tuple[object, ...]:
    """Claves de orden de ``Estatus_Ventas_Final`` según el módulo heredado: Programa positivo
    primero, fecha solicitada, primera semana con faltante, faltante positivo y mayor, CS, grupo,
    artículo. Un valor N/D se ordena como «no positivo» (sin convertirlo en 0 en ninguna cifra)."""

    def pos(v: object) -> Decimal:
        return Decimal(str(v)) if v is not None else ZERO

    wk = row.get("faltante_semana") or "WK9999"
    wkn = int(str(wk)[2:]) if str(wk).startswith("WK") and str(wk)[2:].isdigit() else 9999
    return (
        0 if pos(row.get("programa_v310")) > 0 else 1,
        row.get("fecha_solicitada") or "9999-12-31",
        wkn,
        0 if pos(row.get("faltante_v310")) > 0 else 1,
        -pos(row.get("faltante_v310")),
        row.get("cs") or "~",
        row.get("grupo") or "~",
        row.get("item"),
    )
