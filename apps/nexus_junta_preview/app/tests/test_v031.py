"""v0.3.1 — reproducción de los 4 defectos señalados en la revisión de v0.3 (SIMULADORES, sintético)."""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import pytest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))

import conciliar  # noqa: E402
import dataset_demo as demo  # noqa: E402
import simuladores as sim  # noqa: E402
import test_conectores as tc  # noqa: E402
import test_preview as tp  # noqa: E402

from nexus_preview.conectores import ConectorError, ssrs  # noqa: E402
from nexus_preview.conectores import orquestador as orq  # noqa: E402

lab = tc.lab


def _approvers(paths: dict[str, Path]) -> None:
    (paths["priv"] / "aprobadores.json").write_text(json.dumps({"approvers": ["aprobador.sintetico"]}), "utf-8")
    cfgp = json.loads((paths["data"] / "preview.json").read_text("utf-8"))
    cfgp["approvers"] = str(paths["priv"] / "aprobadores.json")
    (paths["data"] / "preview.json").write_text(json.dumps(cfgp), "utf-8")


# ---------------------------------------------------------------- 1. conectores desactivados
def test_d1_conector_desactivado_no_aporta_capturas(lab: Any) -> None:
    paths, ref = lab
    tc.live(ref, paths)
    st = ref.ejecutar("t")
    assert st["fuentes"]["fg"].startswith("fg_odbc:") and st["fuentes"]["current_r"].startswith("backlog_ssrs:")
    sim.config_conectores(paths["data"], backlog_ssrs={"activo": False}, fg_odbc={"activo": False})
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")
    assert st["conectores"]["fg_odbc"]["estado"] == "INACTIVO" and st["conectores"]["backlog_ssrs"]["estado"] == "INACTIVO"
    assert st["fuentes"]["fg"] == "INV_STA289.TXT"  # vuelve al FG manual
    assert st["fuentes"]["current_r"] == "R_20261101.TXT"
    assert list((paths["data"] / "capturas" / "fg_odbc").glob("*.raw"))  # históricas conservadas


def test_d1b_sin_acreditacion_vigente_no_aporta_capturas(lab: Any) -> None:
    paths, ref = lab
    tc.live(ref, paths)
    assert ref.ejecutar("t")["fuentes"]["fg"].startswith("fg_odbc:")
    (paths["data"] / "acreditacion_fg_odbc.json").unlink()
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")
    assert st["conectores"]["fg_odbc"]["estado"] == "NO_ACREDITADO" and st["fuentes"]["fg"] == "INV_STA289.TXT"


def test_d1c_fallo_con_conservar_sigue_usando_el_ultimo_corte(lab: Any) -> None:
    paths, ref = lab
    tc.live(ref, paths)
    fg = ref.ejecutar("t")["fuentes"]["fg"]
    ref.deps["fabrica_fg"] = sim.fabrica(sim.Conexion([], falla=True))
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")
    assert st["parcial"] is True and st["fuentes"]["fg"] == fg  # política explícita ≠ desactivación


def test_d1d_frozen_capturado_se_conserva_al_desactivar(tmp_path: Path) -> None:
    paths, ref = tp.make_lab(tmp_path)
    (paths["src"] / "R_20261101.TXT").unlink()
    sim.config_conectores(paths["data"], fg_odbc={"activo": False})
    sim.acreditar(paths["data"], "backlog_ssrs")
    ref.deps = tc.deps(sim.xml_ssrs([{"item": i["item"], "lines": i["lines"]} for i in demo._plan()]))
    ref.clock = sim.reloj("2026-11-01", "15:00:00")
    assert ref.ejecutar("t")["estado"] == "PASS"
    lock = ref.store.frozen_lock("2026-11")
    demo.corte_posterior(paths["src"])  # Current manual
    sim.config_conectores(paths["data"], backlog_ssrs={"activo": False}, fg_odbc={"activo": False})
    ref.clock = sim.reloj("2026-11-07")
    st = ref.ejecutar("t")
    assert st["estado"] == "PASS" and st["fuentes"]["frozen_r"] == lock["oe965"]["file"]  # Frozen inmutable
    assert st["fuentes"]["current_r"] == "R_20261107.TXT"


# ---------------------------------------------------------------- 2. fecha FG en America/Mexico_City
@pytest.mark.parametrize(
    ("utc", "fecha_local", "en_noviembre"),
    [("2026-12-01T05:30:00", "2026-11-30", True), ("2026-12-01T06:30:00", "2026-12-01", False)],
)
def test_d2_fecha_fg_es_la_fecha_local_de_corte(lab: Any, utc: str, fecha_local: str, en_noviembre: bool) -> None:
    paths, ref = lab
    tc.live(ref, paths, cfg={"backlog_ssrs": {"activo": False}})
    ref.clock = lambda: dt.datetime.fromisoformat(utc + "+00:00")
    st = ref.ejecutar("t")
    cap = orq.listar(paths["data"], "fg_odbc")[-1]
    assert cap["fecha_documento"] == fecha_local and cap["instante_utc"] == utc + "Z"  # UTC conservado
    assert st["fuentes"]["fg"].startswith("fg_odbc:") is en_noviembre


def test_d2b_fg_manual_del_30_nov_noche_es_noviembre(lab: Any) -> None:
    paths, ref = lab
    (paths["src"] / "INV_STA299.TXT").write_bytes(demo.pf.in092u_bytes(date="11/30/26", time="22:15:00",
                                                                          rows=[("QZXA0016", "FG", "77.000")]))
    st = ref.ejecutar("t")
    assert st["estado"] == "PASS" and st["fuentes"]["fg"] == "INV_STA299.TXT"
    f = next(x for x in ref.store.vigente()["archivos"] if x["file"] == "INV_STA299.TXT")
    assert f["document_date"] == "2026-11-30"


# ---------------------------------------------------------------- 3. acreditación de Embarques verificable
def _emb_capturada(paths: dict[str, Path], ref: Any) -> None:
    _approvers(paths)
    tc.live(ref, paths, cfg={"embarques": {"activo": True}}, emb_rows=[("QZXA0016", "300", 2, 2, 0)])
    assert ref.ejecutar("t")["conectores"]["embarques"]["estado"] == "CAPTURADO"
    ref.clock = sim.reloj("2026-11-08", "16:00:00")


def _acreditado(ref: Any) -> bool:
    ref.ejecutar("t")
    return bool(ref.store.vigente()["embarques"]["acreditado"])


def test_d3_hashes_inventados_no_acreditan(lab: Any) -> None:
    paths, ref = lab
    _emb_capturada(paths, ref)
    (paths["data"] / "acreditacion_embarques.json").write_text(json.dumps({
        "schema": "nexus.preview.acreditacion-embarques/1", "acreditado": True, "evidencia_sha256": "b" * 64,
        "conciliacion_sha256": "c" * 64, "aprobado_por": "aprobador.sintetico", "trata_cancelaciones": True,
        "trata_notas_credito": True, "fecha_es_embarque_fisico": True, "identidad_item_exacta": True}), "utf-8")
    assert _acreditado(ref) is False
    gen = ref.store.vigente()
    assert all(r["embarques"] is None for r in gen["escenarios"]["A"]["rows"])
    assert any("evidencia" in m for m in gen["embarques"]["motivos"]), gen["embarques"]


def test_d3b_cadena_verificable_acredita(lab: Any) -> None:
    paths, ref = lab
    _emb_capturada(paths, ref)
    assert sim.acreditar_embarques_verificable(paths["data"], conciliar.main)["_rc_conciliacion"] == 0
    ok = _acreditado(ref)
    assert ok is True, ref.store.vigente()["embarques"]["motivos"]


@pytest.mark.parametrize(
    "romper",
    [
        "evidencia_alterada", "conciliacion_alterada", "conciliacion_con_diferencias", "sin_identidad_item",
        "naturaleza_no_fisica", "ruta_fuera_de_datos", "evidencia_ajena", "aprobador_no_autorizado",
    ],
)
def test_d3c_acreditacion_sin_respaldo_verificable(lab: Any, romper: str) -> None:
    paths, ref = lab
    _emb_capturada(paths, ref)
    data = paths["data"]
    over: dict[str, Any] = {}
    if romper == "conciliacion_con_diferencias":
        doc = sim.acreditar_embarques_verificable(data, conciliar.main, evidencia_qty="250")
        assert doc["_rc_conciliacion"] == 1
    else:
        if romper == "sin_identidad_item":
            over["identidad_item_exacta"] = False
        elif romper == "naturaleza_no_fisica":
            over["fecha_es_embarque_fisico"] = False
        elif romper == "ruta_fuera_de_datos":
            over["evidencia_ruta"] = "../../etc/passwd"
        elif romper == "aprobador_no_autorizado":
            over["aprobado_por"] = "otra.persona"
        doc = sim.acreditar_embarques_verificable(data, conciliar.main, **over)
        if romper == "evidencia_alterada":
            ev = data / doc["evidencia_ruta"]
            ev.write_text(ev.read_text("utf-8") + "QZXA0001,1\n", "utf-8")
        elif romper == "conciliacion_alterada":
            inf = data / doc["conciliacion_ruta"]
            inf.write_text(inf.read_text("utf-8").replace('"PASS"', '"PASS" '), "utf-8")
        elif romper == "evidencia_ajena":  # otro archivo con hash válido pero que no es la evidencia conciliada
            other = data / "acreditacion" / "otra.csv"
            other.write_text("item,cantidad\nQZXA0016,300\nX,1\n", "utf-8")
            import hashlib

            doc.update(evidencia_ruta=str(other.relative_to(data)), evidencia_sha256=hashlib.sha256(other.read_bytes()).hexdigest())
            (data / "acreditacion_embarques.json").write_text(json.dumps(doc), "utf-8")
    assert _acreditado(ref) is False
    gen = ref.store.vigente()
    assert all(r["embarques"] is None for r in gen["escenarios"]["A"]["rows"])  # Embarques/Programa/Faltante N/D


# ---------------------------------------------------------------- 4. SSRS sin supuestos de entidad/moneda
@pytest.mark.parametrize("attr", [b'entity_id="0036" ', b'target_currency="USD" '])
def test_d4_ssrs_atributo_ausente_se_rechaza(attr: bytes) -> None:
    xml = sim.xml_ssrs(sim.plan_posterior()).replace(attr, b"", 1)  # falta en una fila
    with pytest.raises(ConectorError) as e:
        ssrs.parse(xml, {}, "2026-11")
    assert e.value.code == "SSRS_CALIDAD_FALLIDA"


@pytest.mark.parametrize("attr", [b'entity_id="0036" ', b'target_currency="USD" '])
def test_d4b_ssrs_atributo_ausente_en_todas_las_filas(attr: bytes) -> None:
    xml = sim.xml_ssrs(sim.plan_posterior()).replace(attr, b"")
    with pytest.raises(ConectorError):
        ssrs.parse(xml, {}, "2026-11")


def test_d4c_ssrs_validacion_de_entidad_no_se_puede_desactivar() -> None:
    xml = sim.xml_ssrs(sim.plan_posterior())
    for cfg in ({"atributos": {"entidad": ""}}, {"atributos": {"moneda": ""}}, {"entidad": ""}, {"moneda": ""}):
        with pytest.raises(ConectorError) as e:
            ssrs.parse(xml, cfg, "2026-11")
        assert e.value.code == "SSRS_CONTRATO_INCOMPLETO"
    with pytest.raises(ConectorError):
        ssrs.parse(xml.replace(b'target_currency="USD"', b'target_currency="MXN"', 1), {}, "2026-11")
