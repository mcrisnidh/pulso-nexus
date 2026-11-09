"""Simuladores EXPLÍCITOS para probar los conectores sin sistemas corporativos (datos sintéticos)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

import dataset_demo as demo

from nexus_preview.conectores import ConectorError
from nexus_preview.conectores.credenciales import Credencial, Secreto

SECRETO_FG = "Sintetico-FG-#123"
SECRETO_EMB = "Sintetico-EMB-#456"


def proveedor(target: str) -> Credencial:
    tabla = {
        "PULSO_NEXUS/fg": Credencial("PULSO_NEXUS/fg", "remcb", Secreto(SECRETO_FG)),
        "PULSO_NEXUS/embarques": Credencial("PULSO_NEXUS/embarques", "DOMINIO\\sm0003", Secreto(SECRETO_EMB)),
        "PULSO_NEXUS/cruzada": Credencial("PULSO_NEXUS/cruzada", "sm0003", Secreto("x")),
    }
    if target not in tabla:
        raise ConectorError("CREDENCIAL_NO_ENCONTRADA", target)
    return tabla[target]


def _iso(mmddyy: str) -> str:
    m, d, y = mmddyy.split("/")
    return f"20{y}-{m}-{d}"


def xml_ssrs(lines_by_item: list[dict[str, Any]], periodo: str = "11/2026", nombre: str = "Month End - Backlog Detail") -> bytes:
    """Reporte «Month End - Backlog Detail» con la forma observada en PULSO Web."""
    det = []
    for it in lines_by_item:
        for order, line, due, ordered, open_qty, key in it["lines"]:
            num, seq = key[:-3].rstrip(), key[-3:]
            shipped = int(ordered) - int(open_qty)
            attrs = {
                "entity_id": "0036", "target_currency": "USD", "co_num": order, "co_line": line,
                "item1": it["item"], "due_date": _iso(due) + "T00:00:00", "qty_ordered": ordered,
                "qty_shipped": str(shipped), "Textbox45": open_qty, "cust_ship_num": num, "cust_ship_seq": seq,
            }
            det.append("<Detail " + " ".join(f"{k}={quoteattr(v)}" for k, v in attrs.items()) + "/>")
    body = f'<?xml version="1.0" encoding="utf-8"?><Report Name={quoteattr(nombre)} textbox1="{periodo}"><Details>' + "".join(det) + "</Details></Report>"
    return body.encode("utf-8")


def plan_posterior() -> list[dict[str, Any]]:
    """El mismo contenido que ``dataset_demo.corte_posterior`` (para conciliar directo vs manual)."""
    out = []
    for i in demo._plan():
        lines = []
        for k, line in enumerate(i["lines"]):
            if k % 3 == 2:
                continue
            open_qty = int(line[4]) // 2 if k % 2 else int(line[4])
            lines.append((*line[:4], str(open_qty), line[5]))
        if lines:
            out.append({"item": i["item"], "lines": lines})
    out.append({"item": "QZXA0999", "lines": [("7399900", "1", "11/20/26", "50", "50", "QZXDRV    001")]})
    return out


def filas_fg_posterior() -> list[tuple[str, str, Any]]:
    """Filas STKBAL equivalentes a ``dataset_demo.fg_posterior`` (con relleno CHAR a la derecha)."""
    return [(f"{i['item']:<15}", "FG ", f"{(i['fg'] or 0) + 1000}.000") for i in demo._plan()[:30]]


class Cursor:
    def __init__(self, conn: Conexion) -> None:
        self.conn = conn
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, *params: Any) -> Cursor:
        self.conn.ejecutadas.append((sql, params))
        if self.conn.falla:
            raise RuntimeError(f"fallo simulado con PWD={self.conn.secreto_visto} en el mensaje")
        up = sql.upper()
        if "DB_NAME()" in up:
            self._rows = [(self.conn.base, "servidor-sintetico")]
        elif "SELECT DISTINCT A.NUMFOL" in up and "MF_DETCFD" in up:
            if self.conn.embarques_facturas is not None:
                ids = self.conn.embarques_facturas
            else:
                ids = [f"F{n:06d}" for n in range(1, 1 + sum(int(r[3]) for r in self.conn.filas))]
            self.conn._ids_embarques_ultima = list(ids)
            self._rows = [(v,) for v in ids]
        elif up.startswith("SELECT DISTINCT NUMFOL FROM MF_CUENTAS") or up.startswith("SELECT DISTINCT NUMFOL FROM MF_RECCFD"):
            tabla = "MF_CUENTAS" if "MF_CUENTAS" in up else "MF_RECCFD"
            if self.conn.embarques_relaciones is None:
                disponibles = self.conn._ids_embarques_ultima
            else:
                disponibles = self.conn.embarques_relaciones.get(tabla, [])
            if self.conn.simular_incorrecta_respuesta_in:
                self._rows = [(v,) for v in disponibles]
            else:
                self._rows = [(v,) for v in disponibles if v in params]
        elif "SYSDUMMY1" in up:
            self._rows = [(1,)]
        else:
            self._rows = list(self.conn.filas)
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class Conexion:
    def __init__(self, filas: list[tuple[Any, ...]], base: str = "ASBG", falla: bool = False, secreto_visto: str = "") -> None:
        self.filas, self.base, self.falla, self.secreto_visto = filas, base, falla, secreto_visto
        self.ejecutadas: list[tuple[str, tuple[Any, ...]]] = []
        self.rollbacks = 0
        self.cerrada = False
        self.cadenas: list[str] = []
        self.embarques_facturas: list[str] | None = None
        self.embarques_relaciones: dict[str, list[str]] | None = None
        self._ids_embarques_ultima: list[str] = []
        self.simular_incorrecta_respuesta_in = False

    def cursor(self) -> Cursor:
        return Cursor(self)

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.cerrada = True


def fabrica(conn: Conexion):  # noqa: ANN201
    def f(cadena: str, timeout: int) -> Conexion:
        conn.cadenas.append(cadena)
        conn.secreto_visto = cadena.split("PWD={")[-1].split("}")[0] if "PWD={" in cadena else ""
        return conn

    return f


def config_conectores(data: Path, **over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "schema": "nexus.preview.conectores/1",
        "backlog_ssrs": {"activo": True, "modo": "windows_integrado", "url_servidor": "http://ssrs-sintetico/ReportServer",
                         "ruta_reporte": "/Comercial/Month End - Backlog Detail", "cuenta_esperada": "ucbaa",
                         "formato_clave_cliente": "{num:<10}{seq}"},
        "fg_odbc": {"activo": True, "dsn": "DB2LOCAL", "credencial": "PULSO_NEXUS/fg", "cuenta_esperada": "remcb"},
        "embarques": {"activo": False, "controlador": "ODBC Driver 18 for SQL Server", "servidor": "sql-sintetico",
                      "base": "ASBG", "credencial": "PULSO_NEXUS/embarques", "cuenta_esperada": "sm0003"},
    }
    for k, v in over.items():
        cfg[k] = {**cfg.get(k, {}), **v} if isinstance(v, dict) else v
    (data / "conectores.json").write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")
    return cfg


def acreditar(data: Path, fuente: str) -> None:
    """FG (v0.3.2) y Backlog SSRS (v0.3.3): cadena VERIFICABLE real (preflight + conciliación)."""
    if fuente == "backlog_ssrs":
        if acreditar_backlog_verificable(data) != 0:
            raise RuntimeError("no se pudo acreditar el backlog sintético")
        return
    if fuente == "fg_odbc":
        assert acreditar_fg_verificable(data) == 0
        return
    acreditar_falsa(data, fuente)


def acreditar_falsa(data: Path, fuente: str) -> None:
    """Documento con forma válida y huella INVENTADA (lo que v0.3.2 debe rechazar para FG)."""
    (data / f"acreditacion_{fuente}.json").write_text(json.dumps({
        "schema": "nexus.preview.acreditacion-conector/1", "fuente": fuente, "resultado": "PASS",
        "informe_sha256": "a" * 64}), "utf-8")


def captura_preflight_fg(data: Path, filas: list[tuple[str, str, Any]] | None = None, stamp: str = "20261107T152500Z") -> Path:
    """Equivalente sintético de «preflight.py --conector fg_odbc --nivel consulta --autorizo»."""
    from nexus_preview.conectores import fg_odbc  # noqa: PLC0415

    cfg = json.loads((data / "conectores.json").read_text("utf-8"))["fg_odbc"]
    raw, _ = fg_odbc.capturar(cfg, proveedor, fabrica(Conexion(filas if filas is not None else filas_fg_posterior())))
    out = data / "preflight" / "fg_odbc"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stamp}.raw").write_bytes(raw)
    (out / f"{stamp}.csv").write_bytes(raw)
    return out / f"{stamp}.csv"


def manual_fg(data: Path, filas: list[tuple[str, str, str]] | None = None, nombre: str = "INV_STA290.TXT") -> Path:
    """IN092U manual sintético FUERA de la carpeta de fuentes (evidencia de la conciliación)."""
    import physical_fixtures as pf  # noqa: PLC0415

    ev = data.parent / "evidencia_manual"
    ev.mkdir(exist_ok=True)
    if filas is None:
        return demo.fg_posterior(ev, nombre)
    path = ev / nombre
    path.write_bytes(pf.in092u_bytes(date="11/07/26", time=" 8:15:00", rows=filas))
    return path


def acreditar_fg_verificable(data: Path, filas: list[tuple[str, str, Any]] | None = None,
                             manual: list[tuple[str, str, str]] | None = None, alcance: str = "FG") -> int:
    import conciliar  # noqa: PLC0415

    directa = captura_preflight_fg(data, filas)
    args = ["fg", "--data", str(data), "--manual", str(manual_fg(data, manual)), "--directa", str(directa), "--acreditar"]
    return conciliar.main(args + (["--alcance", alcance] if alcance else []))


def reloj(fecha: str, hora: str = "15:00:00"):  # noqa: ANN201
    moment = dt.datetime.fromisoformat(f"{fecha}T{hora}+00:00")
    return lambda: moment


def acreditar_embarques_verificable(data: Path, conciliar_main: Any, evidencia_qty: str = "300", **over: Any) -> dict[str, Any]:
    """Cadena VERIFICABLE (v0.3.1): evidencia real en disco + conciliación PASS de conciliar.py contra
    una captura de embarques producida por el conector. Devuelve el documento de acreditación."""
    import hashlib

    raw = next((data / "capturas" / "embarques").glob("*.raw"))
    ev_dir = data / "acreditacion"
    ev_dir.mkdir(exist_ok=True)
    ev = ev_dir / "embarques_reconocidos.csv"
    ev.write_text(f"item,cantidad\nQZXA0016,{evidencia_qty}\n", "utf-8")
    rc = conciliar_main(["embarques", "--data", str(data), "--directa", str(raw), "--evidencia", str(ev)])
    folder = sorted((data / "conciliaciones").glob("embarques_*"))[-1]
    informe = folder / "informe_privado.json"
    doc = {
        "schema": "nexus.preview.acreditacion-embarques/1", "acreditado": True,
        "evidencia_ruta": str(ev.relative_to(data)), "evidencia_sha256": hashlib.sha256(ev.read_bytes()).hexdigest(),
        "conciliacion_ruta": str(informe.relative_to(data)), "conciliacion_sha256": hashlib.sha256(informe.read_bytes()).hexdigest(),
        "aprobado_por": "aprobador.sintetico", "trata_cancelaciones": True, "trata_notas_credito": True,
        "fecha_es_embarque_fisico": True, "identidad_item_exacta": True, "_rc_conciliacion": rc,
    }
    doc.update(over)
    (data / "acreditacion_embarques.json").write_text(json.dumps(doc), "utf-8")
    return doc


def captura_preflight_backlog(
    data: Path, plan: list[dict[str, Any]] | None = None, stamp: str = "20261108T150000Z"
) -> Path:
    """Equivalente sintético de «preflight.py --conector backlog_ssrs --nivel consulta».

    Guarda el XML ORIGINAL del reporte (lo que la acreditación exige como procedencia)."""
    out = data / "preflight" / "backlog_ssrs"
    out.mkdir(parents=True, exist_ok=True)
    raw = xml_ssrs(plan if plan is not None else plan_posterior())
    (out / f"{stamp}.raw").write_bytes(raw)
    return out / f"{stamp}.raw"


def manual_backlog(data: Path, fecha: str = "11/08/26", nombre: str = "R_20261108.TXT") -> Path:
    """R manual sintético FUERA de la carpeta de fuentes (evidencia de la conciliación)."""
    ev = data.parent / "evidencia_manual"
    ev.mkdir(exist_ok=True)
    return demo.corte_posterior(ev, date=fecha, name=nombre)


def acreditar_backlog_verificable(
    data: Path, plan: list[dict[str, Any]] | None = None, stamp: str = "20261108T150000Z"
) -> int:
    from conciliar import main as conciliar_main  # noqa: PLC0415

    directa = captura_preflight_backlog(data, plan, stamp)
    args = ["backlog", "--data", str(data), "--manual", str(manual_backlog(data)), "--directa"]
    return conciliar_main([*args, str(directa), "--acreditar"])
