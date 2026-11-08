"""v0.3.2 — verificación de rollback a v0.3.1 y regreso (SINTÉTICO, simuladores, sin red).

  python verificar_rollback.py <ruta a la carpeta app de v0.3.1>

1. v0.3.2 produce datos con FG por consulta directa acreditada (alcance FG) y una contingencia.
2. v0.3.1, sobre los MISMOS datos: muestra la generación vigente de v0.3.2 y ejecuta un Refresh.
   Debe rechazar la acreditación FG /2 (fallo cerrado → IN092U manual), sin consultar ODBC, sin
   tocar el Frozen bloqueado ni las notas.
3. v0.3.2 otra vez: vuelve a usar la consulta directa.
Salida 0 = todas las comprobaciones PASS.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))

import simuladores as sim  # noqa: E402
import test_conectores as tc  # noqa: E402
import test_preview as tp  # noqa: E402

VIEJO = r'''
import json, sys
from pathlib import Path
app, data = Path(sys.argv[1]), Path(sys.argv[2])
sys.path[:0] = [str(app), str(app / "tests")]
import simuladores as sim, test_conectores as tc, test_preview as tp
from nexus_preview.generaciones import PreviewRefresh
ref = PreviewRefresh(data, "v0.3.1-rollback")
html_antes = tp.client(ref).get("/junta")
conn = sim.Conexion(sim.filas_fg_posterior())
ref.deps = tc.deps(sim.xml_ssrs(sim.plan_posterior()), None, fabrica_fg=sim.fabrica(conn))
ref.clock = sim.reloj("2026-11-11")
st = ref.ejecutar("rollback")
html = tp.client(ref).get("/junta").get_data(as_text=True)
print(json.dumps({"render_vigente_v032": html_antes.status_code, "estado": st["estado"], "fg": st.get("fuentes", {}).get("fg"),
                  "fg_odbc": st.get("conectores", {}).get("fg_odbc", {}).get("estado"), "consultas_odbc": len(conn.ejecutadas),
                  "nota": "nota rollback" in html, "motivos": st.get("motivos")}))
'''


def main(viejo: Path) -> int:
    checks: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory(prefix="rollback_v032_") as tmp:
        paths, ref = tp.make_lab(Path(tmp))
        ref.clock = sim.reloj("2026-11-01", "14:00:00")
        checks.append(("v0.3.2 generación inicial", ref.ejecutar("t")["estado"] == "PASS"))
        lock = ref.store.frozen_lock("2026-11")
        c = tp.client(ref)
        c.post("/api/cs", json={"item": "QZXA0016", "version": 0, "nota": "nota rollback", "adelanto": "10"}, headers={"X-CSRF": tp.csrf(c)})
        tc.live(ref, paths)
        st = ref.ejecutar("t")
        checks.append(("v0.3.2 FG por consulta directa acreditada", st["fuentes"]["fg"].startswith("fg_odbc:")))
        ref.deps["fabrica_fg"] = sim.fabrica(sim.Conexion([], falla=True))
        ref.clock = sim.reloj("2026-11-09")
        st = ref.ejecutar("t")
        checks.append(("v0.3.2 contingencia marcada", ref.store.vigente()["sources"]["fg"]["conector"]["estado"] == "CONTINGENCIA"))
        run = subprocess.run([sys.executable, "-c", VIEJO, str(viejo), str(paths["data"])], capture_output=True, text=True, timeout=300)
        if run.returncode != 0:
            print(run.stderr[-2000:])
            return 1
        old = json.loads(run.stdout.strip().splitlines()[-1])
        print("v0.3.1 →", json.dumps(old, ensure_ascii=False))
        checks += [
            ("v0.3.1 muestra la generación vigente de v0.3.2", old["render_vigente_v032"] == 200),
            ("v0.3.1 Refresh sobre datos v0.3.2", old["estado"] in ("PASS", "NO_CHANGE")),
            ("v0.3.1 rechaza la acreditación FG /2 (fallo cerrado)", old["fg_odbc"] == "NO_ACREDITADO"),
            ("v0.3.1 no consulta ODBC sin acreditación", old["consultas_odbc"] == 0),
            ("v0.3.1 vuelve al IN092U manual", old["fg"] == "INV_STA289.TXT"),
            ("v0.3.1 conserva notas", old["nota"] is True),
            ("Frozen intacto tras rollback", ref.store.frozen_lock("2026-11") == lock),
        ]
        tc.live(ref, paths)
        ref.clock = sim.reloj("2026-11-12")
        st = ref.ejecutar("t")
        checks += [
            ("regreso a v0.3.2: FG por consulta directa", st["estado"] == "PASS" and st["fuentes"]["fg"].startswith("fg_odbc:")),
            ("regreso a v0.3.2: Frozen intacto", ref.store.frozen_lock("2026-11") == lock),
        ]
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).resolve()))
