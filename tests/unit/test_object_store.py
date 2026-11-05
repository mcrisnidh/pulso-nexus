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


def _break_publication_primitive(monkeypatch: pytest.MonkeyPatch, exc: OSError) -> None:
    """Hace fallar la primitiva REAL de publicación de la plataforma en curso
    (``os.link`` en POSIX, ``os.rename`` en Windows). No simula otra plataforma."""

    def boom(*_a: Any, **_k: Any) -> None:
        raise exc

    monkeypatch.setattr(os, "rename" if _fs.IS_WINDOWS else "link", boom)


def test_publication_failure_fails_closed_and_cleans_staging(
    store: LocalObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POSIX: un FS sin enlaces duros (EPERM) da UnsupportedFilesystemError, sin degradar a
    un rename que podría reemplazar. Windows: el error de MoveFileExW se propaga. En
    ambos casos no se publica nada y el temporal (marcado como solo lectura) se elimina.
    Este es el escenario que en Windows producía ``WinError 5``."""
    _break_publication_primitive(monkeypatch, OSError(errno.EPERM, "Operation not permitted"))
    expected = OSError if _fs.IS_WINDOWS else UnsupportedFilesystemError
    with pytest.raises(expected):
        store.put("x.bin", b"data")
    assert not store.exists("x.bin")
    assert store.staging_entries() == []


def test_other_publication_errors_propagate(
    store: LocalObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _break_publication_primitive(monkeypatch, OSError(errno.ENOSPC, "No space left on device"))
    with pytest.raises(OSError) as info:
        store.put("x.bin", b"data")
    assert info.value.errno == errno.ENOSPC  # el error original no queda oculto
    assert not store.exists("x.bin")
    assert store.staging_entries() == []


def test_publication_primitive_never_replaces(store: LocalObjectStore) -> None:
    """Primitiva real de la plataforma: con el destino existente falla y no lo toca."""
    store.put("a.bin", b"one")
    with (
        _fs.open_dir_chain(store.root / "staging", (), create=False) as staging,
        _fs.open_dir_chain(store.root / "objects", (), create=False) as objects,
    ):
        fd = _fs.create_exclusive(staging, "loser.tmp")
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"two")
        with pytest.raises(FileExistsError):
            _fs.publish_no_replace(staging, "loser.tmp", objects, "a.bin")
        _fs.remove_own_temp(staging, "loser.tmp")
    assert store.get("a.bin", sha256_bytes(b"one")) == b"one"
    assert store.staging_entries() == []


def test_remove_own_temp_never_touches_published_object(store: LocalObjectStore) -> None:
    """Un temporal que comparte inodo con un objeto publicado no se desprotege nunca.
    POSIX: se elimina el nombre temporal y el objeto conserva modo y contenido.
    Windows: el atributo de solo lectura es del archivo (compartido), así que se rechaza
    la eliminación antes que quitarle la protección al objeto publicado."""
    store.put("pub.bin", b"publicado")
    published = object_path(store, "pub.bin")
    shared = store.root / "staging" / "shared.tmp"
    os.link(published, shared)
    with _fs.open_dir_chain(store.root / "staging", (), create=False) as staging:
        if _fs.IS_WINDOWS:
            with pytest.raises(PermissionError):
                _fs.remove_own_temp(staging, "shared.tmp")
        else:
            _fs.remove_own_temp(staging, "shared.tmp")
            assert not shared.exists()
    assert stat.S_IMODE(os.stat(published).st_mode) & stat.S_IWUSR == 0
    assert store.get("pub.bin", sha256_bytes(b"publicado")) == b"publicado"


def test_reparse_point_detected_via_file_attributes(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    real = os.lstat(path)

    class FakeStat:
        st_mode = real.st_mode
        st_file_attributes = 0x400

    fake = FakeStat()
    assert _fs.stat_is_link(fake) is True  # type: ignore[arg-type]
    assert _fs.stat_is_regular(fake) is False  # type: ignore[arg-type]
    assert _fs.stat_is_regular(real) is True


def test_fs_helpers_on_missing_paths(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert _fs.is_link_like(missing) is False
    with _fs.open_dir_chain(tmp_path, (), create=False) as parent:
        assert _fs.entry_stat(parent, "missing") is None
        _fs.remove_own_temp(parent, "missing")  # tolera inexistente
    with pytest.raises(FileNotFoundError):
        _fs.open_dir_chain(tmp_path, ("no-existe",), create=False).__enter__()


def test_fsync_dir_by_platform(tmp_path: Path) -> None:
    """POSIX sincroniza el directorio; Windows no puede (pendiente de certificar)."""
    with _fs.open_dir_chain(tmp_path, (), create=False) as parent:
        assert _fs.fsync_dir(parent) is (not _fs.IS_WINDOWS)


def test_chain_rejects_non_directory_component(store: LocalObjectStore) -> None:
    store.put("plain.bin", b"x")
    with pytest.raises(IntegrityError, match="no es"):
        store.get("plain.bin/child.bin", sha256_bytes(b"x"))


@pytest.mark.parametrize(("rival", "expected"), [(b"mismo", None), (b"rival", "conflict")])
def test_deterministic_publish_race(tmp_path: Path, rival: bytes, expected: str | None) -> None:
    """Otro escritor publica la misma clave justo antes de nuestra publicación."""
    root = tmp_path / "store"
    other = LocalObjectStore(root)

    def hook(point: str) -> None:
        if point == "before_publish":
            other.put("k/race.bin", rival)

    store = LocalObjectStore(root, fault_hook=hook)
    if expected is None:
        assert store.put("k/race.bin", b"mismo").sha256 == sha256_bytes(b"mismo")
    else:
        with pytest.raises(ImmutableConflictError):
            store.put("k/race.bin", b"mio")
    assert store.get("k/race.bin", sha256_bytes(rival)) == rival
    assert store.staging_entries() == []


def test_directory_key_and_missing_parent(store: LocalObjectStore) -> None:
    store.put("d/x.bin", b"x")
    with pytest.raises(FileNotFoundError):
        store.get("d", sha256_bytes(b"x"))
    assert store.exists("nope/x.bin") is False
    assert store.exists("d/x.bin") is True


@pytest.mark.parametrize(
    ("err", "expected"),
    [(errno.ELOOP, IntegrityError), (errno.EACCES, PermissionError)],
)
def test_dir_open_error_mapping(
    monkeypatch: pytest.MonkeyPatch, err: int, expected: type[Exception]
) -> None:
    """Traducción de errores al abrir un componente sin seguir enlaces: ELOOP (enlace en
    BSD/macOS) → IntegrityError; cualquier otro error se propaga sin ocultarse."""

    def failing_open(*_a: Any, **_k: Any) -> int:
        exc_type = PermissionError if err == errno.EACCES else OSError
        raise exc_type(err, os.strerror(err))

    monkeypatch.setattr(os, "open", failing_open)
    with pytest.raises(expected):
        _fs._open_dir_nofollow("victim", None, Path("victim"))


def test_read_tolerates_transient_exclusive_handle(store: LocalObjectStore) -> None:
    """NX-E1A-01 #8: otro proceso mantiene un instante el objeto recién publicado abierto
    SIN compartir lectura (en Windows: CreateFileW con share=0, como un antivirus). La
    lectura reintenta de forma acotada y verifica el contenido; no falla en falso.
    En POSIX no existen los modos de compartición: la lectura simultánea siempre funciona."""
    import threading
    import time

    store.put("busy/x.bin", b"contenido")
    path = object_path(store, "busy/x.bin")
    released = threading.Event()

    if _fs.IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        handle = k32.CreateFileW(
            str(path), 0x80000000, 0, None, 3, 0, None
        )  # GENERIC_READ, share=0
        assert handle not in (None, ctypes.c_void_p(-1).value), ctypes.get_last_error()

        def release() -> None:
            time.sleep(0.12)
            k32.CloseHandle(wintypes.HANDLE(handle))
            released.set()
    else:
        holder = open(path, "rb")  # noqa: SIM115

        def release() -> None:
            time.sleep(0.12)
            holder.close()
            released.set()

    threading.Thread(target=release, daemon=True).start()
    assert store.get("busy/x.bin", sha256_bytes(b"contenido")) == b"contenido"
    released.wait(5)
    assert released.is_set()
