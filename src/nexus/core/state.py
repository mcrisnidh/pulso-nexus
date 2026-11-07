"""Estado durable de CORE sobre el ObjectStore de E1-A (Hito A).

Toda escritura usa ``LocalObjectStore.put``: staging → fsync → enlace SIN reemplazo → relectura
verificada. Dos escritores de la misma clave con distinto contenido: uno gana y el otro recibe
``ImmutableConflictError`` (garantía demostrada en E1-A sobre POSIX). Sobre esa primitiva:

- Frozen mensual: ``frozen/<canal>/<mes>/<fuente>.json``; la primera escritura gana para siempre.
- Generación: ``gen/<canal>/<gen_id>/…``; ``gen_id`` determinista por entradas (idempotente).
- Publicación: registro encadenado ``pub/<canal>/<seq>.json``; el puntero es el último registro.
  Publicar es CAS: se escribe ``seq = esperado + 1``; si ya existe, conflicto.
- Eventos: ``events/<canal>/<instante>-<id>.json`` (historial de Refresh, fallos y rollback).
- C2 (noviembre 2026): el canal ``replay`` vive en un almacén FÍSICO distinto
  (``DATA/replay/store``, con su propio ``DATA/replay/cs``). Cada ``CoreState`` declara los canales
  de su almacén y rechaza cualquier clave de otro canal: un replay no puede escribir ni leer
  Frozen, generaciones, publicaciones ni eventos operativos, y viceversa.

Pendiente (Hito B): la misma semántica sobre SQL Server aislado. No se presenta esta
implementación como certificación de garantías SQL.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexus.errors import ImmutableConflictError
from nexus.hashing import sha256_bytes
from nexus.sealed import seal, unseal, verifier
from nexus.storage.object_store import LocalObjectStore

CHANNELS = ("operativo", "replay", "sintetico")
MAIN_LANE = ("operativo", "sintetico")
REPLAY_LANE = ("replay",)
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class FrozenExistsError(Exception):
    pass


class PublicationConflictError(Exception):
    pass


class PublicationRefusedError(Exception):
    pass


def check_channel(channel: str) -> str:
    if channel not in CHANNELS:
        raise ValueError(f"canal no soportado: {channel!r}")
    return channel


def check_month(month: str) -> str:
    if not _MONTH.match(month):
        raise ValueError(f"mes inválido: {month!r}")
    return month


def lane_of(channel: str) -> tuple[str, ...]:
    return REPLAY_LANE if check_channel(channel) == "replay" else MAIN_LANE


def store_root(data_dir: Path, channel: str) -> Path:
    """Directorio físico del almacén de un canal (C2: ``replay`` separado)."""
    return data_dir / "replay" / "store" if lane_of(channel) == REPLAY_LANE else data_dir / "store"


def cs_root(data_dir: Path, channel: str) -> Path:
    """Directorio físico de las notas CS de un canal (C2: ``replay`` separado)."""
    return data_dir / "replay" / "cs" if lane_of(channel) == REPLAY_LANE else data_dir / "cs"


def key_part(source_id: str) -> str:
    return source_id.replace("_", "-")


def item_key(item: str) -> str:
    return sha256_bytes(item.encode("utf-8"))[:32]


@dataclass
class CoreState:
    store: LocalObjectStore
    lanes: tuple[str, ...] = MAIN_LANE

    @classmethod
    def for_channel(cls, data_dir: Path, channel: str) -> CoreState:
        return cls(LocalObjectStore(store_root(data_dir, channel)), lane_of(channel))

    def _ch(self, channel: str) -> str:
        """C2: un almacén solo admite los canales de su carril físico."""
        if check_channel(channel) not in self.lanes:
            raise ValueError(
                f"el canal {channel!r} no pertenece a este almacén (canales: {list(self.lanes)})"
            )
        return channel

    # -------------------------------------------------------------- documentos sellados
    def put_sealed(self, key: str, document: dict[str, Any]) -> str:
        return self.store.put(key, seal(document)).sha256

    def get_sealed(self, key: str) -> dict[str, Any]:
        return unseal(self.store.get_self_verifying(key, verifier(key)), key)

    # -------------------------------------------------------------- fuentes
    def put_source(self, source_id: str, content: bytes) -> str:
        sha = sha256_bytes(content)
        self.store.put(f"sources/{key_part(source_id)}/{sha}.csv", content)
        return sha

    def get_source(self, source_id: str, sha: str) -> bytes:
        return self.store.get(f"sources/{key_part(source_id)}/{sha}.csv", sha)

    # -------------------------------------------------------------- C1 Frozen
    def frozen_key(self, channel: str, month: str, source_id: str) -> str:
        return f"frozen/{self._ch(channel)}/{check_month(month)}/{key_part(source_id)}.json"

    def get_frozen(self, channel: str, month: str, source_id: str) -> dict[str, Any] | None:
        key = self.frozen_key(channel, month, source_id)
        if not self.store.exists(key):
            return None
        return self.get_sealed(key)

    def register_frozen(
        self, channel: str, month: str, source_id: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        """Registra el Frozen; si ya existe (aunque sea por una carrera), lanza
        ``FrozenExistsError`` y el registrado permanece intacto."""
        key = self.frozen_key(channel, month, source_id)
        if self.store.exists(key):
            raise FrozenExistsError(key)
        try:
            self.put_sealed(key, record)
        except ImmutableConflictError as exc:
            raise FrozenExistsError(key) from exc
        return self.get_sealed(key)

    def list_frozen(self, channel: str) -> list[dict[str, Any]]:
        return [
            self.get_sealed(k) for k in sorted(self.store.iter_keys(f"frozen/{self._ch(channel)}/"))
        ]

    # -------------------------------------------------------------- generaciones
    def gen_prefix(self, channel: str, gen_id: str) -> str:
        return f"gen/{self._ch(channel)}/{gen_id}"

    def has_generation(self, channel: str, gen_id: str) -> bool:
        return self.store.exists(f"{self.gen_prefix(channel, gen_id)}/manifest.json")

    def get_manifest(self, channel: str, gen_id: str) -> dict[str, Any]:
        return self.get_sealed(f"{self.gen_prefix(channel, gen_id)}/manifest.json")

    def get_artifact(self, key: str, sha: str) -> dict[str, Any]:
        """Artefacto de una generación verificado contra el SHA-256 de su manifiesto."""
        data = self.store.get(key, sha)
        return unseal(data, key)

    # -------------------------------------------------------------- publicación (CAS)
    def _pub_keys(self, channel: str) -> list[str]:
        return sorted(self.store.iter_keys(f"pub/{self._ch(channel)}/"))

    def publication_head(self, channel: str) -> dict[str, Any] | None:
        keys = self._pub_keys(channel)
        return self.get_sealed(keys[-1]) if keys else None

    def publication_history(self, channel: str) -> list[dict[str, Any]]:
        return [self.get_sealed(k) for k in self._pub_keys(channel)]

    def append_publication(
        self, channel: str, expected_seq: int, record: dict[str, Any]
    ) -> dict[str, Any]:
        self._ch(channel)
        head = self.publication_head(channel)
        head_seq = int(head["seq"]) if head else 0
        if head_seq != expected_seq:
            raise PublicationConflictError(f"versión esperada {expected_seq}, vigente {head_seq}")
        prev_hash = None
        if head is not None:
            prev_key = f"pub/{channel}/{head_seq:08d}.json"
            prev_hash = sha256_bytes(self.store.get_self_verifying(prev_key, verifier(prev_key)))
        seq = expected_seq + 1
        doc = {
            **record,
            "seq": seq,
            "channel": channel,
            "prev_seq": head_seq or None,
            "prev_hash": prev_hash,
        }
        try:
            self.put_sealed(f"pub/{channel}/{seq:08d}.json", doc)
        except ImmutableConflictError as exc:
            raise PublicationConflictError(f"otra publicación ganó la versión {seq}") from exc
        return doc

    def verify_publication_chain(self, channel: str) -> list[str]:
        self._ch(channel)
        problems = []
        prev_hash = None
        for i, key in enumerate(self._pub_keys(channel), start=1):
            raw = self.store.get_self_verifying(key, verifier(key))
            doc = unseal(raw, key)
            if doc["seq"] != i or doc["prev_hash"] != prev_hash:
                problems.append(f"cadena rota en {key}")
            prev_hash = sha256_bytes(raw)
        return problems

    # -------------------------------------------------------------- B0-7 vínculo de zona horaria
    def tz_binding_key(self, channel: str) -> str:
        return f"calendar/{self._ch(channel)}/binding.json"

    def get_tz_binding(self, channel: str) -> dict[str, Any] | None:
        key = self.tz_binding_key(channel)
        return self.get_sealed(key) if self.store.exists(key) else None

    def has_history(self, channel: str) -> bool:
        """¿Existe algún Frozen o publicación en el canal?"""
        self._ch(channel)
        return any(True for _ in self.store.iter_keys(f"frozen/{channel}/")) or any(
            True for _ in self.store.iter_keys(f"pub/{channel}/")
        )

    def effective_tz(self, channel: str) -> str | None:
        """Zona a la que está ligado el estado del canal: la del vínculo registrado; UTC si hay
        historial previo a B0-7 (sin vínculo); ``None`` si el canal no tiene estado."""
        binding = self.get_tz_binding(channel)
        if binding is not None:
            return str(binding["business_tz"])
        return "UTC" if self.has_history(channel) else None

    def bind_tz(self, channel: str, record: dict[str, Any]) -> dict[str, Any]:
        """Registra el vínculo una sola vez (enlace sin reemplazo); si ya existe, devuelve el
        registrado sin modificarlo."""
        key = self.tz_binding_key(channel)
        if not self.store.exists(key):
            with contextlib.suppress(ImmutableConflictError):  # otro proceso ganó la carrera
                self.put_sealed(key, record)
        return self.get_sealed(key)

    # -------------------------------------------------------------- eventos
    def append_event(self, channel: str, server_ts: str, kind: str, detail: dict[str, Any]) -> None:
        stamp = re.sub(r"[^0-9]", "", server_ts)
        key = f"events/{self._ch(channel)}/{stamp}-{uuid.uuid4().hex[:12]}.json"
        self.put_sealed(key, {"server_ts": server_ts, "kind": kind, "detail": detail})

    def events(self, channel: str) -> list[dict[str, Any]]:
        return [
            self.get_sealed(k) for k in sorted(self.store.iter_keys(f"events/{self._ch(channel)}/"))
        ]

    def alerts(self, channel: str, month: str) -> list[dict[str, Any]]:
        prefix = f"alerts/{self._ch(channel)}/{month}/"
        return [self.get_sealed(k) for k in sorted(self.store.iter_keys(prefix))]

    def put_alert(self, channel: str, month: str, alert: dict[str, Any]) -> None:
        from nexus.hashing import digest_json  # noqa: PLC0415

        key = f"alerts/{self._ch(channel)}/{month}/{digest_json(alert)[:32]}.json"
        if not self.store.exists(key):
            with contextlib.suppress(ImmutableConflictError):  # contenido idéntico por construcción
                self.put_sealed(key, alert)
