"""FIX5 — Embarques (ASBG/CFDI) en el Refresh One-Click junto con SSRS y FG (dobles OFFLINE).

Regla comercial resuelta (9-nov-2026): la factura CFDI emitida ES el embarque; fecha FECEXP.
P: camino exitoso (un clic → tres fuentes → v3.10 → generación atómica; Frozen y notas intactos).
N: negativos (conector, credencial, ODBC, timeout, nulos, universo, identidad exacta, duplicados,
   ajustes documentales, captura corrupta, periodo, concurrencia, parcial, fallo antes de conmutar,
   publicación oficial, Frozen) y acreditación /2 (fabricada, aprobador, decisión, huella, origen).
F (FIX5.1): procedencia de la captura (fabricada, sin procedencia, legado FIX4, hash, periodo,
   sustitución, otra configuración/conector, conteos) · C (FIX5.1): ausencia ≠ cero automático.
Sin conexiones corporativas: SQL Server, credenciales y SSRS son simuladores.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tests"))

import acreditar_embarques  # noqa: E402
import preflight  # noqa: E402
import simuladores as sim  # noqa: E402
import test_conectores as tc  # noqa: E402
import test_preview as tp  # noqa: E402
from nexus_preview.conectores import ConectorError, embarques  # noqa: E402
from nexus_preview.conectores import acreditacion_embarques as ae  # noqa: E402
from nexus_preview.servicio import evaluar_certificacion  # noqa: E402

lab = tc.lab
ITEM = "QZXA0016"
FILAS = [(ITEM, "300", 2, 2, 0), ("QZXA0010", "40", 1, 1, 0)]
SHA_EMBARQUES_FIX4 = "244ee1989edde2b941164e8527f32277b289e7a5e97950dce324d221e59e7dd9"
A12 = "test_a12"


def _exige(condicion: object, mensaje: str = "") -> None:
    if not condicion:
        pytest.fail(mensaje or "condición no cumplida")


def _aprobadores(paths: dict[str, Path]) -> None:
    (paths["priv"] / "aprobadores.json").write_text(
        json.dumps({"approvers": ["aprobador.sintetico"]}), "utf-8"
    )
    cfgp = json.loads((paths["data"] / "preview.json").read_text("utf-8"))
    cfgp["approvers"] = str(paths["priv"] / "aprobadores.json")
    (paths["data"] / "preview.json").write_text(json.dumps(cfgp), "utf-8")


def _cfg_emb(data: Path) -> dict[str, Any]:
    return json.loads((data / "conectores.json").read_text("utf-8"))["embarques"]


DECISION = "MINUTA-SINTETICA-001"


def _referencia(data: Path, filas: list[Any] | None = None) -> Path:
    """Captura de referencia por el MISMO camino que en ASMXC1200: ``preflight.py --nivel consulta``
    (con simuladores), que escribe .raw, .csv y la procedencia .json de la ejecución."""
    conn = sim.Conexion(filas if filas is not None else FILAS)
    deps = {"proveedor": sim.proveedor, "fabrica": sim.fabrica(conn), "usuario": "ucbaa"}
    args = ["--data", str(data), "--conector", "embarques", "--mes", "2026-11"]
    preflight.main([*args, "--nivel", "consulta", "--autorizo"], deps)
    out = sorted((data / "preflight" / "embarques").glob("*.csv"))
    _exige(out, "el preflight no produjo captura")
    return out[-1]


def _acreditar(
    data: Path,
    ausencia: str = "si",
    aprobador: str = "aprobador.sintetico",
    captura: Path | None = None,
    decision: str = DECISION,
) -> int:
    ref = captura or _referencia(data)
    args = ["--data", str(data), "--captura", str(ref), "--aprobador", aprobador]
    return acreditar_embarques.main(
        [*args, "--ausencia-es-cero", ausencia, "--referencia-decision", decision]
    )


def _vivo(ref: Any, paths: dict[str, Path], filas: list[Any] | None = None, **cfg: Any) -> Any:
    conf = {"embarques": {"activo": True, **cfg.pop("embarques", {})}, **cfg}
    tc.live(ref, paths, cfg=conf, emb_rows=filas if filas is not None else FILAS)
    _aprobadores(paths)
    return ref


def _fila(gen: dict[str, Any], item: str = ITEM) -> dict[str, Any]:
    return next(
        r
        for r in gen["escenarios"][gen["vista_operativa"]["escenario"]]["rows"]
        if r["item"] == item
    )


def _celdas(html: str, item: str = ITEM) -> list[str]:
    row = html[html.index(f'data-item="{item}"') :]
    return re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)


# ================================================================ P. camino exitoso
def test_p01_un_clic_actualiza_ssrs_fg_y_embarques_y_calcula_v310(lab: Any) -> None:
    paths, ref = lab
    before = ref.store.vigente()
    lock = json.dumps(ref.store.frozen_lock("2026-11"), sort_keys=True)
    _vivo(ref, paths)
    c = tp.client(ref)
    nota = {"item": ITEM, "version": 0, "nota": "nota FIX5", "adelanto": "75"}
    _exige(c.post("/api/cs", json=nota, headers={"X-CSRF": tp.csrf(c)}).status_code == 200)
    _exige(_acreditar(paths["data"]) == 0, "acreditación /2 de la referencia sintética")
    st = ref.ejecutar("t")
    con = st["conectores"]
    _exige(st["estado"] == "PASS" and not st["parcial"], str(st))
    _exige(
        all(con[f]["estado"] == "CAPTURADO" for f in ("backlog_ssrs", "fg_odbc", "embarques")),
        str(con),
    )
    gen = ref.store.vigente()
    emb = gen["embarques"]
    _exige(emb["acreditado"] and emb["estado"] == "CONSULTA_DIRECTA", str(emb))
    _exige(emb["captura"]["archivo"] == con["embarques"]["archivo"], "captura de ESTE Refresh")
    _exige(emb["regla"] == ae.REGLA and "CFDI" in emb["naturaleza"])
    _exige(
        gen["inputs"]["embarques_conector"]
        == {"estado": "CONSULTA_DIRECTA", "corte": "2026-11-08T15:00:00Z"}
    )
    _exige(
        gen["sources"]["current_r"]["origen"] == "CONSULTA_DIRECTA"
        and gen["sources"]["fg"]["origen"] == "CONSULTA_DIRECTA"
    )
    fila = _fila(gen)
    _exige(fila["embarques"] == "300", str(fila.get("embarques")))
    frozen = Decimal(fila["frozen"]["value"])
    html = tp.client(ref).get(f"/junta?q={ITEM}").get_data(as_text=True)
    programa = max(Decimal(0), frozen + 75 - 300)
    _exige(f"{int(programa):,}" in html, "Programa v3.10 = max(0, Frozen + Adelanto − Embarques)")
    _exige(
        'id="kpi-embarques"' in html
        and "Embarques: facturación CFDI (ASBG) consultada en este Refresh" in html
    )
    _exige(json.dumps(ref.store.frozen_lock("2026-11"), sort_keys=True) == lock, "lock Frozen")
    for k in ("frozen_r", "frozen_p"):
        _exige(gen["sources"][k] == before["sources"][k], f"{k} mismo SHA-256")
    _exige(set(tp.rows(before)) == set(tp.rows(gen)), "universo mensual = Frozen")
    _exige("nota FIX5" in html and 'value="75"' in html, "notas y adelantos intactos")
    _exige(any("NO se descuentan" in w for w in gen["warnings"]), "alcance de ajustes declarado")


def test_p02_segunda_consulta_identica_es_un_corte_nuevo(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    _exige(_acreditar(paths["data"]) == 0)
    a = ref.ejecutar("t")["conectores"]["embarques"]
    ref.clock = sim.reloj("2026-11-08", "17:00:00")
    st = ref.ejecutar("t")
    b = st["conectores"]["embarques"]
    _exige(
        b["archivo"] != a["archivo"] and b["sha256"] == a["sha256"] and b["reutilizada"] is False
    )
    gen = ref.store.vigente()
    _exige(gen["embarques"]["captura"]["archivo"] == b["archivo"])
    _exige(gen["inputs"]["embarques_conector"]["corte"] == "2026-11-08T17:00:00Z")


@pytest.mark.parametrize(("ausencia", "esperado"), [("si", "0"), ("no", None)])
def test_p03_decision_explicita_sobre_items_sin_factura(
    lab: Any, ausencia: str, esperado: Any
) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    _exige(_acreditar(paths["data"], ausencia) == 0)
    _exige(ref.ejecutar("t")["estado"] == "PASS")
    gen = ref.store.vigente()
    otro = next(r for r in gen["escenarios"]["A"]["rows"] if r["item"] not in (ITEM, "QZXA0010"))
    _exige(otro["embarques"] == esperado, f"{ausencia}: {otro['embarques']}")
    html = tp.client(ref).get("/junta").get_data(as_text=True)
    kpi = html[html.index('id="kpi-embarques"') :][:400]
    _exige(("sin dato" in kpi) == (esperado is None), kpi)


def test_p04_conector_fix4_certificado_sin_cambios() -> None:
    p = APP / "nexus_preview" / "conectores" / "embarques.py"
    _exige(
        hashlib.sha256(p.read_bytes()).hexdigest() == SHA_EMBARQUES_FIX4,
        "embarques.py FIX4 intacto",
    )


# ================================================================ N. negativos del Refresh
@pytest.mark.parametrize(
    ("dep", "codigo"),
    [
        ("falla_odbc", "ODBC_CONSULTA_FALLIDA"),
        ("sin_credencial", "CREDENCIAL_NO_ENCONTRADA"),
        ("timeout_invalido", "EMBARQUES_TIMEOUT_INVALIDO"),
        ("duplicado", "EMBARQUES_ITEM_DUPLICADO"),
        ("facturas_duplicadas", "EMBARQUES_FACTURAS_DUPLICADAS"),
    ],
)
def test_n01_fallo_del_conector_conserva_el_corte_anterior_marcado(
    lab: Any, dep: str, codigo: str
) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    _exige(_acreditar(paths["data"]) == 0)
    _exige(ref.ejecutar("t")["estado"] == "PASS")
    previo = ref.store.vigente()["embarques"]["captura"]
    if dep == "falla_odbc":
        ref.deps["fabrica_embarques"] = sim.fabrica(sim.Conexion([], falla=True))
    elif dep == "sin_credencial":

        def sin(target: str) -> Any:
            if target == "PULSO_NEXUS/embarques":
                raise ConectorError("CREDENCIAL_NO_ENCONTRADA", target)
            return sim.proveedor(target)

        ref.deps["proveedor"] = sin
    elif dep == "timeout_invalido":
        sim.config_conectores(paths["data"], embarques={"activo": True, "timeout_consulta_s": 99})
    elif dep == "duplicado":
        ref.deps["fabrica_embarques"] = sim.fabrica(sim.Conexion([*FILAS, (ITEM, "1", 1, 1, 0)]))
    else:
        conn = sim.Conexion(FILAS)
        conn.embarques_facturas = ["F000001", "F000001"]
        ref.deps["fabrica_embarques"] = sim.fabrica(conn)
    ref.clock = sim.reloj("2026-11-09", "15:00:00")
    st = ref.ejecutar("t")
    emb_c = st["conectores"]["embarques"]
    _exige(
        emb_c["estado"] == "FALLO" and emb_c["codigo"] == codigo and st["parcial"] is True,
        str(emb_c),
    )
    gen = ref.store.vigente()
    if dep == "timeout_invalido":  # la huella de la consulta no cambia: solo el timeout
        _exige(gen["embarques"]["acreditado"] is True)
    _exige(gen["embarques"]["estado"] == "CONTINGENCIA" and gen["embarques"]["captura"] == previo)
    _exige(any(w.startswith("EMBARQUES ANTERIOR") and "24 h 00 min" in w for w in gen["warnings"]))
    html = tp.client(ref).get("/junta").get_data(as_text=True)
    _exige("EMBARQUES ANTERIOR" in html and "consultada en este Refresh" not in html)
    _exige(sim.SECRETO_EMB not in json.dumps(st) and sim.SECRETO_EMB not in html, "sin secretos")


def test_n02_fallo_con_detener_no_publica_generacion_parcial(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths, embarques={"politica_fallo": "DETENER"})
    _exige(_acreditar(paths["data"]) == 0)
    _exige(ref.ejecutar("t")["estado"] == "PASS")
    vigente = ref.store.vigente()["gen_id"]
    ref.deps["fabrica_embarques"] = sim.fabrica(sim.Conexion([], falla=True))
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")
    _exige(st["estado"] == "ERROR" and any("DETENER" in m for m in st["motivos"]), str(st))
    _exige(ref.store.vigente()["gen_id"] == vigente)


@pytest.mark.parametrize("cantidad", [None, "300"])  # "300" con un renglón nulo: suma parcial
def test_n03_cantidad_nula_queda_desconocida_aunque_ausencia_sea_cero(
    lab: Any, cantidad: Any
) -> None:
    paths, ref = lab
    filas = [(ITEM, cantidad, 2, 2, 1), ("QZXA0010", "40", 1, 1, 0)]
    _vivo(ref, paths, filas=filas)
    _exige(_acreditar(paths["data"], "si") == 0)
    _exige(ref.ejecutar("t")["estado"] == "PASS")
    _exige(_fila(ref.store.vigente())["embarques"] is None, "nulo ≠ 0")
    cells = _celdas(tp.client(ref).get(f"/junta?q={ITEM}").get_data(as_text=True))
    _exige(
        "N/D" in cells[7] and "N/D" in cells[8] and "N/D" in cells[12],
        "Programa, Embarques y Faltante N/D",
    )


def test_n04_item_fuera_del_universo_y_no_exacto(lab: Any) -> None:
    paths, ref = lab
    filas = [("QZXA0999", "500", 1, 1, 0), (ITEM.lower(), "300", 2, 2, 0)]
    _vivo(ref, paths, filas=filas)
    _exige(_acreditar(paths["data"], "no") == 0)
    st = ref.ejecutar("t")
    gen = ref.store.vigente()
    rows = {r["item"]: r for r in gen["escenarios"]["A"]["rows"]}
    _exige("QZXA0999" not in rows, "fuera del universo: no entra al cálculo")
    _exige(rows[ITEM]["embarques"] is None, "sin matching aproximado: 'qzxa0016' ≠ 'QZXA0016'")
    cap = (
        paths["data"] / "capturas" / "embarques" / f"{st['conectores']['embarques']['archivo']}.raw"
    )
    _exige(b"QZXA0999" in cap.read_bytes(), "el registro de origen se conserva en la captura")


def test_n05_alcance_de_ajustes_documentales_declarado_no_resuelto(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    _exige(_acreditar(paths["data"]) == 0)
    doc = json.loads((paths["data"] / ae.ARCHIVO).read_text("utf-8"))
    _exige(
        "trata_cancelaciones" not in doc and "trata_notas_credito" not in doc,
        "no se afirma lo no verificable",
    )
    _exige("NO se descuentan" in doc["alcance_ajustes"])
    _exige(ref.ejecutar("t")["estado"] == "PASS")
    _exige(any("Cancelaciones, notas de crédito" in w for w in ref.store.vigente()["warnings"]))


def test_n06_captura_corrupta_detiene_y_conserva(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    _exige(_acreditar(paths["data"]) == 0)
    st = ref.ejecutar("t")
    vigente = ref.store.vigente()["gen_id"]
    p = paths["data"] / "capturas" / "embarques" / f"{st['conectores']['embarques']['archivo']}.raw"
    p.chmod(0o600)
    p.write_bytes(p.read_bytes().replace(b"300", b"900"))
    ref.deps["fabrica_embarques"] = sim.fabrica(
        sim.Conexion([], falla=True)
    )  # obliga a usar la corrupta
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")
    _exige(st["estado"] == "ERROR" and "CAPTURA_ALTERADA" in " ".join(st["motivos"]), str(st))
    _exige(ref.store.vigente()["gen_id"] == vigente)


def test_n07_periodo_incorrecto_no_alimenta_kpi(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    _exige(_acreditar(paths["data"]) == 0)
    ref.clock = sim.reloj("2026-12-02", "15:00:00")  # la consulta cubre otro mes
    ref.ejecutar("t")
    gen = ref.store.vigente()
    _exige(
        gen["embarques"]["acreditado"] is False
        and all(r["embarques"] is None for r in gen["escenarios"]["A"]["rows"])
    )


def test_n07b_captura_que_no_empieza_el_dia_1_no_alimenta_kpi(lab: Any, monkeypatch: Any) -> None:
    """Captura de ESTE Refresh, acreditación vigente, pero periodo declarado incompleto."""
    paths, ref = lab
    _vivo(ref, paths)
    _exige(_acreditar(paths["data"]) == 0)
    real = embarques.capturar

    def parcial(*args: Any, **kw: Any) -> Any:
        raw, meta = real(*args, **kw)
        return raw, {**meta, "periodo": ["2026-11-05", meta["periodo"][1]]}

    monkeypatch.setattr(embarques, "capturar", parcial)
    st = ref.ejecutar("t")
    gen = ref.store.vigente()
    _exige(st["conectores"]["embarques"]["estado"] == "CAPTURADO", str(st["conectores"]))
    emb = gen["embarques"]
    _exige(
        emb["acreditado"] is False and any("desde el día 1" in m for m in emb["motivos"]), str(emb)
    )
    _exige(all(r["embarques"] is None for r in gen["escenarios"]["A"]["rows"]))


def test_n08_concurrencia_y_fallo_antes_de_conmutar(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    _exige(_acreditar(paths["data"]) == 0)
    ref.lock_path.write_text("ocupado", "utf-8")
    _exige(ref.ejecutar("t")["estado"] == "BUSY", "solicitud concurrente rechazada")
    ref.lock_path.unlink()
    vigente = ref.store.vigente()["gen_id"]

    def falla(etapa: str) -> None:
        raise RuntimeError(f"fallo simulado en {etapa}")

    ref.hook = falla
    st = ref.ejecutar("t")
    _exige(st["estado"] == "ERROR" and ref.store.vigente()["gen_id"] == vigente, str(st))


def test_n09_publicacion_oficial_y_frozen_protegidos(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    _exige(_acreditar(paths["data"]) == 0)
    _exige(ref.ejecutar("t")["estado"] == "PASS")
    gen = ref.store.vigente()
    _exige(
        evaluar_certificacion(gen)["certificable"] is False, "Preview nunca certifica ni publica"
    )
    lock = json.dumps(ref.store.frozen_lock("2026-11"), sort_keys=True)
    frozen = paths["src"] / "R_20261101.TXT"
    frozen.write_bytes(frozen.read_bytes() + b"\n")  # intento de alterar el Frozen
    ref.clock = sim.reloj("2026-11-09")
    st = ref.ejecutar("t")
    _exige(st["estado"] == "ERROR" and ref.store.vigente()["gen_id"] == gen["gen_id"], str(st))
    _exige(json.dumps(ref.store.frozen_lock("2026-11"), sort_keys=True) == lock)


# ================================================================ acreditación /2
@pytest.fixture
def datos(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    sim.config_conectores(data, embarques={"activo": False})
    priv = tmp_path / "priv"
    priv.mkdir()
    (priv / "aprobadores.json").write_text(
        json.dumps({"approvers": ["aprobador.sintetico"]}), "utf-8"
    )
    (data / "preview.json").write_text(
        json.dumps({"approvers": str(priv / "aprobadores.json")}), "utf-8"
    )
    return data


APROB = frozenset({"aprobador.sintetico"})


def test_a01_emision_controlada_y_verificable(datos: Path) -> None:
    _exige(_acreditar(datos) == 0)
    ok, motivos, info = ae.verificar(datos, _cfg_emb(datos), APROB)
    _exige(ok and info["ausencia_es_cero"] is True, str(motivos))
    doc = json.loads((datos / ae.ARCHIVO).read_text("utf-8"))
    _exige(doc["evento_comercial_cfdi_validado"] is True and "fecha_es_embarque_fisico" not in doc)
    _exige(doc["regla"] == "CFDI_EMITIDA_ES_EMBARQUE" and doc["fecha_evento"] == "FECEXP")
    _exige(_cfg_emb(datos)["activo"] is False, "acreditar no activa el conector")


@pytest.mark.parametrize(
    "caso",
    [
        "inventada",
        "aprobador",
        "sin_decision",
        "regla",
        "evento",
        "origen_alterado",
        "items",
        "otra_consulta",
        "sin_referencia_decision",
        "decision_incoherente",
        "procedencia_sustituida",
        # FIX5.1: cada control aislado (una sola condición rota por caso)
        "aprobador_retirado",
        "huella_del_documento",
        "ausencia_no_booleana",
        "raw_alterado_tras_acreditar",
    ],
)
def test_a02_acreditacion_fabricada_o_alterada_se_rechaza(datos: Path, caso: str) -> None:
    _exige(_acreditar(datos) == 0)
    p = datos / ae.ARCHIVO
    doc = json.loads(p.read_text("utf-8"))
    cfg = _cfg_emb(datos)
    if caso == "inventada":
        doc["captura_sha256"] = "c" * 64
    elif caso == "aprobador":
        doc["aprobado_por"] = "otro"
    elif caso == "sin_decision":
        doc.pop("ausencia_es_cero")
    elif caso == "regla":
        doc["fecha_evento"] = "FECHA_ENTREGA"
    elif caso == "evento":
        doc["evento_comercial_cfdi_validado"] = False
    elif caso == "origen_alterado":
        ref = datos / doc["captura_referencia"]
        ref.write_bytes(ref.read_bytes() + b"QZXZ,1,1,1,0\n")
    elif caso == "items":
        doc["items_referencia"] += 1
    elif caso == "sin_referencia_decision":
        doc["decision_ausencia"]["referencia"] = "  "
    elif caso == "decision_incoherente":
        doc["decision_ausencia"]["ausencia_es_cero"] = not doc["ausencia_es_cero"]
    elif caso == "procedencia_sustituida":
        side = datos / doc["procedencia"]
        side.write_text(side.read_text("utf-8").replace('"conector"', '"conector" '), "utf-8")
    elif caso == "huella_del_documento":
        doc["huella_config_embarques"] = "a" * 64
    elif caso == "ausencia_no_booleana":
        doc["ausencia_es_cero"] = doc["decision_ausencia"]["ausencia_es_cero"] = "si"
    elif caso == "raw_alterado_tras_acreditar":
        raw = datos / doc["captura_referencia"].replace(".csv", ".raw")
        raw.write_bytes(raw.read_bytes() + b"\n")
    elif caso == "otra_consulta":
        cfg = {**cfg, "servidor": "otro-servidor"}
    no_reescribe = ("origen_alterado", "otra_consulta", "procedencia_sustituida")
    if caso not in (*no_reescribe, "aprobador_retirado", "raw_alterado_tras_acreditar"):
        p.write_text(json.dumps(doc), "utf-8")
    aprob = frozenset({"otro.aprobador"}) if caso == "aprobador_retirado" else APROB
    ok, motivos, _ = ae.verificar(datos, cfg, aprob)
    _exige(not ok and motivos, caso)


def test_a03_cli_rechaza_sin_origen_aprobador_o_decision(datos: Path, tmp_path: Path) -> None:
    ref = _referencia(datos)
    fuera = tmp_path / "copia.csv"
    fuera.write_bytes(ref.read_bytes())
    base = ["--data", str(datos), "--ausencia-es-cero", "si", "--referencia-decision", DECISION]
    _exige(
        acreditar_embarques.main(
            [*base, "--captura", str(fuera), "--aprobador", "aprobador.sintetico"]
        )
        == 2
    )
    _exige(acreditar_embarques.main([*base, "--captura", str(ref), "--aprobador", "intruso"]) == 1)
    sin = ["--data", str(datos), "--captura", str(ref), "--aprobador", "aprobador.sintetico"]
    with pytest.raises(SystemExit):  # sin --ausencia-es-cero: no hay valor por omisión
        acreditar_embarques.main([*sin, "--referencia-decision", DECISION])
    with pytest.raises(SystemExit):  # sin --referencia-decision: la omisión no es aprobación
        acreditar_embarques.main([*sin, "--ausencia-es-cero", "no"])
    _exige(_acreditar(datos, "si", captura=ref, decision="   ") == 2, "referencia en blanco")
    _exige(not (datos / ae.ARCHIVO).exists(), "ninguna acreditación emitida")


def test_a04_acreditacion_v1_mantiene_su_logica_original(datos: Path) -> None:
    """Compatibilidad: una /1 sin evidencia física sigue sin acreditar (no se relaja)."""
    (datos / ae.ARCHIVO).write_text(
        json.dumps(
            {
                "schema": "nexus.preview.acreditacion-embarques/1",
                "acreditado": True,
                "aprobado_por": "aprobador.sintetico",
                "fecha_es_embarque_fisico": False,
            }
        ),
        "utf-8",
    )
    ok, motivos = embarques.acreditacion_vigente(datos / ae.ARCHIVO, APROB, datos)
    _exige(not ok and any("fecha_es_embarque_fisico" in m for m in motivos))


# ======================================================= FIX5.1: procedencia de la captura
def _sidecar(csv: Path) -> Path:
    return csv.with_suffix(".json")


def _rescribir(side: Path, cambio: Any) -> None:
    doc = json.loads(side.read_text("utf-8"))
    cambio(doc)
    side.write_text(json.dumps(doc), "utf-8")


@pytest.mark.parametrize(
    "caso",
    [
        "fabricada_sin_procedencia",
        "procedencia_legado_fix4",
        "hash_alterado",
        "periodo_no_empieza_dia_1",
        "periodo_posterior_a_la_ejecucion",
        "sustitucion_csv_y_raw",
        "raw_distinto_del_csv",
        "otra_configuracion",
        "otro_conector",
        "conteos_incoherentes",
    ],
)
def test_f01_captura_sin_procedencia_corroborable_no_se_acredita(datos: Path, caso: str) -> None:
    ref = _referencia(datos)
    side, raw = _sidecar(ref), ref.with_suffix(".raw")
    if caso == "fabricada_sin_procedencia":  # CSV con formato correcto colocado a mano
        ref = ref.with_name("20261109T000000Z.csv")
        ref.write_bytes(raw.read_bytes())
    elif caso == "procedencia_legado_fix4":  # como el preflight FIX4 del 9-nov: sin huella ni SQL

        def legado(d: dict[str, Any]) -> None:
            for k in ae.meta_procedencia({}):
                d["meta"].pop(k, None)

        _rescribir(side, legado)
    elif caso == "hash_alterado":
        _rescribir(side, lambda d: d.update(sha256="0" * 64))
    elif caso == "periodo_no_empieza_dia_1":
        _rescribir(
            side, lambda d: d["meta"].update(periodo=["2026-11-02", d["meta"]["periodo"][1]])
        )
    elif caso == "periodo_posterior_a_la_ejecucion":
        _rescribir(side, lambda d: d.update(instante_utc="2026-10-31T23:00:00Z"))
    elif caso == "sustitucion_csv_y_raw":  # otra captura con el mismo formato en lugar del original
        otro, _ = embarques.capturar(
            _cfg_emb(datos),
            "2026-11",
            dt.date(2026, 11, 9),
            sim.proveedor,
            sim.fabrica(sim.Conexion([(ITEM, "999", 1, 1, 0)])),
        )
        ref.write_bytes(otro)
        raw.write_bytes(otro)
    elif caso == "raw_distinto_del_csv":
        raw.write_bytes(raw.read_bytes() + b"\n")
    elif caso == "otra_configuracion":
        sim.config_conectores(datos, embarques={"activo": False, "servidor": "otro-servidor"})
    elif caso == "otro_conector":
        _rescribir(side, lambda d: d.update(conector="fg_odbc"))
    else:
        _rescribir(side, lambda d: d["meta"].update(facturas_verificadas=99))
    _exige(_acreditar(datos, "no", captura=ref) == 1, caso)
    _exige(not (datos / ae.ARCHIVO).exists(), "ninguna acreditación emitida")


def test_f02_procedencia_registrada_al_consultar_y_documentada(datos: Path) -> None:
    ref = _referencia(datos)
    meta = json.loads(_sidecar(ref).read_text("utf-8"))["meta"]
    _exige(meta["conector"] == "embarques" and meta["resultado"] == "OK")
    _exige(meta["tipo_conector"] == ae.TIPO_CONECTOR and meta["sha256_sql"] == ae.sha_sql())
    _exige(meta["huella_config_embarques"] == ae.huella_config(_cfg_emb(datos)))
    _exige(meta["items"] == 2 and meta["facturas_verificadas"] == 3)
    _exige(_acreditar(datos, "no", captura=ref) == 0)
    doc = json.loads((datos / ae.ARCHIVO).read_text("utf-8"))
    _exige(doc["procedencia"] == ref.relative_to(datos).with_suffix(".json").as_posix())
    _exige(doc["periodo_referencia"][0] == "2026-11-01" and doc["facturas_referencia"] == 3)
    _exige(
        doc["decision_ausencia"]
        == {
            "ausencia_es_cero": False,
            "referencia": DECISION,
            "aprobado_por": "aprobador.sintetico",
        }
    )


def test_f03_captura_del_refresh_tambien_acredita(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    st = ref.ejecutar("t")  # sin acreditación: captura con procedencia, KPI en N/D
    _exige(st["conectores"]["embarques"]["estado"] == "CAPTURADO")
    cap = (
        paths["data"] / "capturas" / "embarques" / f"{st['conectores']['embarques']['archivo']}.raw"
    )
    _exige(_acreditar(paths["data"], "no", captura=cap) == 0)
    ref.clock = sim.reloj("2026-11-08", "16:00:00")
    _exige(ref.ejecutar("t")["estado"] == "PASS" and ref.store.vigente()["embarques"]["acreditado"])


def test_f04_generacion_rechaza_captura_sin_procedencia(lab: Any) -> None:
    """Una captura del almacén sin procedencia FIX5.1 (p. ej. de FIX5 previo) no alimenta el KPI."""
    paths, ref = lab
    _vivo(ref, paths)
    _exige(_acreditar(paths["data"]) == 0)
    st = ref.ejecutar("t")
    _exige(st["estado"] == "PASS" and ref.store.vigente()["embarques"]["acreditado"])
    side = (
        paths["data"]
        / "capturas"
        / "embarques"
        / f"{st['conectores']['embarques']['archivo']}.json"
    )
    side.chmod(0o600)
    _rescribir(side, lambda d: d["meta"].pop("huella_config_embarques"))
    ref.deps["fabrica_embarques"] = sim.fabrica(sim.Conexion([], falla=True))  # contingencia
    ref.clock = sim.reloj("2026-11-09")
    ref.ejecutar("t")
    emb = ref.store.vigente()["embarques"]
    _exige(emb["estado"] == "CONTINGENCIA", str(emb))
    _exige(emb["acreditado"] is False and any("procedencia" in m for m in emb["motivos"]), str(emb))
    _exige(all(r["embarques"] is None for r in ref.store.vigente()["escenarios"]["A"]["rows"]))


# ======================================================= FIX5.1: ausencia ≠ cero automático
def test_c01_clasificacion_de_los_cinco_casos() -> None:
    universo = ["A1", "B-2", "C3", "D4"]
    por_item = {"A1": "5", "b2": "7", "C3": "", "ZZ9": "4"}  # D4 ausente; b2 ≠ B-2 (no exacta)
    for cero, ausente in ((True, "0"), (False, None)):
        v, cls = ae.asignar(universo, por_item, cero)
        _exige(v == {"A1": "5", "B-2": None, "C3": None, "D4": ausente}, str(v))
        _exige(
            cls
            == {
                "FACTURADO": 1,
                "IDENTIDAD_NO_EXACTA": 1,
                "CANTIDAD_NULA": 1,
                "FUERA_DEL_UNIVERSO": 1,
                ("AUSENTE_CERO" if cero else "AUSENTE_DESCONOCIDO"): 1,
            },
            str(cls),
        )
    v, cls = ae.asignar(universo, None, True)  # consulta fallida/parcial: nada se presume
    _exige(set(v.values()) == {None} and cls == {"SIN_CONSULTA_ACREDITADA": 4})


def test_c02_identidad_no_exacta_no_se_vuelve_cero(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths, filas=[(ITEM.lower(), "300", 2, 2, 0), ("QZXA0010", "40", 1, 1, 0)])
    _exige(_acreditar(paths["data"], "si") == 0)
    _exige(ref.ejecutar("t")["estado"] == "PASS")
    gen = ref.store.vigente()
    _exige(_fila(gen)["embarques"] is None, "qzxa0016 no acredita QZXA0016 ni lo vuelve 0")
    _exige(_fila(gen, "QZXA0010")["embarques"] == "40")
    _exige(gen["embarques"]["clasificacion"]["A"]["IDENTIDAD_NO_EXACTA"] == 1)
    _exige(any("identidad no exacta" in w for w in gen["warnings"]))


def test_c03_consulta_fallida_sin_corte_previo_no_presume_cero(lab: Any) -> None:
    paths, ref = lab
    _vivo(ref, paths)
    _exige(_acreditar(paths["data"], "si") == 0)
    ref.deps["fabrica_embarques"] = sim.fabrica(sim.Conexion([], falla=True))
    ref.ejecutar("t")
    gen = ref.store.vigente()
    _exige(gen["embarques"]["acreditado"] is False and gen["embarques"]["estado"] == "SIN_CAPTURA")
    _exige(all(r["embarques"] is None for r in gen["escenarios"]["A"]["rows"]))
    for esc, cls in gen["embarques"]["clasificacion"].items():
        _exige(cls == {"SIN_CONSULTA_ACREDITADA": len(tp.rows(gen, esc))}, f"{esc}: {cls}")
