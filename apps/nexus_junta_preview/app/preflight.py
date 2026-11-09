"""PULSO NEXUS v0.3 — preflight corporativo de SOLO LECTURA, un conector a la vez.

  preflight.py --data DIR --conector backlog_ssrs|fg_odbc|embarques --nivel entorno
  preflight.py --data DIR --conector fg_odbc --nivel conexion --autorizo
  preflight.py --data DIR --conector fg_odbc --nivel consulta --autorizo

- ``entorno`` (sin red): configuración, credencial presente y de la cuenta correcta (sin mostrar el
  secreto), usuario Windows, arquitectura de Python, controladores y DSN ODBC visibles, PowerShell.
- ``conexion`` (requiere --autorizo): abre la conexión de SOLO LECTURA y consulta su identidad.
- ``consulta`` (requiere --autorizo): ejecuta la consulta del conector y guarda el resultado en
  ``DIR/preflight/<conector>/`` (NO en las capturas que usa el Refresh) para conciliarlo.
Salida: 0 PASS · 1 FAIL · 2 uso no autorizado o inválido. Nunca imprime secretos.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import platform
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from nexus.core.stage import contract_csv  # noqa: E402

from nexus_preview.conectores import ConectorError, acreditacion_fg, embarques, fg_odbc, odbc, ssrs  # noqa: E402
from nexus_preview.consola import utf8_consola  # noqa: E402
from nexus_preview.conectores import credenciales as credmod  # noqa: E402
from nexus_preview.conectores import orquestador as orq  # noqa: E402


def entorno(cfg_all: dict[str, Any], fuente: str, checks: list[dict[str, Any]], deps: dict[str, Any]) -> None:
    c = cfg_all.get(fuente) or {}

    def chk(name: str, ok: bool, detail: Any = "") -> None:
        checks.append({"control": name, "ok": bool(ok), "detalle": detail})

    chk("configuración del conector presente", bool(c), f"conectores.json → {fuente}")
    chk("Python", True, f"{platform.python_version()} · {struct.calcsize('P') * 8} bits · {platform.system()}")
    if fuente == "backlog_ssrs":
        user = deps.get("usuario", credmod.usuario_windows())
        chk("sesión Windows de la cuenta esperada", user.lower() == str(c.get("cuenta_esperada", "ucbaa")).lower(), user)
        try:
            chk("URL del reporte configurada", True, ssrs.urlparse(ssrs.render_url(c)).hostname)
        except ConectorError as exc:
            chk("URL del reporte configurada", c.get("modo") == "importacion_interactiva", exc.code)
        chk("PowerShell disponible", bool(deps.get("powershell", shutil.which("powershell.exe"))), "")
        return
    try:
        cred = credmod.obtener(c.get("credencial", ""), c.get("cuenta_esperada", ""), deps.get("proveedor"))
        chk("credencial en el Administrador de credenciales", True, f"{c.get('credencial')} → cuenta {cred.usuario} (secreto no mostrado)")
    except ConectorError as exc:
        chk("credencial en el Administrador de credenciales", False, f"{exc.code}: {exc.detail}")
    try:
        import pyodbc  # noqa: PLC0415

        drivers, dsns = list(pyodbc.drivers()), dict(pyodbc.dataSources())
        chk("pyodbc y controladores", True, drivers)
        if c.get("dsn"):
            chk(f"DSN «{c['dsn']}» visible para este Python ({struct.calcsize('P') * 8} bits)", c["dsn"] in dsns,
                dsns.get(c["dsn"], "no visible: cree un DSN de sistema de la misma arquitectura que Python"))
        if c.get("controlador"):
            chk(f"controlador «{c['controlador']}»", c["controlador"] in drivers, "")
    except ImportError as exc:
        chk("pyodbc y controladores", False, str(exc)[:120])
    try:
        odbc.cadena(c, None)
        chk("cadena de conexión sin secretos en configuración", True, "")
    except ConectorError as exc:
        chk("cadena de conexión sin secretos en configuración", False, exc.code)


def main(argv: list[str] | None = None, deps: dict[str, Any] | None = None) -> int:
    deps = deps or {}
    ap = argparse.ArgumentParser(prog="preflight.py")
    ap.add_argument("--data", required=True)
    ap.add_argument("--conector", required=True, choices=orq.FUENTES)
    ap.add_argument("--nivel", default="entorno", choices=("entorno", "conexion", "consulta"))
    ap.add_argument("--mes", default=None)
    ap.add_argument("--autorizo", action="store_true", help="autorización explícita para conectar a sistemas corporativos")
    utf8_consola()
    a = ap.parse_args(argv)
    data = Path(a.data).resolve()
    if a.nivel != "entorno" and not a.autorizo:
        print("El nivel «%s» conecta a sistemas corporativos: requiere --autorizo." % a.nivel, file=sys.stderr)
        return 2
    report: dict[str, Any] = {"schema": "nexus.preview.preflight/1", "conector": a.conector, "nivel": a.nivel,
                              "instante_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "controles": []}
    checks = report["controles"]
    try:
        cfg_all = orq.cargar_config(data)
    except ConectorError as exc:
        checks.append({"control": "conectores.json", "ok": False, "detalle": f"{exc.code}: {exc.detail}"})
        cfg_all = {}
    entorno(cfg_all, a.conector, checks, deps)
    c = cfg_all.get(a.conector) or {}
    month = a.mes or json.loads((data / "preview.json").read_text("utf-8"))["month"] if (data / "preview.json").is_file() else a.mes
    try:
        if a.nivel == "conexion" and a.conector != "backlog_ssrs":
            cred = credmod.obtener(c.get("credencial", ""), c.get("cuenta_esperada", ""), deps.get("proveedor"))
            ident = c.get("consulta_identidad") or ("SELECT DB_NAME(), @@SERVERNAME" if a.conector == "embarques" else "SELECT 1 FROM SYSIBM.SYSDUMMY1")
            rows, _ = odbc.consultar(c, cred, ident, [], deps.get("fabrica"))
            checks.append({"control": "conexión de solo lectura e identidad", "ok": True, "detalle": [str(x) for x in (rows[0] if rows else ())]})
        elif a.nivel in ("conexion", "consulta"):
            out = data / "preflight" / a.conector
            out.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
            if a.conector == "backlog_ssrs":
                raw = (deps.get("ssrs_fetch") or (lambda u: ssrs.fetch_windows(u, int(c.get("timeout_s", 150)))))(ssrs.render_url(c))
                records, meta = ssrs.parse(raw, c, month)
                norm = contract_csv("oeras_29", records)
            elif a.conector == "fg_odbc":
                raw, meta = fg_odbc.capturar(c, deps.get("proveedor"), deps.get("fabrica"))
                meta = {**meta, "huella_config_fg": acreditacion_fg.huella_config(c)}
                norm = raw
            else:
                local = dt.datetime.now(dt.UTC).date()
                raw, meta = embarques.capturar(c, month, local if local.strftime("%Y-%m") == month else
                                               (dt.date.fromisoformat(month + "-01") + dt.timedelta(days=31)).replace(day=1) - dt.timedelta(days=1),
                                               deps.get("proveedor"), deps.get("fabrica"))
                norm = raw
            (out / f"{stamp}.raw").write_bytes(raw)
            (out / f"{stamp}.csv").write_bytes(norm)
            # v0.3.2: procedencia del corte (instante, huella de la consulta) para la conciliación
            (out / f"{stamp}.json").write_text(json.dumps({
                "schema": "nexus.preview.preflight-captura/1", "conector": a.conector, "instante_utc": report["instante_utc"],
                "sha256": hashlib.sha256(raw).hexdigest(), "meta": {k: v for k, v in meta.items() if k != "consulta"},
            }, ensure_ascii=False, indent=1, default=str), "utf-8")
            checks.append({"control": "consulta ejecutada (solo lectura)", "ok": True,
                           "detalle": {**{k: v for k, v in meta.items() if k != "consulta"}, "sha256": hashlib.sha256(raw).hexdigest(),
                                       "archivo_para_conciliar": str(out / f"{stamp}.csv")}})
    except ConectorError as exc:
        checks.append({"control": f"nivel {a.nivel}", "ok": False, "detalle": f"{exc.code}: {exc.detail}"})
    if a.conector == "fg_odbc" and c:
        try:  # informativo: no altera el resultado del preflight
            ok_acr, mot, inf = acreditacion_fg.verificar(data, c, str(c.get("zona", "America/Mexico_City")))
            report["acreditacion_fg"] = {"valida": ok_acr, "motivos": mot, "alcance": inf.get("alcance"),
                                         "activo": bool(c.get("activo"))}
        except ConectorError as exc:
            report["acreditacion_fg"] = {"valida": False, "motivos": [f"{exc.code}: {exc.detail}"]}
    if a.conector == "backlog_ssrs" and c:
        try:  # v0.3.3, informativo: no altera el resultado del preflight
            ok_b, mot_b = orq.acreditado(data, "backlog_ssrs", c)
            activo = bool(c.get("activo"))
            report["acreditacion_backlog"] = {"valida": ok_b, "motivos": mot_b, "activo": activo}
        except ConectorError as exc:
            motivo = f"{exc.code}: {exc.detail}"
            report["acreditacion_backlog"] = {"valida": False, "motivos": motivo}
    report["resultado"] = "PASS" if all(x["ok"] for x in checks) else "FAIL"
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    return 0 if report["resultado"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
