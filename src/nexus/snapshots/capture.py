"""Snapshots inmutables: bytes exactos de una captura más un manifiesto autosellado.

Estructura en el almacén (rutas con propietario, sin deduplicación global):

    snapshots/<snapshot_id>/content.bin
    snapshots/<snapshot_id>/manifest.json

El manifiesto no contiene rutas absolutas ni secretos: solo metadatos que el
conector declara no sensibles (p. ej., el nombre del archivo).
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nexus.connectors.base import CaptureResult
from nexus.errors import IntegrityError, NotReproducibleError
from nexus.hashing import sha256_bytes
from nexus.sealed import seal, unseal, verifier
from nexus.storage.object_store import LocalObjectStore

SCHEMA = "nexus.snapshot/1"
_SNAPSHOT_ID = re.compile(r"^snap-[0-9a-f]{32}$")
Clock = Callable[[], dt.datetime]


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _keys(snapshot_id: str) -> tuple[str, str]:
    if not _SNAPSHOT_ID.match(snapshot_id):
        raise NotReproducibleError(f"identificador de snapshot inválido: {snapshot_id!r}")
    base = f"snapshots/{snapshot_id}"
    return f"{base}/content.bin", f"{base}/manifest.json"


@dataclass(frozen=True)
class Snapshot:
    manifest: dict[str, Any]
    content: bytes

    @property
    def snapshot_id(self) -> str:
        return str(self.manifest["snapshot_id"])

    @property
    def content_sha256(self) -> str:
        return str(self.manifest["content_sha256"])


def create_snapshot(
    store: LocalObjectStore, capture: CaptureResult, clock: Clock = _utcnow
) -> dict[str, Any]:
    """Persiste la captura como snapshot inmutable y devuelve su manifiesto."""
    snapshot_id = f"snap-{uuid.uuid4().hex}"
    content_key, manifest_key = _keys(snapshot_id)
    ref = store.put(content_key, capture.content)
    if ref.sha256 != sha256_bytes(capture.content):  # pragma: no cover - defensivo
        raise IntegrityError("hash de captura inconsistente")
    captured_at = clock().astimezone(dt.UTC).replace(microsecond=0)
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "snapshot_id": snapshot_id,
        "source_id": capture.source_id,
        "connector": capture.connector,
        "connector_version": capture.connector_version,
        "media_type": capture.media_type,
        "consistency_level": capture.consistency_level,
        "content_key": content_key,
        "content_sha256": ref.sha256,
        "size": ref.size,
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "source_metadata": dict(sorted(capture.source_metadata.items())),
    }
    store.put(manifest_key, seal(manifest))
    return manifest


def load_snapshot(store: LocalObjectStore, snapshot_id: str, expected_sha256: str) -> Snapshot:
    """Carga y verifica un snapshot.

    - Si no existe: ``NotReproducibleError``.
    - Si el manifiesto o el contenido están alterados: ``IntegrityError``.
    """
    content_key, manifest_key = _keys(snapshot_id)
    try:
        raw_manifest = store.get_self_verifying(manifest_key, verifier(manifest_key))
    except FileNotFoundError as exc:
        raise NotReproducibleError(f"snapshot ausente: {snapshot_id}") from exc
    manifest = unseal(raw_manifest, manifest_key)
    if manifest.get("schema") != SCHEMA or manifest.get("snapshot_id") != snapshot_id:
        raise IntegrityError(f"{manifest_key}: manifiesto no corresponde al snapshot")
    if manifest.get("content_sha256") != expected_sha256:
        raise IntegrityError(
            f"{snapshot_id}: el hash del manifiesto no coincide con el esperado por la ejecución"
        )
    try:
        content = store.get(content_key, expected_sha256)
    except FileNotFoundError as exc:
        raise NotReproducibleError(f"contenido de snapshot ausente: {snapshot_id}") from exc
    return Snapshot(manifest=manifest, content=content)


def read_manifest(store: LocalObjectStore, snapshot_id: str) -> dict[str, Any]:
    """Lee el manifiesto verificando su sello (sin conocer de antemano el hash)."""
    _, manifest_key = _keys(snapshot_id)
    try:
        raw = store.get_self_verifying(manifest_key, verifier(manifest_key))
    except FileNotFoundError as exc:
        raise NotReproducibleError(f"snapshot ausente: {snapshot_id}") from exc
    return unseal(raw, manifest_key)
