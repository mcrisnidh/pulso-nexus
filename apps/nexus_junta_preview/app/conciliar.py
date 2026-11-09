"""PULSO NEXUS v0.3.2 — conciliador de extractos manuales contra consultas directas.

  conciliar.py backlog   --data DIR --manual R_20261108.TXT     --directa <captura .csv> [--acreditar]
  conciliar.py fg        --data DIR --manual INV_STA291.TXT     --directa <preflight/fg_odbc/*.csv> [--alcance FG] [--acreditar]
  conciliar.py embarques --data DIR --directa <captura .csv>    --evidencia <item,cantidad .csv>

Compara por IDENTIDAD EXACTA (sin normalizar ITEM ni cliente) y por ITEM, no solo totales. Escribe
en ``DIR/conciliaciones/<fuente>_<instante>/`` un informe privado (diferencias con identidades) y un
resumen saneado (conteos). ``--acreditar`` solo emite ``acreditacion_<fuente>.json`` si el
resultado es PASS (Backlog/FG). Embarques NUNCA se acredita aquí: requiere evidencia y aprobación
del negocio (``acreditacion_embarques.json``).
v0.3.2 (FG): el informe conserva SIEMPRE el resultado global (p. ej. una diferencia en TH) y,
aparte, el resultado del ALCANCE declarado (``--alcance FG``). ``--acreditar`` emite una acreditación
VERIFICABLE (esquema /2) si el alcance es PASS: copia las evidencias a la carpeta de conciliación,
exige que la consulta directa sea una captura original de ``preflight/fg_odbc`` o
``capturas/fg_odbc`` y registra el desfase entre cortes (no son simultáneos). Nunca activa el
conector.
Salida: 0 PASS (del alcance declarado; global si no se declara) · 1 diferencias · 2 error de entrada.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import secrets
import shutil
import stat
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from nexus_preview.conectores import ConectorError  # noqa: E402
from nexus_preview.conectores import acreditacion_backlog as ab  # noqa: E402
from nexus_preview.conectores import acreditacion_fg as afg  # noqa: E402
from nexus_preview.consola import utf8_consola  # noqa: E402


def dec(v: Any) -> Decimal | None:
    try:
        return None if v in (None, "") else Decimal(str(v))
    except InvalidOperation:
        return None


def leer_csv(p: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(p.read_bytes().decode("utf-8-sig"))))


def backlog(manual: Path, directa: Path, cfg_ssrs: dict[str, Any] | None = None) -> dict[str, Any]:
    """v0.3.3: misma lógica que la verificación del Refresh (``acreditacion_backlog.comparar``)."""
    try:
        return ab.comparar(manual.read_bytes(), directa.read_bytes(), cfg_ssrs or {})
    except ConectorError as exc:
        if exc.code == "BACKLOG_MANUAL_INVALIDO":
            raise SystemExit(f"el extracto manual {manual.name} no es un backlog R válido") from exc
        raise


def fg(manual: Path, directa: Path, tz: str, alcance: list[str] | None = None) -> dict[str, Any]:
    """Misma lógica que la verificación del Refresh (``acreditacion_fg.comparar``)."""
    try:
        return afg.comparar(manual.read_bytes(), directa.read_bytes(), tz, alcance)
    except ConectorError as exc:
        raise SystemExit(f"{manual.name}: {exc.detail}" if exc.code == "FG_MANUAL_INVALIDO" else f"{exc.code}: {exc.detail}") from exc


def embarques(directa: Path, evidencia: Path) -> dict[str, Any]:
    d = {r["item"]: r for r in leer_csv(directa)}
    e: dict[str, Decimal] = {}
    for r in leer_csv(evidencia):
        item = r.get("item") or r.get("ITEM") or ""
        q = dec(r.get("cantidad") or r.get("qty") or "")
        if item and q is not None:
            e[item] = e.get(item, Decimal(0)) + q
    dif = []
    for i in sorted(d.keys() | e.keys()):
        a, b = e.get(i), dec((d.get(i) or {}).get("facturado_cfdi"))
        if a is None or b is None or a != b:
            dif.append({"item": i, "evidencia_embarque": "" if a is None else str(a), "facturado_cfdi": "" if b is None else str(b)})
    return {"resumen": {"items_evidencia": len(e), "items_cfdi": len(d), "items_con_diferencia": len(dif),
                        "nota": "facturación CFDI frente a embarques reconocidos por el negocio"},
            "diferencias": dif, "por_item": []}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="conciliar.py")
    ap.add_argument("fuente", choices=("backlog", "fg", "embarques"))
    ap.add_argument("--data", required=True)
    ap.add_argument("--manual")
    ap.add_argument("--directa", required=True)
    ap.add_argument("--evidencia")
    ap.add_argument("--tz", default="America/Mexico_City")
    ap.add_argument("--alcance", default="", help="FG: stockrooms acreditados, separados por coma (p. ej. FG)")
    ap.add_argument("--acreditar", action="store_true")
    utf8_consola()
    a = ap.parse_args(argv)
    alcance = [x.strip() for x in a.alcance.split(",") if x.strip()] or None
    if alcance and a.fuente != "fg":
        print("--alcance solo aplica a fg", file=sys.stderr)
        return 2
    data, directa = Path(a.data).resolve(), Path(a.directa).resolve()
    try:
        if a.fuente == "backlog":
            from nexus_preview.conectores import orquestador as orq  # noqa: PLC0415

            try:
                out = backlog(Path(a.manual), directa, orq.cargar_config(data).get("backlog_ssrs"))
            except ConectorError as exc:
                print(f"XML SSRS inválido: {exc}", file=sys.stderr)
                return 2
        elif a.fuente == "fg":
            out = fg(Path(a.manual), directa, a.tz, alcance)
        else:
            out = embarques(directa, Path(a.evidencia))
    except (OSError, KeyError, TypeError) as exc:
        print(f"entrada inválida: {exc}", file=sys.stderr)
        return 2
    resultado = "PASS" if not out["diferencias"] and not out["por_item"] else "DIFERENCIAS"
    origen = afg.origen_de(data, directa) if a.fuente == "fg" else None
    if a.fuente == "backlog":
        origen = ab.origen_de(data, directa)
        if a.acreditar and (origen is None or _cfg_fg(data, "backlog_ssrs") is None):
            print(MSG_ORIGEN_BACKLOG, file=sys.stderr)
            return 2
    if a.fuente == "fg" and a.acreditar and origen is None:
        print("No se acredita: --directa debe ser la captura ORIGINAL en <datos>/preflight/fg_odbc o capturas/fg_odbc.", file=sys.stderr)
        return 2
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = data / "conciliaciones" / f"{a.fuente}_{stamp}_{secrets.token_hex(3)}"
    folder.mkdir(parents=True, exist_ok=False)
    huellas = {"directa_sha256": hashlib.sha256(directa.read_bytes()).hexdigest()}
    for k in ("manual", "evidencia"):
        if getattr(a, k):
            huellas[f"{k}_sha256"] = hashlib.sha256(Path(getattr(a, k)).read_bytes()).hexdigest()
    if a.fuente == "fg":
        # v0.3.2: evidencias inmutables (privadas) para que cada Refresh recalcule la conciliación
        man_name = "manual_" + "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in Path(a.manual).name)
        for src, name in ((Path(a.manual), man_name), (directa, "directa.csv")):
            shutil.copyfile(src, folder / name)
            (folder / name).chmod(stat.S_IREAD | stat.S_IRGRP)
        cfg_fg = _cfg_fg(data)
        out = {**out, "evidencia": {"manual": man_name, "directa": "directa.csv"}, "origen_directa": origen,
               "cortes": afg.cortes(out["resumen"].get("instante_manual"), afg.instante_de_archivo(directa)),
               "huella_config_fg": afg.huella_config(cfg_fg) if cfg_fg is not None else None}
    if a.fuente == "backlog":
        out = _evidencia_backlog(data, Path(a.manual), directa, folder, out)
    privado = {"AVISO": "PRIVADO: identidades reales", "fuente": a.fuente, "resultado": resultado, **huellas, **out}
    body = json.dumps(privado, ensure_ascii=False, indent=1).encode("utf-8")
    (folder / "informe_privado.json").write_bytes(body)
    for nombre, filas in (("diferencias.csv", out["diferencias"]), ("diferencias_por_item.csv", out["por_item"])):
        if filas:
            buf = io.StringIO(newline="")
            w = csv.DictWriter(buf, fieldnames=list(filas[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(filas)
            (folder / nombre).write_bytes(("﻿" + buf.getvalue()).encode("utf-8"))
    saneado = {"fuente": a.fuente, "resultado": resultado, "resumen": {k: v for k, v in out["resumen"].items() if k != "sugerencia"},
               "informe_sha256": hashlib.sha256(body).hexdigest()}
    if a.fuente == "fg":
        alc = out["alcance"]
        saneado["alcance"] = {k: alc[k] for k in ("stockrooms", "declarado", "resultado", "pares_manual", "pares_directa",
                                                  "items_manual", "items_directa", "identidad_item_exacta",
                                                  "diferencias_fuera_de_alcance", "stockrooms_fuera_de_alcance")}
        saneado["alcance"]["diferencias"] = len(alc["diferencias"])
        saneado["cortes"] = out["cortes"]
    if a.fuente == "backlog":
        saneado.update(_saneado_backlog(out))
    (folder / "resumen_saneado.json").write_text(json.dumps(saneado, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({**saneado, "carpeta": str(folder), "sugerencia": out["resumen"].get("sugerencia")}, ensure_ascii=False, indent=1))
    if a.acreditar:
        if a.fuente == "embarques":
            print("Embarques no se acredita con este comando: requiere evidencia y aprobación del negocio.", file=sys.stderr)
        elif a.fuente == "fg":
            if out.get("huella_config_fg") is None:
                print("No se acredita: conectores.json no tiene la sección fg_odbc (la huella de la consulta es obligatoria).", file=sys.stderr)
            elif out["alcance"]["resultado"] == "PASS":
                acred = afg.documento(privado, saneado["informe_sha256"], folder.name, origen, stamp)
                (data / afg.ARCHIVO).write_text(json.dumps(acred, ensure_ascii=False, indent=1), "utf-8")
                print(f"{afg.ARCHIVO} emitida: alcance {out['alcance']['stockrooms']} PASS · resultado global {resultado}"
                      f" ({out['alcance']['diferencias_fuera_de_alcance']} diferencia(s) fuera de alcance conservadas en el informe)."
                      " El conector NO se activa.")
            else:
                print(f"No se acredita: el alcance {out['alcance']['stockrooms']} tiene diferencias (ver la carpeta de conciliación).", file=sys.stderr)
        elif resultado == "PASS" and all(out["identidad_exacta"].values()):
            sha_inf = saneado["informe_sha256"]
            acred = ab.documento(privado, sha_inf, folder.name, str(origen), stamp)
            texto = json.dumps(acred, ensure_ascii=False, indent=1)
            (data / ab.ARCHIVO).write_text(texto, "utf-8")
            print(f"{ab.ARCHIVO} emitida (PASS, alcance {ab.ALCANCE}). El conector NO se activa.")
            print(ab.NOTA_V26)
        else:
            print("No se acredita: hay diferencias (ver la carpeta de conciliación).", file=sys.stderr)
    if a.fuente == "fg" and alcance:
        return 0 if out["alcance"]["resultado"] == "PASS" else 1
    return 0 if resultado == "PASS" else 1


def _cfg_fg(data: Path, fuente: str = "fg_odbc") -> dict[str, Any] | None:
    from nexus_preview.conectores import orquestador as orq  # noqa: PLC0415

    try:
        c = orq.cargar_config(data).get(fuente)
    except ConectorError:
        return None
    return c if isinstance(c, dict) else None


MSG_ORIGEN_BACKLOG = (
    "No se acredita: --directa debe ser el XML ORIGINAL de <datos>/preflight/backlog_ssrs "
    "o capturas/backlog_ssrs, y conectores.json debe tener la sección backlog_ssrs."
)


def _evidencia_backlog(
    data: Path, manual: Path, directa: Path, folder: Path, out: dict[str, Any]
) -> dict[str, Any]:
    """v0.3.3: evidencias inmutables y procedencia, para recalcular en cada Refresh."""
    extra = ab.evidencia_y_cortes(data, manual, directa, out, afg.instante_de_archivo(directa))
    if extra["origen_directa"] is not None:
        for src, name in ((manual, extra["evidencia"]["manual"]), (directa, "directa.xml")):
            shutil.copyfile(src, folder / name)
            (folder / name).chmod(stat.S_IREAD | stat.S_IRGRP)
    else:
        extra["evidencia"] = None
    cfg = _cfg_fg(data, "backlog_ssrs")
    extra["huella_config_backlog"] = ab.huella_config(cfg) if cfg is not None else None
    return {**out, **extra}


def _saneado_backlog(out: dict[str, Any]) -> dict[str, Any]:
    """Resumen sin identidades: conteos del diagnóstico, identidad y cortes."""
    return {
        "identidad_exacta": out["identidad_exacta"],
        "diagnostico": out["diagnostico"]["conteo"],
        "cortes": out["cortes"],
        "equivalencia_comercial": out["equivalencia_comercial"],
    }


if __name__ == "__main__":
    sys.exit(main())
