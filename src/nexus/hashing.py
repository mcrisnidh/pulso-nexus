"""Hashes de contenido (integridad) y lógicos (reproducibilidad).

- ``content_hash``: SHA-256 de los bytes exactos. Garantiza integridad y sirve para L3.
- ``logical_hash``: SHA-256 de una serialización canónica propia de una tabla. No
  depende del orden de filas, del orden físico de columnas ni de la versión de la
  librería de escritura. Sirve para L2.

Serialización canónica (versión ``nexus.logical/1``):

* Cabecera: dominio (``contract@version``), nombres de columna en el orden
  declarado por el contrato y tipo Arrow de cada una.
* Cada valor se codifica como ``tag (1 byte) + longitud (8 bytes BE) + carga``.
* Cadenas en Unicode NFC (UTF-8). Coma flotante en IEEE-754 binario64 big-endian,
  con un único patrón NaN canónico. Decimales en texto con su escala. Fechas en ISO-8601.
* Las filas codificadas se ordenan por sus bytes. Esto hace el hash invariante a
  permutaciones de filas.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import hashlib
import json
import math
import struct
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any, BinaryIO

import pyarrow as pa

from nexus.errors import ContractError

LOGICAL_SCHEME = "nexus.logical/1"
_CHUNK = 1024 * 1024
_CANONICAL_NAN = struct.pack(">d", float("nan"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(_CHUNK)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def canonical_json(obj: Any) -> bytes:
    """JSON canónico: claves ordenadas, sin espacios, UTF-8, sin NaN."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest_json(obj: Any) -> str:
    return sha256_bytes(canonical_json(obj))


def _frame(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack(">Q", len(payload)) + payload


def _encode_value(value: Any, arrow_type: Any) -> bytes:
    if value is None:
        return _frame(b"N", b"")
    if pa.types.is_boolean(arrow_type):
        return _frame(b"B", b"1" if value else b"0")
    if pa.types.is_integer(arrow_type):
        return _frame(b"I", str(int(value)).encode("ascii"))
    if pa.types.is_floating(arrow_type):
        fval = float(value)
        packed = _CANONICAL_NAN if math.isnan(fval) else struct.pack(">d", fval)
        return _frame(b"F", packed)
    if pa.types.is_decimal(arrow_type):
        if not isinstance(value, decimal.Decimal):  # pragma: no cover - defensivo
            raise ContractError(f"valor decimal inesperado: {value!r}")
        return _frame(b"D", format(value, "f").encode("ascii"))
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return _frame(b"S", unicodedata.normalize("NFC", str(value)).encode("utf-8"))
    if pa.types.is_date32(arrow_type):
        if not isinstance(value, _dt.date):  # pragma: no cover - defensivo
            raise ContractError(f"valor de fecha inesperado: {value!r}")
        return _frame(b"T", value.isoformat().encode("ascii"))
    raise ContractError(f"tipo Arrow no soportado por {LOGICAL_SCHEME}: {arrow_type}")


def _rows(table: Any, columns: Sequence[str]) -> Iterable[bytes]:
    types = [table.schema.field(name).type for name in columns]
    data = [table.column(name).to_pylist() for name in columns]
    for row in zip(*data, strict=True):
        yield b"".join(_encode_value(v, t) for v, t in zip(row, types, strict=True))


def logical_hash(table: Any, columns: Sequence[str], domain: str) -> str:
    """Hash lógico de ``table`` restringido a ``columns`` (en el orden del contrato)."""
    missing = [c for c in columns if c not in table.column_names]
    if missing:
        raise ContractError(f"columnas ausentes para logical_hash: {missing}")
    header = {
        "scheme": LOGICAL_SCHEME,
        "domain": domain,
        "columns": [[name, str(table.schema.field(name).type)] for name in columns],
    }
    digest = hashlib.sha256()
    digest.update(canonical_json(header))
    encoded = sorted(_rows(table, columns))
    digest.update(struct.pack(">Q", len(encoded)))
    for row in encoded:
        digest.update(struct.pack(">Q", len(row)))
        digest.update(row)
    return digest.hexdigest()
