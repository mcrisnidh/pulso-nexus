"""PULSO NEXUS Preview v0.2 — línea de comandos (laboratorio local, PROVISIONAL / NO-GO).

  preview.py init    --data DIR --src CARPETA [--r3 CSV] [--r1 CSV] [--asignaciones CSV] [--month AAAA-MM]
  preview.py refresh --data DIR
  preview.py serve   --data DIR [--port 8877]

Se ejecuta desde el repositorio (commit de referencia) con:
  uv run --frozen --extra web --with tzdata==2025.2 python <app>/preview.py ...
``DIR`` debe estar fuera del repositorio y fuera de la carpeta de fuentes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from nexus_preview.consola import utf8_consola  # noqa: E402

from nexus_preview.generaciones import PreviewRefresh, code_version  # noqa: E402

from nexus.core.stage import check_private_output  # noqa: E402


def _code() -> str:
    import nexus  # noqa: PLC0415

    return code_version(HERE / "nexus_preview", Path(nexus.__file__).resolve().parent / "core")


def main(argv: list[str] | None = None) -> int:
    utf8_consola()
    ap = argparse.ArgumentParser(prog="preview.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--data", required=True)
    i.add_argument("--src", required=True)
    i.add_argument("--month", default="2026-11")
    i.add_argument("--tz", default="America/Mexico_City")
    for opt in ("r3", "r1", "rules", "homologation", "ledger", "approvers", "asignaciones"):
        i.add_argument(f"--{opt}", default=None)
    i.add_argument("--escenario", default="A", choices=["A", "B", "DIAG", "OFICIAL"])
    r = sub.add_parser("refresh")
    r.add_argument("--data", required=True)
    r.add_argument("--erp-urgente", default=None,
                   help="Solo para un Refresh: TXT OE965 reciente; no cambia Frozen ni configuración")
    r.add_argument("--aprobador", default="")
    r.add_argument("--motivo", default="")
    a6 = sub.add_parser("activar-fix6")
    a6.add_argument("--data", required=True)
    cs = sub.add_parser("confirmar-ssrs-mensual")
    cs.add_argument("--data", required=True)
    cs.add_argument("--mes", required=True)
    cs.add_argument("--sha256", required=True)
    cs.add_argument("--archivo", required=True)
    cs.add_argument("--aprobador", required=True)
    cs.add_argument("--referencia-lote", required=True)
    g = sub.add_parser("generaciones")
    g.add_argument("--data", required=True)
    v = sub.add_parser("activar")
    v.add_argument("--data", required=True)
    v.add_argument("--gen", required=True)
    c = sub.add_parser("certificar")
    c.add_argument("--data", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--data", required=True)
    s.add_argument("--port", type=int, default=8877)
    s.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    data = Path(a.data).resolve()
    if a.cmd == "init":
        src = Path(a.src).resolve()
        check_private_output(data, src)  # fuera de git y fuera del origen
        data.mkdir(parents=True, exist_ok=True)
        cfg = data / "preview.json"
        if cfg.exists():
            print(f"ya existe {cfg}: no se sobrescribe", file=sys.stderr)
            return 2
        body = {
            "schema": "nexus.preview.config/1",
            "month": a.month,
            "tz": a.tz,
            "src": str(src),
            "r3_provisional": a.r3 and str(Path(a.r3).resolve()),
            "r1_provisional": a.r1 and str(Path(a.r1).resolve()),
            "rules": a.rules and str(Path(a.rules).resolve()),
            "homologation": a.homologation and str(Path(a.homologation).resolve()),
            "ledger": a.ledger and str(Path(a.ledger).resolve()),
            "approvers": a.approvers and str(Path(a.approvers).resolve()),
            "asignaciones": a.asignaciones and str(Path(a.asignaciones).resolve()),
            "escenario_por_defecto": a.escenario,
        }
        with cfg.open("x", encoding="utf-8") as fh:
            json.dump(body, fh, indent=1, ensure_ascii=False)
        print(f"configuración creada: {cfg}")
        return 0
    if a.cmd == "activar-fix6":
        from nexus_preview.migracion_fix6 import activar  # noqa: PLC0415

        print(json.dumps(activar(data), ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "confirmar-ssrs-mensual":
        from nexus_preview.conectores.confirmacion_ssrs import emitir  # noqa: PLC0415

        result = emitir(data, a.mes, a.archivo, a.sha256, a.aprobador, a.referencia_lote)
        print(json.dumps({"confirmacion": str(result), "mes": a.mes, "estado": "CONFIRMADO"},
                         ensure_ascii=False))
        return 0
    refresher = PreviewRefresh(data, _code())
    if a.cmd == "generaciones":
        ptr = refresher.store.vigente()
        for d in sorted(refresher.store.gens.iterdir()):
            if d.name.startswith("."):
                continue
            mark = "*" if ptr and ptr["gen_id"] == d.name else " "
            doc = json.loads((d / "generacion.json").read_text("utf-8"))
            src = doc["sources"]
            print(f"{mark} {d.name}  current={src['current_r']['file']}  portal={src['current_p']['file']}  fg={src['fg']['file']}")
        return 0
    if a.cmd == "activar":
        import datetime as dt  # noqa: PLC0415

        from nexus_preview.generaciones import activar  # noqa: PLC0415

        print(json.dumps(activar(refresher.store, a.gen, dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))))
        return 0
    if a.cmd == "certificar":
        from nexus_preview.servicio import evaluar_certificacion, vista_de  # noqa: PLC0415

        gen = refresher.store.vigente()
        if gen is None:
            print("sin generación vigente", file=sys.stderr)
            return 2
        ev = evaluar_certificacion(gen)
        print(json.dumps({"gen_id": gen["gen_id"], "vista": vista_de(gen), **ev, "resultado": "RECHAZADA"}, indent=1, ensure_ascii=False))
        return 3  # Preview nunca certifica: siempre rechaza e informa los motivos
    if a.cmd == "refresh":
        if a.erp_urgente and (not a.aprobador or not a.motivo):
            ap.error("--erp-urgente exige --aprobador y --motivo")
        st = refresher.ejecutar("cli-erp-urgente" if a.erp_urgente else "cli-local",
                               erp_urgente=Path(a.erp_urgente) if a.erp_urgente else None,
                               aprobador=a.aprobador, motivo=a.motivo)
        print(json.dumps(st, indent=1, ensure_ascii=False))
        return 0 if st["estado"] in ("PASS", "NO_CHANGE") else 1
    if a.host != "127.0.0.1":
        print("Preview v0.2 solo escucha en 127.0.0.1 (sin exposición a la red)", file=sys.stderr)
        return 2
    from nexus_preview.webapp import create_app  # noqa: PLC0415

    app = create_app(data, port=a.port, refresher=refresher)
    print(f"PULSO NEXUS Preview en http://127.0.0.1:{a.port}/junta  (Ctrl+C para detener)")
    app.run(host="127.0.0.1", port=a.port, debug=False, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
