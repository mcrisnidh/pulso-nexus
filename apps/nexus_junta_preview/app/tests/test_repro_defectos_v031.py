"""Reproducción, sobre v0.3.1 SIN MODIFICAR, de los defectos que corrige v0.3.2 (sintético).
Cada prueba afirma el comportamiento CORRECTO: en v0.3.1 deben FALLAR."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))

import dataset_demo as demo  # noqa: E402
import physical_fixtures as pf  # noqa: E402
import simuladores as sim  # noqa: E402
import test_conectores as tc  # noqa: E402

from nexus_preview.conectores import orquestador as orq  # noqa: E402

lab = tc.lab


def _manual(src: Path, hora: str) -> None:
    rows = [(i["item"], "FG", "7.000") for i in demo._plan()[:30]]
    (src / "INV_STA291.TXT").write_bytes(pf.in092u_bytes(date="11/08/26", time=hora, rows=rows))


def test_x1_acreditacion_fg_inventada_debe_rechazarse(tmp_path: Path) -> None:
    getattr(sim, "acreditar_falsa", sim.acreditar)(tmp_path, "fg_odbc")  # informe_sha256 = "a"*64, sin informe ni capturas
    assert orq.acreditado(tmp_path, "fg_odbc")[0] is False


def test_x2_manual_posterior_no_debe_sustituir_la_consulta_directa(lab: Any) -> None:
    paths, ref = lab
    _manual(paths["src"], "10:00:00")  # 16:00Z > consulta 15:00Z
    tc.live(ref, paths)
    st = ref.ejecutar("t")
    assert st["conectores"]["fg_odbc"]["estado"] == "CAPTURADO"
    assert st["fuentes"]["fg"].startswith("fg_odbc:"), st["fuentes"]["fg"]


def test_x3_consulta_con_mismos_bytes_debe_ser_un_corte_nuevo(lab: Any) -> None:
    paths, ref = lab
    tc.live(ref, paths)
    ref.ejecutar("t")
    _manual(paths["src"], "10:00:00")
    ref.clock = sim.reloj("2026-11-08", "17:00:00")
    st = ref.ejecutar("t")
    assert st["conectores"]["fg_odbc"]["estado"] == "CAPTURADO"
    assert st["fuentes"]["fg"].startswith("fg_odbc:"), st["fuentes"]["fg"]


def test_x4_contingencia_debe_marcar_el_dato_como_anterior(lab: Any) -> None:
    paths, ref = lab
    tc.live(ref, paths)
    ref.ejecutar("t")
    ref.deps["fabrica_fg"] = sim.fabrica(sim.Conexion([], falla=True))
    ref.clock = sim.reloj("2026-11-09")
    assert ref.ejecutar("t")["parcial"] is True
    gen = ref.store.vigente()
    assert (gen["sources"]["fg"].get("conector") or {}).get("estado") == "CONTINGENCIA"


def test_x5_preflight_no_debe_fallar_con_consola_oem(tmp_path: Path) -> None:
    sim.config_conectores(tmp_path)
    env = {**os.environ, "PYTHONIOENCODING": "cp437", "PYTHONUTF8": "0"}
    run = subprocess.run([sys.executable, str(APP / "preflight.py"), "--data", str(tmp_path), "--conector", "fg_odbc"],
                         capture_output=True, env=env, timeout=120)
    assert b"UnicodeEncodeError" not in run.stderr, run.stderr.decode("utf-8", "replace")[-300:]


def test_x6_lanzador_utf8_bom_y_codigo_de_salida() -> None:
    body = (APP.parent / "NexusJunta.ps1").read_bytes()
    assert body.startswith(b"\xef\xbb\xbf"), "sin BOM: Windows PowerShell 5.1 lee el script como ANSI"
    assert b"& uv @a; return" not in body, "el código de salida de uv se pierde"
