"""Pruebas de aceptación de PULSO NEXUS Preview v0.2 (solo datos SINTÉTICOS).

Ejecutar desde la raíz del repositorio en el commit de referencia:
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen --extra web --with tzdata==2025.2 pytest <app>/tests -p no:cacheprovider -q
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

APP = Path(__file__).resolve().parents[1]
REPO = Path.cwd()
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))

import dataset_demo as demo  # noqa: E402
from nexus_preview.generaciones import PreviewRefresh  # noqa: E402
from nexus_preview.webapp import create_app  # noqa: E402

GROUPS = ("Identificación", "Acción de junta", "Lectura operacional", "Pendiente hoy", "Producción PULSO", "Seguimiento")


def fingerprint(folder: Path) -> dict[str, tuple[str, int]]:
    return {
        p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in sorted(folder.iterdir())
    }


def make_lab(root: Path) -> tuple[dict[str, Path], PreviewRefresh]:
    lab = demo.build(root / "lab")
    data = root / "data"
    data.mkdir()
    cfg = {
        "schema": "nexus.preview.config/1",
        "month": "2026-11",
        "src": str(lab["src"]),
        "r3_provisional": str(lab["r3"]),
        "r1_provisional": str(lab["r1"]),
        "asignaciones": str(lab["asignaciones"]),
        "escenario_por_defecto": "A",
    }
    (data / "preview.json").write_text(json.dumps(cfg), "utf-8")
    return {**lab, "data": data}, PreviewRefresh(data, "test")


def rows(gen: dict[str, Any], esc: str = "A") -> dict[str, dict[str, Any]]:
    return {r["item"]: r for r in gen["escenarios"][esc]["rows"]}


def qty(fact: dict[str, Any]) -> tuple[Any, str]:
    """Cantidad y estado (las limitaciones citan el corte FG vigente y pueden cambiar)."""
    return fact["value"], fact["status"]


@pytest.fixture
def lab(tmp_path: Path) -> tuple[dict[str, Path], PreviewRefresh]:
    lab, ref = make_lab(tmp_path)
    assert ref.ejecutar("test")["estado"] == "PASS"
    return lab, ref


class Client:
    """Cliente de pruebas que se presenta con un Host concreto (por defecto, el permitido)."""

    def __init__(self, app: Any, host: str = "127.0.0.1:8877") -> None:
        self.c = app.test_client()
        self.base = f"http://{host}"

    def get(self, path: str, **kw: Any) -> Any:
        return self.c.get(path, base_url=self.base, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.c.post(path, base_url=self.base, **kw)


def client(ref: PreviewRefresh, host: str = "127.0.0.1:8877") -> Any:
    return Client(create_app(ref.data, port=8877, refresher=ref), host)


def csrf(c: Any) -> str:
    html = c.get("/junta").get_data(as_text=True)
    return re.search(r'name="csrf" content="([^"]+)"', html).group(1)


# ---------------------------------------------------------------- 1-3 datos y universo
def test_a01_frozen_current_fg_del_dataset_completo(lab: Any) -> None:
    _, ref = lab
    gen = ref.store.vigente()
    s = gen["sources"]
    assert s["frozen_r"]["file"] == "R_20261101.TXT" and s["frozen_p"]["file"].startswith("Portal_CAT_2026-11-03")
    assert s["fg"]["file"] == "INV_STA289.TXT"
    a = gen["escenarios"]["A"]
    assert len(a["rows"]) == a["construccion_frozen"]["universe_items"] > 25  # todos, no una muestra


def test_a02_universo_y_totales_por_escenario_sin_mezclar(lab: Any) -> None:
    _, ref = lab
    gen = ref.store.vigente()
    for name, e in gen["escenarios"].items():
        assert len(e["rows"]) == e["construccion_frozen"]["universe_items"], name
        k = e["kpis"]
        known = sum(Decimal(r["frozen"]["value"]) for r in e["rows"] if r["frozen"]["value"] is not None)
        assert Decimal(k["frozen"]["known_sum"]) == known
        unknown = sum(1 for r in e["rows"] if r["frozen"]["value"] is None)
        assert (k["frozen"]["value"] is None) == (unknown > 0)  # N/D con causa, nunca cero
    a, b = gen["escenarios"]["A"], gen["escenarios"]["B"]
    assert a["construccion_frozen"]["universe_items"] != b["construccion_frozen"]["universe_items"]
    assert a["pendientes_r1"] == 3 and b["pendientes_r1"] == 0  # grupo de revisiones (P-0006, REV-01, REV-02 → A0006)


def test_a03_tabla_con_grupos_de_columnas_y_todas_las_filas(lab: Any) -> None:
    _, ref = lab
    html = client(ref).get("/junta").get_data(as_text=True)
    for g in GROUPS:
        assert g in html
    for col in ("Nota junta", "Adelanto", "Programa", "Embarques", "Faltante", "Cobertura 6 sem.", "WO",
                "Forecast", "Recup.", "Estado PULSO", "Solicitada", "Entrega", "Estatus CS", "Review"):
        assert f">{col}<" in html, col
    for col in ("Frozen<br><small>01-nov", "Pendiente Current<br><small>01-nov", "FG<br><small>06-nov"):
        assert col in html, col  # Frozen y Current: columnas distintas con fecha identificable
    n = len(ref.store.vigente()["escenarios"]["A"]["rows"])
    assert html.count('data-item="') == n
    assert "Orden de prioridad provisional" in html and "No es el orden certificado" in html


# ---------------------------------------------------------------- 4-8 Refresh
def test_a04_a08_refresh_corte_posterior_conserva_frozen_y_cambia_current(lab: Any) -> None:
    paths, ref = lab
    before = ref.store.vigente()
    lock_before = ref.store.frozen_lock("2026-11")
    demo.corte_posterior(paths["src"])
    st = ref.ejecutar("test")
    assert st["estado"] == "PASS" and st["fuentes"]["current_r"] == "R_20261107.TXT"
    after = ref.store.vigente()
    assert ref.store.frozen_lock("2026-11") == lock_before  # mismas identidades y hashes
    assert after["sources"]["frozen_r"] == before["sources"]["frozen_r"]
    rb, ra = rows(before), rows(after)
    assert set(rb) == set(ra)  # Current nunca amplía el universo
    assert "QZXA0999" not in ra
    assert all(qty(rb[i]["frozen"]) == qty(ra[i]["frozen"]) for i in rb)  # Frozen idéntico por ITEM
    changed = [i for i in ra if ra[i]["actual"]["value"] != rb[i]["actual"]["value"]]
    assert changed  # Current cambió
    for i in changed:
        r = ra[i]
        if r["frozen"]["value"] is not None and r["actual"]["value"] is not None:
            assert Decimal(r["variacion"]) == Decimal(r["frozen"]["value"]) - Decimal(r["actual"]["value"])
    assert after["escenarios"]["A"]["items_current_fuera_del_universo"] == 1


def test_a07_fg_nuevo_sin_modificar_frozen(lab: Any) -> None:
    paths, ref = lab
    before = rows(ref.store.vigente())
    demo.fg_posterior(paths["src"])
    st = ref.ejecutar("test")
    assert st["estado"] == "PASS" and st["fuentes"]["fg"] == "INV_STA290.TXT"
    after = rows(ref.store.vigente())
    assert all(qty(before[i]["frozen"]) == qty(after[i]["frozen"]) for i in before)
    assert any(before[i]["fg"]["value"] != after[i]["fg"]["value"] for i in before)


def test_a09_faltante_oficial_nd_sin_embarques(lab: Any) -> None:
    _, ref = lab
    gen = ref.store.vigente()
    for e in gen["escenarios"].values():
        assert e["kpis"]["faltante"]["value"] is None and "embarques" in e["kpis"]["faltante"]["causa"].lower()
    html = client(ref).get("/junta").get_data(as_text=True)
    assert "NO es el faltante oficial" in html
    assert "max(0, Frozen + Adelanto − Embarques)" in html  # Programa v3.10 visible como causa del N/D


def test_a10_nota_y_adelanto_sobreviven_refresh_sin_tocar_originales(lab: Any) -> None:
    paths, ref = lab
    src_before = fingerprint(paths["src"])
    c = client(ref)
    token = csrf(c)
    item = ref.store.vigente()["escenarios"]["A"]["rows"][0]["item"]
    r = c.post("/api/cs", json={"item": item, "version": 0, "nota": "Nota sintética", "adelanto": "120",
                                "estatus": "EN_RIESGO", "entrega": "2026-11-20"}, headers={"X-CSRF": token})
    assert r.status_code == 200 and r.get_json()["guardados"] == ["NOTE", "ADVANCE", "CS_STATUS", "DELIVERY_DATE"]
    assert fingerprint(paths["src"]) == src_before
    demo.corte_posterior(paths["src"])
    assert ref.ejecutar("test")["estado"] == "PASS"
    html = client(ref).get("/junta").get_data(as_text=True)  # nueva sesión (otro «navegador»)
    assert "Nota sintética" in html and 'value="120"' in html and 'value="2026-11-20"' in html
    assert "EDITOR_LOCAL_SIN_AUTENTICAR" in html  # no se atribuye a personas
    hist = client(ref).get(f"/item?id={item}").get_data(as_text=True)
    assert hist.count("EDITOR_LOCAL_SIN_AUTENTICAR") >= 4  # nota, adelanto, estatus, fecha
    again = c.post("/api/cs", json={"item": item, "version": 0, "nota": "otra"}, headers={"X-CSRF": token})
    assert again.status_code == 409  # versión obsoleta: concurrencia optimista, nada se pisa


def test_a11_archivo_invalido_no_destruye_la_vista(lab: Any) -> None:
    paths, ref = lab
    gen = ref.store.vigente()
    (paths["src"] / "R_20261108.TXT").write_bytes(b"esto no es un OE965\r\n")
    st = ref.ejecutar("test")
    assert st["estado"] == "ERROR" and any("R_20261108.TXT" in m for m in st["motivos"])
    assert ref.store.vigente()["gen_id"] == gen["gen_id"]
    assert client(ref).get("/junta").status_code == 200


def test_a11b_frozen_bloqueado_alterado_o_ausente(lab: Any) -> None:
    paths, ref = lab
    gen = ref.store.vigente()
    demo.corte_posterior(paths["src"])  # hay un R válido posterior: aun así no sustituye
    frozen = paths["src"] / "R_20261101.TXT"
    original = frozen.read_bytes()
    frozen.write_bytes(demo._oe(demo._plan()[:20], "11/01/26"))  # mismo nombre, otro contenido
    st = ref.ejecutar("test")
    assert st["estado"] == "ERROR" and "Frozen bloqueado" in st["motivos"][0]
    frozen.unlink()
    st = ref.ejecutar("test")
    assert st["estado"] == "ERROR" and "Frozen bloqueado" in st["motivos"][0]
    assert ref.store.vigente()["gen_id"] == gen["gen_id"]
    frozen.write_bytes(original)
    assert ref.ejecutar("test")["estado"] == "PASS"


def test_a11c_archivo_anterior_no_sustituye_frozen(lab: Any) -> None:
    paths, ref = lab
    lock = ref.store.frozen_lock("2026-11")
    demo.corte_posterior(paths["src"], date="11/01/26", name="R_20261101(2).TXT")  # mismo día, otro contenido
    demo.corte_posterior(paths["src"])
    st = ref.ejecutar("test")
    assert st["estado"] == "PASS"
    assert ref.store.frozen_lock("2026-11") == lock


def test_a11d_precedencia_ambigua_inicial_no_genera(tmp_path: Path) -> None:
    paths, ref = make_lab(tmp_path)
    demo.corte_posterior(paths["src"], date="11/01/26", name="R_20261101(2).TXT")
    st = ref.ejecutar("test")
    assert st["estado"] == "ERROR" and "precedencia ambigua" in st["motivos"][0]
    assert ref.store.vigente() is None


def test_a11e_sin_cambios_y_generacion_inmutable(lab: Any) -> None:
    _, ref = lab
    gen = ref.store.vigente()
    assert ref.ejecutar("test")["estado"] == "NO_CHANGE"
    f = ref.store.gens / gen["gen_id"] / "generacion.json"
    assert not (f.stat().st_mode & stat.S_IWUSR)
    lock = ref.data / "frozen_2026-11.json"
    with pytest.raises(FileExistsError):
        lock.open("x")


def test_a11f_concurrencia(lab: Any) -> None:
    _, ref = lab
    ref.lock_path.write_text("otro proceso")
    assert ref.ejecutar("test")["estado"] == "BUSY"
    assert ref.iniciar("web") is False


# ---------------------------------------------------------------- 12 aislamiento y seguridad
def test_a12_sin_sql_stage_refresh_oficial_ni_cambios_en_repo(lab: Any) -> None:
    forbidden = {"pyodbc", "sqlite3", "requests", "urllib.request", "nexus.core.refresh", "nexus.metadata", "nexus.pipeline", "nexus.web"}
    for p in (APP / "nexus_preview").glob("*.py"):
        tree = ast.parse(p.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                assert not any(m in forbidden or m.split(".")[0] in {"pyodbc", "sqlite3", "requests"} for m in mods), (p.name, mods)
                if isinstance(node, ast.ImportFrom) and node.module == "nexus.core.stage":
                    assert "stage" not in {a.name for a in node.names}, p.name
            if isinstance(node, ast.Name) and node.id in ("RefreshService", "stage"):
                pytest.fail(f"{p.name} usa {node.id}")
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True, check=True)
    assert status.stdout == ""


def test_a12b_proteccion_web(lab: Any) -> None:
    _, ref = lab
    c = client(ref)
    item = ref.store.vigente()["escenarios"]["A"]["rows"][0]["item"]
    gen = ref.store.vigente()["gen_id"]
    assert c.post("/api/cs", json={"item": item, "version": 0, "nota": "x"}).status_code == 403  # sin CSRF
    token = csrf(c)
    r = c.post("/api/cs", json={"item": item, "version": 0, "nota": "x"}, headers={"X-CSRF": token, "Origin": "http://evil.example"})
    assert r.status_code == 403
    assert c.post("/refresh").status_code == 403
    assert c.post("/refresh", headers={"X-CSRF": "malo"}).status_code == 403
    assert client(ref, "evil.example:8877").get("/junta").status_code == 400  # anti DNS-rebinding
    assert c.post("/api/cs", json={"item": "NO-EXISTE", "version": 0, "nota": "x"}, headers={"X-CSRF": token}).status_code == 404
    assert ref.store.vigente()["gen_id"] == gen  # ninguna solicitud no autorizada cambia la generación
    assert not (ref.data / "cs").exists() or not list((ref.data / "cs").rglob("*.json"))  # ni las notas
    headers = c.get("/junta").headers
    assert "script-src 'self'" in headers["Content-Security-Policy"] and "unsafe-inline" not in headers["Content-Security-Policy"]


def test_a12c_serve_solo_loopback(tmp_path: Path) -> None:
    r = subprocess.run([sys.executable, str(APP / "preview.py"), "serve", "--data", str(tmp_path), "--host", "0.0.0.0"],
                       cwd=REPO, capture_output=True, text=True, check=False)
    assert r.returncode == 2 and "127.0.0.1" in r.stderr


def test_a12d_init_fuera_del_repo_y_del_origen(tmp_path: Path) -> None:
    lab = demo.build(tmp_path / "lab")
    for bad in (REPO / "datos_preview", lab["src"] / "datos"):
        r = subprocess.run([sys.executable, str(APP / "preview.py"), "init", "--data", str(bad), "--src", str(lab["src"])],
                           cwd=REPO, capture_output=True, text=True, check=False)
        assert r.returncode != 0 and not bad.exists()


def test_a12e_refresh_web_en_segundo_plano(lab: Any) -> None:
    paths, ref = lab
    c = client(ref)
    token = csrf(c)
    demo.corte_posterior(paths["src"])
    r = c.post("/refresh", headers={"X-CSRF": token})
    assert r.status_code in (202, 409)
    for _ in range(200):
        st = c.get("/refresh/estado").get_json()
        if st["estado"] != "RUNNING":
            break
        import time

        time.sleep(0.05)
    assert st["estado"] == "PASS" and st["articulos"] > 25 and st["fuentes"]["current_r"] == "R_20261107.TXT"


@pytest.mark.skipif(os.environ.get("NEXUS_SKIP_BROWSER") == "1", reason="prueba de navegador desactivada")
def test_a13_navegador_flujo_completo(tmp_path: Path) -> None:
    """Criterio final en un navegador real (Chromium headless): nota → corte nuevo → Actualizar."""
    node = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, check=False)
    if node.returncode != 0 or not (Path(node.stdout.strip()) / "playwright").exists():
        pytest.skip("Playwright para Node no disponible en este equipo")
    paths, ref = make_lab(tmp_path)
    assert ref.ejecutar("test")["estado"] == "PASS"
    port = 8899
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    srv = subprocess.Popen([sys.executable, str(APP / "preview.py"), "serve", "--data", str(paths["data"]), "--port", str(port)],
                           cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        out = tmp_path / "shots"
        out.mkdir()
        script = APP / "tests" / "navegador.js"
        env_node = {**os.environ, "NODE_PATH": node.stdout.strip()}
        step1 = subprocess.run(["node", str(script), "nota", str(port), str(out)], capture_output=True, text=True, env=env_node, check=False, timeout=120)
        assert step1.returncode == 0, step1.stdout + step1.stderr
        before = json.loads(step1.stdout.strip().splitlines()[-1])
        demo.corte_posterior(paths["src"])
        demo.fg_posterior(paths["src"])
        step2 = subprocess.run(["node", str(script), "refresh", str(port), str(out), before["item"]], capture_output=True, text=True, env=env_node, check=False, timeout=120)
        assert step2.returncode == 0, step2.stdout + step2.stderr
        after = json.loads(step2.stdout.strip().splitlines()[-1])
        assert before["visibles"] == 1  # búsqueda por ITEM sobre el conjunto completo
        assert before["borrador"] == "Borrador local sin guardar"  # borrador ≠ nota guardada
        assert "01-nov" in before["cab_frozen"] and before["cab_frozen"] != before["cab_current"]
        assert after["antes_nota"] == "Nota de junta desde el navegador"  # persiste tras cerrar el navegador
        assert after["banner"].startswith("Actualización correcta")
        assert after["frozen"] == before["frozen"]  # Frozen sin cambios
        assert after["actual"] != before["actual"] or after["fg"] != before["fg"]  # Current/FG cambian
        assert "07-nov" in after["cab_current"]  # la fecha del corte Current cambia en el encabezado
        assert after["nota"] == "Nota de junta desde el navegador" and after["adelanto"] == "75"
        assert after["programa"] == after["embarques"] == after["faltante"] == "N/D"
        for state in (before, after):  # una sola Junta: sin cambio de escenario accesible
            assert state["pill"] == "PROVISIONAL — R1/R3 PENDIENTES — NO PUBLICABLE"
            assert state["selector_escenarios"] == 0 and state["texto_escenarios"] == []
        assert all(g.upper() in [x.upper() for x in after["grupos"]] for g in GROUPS)
        evid = os.environ.get("NEXUS_EVIDENCIA")
        if evid:
            for p in out.iterdir():
                (Path(evid) / p.name).write_bytes(p.read_bytes())
            (Path(evid) / "navegador.json").write_text(json.dumps({"antes": before, "despues": after}, ensure_ascii=False, indent=1), "utf-8")
    finally:
        srv.terminate()
        srv.wait(10)


def test_a14_rollback_de_vista_a_generacion_anterior(lab: Any) -> None:
    paths, ref = lab
    first = ref.store.vigente()["gen_id"]
    demo.corte_posterior(paths["src"])
    assert ref.ejecutar("test")["estado"] == "PASS"
    second = ref.store.vigente()["gen_id"]
    r = subprocess.run([sys.executable, str(APP / "preview.py"), "activar", "--data", str(paths["data"]), "--gen", first],
                       cwd=REPO, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    assert ref.store.vigente()["gen_id"] == first != second
    assert ref.store.frozen_lock("2026-11")["gen_id"] == first  # el Frozen bloqueado no cambia


# ---------------------------------------------------------------- contrato v3.10, reglas y concurrencia
from nexus_preview import status_v310 as v310  # noqa: E402
from nexus_preview.servicio import load_assignments, resolve_assignment  # noqa: E402

D = Decimal


def test_b01_contrato_v310_formulas_y_nd() -> None:
    assert v310.programa(D(1000), D(200), D(300)) == D(900)
    assert v310.programa(D(100), D(0), D(300)) == D(0)
    assert v310.sobreembarque(D(100), D(0), D(300)) == D(200)
    assert v310.faltante(D(900), D(250)) == D(650) and v310.faltante(D(100), D(250)) == D(0)
    for missing in ((None, D(0), D(1)), (D(1), None, D(1)), (D(1), D(0), None)):
        assert v310.programa(*missing) is None and v310.sobreembarque(*missing) is None  # nunca 0
    assert v310.faltante(None, D(5)) is None and v310.faltante(D(5), None) is None
    assert v310.faltante(D(5), D(-3)) is None  # FG negativo: no certificable (no se recorta a 0)
    weeks = [("WK45", D(100)), ("WK46", D(100)), ("WK47", D(100))]
    assert v310.cobertura_fg(weeks, D(50), D(150)) == ("WK46", "WK47")
    assert v310.cobertura_fg(weeks, D(0), D(1000)) == ("MES COMPLETO", None)
    assert v310.cobertura_fg(weeks, D(400), D(0)) == ("SIN PENDIENTES", None)
    assert v310.cobertura_fg(weeks, None, D(10)) == (None, None)  # sin embarques: N/D


def test_b02_clientes_no_cat_que_comparten_item_se_conservan(lab: Any) -> None:
    _, ref = lab
    plan = {i["item"]: i for i in demo._plan()}
    item = next(k for k, v in plan.items() if "QZXCAT    001" in {ln[5] for ln in v["lines"]} and len({ln[5] for ln in v["lines"]}) > 1)
    row = rows(ref.store.vigente())[item]
    detail = {d["clave"]: d for d in row["clientes_detalle"]}
    expected: dict[str, Decimal] = {}
    for ln in plan[item]["lines"]:
        if ln[5] != "QZXCAT    001" and not ln[2].startswith("12/"):
            expected[ln[5]] = expected.get(ln[5], D(0)) + D(ln[4])
    for key, qty in expected.items():
        assert D(detail[key]["frozen"]) == qty, key  # cada cliente no CAT conserva su demanda exacta
    assert "QZXCAT    001" not in detail  # la línea CAT del ERP no se suma (se sustituye)


def test_b03_shipped_vacio_es_desconocido(lab: Any) -> None:
    _, ref = lab
    b = rows(ref.store.vigente(), "B")["QZXA0006"]  # Portal REV-01 con Shipped vacío (homologado en B)
    assert b["frozen"]["value"] is None and b["frozen"]["status"] == "UNKNOWN_NOT_ZERO"


def test_b04_doble_clic_no_ejecuta_dos_trabajos(lab: Any) -> None:
    import threading

    paths, ref = lab
    demo.corte_posterior(paths["src"])
    gate = threading.Event()
    ref.hook = lambda stage: gate.wait(10)  # retiene el primer trabajo antes de conmutar
    c = client(ref)
    token = csrf(c)
    first = c.post("/refresh", headers={"X-CSRF": token})
    second = c.post("/refresh", headers={"X-CSRF": token})
    assert first.status_code == 202 and second.status_code == 409
    assert second.get_json()["estado"] in ("RUNNING", "BUSY")
    gate.set()
    for _ in range(200):
        if c.get("/refresh/estado").get_json()["estado"] != "RUNNING":
            break
        import time

        time.sleep(0.05)
    log = ref.store.historial()
    assert [x["estado"] for x in log].count("PASS") == 2  # inicial + uno solo de los dos clics


def test_b05_filtro_semana_y_busqueda_sobre_todo_el_universo(lab: Any) -> None:
    _, ref = lab
    gen = ref.store.vigente()
    esc = gen["escenarios"]["A"]
    c = client(ref)
    wk = next(w for w in esc["semanas"] if w["frozen_items"] > 0)
    html = c.get(f"/junta?week={wk['wk']}").get_data(as_text=True)
    assert html.count('data-item="') == wk["frozen_items"]
    last = sorted(r["item"] for r in esc["rows"])[-1]
    html = c.get(f"/junta?q={last}").get_data(as_text=True)
    assert html.count('data-item="') == 1 and f'data-item="{last}"' in html
    assert f"1 de {len(esc['rows'])} artículos" in html  # totales del universo completo


def test_b06_asignacion_cs_formato_pulso_exacta(tmp_path: Path) -> None:
    f = tmp_path / "asig.csv"
    f.write_text(
        "clave_cliente,cs,grupo,Match Type,Match Value,CSR,Active\n"
        "QZXDRV    001,,GRP-DRIV,,,,\n"
        ",,,CUSTOMER_GROUP,GRP-DRIV,CS-GRUPO,TRUE\n"
        ",,,CUSTOMERNO,QZXFRD    001,CS-CUENTA,TRUE\n"
        "QZXFRD    001,,GRP-DRIV,,,,\n"
        ",,,CUSTOMERNO,QZXZF     000,CS-1,TRUE\n"
        ",,,CUSTOMERNO,QZXZF     000,CS-2,TRUE\n",
        "utf-8",
    )
    a = load_assignments(f)
    assert resolve_assignment(a, "QZXDRV    001")["cs"] == "CS-GRUPO"
    assert resolve_assignment(a, "QZXFRD    001")["cs"] == "CS-CUENTA"  # la cuenta prevalece sobre el grupo
    z = resolve_assignment(a, "QZXZF     000")
    assert z["cs"] == "" and "CONFLICTO" in z["cs_origen"]
    assert resolve_assignment(a, "QZXDRV   001")["cs"] == ""  # otra clave (espacios): sin coincidencia aproximada


def test_b07_recarga_no_es_refresh(lab: Any) -> None:
    paths, ref = lab
    gen = ref.store.vigente()["gen_id"]
    demo.corte_posterior(paths["src"])
    c = client(ref)
    for _ in range(3):
        assert c.get("/junta").status_code == 200
    assert ref.store.vigente()["gen_id"] == gen  # el corte nuevo solo entra al pulsar Actualizar datos
    assert c.get("/daily").status_code == 302  # ruta histórica de PULSO redirige a la Junta


# ---------------------------------------------------------------- v0.2.1: una sola Junta Operativa
import dataset_sintetico as dsyn  # noqa: E402

from nexus_preview.servicio import PROVISIONAL_LABEL, evaluar_certificacion, vista_de  # noqa: E402


def test_c01_junta_sin_selector_de_escenarios_y_esc_ignorado(lab: Any) -> None:
    _, ref = lab
    gen = ref.store.vigente()
    c = client(ref)
    html = c.get("/junta").get_data(as_text=True)
    assert "Escenario A" not in html and "Escenario B" not in html and "Escenario DIAG" not in html
    assert "esc=" not in html  # ningún enlace de la pantalla principal cambia de escenario
    a_items = {r["item"] for r in gen["escenarios"]["A"]["rows"]}
    for forced in ("B", "DIAG", "OFICIAL"):
        h = c.get(f"/junta?esc={forced}").get_data(as_text=True)
        assert set(re.findall(r'data-item="([^"]+)"', h)) == a_items  # el parámetro se ignora
    assert c.get("/item?id=QZXA0006&esc=B").get_data(as_text=True).count("DIAGNÓSTICO NO OFICIAL") == 0


def test_c02_vista_provisional_a_identificada(lab: Any) -> None:
    _, ref = lab
    gen = ref.store.vigente()
    v = gen["vista_operativa"]
    assert v == vista_de(gen) and v["modo"] == "PROVISIONAL" and v["escenario"] == "A"
    html = client(ref).get("/junta").get_data(as_text=True)
    assert PROVISIONAL_LABEL in html and "no es una clasificación aprobada" in html
    assert "OFICIAL" not in gen["escenarios"]


def test_c03_b_no_se_mezcla_con_los_totales_de_a(lab: Any) -> None:
    _, ref = lab
    gen = ref.store.vigente()
    a, b = gen["escenarios"]["A"]["kpis"], gen["escenarios"]["B"]["kpis"]
    assert a["frozen"]["known_sum"] != b["frozen"]["known_sum"]
    html = client(ref).get("/junta?esc=B").get_data(as_text=True)
    assert f"de {a['items']} artículos" in html and f"de {b['items']} artículos" not in html
    assert f"{int(Decimal(a['frozen']['known_sum'])):,}" in html
    assert f"conocido {int(Decimal(b['frozen']['known_sum'])):,}" not in html


def test_c04_pendientes_no_se_convierten_en_aprobaciones(lab: Any) -> None:
    _, ref = lab
    gen = ref.store.vigente()
    assert gen["aprobaciones"]["reglas_r3"] == "AUSENTE" and gen["aprobaciones"]["registro_r1"] == "AUSENTE"
    pend = gen["escenarios"]["A"]["pendientes"]
    assert pend["claves_r3"] and pend["portal_r1"] and all(x["causa"] for x in pend["claves_r3"] + pend["portal_r1"])
    ev = evaluar_certificacion(gen)
    assert ev["certificable"] is False and ev["apta_para_revision"] is False and ev["publicacion"] == "NO_HABILITADA"


def test_c05_desconocidos_no_aparecen_como_cero(lab: Any) -> None:
    _, ref = lab
    gen = ref.store.vigente()
    nd = gen["escenarios"]["A"]["pendientes"]["items_no_determinables"]
    assert nd
    html = client(ref).get(f"/junta?q={nd[0]['item']}").get_data(as_text=True)
    row = html[html.index(f'data-item="{nd[0]["item"]}"'):]
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
    assert "N/D" in cells[6] and ">0<" not in cells[6]  # columna Frozen


def test_c06_diagnostico_conserva_comparacion_ab(lab: Any) -> None:
    _, ref = lab
    html = client(ref).get("/escenarios").get_data(as_text=True)
    for txt in ("DIAGNÓSTICO NO OFICIAL", "solo localhost", "Comparación de escenarios", "Líneas CAT ERP retiradas",
                "Identidades Portal pendientes R1", "Artículos afectados", "Motivos de bloqueo", "Decisiones necesarias"):
        assert txt in html, txt
    assert client(ref).get("/escenarios/item?id=QZXA0006&esc=B").status_code == 200


def test_c07_refresh_mantiene_frozen_notas_y_vista(lab: Any) -> None:
    paths, ref = lab
    before = ref.store.vigente()
    c = client(ref)
    item = before["escenarios"]["A"]["rows"][0]["item"]
    assert c.post("/api/cs", json={"item": item, "version": 0, "nota": "nota v021"}, headers={"X-CSRF": csrf(c)}).status_code == 200
    demo.corte_posterior(paths["src"])
    st = ref.ejecutar("test")
    after = ref.store.vigente()
    assert st["estado"] == "PASS" and st["vista"] == {"modo": "PROVISIONAL", "escenario": "A"}
    assert st["cambio_vista"] is False and st["cambio_aprobaciones"] is False
    assert after["vista_operativa"]["escenario"] == "A"
    assert after["sources"]["frozen_r"] == before["sources"]["frozen_r"]
    assert after["inputs"]["private"] == before["inputs"]["private"]  # huellas de reglas/matrices conservadas
    assert "nota v021" in client(ref).get("/junta").get_data(as_text=True)


def test_c08_refresh_fallido_conserva_generacion_y_vista(lab: Any) -> None:
    paths, ref = lab
    gen = ref.store.vigente()
    (paths["src"] / "INV_STA299.TXT").write_bytes(b"corrupto")
    assert ref.ejecutar("test")["estado"] == "ERROR"
    now = ref.store.vigente()
    assert now["gen_id"] == gen["gen_id"] and now["vista_operativa"] == gen["vista_operativa"]


def test_c09_certificacion_rechazada_sin_aprobaciones(lab: Any) -> None:
    paths, ref = lab
    c = client(ref)
    assert c.post("/api/certificar").status_code == 403  # sin CSRF
    r = c.post("/api/certificar", headers={"X-CSRF": csrf(c)})
    assert r.status_code == 409 and r.get_json()["resultado"] == "NO_CERTIFICABLE_SIN_APROBACIONES"
    cli = subprocess.run([sys.executable, str(APP / "preview.py"), "certificar", "--data", str(paths["data"])],
                         cwd=REPO, capture_output=True, text=True, check=False)
    assert cli.returncode == 3 and '"RECHAZADA"' in cli.stdout


def _approved_lab(root: Path, complete: bool = True, escenario: str = "A") -> tuple[dict[str, Path], PreviewRefresh]:
    lab = dsyn.build(root / "lab")
    priv = lab["priv"]
    rules = dsyn.pf.november_rules()
    rules["approval"] = "CRC_APPROVED"
    rules["version"] = "core-sintetico-aprobado"
    if complete:
        rules["customer_classes"]["OTHER"] = [*rules["customer_classes"]["OTHER"], dsyn.NEW_KEY]
    (priv / "rules.json").write_text(json.dumps(rules), "utf-8")
    table = ("item_portal,item_erp,status,approval_id\n"
             "QZX PORTAL 1,QZXITEM1,APPROVED_MAP,R1-1\nQZX REV-01,QZXITEM3,APPROVED_MAP,R1-2\nQZX REV-02,QZXITEM3,APPROVED_MAP,R1-3\n")
    (priv / "homologacion_ok.csv").write_text(table, "utf-8")
    entries = [
        dsyn.pf.ledger_entry("R1-1", "R1_MAP", ["QZX PORTAL 1"], "QZXITEM1", approved_at="2026-10-30"),
        dsyn.pf.ledger_entry("R1-2", "R1_MAP", ["QZX REV-01"], "QZXITEM3", approved_at="2026-10-30"),
        dsyn.pf.ledger_entry("R1-3", "R1_MAP", ["QZX REV-02"], "QZXITEM3", approved_at="2026-10-30"),
        dsyn.pf.ledger_entry("R1-EQ", "R1_REVISION_EQUIVALENCE", ["QZX REV-01", "QZX REV-02"], "QZXITEM3", approved_at="2026-10-30"),
    ]
    (priv / "registro_ok.json").write_text(json.dumps({"schema": "nexus.core.approvals-ledger/1", "entries": entries}), "utf-8")
    data = root / "data"
    data.mkdir()
    cfg = {"schema": "nexus.preview.config/1", "month": "2026-11", "src": str(lab["src"]),
           "r3_provisional": str(lab["r3"]), "r1_provisional": str(lab["r1"]), "rules": str(priv / "rules.json"),
           "homologation": str(priv / "homologacion_ok.csv"), "ledger": str(priv / "registro_ok.json"),
           "approvers": str(priv / "aprobadores.json"), "escenario_por_defecto": escenario}
    (data / "preview.json").write_text(json.dumps(cfg), "utf-8")
    return {**lab, "data": data}, PreviewRefresh(data, "test")


def test_c10_aprobaciones_validas_producen_una_vista_verificada_no_publicada(tmp_path: Path) -> None:
    paths, ref = _approved_lab(tmp_path)
    assert ref.ejecutar("test")["estado"] == "PASS"
    gen = ref.store.vigente()
    v = gen["vista_operativa"]
    assert v["modo"] == "VERIFICADA" and v["escenario"] == "OFICIAL"
    assert gen["aprobaciones"]["reglas_r3"] == "APROBADAS_V2" and gen["aprobaciones"]["registro_r1"] == "VERIFICADO"
    assert set(gen["aprobaciones"]["huellas"]) == {"rules", "homologation", "ledger", "approvers"}
    of = gen["escenarios"]["OFICIAL"]
    assert of["completo"] and not of["pendientes"]["claves_r3"] and not of["pendientes"]["portal_r1"]
    c = client(ref)
    html = c.get("/junta").get_data(as_text=True)
    assert "VERIFICADA (R1/R3 APROBADAS) — NO PUBLICADA" in html and "Escenario" not in html.split("Agenda")[0].split("<main>")[1].split("eyebrow")[0]
    ofi_items = {r["item"] for r in of["rows"]}
    assert set(re.findall(r'data-item="([^"]+)"', html)) == ofi_items  # una sola vista: la verificada
    r = c.post("/api/certificar", headers={"X-CSRF": csrf(c)})
    body = r.get_json()
    assert r.status_code == 409 and body["resultado"] == "CERTIFICACION_FUERA_DE_PREVIEW"
    assert body["apta_para_revision"] is True and body["certificable"] is False and body["publicacion"] == "NO_HABILITADA"


def test_c11_aprobaciones_incompletas_mantienen_vista_provisional(tmp_path: Path) -> None:
    _, ref = _approved_lab(tmp_path, complete=False)
    assert ref.ejecutar("test")["estado"] == "PASS"
    gen = ref.store.vigente()
    v = gen["vista_operativa"]
    assert "OFICIAL" in gen["escenarios"] and not gen["escenarios"]["OFICIAL"]["completo"]
    assert v["modo"] == "PROVISIONAL" and v["escenario"] == "A"
    assert any("aprobaciones incompletas" in m for m in v["motivos"])


def test_c12_cambio_de_aprobaciones_queda_explicito(tmp_path: Path) -> None:
    paths, ref = _approved_lab(tmp_path)
    cfg = json.loads((paths["data"] / "preview.json").read_text("utf-8"))
    for k in ("rules", "homologation", "ledger", "approvers"):
        cfg.pop(k)
    (paths["data"] / "preview.json").write_text(json.dumps(cfg), "utf-8")
    assert ref.ejecutar("test")["vista"]["modo"] == "PROVISIONAL"
    cfg = json.loads((paths["data"] / "preview.json").read_text("utf-8"))
    priv = paths["priv"]
    cfg.update(rules=str(priv / "rules.json"), homologation=str(priv / "homologacion_ok.csv"),
               ledger=str(priv / "registro_ok.json"), approvers=str(priv / "aprobadores.json"))
    (paths["data"] / "preview.json").write_text(json.dumps(cfg), "utf-8")
    st = ref.ejecutar("test")
    assert st["estado"] == "PASS" and st["vista"]["modo"] == "VERIFICADA"
    assert st["cambio_vista"] is True and st["cambio_aprobaciones"] is True
    assert any("CAMBIO EN APROBACIONES" in a for a in st["advertencias"])
    assert any("CAMBIO DE VISTA OPERATIVA" in a for a in st["advertencias"])


def test_c13_configuracion_no_elige_escenario(tmp_path: Path) -> None:
    paths, ref = make_lab(tmp_path)
    cfg = json.loads((paths["data"] / "preview.json").read_text("utf-8"))
    cfg["escenario_por_defecto"] = "B"
    (paths["data"] / "preview.json").write_text(json.dumps(cfg), "utf-8")
    assert ref.ejecutar("test")["estado"] == "PASS"
    gen = ref.store.vigente()
    assert gen["vista_operativa"]["escenario"] == "A"
    assert any("se ignora" in w for w in gen["warnings"])


def test_c14_generacion_v02_sigue_legible(lab: Any) -> None:
    _, ref = lab
    gen = ref.store.vigente()
    legacy = {k: v for k, v in gen.items() if k not in ("vista_operativa", "aprobaciones")}
    for e in legacy["escenarios"].values():
        e.pop("completo", None)
    assert vista_de(legacy)["escenario"] == "A" and vista_de(legacy)["modo"] == "PROVISIONAL"
