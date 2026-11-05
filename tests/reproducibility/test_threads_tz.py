"""A10 — hilos, zona horaria y locale no alteran el resultado.

Cada variante se ejecuta en un proceso independiente (como en producción), con su
propio ``NEXUS_THREADS``, ``TZ`` y ``LC_ALL``.

Nota de entorno: el sandbox de E1-A no tiene instalado ``es_MX.UTF-8``. Las
variantes usan locales disponibles y distintos (``C`` y ``C.UTF-8``); se
documenta como limitación en el informe.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from conftest import AS_OF, child_env, run_cli
from nexus.pipeline.run import execute
from nexus.storage.object_store import LocalObjectStore

VARIANTS = [
    {"NEXUS_THREADS": "1", "TZ": "UTC", "LC_ALL": "C"},
    {"NEXUS_THREADS": "8", "TZ": "America/Mexico_City", "LC_ALL": "C.UTF-8"},
    {"NEXUS_THREADS": "3", "TZ": "Pacific/Chatham", "LC_ALL": "C.UTF-8"},
    {"NEXUS_THREADS": "16", "TZ": "Asia/Kolkata", "LC_ALL": "C"},
]


@pytest.mark.parametrize("min_quantity", [None, "500"])
def test_result_identical_across_threads_tz_locale(
    store: LocalObjectStore, snapshot_id: str, min_quantity: str | None
) -> None:
    extra = ["--param", f"min_quantity={min_quantity}"] if min_quantity else []
    results = []
    for variant in VARIANTS:
        code, out = run_cli(
            "run",
            "--store",
            str(store.root),
            "--snapshot",
            snapshot_id,
            "--as-of",
            AS_OF,
            *extra,
            env=child_env(**variant),
        )
        assert code == 0, out
        results.append(out)
    digests = {r["result_digest"] for r in results}
    inputs = {r["input_digest"] for r in results}
    artifacts = {
        tuple((a["contract"], a["content_sha256"]) for a in r["artifacts"]) for r in results
    }
    assert len(digests) == 1, digests
    assert len(inputs) == 1, inputs
    assert len(artifacts) == 1, artifacts  # L3 dentro del mismo entorno


def test_in_process_thread_counts(store: LocalObjectStore, snapshot_id: str) -> None:
    digests = set()
    for threads in (1, 2, 8):
        record = execute(store, snapshot_id=snapshot_id, as_of=AS_OF, threads=threads)
        assert record["threads"] == threads
        digests.add(record["result_digest"])
    pa.set_cpu_count(1)
    assert len(digests) == 1
