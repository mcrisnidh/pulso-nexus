"""Δ5/C3 — La web del canal replay nunca presenta un desconocido como 0 (Junta, detalle, filtros
y API), rotula la vista como informativa y guarda CS en su propio almacén (C2). Sintético."""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import physical_fixtures as fx

from nexus.core.stage import stage
from nexus.web import create_app

SECRET = "r" * 40


def clock() -> dt.datetime:
    return dt.datetime(2026, 11, 7, 18, 0, tzinfo=dt.UTC)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Any]:
    lab = fx.lab(tmp_path)
    stage(lab["src"], lab["data"], lab["homologation"], clock=clock)
    app = create_app(lab["data"], "replay", SECRET, clock=clock, background=False)
    return {"data": lab["data"], "app": app}


def login(app: Any, user: str) -> tuple[Any, str]:
    client = app.test_client()
    html = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf" value="([^"]+)"', html).group(1)  # type: ignore[union-attr]
    resp = client.post("/login", data={"username": user, "password": f"{user}-demo", "csrf": token})
    assert resp.status_code == 302
    page = client.get("/").get_data(as_text=True)
    return client, re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]


def kpi_values(html: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(
        r'<div class="kpi [^"]*">\s*<div class="label">([^<]+)</div>\s*(?:\{[^}]*\})?\s*'
        r'<div class="value">(.*?)</div>',
        html,
        re.S,
    ):
        out[m.group(1)] = re.sub(r"<[^>]+>", "", m.group(2)).strip()
    return out


def test_wr01_board_shows_dash_never_zero_for_unknown(env: dict[str, Any]) -> None:
    c, token = login(env["app"], "refresher")
    c.post("/refresh", data={"csrf": token})
    html = c.get("/").get_data(as_text=True)
    assert "Vista informativa (replay): no oficial y no certificada" in html
    assert "Embarques: no disponibles" in html
    assert "fecha del documento, sin hora" in html and "Frozen provisional" in html
    kpis = kpi_values(html)
    for label in ("Embarcado", "Cubierto con FG", "Sin cubrir", "Pendiente"):
        assert kpis[label] == "—", (label, kpis[label])
    assert kpis["Demanda congelada exigible"] == "—"  # un ITEM sin dato: el total no se presenta
    assert "suma de ITEM con dato: 890" in html
    assert html.count("No certificado: fuente no disponible (embarques físicos)") == 4
    assert "Indicador no disponible" in html


def test_wr02_rows_and_filters_treat_blocked_as_no_data(env: dict[str, Any]) -> None:
    c, token = login(env["app"], "refresher")
    c.post("/refresh", data={"csrf": token})
    rows = c.get("/?cobertura=sin_dato").get_data(as_text=True)
    assert "3 de 3 ITEM" in rows
    assert "0 de 3 ITEM" in c.get("/?cobertura=faltante").get_data(as_text=True)
    assert "0 de 3 ITEM" in c.get("/?cobertura=cubierto").get_data(as_text=True)
    body = rows.split("<tbody>")[1].split("</tbody>")[0]
    pattern = r'data-label="(?:Embarcado|Pendiente|Cubierto FG|Sin cubrir)">(.*?)</td>'
    for cell in re.findall(pattern, body, re.S):
        assert re.sub(r"<[^>]+>", "", cell).startswith("—"), cell


def test_wr03_item_detail_units_show_dash(env: dict[str, Any]) -> None:
    c, token = login(env["app"], "refresher")
    c.post("/refresh", data={"csrf": token})
    html = c.get("/item?i=QZXITEM1").get_data(as_text=True)
    kpis = kpi_values(html)
    assert kpis["Embarcado"].startswith("—") and kpis["Pendiente"].startswith("—")
    assert kpis["Demanda congelada"] == "650"
    table = html.split("Demanda congelada del mes")[1].split("</table>")[0]
    shipped = re.findall(r'data-label="Embarcado">(.*?)</td>', table, re.S)
    pending = re.findall(r'data-label="Pendiente">(.*?)</td>', table, re.S)
    assert shipped and all(re.sub(r"<[^>]+>", "", x).strip() == "—" for x in shipped + pending)
    assert "agreg." not in table
    assert "embarques no disponibles" in html


def test_wr04_api_exposes_no_data_and_cs_is_isolated(env: dict[str, Any]) -> None:
    c, token = login(env["app"], "admin")
    assert c.get("/healthz").get_json() == {"status": "ok"}
    c.post("/refresh", data={"csrf": token})
    resp = c.post(
        "/cs",
        data={
            "csrf": token,
            "item": "QZXITEM1",
            "kind": "NOTE",
            "value": "nota de laboratorio",
            "customer_key": "",
            "order": "",
            "line": "",
            "expected_version": "0",
        },
    )
    assert resp.status_code in (302, 303), resp.get_data(as_text=True)
    assert (env["data"] / "replay" / "cs").is_dir()
    assert not (env["data"] / "cs").exists()  # el CS principal no se toca
    assert not (env["data"] / "store").exists()  # ni el almacén operativo/sintético
