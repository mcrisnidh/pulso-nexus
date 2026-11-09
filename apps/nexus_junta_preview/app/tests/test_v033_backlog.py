"""v0.3.3 — Backlog SSRS (29 OERAS) en el Refresh de la Junta (SIMULADORES, datos sintéticos).

B: acreditación verificable (fuente y contrato), fabricada / alterada / ajena / otra configuración.
R: consulta nueva en cada Refresh → Current; Frozen inmutable; FG v0.3.2 intacto.
F: fallos SSRS (XML, periodo, HTTP, 401/403, caída) con DETENER / CONSERVAR_ULTIMO_CORTE.
D: diagnóstico reproducible de discrepancias (sin normalizar ni convertir en cero).

Sin conexiones corporativas. Las comprobaciones usan ``_exige`` (no ``assert``) para no añadir
deuda a la línea base Ruff del componente.
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
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))

import conciliar  # noqa: E402
import dataset_demo as demo  # noqa: E402
import simuladores as sim  # noqa: E402
import test_conectores as tc  # noqa: E402
import test_preview as tp  # noqa: E402
from nexus_preview.conectores import ConectorError  # noqa: E402
from nexus_preview.conectores import acreditacion_backlog as ab  # noqa: E402
from nexus_preview.conectores import orquestador as orq  # noqa: E402

lab = tc.lab
ACR = ab.ARCHIVO


def _exige(condicion: object, mensaje: str = "") -> None:
    if not condicion:
        pytest.fail(mensaje or "condición no cumplida")


def _cfg(data: Path) -> dict[str, Any]:
    return json.loads((data / "conectores.json").read_text("utf-8"))["backlog_ssrs"]


def _verificar(data: Path) -> tuple[bool, list[str], dict[str, Any]]:
    return ab.verificar(data, _cfg(data))


def _doc(data: Path) -> dict[str, Any]:
    return json.loads((data / ACR).read_text("utf-8"))


def _put(data: Path, doc: dict[str, Any]) -> None:
    (data / ACR).write_text(json.dumps(doc), "utf-8")


def _carpeta(data: Path) -> Path:
    return data / "conciliaciones" / _doc(data)["carpeta"]


def _escribible(p: Path) -> Path:
    p.chmod(0o600)
    return p


@pytest.fixture
def acred(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    sim.config_conectores(data, backlog_ssrs={"activo": False}, fg_odbc={"activo": False})
    _exige(sim.acreditar_backlog_verificable(data) == 0, "la cadena sintética debe acreditar")
    return data


def _plan_sin(lineas: set[tuple[str, str]]) -> list[dict[str, Any]]:
    """Plan del corte posterior sin las líneas (pedido, línea) indicadas."""
    out = []
    for it in sim.plan_posterior():
        keep = [ln for ln in it["lines"] if (ln[0], ln[1]) not in lineas]
        if keep:
            out.append({**it, "lines": keep})
    return out


def _vivo(ref: Any, paths: dict[str, Path], **kw: Any) -> Any:
    return tc.live(ref, paths, **kw)


def _xml(plan: list[dict[str, Any]] | None = None) -> Any:
    raw = sim.xml_ssrs(plan if plan is not None else sim.plan_posterior())
    return lambda url: raw


def _falla(codigo: str) -> Any:
    def f(url: str) -> bytes:
        raise ConectorError(codigo, "simulado")

    return f


# ================================================================ B. acreditación
def test_b01_acreditacion_verificable_fuente_y_contrato_sin_equivalencia_comercial(
    acred: Path,
) -> None:
    doc = _doc(acred)
    _exige(doc["schema"] == ab.ESQUEMA and doc["fuente"] == "backlog_ssrs")
    _exige(doc["alcance"] == "FUENTE_Y_CONTRATO" and doc["resultado"] == "PASS")
    _exige(
        doc["equivalencia_comercial"] == "NOT_ESTABLISHED", "V26: no se certifica OE965 ↔ 29 OERAS"
    )
    _exige(doc["activa_conector"] is False and doc["origen_directa"].startswith("preflight/"))
    _exige(doc["lineas"] == len(ab.leer_manual(sim.manual_backlog(acred).read_bytes())[0]))
    informe = json.loads((_carpeta(acred) / "informe_privado.json").read_text("utf-8"))
    _exige(informe["diferencias"] == [] and all(informe["identidad_exacta"].values()))
    _exige(informe["cortes"]["simultaneos"] is False and informe["cortes"]["desfase_dias"] == 0)
    saneado = json.loads((_carpeta(acred) / "resumen_saneado.json").read_text("utf-8"))
    _exige("QZX" not in json.dumps(saneado), "el resumen saneado no lleva identidades")
    ok, motivos, info = _verificar(acred)
    _exige(ok, str(motivos))
    _exige(info["lineas"] == doc["lineas"])
    _exige(_cfg(acred)["activo"] is False, "acreditar no activa el conector")


def test_b02_directa_debe_ser_xml_original_y_configuracion_presente(tmp_path: Path) -> None:
    data = tmp_path / "d"
    data.mkdir()
    sim.config_conectores(data)
    raw = sim.captura_preflight_backlog(data)
    copia = tmp_path / "copia.xml"
    copia.write_bytes(raw.read_bytes())
    manual = str(sim.manual_backlog(data))
    args = ["backlog", "--data", str(data), "--manual", manual, "--acreditar", "--directa"]
    _exige(conciliar.main([*args, str(copia)]) == 2 and not (data / ACR).exists())
    csv_preflight = raw.with_suffix(".csv")
    csv_preflight.write_text("item,customer_key_exact\n", "utf-8")
    _exige(conciliar.main([*args, str(csv_preflight)]) == 2, "CSV derivado ≠ captura original")
    (data / "conectores.json").unlink()
    _exige(conciliar.main([*args, str(raw)]) == 2 and not (data / ACR).exists())


@pytest.mark.parametrize(
    "caso",
    [
        "v031_falsa",
        "hashes_inventados",
        "otra_fuente",
        "otro_alcance",
        "certifica_equivalencia",
        "no_pass",
    ],
)
def test_b03_acreditacion_fabricada_se_rechaza(acred: Path, caso: str) -> None:
    doc = _doc(acred)
    if caso == "v031_falsa":
        sim.acreditar_falsa(acred, "backlog_ssrs")
    else:
        cambios = {
            "hashes_inventados": {"informe_sha256": "b" * 64, "directa_sha256": "c" * 64},
            "otra_fuente": {"fuente": "fg_odbc"},
            "otro_alcance": {"alcance": "GLOBAL"},
            "certifica_equivalencia": {"equivalencia_comercial": "ESTABLISHED"},
            "no_pass": {"resultado": "DIFERENCIAS"},
        }[caso]
        _put(acred, {**doc, **cambios})
    ok, motivos, _ = _verificar(acred)
    _exige(not ok and motivos, caso)
    _exige(not orq.acreditado(acred, "backlog_ssrs", _cfg(acred))[0], caso)


@pytest.mark.parametrize(
    "caso",
    [
        "byte_informe",
        "informe_reescrito",
        "lineas_declaradas",
        "manual_evidencia",
        "directa_evidencia",
        "origen_borrado",
        "origen_alterado",
        "origen_sustituido",
    ],
)
def test_b04_acreditacion_alterada_se_rechaza(acred: Path, caso: str) -> None:
    doc = _doc(acred)
    folder = _carpeta(acred)
    informe_p = folder / "informe_privado.json"
    if caso == "byte_informe":
        informe_p.write_bytes(
            informe_p.read_bytes().replace(b'"lineas_manual"', b'"lineas_manuaL"')
        )
    elif caso == "informe_reescrito":
        inf = json.loads(informe_p.read_text("utf-8"))
        inf["resumen"]["lineas_directa"] += 1
        body = json.dumps(inf).encode("utf-8")
        informe_p.write_bytes(body)
        _put(acred, {**doc, "informe_sha256": hashlib.sha256(body).hexdigest()})
    elif caso == "lineas_declaradas":
        _put(acred, {**doc, "lineas": doc["lineas"] + 1})
    elif caso == "manual_evidencia":
        p = _escribible(next(folder.glob("manual_*")))
        p.write_bytes(p.read_bytes() + b"\n")
    elif caso == "directa_evidencia":
        p = _escribible(folder / "directa.xml")
        p.write_bytes(p.read_bytes().replace(b'Textbox45="', b'Textbox45="0', 1))
    elif caso == "origen_borrado":
        (acred / doc["origen_directa"]).unlink()
    elif caso == "origen_alterado":
        p = acred / doc["origen_directa"]
        p.write_bytes(p.read_bytes() + b" ")
    else:  # se sustituye la captura original por OTRA consulta válida (otros datos)
        (acred / doc["origen_directa"]).write_bytes(sim.xml_ssrs(sim.plan_posterior()[1:]))
    ok, motivos, _ = _verificar(acred)
    _exige(not ok and motivos, caso)


def _enlazar_directorio(enlace: Path, destino: Path) -> None:
    """Enlace de directorio REAL hacia ``destino``: junction (``mklink /J``) en Windows, que no
    requiere privilegios elevados; enlace simbólico en POSIX."""
    if os.name == "nt":
        orden = ["cmd.exe", "/d", "/c", "mklink", "/J", str(enlace), str(destino)]
        proceso = subprocess.run(orden, capture_output=True, text=True, check=False)
        _exige(proceso.returncode == 0, f"mklink /J falló: {proceso.stdout} {proceso.stderr}")
        _exige(enlace.is_junction(), "no se creó una junction real")
    else:
        enlace.symlink_to(destino, target_is_directory=True)
        _exige(enlace.is_symlink(), "no se creó un enlace simbólico real")
    _exige(enlace.resolve() == destino.resolve(), "el enlace no apunta al destino ajeno")


MOTIVO_CARPETA = "carpeta de conciliación inexistente o fuera de los datos"
MOTIVO_ORIGEN = "la captura SSRS original no existe en preflight/ o capturas/"


@pytest.mark.parametrize(
    "caso", ["otra_instalacion", "ruta_escapa", "ruta_absoluta", "enlace", "enlace_origen"]
)
def test_b05_acreditacion_ajena_o_ruta_insegura_se_rechaza(tmp_path: Path, caso: str) -> None:
    ajena = tmp_path / "ajena"
    ajena.mkdir()
    sim.config_conectores(ajena)
    _exige(sim.acreditar_backlog_verificable(ajena) == 0)
    data = tmp_path / "propia"
    data.mkdir()
    sim.config_conectores(data)
    doc = _doc(ajena)
    if caso == "otra_instalacion":
        shutil.copytree(ajena / "conciliaciones", data / "conciliaciones")
        shutil.copy(ajena / ACR, data / ACR)
    elif caso == "ruta_escapa":
        _put(data, {**doc, "carpeta": f"../../ajena/conciliaciones/{doc['carpeta']}"})
    elif caso == "ruta_absoluta":
        _put(data, {**doc, "carpeta": str(ajena / "conciliaciones" / doc["carpeta"])})
    elif caso == "enlace":  # carpeta de conciliación enlazada a la instalación ajena
        (data / "conciliaciones").mkdir()
        destino = ajena / "conciliaciones" / doc["carpeta"]
        _enlazar_directorio(data / "conciliaciones" / doc["carpeta"], destino)
        _enlazar_directorio(data / "preflight", ajena / "preflight")
        _put(data, doc)
    else:  # evidencia copiada; solo la captura ORIGINAL llega por un enlace a la ajena
        shutil.copytree(ajena / "conciliaciones", data / "conciliaciones")
        _enlazar_directorio(data / "preflight", ajena / "preflight")
        _put(data, doc)
    ok, motivos, _ = _verificar(data)
    _exige(not ok and motivos, caso)
    esperado = {"enlace": MOTIVO_CARPETA, "enlace_origen": MOTIVO_ORIGEN}.get(caso)
    if esperado is not None:  # el rechazo procede del control de rutas, no de otra causa
        _exige(motivos == [esperado], f"{caso}: {motivos}")


@pytest.mark.parametrize(
    ("caso", "motivo"),
    [("carpeta", MOTIVO_CARPETA), ("origen", MOTIVO_ORIGEN)],
    ids=["carpeta", "origen"],
)
def test_b05b_enlace_no_detectado_como_symlink_se_rechaza_por_contencion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caso: str, motivo: str
) -> None:
    """Semántica de una junction de Windows: ``Path.is_symlink()`` devuelve False. El rechazo
    debe venir igualmente de la contención (la ruta resuelta queda fuera de los datos)."""
    ajena = tmp_path / "ajena"
    ajena.mkdir()
    sim.config_conectores(ajena)
    _exige(sim.acreditar_backlog_verificable(ajena) == 0)
    data = tmp_path / "propia"
    data.mkdir()
    sim.config_conectores(data)
    doc = _doc(ajena)
    if caso == "carpeta":
        (data / "conciliaciones").mkdir()
        _enlazar_directorio(
            data / "conciliaciones" / doc["carpeta"], ajena / "conciliaciones" / doc["carpeta"]
        )
    else:
        shutil.copytree(ajena / "conciliaciones", data / "conciliaciones")
    _enlazar_directorio(data / "preflight", ajena / "preflight")
    _put(data, doc)
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    ok, motivos, _ = _verificar(data)
    _exige(not ok and motivos == [motivo], f"{caso}: {motivos}")


@pytest.mark.parametrize(
    ("cambio", "valida"),
    [
        ({"url_servidor": "http://otro-servidor/ReportServer"}, False),
        ({"ruta_reporte": "/Otra/Month End - Backlog Detail"}, False),
        ({"parametros": {"pKey": "XX"}}, False),
        ({"entidad": "0099"}, False),
        ({"moneda": "MXN"}, False),
        ({"cuenta_esperada": "otra.cuenta"}, False),
        ({"formato_clave_cliente": "{num}{seq}"}, False),
        ({"atributos": {"item": "item2"}}, False),
        ({"activo": True}, True),
        ({"politica_fallo": "DETENER"}, True),
        ({"timeout_s": 30}, True),
        ({"contingencia_max_horas": 6}, True),
    ],
)
def test_b06_huella_de_la_configuracion_ssrs(
    acred: Path, cambio: dict[str, Any], valida: bool
) -> None:
    sim.config_conectores(
        acred, backlog_ssrs={"activo": False, **cambio}, fg_odbc={"activo": False}
    )
    _exige(_verificar(acred)[0] is valida, str(cambio))


@pytest.mark.parametrize("caso", ["cliente_distinto", "item_minusculas", "linea_ausente"])
def test_b07_identidad_no_coincidente_no_acredita(tmp_path: Path, caso: str) -> None:
    data = tmp_path / "d"
    data.mkdir()
    sim.config_conectores(data)
    plan = sim.plan_posterior()
    primero = plan[0]
    if caso == "cliente_distinto":  # misma línea con otro sufijo de cliente: identidad distinta
        lines = [
            (*ln[:5], ln[5][:-1] + ("2" if ln[5][-1] != "2" else "3")) for ln in primero["lines"]
        ]
        plan = [{**primero, "lines": lines}, *plan[1:]]
    elif caso == "item_minusculas":
        plan = [{**primero, "item": primero["item"].lower()}, *plan[1:]]
    else:
        plan = [{**primero, "lines": primero["lines"][1:]}, *plan[1:]]
    _exige(sim.acreditar_backlog_verificable(data, plan) == 1)
    _exige(not (data / ACR).exists())
    informe = json.loads(
        next((data / "conciliaciones").glob("backlog_*/informe_privado.json")).read_text("utf-8")
    )
    _exige(informe["resultado"] == "DIFERENCIAS" and not all(informe["identidad_exacta"].values()))


# ================================================================ D. diagnóstico de discrepancias
def test_d01_diagnostico_clasifica_con_evidencia_sin_normalizar(tmp_path: Path) -> None:
    """Reproduce la forma del caso real (solo-manual, sin solo-directa) con datos sintéticos."""
    plan = sim.plan_posterior()
    cliente = plan[0]["lines"][0][5]
    sin_cliente = {(ln[0], ln[1]) for it in plan for ln in it["lines"] if ln[5] == cliente}
    con_otras = next(it for it in plan if len(it["lines"]) > 1 and it["lines"][0][5] != cliente)
    quitar = sin_cliente | {(con_otras["lines"][-1][0], con_otras["lines"][-1][1])}
    xml = tmp_path / "directa.xml"
    xml.write_bytes(sim.xml_ssrs(_plan_sin(quitar)))
    manual = sim.manual_backlog(tmp_path)
    out = conciliar.backlog(manual, xml, {})
    _exige(out["resumen"]["lineas_manual"] - out["resumen"]["lineas_directa"] == len(quitar))
    _exige(all(x["tipo"] == "SOLO_MANUAL" for x in out["diferencias"]))
    _exige(out["resumen"]["claves_cliente_solo_manual"] == 1)
    conteo = out["diagnostico"]["conteo"]
    _exige(conteo.get("SOLO_MANUAL:CLIENTE_AUSENTE") == len(sin_cliente), str(conteo))
    _exige(sum(conteo.values()) == len(quitar), str(conteo))
    _exige(float(out["resumen"]["variacion_neta_abierto"]) < 0)


def test_d02_discrepancia_no_acredita_y_backlog_sigue_inactivo(tmp_path: Path) -> None:
    data = tmp_path / "d"
    data.mkdir()
    sim.config_conectores(data, backlog_ssrs={"activo": False})
    linea = sim.plan_posterior()[0]["lines"][0]
    _exige(sim.acreditar_backlog_verificable(data, _plan_sin({(linea[0], linea[1])})) == 1)
    _exige(not (data / ACR).exists() and _cfg(data)["activo"] is False)
    _exige(orq.acreditado(data, "backlog_ssrs", _cfg(data))[0] is False)


# ================================================================ R. Refresh
def test_r01_refresh_consulta_ssrs_y_alimenta_current(lab: Any) -> None:
    paths, ref = lab
    before = ref.store.vigente()
    _vivo(ref, paths)
    st = ref.ejecutar("t")
    bc = st["conectores"]["backlog_ssrs"]
    _exige(st["estado"] == "PASS" and bc["estado"] == "CAPTURADO", str(st))
    gen = ref.store.vigente()
    cur = gen["sources"]["current_r"]
    _exige(cur["file"] == f"backlog_ssrs:{bc['archivo']}" and cur["origen"] == "CONSULTA_DIRECTA")
    _exige(
        cur["conector"]["estado"] == "CONSULTA_DIRECTA"
        and cur["instante"] == "2026-11-08T15:00:00Z"
    )
    _exige(gen["inputs"]["backlog_conector"]["corte"] == "2026-11-08T15:00:00Z")
    _exige(gen["sources"]["fg"]["origen"] == "CONSULTA_DIRECTA", "FG directo conservado")
    rb, ra = tp.rows(before), tp.rows(gen)
    _exige(any(rb[i]["actual"] != ra[i]["actual"] for i in rb), "Current refleja la consulta")
    html = tp.client(ref).get("/junta").get_data(as_text=True)
    _exige("Backlog Current: consulta directa SSRS de este Refresh" in html)
    _exige("FG: consulta directa AS400 de este Refresh" in html)


def test_r02_segundo_refresh_usa_el_segundo_corte(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    primero = ref.ejecutar("t")["conectores"]["backlog_ssrs"]
    gen1 = ref.store.vigente()
    linea = sim.plan_posterior()[0]["lines"][0]
    ref.deps["ssrs_fetch"] = _xml(_plan_sin({(linea[0], linea[1])}))
    ref.clock = sim.reloj("2026-11-09", "15:00:00")
    st = ref.ejecutar("t")
    segundo = st["conectores"]["backlog_ssrs"]
    _exige(st["estado"] == "PASS" and segundo["archivo"] != primero["archivo"])
    gen2 = ref.store.vigente()
    _exige(gen2["sources"]["current_r"]["file"] == f"backlog_ssrs:{segundo['archivo']}")
    _exige(gen2["sources"]["current_r"]["instante"] == "2026-11-09T15:00:00Z")
    _exige(gen2["sources"]["current_r"]["records"] == gen1["sources"]["current_r"]["records"] - 1)


def test_r03_consultas_identicas_son_cortes_distintos(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    a = ref.ejecutar("t")["conectores"]["backlog_ssrs"]
    ref.clock = sim.reloj("2026-11-08", "17:00:00")
    st = ref.ejecutar("t")
    b = st["conectores"]["backlog_ssrs"]
    _exige(
        b["reutilizada"] is False and b["archivo"] != a["archivo"] and b["sha256"] == a["sha256"]
    )
    _exige(b["instante_consulta_utc"] == "2026-11-08T17:00:00Z" and st["estado"] == "PASS")
    gen = ref.store.vigente()
    _exige(gen["inputs"]["backlog_conector"]["corte"] == "2026-11-08T17:00:00Z")
    _exige(len(list((paths["data"] / "capturas" / "backlog_ssrs").glob("*.raw"))) == 2)


def test_r04_r_manual_igual_o_posterior_no_desplaza_la_consulta(lab: Any) -> None:
    paths, ref = lab
    demo.corte_posterior(paths["src"], date="11/09/26", name="R_20261109.TXT")  # fecha POSTERIOR
    _vivo(ref, paths)
    st = ref.ejecutar("t")
    _exige(st["fuentes"]["current_r"].startswith("backlog_ssrs:"), str(st["fuentes"]))
    _exige(any("R_20261109.TXT" in w for w in ref.store.vigente()["warnings"]))


def test_r05_frozen_universo_notas_y_huellas_invariantes(lab: Any) -> None:
    paths, ref = lab
    before = ref.store.vigente()
    lock = json.dumps(ref.store.frozen_lock("2026-11"), sort_keys=True)
    c = tp.client(ref)
    nota = {"item": "QZXA0016", "version": 0, "nota": "nota v0.3.3", "adelanto": "33"}
    _exige(c.post("/api/cs", json=nota, headers={"X-CSRF": tp.csrf(c)}).status_code == 200)
    _vivo(ref, paths)
    for dia, plan in (("2026-11-08", None), ("2026-11-09", sim.plan_posterior()[2:])):
        ref.deps["ssrs_fetch"] = _xml(plan)
        ref.clock = sim.reloj(dia)
        _exige(ref.ejecutar("t")["estado"] == "PASS")
    after = ref.store.vigente()
    _exige(json.dumps(ref.store.frozen_lock("2026-11"), sort_keys=True) == lock, "lock Frozen")
    for k in ("frozen_r", "frozen_p"):
        _exige(after["sources"][k]["sha256"] == before["sources"][k]["sha256"], k)
        _exige(after["sources"][k]["file"] == before["sources"][k]["file"], k)
    rb, ra = tp.rows(before), tp.rows(after)
    _exige(set(rb) == set(ra), "universo mensual = Frozen")
    _exige(all(tp.qty(rb[i]["frozen"]) == tp.qty(ra[i]["frozen"]) for i in rb), "Frozen por ITEM")
    _exige(after["vista_operativa"] == before["vista_operativa"])
    _exige(after["embarques"]["acreditado"] is False)
    html = tp.client(ref).get("/junta").get_data(as_text=True)
    _exige("nota v0.3.3" in html and 'value="33"' in html)


def test_r06_fg_directo_y_su_acreditacion_intactos(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    acr_fg = (paths["data"] / "acreditacion_fg_odbc.json").read_bytes()
    ref.deps["ssrs_fetch"] = _falla("SSRS_NO_DISPONIBLE")
    st = ref.ejecutar("t")
    _exige(st["conectores"]["fg_odbc"]["estado"] == "CAPTURADO", str(st["conectores"]))
    _exige(ref.store.vigente()["sources"]["fg"]["conector"]["estado"] == "CONSULTA_DIRECTA")
    doc = _doc(paths["data"])
    _put(paths["data"], {**doc, "lineas": 0})  # acreditación Backlog alterada
    ref.clock = sim.reloj("2026-11-09")
    ref.deps["ssrs_fetch"] = _xml()
    st = ref.ejecutar("t")
    _exige(st["conectores"]["backlog_ssrs"]["estado"] == "NO_ACREDITADO")
    _exige(st["conectores"]["fg_odbc"]["estado"] == "CAPTURADO")
    _exige((paths["data"] / "acreditacion_fg_odbc.json").read_bytes() == acr_fg)


def test_r07_sin_escrituras_fuera_del_laboratorio(lab: Any, tmp_path: Path) -> None:
    paths, ref = lab
    _vivo(ref, paths)

    def foto() -> dict[str, str]:
        return {
            str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in tmp_path.rglob("*")
            if p.is_file() and paths["data"] not in p.parents
        }

    antes = foto()
    for dia in ("2026-11-08", "2026-11-09"):
        ref.clock = sim.reloj(dia)
        _exige(ref.ejecutar("t")["estado"] == "PASS")
    _exige(foto() == antes, "el Refresh solo escribe en la carpeta de datos")


# ================================================================ F. fallos
@pytest.mark.parametrize(
    ("fetch", "codigo"),
    [
        (lambda url: b"<Report", "SSRS_XML_INVALIDO"),
        (
            lambda url: sim.xml_ssrs(sim.plan_posterior(), periodo="10/2026"),
            "SSRS_PERIODO_DISTINTO",
        ),
        (_falla("SSRS_DESCARGA_FALLIDA"), "SSRS_DESCARGA_FALLIDA"),
        (_falla("SSRS_NO_DISPONIBLE"), "SSRS_NO_DISPONIBLE"),
        (_falla("AUTENTICACION_REQUERIDA"), "AUTENTICACION_REQUERIDA"),
    ],
    ids=["xml_invalido", "periodo", "http_invalida", "caida", "401_403"],
)
def test_f01_fallo_conservar_marca_backlog_anterior(lab: Any, fetch: Any, codigo: str) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    _exige(ref.ejecutar("t")["estado"] == "PASS")
    previo = ref.store.vigente()["sources"]["current_r"]
    ref.deps["ssrs_fetch"] = fetch
    ref.clock = sim.reloj("2026-11-09", "15:00:00")
    st = ref.ejecutar("t")
    bc = st["conectores"]["backlog_ssrs"]
    _exige(bc["estado"] == "FALLO" and bc["codigo"] == codigo and st["parcial"] is True, str(bc))
    cur = ref.store.vigente()["sources"]["current_r"]
    _exige(
        cur["file"] == previo["file"] and cur["sha256"] == previo["sha256"], "último corte válido"
    )
    _exige(
        cur["conector"]["estado"] == "CONTINGENCIA" and cur["conector"]["antiguedad_min"] == 1440
    )
    html = tp.client(ref).get("/junta").get_data(as_text=True)
    _exige("BACKLOG ANTERIOR" in html and "Actualización parcial" in html)
    _exige("Backlog Current: consulta directa SSRS de este Refresh" not in html)


def test_f02_fallo_con_detener_conserva_la_generacion_vigente(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths, cfg={"backlog_ssrs": {"politica_fallo": "DETENER"}})
    _exige(ref.ejecutar("t")["estado"] == "PASS")
    vigente = ref.store.vigente()["gen_id"]
    ref.deps["ssrs_fetch"] = _falla("SSRS_NO_DISPONIBLE")
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")  # FG sí captura: no se publica una generación PARCIAL inconsistente
    _exige(st["estado"] == "ERROR" and any("DETENER" in m for m in st["motivos"]), str(st))
    _exige(ref.store.vigente()["gen_id"] == vigente)


@pytest.mark.parametrize(("horas", "estado"), [(12, "ERROR"), (48, "PASS")])
def test_f03_limite_de_contingencia_backlog(lab: Any, horas: int, estado: str) -> None:
    paths, ref = lab
    _vivo(ref, paths, cfg={"backlog_ssrs": {"contingencia_max_horas": horas}})
    _exige(ref.ejecutar("t")["estado"] == "PASS")
    vigente = ref.store.vigente()["gen_id"]
    ref.deps["ssrs_fetch"] = _falla("SSRS_NO_DISPONIBLE")
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")
    _exige(st["estado"] == estado, str(st.get("motivos")))
    if estado == "ERROR":
        _exige(ref.store.vigente()["gen_id"] == vigente)


@pytest.mark.parametrize(
    ("politica", "estado"), [("DETENER", "ERROR"), ("CONSERVAR_ULTIMO_CORTE", "PASS")]
)
def test_f04_backlog_activo_con_acreditacion_invalida(lab: Any, politica: str, estado: str) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    _exige(ref.ejecutar("t")["estado"] == "PASS")
    sim.config_conectores(
        paths["data"], backlog_ssrs={"politica_fallo": politica, "entidad": "0099"}
    )
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")
    _exige(st["estado"] == estado, str(st.get("motivos")))
    if estado == "ERROR":
        _exige(any("backlog_ssrs: NO_ACREDITADO" in m for m in st["motivos"]), str(st["motivos"]))
    else:
        _exige(st["conectores"]["backlog_ssrs"]["estado"] == "NO_ACREDITADO")
    if estado == "PASS":
        cur = ref.store.vigente()["sources"]["current_r"]
        _exige(cur["origen"] == "MANUAL" and cur["conector"]["estado"] == "NO_ACREDITADO", str(cur))


def test_f05_401_con_importacion_interactiva_se_identifica(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    imp = paths["data"] / "ssrs-import"
    imp.mkdir()
    (imp / "Backlog_SSRS_20261108_1000.xml").write_bytes(sim.xml_ssrs(sim.plan_posterior()))
    ref.deps["ssrs_fetch"] = _falla("AUTENTICACION_REQUERIDA")
    st = ref.ejecutar("t")
    _exige(
        st["conectores"]["backlog_ssrs"]["modo"] == "importacion_interactiva", str(st["conectores"])
    )
    cur = ref.store.vigente()["sources"]["current_r"]
    _exige(cur["conector"]["estado"] == "IMPORTACION_INTERACTIVA", str(cur.get("conector")))
    html = tp.client(ref).get("/junta").get_data(as_text=True)
    _exige("importación interactiva" in html)


def test_f06_conector_inactivo_sin_cambios_y_plantilla_inactiva(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths, cfg={"backlog_ssrs": {"activo": False}})
    st = ref.ejecutar("t")
    _exige(st["conectores"]["backlog_ssrs"]["estado"] == "INACTIVO")
    _exige(ref.store.vigente()["sources"]["current_r"]["conector"]["estado"] == "MANUAL")
    plantilla = json.loads((APP / "conectores.plantilla.json").read_text("utf-8"))
    _exige(plantilla["backlog_ssrs"]["activo"] is False and plantilla["fg_odbc"]["activo"] is False)
