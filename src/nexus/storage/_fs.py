"""Primitivas de sistema de archivos dependientes de la plataforma (único módulo con E/S de SO).

Modelo de amenaza (NX-E1A-02): un actor local puede sustituir un directorio intermedio
del almacén por un enlace (symlink o junction) entre la validación de la ruta y la
operación de E/S (TOCTOU). Las operaciones NUNCA resuelven rutas completas por nombre:

POSIX (demostrado en pruebas):
    Se recorre la ruta con descriptores de directorio: ``openat(O_DIRECTORY|O_NOFOLLOW)``
    componente a componente desde la raíz ``objects/`` o ``staging/``. La publicación usa
    ``linkat(src_dir_fd, dst_dir_fd)`` sin seguir enlaces y la lectura ``openat(O_NOFOLLOW)``
    relativa al descriptor del padre. Renombrar un directorio después de abrirlo no
    redirige la operación: el descriptor sigue apuntando al inodo original, que está
    dentro del almacén.

Windows/NTFS (diseñado; se ejecuta en el job de CI de Windows, sin certificar):
    No existe ``dir_fd``. Cada directorio de la ruta se abre con ``CreateFileW`` usando
    ``FILE_FLAG_OPEN_REPARSE_POINT`` y SIN ``FILE_SHARE_DELETE``. Mientras dura la
    operación, nadie puede renombrar ni borrar esos directorios (violación de
    compartición), y los puntos de reanálisis (junctions o symlinks) se rechazan.

Ambas plataformas:
    - Nunca se usa ``os.replace`` ni ``shutil.move``.
    - La lectura compara ``(st_dev, st_ino)`` antes y después de abrir.
    - Los ancestros de la raíz del almacén se consideran de confianza (administración).

Durabilidad: ``fsync`` del archivo en ambas plataformas; ``fsync`` del directorio solo en
POSIX. Nada de esto certifica resistencia a cortes eléctricos.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from nexus.errors import IntegrityError, UnsupportedFilesystemError

IS_WINDOWS = sys.platform == "win32"

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
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_READ_ONLY = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH


@dataclass(frozen=True)
class DirHandle:
    """Directorio abierto de forma segura. ``fd`` es válido solo en POSIX."""

    path: Path
    fd: int


# --------------------------------------------------------------------------- Windows
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _K32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _K32.CreateFileW
    _CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _CreateFileW.restype = wintypes.HANDLE
    _CloseHandle = _K32.CloseHandle
    _CloseHandle.argtypes = (wintypes.HANDLE,)
    _CloseHandle.restype = wintypes.BOOL

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = (
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        )

    _GetFileInformationByHandle = _K32.GetFileInformationByHandle
    _GetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )
    _GetFileInformationByHandle.restype = wintypes.BOOL

    _INVALID_HANDLE = ctypes.c_void_p(-1).value
    # FILE_LIST_DIRECTORY participa en la comprobación de compartición (READ_ATTRIBUTES
    # por sí solo no), así que el handle sí bloquea a quien pida DELETE para renombrar.
    _DIR_ACCESS = 0x0001 | 0x0080  # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES
    _SHARE_NO_DELETE = 0x1 | 0x2  # FILE_SHARE_READ | FILE_SHARE_WRITE (sin DELETE)
    _OPEN_EXISTING = 3
    _DIR_FLAGS = 0x02000000 | 0x00200000  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
    _ATTR_DIRECTORY = 0x10

    def _win_lock_dir(path: Path) -> int:
        handle = _CreateFileW(
            str(path), _DIR_ACCESS, _SHARE_NO_DELETE, None, _OPEN_EXISTING, _DIR_FLAGS, None
        )
        if handle is None or handle == _INVALID_HANDLE:
            code = ctypes.get_last_error()
            if code in (2, 3):  # ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND
                raise FileNotFoundError(errno.ENOENT, "no existe", str(path))
            raise ctypes.WinError(code)
        info = _ByHandleFileInformation()
        if not _GetFileInformationByHandle(handle, ctypes.byref(info)):
            code = ctypes.get_last_error()
            _CloseHandle(handle)
            raise ctypes.WinError(code)
        attrs = int(info.dwFileAttributes)
        if attrs & _FILE_ATTRIBUTE_REPARSE_POINT or not attrs & _ATTR_DIRECTORY:
            _CloseHandle(handle)
            raise IntegrityError(f"componente de ruta es un enlace o no es directorio: {path}")
        return int(handle)

    def _win_remove_own_temp(path: Path) -> None:
        """En Windows un archivo con atributo de solo lectura no se puede borrar
        (``WinError 5``). Se quita el atributo SOLO si el archivo no comparte datos con
        un objeto publicado (``st_nlink == 1``): nunca se desprotege un objeto publicado."""
        try:
            os.unlink(path)
        except FileNotFoundError:
            return
        except PermissionError:
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                return
            if st.st_nlink != 1:
                raise
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            os.unlink(path)

    @contextmanager
    def _win_dir_chain(base: Path, segments: Sequence[str], create: bool) -> Iterator[DirHandle]:
        handles: list[int] = []
        try:
            path = base
            handles.append(_win_lock_dir(path))
            for seg in segments:
                path = path / seg
                if create:
                    with suppress(FileExistsError):
                        os.mkdir(path)
                handles.append(_win_lock_dir(path))
            yield DirHandle(path=path, fd=-1)
        finally:
            for handle in reversed(handles):
                _CloseHandle(handle)


# --------------------------------------------------------------------------- POSIX
def _open_dir_nofollow(name: str, dir_fd: int | None, shown: Path) -> int:
    try:
        return os.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=dir_fd)
    except NotADirectoryError as exc:
        # Linux devuelve ENOTDIR (no ELOOP) cuando O_DIRECTORY|O_NOFOLLOW encuentra un symlink.
        kind = "no es un directorio"
        with suppress(OSError):
            if stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode):
                kind = "es un enlace"
        raise IntegrityError(f"componente de ruta {kind}: {shown}") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise IntegrityError(f"componente de ruta es un enlace: {shown}") from exc
        raise


@contextmanager
def open_dir_chain(base: Path, segments: Sequence[str], *, create: bool) -> Iterator[DirHandle]:
    """Abre ``base/segments...`` sin seguir enlaces y sin resolver la ruta por nombre."""
    if sys.platform == "win32":
        with _win_dir_chain(base, segments, create) as handle:
            yield handle
        return
    fd = _open_dir_nofollow(str(base), None, base)
    try:
        path = base
        for seg in segments:
            path = path / seg
            if create:
                with suppress(FileExistsError):
                    os.mkdir(seg, 0o755, dir_fd=fd)
            child = _open_dir_nofollow(seg, fd, path)
            os.close(fd)
            fd = child
        yield DirHandle(path=path, fd=fd)
    finally:
        os.close(fd)


def entry_stat(parent: DirHandle, name: str) -> os.stat_result | None:
    """``lstat`` de una entrada del directorio abierto; ``None`` si no existe."""
    try:
        if sys.platform == "win32":
            return os.lstat(parent.path / name)
        return os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def stat_is_link(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def stat_is_regular(st: os.stat_result) -> bool:
    return stat.S_ISREG(st.st_mode) and not stat_is_link(st)


def create_exclusive(parent: DirHandle, name: str) -> int:
    """Crea un archivo nuevo (falla si existe; no sigue enlaces)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY | _O_NOFOLLOW
    if sys.platform == "win32":
        return os.open(parent.path / name, flags, 0o600)
    return os.open(name, flags, 0o600, dir_fd=parent.fd)


def make_readonly_open(fd: int, parent: DirHandle, name: str) -> None:
    """Marca como solo lectura un archivo que el llamador tiene abierto (disuasorio)."""
    if sys.platform == "win32":
        os.chmod(parent.path / name, _READ_ONLY)
        return
    os.fchmod(fd, _READ_ONLY)


def read_entry(parent: DirHandle, name: str, expected: os.stat_result) -> bytes:
    """Lee una entrada regular verificando que es el MISMO objeto que ``expected``."""
    flags = os.O_RDONLY | _O_BINARY | _O_NOFOLLOW
    if sys.platform == "win32":
        fd = os.open(parent.path / name, flags)
    else:
        fd = os.open(name, flags, dir_fd=parent.fd)
    with os.fdopen(fd, "rb") as fh:
        opened = os.fstat(fh.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            expected.st_dev,
            expected.st_ino,
        ):
            raise IntegrityError(f"el objeto cambió entre la comprobación y la apertura: {name}")
        return fh.read()


def publish_no_replace(staging: DirHandle, tmp_name: str, parent: DirHandle, name: str) -> None:
    """Publica ``staging/tmp_name`` como ``parent/name`` sin reemplazar NUNCA el destino.

    Lanza ``FileExistsError`` si el destino existe. En POSIX el temporal sigue existiendo
    (enlace duro) y el llamador lo elimina; en Windows se mueve.
    """
    if sys.platform == "win32":
        # MoveFileExW sin MOVEFILE_REPLACE_EXISTING: falla si el destino existe. Los
        # directorios de origen y destino están bloqueados contra renombrado.
        os.rename(staging.path / tmp_name, parent.path / name)
        return
    try:
        os.link(
            tmp_name,
            name,
            src_dir_fd=staging.fd,
            dst_dir_fd=parent.fd,
            follow_symlinks=False,
        )
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno in _LINK_UNSUPPORTED:
            raise UnsupportedFilesystemError(
                f"el sistema de archivos no admite enlaces duros ({exc.strerror}); "
                "no se degrada a un rename que podría reemplazar el destino"
            ) from exc
        raise


def fsync_dir(parent: DirHandle) -> bool:
    """Sincroniza la entrada de directorio. ``False`` si la plataforma no lo permite."""
    if sys.platform == "win32":
        return False
    os.fsync(parent.fd)
    return True


def fsync_fd(fd: int) -> None:
    os.fsync(fd)


def remove_own_temp(staging: DirHandle, name: str) -> None:
    """Elimina un temporal PROPIO de staging. Tolera que no exista."""
    if sys.platform == "win32":
        _win_remove_own_temp(staging.path / name)
        return
    with suppress(FileNotFoundError):
        os.unlink(name, dir_fd=staging.fd)


def is_link_like(path: Path) -> bool:
    """True si ``path`` es symlink o punto de reanálisis. Uso: raíces del almacén."""
    try:
        return stat_is_link(os.lstat(path))
    except FileNotFoundError:
        return False
