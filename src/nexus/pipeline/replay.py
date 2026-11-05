"""Replay: recalcula una corrida a partir de su ``input_manifest`` y la compara con lo guardado.

El replay es de solo lectura: no escribe en el almacén. Falla cerrado ante:
- ejecución o snapshot ausente → ``NotReproducibleError`` (sin resultado parcial);
- manifiesto, snapshot o artefacto alterado → ``IntegrityError``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from nexus.contracts.registry import ContractRegistry, default_registry
from nexus.errors import IntegrityError, NotReproducibleError
from nexus.pipeline.compare import Side, compare
from nexus.pipeline.compute import compute_generation, configure_threads, deserialize
from nexus.pipeline.manifest import (
    INPUT_SCHEMA,
    RECORD_SCHEMA,
    code_identity,
    environment_fingerprint,
    input_digest,
    result_digest,
)
from nexus.pipeline.run import run_keys
from nexus.sealed import unseal, verifier
from nexus.storage.object_store import LocalObjectStore


@dataclass(frozen=True)
class ReplayReport:
    run_id: str
    level: str
    discrepancy: str | None
    per_artifact: dict[str, str]
    recorded_result_digest: str
    replayed_result_digest: str
    environment_match: bool
    code_match: bool
    environment_differences: dict[str, list[str]]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_sealed(store: LocalObjectStore, key: str, what: str) -> dict[str, Any]:
    try:
        raw = store.get_self_verifying(key, verifier(key))
    except FileNotFoundError as exc:
        raise NotReproducibleError(f"{what} ausente: {key}") from exc
    return unseal(raw, key)


def load_run(store: LocalObjectStore, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    keys = run_keys(run_id)
    record = _read_sealed(store, keys["run_record"], "registro de ejecución")
    if record.get("schema") != RECORD_SCHEMA or record.get("run_id") != run_id:
        raise IntegrityError(f"{run_id}: registro de ejecución no corresponde")
    manifest = _read_sealed(store, keys["input_manifest"], "input_manifest")
    if manifest.get("schema") != INPUT_SCHEMA:
        raise IntegrityError(f"{run_id}: input_manifest con esquema desconocido")
    if input_digest(manifest) != record.get("input_digest"):
        raise IntegrityError(f"{run_id}: input_digest no coincide con el registro")
    return record, manifest


def replay(
    store: LocalObjectStore,
    run_id: str,
    *,
    threads: int = 1,
    registry: ContractRegistry | None = None,
) -> ReplayReport:
    registry = registry or default_registry()
    configure_threads(threads)
    record, manifest = load_run(store, run_id)

    expected: list[Side] = []
    for art in record["artifacts"]:
        contract = registry.get(art["contract"])
        try:
            data = store.get(art["key"], art["content_sha256"])
        except FileNotFoundError as exc:
            raise IntegrityError(f"{run_id}: artefacto ausente {art['key']}") from exc
        table = deserialize(data, contract)
        if contract.logical_hash(table) != art["logical_hash"]:
            raise IntegrityError(f"{run_id}: logical_hash registrado no coincide en {art['key']}")
        expected.append(Side(contract, table, art["content_sha256"], art["logical_hash"]))
    recorded_digest = result_digest([(s.contract.ref, s.logical_hash) for s in expected])
    if recorded_digest != record["result_digest"]:
        raise IntegrityError(f"{run_id}: result_digest registrado no coincide")

    generation = compute_generation(store, manifest, registry)
    actual = [
        Side(a.contract, a.table, a.content_sha256, a.logical_hash) for a in generation.artifacts
    ]
    comparison = compare(expected, actual)

    current_env = environment_fingerprint()
    recorded_env = manifest["environment"]
    env_diff = {
        k: [str(recorded_env.get(k)), str(current_env.get(k))]
        for k in sorted(set(recorded_env) | set(current_env))
        if recorded_env.get(k) != current_env.get(k)
    }
    current_code = code_identity()
    code_match = all(
        manifest["code"].get(k) == current_code.get(k) for k in ("git_commit", "lock_sha256")
    )
    return ReplayReport(
        run_id=run_id,
        level=comparison.level,
        discrepancy=comparison.discrepancy,
        per_artifact=comparison.per_artifact,
        recorded_result_digest=record["result_digest"],
        replayed_result_digest=result_digest(
            [(a.contract.ref, a.logical_hash) for a in generation.artifacts]
        ),
        environment_match=not env_diff,
        code_match=code_match,
        environment_differences=env_diff,
    )
