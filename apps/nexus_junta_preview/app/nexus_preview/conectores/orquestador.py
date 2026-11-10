"""Orquestación de conectores dentro del Refresh de Preview + almacén de capturas inmutables.

Cada captura se guarda en ``<datos>/capturas/<fuente>/``: bytes originales (``.raw``), registros
normalizados al contrato NEXUS (``.csv``) y procedencia (``.json``, escrito al final). Nada se
sobrescribe; una captura idéntica (mismo SHA-256) se reutiliza. La procedencia guarda fuente,
cuenta (nombre, nunca secreto), consulta o URL, instante y huellas.

Activación: un conector se usa solo si ``activo: true`` en ``conectores.json`` (privado) y, para
Backlog y FG, si existe ``acreditacion_<fuente>.json`` con una conciliación PASS contra el extracto
manual (``conciliar.py``). v0.3.2: la acreditación FG se VERIFICA en cada Refresh
(``acreditacion_fg``: informe, evidencias, captura original, recálculo, alcance y huella de la
consulta); cada consulta FG exitosa es un corte nuevo (no se reutiliza el instante de otra captura). Política ante fallo (explícita por conector):
``CONSERVAR_ULTIMO_CORTE`` (por defecto: el Refresh continúa con el último corte válido de esa
fuente y se marca PARCIAL) o ``DETENER`` (el Refresh falla y se conserva la generación vigente).
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
import stat
import zoneinfo
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexus.core.physical.common import PhysicalResult
from nexus.core.stage import contract_csv

from . import (
    ConectorError,
    acreditacion_backlog,
    acreditacion_embarques,
    acreditacion_fg,
    embarques,
    fg_odbc,
    ssrs,
)

CONFIG = "conectores.json"
FUENTES = ("backlog_ssrs", "fg_odbc", "embarques")
POLITICAS = ("CONSERVAR_ULTIMO_CORTE", "DETENER")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def cargar_config(data: Path) -> dict[str, Any]:
    p = data / CONFIG
    if not p.is_file():
        return {}
    doc = json.loads(p.read_text("utf-8"))
    if doc.get("schema") != "nexus.preview.conectores/1":
        raise ConectorError("CONFIG_ESQUEMA", "conectores.json: se esperaba nexus.preview.conectores/1")
    texto = json.dumps(doc).lower()
    if re.search(r'"(pwd|password|contrase[nñ]a|secret|token|cookie)"\s*:', texto):
        raise ConectorError("CONFIG_CON_SECRETOS", "conectores.json no admite contraseñas, tokens ni cookies")
    cred = {f: (doc.get(f) or {}).get("credencial") for f in ("fg_odbc", "embarques")}
    if cred["fg_odbc"] and cred["fg_odbc"] == cred["embarques"]:
        raise ConectorError("CREDENCIAL_COMPARTIDA", "FG y Embarques deben usar credenciales distintas")
    return doc


def acreditado(data: Path, fuente: str, cfg: dict[str, Any] | None = None, tz: str = "America/Mexico_City") -> tuple[bool, str]:
    if fuente == "fg_odbc":  # v0.3.2: verificación criptográfica completa con alcance
        ok, motivos, _ = acreditacion_fg.verificar(data, cfg if cfg is not None else (cargar_config(data).get("fg_odbc") or {}), tz)
        return ok, "; ".join(motivos)
    if fuente == "backlog_ssrs":  # v0.3.3: verificación completa (fuente y contrato)
        cfg_b = cfg if cfg is not None else (cargar_config(data).get("backlog_ssrs") or {})
        ok_b, mot_b, _ = acreditacion_backlog.verificar(data, cfg_b)
        return ok_b, "; ".join(mot_b)
    p = data / f"acreditacion_{fuente}.json"
    if not p.is_file():
        return False, f"sin acreditación: ejecute la conciliación de {fuente} contra el extracto manual"
    try:
        doc = json.loads(p.read_text("utf-8"))
    except ValueError:
        return False, "acreditación ilegible"
    ok = (
        doc.get("schema") == "nexus.preview.acreditacion-conector/1"
        and doc.get("fuente") == fuente
        and doc.get("resultado") == "PASS"
        and re.fullmatch(r"[0-9a-f]{64}", str(doc.get("informe_sha256", "")))
    )
    return (True, "") if ok else (False, "acreditación sin conciliación PASS")


# ---------------------------------------------------------------- almacén de capturas
def guardar(
    data: Path, fuente: str, raw: bytes, normalizado: bytes | None, meta: dict[str, Any],
    instante_utc: dt.datetime, fecha_doc: str, reutilizar: bool = True,
) -> dict[str, Any]:
    """``reutilizar=False`` (FG, v0.3.2): cada consulta es un CORTE nuevo con su instante, aunque
    los bytes coincidan con un corte anterior; solo se reutiliza la misma consulta (mismo segundo)."""
    folder = data / "capturas" / fuente
    folder.mkdir(parents=True, exist_ok=True)
    digest = _sha(raw)
    name = f"{instante_utc:%Y%m%dT%H%M%SZ}_{digest[:12]}"
    for side in folder.glob("*.json") if reutilizar else [folder / f"{name}.json"]:
        if not side.is_file():
            continue
        doc = json.loads(side.read_text("utf-8"))
        if doc["sha256"] == digest:
            return {**doc, "reutilizada": True}
    for ext, body in ((".raw", raw), (".csv", normalizado)):
        if body is None:
            continue
        p = folder / f"{name}{ext}"
        with p.open("xb") as fh:
            fh.write(body)
        p.chmod(stat.S_IREAD | stat.S_IRGRP)
    doc = {
        "schema": "nexus.preview.captura/1",
        "fuente": fuente,
        "archivo": name,
        "sha256": digest,
        "sha256_normalizado": _sha(normalizado) if normalizado is not None else None,
        "instante_utc": instante_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fecha_documento": fecha_doc,
        "meta": meta,
    }
    side = folder / f"{name}.json"
    with side.open("x", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1, sort_keys=True)
    side.chmod(stat.S_IREAD | stat.S_IRGRP)
    return {**doc, "reutilizada": False}


def listar(data: Path, fuente: str) -> list[dict[str, Any]]:
    folder = data / "capturas" / fuente
    if not folder.is_dir():
        return []
    return sorted((json.loads(p.read_text("utf-8")) for p in folder.glob("*.json")), key=lambda d: d["instante_utc"])


def _leer(data: Path, fuente: str, doc: dict[str, Any], ext: str) -> bytes:
    p = data / "capturas" / fuente / f"{doc['archivo']}{ext}"
    raw = p.read_bytes()
    want = doc["sha256"] if ext == ".raw" else doc["sha256_normalizado"]
    if _sha(raw) != want:
        raise ConectorError("CAPTURA_ALTERADA", f"{fuente}/{doc['archivo']}{ext}")
    return raw


def habilitadas(conectores: dict[str, Any]) -> set[str]:
    """v0.3.1: fuentes cuyas capturas pueden entrar a una generación NUEVA: conector activo y
    acreditado que capturó, o que falló con la política explícita CONSERVAR_ULTIMO_CORTE.
    INACTIVO (desactivación voluntaria) y NO_ACREDITADO no aportan capturas."""
    return {
        f for f, v in conectores.items()
        if v.get("estado") == "CAPTURADO" or (v.get("estado") == "FALLO" and v.get("politica") == "CONSERVAR_ULTIMO_CORTE")
    }


def cargar_capturas(
    data: Path, month: str, fuentes: set[str] | None = None, frozen_lock: dict[str, Any] | None = None,
    fg_archivo: str | None = None,
    backlog_archivo: str | None = None,
) -> list[tuple[dict[str, Any], PhysicalResult]]:
    """Capturas de Backlog y FG del mes como lecturas equivalentes a los lectores manuales.

    Solo de las ``fuentes`` habilitadas; excepción: la captura que ES el Frozen bloqueado del mes
    se carga siempre (el Frozen es inmutable aunque el conector se desactive). Las demás capturas
    históricas se conservan en disco para auditoría, sin entrar al cálculo.

    v0.3.2 — FG: entra UN solo corte directo: ``fg_archivo`` (la consulta de este Refresh) o, en
    contingencia, el último corte directo del mes. Los cortes anteriores quedan para auditoría.

    v0.3.3 — Backlog: entran solo los cortes que pueden decidir la precedencia: el primero del
    mes (candidato a Frozen), el Frozen bloqueado y el Current (``backlog_archivo``, la consulta de
    este Refresh, o el último corte en contingencia). Los intermedios quedan para auditoría."""
    out = []
    frozen_files = {str((frozen_lock or {}).get(k, {}).get("file", "")) for k in ("oe965", "portal")}
    for fuente, kind, sid in (("backlog_ssrs", "oe965", "oeras_29"), ("fg_odbc", "in092u", "fg_inventory")):
        docs = [d for d in listar(data, fuente) if d["fecha_documento"].startswith(month)]
        if fuente == "fg_odbc":
            docs = [d for d in docs if d["archivo"] == fg_archivo] if fg_archivo else docs[-1:]
        else:
            # FIX6: incluir el primer corte SSRS CONFIRMADO aunque hubo intentos
            # previos al overnight. Un intento anterior nunca se congela por accidente.
            confirmacion = data / "confirmaciones_ssrs" / f"{month}.json"
            aprobado = None
            if confirmacion.is_file():
                try:
                    aprobado = json.loads(confirmacion.read_text("utf-8")).get("archivo")
                except (ValueError, UnicodeError) as exc:
                    raise ConectorError("SSRS_CONFIRMACION_INVALIDA", type(exc).__name__) from exc
            docs = _cortes_backlog(docs, backlog_archivo, frozen_files, aprobado)
        for doc in docs:
            if (fuentes is None or fuente not in fuentes) and f"{fuente}:{doc['archivo']}" not in frozen_files:
                continue
            norm = _leer(data, fuente, doc, ".csv")
            _leer(data, fuente, doc, ".raw")  # integridad del original
            records = list(csv.DictReader(io.StringIO(norm.decode("utf-8"))))
            value = doc["fecha_documento"] if kind == "oe965" else doc["instante_utc"]
            for r in records:
                r["extraction_ts" if kind == "oe965" else "cut_ts"] = value
            res = PhysicalResult(kind=kind, source_id=sid, records=records,
                                 document_time={"value": value, "precision": "date" if kind == "oe965" else "instant",
                                                "source": "consulta directa"})
            out.append(({"file": f"{fuente}:{doc['archivo']}", "kind": kind, "sha256": doc["sha256"],
                         "document_date": doc["fecha_documento"], "instante": doc["instante_utc"],
                         "origen": "CONSULTA_DIRECTA", "records": len(records)}, res))
    return out


# ---------------------------------------------------------------- ejecución de conectores
def capturar_todo(
    data: Path, month: str, tz: str, ahora: Callable[[], dt.datetime], deps: dict[str, Any] | None = None,
    ssrs_automatico: bool = False,
) -> dict[str, Any]:
    deps = deps or {}
    cfg = cargar_config(data)
    zone = zoneinfo.ZoneInfo(tz)
    out: dict[str, Any] = {}
    for fuente in FUENTES:
        c = cfg.get(fuente) or {}
        politica = c.get("politica_fallo", "CONSERVAR_ULTIMO_CORTE")
        if politica not in POLITICAS:
            out[fuente] = {"estado": "FALLO", "codigo": "CONFIG_POLITICA", "politica": "DETENER",
                           "motivo": f"politica_fallo inválida: {politica}"}
            continue
        if not c.get("activo"):
            out[fuente] = {"estado": "INACTIVO", "motivo": "conector no activado"}
            continue
        if fuente != "embarques":
            acr: dict[str, Any] = {}
            if fuente == "backlog_ssrs" and ssrs_automatico:
                # FIX6: validación técnica en ssrs.parse(), SIN conciliar contra ERP manual.
                ok, mot, acr = True, [], {"alcance": "SSRS_CONTRATO_TECNICO", "acreditacion_sha256": None}
                why = ""
            elif fuente == "fg_odbc":
                ok, mot, acr = acreditacion_fg.verificar(data, c, tz)
                why = "; ".join(mot)
            else:
                ok, mot, acr = acreditacion_backlog.verificar(data, c)
                why = "; ".join(mot)
            if not ok:
                out[fuente] = {"estado": "NO_ACREDITADO", "motivo": why, "politica": politica}
                continue
        now = ahora().astimezone(dt.UTC)
        local = now.astimezone(zone)
        try:
            if fuente == "backlog_ssrs":
                out[fuente] = {**_backlog(data, c, month, zone, now, local, deps,
                                                   solo_automatico=ssrs_automatico), **_traza(acr)}
            elif fuente == "fg_odbc":
                raw, meta = fg_odbc.capturar(c, deps.get("proveedor"), deps.get("fabrica_fg"))
                norm = contract_csv("fg_inventory", [{**r, "cut_ts": ""} for r in fg_odbc.registros_contrato(raw)])
                # v0.3.1: fecha de corte = fecha LOCAL (zona del negocio); el instante UTC se conserva
                doc = guardar(data, fuente, raw, norm, {**meta, "huella_config_fg": acreditacion_fg.huella_config(c)},
                              now, local.date().isoformat(), reutilizar=False)
                out[fuente] = {"estado": "CAPTURADO", **_resumen(doc), "instante_consulta_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "alcance": acr.get("alcance"), "acreditacion_sha256": acr.get("acreditacion_sha256")}
            else:
                raw, meta = embarques.capturar(c, month, local.date(), deps.get("proveedor"), deps.get("fabrica_embarques"))
                # FIX5.1: procedencia registrada AL CONSULTAR (sin tocar el conector FIX4)
                meta = {**meta, **acreditacion_embarques.meta_procedencia(c)}
                # FIX5: cada consulta es un corte nuevo con su instante (no se reutiliza la vieja)
                fecha = local.date().isoformat()
                doc = guardar(data, fuente, raw, None, meta, now, fecha, reutilizar=False)
                consulta = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                v2 = acreditacion_embarques.es_v2(data / acreditacion_embarques.ARCHIVO)
                nat = acreditacion_embarques.NATURALEZA if v2 else embarques.NATURALEZA
                out[fuente] = {"estado": "CAPTURADO", "naturaleza": nat,
                               **_resumen(doc), "instante_consulta_utc": consulta}
        except ConectorError as exc:
            out[fuente] = {"estado": "FALLO", "codigo": exc.code, "motivo": exc.detail, "politica": politica}
    return out


def _resumen(doc: dict[str, Any]) -> dict[str, Any]:
    return {"archivo": doc["archivo"], "sha256": doc["sha256"], "instante_utc": doc["instante_utc"],
            "filas": doc["meta"].get("filas", doc["meta"].get("items")), "reutilizada": doc["reutilizada"]}


def _backlog(data: Path, c: dict[str, Any], month: str, zone: zoneinfo.ZoneInfo, now: dt.datetime,
             local: dt.datetime, deps: dict[str, Any], solo_automatico: bool = False) -> dict[str, Any]:
    modo = c.get("modo", "windows_integrado")
    if solo_automatico and modo != "windows_integrado":
        raise ConectorError("SSRS_MODO_NO_AUTOMATICO", "FIX6 exige consulta directa al reporte corporativo")
    if (cuenta := c.get("cuenta_esperada")) and deps.get("usuario", ssrs_usuario()).lower() != cuenta.lower():
        raise ConectorError("SSRS_USUARIO_DISTINTO", f"la sesión Windows no es «{cuenta}»: no se usa otra identidad")
    # FIX6 jamás usa importación interactiva o archivo XML de contingencia como Current.
    imports = [] if solo_automatico else _importar(data, c, month, zone, deps)
    if modo == "importacion_interactiva":
        if not imports:
            raise ConectorError("SSRS_SIN_IMPORTACION", "no hay XML nuevo en la carpeta ssrs-import")
        return {"estado": "CAPTURADO", "modo": modo, **imports[-1]}
    fetch = deps.get("ssrs_fetch") or (lambda url: ssrs.fetch_windows(url, int(c.get("timeout_s", 150))))
    url = ssrs.render_url(c)
    try:
        raw = fetch(url)
    except ConectorError as exc:
        if exc.code == "AUTENTICACION_REQUERIDA" and imports:
            return {"estado": "CAPTURADO", "modo": "importacion_interactiva", "aviso": exc.detail, **imports[-1]}
        raise
    records, meta = ssrs.parse(raw, c, month)
    doc = guardar(data, "backlog_ssrs", raw, contract_csv("oeras_29", records),
                  {**meta, "filas": meta["lineas"], "cuenta": c.get("cuenta_esperada", ""), "url_host": ssrs.urlparse(url).hostname},
                  now, local.date().isoformat(), reutilizar=False)
    # v0.3.3: cada consulta es un corte nuevo (aunque los bytes coincidan) con su instante
    consulta = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"estado": "CAPTURADO", "modo": modo, **_resumen(doc), "instante_consulta_utc": consulta}


def _traza(acr: dict[str, Any]) -> dict[str, Any]:
    return {"alcance": acr.get("alcance"), "acreditacion_sha256": acr.get("acreditacion_sha256")}


def _cortes_backlog(
    docs: list[dict[str, Any]], actual: str | None, frozen: set[str], confirmado: str | None = None
) -> list[dict[str, Any]]:
    """Intento más temprano, primer SSRS confirmado, Frozen sellado y Current; sin repetir."""
    elegidos = docs[:1] + [d for d in docs if f"backlog_ssrs:{d['archivo']}" in frozen
                           or (confirmado is not None and d['archivo'] == confirmado)]
    elegidos += [d for d in docs if d["archivo"] == actual] if actual else docs[-1:]
    unicos: dict[str, dict[str, Any]] = {}
    for d in elegidos:
        unicos.setdefault(d["archivo"], d)
    return list(unicos.values())


def ssrs_usuario() -> str:
    from .credenciales import usuario_windows  # noqa: PLC0415

    return usuario_windows()


def _importar(data: Path, c: dict[str, Any], month: str, zone: zoneinfo.ZoneInfo, deps: dict[str, Any]) -> list[dict[str, Any]]:
    """Modo interactivo: XML exportados por el usuario desde su navegador (sin credenciales)."""
    out = []
    for path, naive in ssrs.importacion_pendiente(data / "ssrs-import"):
        when = naive.replace(tzinfo=zone).astimezone(dt.UTC)
        raw = path.read_bytes()
        records, meta = ssrs.parse(raw, c, month)
        doc = guardar(data, "backlog_ssrs", raw, contract_csv("oeras_29", records),
                      {**meta, "filas": meta["lineas"], "importado_de": path.name, "cuenta": "usuario (navegador)"},
                      when, naive.date().isoformat())
        out.append(_resumen(doc))
    return out


ESQUEMA_EMBARQUES_V1 = "nexus.preview.acreditacion-embarques/1"
CAMPOS_CAPTURA = ("archivo", "sha256", "instante_utc")


def embarques_generacion(
    data: Path,
    month: str,
    aprobadores: frozenset[str],
    habilitado: bool = True,
    conector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estado de Embarques para la generación (FIX5).

    - Captura usada: la de ESTE Refresh (``conector`` CAPTURADO); si el conector falló con
      CONSERVAR_ULTIMO_CORTE, la última del mes marcada ``CONTINGENCIA``; nunca se presenta una
      captura antigua como recién consultada.
    - Periodo: la captura debe cubrir el mes de la generación desde el día 1.
    - Acreditación /2 (regla CFDI emitida = embarque) o /1 (lógica original, compatibilidad).
    Sin acreditación válida o sin captura usable: cantidades no se entregan (KPI en N/D)."""
    acr = data / acreditacion_embarques.ARCHIVO
    v2 = acreditacion_embarques.es_v2(acr)
    cfg = (cargar_config(data).get("embarques") or {}) if v2 else {}
    estado_c = (conector or {}).get("estado")
    caps = [d for d in listar(data, "embarques") if d["fecha_documento"].startswith(month)]
    if estado_c == "CAPTURADO":
        actual = (conector or {}).get("archivo")
        caps = [d for d in caps if d["archivo"] == actual]
        estado = "CONSULTA_DIRECTA"
    else:
        estado = "CONTINGENCIA" if estado_c == "FALLO" else "SIN_CONSULTA"
    last = caps[-1] if caps else None
    if v2:
        ok, motivos, info = acreditacion_embarques.verificar(data, cfg, aprobadores)
        naturaleza = acreditacion_embarques.NATURALEZA
    else:
        ok, motivos = embarques.acreditacion_vigente(acr, aprobadores, data)
        info, naturaleza = {}, embarques.NATURALEZA
    if not habilitado:
        ok, motivos = False, ["conector de embarques inactivo o sin captura habilitada", *motivos]
    out: dict[str, Any] = {
        "acreditado": ok,
        "motivos": motivos,
        "naturaleza": naturaleza,
        "esquema": acreditacion_embarques.ESQUEMA if v2 else ESQUEMA_EMBARQUES_V1,
        "estado": estado if last is not None else "SIN_CAPTURA",
        # v0.3.1: la huella de la acreditación entra en la identidad de la generación
        "acreditacion_sha256": _sha(acr.read_bytes()) if acr.is_file() else None,
        "captura": None if last is None else {k: last[k] for k in CAMPOS_CAPTURA},
    }
    if v2:
        out["regla"] = acreditacion_embarques.REGLA
        out["alcance_ajustes"] = acreditacion_embarques.ALCANCE_AJUSTES
    if not ok:
        return out
    if last is None:
        out.update(acreditado=False, motivos=["acreditada, pero sin captura usable del mes"])
        return out
    periodo = (last.get("meta") or {}).get("periodo") or []
    cubre = len(periodo) == 2 and periodo[0] == f"{month}-01" and str(periodo[1]).startswith(month)
    if v2 and not cubre:
        motivo = f"la captura no cubre el periodo {month} desde el día 1"
        out.update(acreditado=False, motivos=[motivo])
        return out
    huella = (last.get("meta") or {}).get("huella_config_embarques")
    if v2 and huella != acreditacion_embarques.huella_config(cfg):
        motivo = "la captura usada no tiene procedencia corroborable (configuración acreditada)"
        out.update(acreditado=False, motivos=[motivo])
        return out
    raw = _leer(data, "embarques", last, ".raw")
    try:
        lector = acreditacion_embarques.validar_captura if v2 else embarques.leer_captura
        por_item = lector(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        out.update(acreditado=False, motivos=[f"captura de embarques inválida: {exc}"])
        return out
    out["por_item"] = {k: v["facturado_cfdi"] for k, v in por_item.items()}
    if v2:
        out["ausencia_es_cero"] = info["ausencia_es_cero"] is True
        out["periodo"] = list(periodo)
    else:
        acred = json.loads(acr.read_text("utf-8"))
        out["ausencia_es_cero"] = acred.get("ausencia_es_cero") is True
    return out
