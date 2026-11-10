"""FIX6.1: adelanto CS vacío = cero mensual; pruebas SINTÉTICAS, sin consultas externas."""

from __future__ import annotations

from decimal import Decimal

from nexus.core.cs import Association, board_state
from nexus_preview import status_v310 as v310
from test_preview import client, csrf, make_lab


def test_adelanto_por_defecto_y_restauracion_cero(tmp_path, monkeypatch):
    paths, ref = make_lab(tmp_path)
    assert ref.ejecutar("sintetico")["estado"] == "PASS"
    item = ref.store.vigente()["escenarios"]["A"]["rows"][0]["item"]
    c = client(ref)
    html = c.get("/junta").get_data(as_text=True)
    assert 'data-adelanto inputmode="decimal" value="0" placeholder="0"' in html

    entradas = []
    original = v310.programa

    def observar(frozen, adelanto, embarques):
        entradas.append(adelanto)
        return original(frozen, adelanto, embarques)

    monkeypatch.setattr(v310, "programa", observar)
    c.get("/junta")
    assert entradas and all(v == Decimal(0) for v in entradas)

    token = csrf(c)
    headers = {"X-CSRF": token}
    # Borrar un valor vacío nunca crea versiones artificiales.
    r0 = c.post("/api/cs", json={"item": item, "version": 0, "adelanto": ""}, headers=headers)
    assert r0.status_code == 200 and r0.get_json()["guardados"] == []
    assert r0.get_json()["version"] == 0

    r1 = c.post("/api/cs", json={"item": item, "version": 0, "adelanto": "120"}, headers=headers)
    assert r1.status_code == 200 and r1.get_json()["guardados"] == ["ADVANCE"]
    c.get("/junta")
    assert Decimal(120) in entradas

    # Borrar un adelanto existente SÍ registra el retorno explícito a 0.
    r2 = c.post("/api/cs", json={"item": item, "version": 1, "adelanto": ""}, headers=headers)
    assert r2.status_code == 200 and r2.get_json()["guardados"] == ["ADVANCE"]
    assert r2.get_json()["version"] == 2
    c.get("/junta")
    assert entradas[-1] == Decimal(0)

    r3 = c.post("/api/cs", json={"item": item, "version": 2, "adelanto": ""}, headers=headers)
    assert r3.status_code == 200 and r3.get_json()["guardados"] == []
    assert r3.get_json()["version"] == 2

    store = c.c.application.extensions["preview"]["cs"]
    versions = store.history(Association(item).thread_id)
    assert [v["kind"] for v in versions] == ["ADVANCE", "ADVANCE"]
    assert [Decimal(v["value"]["qty"]) for v in versions] == [Decimal(120), Decimal(0)]
    assert board_state(versions, "2026-12")["current"].get("ADVANCE") is None
    assert board_state(versions, "2026-12")["reference"]["ADVANCE"]["valid_month"] == "2026-11"


def test_adelanto_ausente_no_modifica_y_cantidad_invalida_se_rechaza(tmp_path):
    _, ref = make_lab(tmp_path)
    assert ref.ejecutar("sintetico")["estado"] == "PASS"
    item = ref.store.vigente()["escenarios"]["A"]["rows"][0]["item"]
    c = client(ref)
    headers = {"X-CSRF": csrf(c)}
    r1 = c.post("/api/cs", json={"item": item, "version": 0, "adelanto": "23"}, headers=headers)
    assert r1.status_code == 200
    no_field = c.post("/api/cs", json={"item": item, "version": 1, "nota": "Seguimiento"}, headers=headers)
    assert no_field.status_code == 200 and no_field.get_json()["guardados"] == ["NOTE"]
    html = c.get("/junta").get_data(as_text=True)
    assert 'data-adelanto inputmode="decimal" value="23"' in html

    for invalid in ("-1", "NaN", "Infinity", "texto"):
        bad = c.post("/api/cs", json={"item": item, "version": 2, "adelanto": invalid}, headers=headers)
        assert bad.status_code == 400, invalid
    ok = c.post("/api/cs", json={"item": item, "version": 2, "adelanto": "0"}, headers=headers)
    assert ok.status_code == 200 and ok.get_json()["guardados"] == ["ADVANCE"]


def test_formula_no_hace_cero_de_fuentes_desconocidas():
    frozen, fg, embarques = Decimal(100), Decimal(30), Decimal(20)
    prog = v310.programa(frozen, Decimal(0), embarques)
    assert prog == Decimal(80)
    assert v310.faltante(prog, fg) == Decimal(50)
    assert v310.programa(frozen, Decimal(15), embarques) == Decimal(95)
    assert v310.faltante(Decimal(95), fg) == Decimal(65)
    assert v310.programa(None, Decimal(0), embarques) is None
    assert v310.programa(frozen, Decimal(0), None) is None
    assert v310.faltante(prog, None) is None
    assert v310.faltante(prog, Decimal(-1)) is None
