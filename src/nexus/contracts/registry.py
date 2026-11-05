"""Registro de contratos con huellas fijadas.

``fingerprints.json`` fija la huella estructural de cada ``id@version``. Si se
cambia un esquema sin subir la versión, la huella deja de coincidir y la
verificación falla. Esto impide cambios silenciosos de contrato.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from importlib import resources

from nexus.contracts import synthetic
from nexus.contracts.model import Contract
from nexus.errors import ContractError


def load_pinned_fingerprints() -> dict[str, str]:
    text = resources.files("nexus.contracts").joinpath("fingerprints.json").read_text("utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ContractError("fingerprints.json inválido")
    return {str(k): str(v) for k, v in data.items()}


class ContractRegistry:
    def __init__(self, contracts: Iterable[Contract], pinned: Mapping[str, str]) -> None:
        self._contracts: dict[str, Contract] = {}
        for contract in contracts:
            if contract.ref in self._contracts:
                raise ContractError(f"contrato duplicado {contract.ref}")
            self._contracts[contract.ref] = contract
        self._pinned = dict(pinned)

    def get(self, ref: str) -> Contract:
        try:
            return self._contracts[ref]
        except KeyError as exc:
            raise ContractError(f"contrato no registrado: {ref}") from exc

    def refs(self) -> list[str]:
        return sorted(self._contracts)

    def verify(self) -> None:
        """Falla si algún contrato no está fijado o su huella cambió sin nueva versión."""
        for ref, contract in sorted(self._contracts.items()):
            pinned = self._pinned.get(ref)
            if pinned is None:
                raise ContractError(f"{ref}: huella no fijada en fingerprints.json")
            actual = contract.fingerprint()
            if actual != pinned:
                raise ContractError(
                    f"{ref}: el esquema cambió sin subir la versión "
                    f"(fijada={pinned[:12]}…, actual={actual[:12]}…)"
                )


def default_registry() -> ContractRegistry:
    registry = ContractRegistry(synthetic.ALL, load_pinned_fingerprints())
    registry.verify()
    return registry
