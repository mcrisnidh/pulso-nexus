"""Modelo de contratos de datos versionados."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from nexus.errors import ContractError
from nexus.hashing import digest_json, logical_hash


@dataclass(frozen=True)
class Field:
    """Columna de un contrato.

    ``tolerance`` es la tolerancia absoluta permitida en la equivalencia lógica (L1).
    Solo se admite en columnas de coma flotante. ``None`` exige igualdad exacta.
    """

    name: str
    type: Any
    nullable: bool = False
    tolerance: float | None = None
    description: str = ""


@dataclass(frozen=True)
class Contract:
    id: str
    version: str
    fields: tuple[Field, ...]
    key: tuple[str, ...]
    description: str = ""
    _names: tuple[str, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        names = tuple(f.name for f in self.fields)
        object.__setattr__(self, "_names", names)
        if len(set(names)) != len(names):
            raise ContractError(f"{self.ref}: columnas duplicadas")
        if not self.key or any(k not in names for k in self.key):
            raise ContractError(f"{self.ref}: clave inválida {self.key}")
        for f in self.fields:
            if f.tolerance is not None and not pa.types.is_floating(f.type):
                raise ContractError(f"{self.ref}: tolerancia solo en coma flotante ({f.name})")
            if f.tolerance is not None and f.tolerance < 0:
                raise ContractError(f"{self.ref}: tolerancia negativa ({f.name})")
            if f.name in self.key and f.nullable:
                raise ContractError(f"{self.ref}: la clave no puede ser anulable ({f.name})")

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def column_names(self) -> tuple[str, ...]:
        return self._names

    def field(self, name: str) -> Field:
        for f in self.fields:
            if f.name == name:
                return f
        raise ContractError(f"{self.ref}: columna desconocida {name}")

    def schema(self) -> Any:
        return pa.schema([pa.field(f.name, f.type, nullable=f.nullable) for f in self.fields])

    def fingerprint(self) -> str:
        """Huella estructural. Cambia si cambia cualquier aspecto del esquema o de la clave."""
        return digest_json(
            {
                "id": self.id,
                "fields": [[f.name, str(f.type), f.nullable, f.tolerance] for f in self.fields],
                "key": list(self.key),
            }
        )

    def validate(self, table: Any) -> None:
        """Verifica tipos, orden de columnas, nulabilidad y unicidad de la clave."""
        expected = self.schema()
        if table.schema.remove_metadata() != expected:
            raise ContractError(
                f"{self.ref}: esquema distinto.\n esperado={expected}\n recibido={table.schema}"
            )
        for f in self.fields:
            if not f.nullable and table.column(f.name).null_count:
                raise ContractError(f"{self.ref}: nulos en columna no anulable {f.name}")
        if table.num_rows:
            keys = table.select(list(self.key))
            distinct = keys.group_by(list(self.key)).aggregate([]).num_rows
            if distinct != table.num_rows:
                raise ContractError(f"{self.ref}: clave duplicada {self.key}")

    def canonicalize(self, table: Any) -> Any:
        """Selecciona columnas en el orden del contrato, ordena por clave y valida."""
        missing = [n for n in self.column_names if n not in table.column_names]
        if missing:
            raise ContractError(f"{self.ref}: columnas ausentes {missing}")
        projected = table.select(list(self.column_names)).replace_schema_metadata(None)
        try:
            projected = projected.cast(self.schema())
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError, ValueError, TypeError) as exc:
            raise ContractError(f"{self.ref}: tipos incompatibles: {exc}") from exc
        if projected.num_rows:
            order = pc.sort_indices(projected, sort_keys=[(k, "ascending") for k in self.key])
            projected = projected.take(order)
        projected = projected.combine_chunks()
        self.validate(projected)
        return projected

    def logical_hash(self, table: Any) -> str:
        return logical_hash(table, self.column_names, self.ref)
