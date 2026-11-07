"""Lector físico Portal CAT (UTF-16 con BOM, separado por TAB, con preámbulo). Puro.

- Encabezado: la primera línea cuyas celdas contienen exactamente un alias por cada campo
  requerido (``layouts/physical_v1.json``). Las líneas anteriores son preámbulo (contadas).
- Fechas ``M/D/YYYY`` → ISO. ``A(B)`` (dos fechas) u otra forma → VACÍA (desconocida, nunca se
  elige una). Ordered y Shipped se conservan en bruto: vacío o ``8000(0)`` quedan NO_CUANTIFICABLE
  en la regla CORE vigente (desconocido, no cero).
- ITEM Portal → ITEM ERP SOLO con la tabla de homologación exacta (igualdad de texto, sin
  normalizar). Sin entrada aprobada, el registro sale con ``item`` vacío y la excepción
  ``UNMAPPED_ITEM``: nunca se asocia por parecido.
- Fecha del documento: la del nombre (``Portal_CAT_YYYY-MM-DD``), con PRECISIÓN FECHA y origen
  ``file_name`` declarado. La marca ``_0955`` NO es una hora auditada: no se usa.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from nexus.core.physical.common import BLOCKING, WARNING, PhysicalResult, mask, split_lines

_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_DOUBLE_DATE = re.compile(r"^\S+\(\S+\)$")


def _date(raw: str) -> tuple[str, str]:
    """(ISO o vacío, código de excepción o vacío)."""
    text = raw.strip()
    if not text:
        return "", "DATE_MISSING"
    if _DOUBLE_DATE.match(text) or "(" in text or ")" in text:
        return "", "DATE_AMBIGUOUS"  # fecha revisada(original): nunca se elige una
    m = _DATE.match(text)
    if not m:
        return "", "DATE_UNREADABLE"
    try:
        return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat(), ""
    except ValueError:
        return "", "DATE_UNREADABLE"


def _decode_utf16(data: bytes, res: PhysicalResult) -> str | None:
    if data.startswith(b"\xff\xfe"):
        res.bom, codec = "UTF-16LE", "utf-16-le"
    elif data.startswith(b"\xfe\xff"):
        res.bom, codec = "UTF-16BE", "utf-16-be"
    else:
        res.add(None, "NOT_UTF16_BOM", BLOCKING)
        return None
    try:
        text = data[2:].decode(codec)
    except UnicodeDecodeError:
        res.add(None, "DECODE_ERROR", BLOCKING)
        return None
    res.encoding = codec
    return text


def read_portal(  # noqa: PLR0915 (clasificación lineal de líneas físicas)
    data: bytes,
    layout: dict[str, Any],
    file_name: str,
    homologation: dict[str, str] | None = None,
) -> PhysicalResult:
    cfg = layout["portal"]
    res = PhysicalResult(kind="portal", source_id="portal_cat")
    text = _decode_utf16(data, res)
    m_name = re.search(str(cfg["file_date_pattern"]), file_name)
    if m_name:
        try:
            res.document_time = {
                "value": dt.date.fromisoformat(m_name.group(1)).isoformat(),
                "precision": "date",
                "source": "file_name",
            }
            res.add(None, "DOCUMENT_DATE_FROM_FILE_NAME", WARNING)
        except ValueError:
            res.add(None, "DOCUMENT_DATE_UNREADABLE", BLOCKING)
    else:
        res.add(None, "DOCUMENT_DATE_MISSING", BLOCKING)
    if text is None:
        return res
    aliases: dict[str, list[str]] = {k: list(v) for k, v in cfg["columns"].items()}
    header: dict[str, int] | None = None
    width = 0
    mapping = homologation or {}
    seen: set[str] = set()
    for lineno, line in split_lines(text, res):
        cells = line.split("\t")
        if header is None:
            found = _match_header([c.strip() for c in cells], aliases)
            if found is None:
                res.count("preamble", line)
                continue
            res.count("header", line)
            res.flags["header_names"] = [c.strip() for c in cells]  # solo con --show-headers
            res.flags["header_columns"] = len(cells)
            res.flags["header_diagnosis"] = diagnose_header(
                lineno, [c.strip() for c in cells], aliases
            )
            if isinstance(found, str):
                res.add(lineno, found, BLOCKING)
                _count_rest(text, lineno, res)  # estructura del resto, sin interpretar filas
                return res
            header, width = found, len(cells)
            continue
        if not line.strip():
            res.count("blank")
            continue
        while len(cells) > width and cells[-1] == "":
            cells.pop()
        if len(cells) != width:
            res.add(lineno, "ROW_SHAPE", BLOCKING)
            continue
        res.count("data_row", line)
        row = {k: cells[i].strip() for k, i in header.items()}
        if not row["line_id"] or row["line_id"] in seen:
            res.add(lineno, "LINE_ID_EMPTY_OR_DUPLICATE", BLOCKING, "line_id")
        seen.add(row["line_id"])
        dates = {}
        for name in ("ship_date", "delivery_date"):
            iso, code = _date(row[name])
            if code:
                res.add(lineno, code, WARNING, name)
            dates[name] = iso
        if not row["shipped"]:
            res.add(lineno, "SHIPPED_MISSING", WARNING, "shipped")
        erp = mapping.get(row["item_portal"], "")
        if not erp:
            res.add(lineno, "UNMAPPED_ITEM", WARNING, "item_portal")
        res.records.append(
            {
                "capture_ts": str(res.document_time.get("value", "")),
                "source_system": str(cfg["source_system"]),
                "item": erp,
                "item_portal": row["item_portal"],
                "line_id": row["line_id"],
                "status": row["status"],
                "ordered_raw": row["ordered"],
                "shipped_raw": row["shipped"],
                "ship_date": dates["ship_date"],
                "delivery_date": dates["delivery_date"],
            }
        )
    if header is None:
        res.add(None, "HEADER_NOT_FOUND", BLOCKING)
    elif not res.records:
        res.add(None, "NO_DATA_ROWS", BLOCKING)
    return res


def _match_header(cells: list[str], aliases: dict[str, list[str]]) -> dict[str, int] | str | None:
    """Índice de columna por campo. ``None`` si la línea no es el encabezado; un código de
    excepción si lo parece pero es ambiguo (dos columnas para un campo)."""
    out: dict[str, int] = {}
    for name, names in aliases.items():
        hits = [i for i, c in enumerate(cells) if c in names]
        if len(hits) > 1:
            return "HEADER_AMBIGUOUS"
        if hits:
            out[name] = hits[0]
    if len(out) == len(aliases):
        return out
    if len(out) >= max(3, len(aliases) - 2):
        return "HEADER_INCOMPLETE"
    return None


def diagnose_header(lineno: int, cells: list[str], aliases: dict[str, list[str]]) -> dict[str, Any]:
    """Diagnóstico SANEADO del encabezado: estado de cada campo obligatorio (con el alias de
    configuración que coincidió) y las columnas no reconocidas solo como forma enmascarada."""
    fields: dict[str, dict[str, Any]] = {}
    used: set[int] = set()
    for name, names in aliases.items():
        hits = [i for i, c in enumerate(cells) if c in names]
        used.update(hits)
        status = "MATCHED" if len(hits) == 1 else "AMBIGUOUS" if hits else "MISSING"
        fields[name] = {
            "status": status,
            "column": hits[0] if len(hits) == 1 else None,
            "matched_alias": cells[hits[0]] if len(hits) == 1 else None,
            "aliases_tried": len(names),
        }
    return {
        "line": lineno,
        "columns": len(cells),
        "fields": fields,
        "missing": sorted(k for k, v in fields.items() if v["status"] == "MISSING"),
        "ambiguous": sorted(k for k, v in fields.items() if v["status"] == "AMBIGUOUS"),
        "unmatched_columns_masked": [
            {"column": i, "shape": mask(c)} for i, c in enumerate(cells) if i not in used
        ],
    }


def _count_rest(text: str, header_line: int, res: PhysicalResult) -> None:
    """Tras un encabezado inválido: solo cuenta columnas por línea (no interpreta filas)."""
    by_width: dict[int, int] = {}
    scratch = PhysicalResult(kind="portal", source_id="portal_cat")
    for lineno, line in split_lines(text, scratch):
        if lineno <= header_line:
            continue
        if not line.strip():
            res.count("blank")
            continue
        res.count("uninterpreted_row")
        width = len(line.split("\t"))
        by_width[width] = by_width.get(width, 0) + 1
    res.flags["rows_by_column_count"] = dict(sorted(by_width.items()))


def unmapped_lines(result: PhysicalResult) -> list[dict[str, str]]:
    return [r for r in result.records if not r["item"]]
