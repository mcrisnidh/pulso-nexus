"""A5 — caídas reales (``os._exit``) en cada punto del protocolo de escritura.

Resultado exigido: o no existe objeto final o existe uno válido; nunca uno corrupto.
Los restos de staging no son referenciados por ningún manifiesto.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import AS_OF, VALID_CSV, child_env
from nexus.connectors import synthetic_file
from nexus.errors import NotReproducibleError
from nexus.hashing import sha256_bytes
from nexus.pipeline.replay import replay
from nexus.pipeline.run import execute
from nexus.pipeline.verify import verify_store
from nexus.snapshots.capture import create_snapshot
from nexus.storage import _fs
from nexus.storage.object_store import LocalObjectStore

CRASH_EXIT = 77
POINTS = ("before_fsync", "before_publish", "before_verify")

PUT_CHILD = r"""
import os, sys
from nexus.storage import _fs
from nexus.storage.object_store import LocalObjectStore
root, key, point = sys.argv[1], sys.argv[2], sys.argv[3]
def hook(name):
    if name == point:
        os._exit(77)
LocalObjectStore(root, fault_hook=hook).put(key, b"payload-" * 1000)
"""

RUN_CHILD = r"""
import os, sys
from nexus.storage import _fs
from nexus.storage.object_store import LocalObjectStore
from nexus.pipeline.run import execute
root, snapshot_id, as_of, point, nth = sys.argv[1:6]
count = {"n": 0}
def hook(name):
    if name == point:
        count["n"] += 1
        if count["n"] == int(nth):
            os._exit(77)
execute(LocalObjectStore(root, fault_hook=hook), snapshot_id=snapshot_id, as_of=as_of)
"""


def _child(code: str, *args: str) -> int:
    proc = subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        env=child_env(),
        timeout=300,
        check=False,
    )
    return proc.returncode


@pytest.mark.parametrize("point", POINTS)
def test_object_put_crash_leaves_absent_or_valid(tmp_path: Path, point: str) -> None:
    root = tmp_path / "store"
    LocalObjectStore(root)
    assert _child(PUT_CHILD, str(root), "obj/x.bin", point) == CRASH_EXIT
    store = LocalObjectStore(root)
    expected = sha256_bytes(b"payload-" * 1000)
    final = root / "objects" / "obj" / "x.bin"
    if point in ("before_fsync", "before_publish"):
        assert not final.exists()
    else:
        assert store.get("obj/x.bin", expected) == b"payload-" * 1000
    leftovers = store.staging_entries()
    # POSIX publica con os.link: el temporal sigue existiendo hasta que se elimina.
    # Windows publica con os.rename (se MUEVE): tras publicar ya no queda temporal.
    moved_by_rename = _fs.IS_WINDOWS and point == "before_verify"
    expected_leftovers = 0 if moved_by_rename else 1
    assert len(leftovers) == expected_leftovers, leftovers
    assert all(name.endswith(".tmp") for name in leftovers)
    # Recuperación: reintentar la misma escritura funciona y es idempotente.
    ref = store.put("obj/x.bin", b"payload-" * 1000)
    assert ref.sha256 == expected


@pytest.mark.parametrize(("point", "nth"), [(p, n) for p in POINTS for n in (1, 2, 3, 4, 5)])
def test_pipeline_crash_never_yields_referenced_corrupt_or_partial_run(
    tmp_path: Path, point: str, nth: int
) -> None:
    root = tmp_path / "store"
    store = LocalObjectStore(root)
    snap = create_snapshot(store, synthetic_file.capture(VALID_CSV))
    sid = str(snap["snapshot_id"])
    # put #1 = input_manifest, #2-#4 = artefactos, #5 = run_record
    assert _child(RUN_CHILD, str(root), sid, AS_OF, point, str(nth)) == CRASH_EXIT

    store = LocalObjectStore(root)
    report = verify_store(store)
    assert report.ok, report.failures  # nada referenciado está corrupto
    runs_root = root / "objects" / "runs"
    run_dirs = sorted(runs_root.iterdir()) if runs_root.exists() else []
    if point == "before_verify" and nth == 5:
        # run_record ya era durable y estaba publicado: la corrida está completa y es válida.
        assert report.runs_ok == 1
        assert replay(store, run_dirs[0].name).level == "L3"
    else:
        assert report.runs_ok == 0
        # Directorios sin objetos (creados antes de publicar) no contienen datos.
        with_objects = [d.name for d in run_dirs if any(p.is_file() for p in d.rglob("*"))]
        assert report.incomplete_runs == with_objects
        for run_dir in run_dirs:
            with pytest.raises(NotReproducibleError):
                replay(store, run_dir.name)
    # Los restos de staging no aparecen en ningún manifiesto.
    manifests = b"".join(p.read_bytes() for p in (root / "objects").rglob("*.json"))
    for leftover in report.staging_leftovers:
        assert leftover.encode() not in manifests
    # Una nueva ejecución sobre el mismo almacén funciona y es completa.
    record = execute(store, snapshot_id=sid, as_of=AS_OF)
    assert replay(store, record["run_id"]).level == "L3"


def test_crash_injection_records_evidence(tmp_path: Path) -> None:
    """Resumen legible para la evidencia: punto de caída → estado observado."""
    rows = []
    for point in POINTS:
        root = tmp_path / point
        LocalObjectStore(root)
        code = _child(PUT_CHILD, str(root), "obj/x.bin", point)
        final = root / "objects" / "obj" / "x.bin"
        rows.append(
            {
                "point": point,
                "exit": code,
                "final_exists": final.exists(),
                "final_valid": final.exists()
                and sha256_bytes(final.read_bytes()) == sha256_bytes(b"payload-" * 1000),
            }
        )
    print(json.dumps(rows, indent=2))
    assert all(r["exit"] == CRASH_EXIT for r in rows)
    assert all((not r["final_exists"]) or r["final_valid"] for r in rows)
