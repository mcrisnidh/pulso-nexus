"""Ejecución de una corrida: el mismo camino de código para la ejecución manual y la programada.

Orden de escritura (con propietario ``runs/<run_id>/``):

1. ``input_manifest.json``: identidad de reproducibilidad.
2. ``generation/<contract>-<sha256>.parquet``: un archivo por artefacto, cada uno
   durable y verificado.
3. ``run_record.json``: se escribe al final y marca la corrida como completa.

Si la corrida se interrumpe antes del paso 3, la corrida está incompleta:
``verify`` la informa y ``replay`` la rechaza.
"""

from __future__ import annotations

import datetime as dt
import platform
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from nexus.contracts.registry import ContractRegistry, default_registry
from nexus.pipeline.compute import (
    ENGINE,
    NORMALIZER,
    compute_generation,
    configure_threads,
    contract_fingerprints,
)
from nexus.pipeline.manifest import (
    RECORD_SCHEMA,
    build_input_manifest,
    input_digest,
    result_digest,
)
from nexus.sealed import seal
from nexus.snapshots.capture import read_manifest
from nexus.storage.object_store import LocalObjectStore

Clock = Callable[[], dt.datetime]


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def run_keys(run_id: str) -> dict[str, str]:
    base = f"runs/{run_id}"
    return {
        "base": base,
        "input_manifest": f"{base}/input_manifest.json",
        "run_record": f"{base}/run_record.json",
        "generation": f"{base}/generation",
    }


def execute(
    store: LocalObjectStore,
    *,
    snapshot_id: str,
    as_of: str,
    params: Mapping[str, str] | None = None,
    threads: int = 1,
    trigger: str = "manual",
    registry: ContractRegistry | None = None,
    clock: Clock = _utcnow,
) -> dict[str, Any]:
    """Ejecuta el pipeline y devuelve el ``run_record``."""
    if trigger not in {"manual", "scheduled"}:
        raise ValueError(f"trigger inválido: {trigger}")
    registry = registry or default_registry()
    configure_threads(threads)
    started = clock()
    snap_manifest = read_manifest(store, snapshot_id)
    manifest = build_input_manifest(
        snapshots=[
            {"snapshot_id": snapshot_id, "content_sha256": str(snap_manifest["content_sha256"])}
        ],
        normalizer=NORMALIZER,
        engine=ENGINE,
        contracts=contract_fingerprints(registry),
        params=dict(params or {}),
        as_of=as_of,
    )
    generation = compute_generation(store, manifest, registry)

    run_id = f"run-{uuid.uuid4().hex}"
    keys = run_keys(run_id)
    store.put(keys["input_manifest"], seal(manifest))
    artifacts = []
    for art in generation.artifacts:
        key = f"{keys['generation']}/{art.contract.id}-{art.content_sha256}.parquet"
        ref = store.put(key, art.data)
        artifacts.append(
            {
                "role": art.role,
                "contract": art.contract.ref,
                "key": key,
                "content_sha256": ref.sha256,
                "logical_hash": art.logical_hash,
                "rows": art.rows,
                "size": ref.size,
            }
        )
    by_role = {a.role: a.rows for a in generation.artifacts}
    record: dict[str, Any] = {
        "schema": RECORD_SCHEMA,
        "run_id": run_id,
        "status": "COMPLETED",
        "trigger": trigger,
        "input_digest": input_digest(manifest),
        "input_manifest_key": keys["input_manifest"],
        "result_digest": result_digest(
            [(a.contract.ref, a.logical_hash) for a in generation.artifacts]
        ),
        "artifacts": artifacts,
        "counts": {
            "data_rows": generation.data_rows,
            "accepted_rows": by_role["normalized"],
            "rejected_rows": by_role["rejections"],
            "output_rows": by_role["output"],
        },
        "threads": threads,
        "host": platform.node(),
        "started_at": _iso(started),
        "finished_at": _iso(clock()),
    }
    store.put(keys["run_record"], seal(record))
    return record
