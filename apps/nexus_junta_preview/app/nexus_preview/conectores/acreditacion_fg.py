"""v0.3.2 — acreditación VERIFICABLE del conector FG con ALCANCE declarado.

La conciliación de FG (``conciliar.py fg``) compara el IN092U manual con una consulta directa
STKBAL por identidad EXACTA (ITEM, stockroom). El informe conserva SIEMPRE el resultado global
(p. ej. una diferencia en un stockroom ajeno a FG) y, aparte, el resultado dentro del ALCANCE
declarado (``--alcance FG``). Solo se acredita si el alcance no tiene diferencias.

Cada Refresh vuelve a verificar la acreditación desde cero (nada se cree por declaración):

1. documento ``acreditacion_fg_odbc.json`` con esquema /2, fuente ``fg_odbc`` y alcance no vacío;
2. carpeta de conciliación dentro de ``<datos>/conciliaciones`` (sin rutas absolutas, ``..`` ni
   enlaces) cuyo ``informe_privado.json`` tiene exactamente el SHA-256 declarado;
3. copias de evidencia (IN092U manual y consulta directa) dentro de esa carpeta con los SHA-256
   declarados, y la captura ORIGINAL de la consulta directa presente en ``preflight/fg_odbc`` o
   ``capturas/fg_odbc`` con el mismo SHA-256;
4. la comparación se RECALCULA desde esas evidencias y debe coincidir campo a campo con el
   informe (diferencias globales, por ITEM y del alcance) y con el documento de acreditación;
5. dentro del alcance: cero diferencias, mismos ITEM exactos en ambos lados;
6. la huella de la configuración que define la consulta (DSN, biblioteca, tabla, columnas,
   compañía, filtro, cuenta, atributos ODBC) es la misma que la configuración vigente.

Una acreditación falsificada, alterada, de otra instalación o de otra configuración se rechaza.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from nexus.core.physical import default_layout, read_in092u

from . import ConectorError
from .fg_odbc import DEFAULT, sql

ESQUEMA = "nexus.preview.acreditacion-conector/2"
ARCHIVO = "acreditacion_fg_odbc.json"
ORIGENES = ("preflight/fg_odbc", "capturas/fg_odbc")
LIMITACION = (
    "Los cortes manual (IN092U) y directo (STKBAL) NO son simultáneos: las diferencias pueden "
    "reflejar movimientos ocurridos entre ambos instantes."
)
_STAMP = re.compile(r"^(\d{8}T\d{6}Z)")
_HEX = re.compile(r"[0-9a-f]{64}")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _dec(v: Any) -> Decimal | None:
    try:
        return None if v in (None, "") else Decimal(str(v))
    except InvalidOperation:
        return None


def _dentro(base: Path, rel: Any, *, archivo: bool = True) -> Path | None:
    """Ruta relativa dentro de ``base``: sin absolutas, sin escapar y sin enlaces simbólicos."""
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute() or "\\" in rel or ".." in Path(rel).parts:
        return None
    raw = base / rel
    cur = base
    for part in Path(rel).parts:
        cur = cur / part
        if cur.is_symlink():
            return None
    p = raw.resolve()
    if base.resolve() not in p.parents:
        return None
    return p if (p.is_file() if archivo else p.is_dir()) else None


# ---------------------------------------------------------------- huella de la consulta
def huella_config(cfg: dict[str, Any]) -> str:
    """SHA-256 de lo que DEFINE la consulta FG (no incluye activo, política ni tiempos)."""
    cols = {**DEFAULT["columnas"], **(cfg.get("columnas") or {})}
    body = {
        "dsn": cfg.get("dsn", ""),
        "controlador": cfg.get("controlador", ""),
        "servidor": cfg.get("servidor", ""),
        "biblioteca": cfg.get("biblioteca", DEFAULT["biblioteca"]),
        "tabla": cfg.get("tabla", DEFAULT["tabla"]),
        "columnas": dict(sorted(cols.items())),
        "compania": str(cfg.get("compania", DEFAULT["compania"])),
        "filtro_cantidad": cfg.get("filtro_cantidad", DEFAULT["filtro_cantidad"]),
        "sufijo_sql": cfg.get("sufijo_sql", DEFAULT["sufijo_sql"]),
        "credencial": cfg.get("credencial", ""),
        "cuenta_esperada": str(cfg.get("cuenta_esperada", "remcb")).lower(),
        "atributos_conexion": {str(k): str(v) for k, v in sorted((cfg.get("atributos_conexion") or {}).items())},
        "sql": sql(cfg),
    }
    return _sha(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8"))


# ---------------------------------------------------------------- comparación (única lógica)
def leer_directa(raw: bytes) -> dict[tuple[str, str], str]:
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if not rows or not {"item", "stockroom", "qty_physical"} <= set(rows[0]):
        raise ConectorError("FG_DIRECTA_INVALIDA", "se esperaba el CSV canónico item,stockroom,qty_physical")
    out: dict[tuple[str, str], str] = {}
    for n, r in enumerate(rows, start=2):
        k = (r["item"], r["stockroom"])
        if k in out:
            raise ConectorError("FG_DUPLICADO", f"(ITEM, stockroom) repetido en la línea {n}")
        out[k] = r["qty_physical"]
    return out


def leer_manual(raw: bytes, tz: str) -> tuple[dict[tuple[str, str], str], str | None]:
    res = read_in092u(raw, default_layout(), tz)
    if not res.valid:
        raise ConectorError("FG_MANUAL_INVALIDO", "el extracto manual no es un IN092U válido")
    m: dict[tuple[str, str], str] = {}
    for r in res.records:
        m[(r["item"], r["stockroom"])] = r["qty_physical"]
    return m, res.document_time.get("value")


def comparar(manual: bytes, directa: bytes, tz: str, alcance: list[str] | None = None) -> dict[str, Any]:
    """Comparación por identidad exacta. ``alcance`` None = global (todos los stockrooms)."""
    m, instante_manual = leer_manual(manual, tz)
    d = leer_directa(directa)
    dif: list[dict[str, str]] = []
    for k in sorted(m.keys() | d.keys()):
        a, b = m.get(k), d.get(k)
        if a is None or b is None or _dec(a) != _dec(b) or (a == "") != (b == ""):
            dif.append({"tipo": "SOLO_DIRECTA" if a is None else "SOLO_MANUAL" if b is None else "CANTIDAD_DIFERENTE",
                        "item": k[0], "stockroom": k[1], "manual": a or "", "directa": b or ""})

    def totales(rooms: set[str]) -> list[dict[str, str]]:
        acc: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
        for side, src in ((0, m), (1, d)):
            for (i, room), q in src.items():
                if room in rooms:
                    acc[i][side] += _dec(q) or 0
        return [{"item": i, "manual": str(a), "directa": str(b), "diferencia": str(b - a)}
                for i, (a, b) in sorted(acc.items()) if a != b]

    por_item = [{"item": x["item"], "manual_fg": x["manual"], "directa_fg": x["directa"], "diferencia": x["diferencia"]}
                for x in totales({"FG"})]
    todos = sorted({k[1] for k in m.keys() | d.keys()})
    rooms = sorted(set(alcance)) if alcance else todos
    dentro = [x for x in dif if x["stockroom"] in rooms]
    items_m = sorted({k[0] for k in m if k[1] in rooms})
    items_d = sorted({k[0] for k in d if k[1] in rooms})
    item_dif = totales(set(rooms))
    alc = {
        "stockrooms": rooms,
        "declarado": bool(alcance),
        "resultado": "PASS" if not dentro and not item_dif and items_m == items_d and items_m else "DIFERENCIAS",
        "pares_manual": sum(1 for k in m if k[1] in rooms),
        "pares_directa": sum(1 for k in d if k[1] in rooms),
        "items_manual": len(items_m),
        "items_directa": len(items_d),
        "identidad_item_exacta": items_m == items_d,
        "diferencias": dentro,
        "items_con_diferencia": item_dif,
        "diferencias_fuera_de_alcance": len(dif) - len(dentro),
        "stockrooms_fuera_de_alcance": sorted({x["stockroom"] for x in dif} - set(rooms)),
    }
    return {
        "resumen": {"pares_manual": len(m), "pares_directa": len(d), "diferencias": len(dif),
                    "items_fg_con_diferencia": len(por_item), "instante_manual": instante_manual},
        "diferencias": dif,
        "por_item": por_item,
        "alcance": alc,
    }


# ---------------------------------------------------------------- cortes no simultáneos
def instante_de_archivo(p: Path) -> str | None:
    """Instante UTC de una captura (sidecar .json de preflight/captura o sello del nombre)."""
    side = p.with_suffix(".json")
    if side.is_file():
        try:
            doc = json.loads(side.read_text("utf-8"))
            if doc.get("instante_utc"):
                return str(doc["instante_utc"])
        except ValueError:
            pass
    mt = _STAMP.match(p.name)
    if mt:
        return dt.datetime.strptime(mt.group(1), "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def cortes(instante_manual: str | None, instante_directa: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {"instante_manual_utc": instante_manual, "instante_directa_utc": instante_directa,
                           "simultaneos": False, "limitacion": LIMITACION}
    try:
        a = dt.datetime.fromisoformat(str(instante_manual).replace("Z", "+00:00"))
        b = dt.datetime.fromisoformat(str(instante_directa).replace("Z", "+00:00"))
        out["desfase_minutos"] = round(abs((b - a).total_seconds()) / 60)
        out["directa_posterior"] = b >= a
    except ValueError:
        out["desfase_minutos"] = None
    return out


def origen_de(data: Path, directa: Path) -> str | None:
    """Ruta relativa (dentro de los datos) si ``directa`` es una captura original de FG."""
    try:
        rel = directa.resolve().relative_to(data.resolve()).as_posix()
    except ValueError:
        return None
    return rel if any(rel.startswith(o + "/") for o in ORIGENES) and _dentro(data, rel) else None


# ---------------------------------------------------------------- verificación en cada Refresh
def verificar(data: Path, cfg: dict[str, Any], tz: str, ruta: Path | None = None) -> tuple[bool, list[str], dict[str, Any]]:
    """(válida, motivos, resumen). Fallo cerrado ante cualquier inconsistencia."""
    p = ruta or data / ARCHIVO
    if not p.is_file():
        return False, ["sin acreditación FG: ejecute «conciliar.py fg --alcance FG --acreditar»"], {}
    try:
        doc = json.loads(p.read_text("utf-8"))
    except ValueError:
        return False, ["acreditación FG ilegible"], {}
    if not isinstance(doc, dict):
        return False, ["acreditación FG ilegible"], {}
    info = {"acreditacion_sha256": _sha(p.read_bytes()), "alcance": doc.get("alcance", {}).get("stockrooms")
            if isinstance(doc.get("alcance"), dict) else None}
    motivos: list[str] = []
    if doc.get("schema") != ESQUEMA:
        return False, [f"acreditación FG con esquema «{doc.get('schema')}»: se exige {ESQUEMA} (vuelva a conciliar con v0.3.2)"], info
    if doc.get("fuente") != "fg_odbc":
        return False, [f"acreditación de otra fuente («{doc.get('fuente')}»)"], info
    alc = doc.get("alcance")
    if not isinstance(alc, dict) or not isinstance(alc.get("stockrooms"), list) or not alc["stockrooms"] \
            or not all(isinstance(s, str) and s for s in alc["stockrooms"]):
        return False, ["acreditación FG sin alcance declarado"], info
    if doc.get("resultado") != "PASS" or alc.get("resultado") != "PASS":
        motivos.append("la acreditación no declara PASS en su alcance")
    for k in ("informe_sha256", "directa_sha256", "manual_sha256", "huella_config_fg"):
        if not _HEX.fullmatch(str(doc.get(k, ""))):
            motivos.append(f"{k} ausente o mal formado")
    if motivos:
        return False, motivos, info
    if doc["huella_config_fg"] != huella_config(cfg):
        return False, ["la configuración de la consulta FG cambió desde la acreditación (DSN, filtro, columnas, cuenta o atributos)"], info
    carpeta = _dentro(data / "conciliaciones", doc.get("carpeta"), archivo=False) if (data / "conciliaciones").is_dir() else None
    if carpeta is None or not str(doc.get("carpeta", "")).startswith("fg_"):
        return False, ["carpeta de conciliación inexistente o fuera de los datos"], info
    informe_p = _dentro(carpeta, "informe_privado.json")
    if informe_p is None:
        return False, ["informe de conciliación ausente"], info
    informe_b = informe_p.read_bytes()
    if _sha(informe_b) != doc["informe_sha256"]:
        return False, ["el informe de conciliación no coincide con su SHA-256 (alterado o ajeno)"], info
    informe = json.loads(informe_b.decode("utf-8"))
    ev = informe.get("evidencia") or {}
    man_p, dir_p = _dentro(carpeta, ev.get("manual")), _dentro(carpeta, ev.get("directa"))
    if man_p is None or dir_p is None:
        return False, ["evidencias de la conciliación ausentes"], info
    man_b, dir_b = man_p.read_bytes(), dir_p.read_bytes()
    if _sha(man_b) != doc["manual_sha256"] or informe.get("manual_sha256") != doc["manual_sha256"]:
        return False, ["el IN092U manual de la evidencia no coincide con su SHA-256"], info
    if _sha(dir_b) != doc["directa_sha256"] or informe.get("directa_sha256") != doc["directa_sha256"]:
        return False, ["la consulta directa de la evidencia no coincide con su SHA-256"], info
    origen = _dentro(data, doc.get("origen_directa"))
    if origen is None or not any(str(doc["origen_directa"]).startswith(o + "/") for o in ORIGENES) \
            or _sha(origen.read_bytes()) != doc["directa_sha256"]:
        return False, ["la captura original de la consulta directa no existe en preflight/ o capturas/ con ese SHA-256"], info
    try:
        rec = comparar(man_b, dir_b, tz, alc["stockrooms"])
    except ConectorError as exc:
        return False, [f"evidencia inválida: {exc.code}"], info
    for campo in ("diferencias", "por_item", "alcance"):
        if informe.get(campo) != rec[campo]:
            return False, [f"el informe no coincide con la comparación recalculada ({campo})"], info
    if {k: v for k, v in informe.get("resumen", {}).items()} != rec["resumen"]:
        return False, ["el informe no coincide con la comparación recalculada (resumen)"], info
    if informe.get("fuente") != "fg" or informe.get("huella_config_fg") != doc["huella_config_fg"]:
        return False, ["el informe pertenece a otra fuente o a otra configuración"], info
    ra = rec["alcance"]
    if ra["resultado"] != "PASS" or not ra["identidad_item_exacta"]:
        return False, [f"el alcance {ra['stockrooms']} tiene diferencias al recalcular: no se acredita"], info
    if sorted(alc["stockrooms"]) != ra["stockrooms"] or alc.get("diferencias_fuera_de_alcance") != ra["diferencias_fuera_de_alcance"] \
            or alc.get("items") != ra["items_directa"]:
        return False, ["el alcance declarado no coincide con el informe"], info
    global_res = "PASS" if not rec["diferencias"] and not rec["por_item"] else "DIFERENCIAS"
    if doc.get("resultado_global") != global_res or informe.get("resultado") != global_res:
        return False, ["el resultado global declarado no coincide (las diferencias fuera de alcance deben conservarse)"], info
    info.update({"alcance": ra["stockrooms"], "items": ra["items_directa"], "fuera_de_alcance": ra["diferencias_fuera_de_alcance"],
                 "resultado_global": global_res, "carpeta": doc["carpeta"], "cortes": informe.get("cortes")})
    return True, [], info


def documento(informe: dict[str, Any], informe_sha: str, carpeta: str, origen: str, stamp: str) -> dict[str, Any]:
    alc = informe["alcance"]
    return {
        "schema": ESQUEMA,
        "fuente": "fg_odbc",
        "resultado": "PASS",
        "alcance": {"stockrooms": alc["stockrooms"], "resultado": alc["resultado"], "items": alc["items_directa"],
                    "diferencias_fuera_de_alcance": alc["diferencias_fuera_de_alcance"],
                    "stockrooms_fuera_de_alcance": alc["stockrooms_fuera_de_alcance"]},
        "resultado_global": informe["resultado"],
        "informe_sha256": informe_sha,
        "carpeta": carpeta,
        "directa_sha256": informe["directa_sha256"],
        "manual_sha256": informe["manual_sha256"],
        "origen_directa": origen,
        "huella_config_fg": informe["huella_config_fg"],
        "cortes": informe["cortes"],
        "acreditado_utc": stamp,
        "activa_conector": False,
        "nota": "La acreditación NO activa el conector: «activo» se cambia a mano en conectores.json con autorización expresa.",
    }
