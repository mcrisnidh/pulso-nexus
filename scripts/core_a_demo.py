"""Demostración end-to-end del Hito A (dos meses SINTÉTICOS) a través de la web.

Uso:  NEXUS_WEB_SECRET=... python scripts/core_a_demo.py DIRECTORIO_DE_DATOS

Recorre, con usuarios sintéticos y la aplicación Flask (cliente de pruebas, sin red):
Frozen M1 → Refresh → notas CS (y conflicto concurrente) → Current/extracto inválido/extracto
tardío → nuevo corte → Refresh fallido (sin publicar) → rollback → reinicio de la aplicación →
mes 2 (Frozen nuevo, reinicio mensual CS) → verificación de Frozen M1, cadena e invariantes.
Cada paso verifica su resultado; cualquier discrepancia termina con código distinto de 0.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from nexus.core import demo
from nexus.web import create_app

LOG: list[dict[str, Any]] = []


class Clock:
    def __init__(self, iso: str) -> None:
        self.set(iso)

    def set(self, iso: str) -> None:
        self.now = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))

    def __call__(self) -> dt.datetime:
        return self.now


def step(name: str, ok: bool, **detail: Any) -> None:
    LOG.append({"step": name, "ok": ok, **detail})
    print(json.dumps({"step": name, "ok": ok, **detail}, ensure_ascii=False, default=str))
    if not ok:
        raise SystemExit(f"FALLO en el paso: {name}")


def login(app: Any, user: str) -> tuple[Any, str]:
    c = app.test_client()
    html = c.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf" value="([^"]+)"', html).group(1)  # type: ignore[union-attr]
    c.post("/login", data={"username": user, "password": f"{user}-demo", "csrf": token})
    page = c.get("/").get_data(as_text=True)
    return c, re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode() + p.read_bytes())
    return h.hexdigest()


def main(root: Path) -> int:  # noqa: PLR0915 (pasos lineales y verificados de la demo)
    secret = os.environ.get("NEXUS_WEB_SECRET", "")
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"{root} debe estar vacío")
    demo.scenario(root)
    clock = Clock("2031-01-10T20:00:00Z")
    app = create_app(root, "sintetico", secret, clock=clock, background=False)
    svc = app.extensions["nexus"]["service"]
    ref, rtok = login(app, "refresher")
    ed, etok = login(app, "editorcs")
    ed2, etok2 = login(app, "editorcs2")
    adm, atok = login(app, "admin")
    rd, _ = login(app, "lector")

    # --- Mes 1: Frozen + primer Refresh
    ref.post("/refresh", data={"csrf": rtok})
    head = svc.state.publication_head("sintetico")
    step(
        "M1 Frozen registrado y primer corte publicado",
        head is not None and head["seq"] == 1,
        frozen=svc.state.get_frozen("sintetico", "2031-01", "oeras_29")["file"],
        gen=head and head["gen_id"],
    )
    board = rd.get("/").get_data(as_text=True)
    step(
        "Lector ve la junta sin botón Refresh", "650" in board and 'action="/refresh"' not in board
    )

    # --- Notas CS y conflicto concurrente
    form = {
        "csrf": etok,
        "item": "SYN-A",
        "customer_key": demo.TUPY,
        "order": "000101",
        "line": "1",
        "kind": "CS_STATUS",
        "value": "EN_RIESGO",
        "expected_version": "0",
    }
    r1 = ed.post("/cs", data=form)
    r2 = ed2.post("/cs", data={**form, "csrf": etok2, "value": "EN_TIEMPO"})
    step(
        "Dos editores sobre la misma versión: uno gana, otro conflicto 409",
        r1.status_code == 302 and r2.status_code == 409,
    )
    for kind, value, ver in (
        ("DELIVERY_DATE", "2031-01-27", "1"),
        ("NOTE", "Cliente acepta parcial; revisar FG el 20", "2"),
    ):
        ed.post("/cs", data={**form, "kind": kind, "value": value, "expected_version": ver})
    ed.post(
        "/cs",
        data={
            "csrf": etok,
            "item": "SYN-A",
            "kind": "ADVANCE",
            "value": "50",
            "date": "2031-01-18",
            "expected_version": "0",
        },
    )
    cs_before = tree_hash(root / "cs")

    # --- Current, extracto inválido y extracto tardío anterior al Frozen
    demo.current_m1(root)
    demo.invalid_extract(root)
    demo.late_earlier_extract(root)
    clock.set("2031-01-12T20:00:00Z")
    ref.post("/refresh", data={"csrf": rtok})
    alerts = svc.state.alerts("sintetico", "2031-01")
    step(
        "Frozen intacto; extracto tardío anterior → alerta sin reemplazo",
        svc.state.get_frozen("sintetico", "2031-01", "oeras_29")["file"] == "R_20310101_0600.csv"
        and [a["kind"] for a in alerts] == ["FROZEN_PRECEDENCE_CONFLICT"],
    )

    # --- Nuevo corte FG + embarques
    demo.new_cut_m1(root)
    clock.set("2031-01-20T20:00:00Z")
    ref.post("/refresh", data={"csrf": rtok})
    good = svc.state.publication_head("sintetico")
    board = rd.get("/").get_data(as_text=True)
    step(
        "Nuevo corte publicado: SYN-A entregado 490, sin cubrir 70",
        good["seq"] == 3 and ">490<" in board.replace(" ", "") and ">70<" in board.replace(" ", ""),
        seq=good["seq"],
    )

    # --- Refresh fallido: FG sin embarques del mismo corte
    demo.broken_cut_m1(root)
    clock.set("2031-01-25T20:00:00Z")
    ref.post("/refresh", data={"csrf": rtok})
    step(
        "Refresh fallido no publica: se conserva el último corte válido",
        svc.state.publication_head("sintetico") == good,
    )

    # --- Rollback y notas CS intactas
    first_gen = svc.state.publication_history("sintetico")[0]["gen_id"]
    adm.post("/admin/rollback", data={"csrf": atok, "gen_id": first_gen})
    head = svc.state.publication_head("sintetico")
    step(
        "Rollback atómico a la primera generación; notas CS intactas",
        head["action"] == "ROLLBACK"
        and head["gen_id"] == first_gen
        and tree_hash(root / "cs") == cs_before,
    )
    adm.post("/admin/rollback", data={"csrf": atok, "gen_id": good["gen_id"]})

    # --- Reinicio de la aplicación
    app = create_app(root, "sintetico", secret, clock=clock, background=False)
    svc = app.extensions["nexus"]["service"]
    rd, _ = login(app, "lector")
    page = rd.get("/item?i=SYN-A").get_data(as_text=True)
    step(
        "Tras reinicio: notas, estatus y publicación presentes",
        "Cliente acepta parcial" in page
        and "EN_RIESGO" in page
        and svc.state.publication_head("sintetico")["gen_id"] == good["gen_id"],
    )

    # --- Mes 2
    f1 = svc.state.get_frozen("sintetico", "2031-01", "oeras_29")
    demo.month2(root)
    clock.set("2031-02-10T20:00:00Z")
    ref, rtok = login(app, "refresher")
    ref.post("/refresh", data={"csrf": rtok})
    head = svc.state.publication_head("sintetico")
    page = rd.get("/item?i=SYN-A").get_data(as_text=True)
    board = rd.get("/").get_data(as_text=True)
    step(
        "Mes 2: Frozen nuevo publicado; Frozen M1 intacto",
        head["month"] == "2031-02"
        and svc.state.get_frozen("sintetico", "2031-01", "oeras_29") == f1
        and svc.state.get_frozen("sintetico", "2031-02", "oeras_29") is not None,
    )
    step(
        "Mes 2: estatus CS reiniciado; valor de enero solo como referencia",
        "Ref. 2031-01: EN_RIESGO — requiere reconfirmar" in page
        and "Cliente acepta parcial" in page
        and ">400<" in board.replace(" ", ""),
    )
    chain = svc.state.verify_publication_chain("sintetico")
    step(
        "Cadena de publicaciones íntegra",
        chain == [],
        publications=len(svc.state.publication_history("sintetico")),
    )
    (root / "demo_log.json").write_text(json.dumps(LOG, indent=1, ensure_ascii=False), "utf-8")
    print(json.dumps({"result": "PASS", "steps": len(LOG)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
