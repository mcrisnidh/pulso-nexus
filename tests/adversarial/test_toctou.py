"""NX-E1A-02 — sondas TOCTOU: sustitución de un directorio intermedio por un enlace.

Atacante: entre la validación de la ruta y la operación de E/S, renombra un
directorio intermedio del almacén y pone en su lugar un enlace (symlink en POSIX,
junction en Windows) hacia un directorio EXTERNO.

Invariantes exigidos:
- Escritura: nunca se crea un archivo fuera del ObjectStore.
- Lectura: nunca se abre un archivo fuera del ObjectStore. La sonda pone en el
  directorio externo un archivo con contenido distinto. Si el almacén lo abre, el
  hash no coincide y aparece "hash no coincide": esa es la firma de una lectura fuera.

En Windows el ataque puede fallar en el propio renombrado: el almacén mantiene
abiertos los directorios de la ruta sin compartir DELETE. Se registra igualmente y el
invariante se comprueba en ambos casos.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from nexus.errors import IntegrityError, NexusError
from nexus.hashing import sha256_bytes
from nexus.storage.object_store import LocalObjectStore

REAL = b"contenido-real-del-almacen"
# Rechazos cerrados de Windows durante el ataque (no devuelven datos):
#   32 ERROR_SHARING_VIOLATION: el renombrado del atacante y nuestro bloqueo compiten.
#   5  ERROR_ACCESS_DENIED: la junction del atacante quedó en estado "delete-pending"
#      (borrada mientras estaba abierta) y Windows deniega nuevas aperturas.
WINDOWS_CLOSED_REJECTIONS = {5, 32}
ATTACKER = b"contenido-externo-del-atacante"


def make_dir_link(target: Path, link: Path) -> None:
    """Crea un enlace de directorio: symlink en POSIX, junction en Windows (sin privilegios)."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def remove_dir_link(link: Path) -> None:
    if os.name == "nt":
        os.rmdir(link)  # elimina la junction sin tocar el destino
    else:
        link.unlink()


def files_under(path: Path) -> list[str]:
    return sorted(p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file())


def swapper(root: Path, rel_dir: str, outside: Path, log: dict[str, object]) -> Callable[[], None]:
    def swap() -> None:
        victim = root / "objects" / rel_dir
        try:
            os.rename(victim, victim.with_name(victim.name + "-moved"))
        except OSError as exc:
            log["swapped"] = False
            log["swap_error"] = repr(exc)
            return
        make_dir_link(outside, victim)
        log["swapped"] = True

    return swap


@pytest.mark.parametrize(
    ("key", "rel_dir"),
    [("a/x.bin", "a"), ("a/b/x.bin", "a"), ("a/b/x.bin", "a/b")],
)
def test_publish_never_creates_file_outside_store(tmp_path: Path, key: str, rel_dir: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "store"
    LocalObjectStore(root).put(f"{rel_dir}/seed.bin", b"seed")  # crea los directorios
    log: dict[str, object] = {}
    swap = swapper(root, rel_dir, outside, log)

    def hook(point: str) -> None:
        if point == "before_publish" and "swapped" not in log:
            swap()

    store = LocalObjectStore(root, fault_hook=hook)
    outcome: object
    try:
        outcome = store.put(key, REAL)
    except (NexusError, OSError) as exc:
        outcome = exc
    print(f"swap={log} outcome={outcome!r}")
    assert "swapped" in log, "la sonda debe haber intentado el ataque"
    assert files_under(outside) == [], f"archivo creado FUERA del almacén: {files_under(outside)}"
    if log["swapped"] and not isinstance(outcome, Exception):
        # El objeto quedó DENTRO del almacén, en el directorio original (ahora renombrado),
        # y la clave ya no es legible a través del enlace.
        inside = files_under(root / "objects")
        assert any(p.endswith(key.rsplit("/", 1)[-1]) and "-moved" in p for p in inside), inside
        with pytest.raises(IntegrityError, match="enlace"):
            store.get(key, sha256_bytes(REAL))


def test_read_never_opens_file_outside_store(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.bin").write_bytes(ATTACKER)
    root = tmp_path / "store"
    LocalObjectStore(root).put("a/x.bin", REAL)
    log: dict[str, object] = {}
    swap = swapper(root, "a", outside, log)

    def hook(point: str) -> None:
        if point == "before_open_read" and "swapped" not in log:
            swap()

    store = LocalObjectStore(root, fault_hook=hook)
    try:
        data = store.get("a/x.bin", sha256_bytes(REAL))
    except IntegrityError as exc:
        assert "hash no coincide" not in str(exc), f"se leyó un archivo FUERA del almacén: {exc}"
        assert "enlace" in str(exc)  # rechazo legítimo por enlace detectado
        return
    print(f"swap={log}")
    assert data == REAL


class ParentSwapAttacker(threading.Thread):
    """Hilo atacante: alterna ``objects/a`` entre el directorio real y un enlace externo.

    En Windows cada paso puede chocar con el bloqueo del almacén (WinError 32). El
    atacante NO se rinde: reintenta y lo contabiliza. Cualquier otra excepción queda en
    ``self.error`` y hace fallar la prueba, así que un ataque no puede morir en silencio.
    """

    def __init__(self, root: Path, outside: Path) -> None:
        super().__init__(daemon=True)
        self.victim = root / "objects" / "a"
        self.moved = root / "objects" / "a-moved"
        self.outside = outside
        self.stop = threading.Event()
        self.swaps = {"ok": 0, "blocked": 0, "contention": 0}
        self.error: BaseException | None = None

    def _persist(self, action: Callable[[], None]) -> bool:
        while not self.stop.is_set():
            try:
                action()
                return True
            except PermissionError:
                self.swaps["contention"] += 1
        return False

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                try:
                    os.rename(self.victim, self.moved)
                except PermissionError:
                    self.swaps["blocked"] += 1  # el almacén tiene el directorio bloqueado
                    continue
                if not self._persist(lambda: make_dir_link(self.outside, self.victim)):
                    return
                self.swaps["ok"] += 1
                if not self._persist(lambda: remove_dir_link(self.victim)):
                    return
                if not self._persist(lambda: os.rename(self.moved, self.victim)):
                    return
        except BaseException as exc:  # registrada y comprobada por la prueba
            self.error = exc

    @property
    def attempts(self) -> int:
        return self.swaps["ok"] + self.swaps["blocked"]

    def finish(self) -> None:
        self.stop.set()
        self.join(timeout=30)
        if self.victim.is_symlink() or (
            os.name == "nt" and self.victim.exists() and self.moved.exists()
        ):
            remove_dir_link(self.victim)
        if self.moved.exists() and not self.victim.exists():
            os.rename(self.moved, self.victim)


def test_concurrent_reads_during_parent_swaps(tmp_path: Path) -> None:
    """Variante concurrente: un hilo atacante alterna el directorio con un enlace mientras
    otro hilo lee repetidamente. Ninguna lectura debe abrir el archivo externo."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.bin").write_bytes(ATTACKER)
    store = LocalObjectStore(tmp_path / "store")
    store.put("a/x.bin", REAL)
    expected = sha256_bytes(REAL)
    attacker = ParentSwapAttacker(store.root, outside)
    outcomes = {"real": 0, "rejected": 0, "missing": 0, "win_denied_5": 0, "win_denied_32": 0}
    escapes: list[str] = []
    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # fuerza intercalados frecuentes entre hilos
    deadline = time.monotonic() + 20
    attacker.start()
    try:
        while time.monotonic() < deadline and (
            attacker.attempts < 300 or sum(outcomes.values()) < 300
        ):
            try:
                assert store.get("a/x.bin", expected) == REAL
                outcomes["real"] += 1
            except IntegrityError as exc:
                if "hash no coincide" in str(exc):
                    escapes.append(str(exc))
                else:
                    outcomes["rejected"] += 1
            except FileNotFoundError:
                outcomes["missing"] += 1
            except PermissionError as exc:
                # Solo Windows: rechazo cerrado por bloqueo o por objeto pendiente de borrado.
                # Cualquier otro PermissionError (incluido cualquiera en POSIX) hace fallar.
                if getattr(exc, "winerror", None) not in WINDOWS_CLOSED_REJECTIONS:
                    raise
                outcomes[f"win_denied_{exc.winerror}"] += 1  # type: ignore[attr-defined]
    finally:
        attacker.finish()
        sys.setswitchinterval(previous_interval)
        shutil.rmtree(outside, ignore_errors=True)
    print(f"swaps={attacker.swaps} outcomes={outcomes} escapes={len(escapes)}")
    assert attacker.error is None, f"el hilo atacante terminó con error: {attacker.error!r}"
    assert attacker.attempts >= 300, f"ataque insuficiente: {attacker.swaps}"
    assert escapes == [], f"{len(escapes)} lecturas abrieron un archivo externo: {escapes[:2]}"


def test_final_component_swap_between_check_and_open(tmp_path: Path) -> None:
    """El propio objeto se sustituye (otro inodo, mismo contenido) entre la comprobación y
    la apertura: la identidad (st_dev, st_ino) no coincide y la lectura falla cerrada."""
    root = tmp_path / "store"
    LocalObjectStore(root).put("a/x.bin", REAL)
    target = root / "objects" / "a" / "x.bin"
    done: dict[str, bool] = {}

    def hook(point: str) -> None:
        if point == "before_open_read" and not done:
            # El sustituto se crea ANTES de retirar el original para que tenga otro inodo
            # (tras un unlink, el sistema de archivos puede reutilizar el número de inodo).
            replacement = target.with_name("replacement.tmp")
            replacement.write_bytes(REAL)  # mismo contenido: el hash por sí solo no lo vería
            os.chmod(target, 0o600)
            target.unlink()
            os.rename(replacement, target)
            done["swapped"] = True

    store = LocalObjectStore(root, fault_hook=hook)
    with pytest.raises(IntegrityError, match="cambió"):
        store.get("a/x.bin", sha256_bytes(REAL))
    assert done == {"swapped": True}
