"""Manifiestos de ejecución: separación entre identidad de reproducibilidad y auditoría.

- ``input_manifest``: identidad de lo que se calcula (snapshots, código, contratos,
  parámetros, ``as_of``, semillas, opciones del escritor y huella de entorno).
  Su hash es el ``input_digest``.
- ``run_record``: metadatos particulares de una corrida (``run_id``, marcas de
  tiempo, host, hilos, claves de almacenamiento). **Nunca** participa en la
  equivalencia de resultados.
- ``result_digest``: hash de la lista ordenada ``(contract@versión, logical_hash)``.
  Cubre solo datos de negocio.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa

import nexus
from nexus.errors import ConfigurationError
from nexus.hashing import digest_json, sha256_bytes

INPUT_SCHEMA = "nexus.input_manifest/1"
RECORD_SCHEMA = "nexus.run_record/1"
RESULT_SCHEME = "nexus.result/1"
_AS_OF = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Opciones fijas del escritor Parquet: parte de la identidad (afectan a L3).
PARQUET_WRITER_OPTIONS: dict[str, Any] = {
    "compression": "zstd",
    "compression_level": 3,
    "row_group_size": 65536,
    "use_dictionary": False,
    "write_statistics": True,
    "data_page_version": "2.0",
    "version": "2.6",
    "store_schema": True,
}


def validate_as_of(as_of: str) -> str:
    if not _AS_OF.match(as_of):
        raise ConfigurationError(
            f"as_of debe tener forma canónica UTC 'YYYY-MM-DDTHH:MM:SSZ': {as_of!r}"
        )
    return as_of


def project_root() -> Path | None:
    """Raíz del proyecto (para lockfile y commit), si el paquete se ejecuta desde el repositorio."""
    override = os.environ.get("NEXUS_PROJECT_ROOT")
    if override:
        return Path(override)
    candidate = Path(nexus.__file__).resolve().parents[2]
    return candidate if (candidate / "pyproject.toml").is_file() else None


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def code_identity() -> dict[str, Any]:
    root = project_root()
    lock_sha = "unknown"
    commit = "unknown"
    dirty: bool | None = None
    if root is not None:
        lock = root / "uv.lock"
        if lock.is_file():
            lock_sha = sha256_bytes(lock.read_bytes())
        head = _git(root, "rev-parse", "HEAD")
        if head:
            commit = head
            status = _git(root, "status", "--porcelain", "--untracked-files=no")
            dirty = bool(status) if status is not None else None
    return {
        "nexus_version": nexus.__version__,
        "git_commit": commit,
        "git_dirty": dirty,
        "lock_sha256": lock_sha,
    }


def environment_fingerprint() -> dict[str, str]:
    """Huella de entorno relevante para L2/L3. Excluye TZ, locale e hilos, que no
    deben alterar el resultado (eso se verifica en A10)."""
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "machine": platform.machine(),
        "byteorder": sys.byteorder,
        "pyarrow": str(pa.__version__),
    }


def build_input_manifest(
    *,
    snapshots: Sequence[Mapping[str, str]],
    normalizer: Mapping[str, str],
    engine: Mapping[str, str],
    contracts: Mapping[str, str],
    params: Mapping[str, str],
    as_of: str,
    seeds: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "schema": INPUT_SCHEMA,
        "snapshots": [dict(s) for s in snapshots],
        "normalizer": dict(normalizer),
        "engine": dict(engine),
        "contracts": dict(sorted(contracts.items())),
        "params": dict(sorted(params.items())),
        "as_of": validate_as_of(as_of),
        "seeds": dict(sorted((seeds or {}).items())),
        "writer": {"format": "parquet", **PARQUET_WRITER_OPTIONS},
        "environment": environment_fingerprint(),
        "code": code_identity(),
    }


def input_digest(manifest: Mapping[str, Any]) -> str:
    return digest_json(manifest)


def result_digest(artifacts: Sequence[tuple[str, str]]) -> str:
    """``artifacts``: pares ``(contract_ref, logical_hash)``. Solo datos de negocio."""
    return digest_json({"scheme": RESULT_SCHEME, "artifacts": sorted([list(a) for a in artifacts])})
