"""§10.2 / A4 — escrituras concurrentes desde procesos reales sobre la misma clave inmutable.

Cada proceso hijo espera una barrera (archivo ``go``) y llama ``store.put`` a la vez.
Se repite en varias rondas para aumentar la probabilidad de carreras reales.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import child_env
from nexus.hashing import sha256_bytes
from nexus.storage.object_store import LocalObjectStore

WORKERS = 8
ROUNDS = 5

CHILD = r"""
import json, sys, time
from pathlib import Path
from nexus.storage.object_store import LocalObjectStore
from nexus.errors import ImmutableConflictError
root, key, payload, go = sys.argv[1], sys.argv[2], sys.argv[3].encode(), Path(sys.argv[4])
store = LocalObjectStore(root)
deadline = time.monotonic() + 60
while not go.exists():
    if time.monotonic() > deadline:
        raise SystemExit("barrier timeout")
    time.sleep(0.0005)
try:
    ref = store.put(key, payload)
    print(json.dumps({"status": "ok", "sha": ref.sha256}))
except ImmutableConflictError as exc:
    print(json.dumps({"status": "conflict", "detail": str(exc)}))
"""


def _race(tmp_path: Path, key: str, payloads: list[str]) -> list[dict[str, str]]:
    root = tmp_path / "store"
    LocalObjectStore(root)
    go = tmp_path / f"go-{key.replace('/', '_')}"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", CHILD, str(root), key, p, str(go)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=child_env(),
        )
        for p in payloads
    ]
    time.sleep(0.5)  # todos los hijos importan y quedan en la barrera
    go.write_text("go")
    results = []
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err
        results.append(json.loads(out))
    return results


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_same_content_concurrent_writers_all_succeed(tmp_path: Path, round_no: int) -> None:
    key = f"race/same-{round_no}.bin"
    results = _race(tmp_path, key, ["identico"] * WORKERS)
    assert [r["status"] for r in results] == ["ok"] * WORKERS
    store = LocalObjectStore(tmp_path / "store")
    assert store.get(key, sha256_bytes(b"identico")) == b"identico"
    assert list(store.iter_keys("race/")) == [key]
    assert store.staging_entries() == []


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_different_content_exactly_one_winner(tmp_path: Path, round_no: int) -> None:
    key = f"race/diff-{round_no}.bin"
    payloads = [f"contenido-{i}" for i in range(WORKERS)]
    results = _race(tmp_path, key, payloads)
    winners = [(i, r) for i, r in enumerate(results) if r["status"] == "ok"]
    conflicts = [r for r in results if r["status"] == "conflict"]
    assert len(winners) == 1, results
    assert len(conflicts) == WORKERS - 1
    index, winner = winners[0]
    store = LocalObjectStore(tmp_path / "store")
    assert store.get(key, winner["sha"]) == payloads[index].encode()
    assert store.staging_entries() == []
