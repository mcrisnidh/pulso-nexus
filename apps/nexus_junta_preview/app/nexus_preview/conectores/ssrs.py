"""Conector Backlog SSRS (29 OERAS = Backlog = R_YYYYMMDD.TXT), SOLO LECTURA.

Procedencia (PULSO Web, 73 archivos de referencia):
- ``ssrs_preview_core.py`` y ``stage50f_engines/stage50a_probe.py``: reporte «Month End - Backlog
  Detail», exportación XML (``Report/@Name``, periodo ``@textbox1`` = MM/AAAA), elementos
  ``Detail`` con ``co_num``, ``co_line``, ``item1``, ``due_date``, ``qty_ordered``,
  ``qty_shipped``, ``Textbox45`` (abierto), ``cust_ship_num``, ``cust_ship_seq``, ``entity_id``
  (0036) y ``target_currency`` (USD); ecuación ``ordenado − embarcado = abierto``; sin duplicados.
- La descarga heredada usaba PowerShell con el token Windows del proceso
  (``ssrs_fetch_readonly.ps1``, NO incluido en el paquete): la URL del servidor y la ruta del
  reporte NO están en el código de referencia ⇒ son configuración local obligatoria.

Autenticación: sesión Windows del usuario (``-UseDefaultCredentials``, Negotiate/NTLM del
servidor). NEXUS no ve ni guarda contraseñas, cookies ni tokens. Si el servidor exige un inicio de
sesión interactivo (HTTP 401/403), el conector responde ``AUTENTICACION_REQUERIDA`` y el usuario
exporta el XML desde su navegador a la carpeta de importación (modo interactivo).
"""

from __future__ import annotations

import base64
import datetime as dt
import re
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse
from xml.etree import ElementTree as ET

from . import ConectorError

MAX_XML = 25 * 1024 * 1024
FETCH_PS1 = Path(__file__).with_name("ssrs_fetch.ps1")
IMPORT_RE = re.compile(r"^Backlog_SSRS_(20\d{2})(\d{2})(\d{2})(?:_(\d{2})(\d{2}))?\.xml$", re.IGNORECASE)
DEFAULT_ATTRS = {
    "pedido": "co_num",
    "linea": "co_line",
    "item": "item1",
    "fecha": "due_date",
    "ordenado": "qty_ordered",
    "embarcado": "qty_shipped",
    "abierto": "Textbox45",
    "cliente": "cust_ship_num",
    "sufijo": "cust_ship_seq",
    "nombre_cliente": "",
    "entidad": "entity_id",
    "moneda": "target_currency",
}


def render_url(cfg: dict[str, Any]) -> str:
    base = str(cfg.get("url_servidor") or "")
    ruta = str(cfg.get("ruta_reporte") or "")
    u = urlparse(base)
    if u.scheme not in ("http", "https") or not u.hostname or "<" in base:
        raise ConectorError("SSRS_URL_NO_CONFIGURADA", "url_servidor debe ser http(s)://servidor/ReportServer")
    if not ruta.startswith("/") or "<" in ruta:
        raise ConectorError("SSRS_RUTA_NO_CONFIGURADA", "ruta_reporte debe ser /carpeta/Month End - Backlog Detail")
    params = {str(k): str(v) for k, v in (cfg.get("parametros") or {}).items()}
    for k in params:
        if k.lower().startswith(("rs:", "rc:", "rv:")):
            raise ConectorError("SSRS_PARAMETRO_RESERVADO", k)
    query = urlencode({**params, "rs:Command": "Render", "rs:Format": "XML"}, quote_via=quote)
    return f"{base.rstrip('/')}?{quote(ruta, safe='/')}&{query}"


def fetch_windows(url: str, timeout: int = 150) -> bytes:
    """Descarga con la sesión Windows del usuario mediante PowerShell (como PULSO Web)."""
    if sys.platform != "win32":
        raise ConectorError("SSRS_SOLO_WINDOWS", "la autenticación integrada requiere Windows")
    try:
        run = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(FETCH_PS1), "-Url", url, "-TimeoutSec", str(timeout)],
            capture_output=True, timeout=timeout + 30, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConectorError("SSRS_NO_DISPONIBLE", type(exc).__name__) from exc
    if run.returncode == 3:
        raise ConectorError(
            "AUTENTICACION_REQUERIDA",
            "el servidor SSRS pidió iniciar sesión: abra el reporte en el navegador, expórtelo a XML y "
            "guárdelo en la carpeta de importación como Backlog_SSRS_AAAAMMDD_HHMM.xml",
        )
    if run.returncode != 0:
        raise ConectorError("SSRS_DESCARGA_FALLIDA", run.stderr.decode("utf-8", "replace")[:80])
    out = run.stdout.strip()
    if not out or len(out) > (MAX_XML // 3 + 1) * 4 + 4096:
        raise ConectorError("SSRS_TAMANO_INVALIDO")
    try:
        return base64.b64decode(out, validate=True)
    except ValueError as exc:
        raise ConectorError("SSRS_CODIFICACION_INVALIDA") from exc


def _dec(v: str) -> Decimal:
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("cantidad no numérica") from exc
    if not d.is_finite():
        raise ValueError("cantidad no finita")
    return d


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f") if d != d.to_integral() else str(int(d))


def parse(xml: bytes, cfg: dict[str, Any], month: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """XML del reporte → registros del contrato ``oeras_29`` de NEXUS. Falla cerrado ante cualquier
    fila inválida (no se descartan líneas en silencio)."""
    if not xml or len(xml) > MAX_XML:
        raise ConectorError("SSRS_TAMANO_INVALIDO")
    head = xml[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ConectorError("SSRS_XML_INSEGURO")
    try:
        root = ET.fromstring(xml)  # noqa: S314 (sin DTD/entidades: verificado arriba)
    except ET.ParseError as exc:
        raise ConectorError("SSRS_XML_INVALIDO") from exc
    name = str(cfg.get("nombre_reporte") or "Month End - Backlog Detail")
    if root.tag.rsplit("}", 1)[-1] != "Report" or root.attrib.get("Name") != name:
        raise ConectorError("SSRS_REPORTE_EQUIVOCADO", f"se esperaba «{name}»")
    period_attr = str(cfg.get("atributo_periodo") or "textbox1")
    expected = f"{month[5:7]}/{month[:4]}"
    if root.attrib.get(period_attr, "").strip() != expected:
        raise ConectorError("SSRS_PERIODO_DISTINTO", f"periodo {root.attrib.get(period_attr)!r} ≠ {expected}")
    a = {**DEFAULT_ATTRS, **(cfg.get("atributos") or {})}
    template = str(cfg.get("formato_clave_cliente") or "{num:<10}{seq}")
    # v0.3.1: entidad y moneda son parte del contrato del XML: valores esperados y atributos
    # obligatorios; no se pueden desactivar ni se asumen si faltan en una fila.
    entity, currency = str(cfg.get("entidad", "0036")).strip(), str(cfg.get("moneda", "USD")).strip()
    if not entity or not currency or not a["entidad"] or not a["moneda"]:
        raise ConectorError("SSRS_CONTRATO_INCOMPLETO", "entidad, moneda y sus atributos son obligatorios")
    records: list[dict[str, str]] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    n = 0
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "Detail":
            continue
        n += 1
        at = node.attrib
        req = [a[k] for k in ("pedido", "linea", "item", "fecha", "ordenado", "embarcado", "abierto", "cliente")]
        missing = [f for f in req if not at.get(f, "").strip()]
        if missing:
            errors.append(f"Detail {n}: faltan {missing}")
            continue
        order, line = at[a["pedido"]].strip(), at[a["linea"]].strip()
        if (order, line) in seen:
            errors.append(f"Detail {n}: pedido/línea duplicado")
            continue
        seen.add((order, line))
        ent, cur = at.get(a["entidad"], "").strip(), at.get(a["moneda"], "").strip()
        if not ent or not cur:
            errors.append(f"Detail {n}: falta entidad o moneda (no se asumen)")
        elif ent != entity or cur != currency:
            errors.append(f"Detail {n}: entidad/moneda {ent}/{cur} ≠ contrato {entity}/{currency}")
        try:
            ordered, shipped, opened = (_dec(at[a[k]]) for k in ("ordenado", "embarcado", "abierto"))
            if min(ordered, shipped, opened) < 0 or ordered - shipped != opened:
                raise ValueError("ecuación ordenado − embarcado ≠ abierto")
            due = dt.date.fromisoformat(at[a["fecha"]].strip()[:10]).isoformat()
        except ValueError as exc:
            errors.append(f"Detail {n}: {exc}")
            continue
        key = template.format(num=at[a["cliente"]].strip(), seq=at.get(a["sufijo"], "").strip()).rstrip()
        records.append(
            {
                "extraction_ts": "",
                "source_system": "SSRS_BACKLOG",
                "report_id": name,
                "item": at[a["item"]].strip(),
                "customer_key_exact": key,
                "customer_name": at.get(a["nombre_cliente"], "").strip() if a["nombre_cliente"] else "",
                "order": order,
                "line": line,
                "due_date": due,
                "qty_ordered": _fmt(ordered),
                "qty_open": _fmt(opened),
            }
        )
    if errors:
        raise ConectorError("SSRS_CALIDAD_FALLIDA", f"{len(errors)} filas inválidas; primera: {errors[0]}")
    if not records:
        raise ConectorError("SSRS_REPORTE_VACIO")
    return records, {"periodo": expected, "lineas": len(records), "reporte": name}


def importacion_pendiente(folder: Path) -> list[tuple[Path, dt.datetime]]:
    """XML exportados por el usuario (modo interactivo), con la fecha/hora tomada del nombre."""
    out = []
    if not folder.is_dir():
        return out
    for p in sorted(folder.iterdir()):
        m = IMPORT_RE.match(p.name)
        if p.is_file() and not p.is_symlink() and m:
            y, mo, d, hh, mm = m.groups()
            out.append((p, dt.datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), tzinfo=dt.UTC)))
    return out
