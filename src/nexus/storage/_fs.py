"""Primitivas de sistema de archivos dependientes de la plataforma.

Este es el único módulo de NEXUS con llamadas específicas de SO. Garantías:

========================  ============================  ==================================
Garantía                  POSIX (Linux)                 Windows / NTFS
========================  ============================  ==================================
Publicación sin           ``os.link``: falla con        ``os.rename`` → ``MoveFileExW``
reemplazo                 ``EEXIST`` si el destino      sin ``REPLACE_EXISTING``: falla si
                          existe. Demostrado en         el destino existe. Pendiente de
                          pruebas.                      certificar.
Durabilidad del archivo   ``os.fsync``                  ``os.fsync`` → ``FlushFileBuffers``
Durabilidad del           ``fsync`` del directorio      No disponible. Pendiente de
directorio                                              certificar (``WRITE_THROUGH``)
Solo lectura              ``chmod 0444`` (disuade)      Atributo read-only (disuade)
========================  ============================  ==================================

Nunca se usa ``os.replace`` ni ``shutil.move``: ambos pueden sustituir el destino.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

from nexus.errors import UnsupportedFilesystemError

IS_WINDOWS = os.name == "nt"

_LINK_UNSUPPORTED = {
    errno.EPERM,
    errno.EXDEV,
    errno.EMLINK,
    getattr(errno, "ENOTSUP", -1),
    getattr(errno, "EOPNOTSUPP", -1),
    errno.ENOSYS,
}

_O_BINARY = getattr(os, "O_BINARY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def open_exclusive(path: Path) -> int:
    """Crea un archivo nuevo; falla si ya existe (no sigue enlaces)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY | _O_NOFOLLOW
    return os.open(path, flags, 0o600)


def open_read_nofollow(path: Path) -> int:
    return os.open(path, os.O_RDONLY | _O_BINARY | _O_NOFOLLOW)


def fsync_fd(fd: int) -> None:
    os.fsync(fd)


def fsync_dir(path: Path) -> bool:
    """Sincroniza la entrada de directorio. Devuelve False si la plataforma no lo permite."""
    if IS_WINDOWS:
        return False
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def is_link_like(path: Path) -> bool:
    """True si ``path`` es enlace simbólico o punto de reanálisis (junction en Windows)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)


def is_regular_file(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISREG(st.st_mode) and not is_link_like(path)


def make_readonly(path: Path) -> None:
    os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def make_writable(path: Path) -> None:
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def publish_no_replace(src: Path, dst: Path) -> None:
    """Publica ``src`` en ``dst`` de forma atómica y SIN reemplazar nunca ``dst``.

    Lanza ``FileExistsError`` si ``dst`` existe. En POSIX, ``src`` sigue existiendo
    tras el enlace y el llamador debe eliminarlo. En Windows se mueve.
    """
    if IS_WINDOWS:
        # MoveFileExW sin MOVEFILE_REPLACE_EXISTING: atómico en el mismo volumen
        # y falla con FileExistsError si el destino existe.
        os.rename(src, dst)
        return
    try:
        os.link(src, dst, follow_symlinks=False)
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno in _LINK_UNSUPPORTED:
            raise UnsupportedFilesystemError(
                f"el sistema de archivos no admite enlaces duros ({exc.strerror}); "
                "no se degrada a un rename que podría reemplazar el destino"
            ) from exc
        raise


def remove_file(path: Path) -> None:
    """Elimina un archivo propio (p. ej., temporal de staging). Tolera que no exista."""
    try:
        if IS_WINDOWS:
            make_writable(path)
        os.unlink(path)
    except FileNotFoundError:
        pass
