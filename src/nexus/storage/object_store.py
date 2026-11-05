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

Resistencia TOCTOU (NX-E1A-02): ninguna operación resuelve la ruta completa por nombre.
Los directorios se abren componente a componente sin seguir enlaces y las operaciones
se realizan relativas al directorio abierto (ver ``_fs``). Así, sustituir un directorio
intermedio por un enlace después de la validación no puede llevar una escritura ni una
lectura fuera del almacén.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
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
            _fs.ensure_private_dir(directory)

    # ------------------------------------------------------------------ internos
    def _checkpoint(self, name: str) -> None:
        if self._fault is not None:
            self._fault(name)

    def _parent(
        self, key: str, *, create: bool
    ) -> tuple[AbstractContextManager[_fs.DirHandle], str]:
        segments = validate_key(key)
        return _fs.open_dir_chain(self._objects, segments[:-1], create=create), segments[-1]

    def _read_in(self, parent: _fs.DirHandle, name: str, key: str) -> bytes:
        st = _fs.entry_stat(parent, name)
        if st is None:
            raise FileNotFoundError(key)
        if _fs.stat_is_link(st):
            raise IntegrityError(f"{key}: el objeto es un enlace, no un archivo regular")
        if not _fs.stat_is_regular(st):
            raise FileNotFoundError(key)
        self._checkpoint("before_open_read")
        return _fs.read_entry(parent, name, st)

    def _read_verified_in(
        self, parent: _fs.DirHandle, name: str, key: str, expected_sha256: str
    ) -> bytes:
        data = self._read_in(parent, name, key)
        actual = sha256_bytes(data)
        if actual != expected_sha256:
            raise IntegrityError(
                f"{key}: hash no coincide (esperado={expected_sha256[:12]}…, actual={actual[:12]}…)"
            )
        return data

    def _existing_matches(self, parent: _fs.DirHandle, name: str, key: str, sha: str) -> None:
        """Vuelve sin error si el destino existente es idéntico; si no, conflicto."""
        st = _fs.entry_stat(parent, name)
        if st is None:  # pragma: no cover - carrera imposible: nunca se borra un objeto
            raise ImmutableConflictError(f"{key}: el destino desapareció durante la operación")
        if _fs.stat_is_link(st):
            raise ImmutableConflictError(f"{key}: el destino es un enlace; no se toca")
        if not _fs.stat_is_regular(st):
            raise ImmutableConflictError(f"{key}: el destino existe y no es un archivo regular")
        try:
            self._read_verified_in(parent, name, key, sha)
        except IntegrityError as exc:
            raise ImmutableConflictError(
                f"{key}: ya existe con contenido distinto; inmutable, no se sobrescribe"
            ) from exc

    def _ensure_contained(
        self,
        parent: _fs.DirHandle,
        key: str,
        name: str,
        created: tuple[int, int] | None,
    ) -> None:
        """NX-E1A-03: no declarar éxito si el directorio fue relocalizado fuera del almacén.

        Es un control de DETECCIÓN con carrera residual (relocalización posterior a esta
        comprobación). La prevención es el aislamiento por permisos (``ensure_private_dir``).
        Si se perdió la frontera, se retira el enlace que creó este ``put`` (solo si su
        identidad coincide) y se falla cerrado.
        """
        segments = validate_key(key)
        if _fs.same_directory(parent, self._objects, segments[:-1]):
            self._checkpoint("after_containment_check")
            return
        removed = created is not None and _fs.remove_entry_if_same(parent, name, created)
        raise IntegrityError(
            f"{key}: frontera de almacenamiento perdida (directorio relocalizado durante la "
            f"operación); no se declara éxito; enlace propio retirado={removed}"
        )

    # ------------------------------------------------------------------ API
    def put(self, key: str, data: bytes) -> ObjectRef:
        sha = sha256_bytes(data)
        ref = ObjectRef(key=key, sha256=sha, size=len(data))
        chain, name = self._parent(key, create=True)
        with chain as parent, _fs.open_dir_chain(self._staging, (), create=False) as staging:
            if _fs.entry_stat(parent, name) is not None:
                self._existing_matches(parent, name, key, sha)
                self._ensure_contained(parent, key, name, None)
                return ref
            tmp_name = f"{uuid.uuid4().hex}.tmp"
            fd = _fs.create_exclusive(staging, tmp_name)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    self._checkpoint("before_fsync")
                    _fs.fsync_fd(fh.fileno())
                    _fs.make_readonly_open(fh.fileno(), staging, tmp_name)
                    written = os.fstat(fh.fileno())
                created = (written.st_dev, written.st_ino)
                self._checkpoint("before_publish")
                try:
                    _fs.publish_no_replace(staging, tmp_name, parent, name)
                except FileExistsError:
                    self._existing_matches(parent, name, key, sha)
                    self._ensure_contained(parent, key, name, None)
                    return ref
                _fs.fsync_dir(parent)
                self._checkpoint("before_verify")
                self._read_verified_in(parent, name, key, sha)
                self._ensure_contained(parent, key, name, created)
                return ref
            finally:
                _fs.remove_own_temp(staging, tmp_name)

    def get(self, key: str, expected_sha256: str) -> bytes:
        chain, name = self._parent(key, create=False)
        try:
            with chain as parent:
                return self._read_verified_in(parent, name, key, expected_sha256)
        except FileNotFoundError as exc:
            raise FileNotFoundError(key) from exc

    def get_self_verifying(self, key: str, verifier: Callable[[bytes], None]) -> bytes:
        """Lee un documento autosellado (manifiesto). ``verifier`` debe lanzar
        ``IntegrityError`` si el sello interno no coincide. Detecta corrupción
        accidental; la protección frente a manipulación deliberada corresponde a E1-C.
        """
        chain, name = self._parent(key, create=False)
        try:
            with chain as parent:
                data = self._read_in(parent, name, key)
        except FileNotFoundError as exc:
            raise FileNotFoundError(key) from exc
        verifier(data)
        return data

    def verify(self, key: str, expected_sha256: str) -> None:
        self.get(key, expected_sha256)

    def exists(self, key: str) -> bool:
        chain, name = self._parent(key, create=False)
        try:
            with chain as parent:
                return _fs.entry_stat(parent, name) is not None
        except FileNotFoundError:
            return False

    def iter_keys(self, prefix: str = "") -> Iterator[str]:
        """Enumera claves de objetos (orden determinista; no sigue enlaces)."""
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
