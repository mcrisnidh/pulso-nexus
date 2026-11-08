"""Demostración reproducible (SIMULADORES, datos sintéticos): Refresh con Backlog SSRS y FG ODBC.

  python demo_conectores.py <carpeta_vacía> [--fallo-fg]
Deja una carpeta de datos lista para ``preview.py serve``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import simuladores as sim  # noqa: E402
import test_preview as tp  # noqa: E402

root = Path(sys.argv[1])
paths, ref = tp.make_lab(root)
ref.clock = sim.reloj("2026-11-01", "14:00:00")
print(ref.ejecutar("demo")["estado"])
sim.config_conectores(paths["data"], embarques={"activo": True})
for f in ("backlog_ssrs", "fg_odbc"):
    sim.acreditar(paths["data"], f)
ref.deps = {"proveedor": sim.proveedor, "usuario": "ucbaa", "ssrs_fetch": lambda url: sim.xml_ssrs(sim.plan_posterior()),
            "fabrica_fg": sim.fabrica(sim.Conexion(sim.filas_fg_posterior())),
            "fabrica_embarques": sim.fabrica(sim.Conexion([("QZXA0016", "300", 2, 2, 0)]))}
ref.clock = sim.reloj("2026-11-08")
st = ref.ejecutar("demo")
print(json.dumps({k: st[k] for k in ("estado", "fuentes", "parcial")}, ensure_ascii=False))
if "--fallo-fg" in sys.argv:
    ref.deps["fabrica_fg"] = sim.fabrica(sim.Conexion([], falla=True))
    ref.deps["ssrs_fetch"] = lambda url: sim.xml_ssrs(sim.plan_posterior()[:-2])
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("demo")
    print(json.dumps({k: st[k] for k in ("estado", "parcial", "advertencias")}, ensure_ascii=False))
