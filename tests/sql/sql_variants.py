"""Instalación de VARIANTES de los procedimientos en esquemas aislados (B12 candidatos de hints,
B16 mutantes). Los procedimientos de producción (esquema ``nexus``) nunca se modifican: cada
variante se crea en su propio esquema (propiedad de dbo, como ``nexus``, para que la cadena de
propiedad sea idéntica) y reutiliza las MISMAS tablas ``nexus.*``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from importlib import resources
from typing import Any

from nexus.metadata.migrations import split_batches

_PROC = re.compile(r"CREATE OR ALTER PROCEDURE nexus\.(usp_[a-z_]+)")
_FENCE = re.compile(r"[ \t]*-- FENCE:BEGIN.*?-- FENCE:END\n", re.DOTALL)
_HINTS = re.compile(r"(-- CLAIM_HINTS\n)([^)]*?)(\n\s*\))")

Transform = Callable[[str, str], str]


def production_procs() -> dict[str, str]:
    text = (
        resources.files("nexus.metadata").joinpath("sql", "0002_procedures.sql").read_text("utf-8")
    )
    procs = {}
    for batch in split_batches(text):
        match = _PROC.search(batch)
        if match:
            procs[match.group(1)] = batch
    return procs


def claim_hints(proc_text: str) -> str:
    match = _HINTS.search(proc_text)
    if match is None:
        raise AssertionError("marcador CLAIM_HINTS ausente")
    return " ".join(match.group(2).split())


def with_claim_hints(hints: str) -> Transform:
    def transform(name: str, text: str) -> str:
        if name != "usp_claim_request":
            return text
        new, n = _HINTS.subn(lambda m: f"{m.group(1)}            {hints}{m.group(3)}", text)
        assert n == 1
        return new

    return transform


def without_fence(target: str) -> Transform:
    def transform(name: str, text: str) -> str:
        if name != target:
            return text
        new, n = _FENCE.subn("", text)
        assert n == 1, f"{target}: bloque FENCE no encontrado"
        return new

    return transform


def install_variant(admin: Any, schema: str, transform: Transform | None = None) -> None:
    assert re.fullmatch(r"nexus_[a-z0-9_]+", schema) and schema != "nexus"
    cur = admin.cursor()
    cur.execute(
        f"IF SCHEMA_ID(N'{schema}') IS NULL EXEC(N'CREATE SCHEMA {schema} AUTHORIZATION dbo')"
    )
    for name, text in production_procs().items():
        body = text.replace(
            f"CREATE OR ALTER PROCEDURE nexus.{name}", f"CREATE OR ALTER PROCEDURE {schema}.{name}"
        )
        if transform is not None:
            body = transform(name, body)
        cur.execute(body)
    cur.execute(f"GRANT EXECUTE ON SCHEMA::{schema} TO nexus_producer_role")


def record_evidence(name: str, payload: dict[str, Any]) -> None:
    """Imprime la evidencia y, si NEXUS_EVIDENCE_DIR existe, la añade a <name>.jsonl."""
    line = json.dumps(payload, sort_keys=True, default=str)
    print(line)
    folder = os.environ.get("NEXUS_EVIDENCE_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"{name}.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
