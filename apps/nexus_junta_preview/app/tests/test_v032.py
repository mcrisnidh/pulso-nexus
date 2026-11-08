"""v0.3.2 — cierre del conector FG (SIMULADORES, datos sintéticos; sin conexiones corporativas).

A: acreditación FG con alcance, verificable y fallo cerrado ante falsificación / alteración / ajena.
R: el Refresh usa la consulta directa de ESE Refresh; contingencia explícita y marcada como dato anterior.
W: consola UTF-8 en Windows y propagación de códigos de salida del lanzador.

Ninguna prueba fija el número de ITEM real (343): las cantidades salen de los datos sintéticos.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

APP = Path(__file__).resolve().parents[1]
PKG = APP.parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))

import conciliar  # noqa: E402
import dataset_demo as demo  # noqa: E402
import physical_fixtures as pf  # noqa: E402
import simuladores as sim  # noqa: E402
import test_conectores as tc  # noqa: E402
import test_preview as tp  # noqa: E402

lab = tc.lab
TZ = "America/Mexico_City"


def _afg() -> Any:
    from nexus_preview.conectores import acreditacion_fg  # noqa: PLC0415  (no existe en v0.3.1)

    return acreditacion_fg


def _verificar(data: Path) -> tuple[bool, list[str], dict[str, Any]]:
    cfg = json.loads((data / "conectores.json").read_text("utf-8"))["fg_odbc"]
    return _afg().verificar(data, cfg, TZ)


def _fg_manual_rows(n: int | None = None) -> list[tuple[str, str, str]]:
    return [(i["item"], "FG", f"{(i['fg'] or 0) + 1000}.000") for i in demo._plan()[: n or 30]]


def _con_th(filas: list[Any]) -> list[Any]:
    """Caso observado en ASMXC1200: un registro adicional solo en TH en la consulta directa."""
    return [*filas, ("SERV19         ", "TH ", "1.000")]


@pytest.fixture
def acred(tmp_path: Path) -> Path:
    """Datos con conectores.json (inactivo) y acreditación FG por alcance con la diferencia TH."""
    data = tmp_path / "data"
    data.mkdir()
    sim.config_conectores(data, fg_odbc={"activo": False})
    assert sim.acreditar_fg_verificable(data, _con_th(sim.filas_fg_posterior())) == 0
    return data


def _doc(data: Path) -> dict[str, Any]:
    return json.loads((data / "acreditacion_fg_odbc.json").read_text("utf-8"))


def _put(data: Path, doc: dict[str, Any]) -> None:
    (data / "acreditacion_fg_odbc.json").write_text(json.dumps(doc), "utf-8")


def _carpeta(data: Path) -> Path:
    return data / "conciliaciones" / _doc(data)["carpeta"]


def _escribible(p: Path) -> Path:
    p.chmod(0o600)
    return p


# ================================================================ A. acreditación FG
def test_a01_alcance_fg_acredita_y_conserva_la_diferencia_th(acred: Path) -> None:
    data = acred
    doc = _doc(data)
    assert doc["schema"] == "nexus.preview.acreditacion-conector/2" and doc["fuente"] == "fg_odbc"
    assert doc["alcance"]["stockrooms"] == ["FG"] and doc["alcance"]["resultado"] == "PASS"
    assert doc["resultado_global"] == "DIFERENCIAS" and doc["alcance"]["diferencias_fuera_de_alcance"] == 1
    assert doc["alcance"]["stockrooms_fuera_de_alcance"] == ["TH"]
    assert doc["alcance"]["items"] == len(_fg_manual_rows())  # del dato, no constante
    informe = json.loads((_carpeta(data) / "informe_privado.json").read_text("utf-8"))
    assert informe["resultado"] == "DIFERENCIAS"  # el informe global se conserva íntegro
    assert informe["diferencias"] == [{"tipo": "SOLO_DIRECTA", "item": "SERV19", "stockroom": "TH", "manual": "", "directa": "1"}]
    assert informe["por_item"] == [] and informe["alcance"]["diferencias"] == []
    assert informe["alcance"]["identidad_item_exacta"] is True
    saneado = json.loads((_carpeta(data) / "resumen_saneado.json").read_text("utf-8"))
    assert saneado["resultado"] == "DIFERENCIAS" and saneado["alcance"]["resultado"] == "PASS"
    assert "SERV19" not in json.dumps(saneado)  # el resumen saneado no lleva identidades
    # cortes NO simultáneos: desfase documentado (manual 08:15 local = 14:15Z; directa 15:25Z)
    assert doc["cortes"]["simultaneos"] is False and doc["cortes"]["desfase_minutos"] == 70
    assert "NO son simultáneos" in doc["cortes"]["limitacion"]
    ok, motivos, info = _verificar(data)
    assert ok, motivos
    assert info["alcance"] == ["FG"] and info["fuera_de_alcance"] == 1 and info["resultado_global"] == "DIFERENCIAS"


def test_a02_la_acreditacion_no_activa_el_conector(acred: Path) -> None:
    cfg = json.loads((acred / "conectores.json").read_text("utf-8"))
    assert cfg["fg_odbc"]["activo"] is False and _doc(acred)["activa_conector"] is False
    plantilla = json.loads((APP / "conectores.plantilla.json").read_text("utf-8"))
    assert all(v.get("activo") is False for k, v in plantilla.items() if isinstance(v, dict))


def test_a03_sin_alcance_no_acredita_con_diferencia_global(tmp_path: Path) -> None:
    data = tmp_path / "d"
    data.mkdir()
    sim.config_conectores(data)
    assert sim.acreditar_fg_verificable(data, _con_th(sim.filas_fg_posterior()), alcance="") == 1
    assert not (data / "acreditacion_fg_odbc.json").exists()


def test_a04_diferencia_dentro_del_alcance_no_acredita(tmp_path: Path) -> None:
    data = tmp_path / "d"
    data.mkdir()
    sim.config_conectores(data)
    filas = sim.filas_fg_posterior()
    filas[3] = (filas[3][0], filas[3][1], "1.000")
    assert sim.acreditar_fg_verificable(data, filas) == 1
    assert not (data / "acreditacion_fg_odbc.json").exists()


def test_a05_identidad_item_exacta(tmp_path: Path) -> None:
    data = tmp_path / "d"
    data.mkdir()
    sim.config_conectores(data)
    filas = sim.filas_fg_posterior()
    filas[0] = (filas[0][0].lower(), filas[0][1], filas[0][2])  # mismo texto en minúsculas ≠ mismo ITEM
    assert sim.acreditar_fg_verificable(data, filas) == 1
    informe = json.loads(next((data / "conciliaciones").glob("fg_*/informe_privado.json")).read_text("utf-8"))
    assert informe["alcance"]["identidad_item_exacta"] is False
    assert {d["tipo"] for d in informe["alcance"]["diferencias"]} == {"SOLO_DIRECTA", "SOLO_MANUAL"}


def test_a06_cantidad_de_items_sale_del_dato(tmp_path: Path) -> None:
    data = tmp_path / "d"
    data.mkdir()
    sim.config_conectores(data)
    filas = sim.filas_fg_posterior()[:7]
    assert sim.acreditar_fg_verificable(data, filas, manual=_fg_manual_rows(7)) == 0
    assert _doc(data)["alcance"]["items"] == 7 and _verificar(data)[0]


def test_a07_directa_debe_ser_captura_original(tmp_path: Path) -> None:
    data = tmp_path / "d"
    data.mkdir()
    sim.config_conectores(data)
    directa = sim.captura_preflight_fg(data)
    copia = tmp_path / "copia.csv"
    copia.write_bytes(directa.read_bytes())
    rc = conciliar.main(["fg", "--data", str(data), "--manual", str(sim.manual_fg(data)), "--directa", str(copia),
                         "--alcance", "FG", "--acreditar"])
    assert rc == 2 and not (data / "acreditacion_fg_odbc.json").exists()


@pytest.mark.parametrize("caso", ["v031_falsa", "v032_inventada", "sin_alcance", "otra_fuente", "no_pass"])
def test_a08_acreditacion_falsificada_se_rechaza(acred: Path, caso: str) -> None:
    data = acred
    doc = _doc(data)
    if caso == "v031_falsa":
        sim.acreditar_falsa(data, "fg_odbc")
    else:
        if caso == "v032_inventada":
            doc.update(informe_sha256="b" * 64, directa_sha256="c" * 64, manual_sha256="d" * 64)
        elif caso == "sin_alcance":
            doc["alcance"] = {}
        elif caso == "otra_fuente":
            doc["fuente"] = "backlog_ssrs"
        else:
            doc["alcance"]["resultado"] = "DIFERENCIAS"
        _put(data, doc)
    ok, motivos, _ = _verificar(data)
    assert not ok and motivos


@pytest.mark.parametrize("caso", ["byte_informe", "informe_reescrito", "alcance_ampliado", "global_maquillado",
                                  "fuera_de_alcance_borradas", "manual_evidencia", "directa_evidencia",
                                  "origen_borrado", "origen_alterado"])
def test_a09_acreditacion_alterada_se_rechaza(acred: Path, caso: str) -> None:
    data = acred
    doc = _doc(data)
    folder = _carpeta(data)
    informe_p = folder / "informe_privado.json"
    if caso == "byte_informe":
        informe_p.write_bytes(informe_p.read_bytes().replace(b'"TH"', b'"TX"'))
    elif caso == "informe_reescrito":
        # atacante borra la diferencia TH del informe y actualiza la huella en la acreditación
        inf = json.loads(informe_p.read_text("utf-8"))
        inf.update(resultado="PASS", diferencias=[])
        inf["resumen"]["diferencias"] = 0
        body = json.dumps(inf).encode("utf-8")
        informe_p.write_bytes(body)
        doc.update(informe_sha256=hashlib.sha256(body).hexdigest(), resultado_global="PASS")
        _put(data, doc)
    elif caso == "alcance_ampliado":
        doc["alcance"]["stockrooms"] = ["FG", "TH"]
        _put(data, doc)
    elif caso == "global_maquillado":
        doc["resultado_global"] = "PASS"
        _put(data, doc)
    elif caso == "fuera_de_alcance_borradas":
        doc["alcance"]["diferencias_fuera_de_alcance"] = 0
        _put(data, doc)
    elif caso == "manual_evidencia":
        p = _escribible(next(folder.glob("manual_*")))
        p.write_bytes(p.read_bytes() + b"\n")
    elif caso == "directa_evidencia":
        p = _escribible(folder / "directa.csv")
        p.write_bytes(p.read_bytes() + b"QZXZ9999,FG,5\n")
    elif caso == "origen_borrado":
        (data / doc["origen_directa"]).unlink()
    else:
        p = data / doc["origen_directa"]
        p.write_bytes(p.read_bytes().replace(b",FG,", b",FG ,", 1))
    ok, motivos, _ = _verificar(data)
    assert not ok and motivos, caso


@pytest.mark.parametrize("caso", ["otra_instalacion", "otra_captura", "ruta_escapa", "ruta_absoluta", "enlace"])
def test_a10_acreditacion_ajena_se_rechaza(tmp_path: Path, caso: str) -> None:
    ajena = tmp_path / "ajena"
    ajena.mkdir()
    sim.config_conectores(ajena)
    assert sim.acreditar_fg_verificable(ajena, _con_th(sim.filas_fg_posterior())) == 0
    data = tmp_path / "propia"
    data.mkdir()
    sim.config_conectores(data)
    doc = _doc(ajena)
    if caso in ("otra_instalacion", "otra_captura"):
        shutil.copytree(ajena / "conciliaciones", data / "conciliaciones")
        shutil.copy(ajena / "acreditacion_fg_odbc.json", data / "acreditacion_fg_odbc.json")
        if caso == "otra_captura":  # la instalación propia tiene SU captura, distinta de la evidencia ajena
            sim.captura_preflight_fg(data, sim.filas_fg_posterior()[:5], stamp=Path(doc["origen_directa"]).stem)
    elif caso == "ruta_escapa":
        doc["carpeta"] = f"../../ajena/conciliaciones/{doc['carpeta']}"
        _put(data, doc)
    elif caso == "ruta_absoluta":
        doc["carpeta"] = str(ajena / "conciliaciones" / doc["carpeta"])
        _put(data, doc)
    else:
        (data / "conciliaciones").mkdir()
        (data / "conciliaciones" / doc["carpeta"]).symlink_to(ajena / "conciliaciones" / doc["carpeta"])
        (data / "preflight").symlink_to(ajena / "preflight")
        _put(data, doc)
    ok, motivos, _ = _verificar(data)
    assert not ok and motivos, caso


@pytest.mark.parametrize("cambio,valida", [
    ({"filtro_cantidad": ">"}, False),
    ({"dsn": "OTRO_DSN"}, False),
    ({"atributos_conexion": {"SORTTYPE": "1"}}, False),
    ({"cuenta_esperada": "otra"}, False),
    ({"activo": True}, True),
    ({"politica_fallo": "DETENER"}, True),
    ({"contingencia_max_horas": 6}, True),
])
def test_a11_huella_de_la_consulta(acred: Path, cambio: dict[str, Any], valida: bool) -> None:
    sim.config_conectores(acred, fg_odbc={"activo": False, **cambio})
    assert _verificar(acred)[0] is valida


def test_a12_sorttype_en_conectores_json_se_acredita_con_su_huella(tmp_path: Path) -> None:
    data = tmp_path / "d"
    data.mkdir()
    sim.config_conectores(data, fg_odbc={"dsn": "AuroraODBC", "atributos_conexion": {"SORTTYPE": "0"}})
    assert sim.acreditar_fg_verificable(data) == 0 and _verificar(data)[0]
    sim.config_conectores(data, fg_odbc={"atributos_conexion": {}})
    assert not _verificar(data)[0]


# ================================================================ R. Refresh
def _vivo(ref: Any, paths: dict[str, Path], **kw: Any) -> Any:
    return tc.live(ref, paths, **kw)


def _manual_posterior(src: Path, fecha: str, hora: str, nombre: str) -> Path:
    rows = [(i["item"], "FG", "7.000") for i in demo._plan()[:30]]
    p = src / nombre
    p.write_bytes(pf.in092u_bytes(date=fecha, time=hora, rows=rows))
    return p


def test_r01_refresh_usa_la_consulta_directa_de_ese_refresh(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    st = ref.ejecutar("t")
    assert st["estado"] == "PASS" and st["conectores"]["fg_odbc"]["estado"] == "CAPTURADO"
    gen = ref.store.vigente()
    fg = gen["sources"]["fg"]
    assert fg["file"] == f"fg_odbc:{st['conectores']['fg_odbc']['archivo']}" and fg["origen"] == "CONSULTA_DIRECTA"
    assert fg["document_time"] == "2026-11-08T15:00:00Z" and fg["conector"]["estado"] == "CONSULTA_DIRECTA"
    assert fg["conector"]["alcance"] == ["FG"] and fg["conector"]["acreditacion_sha256"]
    assert gen["inputs"]["fg_conector"] == {"estado": "CONSULTA_DIRECTA", "corte": "2026-11-08T15:00:00Z",
                                            "acreditacion_sha256": fg["conector"]["acreditacion_sha256"]}
    html = tp.client(ref).get("/junta").get_data(as_text=True)
    assert "consulta directa AS400 de este Refresh" in html


@pytest.mark.parametrize("hora", ["10:00:00", " 9:00:00"])  # posterior / igual (15:00Z = 09:00 local) a la consulta
def test_r02_manual_igual_o_posterior_no_sustituye_la_consulta(lab: Any, hora: str) -> None:
    paths, ref = lab
    _manual_posterior(paths["src"], "11/08/26", hora, "INV_STA291.TXT")
    _vivo(ref, paths)
    st = ref.ejecutar("t")
    assert st["fuentes"]["fg"].startswith("fg_odbc:"), st["fuentes"]
    gen = ref.store.vigente()
    assert any("INV_STA291.TXT" in w and "consulta directa" in w for w in gen["warnings"])
    assert all(tp.qty(r["fg"]) != 7 for r in tp.rows(gen).values() if r.get("fg") and r["fg"].get("value") is not None)


def test_r03_cada_consulta_es_un_corte_nuevo_aunque_los_bytes_coincidan(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    first = ref.ejecutar("t")
    _manual_posterior(paths["src"], "11/08/26", "10:00:00", "INV_STA291.TXT")  # 16:00Z: entre ambas consultas
    ref.clock = sim.reloj("2026-11-08", "17:00:00")
    st = ref.ejecutar("t")
    fgc = st["conectores"]["fg_odbc"]
    assert fgc["reutilizada"] is False and fgc["archivo"] != first["conectores"]["fg_odbc"]["archivo"]
    assert fgc["sha256"] == first["conectores"]["fg_odbc"]["sha256"]  # mismos bytes, otro corte
    assert st["estado"] == "PASS" and st["fuentes"]["fg"] == f"fg_odbc:{fgc['archivo']}"
    gen = ref.store.vigente()
    assert gen["sources"]["fg"]["document_time"] == "2026-11-08T17:00:00Z"
    assert gen["inputs"]["fg_conector"]["corte"] == "2026-11-08T17:00:00Z"
    fg_en_gen = [f for f in gen["archivos"] if f["file"].startswith("fg_odbc:")]
    assert len(fg_en_gen) == 1  # solo el corte de este Refresh entra a la generación
    assert len(list((paths["data"] / "capturas" / "fg_odbc").glob("*.raw"))) == 2  # ambos cortes para auditoría


def test_r04_contingencia_marca_el_dato_como_anterior(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    fg = ref.ejecutar("t")["fuentes"]["fg"]
    ref.deps["fabrica_fg"] = sim.fabrica(sim.Conexion([], falla=True))
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")
    assert st["estado"] == "PASS" and st["parcial"] is True and st["fuentes"]["fg"] == fg
    con = ref.store.vigente()["sources"]["fg"]["conector"]
    assert con["estado"] == "CONTINGENCIA" and con["codigo"] == "ODBC_CONSULTA_FALLIDA" and con["antiguedad_min"] == 24 * 60
    assert any(w.startswith("FG ANTERIOR") and "24 h 00 min" in w for w in ref.store.vigente()["warnings"])
    html = tp.client(ref).get("/junta").get_data(as_text=True)
    assert "FG ANTERIOR" in html and "antigüedad 24 h 00 min" in html
    assert sim.SECRETO_FG not in json.dumps(st) and sim.SECRETO_FG not in html


@pytest.mark.parametrize("horas,estado", [(12, "ERROR"), (48, "PASS")])
def test_r05_limite_autorizado_de_contingencia(lab: Any, horas: int, estado: str) -> None:
    paths, ref = lab
    _vivo(ref, paths, cfg={"fg_odbc": {"contingencia_max_horas": horas}})
    assert ref.ejecutar("t")["estado"] == "PASS"
    vigente = ref.store.vigente()["gen_id"]
    ref.deps["fabrica_fg"] = sim.fabrica(sim.Conexion([], falla=True))
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")
    assert st["estado"] == estado
    if estado == "ERROR":
        assert "contingencia autorizada" in st["motivos"][0] and ref.store.vigente()["gen_id"] == vigente


@pytest.mark.parametrize("politica,estado", [("DETENER", "ERROR"), ("CONSERVAR_ULTIMO_CORTE", "PASS")])
def test_r06_conector_activo_con_acreditacion_alterada(lab: Any, politica: str, estado: str) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    assert ref.ejecutar("t")["estado"] == "PASS"
    vigente = ref.store.vigente()["gen_id"]
    doc = _doc(paths["data"])
    doc["alcance"]["items"] += 1  # declaración que no coincide con el recálculo
    _put(paths["data"], doc)
    sim.config_conectores(paths["data"], fg_odbc={"politica_fallo": politica})
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")
    assert st["estado"] == estado
    if estado == "ERROR":
        assert any("NO_ACREDITADO" in m for m in st["motivos"]) and ref.store.vigente()["gen_id"] == vigente
    else:
        assert st["conectores"]["fg_odbc"]["estado"] == "NO_ACREDITADO" and st["fuentes"]["fg"] == "INV_STA289.TXT"
        assert ref.store.vigente()["sources"]["fg"]["conector"]["estado"] == "NO_ACREDITADO"


def test_r07_alcance_que_no_cubre_stockrooms_usables(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    filas = [*sim.filas_fg_posterior(), ("QZXA0001       ", "QH ", "5.000")]
    manual = [*_fg_manual_rows(), ("QZXA0001", "QH", "5.000")]
    manual[2] = (manual[2][0], "FG", "1.000")  # FG distinto: el alcance QH sí es PASS
    assert sim.acreditar_fg_verificable(paths["data"], filas, manual=manual, alcance="QH") == 0
    st = ref.ejecutar("t")
    assert st["estado"] == "ERROR" and "no cubre los stockrooms usables" in st["motivos"][0]


def test_r08_frozen_universo_notas_y_otros_conectores_intactos(lab: Any) -> None:
    paths, ref = lab
    before = ref.store.vigente()
    lock = ref.store.frozen_lock("2026-11")
    c = tp.client(ref)
    assert c.post("/api/cs", json={"item": "QZXA0016", "version": 0, "nota": "nota v0.3.2", "adelanto": "40"},
                  headers={"X-CSRF": tp.csrf(c)}).status_code == 200
    _vivo(ref, paths)
    for dia in ("2026-11-08", "2026-11-09", "2026-11-10"):
        ref.clock = sim.reloj(dia)
        assert ref.ejecutar("t")["estado"] == "PASS"
    after = ref.store.vigente()
    assert ref.store.frozen_lock("2026-11") == lock
    assert after["sources"]["frozen_r"] == before["sources"]["frozen_r"] and after["sources"]["frozen_p"] == before["sources"]["frozen_p"]
    rb, ra = tp.rows(before), tp.rows(after)
    assert set(rb) == set(ra)  # universo del mes: solo del Frozen
    assert all(tp.qty(rb[i]["frozen"]) == tp.qty(ra[i]["frozen"]) for i in rb)
    for nombre in before["escenarios"]:
        assert nombre in after["escenarios"]
    assert after["vista_operativa"] == before["vista_operativa"] and after["embarques"]["acreditado"] is False
    html = tp.client(ref).get("/junta").get_data(as_text=True)
    assert "nota v0.3.2" in html and 'value="40"' in html


def test_r09_desactivar_fg_vuelve_al_manual_y_conserva_auditoria(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    assert ref.ejecutar("t")["fuentes"]["fg"].startswith("fg_odbc:")
    sim.config_conectores(paths["data"], fg_odbc={"activo": False})
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")
    assert st["fuentes"]["fg"] == "INV_STA289.TXT" and ref.store.vigente()["sources"]["fg"]["conector"]["estado"] == "MANUAL"
    assert list((paths["data"] / "capturas" / "fg_odbc").glob("*.raw"))


# ================================================================ W. Windows
def _run(script: str, args: list[str], tmp: Path) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "PYTHONIOENCODING": "cp437", "PYTHONUTF8": "0", "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, str(APP / script), *args], capture_output=True, env=env, cwd=tmp, timeout=120)


def test_w01_salida_utf8_con_consola_oem(tmp_path: Path) -> None:
    data = tmp_path / "d"
    data.mkdir()
    sim.config_conectores(data)
    run = _run("preflight.py", ["--data", str(data), "--conector", "fg_odbc", "--nivel", "entorno"], tmp_path)
    err = run.stderr.decode("utf-8", "replace")
    assert "UnicodeEncodeError" not in err and "Traceback" not in err, err
    rep = json.loads(run.stdout.decode("utf-8"))
    assert any("→" in str(x["detalle"]) for x in rep["controles"])
    assert run.returncode in (0, 1) and rep["resultado"] in ("PASS", "FAIL")


def test_w02_conciliar_y_preview_con_consola_oem(tmp_path: Path) -> None:
    data = tmp_path / "d"
    data.mkdir()
    sim.config_conectores(data)
    directa = sim.captura_preflight_fg(data, _con_th(sim.filas_fg_posterior()))
    run = _run("conciliar.py", ["fg", "--data", str(data), "--manual", str(sim.manual_fg(data)), "--directa", str(directa),
                                "--alcance", "FG", "--acreditar"], tmp_path)
    assert "Traceback" not in run.stderr.decode("utf-8", "replace") and run.returncode == 0
    assert "NO son simultáneos" in run.stdout.decode("utf-8")
    run = _run("preview.py", ["refresh", "--data", str(tmp_path / "inexistente")], tmp_path)
    assert "UnicodeEncodeError" not in run.stderr.decode("utf-8", "replace")


LANZADOR = PKG / "NexusJunta.ps1"


def test_w03_lanzador_utf8_con_bom() -> None:
    body = LANZADOR.read_bytes()
    assert body.startswith(b"\xef\xbb\xbf")  # Windows PowerShell 5.1 lee sin BOM como ANSI
    text = body.decode("utf-8-sig")
    for frag in ("$env:PYTHONUTF8 = '1'", "$env:PYTHONIOENCODING = 'utf-8'", "[Console]::OutputEncoding = $utf8"):
        assert frag in text


def test_w04_lanzador_propaga_codigos_de_salida() -> None:
    lines = LANZADOR.read_text("utf-8-sig").splitlines()
    llamadas = [n for n, ln in enumerate(lines) if ln.strip().startswith("& uv")]
    assert len(llamadas) >= 5
    for n in llamadas:
        assert "$LASTEXITCODE" in lines[n + 1], lines[n]  # cada llamada a uv revisa su código de salida
        assert "return" not in lines[n]
    assert not [ln for ln in lines if ln.strip().startswith("uv run")]  # ninguna llamada sin revisar
    assert lines[-1].strip() == "exit $rc"
    assert any("catch" in ln for ln in lines) and any("$rc = 1" in ln for ln in lines)
    assert any("'--alcance'" in ln for ln in lines)
