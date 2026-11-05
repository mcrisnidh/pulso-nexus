"""Manipulación con re-sellado: el atacante recalcula el sello de un manifiesto.

El sello interno solo detecta corrupción accidental. Estas pruebas demuestran que las
verificaciones cruzadas (input_digest, logical_hash, result_digest, versiones y huellas)
detectan además manipulaciones *inconsistentes*. Una manipulación totalmente consistente
de todos los documentos, hecha por un administrador, NO se detecta en E1-A: es el riesgo
residual declarado, que se cubrirá con firma y anclaje externo en E1-C (P16).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from conftest import AS_OF, object_path
from nexus.errors import ConfigurationError, ContractError, IntegrityError
from nexus.pipeline.manifest import input_digest
from nexus.pipeline.replay import replay
from nexus.pipeline.run import execute
from nexus.sealed import seal, unseal
from nexus.storage.object_store import LocalObjectStore


def _reseal(store: LocalObjectStore, key: str, change: Callable[[dict[str, Any]], None]) -> None:
    path = object_path(store, key)
    doc = unseal(path.read_bytes(), key)
    change(doc)
    path.chmod(0o600)
    path.write_bytes(seal(doc))


@pytest.fixture
def run(store: LocalObjectStore, snapshot_id: str) -> dict[str, Any]:
    return execute(store, snapshot_id=snapshot_id, as_of=AS_OF)


def _record_key(run: dict[str, Any]) -> str:
    return f"runs/{run['run_id']}/run_record.json"


def _manifest_key(run: dict[str, Any]) -> str:
    return f"runs/{run['run_id']}/input_manifest.json"


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda d: d.update(run_id="run-" + "1" * 32), "no corresponde"),
        (lambda d: d.update(input_digest="0" * 64), "input_digest"),
        (lambda d: d.update(result_digest="0" * 64), "result_digest"),
        (lambda d: d["artifacts"][2].update(logical_hash="0" * 64), "logical_hash"),
    ],
)
def test_inconsistent_resealed_run_record(
    store: LocalObjectStore,
    run: dict[str, Any],
    change: Callable[[dict[str, Any]], None],
    match: str,
) -> None:
    _reseal(store, _record_key(run), change)
    with pytest.raises(IntegrityError, match=match):
        replay(store, run["run_id"])


def test_resealed_manifest_with_unknown_schema(
    store: LocalObjectStore, run: dict[str, Any]
) -> None:
    _reseal(store, _manifest_key(run), lambda d: d.update(schema="otro/1"))
    with pytest.raises(IntegrityError, match="esquema"):
        replay(store, run["run_id"])


def _consistent_manifest_change(
    store: LocalObjectStore, run: dict[str, Any], change: Callable[[dict[str, Any]], None]
) -> None:
    """Cambia el input_manifest y actualiza el input_digest del registro (coherente)."""
    holder: dict[str, Any] = {}

    def apply(doc: dict[str, Any]) -> None:
        change(doc)
        holder["digest"] = input_digest(doc)

    _reseal(store, _manifest_key(run), apply)
    _reseal(store, _record_key(run), lambda d: d.update(input_digest=holder["digest"]))


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda d: d["engine"].update(version="9.9.9"), ConfigurationError),
        (lambda d: d["contracts"].update({"synthetic_input@1.0.0": "0" * 64}), ContractError),
        (lambda d: d.update(snapshots=[*d["snapshots"], *d["snapshots"]]), ConfigurationError),
    ],
)
def test_consistent_manifest_changes_still_rejected(
    store: LocalObjectStore,
    run: dict[str, Any],
    change: Callable[[dict[str, Any]], None],
    error: type[Exception],
) -> None:
    _consistent_manifest_change(store, run, change)
    with pytest.raises(error):
        replay(store, run["run_id"])


def test_fully_consistent_admin_tampering_is_a_declared_residual_risk(
    store: LocalObjectStore, run: dict[str, Any]
) -> None:
    """Documenta el límite de E1-A: si se cambia `as_of` y se re-sellan coherentemente
    manifiesto y registro, el replay recalcula sobre el manifiesto manipulado y la
    discrepancia sí aparece frente a los artefactos guardados (nivel NONE). Pero si el
    atacante también reescribiera los artefactos y sus hashes, no habría evidencia
    externa. Esa protección es de E1-C (firma con custodia externa, P16)."""
    _consistent_manifest_change(store, run, lambda d: d.update(as_of="2026-01-31T00:00:00Z"))
    report = replay(store, run["run_id"])
    assert report.level == "NONE"
