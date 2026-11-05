"""NX-E1A-03: relocalización EXTERNA de un directorio ya abierto durante ``put``.

A diferencia de ``test_toctou.py`` (que renombra dentro de ``objects/`` o sustituye por
un enlace), aquí el atacante MUEVE el directorio original ``objects/a`` fuera del
almacén (``outside/moved``), en el mismo sistema de archivos, justo antes de publicar.
Opcionalmente deja un symlink en su lugar.

En POSIX un descriptor abierto sigue apuntando al inodo del directorio movido:
``linkat(dst_dir_fd)`` por sí solo crearía el objeto en ``outside/moved/x.bin``.

Invariantes exigidos (ambas plataformas):
- I1: nunca queda ``outside/moved/x.bin`` (ni ningún archivo nuevo fuera del almacén).
- I2: si ``put`` declara éxito, el objeto es legible en su clave DENTRO del almacén.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nexus.errors import IntegrityError, NexusError
from nexus.hashing import sha256_bytes
from nexus.storage.object_store import LocalObjectStore

PAYLOAD = b"objeto-que-debe-quedar-dentro"


def _files(path: Path) -> list[str]:
    return sorted(p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file())


@pytest.mark.parametrize("replace_with_link", [False, True], ids=["move", "move+symlink"])
def test_external_relocation_during_publish(tmp_path: Path, replace_with_link: bool) -> None:
    root = tmp_path / "store"
    outside = tmp_path / "outside"
    outside.mkdir()
    LocalObjectStore(root).put("a/seed.bin", b"seed")  # crea objects/a
    victim = root / "objects" / "a"
    moved = outside / "moved"
    log: dict[str, object] = {}

    def hook(point: str) -> None:
        if point != "before_publish" or "relocated" in log:
            return
        try:
            os.rename(victim, moved)
        except OSError as exc:  # Windows: el directorio está bloqueado durante la operación
            log["relocated"] = False
            log["error"] = repr(exc)
            return
        log["relocated"] = True
        if replace_with_link and os.name != "nt":
            victim.symlink_to(moved, target_is_directory=True)
            log["symlink"] = True

    store = LocalObjectStore(root, fault_hook=hook)
    outcome: object
    try:
        outcome = store.put("a/x.bin", PAYLOAD)
    except (NexusError, OSError) as exc:
        outcome = exc
    print(f"log={log} outcome={outcome!r} outside={_files(outside)}")

    assert "relocated" in log, "la sonda debe haber intentado la relocalización"
    # I1: nada nuevo fuera del almacén (seed.bin sí puede estar fuera si se movió el dir:
    # lo movió el atacante, no lo escribió el almacén).
    created_outside = [f for f in _files(outside) if not f.endswith("seed.bin")]
    assert created_outside == [], f"el almacén creó archivos FUERA: {created_outside}"
    # I2: no se declara éxito sin contención.
    if not isinstance(outcome, Exception):
        assert store.get("a/x.bin", sha256_bytes(PAYLOAD)) == PAYLOAD
    else:
        assert isinstance(outcome, IntegrityError), outcome
        assert "frontera" in str(outcome)
