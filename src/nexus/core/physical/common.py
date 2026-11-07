"""Utilidades puras de los lectores físicos (sin E/S, sin reloj, sin zona del sistema)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from importlib import resources
from typing import Any

BLOCKING, WARNING = "BLOCKING", "WARNING"
# Gramática estricta de una cantidad física impresa:
#   entero: dígitos sin separador, o miles agrupados de 3 en 3 con coma (1,234,567)
#   decimal opcional: punto y al menos un dígito; también decimal SIN entero (.999, observado en
#   IN092U, probe R2 nº 2); signo negativo al inicio O al final, nunca ambos.
_QTY = re.compile(
    r"^(?P<neg_lead>-)?"
    r"(?P<num>(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)|\.\d+)"
    r"(?P<neg_trail>-)?$"
)
_LINE_BREAK = re.compile(r"\r\n|\n|\r")


def default_layout() -> dict[str, Any]:
    text = resources.files("nexus.core").joinpath("layouts/physical_v1.json").read_text("utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


@dataclass(frozen=True)
class PhysicalException:
    """Excepción de lectura. Nunca contiene valores de la fuente: solo línea física, código y
    nombre de campo (seguro para informes saneados)."""

    line: int | None
    code: str
    severity: str
    field: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "code": self.code,
            "severity": self.severity,
            "field": self.field,
        }


@dataclass
class PhysicalResult:
    """Salida de un lector físico: registros en columnas del contrato CORE + procedencia."""

    kind: str
    source_id: str
    encoding: str = ""
    bom: str = ""
    line_endings: dict[str, int] = field(default_factory=dict)
    total_lines: int = 0
    classes: dict[str, int] = field(default_factory=dict)
    lengths: dict[int, int] = field(default_factory=dict)
    shapes: dict[str, list[str]] = field(default_factory=dict)
    document_time: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    records: list[dict[str, str]] = field(default_factory=list)
    exceptions: list[PhysicalException] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return (
            bool(self.records)
            and bool(self.document_time.get("value"))
            and not any(e.severity == BLOCKING for e in self.exceptions)
        )

    def add(self, line: int | None, code: str, severity: str, field_name: str = "") -> None:
        self.exceptions.append(PhysicalException(line, code, severity, field_name))

    def count(self, cls: str, line_text: str | None = None, keep: int = 3) -> None:
        self.classes[cls] = self.classes.get(cls, 0) + 1
        if line_text is not None:
            bucket = self.shapes.setdefault(cls, [])
            shape = mask(line_text)
            if len(bucket) < keep and shape not in bucket:
                bucket.append(shape)

    def exception_summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.exceptions:
            key = f"{e.severity}:{e.code}" + (f":{e.field}" if e.field else "")
            out[key] = out.get(key, 0) + 1
        return dict(sorted(out.items()))


def mask(text: str) -> str:
    """Forma saneada: letras → ``A``, dígitos → ``9``; se conservan espacios y puntuación."""
    return "".join("9" if c.isdigit() else "A" if c.isalpha() else c for c in text)


def decode(data: bytes, encoding: str, result: PhysicalResult) -> str | None:
    """Decodifica con la codificación declarada (nunca adivina). UTF-8 con BOM se reconoce y se
    registra; un BOM UTF-16 en una fuente de un solo byte es un formato distinto (bloqueante)."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        result.bom = "UTF-16"
        result.add(None, "UNEXPECTED_UTF16", BLOCKING)
        return None
    if data.startswith(b"\xef\xbb\xbf"):
        result.bom, data, encoding = "UTF-8", data[3:], "utf-8"
    if b"\x00" in data:
        result.add(None, "NUL_BYTES", BLOCKING)
        return None
    try:
        text = data.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        result.add(None, "DECODE_ERROR", BLOCKING)
        return None
    result.encoding = encoding
    return text


def split_lines(text: str, result: PhysicalResult) -> list[tuple[int, str]]:
    """Líneas físicas numeradas desde 1 (``\\r\\n``, ``\\n`` y ``\\r`` cuentan como un salto).
    El salto de página (``\\f``) se conserva dentro de la línea y se cuenta aparte."""
    endings = {"CRLF": 0, "LF": 0, "CR": 0}
    for m in _LINE_BREAK.finditer(text):
        endings["CRLF" if m.group() == "\r\n" else "LF" if m.group() == "\n" else "CR"] += 1
    result.line_endings = {k: v for k, v in endings.items() if v}
    parts = _LINE_BREAK.split(text)
    if parts and parts[-1] == "":
        parts.pop()
    result.total_lines = len(parts)
    out = []
    for i, raw in enumerate(parts, start=1):
        if "\f" in raw:
            result.flags["form_feeds"] = int(result.flags.get("form_feeds", 0)) + raw.count("\f")
        line = raw.replace("\f", "")
        result.lengths[len(line)] = result.lengths.get(len(line), 0) + 1
        out.append((i, line))
    return out


def parse_qty(token: str) -> Decimal | None:
    """Cantidad física: miles con coma, decimal opcional (``.999`` admitido), signo negativo al
    inicio o AL FINAL (``6516-``). Gramática estricta: ``1..5``, ``1.2.3``, ``1,23`` o ``.``
    son inválidos.
    ``None`` si está vacía o no es numérica (desconocida; nunca cero)."""
    m = _QTY.match(token.strip())
    if not m or (m.group("neg_lead") and m.group("neg_trail")):
        return None
    try:
        value = Decimal(m.group("num").replace(",", ""))
    except InvalidOperation:  # pragma: no cover (la expresión regular ya lo impide)
        return None
    return -value if (m.group("neg_lead") or m.group("neg_trail")) else value


def fmt_qty(value: Decimal | None) -> str:
    """Cantidad en el formato del contrato CSV (``-6516``); desconocida → vacía."""
    if value is None:
        return ""
    return format(value, "f")


def field_at(line: str, start: int, end: int) -> str:
    """Campo de ancho fijo ``[start:end)`` que admite desborde hacia la izquierda (más dígitos que
    el ancho) y un signo negativo final inmediatamente después del ancho."""
    if len(line) < start:
        return ""
    left = start
    while left > 0 and line[left - 1] not in " \t" and line[left:end].strip():
        left -= 1
    right = end
    if right < len(line) and line[right] == "-" and line[left:end].strip():
        right += 1
    return line[left:right].strip()


def matches_any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)
