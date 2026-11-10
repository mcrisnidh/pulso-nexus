"""FIX5 — emisión CONTROLADA de la acreditación /2 de Embarques (regla CFDI emitida = embarque).

  acreditar_embarques.py --data DIR --captura DIR/preflight/embarques/<instante>.csv
                         --aprobador NOMBRE --ausencia-es-cero si|no --referencia-decision TEXTO

- La captura debe tener PROCEDENCIA corroborable (FIX5.1): el ``.json`` que el preflight o el
  Refresh escriben al consultar, con conector, resultado OK, huella de configuración + SQL,
  periodo y conteos coherentes con el archivo. Un CSV sin esa procedencia se rechaza.
- El aprobador debe figurar en la lista privada de aprobadores (``preview.json`` → approvers).
- ``--ausencia-es-cero`` es OBLIGATORIO: decide si un ITEM del universo sin facturación en el
  periodo vale 0 (``si``) o queda desconocido (``no``). No hay valor por omisión.
- ``--referencia-decision`` es OBLIGATORIO: referencia verificable de la decisión comercial
  APROBADA (minuta, correo, folio). Sin ella no se emite nada: la omisión no es aprobación.
- No activa el conector ni consulta sistemas corporativos.
- Verifica el documento antes de escribirlo.
Salida: 0 emitida · 1 rechazada · 2 uso inválido.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from nexus_preview.conectores import ConectorError  # noqa: E402
from nexus_preview.conectores import acreditacion_embarques as ae  # noqa: E402
from nexus_preview.conectores import orquestador as orq  # noqa: E402
from nexus_preview.consola import utf8_consola  # noqa: E402


def _aprobadores(data: Path) -> frozenset[str]:
    cfg = json.loads((data / "preview.json").read_text("utf-8"))
    ruta = cfg.get("approvers")
    if not ruta or not Path(ruta).is_file():
        return frozenset()
    return frozenset(json.loads(Path(ruta).read_text("utf-8")).get("approvers", []))


def main(argv: list[str] | None = None) -> int:
    utf8_consola()
    ap = argparse.ArgumentParser(prog="acreditar_embarques.py")
    ap.add_argument("--data", required=True)
    ap.add_argument("--captura", required=True)
    ap.add_argument("--aprobador", required=True)
    ap.add_argument("--ausencia-es-cero", required=True, choices=("si", "no"))
    ap.add_argument("--referencia-decision", required=True)
    a = ap.parse_args(argv)
    data, captura = Path(a.data).resolve(), Path(a.captura).resolve()
    if not a.referencia_decision.strip():
        print("--referencia-decision vacía: falta la decisión comercial aprobada", file=sys.stderr)
        return 2
    try:
        cfg = orq.cargar_config(data).get("embarques")
    except ConectorError as exc:
        print(f"conectores.json inválido: {exc}", file=sys.stderr)
        return 2
    if not isinstance(cfg, dict):
        print("conectores.json no tiene la sección embarques", file=sys.stderr)
        return 2
    try:
        rel = captura.relative_to(data).as_posix()
    except ValueError:
        rel = ""
    if not any(rel.startswith(o + "/") for o in ae.ORIGENES):
        print(
            "--captura debe estar en <datos>/preflight/embarques o capturas/embarques",
            file=sys.stderr,
        )
        return 2
    aprobadores = _aprobadores(data) if (data / "preview.json").is_file() else frozenset()
    if a.aprobador not in aprobadores:
        print("el aprobador no figura en la lista privada de aprobadores", file=sys.stderr)
        return 1
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        cero = a.ausencia_es_cero == "si"
        doc = ae.documento(data, cfg, captura, a.aprobador, cero, stamp, a.referencia_decision)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"captura rechazada: {exc}", file=sys.stderr)
        return 1
    tmp = data / (ae.ARCHIVO + ".nuevo")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), "utf-8")
    ok, motivos, _ = ae.verificar(data, cfg, aprobadores, tmp)
    if not ok:
        tmp.unlink()
        print("acreditación rechazada: " + "; ".join(motivos), file=sys.stderr)
        return 1
    tmp.replace(data / ae.ARCHIVO)
    print(f"{ae.ARCHIVO} emitida (esquema /2, regla {ae.REGLA}, fecha {ae.FECHA_EVENTO}).")
    print(
        f"Procedencia: {doc['procedencia']} · ejecución {doc['instante_referencia_utc']} · "
        f"periodo {doc['periodo_referencia']} · {doc['items_referencia']} ITEM · "
        f"{doc['facturas_referencia']} facturas"
    )
    valor = "0" if cero else "desconocido"
    print(f"Ausencia de factura: {valor} (decisión: {a.referencia_decision.strip()})")
    print(f"Alcance: {ae.ALCANCE_AJUSTES}")
    print("El conector NO se activa: «activo» se cambia a mano con autorización.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
