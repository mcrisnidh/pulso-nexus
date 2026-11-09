"""v0.3.3 — acreditación VERIFICABLE del conector Backlog SSRS (29 OERAS), equivalente a FG v0.3.2.

``conciliar.py backlog --acreditar`` compara el backlog manual (``R_AAAAMMDD.TXT``, lector OE965)
con una consulta directa ORIGINAL del reporte «Month End - Backlog Detail» (XML guardado por
``preflight.py`` o por el Refresh) por identidad EXACTA (ITEM, clave de cliente, pedido, línea) y
por ITEM. Solo se acredita un resultado sin ninguna diferencia.

Alcance: ``FUENTE_Y_CONTRATO``. La acreditación establece que el reporte SSRS configurado y el
contrato ``oeras_29`` reproducen el backlog manual de UN corte; los cortes posteriores pueden
diferir por cambios comerciales legítimos (se trazan, no se comparan contra el corte inicial).
NO certifica la equivalencia comercial OE965 ↔ 29 OERAS (registro de decisiones V26:
``NOT_ESTABLISHED``), que sigue pendiente.

Cada Refresh vuelve a verificar la acreditación desde cero: informe con su SHA-256; copias de
evidencia; captura ORIGINAL en ``preflight/backlog_ssrs`` o ``capturas/backlog_ssrs`` con el mismo
SHA-256; RECÁLCULO de la conciliación; identidades exactas; huella de la configuración del reporte
(servidor, ruta, parámetros, nombre, periodo, entidad, moneda, cuenta Windows, atributos y formato
de clave de cliente). Cualquier discrepancia → ``NO_ACREDITADO`` (fallo cerrado).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from nexus.core.physical import default_layout, read_oe965

from . import ConectorError, ssrs
from .acreditacion_fg import _dentro, _sha

ESQUEMA = "nexus.preview.acreditacion-conector/2"
FUENTE = "backlog_ssrs"
ARCHIVO = "acreditacion_backlog_ssrs.json"
ORIGENES = ("preflight/backlog_ssrs", "capturas/backlog_ssrs")
ALCANCE = "FUENTE_Y_CONTRATO"
EQUIVALENCIA_COMERCIAL = "NOT_ESTABLISHED"
NOTA_V26 = (
    "Acredita la fuente SSRS y el contrato oeras_29 frente al backlog manual de un corte. "
    "NO certifica la equivalencia comercial OE965 ↔ 29 OERAS (V26: NOT_ESTABLISHED)."
)
_HEX = re.compile(r"[0-9a-f]{64}")
Clave = tuple[str, str, str, str]
CAMPOS = ("qty_open", "qty_ordered", "due_date")


def _dec(v: Any) -> Decimal | None:
    try:
        return None if v in (None, "") else Decimal(str(v))
    except InvalidOperation:
        return None


# ---------------------------------------------------------------- huella de la configuración
def huella_config(cfg: dict[str, Any]) -> str:
    """SHA-256 de lo que DEFINE la fuente (no incluye activo, modo, política ni tiempos)."""
    body = {
        "url_servidor": str(cfg.get("url_servidor", "")).rstrip("/"),
        "ruta_reporte": str(cfg.get("ruta_reporte", "")),
        "parametros": {str(k): str(v) for k, v in sorted((cfg.get("parametros") or {}).items())},
        "nombre_reporte": str(cfg.get("nombre_reporte") or "Month End - Backlog Detail"),
        "atributo_periodo": str(cfg.get("atributo_periodo") or "textbox1"),
        "entidad": str(cfg.get("entidad", "0036")).strip(),
        "moneda": str(cfg.get("moneda", "USD")).strip(),
        "cuenta_esperada": str(cfg.get("cuenta_esperada", "")).lower(),
        "formato_clave_cliente": str(cfg.get("formato_clave_cliente") or "{num:<10}{seq}"),
        "atributos": dict(sorted({**ssrs.DEFAULT_ATTRS, **(cfg.get("atributos") or {})}.items())),
    }
    return _sha(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8"))


# ---------------------------------------------------------------- lectura
def leer_manual(raw: bytes) -> tuple[list[dict[str, str]], dict[str, Any]]:
    res = read_oe965(raw, default_layout())
    if not res.valid:
        raise ConectorError(
            "BACKLOG_MANUAL_INVALIDO", "el extracto manual no es un backlog R válido"
        )
    return list(res.records), dict(res.document_time)


def es_xml(raw: bytes) -> bool:
    return raw.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<")


def leer_directa(raw: bytes, cfg: dict[str, Any], month: str) -> list[dict[str, str]]:
    """XML del reporte (validado con el contrato SSRS) o CSV ``oeras_29`` (solo diagnóstico)."""
    if es_xml(raw):
        return ssrs.parse(raw, cfg, month)[0]
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))


def _indice(filas: list[dict[str, str]], lado: str) -> dict[Clave, dict[str, str]]:
    out: dict[Clave, dict[str, str]] = {}
    for r in filas:
        k = (r["item"], r["customer_key_exact"], r["order"], r["line"])
        if k in out:
            raise ConectorError(
                "BACKLOG_DUPLICADO", f"{lado}: (ITEM, cliente, pedido, línea) repetido"
            )
        out[k] = r
    return out


# ---------------------------------------------------------------- comparación (única lógica)
def comparar(manual: bytes, directa: bytes, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Identidad EXACTA (sin normalizar) por línea y por ITEM, más diagnóstico de discrepancias."""
    cfg = cfg or {}
    filas_m, doc_time = leer_manual(manual)
    month = str(doc_time.get("value", ""))[:7]
    m, d = _indice(filas_m, "manual"), _indice(leer_directa(directa, cfg, month), "directa")
    dif: list[dict[str, str]] = []
    for tipo, keys in (("SOLO_MANUAL", m.keys() - d.keys()), ("SOLO_DIRECTA", d.keys() - m.keys())):
        for k in sorted(keys):
            dif.append({"tipo": tipo, "item": k[0], "cliente": k[1], "pedido": k[2], "linea": k[3]})
    for k in sorted(m.keys() & d.keys()):
        for campo in CAMPOS:
            a, b = m[k][campo], d[k][campo]
            if (_dec(a) != _dec(b)) if campo != "due_date" else (a != b):
                fila = {"tipo": f"DIFERENTE_{campo}", "item": k[0], "cliente": k[1]}
                dif.append({**fila, "pedido": k[2], "linea": k[3], "manual": a, "directa": b})
    por_item: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for lado, src in ((0, m), (1, d)):
        for r in src.values():
            por_item[r["item"]][lado] += _dec(r["qty_open"]) or 0
    items = [
        {"item": i, "manual": str(a), "directa": str(b), "diferencia": str(b - a)}
        for i, (a, b) in sorted(por_item.items())
        if a != b
    ]
    cm, cd = {k[1] for k in m}, {k[1] for k in d}
    resumen: dict[str, Any] = {
        "lineas_manual": len(m),
        "lineas_directa": len(d),
        "diferencias_linea": len(dif),
        "items_con_diferencia": len(items),
        "claves_cliente_solo_manual": len(cm - cd),
        "claves_cliente_solo_directa": len(cd - cm),
        "variacion_neta_abierto": str(sum((Decimal(x["diferencia"]) for x in items), Decimal(0))),
        "fecha_manual": doc_time.get("value"),
    }
    if cm != cd:
        resumen["sugerencia"] = (
            "revise formato_clave_cliente y los parámetros del reporte (sin normalizar claves)"
        )
    identidad = {
        "lineas": sorted(m) == sorted(d),
        "items": {k[0] for k in m} == {k[0] for k in d},
        "clientes": cm == cd,
    }
    return {
        "resumen": resumen,
        "diferencias": dif,
        "por_item": items,
        "identidad_exacta": identidad,
        "diagnostico": diagnosticar(m, d, str(doc_time.get("value", ""))),
    }


def diagnosticar(
    m: dict[Clave, dict[str, str]], d: dict[Clave, dict[str, str]], fecha_corte: str
) -> dict[str, Any]:
    """Clasifica cada línea presente en un solo lado con EVIDENCIA estructural (no conclusiones).

    - ``IDENTIDAD_DISTINTA``: el mismo pedido/línea existe al otro lado con otro ITEM o cliente;
    - ``CLIENTE_AUSENTE``: la clave de cliente no aparece en el otro lado (posible filtro o
      parámetro del reporte, o maestro de clientes distinto);
    - ``PEDIDO_AUSENTE``: el cliente existe pero el pedido no (posible cierre/cancelación entre
      cortes o filtro);
    - ``LINEA_AUSENTE``: el pedido existe con otras líneas (posible cierre o embarque de la línea
      entre cortes).
    La causa definitiva exige cortes manual y SSRS cercanos en el tiempo; nada se convierte en cero
    ni en excepción permanente."""
    lineas = []
    for lado, propio, otro in (("SOLO_MANUAL", m, d), ("SOLO_DIRECTA", d, m)):
        por_pl = {(k[2], k[3]): k for k in otro}
        clientes = {k[1] for k in otro}
        pedidos = {(k[1], k[2]) for k in otro}
        pedidos_sin_cliente = {k[2] for k in otro}
        for k in sorted(propio.keys() - otro.keys()):
            r = propio[k]
            if (k[2], k[3]) in por_pl:
                clase = "IDENTIDAD_DISTINTA"
            elif k[1] not in clientes:
                clase = "CLIENTE_AUSENTE"
            elif (k[1], k[2]) not in pedidos and k[2] not in pedidos_sin_cliente:
                clase = "PEDIDO_AUSENTE"
            else:
                clase = "LINEA_AUSENTE"
            lineas.append(
                {
                    "lado": lado,
                    "clase": clase,
                    "item": k[0],
                    "cliente": k[1],
                    "pedido": k[2],
                    "linea": k[3],
                    "qty_open": r["qty_open"],
                    "qty_ordered": r["qty_ordered"],
                    "due_date": r["due_date"],
                    "vencida_al_corte": bool(fecha_corte) and r["due_date"] < fecha_corte[:10],
                    "sin_embarque_parcial": _dec(r["qty_open"]) == _dec(r["qty_ordered"]),
                }
            )
    conteo = Counter(f"{x['lado']}:{x['clase']}" for x in lineas)
    return {"lineas": lineas, "conteo": dict(sorted(conteo.items()))}


# ---------------------------------------------------------------- procedencia
def origen_de(data: Path, directa: Path) -> str | None:
    """Ruta relativa si ``directa`` es una captura ORIGINAL (XML) de Backlog SSRS en los datos."""
    try:
        rel = directa.resolve().relative_to(data.resolve()).as_posix()
    except ValueError:
        return None
    ok = any(rel.startswith(o + "/") for o in ORIGENES) and _dentro(data, rel) is not None
    return rel if ok and es_xml(directa.read_bytes()) else None


# ---------------------------------------------------------------- verificación en cada Refresh
def _carga(data: Path, ruta: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not ruta.is_file():
        return None, ["sin acreditación Backlog: ejecute «conciliar.py backlog --acreditar»"]
    try:
        doc = json.loads(ruta.read_text("utf-8"))
    except ValueError:
        return None, ["acreditación Backlog ilegible"]
    return (doc, []) if isinstance(doc, dict) else (None, ["acreditación Backlog ilegible"])


def _forma(doc: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    if doc.get("schema") != ESQUEMA:
        return [f"acreditación Backlog con esquema «{doc.get('schema')}»: se exige {ESQUEMA}"]
    if doc.get("fuente") != FUENTE:
        return [f"acreditación de otra fuente («{doc.get('fuente')}»)"]
    errores = []
    if doc.get("resultado") != "PASS" or doc.get("alcance") != ALCANCE:
        errores.append("la acreditación no declara PASS con alcance FUENTE_Y_CONTRATO")
    if doc.get("equivalencia_comercial") != EQUIVALENCIA_COMERCIAL:
        errores.append("la acreditación no puede certificar la equivalencia comercial (V26)")
    for k in ("informe_sha256", "directa_sha256", "manual_sha256", "huella_config_backlog"):
        if not _HEX.fullmatch(str(doc.get(k, ""))):
            errores.append(f"{k} ausente o mal formado")
    if not errores and doc["huella_config_backlog"] != huella_config(cfg):
        errores.append(
            "la configuración del reporte SSRS cambió desde la acreditación "
            "(servidor, ruta, parámetros, entidad, moneda, cuenta o atributos)"
        )
    return errores


def verificar(
    data: Path, cfg: dict[str, Any], ruta: Path | None = None
) -> tuple[bool, list[str], dict[str, Any]]:
    """(válida, motivos, resumen). Fallo cerrado ante cualquier inconsistencia."""
    p = ruta or data / ARCHIVO
    doc, motivos = _carga(data, p)
    if doc is None:
        return False, motivos, {}
    info: dict[str, Any] = {
        "acreditacion_sha256": _sha(p.read_bytes()),
        "alcance": doc.get("alcance"),
    }
    motivos = _forma(doc, cfg)
    if motivos:
        return False, motivos, info
    base = data / "conciliaciones"
    carpeta = _dentro(base, doc.get("carpeta"), archivo=False) if base.is_dir() else None
    if carpeta is None or not str(doc.get("carpeta", "")).startswith("backlog_"):
        return False, ["carpeta de conciliación inexistente o fuera de los datos"], info
    informe_p = _dentro(carpeta, "informe_privado.json")
    if informe_p is None or _sha(informe_p.read_bytes()) != doc["informe_sha256"]:
        return False, ["informe de conciliación ausente o distinto de su SHA-256"], info
    informe = json.loads(informe_p.read_bytes().decode("utf-8"))
    ev = informe.get("evidencia") or {}
    man_p, dir_p = _dentro(carpeta, ev.get("manual")), _dentro(carpeta, ev.get("directa"))
    if man_p is None or dir_p is None:
        return False, ["evidencias de la conciliación ausentes"], info
    man_b, dir_b = man_p.read_bytes(), dir_p.read_bytes()
    for nombre, cuerpo in (("manual", man_b), ("directa", dir_b)):
        if _sha(cuerpo) != doc[f"{nombre}_sha256"] or informe.get(f"{nombre}_sha256") != _sha(
            cuerpo
        ):
            return False, [f"la evidencia {nombre} no coincide con su SHA-256"], info
    rel = str(doc.get("origen_directa", ""))
    origen = _dentro(data, rel)
    if origen is None or not any(rel.startswith(o + "/") for o in ORIGENES):
        return False, ["la captura SSRS original no existe en preflight/ o capturas/"], info
    if _sha(origen.read_bytes()) != doc["directa_sha256"] or not es_xml(dir_b):
        return False, ["la captura SSRS original no coincide con la evidencia (SHA-256)"], info
    try:
        rec = comparar(man_b, dir_b, cfg)
    except ConectorError as exc:
        return False, [f"evidencia inválida al recalcular: {exc.code}"], info
    for campo in ("resumen", "diferencias", "por_item", "identidad_exacta", "diagnostico"):
        if informe.get(campo) != rec[campo]:
            return (
                False,
                [f"el informe no coincide con la conciliación recalculada ({campo})"],
                info,
            )
    if informe.get("fuente") != "backlog" or informe.get("huella_config_backlog") != huella_config(
        cfg
    ):
        return False, ["el informe pertenece a otra fuente o a otra configuración"], info
    if rec["diferencias"] or rec["por_item"] or not all(rec["identidad_exacta"].values()):
        return False, ["la conciliación recalculada tiene DIFERENCIAS: no se acredita"], info
    if doc.get("lineas") != rec["resumen"]["lineas_directa"]:
        return False, ["el número de líneas declarado no coincide con el recálculo"], info
    info.update(lineas=rec["resumen"]["lineas_directa"], carpeta=doc["carpeta"])
    return True, [], info


def documento(
    informe: dict[str, Any], informe_sha: str, carpeta: str, origen: str, stamp: str
) -> dict[str, Any]:
    return {
        "schema": ESQUEMA,
        "fuente": FUENTE,
        "resultado": "PASS",
        "alcance": ALCANCE,
        "equivalencia_comercial": EQUIVALENCIA_COMERCIAL,
        "nota": NOTA_V26,
        "lineas": informe["resumen"]["lineas_directa"],
        "informe_sha256": informe_sha,
        "carpeta": carpeta,
        "directa_sha256": informe["directa_sha256"],
        "manual_sha256": informe["manual_sha256"],
        "origen_directa": origen,
        "huella_config_backlog": informe["huella_config_backlog"],
        "cortes": informe["cortes"],
        "acreditado_utc": stamp,
        "activa_conector": False,
    }


def evidencia_y_cortes(
    data: Path, manual: Path, directa: Path, out: dict[str, Any], instante_directa: str | None
) -> dict[str, Any]:
    """Campos de procedencia del informe (los usa ``conciliar.py``)."""
    return {
        "evidencia": {"manual": "manual_" + _seguro(manual.name), "directa": "directa.xml"},
        "origen_directa": origen_de(data, directa),
        "cortes": cortes(out["resumen"].get("fecha_manual"), instante_directa),
        "equivalencia_comercial": EQUIVALENCIA_COMERCIAL,
        "nota": NOTA_V26,
    }


def cortes(fecha_manual: Any, instante_directa: str | None) -> dict[str, Any]:
    """Desfase entre cortes con la precisión REAL de cada fuente (OE965 solo trae fecha)."""
    out: dict[str, Any] = {
        "fecha_manual": fecha_manual,
        "precision_manual": "fecha (OE965 no trae hora)",
        "instante_directa_utc": instante_directa,
        "simultaneos": False,
        "limitacion": (
            "El backlog manual (OE965) y la consulta SSRS no son simultáneos: las diferencias "
            "pueden reflejar movimientos comerciales entre ambos cortes."
        ),
    }
    try:
        a = dt.date.fromisoformat(str(fecha_manual)[:10])
        b = dt.datetime.fromisoformat(str(instante_directa).replace("Z", "+00:00")).date()
        out["desfase_dias"] = (b - a).days
    except ValueError:
        out["desfase_dias"] = None
    return out


def _seguro(nombre: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in nombre)
