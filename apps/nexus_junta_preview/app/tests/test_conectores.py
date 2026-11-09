"""Pruebas de los conectores v0.3 con SIMULADORES (sin sistemas corporativos, datos sintéticos)."""

from __future__ import annotations

import json
import pickle
import re
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

APP = Path(__file__).resolve().parents[1]
REPO = Path.cwd()
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))

import conciliar  # noqa: E402
import dataset_demo as demo  # noqa: E402
import preflight  # noqa: E402
import simuladores as sim  # noqa: E402
import test_preview as tp  # noqa: E402

from nexus_preview.conectores import ConectorError, embarques, fg_odbc, odbc, ssrs  # noqa: E402
from nexus_preview.conectores import credenciales as cred  # noqa: E402
from nexus_preview.conectores import orquestador as orq  # noqa: E402
from nexus_preview.generaciones import PreviewRefresh  # noqa: E402

D = Decimal


def deps(ssrs_xml: bytes | None = None, fg_rows: list[Any] | None = None, emb_rows: list[Any] | None = None, **over: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"proveedor": sim.proveedor, "usuario": "ucbaa"}
    if ssrs_xml is not None:
        d["ssrs_fetch"] = lambda url: ssrs_xml
    if fg_rows is not None:
        d["conn_fg"] = sim.Conexion(fg_rows)
        d["fabrica_fg"] = sim.fabrica(d["conn_fg"])
    if emb_rows is not None:
        d["conn_emb"] = sim.Conexion(emb_rows)
        d["fabrica_embarques"] = sim.fabrica(d["conn_emb"])
    d.update(over)
    return d


@pytest.fixture
def lab(tmp_path: Path) -> tuple[dict[str, Path], PreviewRefresh]:
    paths, ref = tp.make_lab(tmp_path)
    ref.clock = sim.reloj("2026-11-01", "14:00:00")
    assert ref.ejecutar("test")["estado"] == "PASS"  # generación inicial con archivos manuales
    return paths, ref


def live(ref: PreviewRefresh, paths: dict[str, Path], fecha: str = "2026-11-08", **kw: Any) -> PreviewRefresh:
    """Refresh con conectores activos y acreditados (simuladores)."""
    sim.config_conectores(paths["data"], **kw.pop("cfg", {}))
    for f in ("backlog_ssrs", "fg_odbc"):
        sim.acreditar(paths["data"], f)
    ref.deps = deps(sim.xml_ssrs(sim.plan_posterior()), sim.filas_fg_posterior(), **kw)
    ref.clock = sim.reloj(fecha)
    return ref


# ---------------------------------------------------------------- identidades y credenciales
def test_k01_credencial_no_se_intercambia_ni_se_revela() -> None:
    c = cred.obtener("PULSO_NEXUS/fg", "remcb", sim.proveedor)
    assert c.usuario == "remcb" and "Sintetico" not in repr(c) and "Sintetico" not in f"{c.secreto}"
    with pytest.raises(ConectorError) as e:
        cred.obtener("PULSO_NEXUS/cruzada", "remcb", sim.proveedor)  # credencial de otra cuenta
    assert e.value.code == "CUENTA_NO_COINCIDE"
    with pytest.raises(TypeError):
        pickle.dumps(c.secreto)
    assert sim.SECRETO_FG not in cred.sanear(f"error PWD={sim.SECRETO_FG};", c.secreto)
    assert cred.obtener("PULSO_NEXUS/embarques", "sm0003", sim.proveedor).usuario.endswith("sm0003")


def test_k02_configuracion_sin_secretos_y_sin_credencial_compartida(tmp_path: Path) -> None:
    (tmp_path / "conectores.json").write_text(json.dumps({"schema": "nexus.preview.conectores/1", "fg_odbc": {"password": "x"}}), "utf-8")
    with pytest.raises(ConectorError) as e:
        orq.cargar_config(tmp_path)
    assert e.value.code == "CONFIG_CON_SECRETOS"
    sim.config_conectores(tmp_path, embarques={"credencial": "PULSO_NEXUS/fg"})
    with pytest.raises(ConectorError) as e:
        orq.cargar_config(tmp_path)
    assert e.value.code == "CREDENCIAL_COMPARTIDA"
    with pytest.raises(ConectorError) as e:
        odbc.cadena({"dsn": "X", "atributos_conexion": {"PWD": "x"}}, None)
    assert e.value.code == "CONFIG_CON_SECRETOS"


# ---------------------------------------------------------------- Backlog SSRS
def test_k03_ssrs_normaliza_al_contrato_y_concilia_con_el_R_manual(tmp_path: Path) -> None:
    records, meta = ssrs.parse(sim.xml_ssrs(sim.plan_posterior()), {}, "2026-11")
    assert meta["lineas"] == len(records)
    src = tmp_path / "src"
    src.mkdir()
    manual = demo.corte_posterior(src, date="11/08/26", name="R_20261108.TXT")
    from nexus.core.stage import contract_csv

    directa = tmp_path / "directa.csv"
    directa.write_bytes(contract_csv("oeras_29", records))
    out = conciliar.backlog(manual, directa)
    assert out["diferencias"] == [] and out["por_item"] == []  # identidad exacta (clave con espacios)
    assert out["resumen"]["claves_cliente_solo_manual"] == 0


@pytest.mark.parametrize(
    ("mutar", "codigo"),
    [
        (lambda x: x.replace(b"Month End - Backlog Detail", b"Otro"), "SSRS_REPORTE_EQUIVOCADO"),
        (lambda x: x.replace(b'textbox1="11/2026"', b'textbox1="10/2026"'), "SSRS_PERIODO_DISTINTO"),
        (lambda x: b"<!DOCTYPE x [<!ENTITY a 'b'>]>" + x, "SSRS_XML_INSEGURO"),
        (lambda x: x.replace(b'Textbox45="', b'Textbox45="9', 1), "SSRS_CALIDAD_FALLIDA"),
        (lambda x: x.replace(b'entity_id="0036"', b'entity_id="0099"', 1), "SSRS_CALIDAD_FALLIDA"),
        (lambda x: x.replace(b"</Details>", x[x.index(b"<Detail "):x.index(b"/>") + 2] + b"</Details>"), "SSRS_CALIDAD_FALLIDA"),
        (lambda x: x[:200], "SSRS_XML_INVALIDO"),
    ],
)
def test_k04_ssrs_falla_cerrado(mutar: Any, codigo: str) -> None:
    with pytest.raises(ConectorError) as e:
        ssrs.parse(mutar(sim.xml_ssrs(sim.plan_posterior())), {}, "2026-11")
    assert e.value.code == codigo


def test_k05_ssrs_url_solo_configurada_y_sin_parametros_reservados() -> None:
    with pytest.raises(ConectorError):
        ssrs.render_url({"url_servidor": "http://<servidor>/ReportServer", "ruta_reporte": "/x"})
    with pytest.raises(ConectorError):
        ssrs.render_url({"url_servidor": "http://s/ReportServer", "ruta_reporte": "/x", "parametros": {"rs:Command": "x"}})
    url = ssrs.render_url({"url_servidor": "http://s/ReportServer", "ruta_reporte": "/Com/Month End - Backlog Detail", "parametros": {"Mes": "11"}})
    assert url.startswith("http://s/ReportServer?/Com/Month%20End%20-%20Backlog%20Detail&") and "rs%3AFormat=XML" in url


def test_k06_ssrs_autenticacion_interactiva_y_usuario(lab: Any) -> None:
    paths, ref = lab
    sim.config_conectores(paths["data"], fg_odbc={"activo": False})
    sim.acreditar(paths["data"], "backlog_ssrs")

    def pide_login(url: str) -> bytes:
        raise ConectorError("AUTENTICACION_REQUERIDA", "inicie sesión")

    ref.deps = deps(ssrs_fetch=pide_login)
    st = ref.ejecutar("t")
    assert st["conectores"]["backlog_ssrs"]["estado"] == "FALLO" and st["parcial"] is True
    assert st["conectores"]["backlog_ssrs"]["codigo"] == "AUTENTICACION_REQUERIDA"
    imp = paths["data"] / "ssrs-import"
    imp.mkdir()
    (imp / "Backlog_SSRS_20261108_0930.xml").write_bytes(sim.xml_ssrs(sim.plan_posterior()))  # exportado por el usuario
    st = ref.ejecutar("t")
    assert st["conectores"]["backlog_ssrs"]["estado"] == "CAPTURADO" and st["conectores"]["backlog_ssrs"]["modo"] == "importacion_interactiva"
    assert st["fuentes"]["current_r"].startswith("backlog_ssrs:")
    ref.deps = deps(sim.xml_ssrs(sim.plan_posterior()), usuario="otro")
    assert ref.ejecutar("t")["conectores"]["backlog_ssrs"]["codigo"] == "SSRS_USUARIO_DISTINTO"


# ---------------------------------------------------------------- FG ODBC
def test_k07_fg_consulta_solo_lectura_identidad_exacta_y_desconocidos() -> None:
    q = fg_odbc.sql({})
    assert q.startswith("SELECT PNUM60, STRC60, SPHY60 FROM AULT2F2PRD.STKBAL WHERE CONO60 = ? AND SPHY60 <> 0")
    assert "FOR READ ONLY" in q
    with pytest.raises(ConectorError):
        fg_odbc.sql({"tabla": "STKBAL; DROP TABLE X"})
    rows = [(" QZX A1  ", "FG  ", "10.000"), ("QZXA2", "FG", None), ("QZXA3", "QH", "-5.000")]
    recs = fg_odbc.normalizar(rows)
    assert {r["item"] for r in recs} == {" QZX A1", "QZXA2", "QZXA3"}  # solo relleno a la derecha
    assert next(r for r in recs if r["item"] == "QZXA2")["qty_physical"] == ""  # desconocido, no 0
    with pytest.raises(ConectorError) as e:
        fg_odbc.normalizar([("A", "FG", "1"), ("A ", "FG", "2")])
    assert e.value.code == "FG_DUPLICADO"


def test_k08_fg_concilia_por_item_y_stockroom(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    manual = demo.fg_posterior(src)
    conn = sim.Conexion(sim.filas_fg_posterior())
    raw, meta = fg_odbc.capturar({"dsn": "DB2LOCAL", "credencial": "PULSO_NEXUS/fg", "cuenta_esperada": "remcb"}, sim.proveedor, sim.fabrica(conn))
    assert conn.rollbacks == 1 and conn.cerrada and conn.ejecutadas[0][1] == ("36",)
    assert "UID=remcb" in conn.cadenas[0] and sim.SECRETO_FG not in raw.decode() and sim.SECRETO_FG not in json.dumps(meta)
    directa = tmp_path / "fg.csv"
    directa.write_bytes(raw)
    assert conciliar.fg(manual, directa, "America/Mexico_City")["diferencias"] == []
    rows = sim.filas_fg_posterior()
    rows[0] = (rows[0][0], rows[0][1], "1.000")
    directa.write_bytes(fg_odbc.capturar({"dsn": "D", "credencial": "PULSO_NEXUS/fg", "cuenta_esperada": "remcb"}, sim.proveedor, sim.fabrica(sim.Conexion(rows)))[0])
    out = conciliar.fg(manual, directa, "America/Mexico_City")
    assert len(out["diferencias"]) == 1 and len(out["por_item"]) == 1  # por ITEM, no solo total


def test_k09_error_odbc_saneado(tmp_path: Path) -> None:
    conn = sim.Conexion([], falla=True)
    with pytest.raises(ConectorError) as e:
        fg_odbc.capturar({"dsn": "D", "credencial": "PULSO_NEXUS/fg", "cuenta_esperada": "remcb"}, sim.proveedor, sim.fabrica(conn))
    assert e.value.code == "ODBC_CONSULTA_FALLIDA" and sim.SECRETO_FG not in str(e.value)
    assert conn.rollbacks == 1 and conn.cerrada


# ---------------------------------------------------------------- Embarques (facturación CFDI)
def test_k10_embarques_guardas_y_nulos(tmp_path: Path) -> None:
    c = {"controlador": "ODBC Driver 18 for SQL Server", "servidor": "s", "base": "ASBG", "credencial": "PULSO_NEXUS/embarques", "cuenta_esperada": "sm0003"}
    import datetime as dt

    with pytest.raises(ConectorError) as e:
        embarques.capturar({**c, "atributos_conexion": {"TrustServerCertificate": "yes"}}, "2026-11", dt.date(2026, 11, 8), sim.proveedor)
    assert e.value.code == "EMBARQUES_CERTIFICADO_NO_VERIFICADO"
    conn = sim.Conexion([("QZXA0016", "300", 2, 2, 0), ("QZXA0010", None, 1, 1, 1)])
    raw, meta = embarques.capturar(c, "2026-11", dt.date(2026, 11, 8), sim.proveedor, sim.fabrica(conn))
    sql, params = conn.ejecutadas[-1]
    assert "MF_CFD" in sql and "MF_DETCFD" in sql and "UPPER" not in sql and params == (dt.date(2026, 11, 1), dt.date(2026, 11, 9))
    assert "ApplicationIntent=ReadOnly" in conn.cadenas[0] and "Encrypt=yes" in conn.cadenas[0]
    cap = embarques.leer_captura(raw)
    assert cap["QZXA0016"]["facturado_cfdi"] == "300" and cap["QZXA0010"]["facturado_cfdi"] == ""  # nulo: desconocido
    assert "no acredita embarque físico" in meta["naturaleza"]
    with pytest.raises(ConectorError) as e:
        embarques.capturar(c, "2026-11", dt.date(2026, 11, 8), sim.proveedor, sim.fabrica(sim.Conexion([], base="OTRA")))
    assert e.value.code == "EMBARQUES_BASE_DISTINTA"


def test_k11_embarques_no_acreditados_siguen_nd(lab: Any) -> None:
    paths, ref = lab
    live(ref, paths, cfg={"embarques": {"activo": True}}, emb_rows=[("QZXA0016", "300", 2, 2, 0)])
    st = ref.ejecutar("t")
    assert st["conectores"]["embarques"]["estado"] == "CAPTURADO"
    gen = ref.store.vigente()
    assert gen["embarques"]["acreditado"] is False and all(r["embarques"] is None for r in gen["escenarios"]["A"]["rows"])
    html = tp.client(ref).get("/junta?q=QZXA0016").get_data(as_text=True)
    row = html[html.index('data-item="QZXA0016"'):]
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
    assert "N/D" in cells[7] and "N/D" in cells[8] and "N/D" in cells[12]  # Programa, Embarques, Faltante


def test_k12_embarques_acreditados_alimentan_programa_v310(lab: Any) -> None:
    paths, ref = lab
    (paths["priv"] / "aprobadores.json").write_text(json.dumps({"approvers": ["aprobador.sintetico"]}), "utf-8")
    cfgp = json.loads((paths["data"] / "preview.json").read_text("utf-8"))
    cfgp["approvers"] = str(paths["priv"] / "aprobadores.json")
    (paths["data"] / "preview.json").write_text(json.dumps(cfgp), "utf-8")
    # v0.3.1: la acreditación debe ser VERIFICABLE (evidencia y conciliación PASS reales en disco);
    # la versión anterior de esta prueba usaba hashes inventados, que ahora se rechazan (ver test_v031).
    live(ref, paths, cfg={"embarques": {"activo": True}}, emb_rows=[("QZXA0016", "300", 2, 2, 0)])
    assert ref.ejecutar("t")["estado"] == "PASS"  # captura de embarques (aún no acreditada)
    assert sim.acreditar_embarques_verificable(paths["data"], conciliar.main)["_rc_conciliacion"] == 0
    ref.clock = sim.reloj("2026-11-08", "16:00:00")
    assert ref.ejecutar("t")["estado"] == "PASS"
    gen = ref.store.vigente()
    row = next(r for r in gen["escenarios"]["A"]["rows"] if r["item"] == "QZXA0016")
    assert gen["embarques"]["acreditado"] is True and row["embarques"] == "300"
    c = tp.client(ref)
    assert c.post("/api/cs", json={"item": "QZXA0016", "version": 0, "adelanto": "0"}, headers={"X-CSRF": tp.csrf(c)}).status_code == 200
    html = c.get("/junta?q=QZXA0016").get_data(as_text=True)
    frozen = D(row["frozen"]["value"])
    assert f"{int(max(D(0), frozen - 300)):,}" in html  # Programa = max(0, Frozen + Adelanto − Embarques)


# ---------------------------------------------------------------- integración con Refresh
def test_k13_refresh_en_vivo_actualiza_current_y_fg_sin_tocar_frozen_ni_notas(lab: Any) -> None:
    paths, ref = lab
    before = ref.store.vigente()
    lock = ref.store.frozen_lock("2026-11")
    c = tp.client(ref)
    assert c.post("/api/cs", json={"item": "QZXA0016", "version": 0, "nota": "nota v0.3", "adelanto": "75"}, headers={"X-CSRF": tp.csrf(c)}).status_code == 200
    live(ref, paths)
    st = ref.ejecutar("t")
    assert st["estado"] == "PASS" and not st["parcial"]
    assert st["fuentes"]["current_r"].startswith("backlog_ssrs:") and st["fuentes"]["fg"].startswith("fg_odbc:")
    after = ref.store.vigente()
    assert ref.store.frozen_lock("2026-11") == lock and after["sources"]["frozen_r"] == before["sources"]["frozen_r"]
    assert after["sources"]["current_r"]["origen"] == "CONSULTA_DIRECTA" and after["sources"]["current_r"]["document_date"] == "2026-11-08"
    rb, ra = tp.rows(before), tp.rows(after)
    assert set(rb) == set(ra) and "QZXA0999" not in ra  # Current no amplía el universo
    assert all(tp.qty(rb[i]["frozen"]) == tp.qty(ra[i]["frozen"]) for i in rb)
    assert ra["QZXA0016"]["actual"] != rb["QZXA0016"]["actual"] and ra["QZXA0016"]["fg"] != rb["QZXA0016"]["fg"]
    assert after["vista_operativa"] == before["vista_operativa"] and after["aprobaciones"] == before["aprobaciones"]
    html = tp.client(ref).get("/junta").get_data(as_text=True)
    assert "nota v0.3" in html and 'value="75"' in html
    again = ref.ejecutar("t")  # mismos datos en la fuente: capturas reutilizadas
    assert again["estado"] == "NO_CHANGE" and again["conectores"]["fg_odbc"]["reutilizada"] is True


def test_k14_politica_parcial_y_detener(lab: Any) -> None:
    paths, ref = lab
    live(ref, paths)
    assert ref.ejecutar("t")["estado"] == "PASS"
    gen = ref.store.vigente()
    ref.deps["fabrica_fg"] = sim.fabrica(sim.Conexion([], falla=True))
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")
    assert st["parcial"] is True and st["conectores"]["fg_odbc"]["estado"] == "FALLO"
    assert st["fuentes"]["fg"] == gen["sources"]["fg"]["file"]  # se conserva el último FG válido (no vacío)
    assert any("ACTUALIZACIÓN PARCIAL" in a for a in st["advertencias"])
    c = tp.client(ref)
    assert c.get("/refresh/estado").get_json()["etiqueta"] == "Actualización parcial"
    assert "Actualización parcial" in c.get("/junta").get_data(as_text=True)
    sim.config_conectores(paths["data"], fg_odbc={"politica_fallo": "DETENER"})
    vigente = ref.store.vigente()["gen_id"]
    st = ref.ejecutar("t")
    assert st["estado"] == "ERROR" and any("DETENER" in m for m in st["motivos"])
    assert ref.store.vigente()["gen_id"] == vigente


def test_k15_conector_sin_acreditacion_no_se_usa(lab: Any) -> None:
    paths, ref = lab
    sim.config_conectores(paths["data"])
    ref.deps = deps(sim.xml_ssrs(sim.plan_posterior()), sim.filas_fg_posterior())
    st = ref.ejecutar("t")
    assert st["conectores"]["backlog_ssrs"]["estado"] == "NO_ACREDITADO" and st["conectores"]["fg_odbc"]["estado"] == "NO_ACREDITADO"
    assert not (paths["data"] / "capturas").exists()


def test_k16_captura_alterada_detiene_y_conserva(lab: Any) -> None:
    paths, ref = lab
    live(ref, paths)
    assert ref.ejecutar("t")["estado"] == "PASS"
    gen = ref.store.vigente()["gen_id"]
    f = next((paths["data"] / "capturas" / "fg_odbc").glob("*.csv"))
    f.chmod(0o600)
    f.write_bytes(f.read_bytes() + b"QZXA0001,FG,999\n")
    st = ref.ejecutar("t")
    assert st["estado"] == "ERROR" and "CAPTURA_ALTERADA" in st["motivos"][0]
    assert ref.store.vigente()["gen_id"] == gen


def test_k17_ssrs_sin_manual_primera_captura_es_frozen(tmp_path: Path) -> None:
    paths, ref = tp.make_lab(tmp_path)
    (paths["src"] / "R_20261101.TXT").unlink()  # sin backlog manual: solo consulta directa
    sim.config_conectores(paths["data"], fg_odbc={"activo": False})
    sim.acreditar(paths["data"], "backlog_ssrs")
    ref.deps = deps(sim.xml_ssrs([{"item": i["item"], "lines": i["lines"]} for i in demo._plan()]))
    ref.clock = sim.reloj("2026-11-01", "15:00:00")
    assert ref.ejecutar("t")["estado"] == "PASS"
    first = ref.store.frozen_lock("2026-11")["oe965"]
    ref.deps = deps(sim.xml_ssrs(sim.plan_posterior()))
    ref.clock = sim.reloj("2026-11-08")
    st = ref.ejecutar("t")
    assert st["estado"] == "PASS" and ref.store.frozen_lock("2026-11")["oe965"] == first
    gen = ref.store.vigente()
    assert gen["sources"]["frozen_r"]["file"] == first["file"] and gen["sources"]["current_r"]["file"] != first["file"]


def test_k18_doble_clic_con_conector_lento(lab: Any) -> None:
    paths, ref = lab
    gate = threading.Event()
    live(ref, paths)
    xml = sim.xml_ssrs(sim.plan_posterior())

    def lento(url: str) -> bytes:
        gate.wait(10)
        return xml

    ref.deps["ssrs_fetch"] = lento
    c = tp.client(ref)
    token = tp.csrf(c)
    assert c.post("/refresh", headers={"X-CSRF": token}).status_code == 202
    assert c.post("/refresh", headers={"X-CSRF": token}).status_code == 409
    gate.set()
    for _ in range(200):
        if c.get("/refresh/estado").get_json()["estado"] != "RUNNING":
            break
        time.sleep(0.05)
    assert c.get("/refresh/estado").get_json()["estado"] == "PASS"


def test_k19_ningun_secreto_en_datos_ni_estados(lab: Any) -> None:
    paths, ref = lab
    live(ref, paths, cfg={"embarques": {"activo": True}}, emb_rows=[("QZXA0016", "300", 2, 2, 0)])
    ref.ejecutar("t")
    ref.deps["fabrica_fg"] = sim.fabrica(sim.Conexion([], falla=True))
    ref.ejecutar("t")
    for p in paths["data"].rglob("*"):
        if p.is_file():
            body = p.read_bytes()
            assert sim.SECRETO_FG.encode() not in body and sim.SECRETO_EMB.encode() not in body, p.name


# ---------------------------------------------------------------- preflight y conciliación
def test_k20_preflight_por_conector_y_autorizacion(lab: Any, capsys: Any) -> None:
    paths, ref = lab
    sim.config_conectores(paths["data"])
    d = paths["data"]
    assert preflight.main(["--data", str(d), "--conector", "fg_odbc", "--nivel", "consulta"], deps(fg_rows=[])) == 2  # sin --autorizo
    rc = preflight.main(["--data", str(d), "--conector", "fg_odbc", "--nivel", "consulta", "--autorizo"],
                        {**deps(), "fabrica": sim.fabrica(sim.Conexion(sim.filas_fg_posterior()))})
    capsys.readouterr()
    assert rc in (0, 1)  # pyodbc puede no existir en el entorno de desarrollo: el control lo informa
    assert list((d / "preflight" / "fg_odbc").glob("*.csv")) and not (d / "capturas").exists()
    rc = preflight.main(["--data", str(d), "--conector", "backlog_ssrs"], deps(usuario="ucbaa", powershell="powershell.exe"))
    report = json.loads(capsys.readouterr().out)
    assert rc == 0 and report["resultado"] == "PASS"
    rc = preflight.main(["--data", str(d), "--conector", "embarques", "--nivel", "conexion", "--autorizo"],
                        {**deps(), "fabrica": sim.fabrica(sim.Conexion([]))})
    rep = json.loads(capsys.readouterr().out)
    assert any(c["control"] == "conexión de solo lectura e identidad" and c["ok"] for c in rep["controles"])
    assert sim.SECRETO_EMB not in json.dumps(rep)


def test_k21_conciliar_acredita_solo_con_pass(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    manual = demo.corte_posterior(src, date="11/08/26", name="R_20261108.TXT")
    data = tmp_path / "data"
    data.mkdir()
    # v0.3.3: la acreditación exige el XML ORIGINAL (preflight) y la huella del reporte configurado
    sim.config_conectores(data)
    plan = sim.plan_posterior()
    mal = [{**plan[0], "lines": plan[0]["lines"][1:]}, *plan[1:]]  # una línea menos que el manual
    directa = sim.captura_preflight_backlog(data, mal)
    assert conciliar.main(["backlog", "--data", str(data), "--manual", str(manual), "--directa", str(directa), "--acreditar"]) == 1
    assert not (data / "acreditacion_backlog_ssrs.json").exists()
    directa.write_bytes(sim.xml_ssrs(plan))
    assert conciliar.main(["backlog", "--data", str(data), "--manual", str(manual), "--directa", str(directa), "--acreditar"]) == 0
    assert orq.acreditado(data, "backlog_ssrs")[0]
    ev = tmp_path / "ev.csv"
    ev.write_text("item,cantidad\nQZXA0016,250\n", "utf-8")
    capd = tmp_path / "emb.csv"
    capd.write_text("item,facturado_cfdi,renglones,documentos,nulos\nQZXA0016,300,2,2,0\n", "utf-8")
    assert conciliar.main(["embarques", "--data", str(data), "--directa", str(capd), "--evidencia", str(ev), "--acreditar"]) == 1
    assert not (data / "acreditacion_embarques.json").exists()  # nunca se acredita por comando


def test_k22_plantilla_valida_y_xml_ssrs_en_conciliacion(tmp_path: Path) -> None:
    import shutil

    shutil.copy(APP / "conectores.plantilla.json", tmp_path / "conectores.json")
    cfg = orq.cargar_config(tmp_path)
    assert not any((cfg.get(f) or {}).get("activo") for f in orq.FUENTES)  # todo inactivo por defecto
    src = tmp_path / "src"
    src.mkdir()
    manual = demo.corte_posterior(src, date="11/08/26", name="R_20261108.TXT")
    xml = tmp_path / "first_ssrs.xml"
    xml.write_bytes(sim.xml_ssrs(sim.plan_posterior()))
    assert conciliar.main(["backlog", "--data", str(tmp_path), "--manual", str(manual), "--directa", str(xml)]) == 0
    assert not (tmp_path / "acreditacion_backlog_ssrs.json").exists()  # sin --acreditar no se acredita
