"""v0.3.2 — salida UTF-8 estable en la consola de Windows.

En Windows, Python escribe en la consola con la página de códigos activa (cp437/cp850/cp1252).
Los informes contienen caracteres fuera de esas páginas («→», «≠», «—», acentos en rutas): sin
esta corrección la salida se corrompe o el proceso termina con ``UnicodeEncodeError`` DESPUÉS de
haber hecho el trabajo, y el lanzador recibe un código de salida engañoso.
"""

from __future__ import annotations

import contextlib
import sys


def utf8_consola() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if reconf is None:
            continue
        with contextlib.suppress(ValueError, OSError):  # flujo cerrado o no reconfigurable: se deja
            reconf(encoding="utf-8", errors="replace")
