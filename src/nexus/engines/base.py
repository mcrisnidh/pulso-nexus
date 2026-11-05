"""Protocolo de motores: funciones puras sobre contratos normalizados.

Un motor:
- declara ``consumes`` y ``produces`` (``contract@version``) y su ``version``;
- recibe ``as_of`` y cualquier semilla como parámetro: no lee el reloj, el
  entorno, la red ni archivos;
- debe ser determinista: las mismas entradas producen las mismas salidas.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Engine(Protocol):
    name: str
    version: str
    consumes: tuple[str, ...]
    produces: tuple[str, ...]

    def run(
        self,
        inputs: Mapping[str, Any],
        params: Mapping[str, str],
        as_of: str,
    ) -> dict[str, Any]: ...
