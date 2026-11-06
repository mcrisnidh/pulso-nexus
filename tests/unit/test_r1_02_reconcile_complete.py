"""NX-E1B-R1-02: reconciliación completa del ObjectStore (con el doble de metadatos en memoria).

Cubre:

- archivos sin fila de todos los tipos;
- registros de corrida completos, incompletos y corruptos;
- manifiestos de entrada ausentes, corruptos o con otro digest;
- referencias incompletas: artefacto citado ausente o con otro hash, y ``.parquet`` no citado;
- integridad de snapshots;
- referencias de SQL que no casan con el almacén;
- restos de staging.

En todos los casos se exige que la reconciliación no elimine, mueva, altere ni registre nada.
Las mismas direcciones se prueban contra SQL Server real en ``tests/sql/test_b15_reconcile.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from conftest import AS_OF, VALID_CSV, object_path, tamper
from nexus.connectors import synthetic_file
from nexus.hashing import sha256_bytes
from nexus.metadata.memory import InMemoryMetadata
from nexus.metadata.profile import load_profile
from nexus.pipeline.reconcile import ReconcileReport, reconcile
from nexus.pipeline.run import execute
from nexus.pipeline.worker import work_once
from nexus.snapshots.capture import create_snapshot
from nexus.storage.object_store import LocalObjectStore

PROFILE = load_profile("e1b-ephemeral")


def _files(store: LocalObjectStore) -> dict[str, str]:
    root = store.root / "objects"
    return {
        p.relative_to(root).as_posix(): sha256_bytes(p.read_bytes())
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _registered_run(tmp_path: Path) -> tuple[LocalObjectStore, InMemoryMetadata, str, str]:
    """Una corrida completa y REGISTRADA (filas en el doble de SQL) y su snapshot registrado."""
    store = LocalObjectStore(tmp_path / "store")
    snap = create_snapshot(store, synthetic_file.capture(VALID_CSV))
    repo = InMemoryMetadata()
    payload = {
        "snapshot_id": snap["snapshot_id"],
        "snapshot_sha256": snap["content_sha256"],
        "as_of": AS_OF,
        "params": {},
        "trigger": "manual",
    }
    repo.enqueue("r102", "scope-r102", "manual", payload, 3)
    out = work_once(store, repo, repo, PROFILE, "w")
    assert out.status == "COMPLETED"
    run_id = str(next(g.store_run_id for g in repo.generations.values() if g.sealed))
    return store, repo, run_id, str(snap["snapshot_id"])


def _safe_reconcile(store: LocalObjectStore, repo: InMemoryMetadata) -> ReconcileReport:
    """Ejecuta la reconciliación y comprueba que es NO destructiva y NO registra nada."""
    files_before = _files(store)
    staging_before = store.staging_entries()
    artifacts_before = repo.list_artifacts()
    snapshots_before = repo.list_snapshot_refs()
    generations_before = repo.list_generations()
    report = reconcile(store, repo)
    assert _files(store) == files_before, "la reconciliación modificó el almacén"
    assert store.staging_entries() == staging_before
    assert repo.list_snapshot_refs() == snapshots_before, "registró snapshots por su cuenta"
    assert [r["object_key"] for r in repo.list_artifacts()] == [
        r["object_key"] for r in artifacts_before
    ], "registró artefactos por su cuenta"
    assert [g["attempt_id"] for g in repo.list_generations()] == [
        g["attempt_id"] for g in generations_before
    ]
    for entry in report.unregistered_files:  # identidad exacta de los bytes en disco
        assert entry["sha256"] == files_before[entry["key"]], entry
        assert entry["size"] == (store.root / "objects" / entry["key"]).stat().st_size
    return report


def _by_key(report: ReconcileReport) -> dict[str, dict[str, Any]]:
    return {u["key"]: u for u in report.unregistered_files}


def test_registered_and_consistent_store_reports_nothing(tmp_path: Path) -> None:
    store, repo, run_id, snapshot_id = _registered_run(tmp_path)
    report = _safe_reconcile(store, repo)
    assert report.unregistered_files == []
    assert report.sql_reference_issues == []
    assert (
        report.runs[run_id]["status"] == "COMPLETE_STORE_RUN" and report.runs[run_id]["registered"]
    )
    assert report.snapshots[snapshot_id]["status"] == "SNAPSHOT_VALID"
    assert report.objects_scanned == len(_files(store))


def test_every_object_type_without_row_is_reported_with_identity_and_provenance(
    tmp_path: Path,
) -> None:
    store, repo, _, _ = _registered_run(tmp_path)
    other_snap = create_snapshot(store, synthetic_file.capture(VALID_CSV))  # sin snapshot_ref
    record = execute(store, snapshot_id=str(other_snap["snapshot_id"]), as_of=AS_OF)  # sin filas
    store.put("misc/desconocido.bin", b"objeto fuera de los contratos")
    report = _safe_reconcile(store, repo)
    found = _by_key(report)
    run_id = record["run_id"]
    types = {found[k]["object_type"] for k in found}
    assert types == {
        "ARTIFACT",
        "INPUT_MANIFEST",
        "RUN_RECORD",
        "SNAPSHOT_MANIFEST",
        "SNAPSHOT_CONTENT",
        "UNKNOWN_OBJECT",
    }
    for art in record["artifacts"]:
        entry = found[art["key"]]
        assert entry["kind"] == "UNREGISTERED_FILE" and entry["integrity"] == "VALID"
        assert entry["sha256"] == art["content_sha256"]
        prov = entry["provenance"]
        assert prov["run_id"] == run_id and prov["result_digest"] == record["result_digest"]
        assert prov["run_record_entry"]["logical_hash"] == art["logical_hash"]
    rr = found[f"runs/{run_id}/run_record.json"]
    assert rr["label"] == "COMPLETE_STORE_RUN" and rr["integrity"] == "VALID"
    assert rr["provenance"]["input_digest"] == record["input_digest"]
    content = found[f"snapshots/{other_snap['snapshot_id']}/content.bin"]
    assert content["integrity"] == "VALID" and content["sha256"] == other_snap["content_sha256"]
    assert content["provenance"]["captured_at"] == other_snap["captured_at"]
    unknown = found["misc/desconocido.bin"]
    assert unknown["label"] == "UNKNOWN_OBJECT" and unknown["integrity"] == "UNVERIFIABLE"


def test_incomplete_run_without_run_record(tmp_path: Path) -> None:
    store, repo, _, snapshot_id = _registered_run(tmp_path)
    record = execute(store, snapshot_id=snapshot_id, as_of=AS_OF)
    path = object_path(store, f"runs/{record['run_id']}/run_record.json")
    os.chmod(path, 0o600)
    path.unlink()  # simula la caída antes del paso 3 de E1-A
    report = _safe_reconcile(store, repo)
    assert report.runs[record["run_id"]]["status"] == "INCOMPLETE_RUN"
    labels = {u["label"] for u in report.unregistered_files}
    assert labels == {"INCOMPLETE_RUN"}


def test_corrupt_run_record_is_detected(tmp_path: Path) -> None:
    store, repo, _, snapshot_id = _registered_run(tmp_path)
    record = execute(store, snapshot_id=snapshot_id, as_of=AS_OF)
    key = f"runs/{record['run_id']}/run_record.json"
    tamper(object_path(store, key))
    report = _safe_reconcile(store, repo)
    assert report.runs[record["run_id"]]["status"] == "CORRUPT_RUN_RECORD"
    assert _by_key(report)[key]["integrity"] == "CORRUPT"


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_input_manifest_missing_or_corrupt_makes_run_inconsistent(
    tmp_path: Path, damage: str
) -> None:
    store, repo, _, snapshot_id = _registered_run(tmp_path)
    record = execute(store, snapshot_id=snapshot_id, as_of=AS_OF)
    key = f"runs/{record['run_id']}/input_manifest.json"
    path = object_path(store, key)
    if damage == "missing":
        os.chmod(path, 0o600)
        path.unlink()
    else:
        tamper(path)
    report = _safe_reconcile(store, repo)
    run = report.runs[record["run_id"]]
    assert run["status"] == "INCONSISTENT_RUN"
    assert any("input_manifest" in p for p in run["problems"])
    if damage == "corrupt":
        assert _by_key(report)[key]["integrity"] == "CORRUPT"


def test_incomplete_references_cited_artifact_missing_altered_or_extra(tmp_path: Path) -> None:
    store, repo, _, snapshot_id = _registered_run(tmp_path)
    record = execute(store, snapshot_id=snapshot_id, as_of=AS_OF)
    gone, altered = record["artifacts"][0]["key"], record["artifacts"][1]["key"]
    os.chmod(object_path(store, gone), 0o600)
    object_path(store, gone).unlink()
    tamper(object_path(store, altered))
    extra = f"runs/{record['run_id']}/generation/synthetic_output-{'0' * 64}.parquet"
    store.put(extra, b"artefacto no citado por el run_record")
    report = _safe_reconcile(store, repo)
    problems = report.runs[record["run_id"]]["problems"]
    assert report.runs[record["run_id"]]["status"] == "INCONSISTENT_RUN"
    assert any(gone in p and "ausente" in p for p in problems)
    assert any(altered in p and "SHA-256" in p for p in problems)
    assert any(extra in p and "no citado" in p for p in problems)
    found = _by_key(report)
    assert found[altered]["integrity"] == "CORRUPT"
    assert found[extra]["integrity"] == "CORRUPT"  # el nombre declara un hash que no es el real


def test_snapshot_integrity_content_and_manifest(tmp_path: Path) -> None:
    store, repo, _, _ = _registered_run(tmp_path)
    bad_content = create_snapshot(store, synthetic_file.capture(VALID_CSV))
    bad_manifest = create_snapshot(store, synthetic_file.capture(VALID_CSV))
    tamper(object_path(store, f"snapshots/{bad_content['snapshot_id']}/content.bin"))
    tamper(object_path(store, f"snapshots/{bad_manifest['snapshot_id']}/manifest.json"))
    report = _safe_reconcile(store, repo)
    found = _by_key(report)
    assert found[f"snapshots/{bad_content['snapshot_id']}/content.bin"]["integrity"] == "CORRUPT"
    assert found[f"snapshots/{bad_manifest['snapshot_id']}/manifest.json"]["integrity"] == "CORRUPT"
    assert report.snapshots[bad_content["snapshot_id"]]["status"] == "SNAPSHOT_INCONSISTENT"


def test_sql_references_that_do_not_match_the_store_are_reported(tmp_path: Path) -> None:
    store, repo, run_id, snapshot_id = _registered_run(tmp_path)
    for key in (f"runs/{run_id}/run_record.json", f"snapshots/{snapshot_id}/content.bin"):
        os.chmod(object_path(store, key), 0o600)
        object_path(store, key).unlink()
    report = _safe_reconcile(store, repo)
    kinds = {i["kind"] for i in report.sql_reference_issues}
    assert "GENERATION_RUN_RECORD_UNAVAILABLE" in kinds
    assert "SNAPSHOT_REF_UNMATCHED" in kinds
    assert report.marked_missing == []  # los artefactos siguen presentes e íntegros


def test_staging_leftovers_are_reported_not_removed(tmp_path: Path) -> None:
    store, repo, _, _ = _registered_run(tmp_path)
    (store.root / "staging" / "resto.tmp").write_bytes(b"resto de una caida")
    report = _safe_reconcile(store, repo)
    assert report.staging_leftovers == ["resto.tmp"]
    assert (store.root / "staging" / "resto.tmp").exists()
