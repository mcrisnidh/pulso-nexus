"""Adversariales de manipulación: archivo plantado, sustitución posterior, enlaces."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import AS_OF, object_path
from nexus.errors import ImmutableConflictError, IntegrityError
from nexus.hashing import sha256_bytes
from nexus.pipeline.replay import replay
from nexus.pipeline.run import execute
from nexus.pipeline.verify import verify_store
from nexus.storage.object_store import LocalObjectStore

posix_only = pytest.mark.skipif(os.name == "nt", reason="garantía POSIX; Windows pendiente")


def test_planted_file_before_write_is_detected_and_untouched(store: LocalObjectStore) -> None:
    dst = object_path(store, "planted/x.bin")
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"plantado")
    with pytest.raises(ImmutableConflictError):
        store.put("planted/x.bin", b"legitimo")
    assert dst.read_bytes() == b"plantado"


def test_substitution_after_write_detected_on_read(store: LocalObjectStore) -> None:
    ref = store.put("sub/x.bin", b"original")
    path = object_path(store, "sub/x.bin")
    path.chmod(0o600)
    path.unlink()
    path.write_bytes(b"sustituto")  # sustitución completa (nuevo inodo)
    with pytest.raises(IntegrityError):
        store.get("sub/x.bin", ref.sha256)


def test_artifact_substitution_detected_by_verify_and_replay(
    store: LocalObjectStore, snapshot_id: str
) -> None:
    record = execute(store, snapshot_id=snapshot_id, as_of=AS_OF)
    victim = record["artifacts"][2]
    other = record["artifacts"][1]
    path = object_path(store, victim["key"])
    path.chmod(0o600)
    path.unlink()
    path.write_bytes(object_path(store, other["key"]).read_bytes())  # archivo válido ajeno
    report = verify_store(store)
    assert not report.ok
    with pytest.raises(IntegrityError):
        replay(store, record["run_id"])


@posix_only
def test_object_replaced_by_symlink_detected(store: LocalObjectStore, tmp_path: Path) -> None:
    ref = store.put("lnk/x.bin", b"original")
    decoy = tmp_path / "decoy.bin"
    decoy.write_bytes(b"original")  # incluso con el mismo contenido
    path = object_path(store, "lnk/x.bin")
    path.chmod(0o600)
    path.unlink()
    path.symlink_to(decoy)
    with pytest.raises(IntegrityError, match="enlace"):
        store.get("lnk/x.bin", ref.sha256)


def test_conflict_never_modifies_existing(store: LocalObjectStore) -> None:
    store.put("k.bin", b"A")
    for attempt in (b"B", b"", b"A" * 10):
        with pytest.raises(ImmutableConflictError):
            store.put("k.bin", attempt)
    assert store.get("k.bin", sha256_bytes(b"A")) == b"A"
