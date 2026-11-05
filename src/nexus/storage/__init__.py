"""Almacenamiento de objetos inmutables."""

from nexus.storage.object_store import LocalObjectStore, ObjectRef, validate_key

__all__ = ["LocalObjectStore", "ObjectRef", "validate_key"]
