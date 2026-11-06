"""NX-E1B-R1-02 (reproducción): la reconciliación del candidato original ``531107c`` (copia
congelada en ``tests/regress/reconcile_531107c.py``) solo examina ``runs/.../generation/*.parquet``
y sus entradas ``UNREGISTERED_FILE`` no llevan SHA-256.

Esta prueba AFIRMA ambos defectos sobre el código original. La cobertura corregida se prueba en
``test_r1_02_reconcile_complete.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from conftest import AS_OF, ROOT, VALID_CSV
from nexus.connectors import synthetic_file
from nexus.metadata.memory import InMemoryMetadata
from nexus.pipeline.run import execute
from nexus.snapshots.capture import create_snapshot
from nexus.storage.object_store import LocalObjectStore


def _original() -> ModuleType:
    path = ROOT / "tests" / "regress" / "reconcile_531107c.py"
    spec = importlib.util.spec_from_file_location("reconcile_531107c", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # requerido por @dataclass al ejecutar el módulo
    spec.loader.exec_module(module)
    return module


def test_original_reconcile_ignores_non_parquet_objects_and_omits_sha256(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "store")
    snap = create_snapshot(store, synthetic_file.capture(VALID_CSV))
    record = execute(store, snapshot_id=str(snap["snapshot_id"]), as_of=AS_OF)
    store.put("misc/desconocido.bin", b"objeto fuera de los contratos")
    every_key = set(store.iter_keys())
    report = _original().reconcile(store, InMemoryMetadata())  # SQL vacío: nada registrado
    reported = {u["key"] for u in report.unregistered_files}
    parquet = {k for k in every_key if k.endswith(".parquet")}
    not_examined = every_key - reported
    # Defecto 1: solo se informan los .parquet; run_record, input_manifest, snapshots y
    # objetos desconocidos quedan fuera aunque ninguno esté registrado en SQL.
    assert reported == parquet
    assert f"runs/{record['run_id']}/run_record.json" in not_examined
    assert f"runs/{record['run_id']}/input_manifest.json" in not_examined
    assert f"snapshots/{snap['snapshot_id']}/manifest.json" in not_examined
    assert "misc/desconocido.bin" in not_examined
    # Defecto 2: las entradas no llevan identidad criptográfica del contenido.
    assert all("sha256" not in u for u in report.unregistered_files)
