"""WP7 — web mínima (Flask): seguridad, roles, Refresh, CS y lectura verificada."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from core_helpers import Clock

from nexus.cli import main
from nexus.core import demo
from nexus.web import create_app

SECRET = "s" * 40


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "data"
    demo.scenario(root)
    clock = Clock("2031-01-10T20:00:00Z")
    app = create_app(root, "sintetico", SECRET, clock=clock, background=False)
    return {"root": root, "clock": clock, "app": app}


def _csrf(client: Any) -> str:
    html = client.get("/login").get_data(as_text=True)
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)  # type: ignore[union-attr]


def login(app: Any, user: str) -> tuple[Any, str]:
    client = app.test_client()
    token = _csrf(client)
    resp = client.post("/login", data={"username": user, "password": f"{user}-demo", "csrf": token})
    assert resp.status_code == 302, resp.get_data(as_text=True)
    page = client.get("/").get_data(as_text=True)
    return client, re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]


def test_security_config_refusals(tmp_path: Path) -> None:
    root = tmp_path / "d"
    demo.scenario(root)
    with pytest.raises(ValueError, match="NEXUS_WEB_SECRET"):
        create_app(root, "sintetico", "corto")
    with pytest.raises(ValueError, match="DevAuth"):
        create_app(root, "operativo", SECRET)
    assert main(["web", "--data", str(root), "--host", "0.0.0.0"]) == 1  # noqa: S104
    assert main(["web", "--data", str(root), "--host", "192.168.1.5"]) == 1


def test_headers_auth_and_csrf(env: dict[str, Any]) -> None:
    client = env["app"].test_client()
    first = client.get("/")
    assert first.status_code == 302  # sin sesión → login
    cookie = first.headers.get("Set-Cookie", "")
    resp = client.get("/login")
    for header in (
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
    ):
        assert header in resp.headers
    assert "unsafe-inline" not in resp.headers["Content-Security-Policy"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    bad = client.post("/login", data={"username": "admin", "password": "admin-demo"})
    assert bad.status_code == 403  # sin token CSRF
    wrong = client.post(
        "/login", data={"username": "admin", "password": "x", "csrf": _csrf(client)}
    )
    assert wrong.status_code == 401
    c, _token = login(env["app"], "refresher")
    assert c.post("/refresh", data={"csrf": "otro"}).status_code == 403
    assert c.get("/healthz").get_json() == {"status": "ok"}
    assert c.get("/no-existe").status_code == 404


def test_roles_default_deny(env: dict[str, Any]) -> None:
    reader, token = login(env["app"], "lector")
    page = reader.get("/").get_data(as_text=True)
    assert "DATOS SINTÉTICOS" in page and 'action="/refresh"' not in page
    assert reader.post("/refresh", data={"csrf": token}).status_code == 403
    assert (
        reader.post(
            "/cs",
            data={
                "csrf": token,
                "item": "SYN-A",
                "kind": "NOTE",
                "value": "x",
                "expected_version": "0",
            },
        ).status_code
        == 403
    )
    assert reader.post("/admin/rollback", data={"csrf": token, "gen_id": "x"}).status_code == 403
    editor, etoken = login(env["app"], "editorcs")
    assert editor.post("/refresh", data={"csrf": etoken}).status_code == 403


def test_refresh_board_item_and_history(env: dict[str, Any]) -> None:
    c, token = login(env["app"], "refresher")
    resp = c.post("/refresh", data={"csrf": token})
    assert resp.status_code == 302 and "/refresh/status" in resp.headers["Location"]
    status = c.get(resp.headers["Location"]).get_data(as_text=True)
    assert "COMPLETED" in status
    board = c.get("/").get_data(as_text=True)
    assert "PROVISIONAL — reglas core-synthetic-1" in board
    for text in (
        "SYN-A",
        "650",
        "240",
        "260",
        "Frozen 29 OERAS 2031-01-01T06:00:00Z",
        "corte FG/embarques 2031-01-10T18:00:00Z",
        "incompleto",
    ):
        assert text in board, text
    item = c.get("/item?i=SYN-A").get_data(as_text=True)
    assert "SYNTUP    000" in item and "no (separado)" in item and "BLOCKED_CRC" in item
    hist = c.get("/history").get_data(as_text=True)
    assert "PUBLISH" in hist and "FROZEN_REGISTERED" in hist


def test_cs_via_web_author_escape_conflict_and_origin(env: dict[str, Any]) -> None:
    admin, atoken = login(env["app"], "admin")
    admin.post("/refresh", data={"csrf": atoken})
    editor, token = login(env["app"], "editorcs")
    form = {
        "csrf": token,
        "item": "SYN-A",
        "kind": "NOTE",
        "expected_version": "0",
        "value": "<script>alert(1)</script> acuerdo",
        "author": "impostor",
    }
    assert editor.post("/cs", data=form).status_code == 302
    page = editor.get("/item?i=SYN-A").get_data(as_text=True)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<script>alert" not in page
    assert "impostor" not in page and "editorcs" in page
    stale = editor.post("/cs", data={**form, "value": "otra"})  # versión esperada 0 ya usada
    assert stale.status_code == 409
    ok = editor.post(
        "/cs",
        data={
            "csrf": token,
            "item": "SYN-A",
            "kind": "DELIVERY_DATE",
            "value": "2031-01-29",
            "expected_version": "1",
        },
    )
    assert ok.status_code == 302
    board = editor.get("/").get_data(as_text=True)
    assert "2031-01-29" in board and "MANUAL_CS" in board
    item = editor.get("/item?i=SYN-A").get_data(as_text=True)
    assert "2031-01-10 (" not in item  # la fecha CS no sustituye la fecha de la línea
    assert "2031-01-10" in item and "2031-01-29" in item
    bad = editor.post(
        "/cs",
        data={
            "csrf": token,
            "item": "SYN-A",
            "kind": "CS_STATUS",
            "value": "INVENTADO",
            "expected_version": "2",
        },
    )
    assert bad.status_code == 400
    thread = re.search(r"/cs/history\?t=([0-9a-f]{32})", item).group(1)  # type: ignore[union-attr]
    hist = editor.get(f"/cs/history?t={thread}").get_data(as_text=True)
    assert hist.count("<tr><td>") == 2 and "editorcs" in hist


def test_rollback_via_web_keeps_cs(env: dict[str, Any]) -> None:
    admin, token = login(env["app"], "admin")
    admin.post("/refresh", data={"csrf": token})
    first = env["app"].extensions["nexus"]["service"].state.publication_head("sintetico")
    admin.post(
        "/cs",
        data={
            "csrf": token,
            "item": "SYN-A",
            "kind": "NOTE",
            "value": "antes",
            "expected_version": "0",
        },
    )
    demo.new_cut_m1(env["root"])
    env["clock"].set("2031-01-20T20:00:00Z")
    admin.post("/refresh", data={"csrf": token})
    assert "490" in admin.get("/").get_data(as_text=True)
    resp = admin.post("/admin/rollback", data={"csrf": token, "gen_id": first["gen_id"]})
    assert resp.status_code == 302
    board = admin.get("/").get_data(as_text=True)
    assert "ROLLBACK" in board and ">240<" in board.replace(" ", "")
    assert "antes" in board  # la nota sobrevive al rollback


def test_integrity_error_fails_closed_per_item(env: dict[str, Any]) -> None:
    c, token = login(env["app"], "refresher")
    c.post("/refresh", data={"csrf": token})
    svc = env["app"].extensions["nexus"]["service"]
    m = svc.state.get_manifest("sintetico", svc.state.publication_head("sintetico")["gen_id"])
    p = svc.state.store.root / "objects" / m["items"]["SYN-C"]["key"]
    p.chmod(0o644)
    p.write_bytes(p.read_bytes().replace(b'"80"', b'"81"'))
    page = c.get("/item?i=SYN-C").get_data(as_text=True)
    assert "Integridad" in page and ">81<" not in page
    assert "Integridad" not in c.get("/item?i=SYN-A").get_data(as_text=True)


def test_notes_survive_app_restart(env: dict[str, Any], tmp_path: Path) -> None:
    editor, token = login(env["app"], "editorcs")
    editor.post(
        "/cs",
        data={
            "csrf": token,
            "item": "SYN-B",
            "kind": "NOTE",
            "value": "persistente",
            "expected_version": "0",
        },
    )
    app2 = create_app(env["root"], "sintetico", SECRET, clock=env["clock"], background=False)
    reader, _ = login(app2, "lector")
    assert "persistente" in reader.get("/item?i=SYN-B").get_data(as_text=True)


def test_templates_have_no_inline_styles_or_scripts() -> None:
    """La CSP no admite 'unsafe-inline': ninguna plantilla puede usar style= ni <script>."""
    root = Path(__file__).resolve().parents[2] / "src/nexus/web/templates"
    for path in sorted(root.glob("*.html")):
        text = path.read_text("utf-8")
        assert "style=" not in text and "<script" not in text, path.name
