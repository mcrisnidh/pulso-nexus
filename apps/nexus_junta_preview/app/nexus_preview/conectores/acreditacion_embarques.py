"""FIX5 — acreditación /2 de Embarques con la regla comercial RESUELTA (9-nov-2026).

Regla de Associated Spring México, confirmada por el responsable del proyecto: cuando se emite la
factura CFDI el material está listo para cargarse; ese evento ES el embarque. La facturación de
``MF_CFD``/``MF_DETCFD`` (fecha ``FECEXP``) alimenta el indicador Embarques por ITEM. No se exige
evidencia de salida física, transporte ni recepción.

La acreditación /2 NO se fabrica ni se emite sin verificación:

- ``evento_comercial_cfdi_validado`` (sustituye a ``fecha_es_embarque_fisico`` de la /1, que
  obligaba a afirmar un evento logístico que la empresa no necesita demostrar);
- regla ``CFDI_EMITIDA_ES_EMBARQUE`` con fecha ``FECEXP`` (contrato del conector FIX4);
- identidad EXACTA del ITEM (sin normalizar ni agrupar);
- decisión EXPLÍCITA sobre la ausencia de un ITEM en la facturación del periodo
  (``ausencia_es_cero``: sin valor por omisión);
- aprobador incluido en la lista privada de aprobadores;
- captura de REFERENCIA con PROCEDENCIA corroborable (FIX5.1): su archivo de procedencia (el
  ``.json`` que escriben el preflight o el almacén de capturas AL CONSULTAR) declara conector,
  tipo, resultado OK, huella de configuración + SQL, periodo y conteos, y concuerda con el archivo
  (SHA-256, ITEM, facturas). Un CSV colocado a mano, sin esa procedencia, se rechaza;
- decisión comercial EXPLÍCITA y REFERENCIADA sobre la ausencia (``decision_ausencia``);
- huella de la configuración y de la CONSULTA (servidor, base, cuenta, atributos ODBC y texto SQL
  de FIX4): si cambian, la acreditación deja de valer.

Alcance del control de procedencia (honesto): es un control de INTEGRIDAD y COHERENCIA local, no
una firma. Detecta CSV fabricados o sustituidos sin su procedencia, procedencias de otra consulta,
configuración o periodo, y alteraciones posteriores a la acreditación. NO protege frente a quien
pueda reescribir a la vez el CSV, su procedencia y la acreditación en la carpeta de datos.

Alcance declarado (no se afirma lo que la consulta no puede verificar): cancelaciones, notas de
crédito, devoluciones y refacturaciones NO se descuentan en la consulta actual (no lee estado de
cancelación ni documentos de egreso). Se informa en cada generación como limitación.
La /1 (con evidencia física) se sigue verificando con su lógica original por compatibilidad.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from . import embarques
from .acreditacion_fg import _dentro

ESQUEMA = "nexus.preview.acreditacion-embarques/2"
ARCHIVO = "acreditacion_embarques.json"
REGLA = "CFDI_EMITIDA_ES_EMBARQUE"
FECHA_EVENTO = "FECEXP"
ORIGENES = ("preflight/embarques", "capturas/embarques")
NATURALEZA = "FACTURACION_CFDI_POR_ITEM = EMBARQUE (regla comercial ASM, factura CFDI emitida)"
ALCANCE_AJUSTES = (
    "Cancelaciones, notas de crédito, devoluciones y refacturaciones NO se descuentan: la "
    "consulta actual (MF_CFD/MF_DETCFD por FECEXP) no lee estado de cancelación ni documentos "
    "de egreso. Embarques = facturación CFDI emitida en el periodo."
)
TIPO_CONECTOR = "ODBC_SQLSERVER_ASBG_CFDI"
ESQUEMA_PREFLIGHT = "nexus.preview.preflight-captura/1"
ESQUEMA_CAPTURA = "nexus.preview.captura/1"
_HEX = re.compile(r"[0-9a-f]{64}")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def huella_config(cfg: dict[str, Any]) -> str:
    """SHA-256 de lo que DEFINE la fuente: conexión, cuenta y texto exacto de la consulta FIX4."""
    body = {
        "controlador": str(cfg.get("controlador", "")),
        "servidor": str(cfg.get("servidor", "")),
        "base": str(cfg.get("base", "")),
        "credencial": str(cfg.get("credencial", "")),
        "cuenta_esperada": str(cfg.get("cuenta_esperada", "sm0003")).lower(),
        "atributos_conexion": {
            str(k): str(v) for k, v in sorted((cfg.get("atributos_conexion") or {}).items())
        },
        "sql": embarques.SQL,
        "sql_facturas": embarques.SQL_FACTURAS,
        "fecha_evento": FECHA_EVENTO,
    }
    return _sha(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8"))


def validar_captura(raw: bytes) -> dict[str, dict[str, str]]:
    """Contrato de columnas del conector, ITEM no vacío y único. Falla con ValueError."""
    texto = raw.decode("utf-8")
    lector = csv.DictReader(io.StringIO(texto))
    if tuple(lector.fieldnames or ()) != embarques.CAMPOS:
        raise ValueError(f"columnas {lector.fieldnames} ≠ contrato {list(embarques.CAMPOS)}")
    out: dict[str, dict[str, str]] = {}
    for fila in lector:
        item = fila["item"]
        if not item or item != item.strip():
            raise ValueError("ITEM vacío o con espacios exteriores")
        if item in out:
            raise ValueError("ITEM duplicado")
        out[item] = fila
    return out


def sha_sql() -> str:
    """SHA-256 del contrato SQL del conector FIX4 (agregado + identidades de factura)."""
    return _sha((embarques.SQL + "\n" + embarques.SQL_FACTURAS).encode("utf-8"))


def meta_procedencia(cfg: dict[str, Any]) -> dict[str, Any]:
    """Procedencia que se REGISTRA en el momento de la captura (preflight y Refresh).

    Solo se llama después de que ``embarques.capturar`` terminó sin error: ``resultado`` OK."""
    return {
        "conector": "embarques",
        "tipo_conector": TIPO_CONECTOR,
        "resultado": "OK",
        "huella_config_embarques": huella_config(cfg),
        "sha256_sql": sha_sql(),
        "contrato_columnas": list(embarques.CAMPOS),
    }


def _fecha(v: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v))
    except ValueError:
        return None


def _coherencia(
    meta: dict[str, Any],
    esperado: dict[str, Any],
    instante: str,
    periodo: list[Any],
    items: dict[str, dict[str, str]],
) -> list[str]:
    """Metadatos registrados al consultar frente a la configuración actual y al propio archivo."""
    motivos = [
        f"procedencia sin «{k}» válido (captura anterior a FIX5.1 o ajena)"
        for k in ("conector", "tipo_conector", "resultado", "sha256_sql", "contrato_columnas")
        if meta.get(k) != esperado[k]
    ]
    if meta.get("huella_config_embarques") != esperado["huella_config_embarques"]:
        motivos.append("la captura se hizo con otra configuración o consulta de Embarques")
    ini, fin = (_fecha(periodo[0]), _fecha(periodo[1])) if len(periodo) == 2 else (None, None)
    hecho = _fecha(instante[:10])
    if ini is None or fin is None or hecho is None:
        motivos.append("procedencia sin periodo o instante de ejecución válidos")
    elif ini.day != 1 or (ini.year, ini.month) != (fin.year, fin.month) or fin < ini or fin > hecho:
        motivos.append("periodo inconsistente con el mes o con el instante de la ejecución")
    if items:
        docs = [int(f["documentos"] or 0) for f in items.values()]
        fact = meta.get("facturas_verificadas")
        if meta.get("items") != len(items):
            motivos.append("el número de ITEM registrado no coincide con la captura")
        if not isinstance(fact, int) or not max(docs) <= fact <= sum(docs):
            motivos.append("facturas verificadas incoherentes con los documentos por ITEM")
    return motivos


def procedencia(
    data: Path, rel: str, cfg: dict[str, Any]
) -> tuple[list[str], dict[str, Any], Path | None]:
    """Verifica la procedencia registrada de una captura de Embarques. (motivos, resumen, ruta).

    Reutiliza los archivos que YA escriben el preflight (``<instante>.json`` junto a ``.raw`` y
    ``.csv``) y el almacén de capturas (``<nombre>.json`` junto a ``.raw``)."""
    data = data.resolve()
    motivos: list[str] = []
    if rel.startswith("preflight/embarques/") and rel.endswith(".csv"):
        base, esquema = rel[: -len(".csv")], ESQUEMA_PREFLIGHT
    elif rel.startswith("capturas/embarques/") and rel.endswith(".raw"):
        base, esquema = rel[: -len(".raw")], ESQUEMA_CAPTURA
    else:
        return (
            ["tipo de captura no admitido (preflight/embarques/*.csv o capturas/embarques/*.raw)"],
            {},
            None,
        )
    arch = _dentro(data, rel)
    side = _dentro(data, base + ".json")
    if arch is None:
        return ["la captura de referencia no existe dentro de la carpeta de datos"], {}, None
    if side is None:
        return ["captura sin procedencia registrada (falta su .json de ejecución)"], {}, None
    try:
        doc = json.loads(side.read_text("utf-8"))
    except ValueError:
        return ["procedencia ilegible"], {}, side
    if not isinstance(doc, dict) or doc.get("schema") != esquema:
        return [f"procedencia con esquema distinto de {esquema}"], {}, side
    raw = arch.read_bytes()
    sha = _sha(raw)
    if esquema == ESQUEMA_PREFLIGHT:
        if doc.get("conector") != "embarques":
            motivos.append("la procedencia no es del conector embarques")
        gemelo = _dentro(data, base + ".raw")
        if gemelo is None or _sha(gemelo.read_bytes()) != sha:
            motivos.append("el .csv no coincide con el .raw original de la misma ejecución")
    elif doc.get("fuente") != "embarques" or doc.get("archivo") != Path(base).name:
        motivos.append("la procedencia no corresponde a esta captura del almacén")
    if str(doc.get("sha256", "")).lower() != sha:
        motivos.append("el SHA-256 registrado en la procedencia no coincide con el archivo")
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    instante = str(doc.get("instante_utc", ""))
    periodo = meta.get("periodo") if isinstance(meta.get("periodo"), list) else []
    try:
        items = validar_captura(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        motivos.append(f"la captura no cumple el contrato del conector: {exc}")
        items = {}
    motivos += _coherencia(meta, meta_procedencia(cfg), instante, periodo, items)
    resumen = {
        "procedencia": base + ".json",
        "procedencia_sha256": _sha(side.read_bytes()),
        "instante_utc": instante,
        "periodo": list(periodo),
        "items": len(items),
        "facturas_verificadas": meta.get("facturas_verificadas"),
    }
    return motivos, resumen, side


def _clave(item: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", item.upper())


def asignar(
    universo: list[str], por_item: dict[str, str] | None, ausencia_es_cero: bool
) -> tuple[dict[str, str | None], dict[str, int]]:
    """Valor de Embarques por ITEM del universo y su CLASIFICACIÓN (FIX5.1). Sin ``por_item``
    (consulta fallida/parcial sin corte acreditado) todo queda desconocido. Clases:

    - FACTURADO: ITEM exacto con cantidad conocida;
    - CANTIDAD_NULA: ITEM exacto con algún renglón nulo → desconocido (nunca 0);
    - IDENTIDAD_NO_EXACTA: el ITEM no aparece exacto pero sí una variante (mayúsculas, espacios o
      símbolos) → desconocido: NO se le asigna la cantidad ni se presume 0;
    - AUSENTE_CERO / AUSENTE_DESCONOCIDO: ausente de una consulta completa y exitosa; vale 0 solo
      con la decisión comercial aprobada ``ausencia_es_cero``;
    - FUERA_DEL_UNIVERSO: ITEM facturado que no está en el universo mensual (no entra al cálculo);
    - SIN_CONSULTA_ACREDITADA: no hay corte acreditado utilizable.
    """
    if por_item is None:
        return dict.fromkeys(universo), {"SIN_CONSULTA_ACREDITADA": len(universo)}
    exactos = set(universo)
    fuera = [k for k in por_item if k not in exactos]
    claves_fuera = {_clave(k) for k in fuera}
    claves_universo = {_clave(k) for k in universo}
    out: dict[str, str | None] = {}
    cls: Counter[str] = Counter()
    for it in universo:
        if it in por_item:
            v = por_item[it]
            out[it] = v if v != "" else None
            cls["FACTURADO" if v != "" else "CANTIDAD_NULA"] += 1
        elif _clave(it) in claves_fuera:
            out[it] = None
            cls["IDENTIDAD_NO_EXACTA"] += 1
        else:
            out[it] = "0" if ausencia_es_cero else None
            cls["AUSENTE_CERO" if ausencia_es_cero else "AUSENTE_DESCONOCIDO"] += 1
    cls["FUERA_DEL_UNIVERSO"] = sum(1 for k in fuera if _clave(k) not in claves_universo)
    return out, {k: v for k, v in sorted(cls.items()) if v}


def es_v2(path: Path) -> bool:
    try:
        return json.loads(path.read_text("utf-8")).get("schema") == ESQUEMA
    except (OSError, ValueError, AttributeError):
        return False


def verificar(
    data: Path, cfg: dict[str, Any], aprobadores: frozenset[str], ruta: Path | None = None
) -> tuple[bool, list[str], dict[str, Any]]:
    """(válida, motivos, resumen). Fallo cerrado ante cualquier inconsistencia."""
    p = ruta or data / ARCHIVO
    if not p.is_file():
        return False, ["sin acreditación de la fuente CFDI de Embarques"], {}
    try:
        doc = json.loads(p.read_text("utf-8"))
    except ValueError:
        return False, ["acreditación de Embarques ilegible"], {}
    if not isinstance(doc, dict) or doc.get("schema") != ESQUEMA:
        return False, [f"se esperaba el esquema {ESQUEMA}"], {}
    info: dict[str, Any] = {"acreditacion_sha256": _sha(p.read_bytes())}
    motivos: list[str] = []
    if doc.get("acreditado") is not True:
        motivos.append("acreditación no vigente")
    if doc.get("regla") != REGLA or doc.get("fecha_evento") != FECHA_EVENTO:
        motivos.append(f"regla distinta de {REGLA} con fecha {FECHA_EVENTO}")
    for k in ("evento_comercial_cfdi_validado", "identidad_item_exacta"):
        if doc.get(k) is not True:
            motivos.append(f"criterio no acreditado: {k}")
    if not isinstance(doc.get("ausencia_es_cero"), bool):
        motivos.append("falta la decisión explícita ausencia_es_cero (true/false)")
    dec = doc.get("decision_ausencia") if isinstance(doc.get("decision_ausencia"), dict) else {}
    if (
        dec.get("ausencia_es_cero") != doc.get("ausencia_es_cero")
        or not str(dec.get("referencia", "")).strip()
        or dec.get("aprobado_por") != doc.get("aprobado_por")
    ):
        motivos.append("falta la decisión comercial aprobada y referenciada sobre la ausencia")
    if doc.get("aprobado_por") not in aprobadores:
        motivos.append("aprobador no autorizado")
    if not _HEX.fullmatch(str(doc.get("huella_config_embarques", ""))):
        motivos.append("huella de configuración ausente o mal formada")
    elif doc["huella_config_embarques"] != huella_config(cfg):
        motivos.append("la configuración o la consulta de Embarques cambió desde la acreditación")
    rel = str(doc.get("captura_referencia", ""))
    ref = _dentro(data, rel)
    if ref is None or not any(rel.startswith(o + "/") for o in ORIGENES):
        motivos.append("la captura de referencia no existe en preflight/ o capturas/ de Embarques")
    elif _sha(ref.read_bytes()) != str(doc.get("captura_sha256", "")).lower():
        motivos.append("la captura de referencia no coincide con su SHA-256")
    else:
        prov, res, _ = procedencia(data, rel, cfg)
        motivos += prov
        if not prov:
            if res["procedencia_sha256"] != doc.get("procedencia_sha256"):
                motivos.append("la procedencia de la captura cambió desde la acreditación")
            if doc.get("items_referencia") != res["items"]:
                motivos.append(
                    "el número de ITEM declarado no coincide con la captura de referencia"
                )
    if motivos:
        return False, motivos, info
    info.update(ausencia_es_cero=doc["ausencia_es_cero"], aprobado_por=doc["aprobado_por"])
    return True, [], info


def documento(
    data: Path,
    cfg: dict[str, Any],
    captura: Path,
    aprobado_por: str,
    ausencia_es_cero: bool,
    stamp: str,
    referencia_decision: str,
) -> dict[str, Any]:
    """Documento /2 sobre una captura con PROCEDENCIA verificada (si no, ValueError)."""
    rel = captura.resolve().relative_to(data.resolve()).as_posix()
    raw = captura.read_bytes()
    if not referencia_decision.strip():
        raise ValueError("falta la referencia de la decisión comercial sobre la ausencia")
    prov, res, _ = procedencia(data.resolve(), rel, cfg)
    if prov:
        raise ValueError("procedencia no corroborable: " + "; ".join(prov))
    return {
        "schema": ESQUEMA,
        "acreditado": True,
        "regla": REGLA,
        "fecha_evento": FECHA_EVENTO,
        "evento_comercial_cfdi_validado": True,
        "identidad_item_exacta": True,
        "ausencia_es_cero": ausencia_es_cero,
        "decision_ausencia": {
            "ausencia_es_cero": ausencia_es_cero,
            "referencia": referencia_decision.strip(),
            "aprobado_por": aprobado_por,
        },
        "alcance_ajustes": ALCANCE_AJUSTES,
        "aprobado_por": aprobado_por,
        "captura_referencia": rel,
        "captura_sha256": _sha(raw),
        "items_referencia": res["items"],
        "procedencia": res["procedencia"],
        "procedencia_sha256": res["procedencia_sha256"],
        "instante_referencia_utc": res["instante_utc"],
        "periodo_referencia": res["periodo"],
        "facturas_referencia": res["facturas_verificadas"],
        "huella_config_embarques": huella_config(cfg),
        "acreditado_utc": stamp,
        "activa_conector": False,
    }
