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
    args=dict(number="170577", sequence="029", action="CLASIFICAR_INICIAL_PORTAL_CAT",
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
            submit(a, number=number,sequence=seq,action="AGREGAR_PORTAL_CAT")
        with pytest.raises(m.R3AdminError,match="SSRS protegida"):
            submit(a, number=number,sequence=seq,action="CLASIFICAR_INICIAL_PORTAL_CAT")
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
        criterio_por_clave={x["customer_key_exact"]: x["commercial_criterion"] for x in admin.catalog()},
        ssrs_protegidos=sorted(m.PROTECTED_SSRS),
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


def test_11_contextual_actions_and_no_invalid_removals(tmp_path):
    a=m.R3Admin(SEED, tmp_path/"private")
    # User's prior test of an unclassified account is now correctly rejected.
    unknown_existing=m.exact_key("00201000", "001")
    assert a.criterion_for(unknown_existing)=="PENDIENTE"
    assert a.allowed_actions(unknown_existing)==(
        "CLASIFICAR_INICIAL_PORTAL_CAT", "CLASIFICAR_INICIAL_SSRS")
    with pytest.raises(m.R3AdminError,match="incompatible"):
        submit(a,number="00201000",sequence="001",action="RETIRAR_PORTAL_CAT")
    with pytest.raises(m.R3AdminError,match="incompatible"):
        submit(a,number="00201000",sequence="001",action="AGREGAR_PORTAL_CAT")
    assert a.allowed_actions(m.exact_key("PERK2", "001"))==("RETIRAR_PORTAL_CAT",)
    with pytest.raises(m.R3AdminError,match="incompatible"):
        submit(a,number="PERK2",sequence="001",action="CLASIFICAR_INICIAL_PORTAL_CAT")
    assert a.proposals()==[]


def test_12_persistencia_tras_reinicio_y_duplicado_por_mes(tmp_path):
    path=tmp_path/"private"
    a=m.R3Admin(SEED,path)
    created=submit(a,number="00201000",sequence="001",action="CLASIFICAR_INICIAL_SSRS")
    assert created['criterion_at_request']=='PENDIENTE'
    # New application process: reads exactly the previously saved event.
    restarted=m.R3Admin(SEED,path)
    loaded=restarted.proposals()
    assert len(loaded)==1 and loaded[0]==created
    with pytest.raises(m.R3AdminError,match="Ya existe"):
        submit(restarted,number="00201000",sequence="001",action="CLASIFICAR_INICIAL_PORTAL_CAT")
    assert restarted.proposals()==loaded


def test_13_nueva_cuenta_solo_solicitud_y_verificacion_erp(tmp_path):
    a=m.R3Admin(SEED,tmp_path/"private")
    key=m.exact_key("NUEVOERP", "001")
    assert a.criterion_for(key)=="NO_EN_INVENTARIO"
    assert a.allowed_actions(key)==(
        "CLASIFICAR_INICIAL_PORTAL_CAT", "CLASIFICAR_INICIAL_SSRS")
    record=submit(a,number="NUEVOERP",sequence="001",action="CLASIFICAR_INICIAL_PORTAL_CAT")
    assert record["requires_erp_identity_verification"] is True
    assert record["approved"] is False and record["applies_to_refresh"] is False


def test_14_legacy_event_preserved_not_rewritten(tmp_path):
    admin=m.R3Admin(SEED,tmp_path/"private")
    # Former UI accepted an incorrect 'retirar' for an unclassified account.
    # Persist historical evidence, do not re-approve or erase it.
    legacy={
        "schema":m.SCHEMA,"id":"legacy001","created_utc":"2026-11-11T18:00:00Z",
        "customer_key_exact":m.exact_key("00201000","001"),
        "action":"RETIRAR_PORTAL_CAT", "effective_month":m.next_month_utc(),
        "reason":"Prueba anterior sin efecto", "evidence_reference":"Prueba local",
        "author":"EDITOR_LOCAL_SIN_AUTENTICAR", "status":"PENDIENTE_APROBACION",
        "approved":False,"applies_to_refresh":False,"applies_to_frozen":False,
    }
    legacy['record_sha256']=__import__('hashlib').sha256(m.canonical(legacy)).hexdigest()
    admin.proposals_dir.mkdir(parents=True)
    (admin.proposals_dir/'20261111_legacy001.json').write_text(json.dumps(legacy),encoding='utf-8')
    assert admin.proposals()[0]['id']=='legacy001'
    with pytest.raises(m.R3AdminError,match="Ya existe"):
        submit(admin,number="00201000",sequence="001",action="CLASIFICAR_INICIAL_PORTAL_CAT")
    assert len(admin.proposals())==1


def test_15_bloqueo_escritura_concurrente_y_sin_archivos_parciales(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    a=m.R3Admin(SEED,tmp_path/"private")
    def attempt(_):
        try:
            return submit(a,number="170577",sequence="029")['id']
        except m.R3AdminError:
            return None
    with ThreadPoolExecutor(max_workers=4) as executor:
        ids=list(executor.map(attempt,range(8)))
    assert sum(i is not None for i in ids)==1
    assert len(a.proposals())==1
    assert not list(a.proposals_dir.glob('*.partial'))
    assert not (a.proposals_dir/'.submission.lock').exists()


def test_16_rechazo_de_archivo_alterado_despues_reinicio(tmp_path):
    p=tmp_path/"private"
    a=m.R3Admin(SEED,p)
    submit(a)
    fp=next(a.proposals_dir.glob('*.json'))
    raw=json.loads(fp.read_text('utf-8'))
    raw['status']='APROBADO'
    fp.write_text(json.dumps(raw),encoding='utf-8')
    with pytest.raises(m.R3AdminError,match="alterada"):
        m.R3Admin(SEED,p).proposals()


def test_17_form_context_and_no_operational_write():
    tpl=(ROOT/'nexus_preview'/'templates'/'clientes_portal_cat.html').read_text('utf-8')
    web=(ROOT/'nexus_preview'/'webapp.py').read_text('utf-8')
    assert 'id="r3Action"' in tpl
    assert 'r3_proposal_form.js' in tpl
    assert 'data-criterios=' in tpl
    assert 'Pendiente heredada · revisar' in tpl
    assert 'criterio_por_clave=' in web and 'ssrs_protegidos=' in web
    js=(ROOT/'nexus_preview'/'static'/'r3_proposal_form.js').read_text('utf-8')
    assert 'CLASIFICAR_INICIAL_PORTAL_CAT' in js
    assert 'CLASIFICAR_INICIAL_SSRS' in js
    assert '<script>' not in tpl  # CSP script-src self must not be bypassed
    assert "script-src 'self'" in web
    engine=(ROOT/'nexus_preview'/'generaciones.py').read_text('utf-8')
    assert 'r3_admin' not in engine and 'r3_catalogo_visual' not in engine
