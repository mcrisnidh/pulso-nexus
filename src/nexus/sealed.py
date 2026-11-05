"""Documentos JSON autosellados (manifiestos).

El sello (``seal_sha256``) es el SHA-256 del JSON canónico del documento sin el
propio sello. Detecta corrupción o edición accidental. **No** protege frente a un
actor que recalcule el sello; esa protección (firma con custodia externa) es de E1-C.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from nexus.errors import IntegrityError
from nexus.hashing import canonical_json, sha256_bytes

SEAL_FIELD = "seal_sha256"


def seal(document: dict[str, Any]) -> bytes:
    if SEAL_FIELD in document:
        raise ValueError("el documento ya contiene un sello")
    body = dict(document)
    body[SEAL_FIELD] = sha256_bytes(canonical_json(document))
    return canonical_json(body) + b"\n"


def unseal(data: bytes, what: str) -> dict[str, Any]:
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"{what}: documento ilegible") from exc
    if not isinstance(document, dict) or SEAL_FIELD not in document:
        raise IntegrityError(f"{what}: documento sin sello")
    declared = document.pop(SEAL_FIELD)
    if sha256_bytes(canonical_json(document)) != declared:
        raise IntegrityError(f"{what}: el sello no coincide (documento alterado)")
    return document


def verifier(what: str) -> Callable[[bytes], None]:
    def _check(data: bytes) -> None:
        unseal(data, what)

    return _check
