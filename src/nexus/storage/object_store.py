"""Almacén local de objetos inmutables (protocolo E0-R2 §4.2 pasos 1–3 y §10.2).

Protocolo de ``put``:

1. Se valida la clave (portable POSIX + Windows/NTFS).
2. Si el destino ya existe, se verifica su hash. Si es igual, la operación es
   idempotente y no escribe nada. Si es distinto, ``ImmutableConflictError``.
3. Se escribe en ``staging/<uuid>.tmp`` con ``O_CREAT|O_EXCL`` y se hace ``fsync``.
4. El temporal se marca como solo lectura **antes** de publicarse.
5. Publicación **sin reemplazo** (``_fs.publish_no_replace``). Si otro proceso
   ganó la carrera, se aplica la misma regla del paso 2.
6. Se hace ``fsync`` del directorio (cuando la plataforma lo permite).
7. Se relee el destino y se verifica el hash.
8. Se elimina el temporal.

Cualquier lectura exige el hash esperado y falla cerrado (``IntegrityError``).
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from nexus.errors import ImmutableConflictError, IntegrityError, InvalidKeyError
from nexus.hashing import sha256_bytes
from nexus.storage import _fs

FaultHook = Callable[[str], None]

_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MAX_KEY_LENGTH = 180  # deja margen bajo MAX_PATH (260) para la raíz del almacén en Windows
_READ_CHUNK = 1024 * 1024


def validate_key(key: str) -> tuple[str, ...]:
    """Valida una clave relativa POSIX portable a NTFS y devuelve sus segmentos.

    Reglas: solo ``[a-z0-9._-]`` en minúsculas (evita colisiones por mayúsculas y
    minúsculas en NTFS), segmentos separados por ``/`` que empiezan por un carácter
    alfanumérico (excluye ``.``, ``..`` y archivos ocultos), sin punto final, sin
    nombres reservados de Windows y con una longitud total acotada.
    """
    if not isinstance(key, str) or not key or len(key) > MAX_KEY_LENGTH:
        raise InvalidKeyError(f"clave vacía o demasiado larga: {key!r}")
    segments = tuple(key.split("/"))
    for seg in segments:
        if not _SEGMENT.match(seg):
            raise InvalidKeyError(f"segmento no portable en {key!r}: {seg!r}")
        if seg.endswith("."):
            raise InvalidKeyError(f"segmento con punto final en {key!r}")
        if seg.split(".", 1)[0] in _WINDOWS_RESERVED:
            raise InvalidKeyError(f"nombre reservado de Windows en {key!r}: {seg!r}")
    return segments


@dataclass(frozen=True)
class ObjectRef:
    key: str
    sha256: str
    size: int


class LocalObjectStore:
    """Almacén de objetos inmutables sobre un directorio local."""

    def __init__(self, root: Path | str, fault_hook: FaultHook | None = None) -> None:
        self.root = Path(root).absolute()
        self._objects = self.root / "objects"
        self._staging = self.root / "staging"
        self._fault = fault_hook
        for directory in (self.root, self._objects, self._staging):
            if _fs.is_link_like(directory):
                raise IntegrityError(f"directorio del almacén es un enlace: {directory}")
            directory.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ internos
    def _checkpoint(self, name: str) -> None:
        if self._fault is not None:
            self._fault(name)

    def _path(self, key: str) -> Path:
        segments = validate_key(key)
        current = self._objects
        for seg in segments[:-1]:
            current = current / seg
            if _fs.is_link_like(current):
                raise IntegrityError(f"componente de ruta es un enlace: {key!r}")
        return self._objects.joinpath(*segments)

    def _read_verified(self, path: Path, key: str, expected_sha256: str) -> bytes:
        if not _fs.is_regular_file(path):
            if _fs.is_link_like(path):
                raise IntegrityError(f"{key}: el objeto es un enlace, no un archivo regular")
            raise FileNotFoundError(key)
        self._checkpoint("before_open_read")
        fd = _fs.open_read_nofollow(path)
        with os.fdopen(fd, "rb") as fh:
            data = fh.read()
        actual = sha256_bytes(data)
        if actual != expected_sha256:
            raise IntegrityError(
                f"{key}: hash no coincide (esperado={expected_sha256[:12]}…, actual={actual[:12]}…)"
            )
        return data

    def _existing_matches(self, path: Path, key: str, sha: str) -> bool:
        """True si el destino existente es idéntico. Lanza conflicto si difiere."""
        if _fs.is_link_like(path):
            raise ImmutableConflictError(f"{key}: el destino es un enlace; no se toca")
        if not _fs.is_regular_file(path):
            raise ImmutableConflictError(f"{key}: el destino existe y no es un archivo regular")
        try:
            self._read_verified(path, key, sha)
        except IntegrityError as exc:
            raise ImmutableConflictError(
                f"{key}: ya existe con contenido distinto; inmutable, no se sobrescribe"
            ) from exc
        return True

    # ------------------------------------------------------------------ API
    def put(self, key: str, data: bytes) -> ObjectRef:
        sha = sha256_bytes(data)
        path = self._path(key)
        ref = ObjectRef(key=key, sha256=sha, size=len(data))
        if os.path.lexists(path) and self._existing_matches(path, key, sha):
            return ref

        tmp = self._staging / f"{uuid.uuid4().hex}.tmp"
        fd = _fs.open_exclusive(tmp)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                self._checkpoint("before_fsync")
                _fs.fsync_fd(fh.fileno())
            _fs.make_readonly(tmp)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._path(key)  # revalida que ningún componente se volvió enlace
            self._checkpoint("before_publish")
            try:
                _fs.publish_no_replace(tmp, path)
            except FileExistsError:
                self._existing_matches(path, key, sha)
                return ref
            _fs.fsync_dir(path.parent)
            self._checkpoint("before_verify")
            self._read_verified(path, key, sha)
            return ref
        finally:
            _fs.remove_file(tmp)

    def get(self, key: str, expected_sha256: str) -> bytes:
        return self._read_verified(self._path(key), key, expected_sha256)

    def get_self_verifying(self, key: str, verifier: Callable[[bytes], None]) -> bytes:
        """Lee un documento autosellado (manifiesto). ``verifier`` debe lanzar
        ``IntegrityError`` si el sello interno no coincide. Detecta corrupción
        accidental; la protección frente a manipulación deliberada corresponde a E1-C.
        """
        path = self._path(key)
        if not _fs.is_regular_file(path):
            if _fs.is_link_like(path):
                raise IntegrityError(f"{key}: el objeto es un enlace, no un archivo regular")
            raise FileNotFoundError(key)
        fd = _fs.open_read_nofollow(path)
        with os.fdopen(fd, "rb") as fh:
            data = fh.read()
        verifier(data)
        return data

    def verify(self, key: str, expected_sha256: str) -> None:
        self.get(key, expected_sha256)

    def exists(self, key: str) -> bool:
        return os.path.lexists(self._path(key))

    def iter_keys(self, prefix: str = "") -> Iterator[str]:
        """Enumera claves de objetos (orden determinista)."""
        base = self._objects
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            rel_dir = Path(dirpath).relative_to(base).as_posix()
            for name in sorted(filenames):
                key = name if rel_dir == "." else f"{rel_dir}/{name}"
                if key.startswith(prefix):
                    yield key

    def staging_entries(self) -> list[str]:
        """Restos de staging (p. ej., tras una caída). Nunca son objetos publicados."""
        return sorted(p.name for p in self._staging.iterdir())
