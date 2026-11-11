"""R3: suite de alcance NO OPERATIVO, sin Flask ni consultas corporativas."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "nexus_preview" / "r3_admin.py"
SEED = ROOT / "nexus_preview" / "r3_catalogo_visual.json"
spec = importlib.util.spec_from_file_location("r3_admin", MOD)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_01_inventario_exactitud_y_criterios_no_aprobados(tmp_path):
    admin = m.R3Admin(SEED, tmp_path / "private")
    clients = admin.catalog()
    assert len(clients) == 84
    assert sum(x["historical_lines"] for x in clients) == 3147
    assert all(len(x["customer_key_exact"]) == 13 for x in clients)
    assert len({x["customer_key_exact"] for x in clients}) == 84
    assert sum(x["commercial_criterion"] == "PORTAL_CAT" for x in clients) == 4
    assert sum(x["commercial_criterion"] == "SSRS" for x in clients) == 4
    assert sum(x["commercial_criterion"] == "PENDIENTE" for x in clients) == 76
    assert sum(x["source_triaje"] == "CAT_NOMBRE_170_19" and x["commercial_criterion"] == "PENDIENTE" for x in clients) == 18
    assert not any(x["formal_approval"] for x in clients)
    assert not (tmp_path / "private").exists()  # GET/read no escribe


def test_02_identidad_erp_exacta_y_ceros_iniciales():
    assert m.exact_key("17057704", "205") == "17057704  205"
    assert m.exact_key("CATPERK", "000") == "CATPERK   000"
    assert m.split_key("170577    029") == ("170577", "029")
    for invalid in [("170 577", "001"), ("170577", "01"), ("170577", "1"), ("170577", "A01"), ("a/b", "001")]:
        with pytest.raises(m.R3AdminError):
            m.exact_key(*invalid)
    with pytest.raises(m.R3AdminError):
        m.split_key("CATPERK000")


def submit(admin, **extra):
    args=dict(number="170577", sequence="029", action="AGREGAR_PORTAL_CAT",
              effective_month=m.next_month_utc(), reason="Confirmar cobertura Portal CAT de la cuenta",
              evidence_ref="Correo Carla por validar")
    args.update(extra)
    return admin.submit(**args)


def test_03_propuesta_guardada_inmutable_sin_autoridad(tmp_path):
    admin=m.R3Admin(SEED, tmp_path / "private")
    before=hashlib.sha256(SEED.read_bytes()).hexdigest()
    event=submit(admin)
    history=admin.proposals()
    assert len(history)==1 and history[0]["id"]==event["id"]
    assert history[0]["status"]=="PENDIENTE_APROBACION"
    assert history[0]["approved"] is False
    assert history[0]["applies_to_refresh"] is False
    assert history[0]["applies_to_frozen"] is False
    assert len(history[0]["customer_key_exact"])==13
    assert hashlib.sha256(SEED.read_bytes()).hexdigest()==before
    assert not (tmp_path/"private"/"rules.json").exists()
    assert not (tmp_path/"private"/"preview.json").exists()
    with pytest.raises(m.R3AdminError,match="Ya existe"):
        submit(admin)


def test_04_proteccion_positiva_ssrs_y_tupy(tmp_path):
    a=m.R3Admin(SEED,tmp_path/"private")
    for key in m.PROTECTED_SSRS:
        number,seq=m.split_key(key)
        with pytest.raises(m.R3AdminError,match="SSRS protegida"):
            submit(a, number=number,sequence=seq)
    assert a.proposals()==[]


def test_05_retiro_solo_pendiente_y_no_reescribe_frozen(tmp_path):
    admin=m.R3Admin(SEED,tmp_path/"private")
    event=submit(admin,number="PERK2",sequence="001",action="RETIRAR_PORTAL_CAT")
    assert event["action"]=="RETIRAR_PORTAL_CAT"
    assert event["effective_month"]>=m.next_month_utc()
    assert event["applies_to_frozen"] is False
    assert next(x for x in admin.catalog() if x["customer_key_exact"]=="PERK2     001")["commercial_criterion"]=="PORTAL_CAT"


def test_06_rechazos_sin_autoaprobacion(tmp_path):
    admin=m.R3Admin(SEED,tmp_path/"private")
    for args in [
        {"effective_month":"2026-01"},
        {"effective_month":"2099-14"},
        {"reason":"corto"},
        {"evidence_ref":""},
        {"action":"APROBAR"},
    ]:
        with pytest.raises(m.R3AdminError):
            submit(admin,**args)
    assert not admin.proposals()


def test_07_fichero_alterado_falla_cerrado(tmp_path):
    admin=m.R3Admin(SEED,tmp_path/"private")
    submit(admin)
    fp=next(admin.proposals_dir.glob("*.json"))
    raw=json.loads(fp.read_text("utf-8"))
    raw["approved"]=True
    fp.write_text(json.dumps(raw),encoding="utf-8")
    with pytest.raises(m.R3AdminError,match="alterada"):
        admin.proposals()


def test_08_catalogo_suplantado_falla_cerrado(tmp_path):
    raw=json.loads(SEED.read_text("utf-8"))
    raw["customers"][0]["formal_approval"]=True
    bad=tmp_path/"seed.json"
    bad.write_text(json.dumps(raw),encoding="utf-8")
    with pytest.raises(m.R3AdminError):
        m.R3Admin(bad,tmp_path/"private")


def test_09_contrato_web_no_publicacion_y_csrf():
    web=(ROOT/"nexus_preview"/"webapp.py").read_text("utf-8")
    base=(ROOT/"nexus_preview"/"templates"/"base.html").read_text("utf-8")
    view=(ROOT/"nexus_preview"/"templates"/"clientes_portal_cat.html").read_text("utf-8")
    assert '@app.get("/clientes-portal-cat")' in web
    assert '@app.post("/clientes-portal-cat/solicitudes")' in web
    assert '"clientes_portal_cat.html"' in web
    assert 'r3.submit(' in web
    assert 'name="csrf" value="{{ csrf }}"' in view
    assert 'url_for(\'clientes_portal_cat\')' in base
    assert 'Aprobar regla' not in view
    assert 'PREAPROBACIÓN · NO PUBLICABLE' in view
    assert 'def _guard()' in web and 'request.method == "POST"' in web
    # Módulos de generación/conectores no deben ser modificados por este cambio.
    assert "rules.json" not in view


def test_10_plantilla_se_renderiza_sin_servicios_corporativos():
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    templates=ROOT/"nexus_preview"/"templates"
    env=Environment(loader=FileSystemLoader(templates),autoescape=select_autoescape(["html"]))
    env.globals["url_for"]=lambda endpoint, **kwargs: "/"+endpoint + ("/"+kwargs["filename"] if "filename" in kwargs else "")
    admin=m.R3Admin(SEED,Path("/not_used"))
    html=env.get_template("clientes_portal_cat.html").render(
        active="clientes_cat", csrf="synthetic_csrf_only",
        vista_global=None, estado_refresh={}, estado_label="Sin Refresh",
        editor="EDITOR_LOCAL_SIN_AUTENTICAR",
        clientes=admin.catalog(), total=84, confirmadas_portal=4, confirmadas_ssrs=4,
        pendientes=76, propuestas=[], registrada="", error=None, mes_propuesto="2026-12", q="", fuente="",
    )
    assert "Clientes Portal CAT" in html
    assert "PREAPROBACIÓN · NO PUBLICABLE" in html
    assert "synthetic_csrf_only" in html
    assert "17057704  205" in html
    assert "CATPERK   000" in html
    assert "Registrar solicitud pendiente" in html
    assert "CATERPILLAR REMANUFACTURI" in html
    assert "Aprobar definitivamente" not in html
    assert "r3_portal_cat.css" in html
