"""C7 — Notas y seguimiento de Customer Service (append-only, independiente de las generaciones).

Almacenamiento PROPIO (un ``LocalObjectStore`` distinto del de generaciones): Refresh,
publicación y rollback no lo reciben ni lo importan. Cada cambio es una versión inmutable
``cs/threads/<hilo>/<versión>.json``; la versión N+1 se escribe con enlace sin reemplazo, por lo
que dos editores concurrentes sobre la misma versión producen exactamente un ganador y un
``CsConflictError`` (concurrencia optimista; nada se pierde ni se sobrescribe).

Tipos: NOTE (texto, no mensual), CS_STATUS / ADVANCE / DELIVERY_DATE (mensuales: vigentes solo en
su ``valid_month``; al cambiar de mes los anteriores se muestran como referencia), RETRACT (retira
una versión anterior sin borrarla). Los campos CS son seguimiento: no alteran la cobertura.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from nexus.errors import ImmutableConflictError
from nexus.hashing import digest_json, sha256_bytes
from nexus.planning.model import jsonable
from nexus.sealed import seal, unseal, verifier
from nexus.storage.object_store import LocalObjectStore

KINDS = ("NOTE", "CS_STATUS", "ADVANCE", "DELIVERY_DATE", "RETRACT")
MONTHLY = ("CS_STATUS", "ADVANCE", "DELIVERY_DATE")
MAX_TEXT = 2000
ORIGIN = "MANUAL_CS"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class CsConflictError(Exception):
    """Otro usuario guardó una versión antes; se debe recargar y reintentar."""


class CsValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Association:
    item: str
    customer_key: str = ""  # exacta (con espacios significativos)
    order: str = ""
    line: str = ""

    def __post_init__(self) -> None:
        if not self.item or not self.item.strip():
            raise CsValidationError("ITEM obligatorio")
        if self.line and not self.order:
            raise CsValidationError("una línea requiere su pedido")
        for value in (self.item, self.customer_key, self.order, self.line):
            if len(value) > 100 or _CONTROL.search(value):
                raise CsValidationError("asociación inválida")

    @property
    def thread_id(self) -> str:
        return digest_json([self.item, self.customer_key, self.order, self.line])[:32]

    def as_dict(self) -> dict[str, str]:
        return {
            "item": self.item,
            "customer_key": self.customer_key,
            "order": self.order,
            "line": self.line,
        }


def _validate_value(kind: str, raw: Any, vocabulary: tuple[str, ...]) -> Any:
    if kind not in KINDS:
        raise CsValidationError(f"tipo desconocido: {kind}")
    if kind == "NOTE":
        text = str(raw or "").replace("\r\n", "\n")
        if not text.strip() or len(text) > MAX_TEXT or _CONTROL.search(text):
            raise CsValidationError(f"nota vacía, con control o de más de {MAX_TEXT} caracteres")
        return text
    if kind == "CS_STATUS":
        if raw not in vocabulary:
            raise CsValidationError(f"estatus fuera del vocabulario: {raw!r}")
        return raw
    if kind == "DELIVERY_DATE":
        try:
            return dt.date.fromisoformat(str(raw)).isoformat()
        except ValueError as exc:
            raise CsValidationError("fecha de entrega inválida (AAAA-MM-DD)") from exc
    if kind == "ADVANCE":
        data = raw if isinstance(raw, dict) else {"qty": raw}
        try:
            qty = Decimal(str(data.get("qty", "")).strip())
        except InvalidOperation as exc:
            raise CsValidationError("adelanto: cantidad inválida") from exc
        if not qty.is_finite() or qty < 0:
            raise CsValidationError("adelanto: cantidad inválida")
        date = str(data.get("date") or "").strip()
        if date:
            try:
                date = dt.date.fromisoformat(date).isoformat()
            except ValueError as exc:
                raise CsValidationError("adelanto: fecha inválida") from exc
        return {"qty": jsonable(qty), "date": date or None}
    if not isinstance(raw, int) or raw < 1:  # RETRACT
        raise CsValidationError("retiro: versión objetivo inválida")
    return raw


class CsStore:
    def __init__(self, store: LocalObjectStore, vocabulary: tuple[str, ...]) -> None:
        self.store = store
        self.vocabulary = vocabulary

    # ---------------------------------------------------------------- lectura
    def _versions(self, thread_id: str) -> list[dict[str, Any]]:
        keys = sorted(self.store.iter_keys(f"cs/threads/{thread_id}/"))
        return [unseal(self.store.get_self_verifying(k, verifier(k)), k) for k in keys]

    def head_version(self, thread_id: str) -> int:
        return len(list(self.store.iter_keys(f"cs/threads/{thread_id}/")))

    def threads_for_item(self, item: str) -> list[dict[str, Any]]:
        prefix = f"cs/items/{sha256_bytes(item.encode('utf-8'))[:32]}/"
        out = []
        for key in sorted(self.store.iter_keys(prefix)):
            assoc = unseal(self.store.get_self_verifying(key, verifier(key)), key)
            out.append({**assoc, "versions": self._versions(assoc["thread_id"])})
        return out

    def history(self, thread_id: str) -> list[dict[str, Any]]:
        return self._versions(thread_id)

    # ---------------------------------------------------------------- escritura
    def append(
        self,
        assoc: Association,
        kind: str,
        value: Any,
        *,
        author: str,
        expected_version: int,
        server_ts: str,
        month: str,
        reason: str = "",
        published_gen: str | None = None,
    ) -> dict[str, Any]:
        """Añade una versión. ``author`` y ``server_ts`` los pone el servidor, nunca el cliente."""
        if not author:
            raise CsValidationError("autor obligatorio (identidad autenticada)")
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise CsValidationError("mes inválido")
        clean = _validate_value(kind, value, self.vocabulary)
        thread_id = assoc.thread_id
        current = self.head_version(thread_id)
        if current != expected_version:
            raise CsConflictError(f"versión esperada {expected_version}, vigente {current}")
        if kind == "RETRACT":
            targets = {v["version"]: v for v in self._versions(thread_id)}
            if clean not in targets or targets[clean]["kind"] == "RETRACT":
                raise CsValidationError("retiro: la versión objetivo no existe o es un retiro")
        version = expected_version + 1
        doc = {
            "schema": "nexus.core.cs/1",
            "thread_id": thread_id,
            **assoc.as_dict(),
            "version": version,
            "kind": kind,
            "value": clean,
            "valid_month": month,
            "author": author,
            "server_ts": server_ts,
            "reason": reason[:200],
            "origin": ORIGIN,
            "published_generation": published_gen,
        }
        index_key = f"cs/items/{sha256_bytes(assoc.item.encode('utf-8'))[:32]}/{thread_id}.json"
        if not self.store.exists(index_key):
            with contextlib.suppress(ImmutableConflictError):  # contenido idéntico por construcción
                self.store.put(index_key, seal({"thread_id": thread_id, **assoc.as_dict()}))
        try:
            self.store.put(f"cs/threads/{thread_id}/{version:06d}.json", seal(doc))
        except ImmutableConflictError as exc:
            raise CsConflictError(f"otro usuario guardó la versión {version}") from exc
        return doc


def board_state(versions: list[dict[str, Any]], month: str) -> dict[str, Any]:
    """Estado vigente de un hilo para ``month``: último valor de cada campo mensual del mes, el
    último del mes anterior como referencia ("requiere reconfirmar") y la última nota."""
    retracted = {v["value"] for v in versions if v["kind"] == "RETRACT"}
    live = [v for v in versions if v["kind"] != "RETRACT" and v["version"] not in retracted]
    state: dict[str, Any] = {
        "current": {},
        "reference": {},
        "last_note": None,
        "versions": len(versions),
    }
    for kind in MONTHLY:
        this_month = [v for v in live if v["kind"] == kind and v["valid_month"] == month]
        earlier = [v for v in live if v["kind"] == kind and v["valid_month"] < month]
        if this_month:
            state["current"][kind] = this_month[-1]
        if earlier:
            state["reference"][kind] = {**earlier[-1], "note": "requiere reconfirmar"}
    notes = [v for v in live if v["kind"] == "NOTE"]
    if notes:
        state["last_note"] = notes[-1]
    return state
