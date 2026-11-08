"""Credenciales desde el Administrador de credenciales de Windows (Credential Manager).

La credencial se crea UNA vez en ASMXC1200, sin pasar por NEXUS:

    cmdkey /generic:PULSO_NEXUS/fg /user:remcb /pass          (pide la contraseña sin mostrarla)
    cmdkey /generic:PULSO_NEXUS/embarques /user:sm0003 /pass

NEXUS solo LEE la credencial en memoria al conectar. ``Secreto`` nunca se imprime ni se serializa.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

from . import ConectorError


class Secreto:
    """Contenedor que no revela su valor en repr/str/format ni se puede serializar."""

    __slots__ = ("_v",)

    def __init__(self, value: str) -> None:
        self._v = value

    def revelar(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return "Secreto(***)"

    __str__ = __repr__

    def __format__(self, _spec: str) -> str:
        return "***"

    def __reduce__(self) -> object:
        raise TypeError("un Secreto no se serializa")


@dataclass(frozen=True)
class Credencial:
    destino: str
    usuario: str
    secreto: Secreto


Proveedor = Callable[[str], Credencial]


def credman_windows(destino: str) -> Credencial:
    """Lee una credencial GENÉRICA con ``CredReadW`` (advapi32), solo en Windows."""
    if sys.platform != "win32":
        raise ConectorError("CREDENCIAL_SOLO_WINDOWS", "el Administrador de credenciales requiere Windows")
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    pcred = ctypes.POINTER(CREDENTIALW)()
    if not advapi.CredReadW(destino, 1, 0, ctypes.byref(pcred)):  # CRED_TYPE_GENERIC = 1
        raise ConectorError("CREDENCIAL_NO_ENCONTRADA", f"no existe la credencial genérica «{destino}»")
    try:
        c = pcred.contents
        blob = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
        secret = blob.decode("utf-16-le") if c.CredentialBlobSize else ""
        user = c.UserName or ""
    finally:
        advapi.CredFree(pcred)
    if not secret:
        raise ConectorError("CREDENCIAL_VACIA", f"la credencial «{destino}» no tiene contraseña")
    return Credencial(destino, user, Secreto(secret))


def obtener(destino: str, cuenta_esperada: str, proveedor: Proveedor | None = None) -> Credencial:
    """Credencial de UN conector; rechaza si la cuenta no es la esperada (no hay intercambio)."""
    if not destino or not cuenta_esperada:
        raise ConectorError("CREDENCIAL_NO_CONFIGURADA", "destino y cuenta esperada son obligatorios")
    cred = (proveedor or credman_windows)(destino)
    if cred.usuario.split("\\")[-1].lower() != cuenta_esperada.lower():
        raise ConectorError(
            "CUENTA_NO_COINCIDE",
            f"la credencial «{destino}» pertenece a otra cuenta; se esperaba «{cuenta_esperada}»",
        )
    return cred


def usuario_windows() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def sanear(texto: str, *secretos: Secreto | None) -> str:
    """Quita cualquier aparición de un secreto de un mensaje de error del controlador."""
    out = str(texto)
    for s in secretos:
        if s is not None and s.revelar():
            out = out.replace(s.revelar(), "***")
    for clave in ("PWD=", "Password=", "pwd="):
        if clave in out:
            out = out.split(clave)[0] + clave + "***"
    return out[:400]
