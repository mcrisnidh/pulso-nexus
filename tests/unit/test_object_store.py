"""A4 — ObjectStore: escritura protegida, inmutabilidad, verificación y claves portables."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from conftest import object_path, tamper
from nexus.errors import (
    ImmutableConflictError,
    IntegrityError,
    InvalidKeyError,
    UnsupportedFilesystemError,
)
from nexus.hashing import sha256_bytes
from nexus.storage import _fs
from nexus.storage.object_store import LocalObjectStore, validate_key

posix_only = pytest.mark.skipif(os.name == "nt", reason="garantía POSIX; Windows pendiente")


def test_put_get_roundtrip(store: LocalObjectStore) -> None:
    ref = store.put("a/b/c.bin", b"hola")
    assert ref.sha256 == sha256_bytes(b"hola")
    assert ref.size == 4
    assert store.get("a/b/c.bin", ref.sha256) == b"hola"
    assert store.exists("a/b/c.bin")
    assert not store.exists("a/b/d.bin")
    assert store.staging_entries() == []


@posix_only
def test_published_object_is_read_only(store: LocalObjectStore) -> None:
    store.put("x.bin", b"data")
    mode = stat.S_IMODE(os.stat(object_path(store, "x.bin")).st_mode)
    assert mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) == 0


def test_rewrite_same_content_is_idempotent_and_does_not_touch_file(
    store: LocalObjectStore,
) -> None:
    store.put("x.bin", b"same")
    before = os.stat(object_path(store, "x.bin"))
    ref = store.put("x.bin", b"same")
    after = os.stat(object_path(store, "x.bin"))
    assert ref.sha256 == sha256_bytes(b"same")
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
    assert store.staging_entries() == []


def test_rewrite_different_content_is_rejected(store: LocalObjectStore) -> None:
    store.put("x.bin", b"original")
    with pytest.raises(ImmutableConflictError):
        store.put("x.bin", b"different")
    assert store.get("x.bin", sha256_bytes(b"original")) == b"original"
    assert store.staging_entries() == []


def test_tampered_object_fails_closed_on_read(store: LocalObjectStore) -> None:
    ref = store.put("x.bin", b"original-content")
    tamper(object_path(store, "x.bin"))
    with pytest.raises(IntegrityError):
        store.get("x.bin", ref.sha256)
    with pytest.raises(IntegrityError):
        store.verify("x.bin", ref.sha256)


def test_missing_object(store: LocalObjectStore) -> None:
    with pytest.raises(FileNotFoundError):
        store.get("nope.bin", "0" * 64)
    with pytest.raises(FileNotFoundError):
        store.get_self_verifying("nope.json", lambda _b: None)


def test_self_verifying_read_calls_verifier(store: LocalObjectStore) -> None:
    store.put("m.json", b"{}")
    seen: list[bytes] = []
    assert store.get_self_verifying("m.json", seen.append) == b"{}"
    assert seen == [b"{}"]

    def reject(_data: bytes) -> None:
        raise IntegrityError("sello inválido")

    with pytest.raises(IntegrityError):
        store.get_self_verifying("m.json", reject)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "../x",
        "a/../b",
        "/abs",
        "C:/x",
        "c:x",
        "A/b",
        "con",
        "nul.txt",
        "a/com1.json",
        "a/.hidden",
        "a/b.",
        "a b",
        "a\\b",
        "a//b",
        "a/b:stream",
        "x" * 181,
        "a/" + "y" * 101,
        "ñ",
    ],
)
def test_non_portable_keys_rejected(store: LocalObjectStore, key: str) -> None:
    with pytest.raises(InvalidKeyError):
        validate_key(key)
    with pytest.raises(InvalidKeyError):
        store.put(key, b"x")


@pytest.mark.parametrize(
    "key", ["a", "runs/run-1/generation/x-0abc.parquet", "snap-01/content.bin", "a.b_c-d"]
)
def test_portable_keys_accepted(key: str) -> None:
    assert validate_key(key) == tuple(key.split("/"))


@posix_only
def test_symlink_at_destination_is_never_followed_or_replaced(
    store: LocalObjectStore, tmp_path: Path
) -> None:
    target = tmp_path / "outside.bin"
    target.write_bytes(b"victim")
    dst = object_path(store, "link.bin")
    dst.symlink_to(target)
    with pytest.raises(ImmutableConflictError):
        store.put("link.bin", b"attacker")
    assert target.read_bytes() == b"victim"
    assert dst.is_symlink()
    with pytest.raises(IntegrityError):
        store.get("link.bin", sha256_bytes(b"victim"))


@posix_only
def test_symlinked_intermediate_directory_rejected(store: LocalObjectStore, tmp_path: Path) -> None:
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (store.root / "objects" / "evil").symlink_to(outside, target_is_directory=True)
    with pytest.raises(IntegrityError):
        store.put("evil/x.bin", b"data")
    assert list(outside.iterdir()) == []


@posix_only
def test_store_root_symlink_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(IntegrityError):
        LocalObjectStore(link)


def test_destination_directory_is_not_regular_file(store: LocalObjectStore) -> None:
    object_path(store, "dir.bin").mkdir(parents=True)
    with pytest.raises(ImmutableConflictError):
        store.put("dir.bin", b"x")


def test_iter_keys_deterministic(store: LocalObjectStore) -> None:
    for key in ["b/2.bin", "a/1.bin", "b/1.bin", "c.bin"]:
        store.put(key, key.encode())
    assert list(store.iter_keys()) == ["c.bin", "a/1.bin", "b/1.bin", "b/2.bin"]
    assert list(store.iter_keys("b/")) == ["b/1.bin", "b/2.bin"]


def test_unsupported_filesystem_fails_closed(
    store: LocalObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_fs, "IS_WINDOWS", False)

    def no_links(*_a: Any, **_k: Any) -> None:
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_links)
    with pytest.raises(UnsupportedFilesystemError):
        store.put("x.bin", b"data")
    assert not store.exists("x.bin")
    assert store.staging_entries() == []


def test_other_link_errors_propagate(
    store: LocalObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_fs, "IS_WINDOWS", False)

    def no_space(*_a: Any, **_k: Any) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "link", no_space)
    with pytest.raises(OSError) as info:
        store.put("x.bin", b"data")
    assert info.value.errno == errno.ENOSPC
    assert store.staging_entries() == []


def test_windows_code_path_uses_non_replacing_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulación de la RUTA DE CÓDIGO de Windows en Linux. No certifica NTFS: solo
    comprueba que en Windows se usa ``os.rename`` (MoveFileExW sin REPLACE_EXISTING)
    y nunca ``os.replace``, y que no se fsincroniza el directorio."""
    calls: list[str] = []
    real_rename = os.rename

    def spy_rename(src: Any, dst: Any) -> None:
        calls.append("rename")
        if os.path.lexists(dst):  # emula la semántica de Windows: no reemplaza
            raise FileExistsError(errno.EEXIST, "exists", str(dst))
        real_rename(src, dst)

    def forbidden(*_a: Any, **_k: Any) -> None:
        raise AssertionError("os.replace no debe usarse")

    monkeypatch.setattr(_fs, "IS_WINDOWS", True)
    monkeypatch.setattr(os, "rename", spy_rename)
    monkeypatch.setattr(os, "replace", forbidden)
    store = LocalObjectStore(tmp_path / "winstore")
    store.put("a.bin", b"one")
    with pytest.raises(ImmutableConflictError):
        store.put("a.bin", b"two")
    assert calls == ["rename"]  # el segundo put se rechaza antes de escribir
    # Carrera: el destino aparece entre la comprobación y la publicación.
    src = tmp_path / "loser.tmp"
    src.write_bytes(b"two")
    with pytest.raises(FileExistsError):
        _fs.publish_no_replace(src, store.root / "objects" / "a.bin")
    assert calls == ["rename", "rename"]
    assert store.get("a.bin", sha256_bytes(b"one")) == b"one"
    assert _fs.fsync_dir(tmp_path) is False
    assert store.staging_entries() == []


def test_reparse_point_detected_via_file_attributes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    real = os.lstat(path)

    class FakeStat:
        st_mode = real.st_mode
        st_file_attributes = 0x400

    monkeypatch.setattr(os, "lstat", lambda _p: FakeStat())
    assert _fs.is_link_like(path) is True
    assert _fs.is_regular_file(path) is False


def test_fs_helpers_on_missing_paths(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert _fs.is_link_like(missing) is False
    assert _fs.is_regular_file(missing) is False
    _fs.remove_file(missing)  # tolera inexistente


@posix_only
def test_fsync_dir_posix(tmp_path: Path) -> None:
    assert _fs.fsync_dir(tmp_path) is True
