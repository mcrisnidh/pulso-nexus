"""FIX6: confirmación de primer corte SSRS mensual, SIN ERP manual.

El XML conocido prueba reporte, estructura, periodo y contenido, pero NO contiene evidencia
independiente de cuándo terminó el ETL overnight. Sin marca de lote del servidor, la
primera captura del mes requiere confirmación explícita del operador acreditado.
Nunca se deduce que el lote nocturno concluyó a partir de la hora local.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from . import ConectorError, ssrs
from . import orquestador as orq

_SCHEMA = "nexus.preview.confirmacion-ssrs-frozen/1"
_HEX = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def primer_corte(data: Path, month: str) -> dict[str, Any]:
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month):
        raise ConectorError("MES_INVALIDO", month)
    docs = [d for d in orq.listar(data, "backlog_ssrs") if
            str(d.get("fecha_documento") or "").startswith(month)]
    docs.sort(key=lambda d: (d["fecha_documento"], d["instante_utc"], d["archivo"]))
    if not docs:
        raise ConectorError("SSRS_SIN_CAPTURA_MENSUAL", "se requiere el primer XML SSRS válido del mes")
    return docs[0]


def _validar_captura(data: Path, month: str, doc: dict[str, Any]) -> None:
    if doc.get("schema") != "nexus.preview.captura/1" or doc.get("fuente") != "backlog_ssrs":
        raise ConectorError("SSRS_CAPTURA_INVALIDA", "esquema o fuente")
    if not _HEX.fullmatch(str(doc.get("sha256") or "")):
        raise ConectorError("SSRS_CAPTURA_INVALIDA", "huella")
    if doc.get("meta", {}).get("periodo") != f"{month[5:]}/{month[:4]}":
        raise ConectorError("SSRS_PERIODO_DISTINTO", "periodo reportado no coincide con mes")
    cfg = (orq.cargar_config(data).get("backlog_ssrs") or {})
    raw = orq._leer(data, "backlog_ssrs", doc, ".raw")
    normalizado = orq._leer(data, "backlog_ssrs", doc, ".csv")
    from nexus.core.stage import contract_csv  # noqa: PLC0415

    records, meta = ssrs.parse(raw, cfg, month)
    if meta.get("lineas", 0) <= 0 or contract_csv("oeras_29", records) != normalizado:
        raise ConectorError("SSRS_CONTRATO_INVALIDO", "normalización o línea inválida")


def emitir(data: Path, month: str, archivo: str, sha256: str,
           aprobador: str, referencia: str) -> Path:
    """Sella el PRIMER corte CONFIRMADO, no el primer intento previo al overnight.

    archivo es el identificador interno de una captura SSRS real, NO una ruta arbitraria.
    """
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month):
        raise ConectorError("MES_INVALIDO", month)
    if not _HEX.fullmatch(sha256):
        raise ConectorError("SSRS_HUELLA_INVALIDA")
    if not aprobador.strip() or not referencia.strip():
        raise ConectorError("SSRS_CONFIRMACION_INCOMPLETA")
    # Usa la lista privada preexistente de aprobadores, no la inventa.
    config = json.loads((data / "preview.json").read_text("utf-8"))
    approvers = config.get("approvers")
    path = Path(approvers) if approvers else None
    if path and not path.is_absolute():
        path = data / path
    if not path or not path.is_file() or aprobador not in json.loads(path.read_text("utf-8")).get("approvers", []):
        raise ConectorError("SSRS_APROBADOR_NO_AUTORIZADO")
    matches = [d for d in orq.listar(data, "backlog_ssrs") if
               d.get("archivo") == archivo and
               str(d.get("fecha_documento") or "").startswith(month)]
    if len(matches) != 1 or matches[0].get("sha256", "").lower() != sha256.lower():
        raise ConectorError("SSRS_CAPTURA_NO_ENCONTRADA", "archivo/SHA/mes no coinciden")
    first = matches[0]  # primer corte APROBADO; los intentos previos no se congelan
    _validar_captura(data, month, first)
    folder = data / "confirmaciones_ssrs"
    folder.mkdir(exist_ok=True)
    target = folder / f"{month}.json"
    body = {
        "schema": _SCHEMA,
        "mes": month,
        "fuente": "backlog_ssrs",
        "archivo": first["archivo"],
        "sha256": first["sha256"],
        "instante_utc": first["instante_utc"],
        "periodo": first["meta"]["periodo"],
        "aprobador": aprobador,
        "referencia_lote_nocturno": referencia,
        "confirmado_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    # O_EXCL: la decisión mensual nunca se actualiza ni sustituye.
    with target.open("xb") as fh:
        fh.write((json.dumps(body, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
    return target


def confirmar_frozen(data: Path | None, month: str,
                     opciones: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    """Escoge SOLO el primer SSRS confirmado; rechaza cualquier Frozen no aprobado."""
    from nexus_preview.servicio import RefreshError  # noqa: PLC0415

    if data is None:
        raise RefreshError(["FIX6: directorio privado no disponible para validar Frozen"])
    p = data / "confirmaciones_ssrs" / f"{month}.json"
    if not p.is_file():
        raise RefreshError([f"FIX6: Frozen {month} PENDIENTE. Primero confirme el "
                            "corte SSRS tras el ETL nocturno; nunca se sella a ciegas"])
    raw = p.read_bytes()
    try:
        approval = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise RefreshError(["FIX6: confirmación SSRS inválida"]) from exc
    matches = [f for f in opciones if f["file"] ==
               f"backlog_ssrs:{approval.get('archivo')}"]
    if len(matches) != 1:
        raise RefreshError(["FIX6: el corte SSRS confirmado no está disponible como Frozen"])
    frozen = matches[0]
    docs = [d for d in orq.listar(data, "backlog_ssrs") if
            d.get("archivo") == approval.get("archivo")]
    if len(docs) != 1:
        raise RefreshError(["FIX6: procedencia del SSRS confirmado ausente"])
    first = docs[0]
    checks = (
        approval.get("schema") == _SCHEMA,
        approval.get("mes") == month,
        approval.get("fuente") == "backlog_ssrs",
        approval.get("archivo") == first["archivo"],
        approval.get("sha256") == first["sha256"] == frozen["sha256"],
        frozen["file"] == f"backlog_ssrs:{first['archivo']}",
        approval.get("instante_utc") == first["instante_utc"],
        approval.get("periodo") == f"{month[5:]}/{month[:4]}",
        bool(approval.get("aprobador")), bool(approval.get("referencia_lote_nocturno")),
    )
    if not all(checks):
        raise RefreshError(["FIX6: confirmación del primer SSRS aprobado alterada o incongruente"])
    _validar_captura(data, month, first)
    return frozen, _hash(raw)


def verificar_bloqueo(data: Path | None, month: str, lock: dict[str, Any]) -> None:
    """Un Frozen SSRS ya sellado exige confirmación inmutable con huella vinculada."""
    from nexus_preview.servicio import RefreshError  # noqa: PLC0415

    if data is None or not lock.get("confirmacion_ssrs_sha256"):
        raise RefreshError(["FIX6: Frozen SSRS sin confirmación sellada"])
    p = data / "confirmaciones_ssrs" / f"{month}.json"
    if not p.is_file() or _hash(p.read_bytes()) != lock["confirmacion_ssrs_sha256"]:
        raise RefreshError(["FIX6: confirmación Frozen SSRS ausente o alterada"])
    approval = json.loads(p.read_text("utf-8"))
    docs = [d for d in orq.listar(data, "backlog_ssrs") if
            d.get("archivo") == approval.get("archivo")]
    if len(docs) != 1:
        raise RefreshError(["FIX6: captura SSRS sellada no disponible"])
    first = docs[0]
    expected = lock["oe965"]
    if approval.get("sha256") != first["sha256"] or approval.get("archivo") != first["archivo"]:
        raise RefreshError(["FIX6: primer corte SSRS no coincide con confirmación"])
    if expected.get("file") != f"backlog_ssrs:{first['archivo']}" or expected.get("sha256") != first["sha256"]:
        raise RefreshError(["FIX6: Frozen SSRS distinto del primer corte confirmado"])
    _validar_captura(data, month, first)
