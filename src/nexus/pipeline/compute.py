"""Cálculo de una generación a partir de un ``input_manifest`` (compartido por run y replay)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from nexus.contracts.model import Contract
from nexus.contracts.registry import ContractRegistry
from nexus.contracts.synthetic import SYNTHETIC_INPUT, SYNTHETIC_OUTPUT, SYNTHETIC_REJECTION
from nexus.engines import synthetic_sum
from nexus.errors import ConfigurationError, ContractError
from nexus.hashing import sha256_bytes
from nexus.normalize import synthetic_v1
from nexus.pipeline.manifest import PARQUET_WRITER_OPTIONS
from nexus.snapshots.capture import load_snapshot
from nexus.storage.object_store import LocalObjectStore

NORMALIZER = {"name": synthetic_v1.NAME, "version": synthetic_v1.VERSION}
ENGINE = {"name": synthetic_sum.NAME, "version": synthetic_sum.VERSION}


@dataclass(frozen=True)
class Artifact:
    role: str
    contract: Contract
    table: Any
    data: bytes
    content_sha256: str
    logical_hash: str

    @property
    def rows(self) -> int:
        return int(self.table.num_rows)


@dataclass(frozen=True)
class Generation:
    artifacts: tuple[Artifact, ...]
    data_rows: int


def configure_threads(threads: int) -> None:
    if threads < 1:
        raise ConfigurationError("threads debe ser >= 1")
    pa.set_cpu_count(threads)
    pa.set_io_thread_count(threads)


def serialize(table: Any) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table.replace_schema_metadata(None), sink, **PARQUET_WRITER_OPTIONS)
    return bytes(sink.getvalue().to_pybytes())


def deserialize(data: bytes, contract: Contract) -> Any:
    table = pq.read_table(pa.BufferReader(data))
    return contract.canonicalize(table)


def make_artifact(role: str, contract: Contract, table: Any) -> Artifact:
    canonical = contract.canonicalize(table)
    data = serialize(canonical)
    return Artifact(
        role=role,
        contract=contract,
        table=canonical,
        data=data,
        content_sha256=sha256_bytes(data),
        logical_hash=contract.logical_hash(canonical),
    )


def contract_fingerprints(registry: ContractRegistry) -> dict[str, str]:
    return {ref: registry.get(ref).fingerprint() for ref in registry.refs()}


def compute_generation(
    store: LocalObjectStore, input_manifest: dict[str, Any], registry: ContractRegistry
) -> Generation:
    if input_manifest.get("normalizer") != NORMALIZER or input_manifest.get("engine") != ENGINE:
        # E1-A solo dispone de una versión de cada componente; otra versión no es ejecutable.
        raise ConfigurationError("el manifiesto requiere versiones de componentes no disponibles")
    expected_contracts = input_manifest.get("contracts", {})
    if expected_contracts != contract_fingerprints(registry):
        raise ContractError("las huellas de contrato del manifiesto no coinciden con el código")
    snapshots = input_manifest["snapshots"]
    if len(snapshots) != 1:
        raise ConfigurationError("el pipeline sintético requiere exactamente un snapshot")
    snap = load_snapshot(store, snapshots[0]["snapshot_id"], snapshots[0]["content_sha256"])

    normalized = synthetic_v1.normalize(snap.content)
    outputs = synthetic_sum.ENGINE.run(
        {SYNTHETIC_INPUT.ref: normalized.accepted},
        input_manifest["params"],
        input_manifest["as_of"],
    )
    artifacts = (
        make_artifact("normalized", SYNTHETIC_INPUT, normalized.accepted),
        make_artifact("rejections", SYNTHETIC_REJECTION, normalized.rejections),
        make_artifact("output", SYNTHETIC_OUTPUT, outputs[SYNTHETIC_OUTPUT.ref]),
    )
    return Generation(artifacts=artifacts, data_rows=normalized.data_rows)
