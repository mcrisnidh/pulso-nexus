"""A6 — snapshots: hash independiente, inmutabilidad, sellado y fallos cerrados."""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import pytest

from conftest import VALID_CSV, force_rmtree, object_path, tamper
from nexus.connectors import synthetic_file
from nexus.errors import IntegrityError, NotReproducibleError
from nexus.sealed import seal, unseal
from nexus.snapshots.capture import create_snapshot, load_snapshot, read_manifest
from nexus.storage.object_store import LocalObjectStore


def test_snapshot_hash_matches_independent_hashlib(store: LocalObjectStore) -> None:
    independent = hashlib.sha256(VALID_CSV.read_bytes()).hexdigest()
    manifest = create_snapshot(store, synthetic_file.capture(VALID_CSV))
    assert manifest["content_sha256"] == independent
    snap = load_snapshot(store, manifest["snapshot_id"], independent)
    assert snap.content == VALID_CSV.read_bytes()
    assert snap.content_sha256 == independent
    assert snap.snapshot_id == manifest["snapshot_id"]


def test_snapshot_manifest_fields_and_clock_injection(store: LocalObjectStore) -> None:
    fixed = dt.datetime(2026, 3, 4, 5, 6, 7, 999, tzinfo=dt.UTC)
    manifest = create_snapshot(store, synthetic_file.capture(VALID_CSV), clock=lambda: fixed)
    assert manifest["captured_at"] == "2026-03-04T05:06:07Z"
    assert manifest["source_metadata"] == {"file_name": "input_v1.csv"}
    assert manifest["consistency_level"] == "single_object"
    assert read_manifest(store, manifest["snapshot_id"]) == manifest


def test_each_capture_is_a_distinct_immutable_snapshot(store: LocalObjectStore) -> None:
    a = create_snapshot(store, synthetic_file.capture(VALID_CSV))
    b = create_snapshot(store, synthetic_file.capture(VALID_CSV))
    assert a["snapshot_id"] != b["snapshot_id"]
    assert a["content_sha256"] == b["content_sha256"]
    with pytest.raises(Exception, match="inmutable"):
        store.put(a["content_key"], b"otro contenido")


def test_wrong_expected_hash_rejected(store: LocalObjectStore, snapshot_id: str) -> None:
    with pytest.raises(IntegrityError):
        load_snapshot(store, snapshot_id, "0" * 64)


def test_tampered_content_rejected(store: LocalObjectStore, snapshot_id: str) -> None:
    manifest = read_manifest(store, snapshot_id)
    tamper(object_path(store, manifest["content_key"]))
    with pytest.raises(IntegrityError):
        load_snapshot(store, snapshot_id, manifest["content_sha256"])


def test_tampered_manifest_rejected(store: LocalObjectStore, snapshot_id: str) -> None:
    path = object_path(store, f"snapshots/{snapshot_id}/manifest.json")
    doc = json.loads(path.read_text("utf-8"))
    doc["source_id"] = "otra-fuente"
    tamper(path, json.dumps(doc).encode())
    with pytest.raises(IntegrityError):
        read_manifest(store, snapshot_id)


def test_manifest_for_other_snapshot_rejected(store: LocalObjectStore, snapshot_id: str) -> None:
    path = object_path(store, f"snapshots/{snapshot_id}/manifest.json")
    doc = unseal(path.read_bytes(), "m")
    doc["snapshot_id"] = "snap-" + "0" * 32
    tamper(path, seal(doc))
    with pytest.raises(IntegrityError):
        load_snapshot(store, snapshot_id, doc["content_sha256"])


def test_missing_snapshot_not_reproducible(store: LocalObjectStore, snapshot_id: str) -> None:
    manifest = read_manifest(store, snapshot_id)
    content = object_path(store, manifest["content_key"])
    content.chmod(0o600)
    content.unlink()
    with pytest.raises(NotReproducibleError):
        load_snapshot(store, snapshot_id, manifest["content_sha256"])
    force_rmtree(object_path(store, f"snapshots/{snapshot_id}"))
    with pytest.raises(NotReproducibleError):
        load_snapshot(store, snapshot_id, manifest["content_sha256"])
    with pytest.raises(NotReproducibleError):
        read_manifest(store, snapshot_id)


@pytest.mark.parametrize("bad_id", ["", "snap-XYZ", "../snap", "snap-" + "0" * 31])
def test_invalid_snapshot_id(store: LocalObjectStore, bad_id: str) -> None:
    with pytest.raises(NotReproducibleError):
        load_snapshot(store, bad_id, "0" * 64)
