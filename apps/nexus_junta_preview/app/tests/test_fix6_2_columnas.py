"""FIX6.2 — contrato VISUAL de la agenda. No cambia cálculos ni consulta fuentes.

Los tests *_static son ejecutables offline; el de render requiere el runtime NEXUS
(Flask y dependencias) y solo utiliza datos sintéticos generados en tmp_path.
"""
from __future__ import annotations

import html
from html.parser import HTMLParser
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
TPL = APP / "nexus_preview" / "templates"
CSS = APP / "nexus_preview" / "static" / "nexus.css"

HEADERS = (
    "CS", "Cliente", "Grupo", "Artículo", "Nota junta", "Adelanto",
    "Frozen", "Pendiente Current", "Cambio Backlog",
    "Embarques", "Programa", "FG", "Faltante",
    "Cobertura 6 sem.", "WO", "Forecast", "Recup.", "Estado PULSO",
    "Solicitada", "Entrega", "Estatus CS", "Review",
)
GROUPS = (
    ("Identificación", 4), ("Acción de junta", 2), ("Demanda", 3),
    ("Necesidad", 4), ("Producción PULSO", 5), ("Seguimiento", 4),
)


class AgendaParser(HTMLParser):
    """Lee estructura real de la tabla; no asume orden basado en otro array."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.section = None
        self.row = None
        self.cell = None
        self.sections = {"thead": [], "tbody": [], "tfoot": []}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and "nexus-table" in (a.get("class") or ""):
            assert not self.active
            self.active = True
        elif not self.active:
            return
        if tag in self.sections:
            self.section = tag
        elif tag == "tr" and self.section:
            self.row = {"attrs": a, "cells": []}
        elif tag in ("td", "th") and self.row is not None:
            self.cell = {"tag": tag, "attrs": a, "text": ""}

    def handle_data(self, data):
        if self.active and self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag):
        if not self.active:
            return
        if tag in ("td", "th") and self.cell is not None:
            self.cell["text"] = " ".join(html.unescape(self.cell["text"]).split())
            self.row["cells"].append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.sections[self.section].append(self.row)
            self.row = None
        elif tag in self.sections:
            self.section = None
        elif tag == "table":
            self.active = False


def parse(markup):
    p = AgendaParser()
    p.feed(markup)
    return p.sections


def width(cells):
    return sum(int(c["attrs"].get("colspan", "1")) for c in cells)


def test_fix62_static_encabezados_grupos_filas_y_totales():
    s = parse((TPL / "junta.html").read_text("utf-8"))
    groups, heads = s["thead"]
    assert tuple((c["text"], int(c["attrs"].get("colspan", "1"))) for c in groups["cells"]) == GROUPS
    assert len(heads["cells"]) == len(HEADERS) == 22
    for c, label in zip(heads["cells"], HEADERS):
        assert c["text"].startswith(label), (label, c["text"])
    assert len(s["tbody"][0]["cells"]) == len(HEADERS)
    assert width(s["tbody"][1]["cells"]) == 22  # fila vacía del {% else %}
    assert len(s["tfoot"]) == 1  # el cuerpo del bucle {% for %}, antes de renderizar
    assert width(s["tfoot"][0]["cells"]) == 22


def test_fix62_static_valores_no_intercambiados_y_diagnostico_en_detalle():
    s = parse((TPL / "junta.html").read_text("utf-8"))
    cells = s["tbody"][0]["cells"]
    contract = {
        0: "r.cs", 1: "r.cliente_nombre", 2: "r.grupo", 3: "r.item",
        4: "data-note", 5: "data-adelanto", 6: "r.frozen", 7: "r.actual",
        8: "r.variacion", 9: "r.embarques", 10: "r.programa_v310",
        11: "r.fg", 12: "r.faltante_v310", 13: "r.semanas.cells",
        14: "gen.nd.wo", 15: "gen.nd.forecast", 16: "gen.nd.recuperacion",
        17: "N/D · sin PULSO", 18: "r.fecha_solicitada", 19: "data-entrega",
        20: "data-estatus", 21: "r.review",
    }
    # HTMLParser no incluye atributos en el texto de celda; comprobamos sobre
    # el fragmento de la fila usando el patrón de <td> para controles editables.
    template = (TPL / "junta.html").read_text("utf-8")
    for i, marker in contract.items():
        if marker.startswith("data-"):
            assert marker in template
        else:
            assert marker in cells[i]["text"], (i, marker, cells[i]["text"])
    assert all("Brecha diag." not in c["text"] for c in s["thead"][1]["cells"])
    assert "r.brecha_diag" not in template
    detail = (TPL / "item.html").read_text("utf-8")
    assert "Brecha diagnóstica" in detail and "row.brecha_diag" in detail
    assert "Cambio Backlog" in detail and "row.variacion" in detail
    assert "Cambio Backlog (Frozen − Current)" in template


def test_fix62_static_css_y_navegador_actualizados():
    css = CSS.read_text("utf-8")
    assert ".nexus-table.compact-flow .s2{left:96px;min-width:170px;max-width:170px}" in css
    assert ".nexus-table.compact-flow .s3{left:266px;min-width:86px;max-width:86px}" in css
    js = (APP / "tests" / "navegador.js").read_text("utf-8")
    assert "actual: (await cells.nth(7)" in js
    assert "embarques: (await cells.nth(9)" in js
    assert "programa: (await cells.nth(10)" in js
    assert "fg: (await cells.nth(11)" in js
    assert "faltante: (await cells.nth(12)" in js
    assert "cab_current: head[7]" in js


def test_fix62_renderizado_sintetico_sin_consultas(tmp_path):
    # Import diferido para permitir ejecutar las comprobaciones estáticas
    # también en entornos donde las dependencias corporativas no están presentes.
    from test_preview import client, make_lab

    _, ref = make_lab(tmp_path)
    assert ref.ejecutar("layout_sintetico")["estado"] == "PASS"
    c = client(ref)
    response = c.get("/junta")
    assert response.status_code == 200
    markup = response.get_data(as_text=True)
    sections = parse(markup)
    assert len(sections["tbody"]) == len(ref.store.vigente()["escenarios"]["A"]["rows"])
    assert all(len(row["cells"]) == 22 for row in sections["tbody"])
    assert all(width(row["cells"]) == 22 for row in sections["tfoot"])
    first = sections["tbody"][0]["cells"]
    item = sections["tbody"][0]["attrs"]["data-item"]
    assert item in first[3]["text"]
    assert "Cambio Backlog" in markup
    detail = c.get("/item?id=" + item)
    assert detail.status_code == 200
    assert "Brecha diagnóstica" in detail.get_data(as_text=True)
