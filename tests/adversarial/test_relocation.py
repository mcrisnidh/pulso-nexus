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


# --------------------------------------------------------------------------- regresión
ATTACKER_CODE = r"""
import json, os, sys
try:
    os.rename(sys.argv[1], sys.argv[2])
    print(json.dumps({"renamed": True}))
except OSError as exc:
    print(json.dumps({"renamed": False, "errno": exc.errno, "error": repr(exc)}))
"""


def _run_as_other_uid(src: Path, dst: Path) -> dict[str, object]:
    """Ejecuta el atacante como OTRO usuario real del sistema (nobody, uid 65534).

    root: setuid en el hijo; usuario normal: ``sudo -n -u nobody``. Si ninguna opción es
    posible, la prueba FALLA (no se omite): el control de permisos no puede darse por
    demostrado sin un segundo principal.
    """
    import json
    import shutil
    import subprocess

    python = shutil.which("python3", path="/usr/bin:/bin")
    assert python, "se requiere /usr/bin/python3 para el proceso atacante"
    cmd = [python, "-I", "-c", ATTACKER_CODE, str(src), str(dst)]
    if os.geteuid() == 0:  # type: ignore[attr-defined]

        def drop() -> None:
            os.setgid(65534)  # type: ignore[attr-defined]
            os.setuid(65534)  # type: ignore[attr-defined]

        proc = subprocess.run(cmd, capture_output=True, text=True, preexec_fn=drop, check=False)
    else:
        sudo = shutil.which("sudo")
        probe = (
            subprocess.run(["sudo", "-n", "true"], capture_output=True, check=False)
            if sudo
            else None
        )
        assert probe is not None and probe.returncode == 0, (
            "se requiere root o 'sudo -n' para ejecutar al atacante con otro uid"
        )
        proc = subprocess.run(
            ["sudo", "-n", "-u", "nobody", *cmd], capture_output=True, text=True, check=False
        )
    assert proc.returncode == 0, proc.stderr
    return dict(json.loads(proc.stdout))


@pytest.fixture
def shared_base() -> object:
    """Directorio base atravesable por otros usuarios (como un volumen compartido)."""
    import shutil
    import tempfile

    base = Path(tempfile.mkdtemp(prefix="nexus-relocation-"))
    os.chmod(base, 0o755)
    yield base
    for p in sorted(base.rglob("*"), reverse=True):
        if not p.is_symlink():
            os.chmod(p, 0o777)
    shutil.rmtree(base, ignore_errors=True)


def test_permission_isolation_prevents_relocation_by_other_principal(shared_base: Path) -> None:
    """Control EFECTIVO de NX-E1A-03.

    POSIX: el árbol del almacén es 0700 y propiedad de su uid, así que un principal distinto
    no puede relocalizar ``objects/a`` (EACCES) y el objeto queda dentro. Control positivo:
    el mismo atacante SÍ relocaliza en un árbol permisivo, lo que demuestra que tiene la
    capacidad y que el control efectivo son los permisos.
    Windows: el directorio está bloqueado sin FILE_SHARE_DELETE durante la operación.
    """
    base = shared_base
    assert isinstance(base, Path)
    outside = base / "outside"
    outside.mkdir()
    os.chmod(outside, 0o777)
    root = base / "store"
    LocalObjectStore(root).put("a/seed.bin", b"seed")
    events: dict[str, object] = {}

    if os.name == "nt":

        def hook(point: str) -> None:
            if point == "before_publish" and not events:
                try:
                    os.rename(root / "objects" / "a", outside / "moved")
                    events["result"] = {"renamed": True}
                except OSError as exc:
                    events["result"] = {"renamed": False, "error": repr(exc)}
    else:
        # Control positivo: árbol permisivo NO gestionado por el almacén.
        open_tree = base / "open-tree" / "objects" / "a"
        open_tree.mkdir(parents=True)
        for d in (base / "open-tree", base / "open-tree" / "objects", open_tree):
            os.chmod(d, 0o777)
        control = _run_as_other_uid(open_tree, outside / "control-moved")
        assert control["renamed"] is True, f"el atacante debe tener capacidad real: {control}"

        def hook(point: str) -> None:
            if point == "before_publish" and not events:
                events["result"] = _run_as_other_uid(root / "objects" / "a", outside / "moved")

    store = LocalObjectStore(root, fault_hook=hook)
    ref = store.put("a/x.bin", PAYLOAD)
    print(f"events={events}")
    result = events["result"]
    assert isinstance(result, dict) and result["renamed"] is False, result
    assert store.get("a/x.bin", ref.sha256) == PAYLOAD
    assert not (outside / "moved").exists()


def test_residual_race_after_check_is_detected_not_prevented(tmp_path: Path) -> None:
    """Límite DECLARADO (POSIX): si quien comparte el uid del almacén (o root) relocaliza
    el directorio DESPUÉS de la comprobación de contención, el éxito ya declarado no puede
    revertirse. Equivale a manipular el almacén después de publicar. Se DETECTA en la
    siguiente operación (fallo cerrado), pero no se previene. Por eso el control efectivo
    es el aislamiento por permisos y no la comprobación de ruta.
    En Windows la relocalización se rechaza mientras la cadena está bloqueada."""
    root = tmp_path / "store"
    outside = tmp_path / "outside"
    outside.mkdir()
    LocalObjectStore(root).put("a/seed.bin", b"seed")
    log: dict[str, object] = {}

    def hook(point: str) -> None:
        if point == "after_containment_check" and not log:
            try:
                os.rename(root / "objects" / "a", outside / "moved")
                log["relocated"] = True
            except OSError as exc:
                log["relocated"] = False
                log["error"] = repr(exc)

    store = LocalObjectStore(root, fault_hook=hook)
    store.put("a/x.bin", PAYLOAD)  # éxito declarado antes de la relocalización
    if os.name == "nt":
        assert log["relocated"] is False, log  # bloqueado por el candado de directorio
        assert store.get("a/x.bin", sha256_bytes(PAYLOAD)) == PAYLOAD
        return
    assert log["relocated"] is True
    assert (outside / "moved" / "x.bin").exists()  # límite: posterior al éxito
    with pytest.raises(FileNotFoundError):  # detección: fallo cerrado
        LocalObjectStore(root).get("a/x.bin", sha256_bytes(PAYLOAD))
