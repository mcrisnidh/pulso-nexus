"""C1/C2/C5 — Refresh: captura, Frozen, validación, cálculo, generación y publicación atómica.

Flujo de una solicitud (worker E1-B sin cambios de semántica: cola idempotente, claim, lease por
ámbito ``core:<canal>:<mes>``, fencing, sellado de una generación por solicitud):

1. Captura las fuentes de la bandeja (``inbox/<fuente>/*.csv``) como contenido inmutable.
2. Frozen del mes: si no existe, registra el PRIMER extracto válido por instante de extracción
   (29 OERAS y Portal). Si existe, lo reutiliza verificando su hash; nunca lo recalcula. Un extracto
   válido anterior al registrado que llega tarde genera la alerta FROZEN_PRECEDENCE_CONFLICT.
3. FG (último corte válido) y embarques con EL MISMO corte; si no coinciden, no se publica.
4. Cálculo puro (C4) + invariantes. Generación determinista (mismas fuentes ⇒ mismo gen_id).
5. Publicación CAS solo si la validación es PASS. Si algo falla, el puntero no se mueve.

Este módulo NO conoce las notas de Customer Service (C7): ni las lee ni las escribe.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus.core.coverage import ENGINE_VERSION, CoverageInput, check_invariants, compute, serialize
from nexus.core.rules import CoreRules, load_core_rules
from nexus.core.sources import SOURCE_IDS, ParsedSource, read_source
from nexus.core.state import (
    CoreState,
    FrozenExistsError,
    PublicationConflictError,
    PublicationRefusedError,
    check_channel,
    item_key,
)
from nexus.core.timebase import SAFE_PROCEDURE, UTC_NAME, BusinessCalendar, TimezoneBindingError
from nexus.errors import ConfigurationError
from nexus.hashing import digest_json, sha256_bytes
from nexus.metadata.memory import InMemoryMetadata
from nexus.metadata.profile import Profile
from nexus.pipeline.worker import StageHook, work_once
from nexus.sealed import seal
from nexus.storage.object_store import LocalObjectStore

Clock = Callable[[], dt.datetime]
SYNTHETIC_SYSTEMS = frozenset({"SYNTHETIC"})
FROZEN_SOURCES = ("oeras_29", "portal_cat")
SCHEMA = "nexus.core.generation/1"

PROFILE = Profile(
    name="core-local",
    kind="LOCAL",
    schema_version=1,
    host_allowlist=(),
    host_denylist=(),
    database_pattern="",
    database_denylist=(),
    server_name_pattern="",
    allowed_editions=(),
    lease_ms=60_000,
    heartbeat_ms=5_000,
    claim_ms=60_000,
    backoff_ms=50,
    max_attempts=3,
    query_timeout_s=30,
)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Selection:
    frozen: dict[str, dict[str, Any]] = field(default_factory=dict)
    current: dict[str, ParsedSource] = field(default_factory=dict)
    fg: ParsedSource | None = None
    shipments: ParsedSource | None = None
    rejected: list[dict[str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)


class CoreJob:
    """Ejecutor CORE para ``work_once``. Puro respecto de CS: no recibe el almacén de notas."""

    def __init__(
        self,
        state: CoreState,
        inbox: Path,
        channel: str,
        rules: CoreRules,
        clock: Clock,
        hook: StageHook | None = None,
        calendar: BusinessCalendar | None = None,
    ) -> None:
        self.state = state
        self.inbox = inbox
        self.channel = check_channel(channel)
        self.rules = rules
        self.clock = clock
        self.hook = hook
        self.calendar = calendar or BusinessCalendar(UTC_NAME)

    def _stage(self, name: str) -> None:
        if self.hook is not None:
            self.hook(name)

    # ---------------------------------------------------------------- captura y selección
    def _parsed(self, source_id: str, month: str, now: str) -> list[ParsedSource]:
        folder = self.inbox / source_id
        out = []
        for path in sorted(folder.glob("*.csv")) if folder.is_dir() else []:
            parsed = read_source(source_id, path)
            if parsed.instant and self.calendar.month_of(parsed.instant) != month:
                continue  # otro mes: no participa en este corte (sigue en la bandeja)
            if parsed.valid and parsed.instant and parsed.instant > now:
                parsed.valid = False
                parsed.errors.append("instante posterior al reloj del servidor")
            if parsed.valid:
                self.state.put_source(source_id, path.read_bytes())
            out.append(parsed)
        return out

    def select(self, month: str, actor: str) -> Selection:
        now = iso(self.clock())
        sel = Selection()
        for source_id in SOURCE_IDS:
            parsed = self._parsed(source_id, month, now)
            sel.rejected += [p.describe() for p in parsed if not p.valid]
            valid = sorted(
                (p for p in parsed if p.valid), key=lambda p: (p.instant or "", p.sha256 or "")
            )
            if source_id in FROZEN_SOURCES:
                self._frozen(source_id, month, valid, sel, actor, now)
                if valid:
                    sel.current[source_id] = valid[-1]
            elif source_id == "fg_inventory":
                sel.fg = valid[-1] if valid else None
            else:
                sel.shipments = None
                if sel.fg is not None:
                    same_cut = [p for p in valid if p.instant == sel.fg.instant]
                    sel.shipments = same_cut[-1] if same_cut else None
                if sel.shipments is None:
                    sel.failures.append(
                        "TEMPORAL_MISMATCH/BLOCKED_SOURCE: no hay embarques físicos válidos con el "
                        f"mismo corte que FG ({sel.fg.instant if sel.fg else 'sin FG'})"
                    )
        if sel.fg is None:
            sel.failures.append("BLOCKED_SOURCE: no hay inventario FG válido")
        return sel

    def _frozen(
        self,
        source_id: str,
        month: str,
        valid: list[ParsedSource],
        sel: Selection,
        actor: str,
        now: str,
    ) -> None:
        record = self.state.get_frozen(self.channel, month, source_id)
        if record is None and valid:
            first = valid[0]
            try:
                record = self.state.register_frozen(
                    self.channel,
                    month,
                    source_id,
                    {
                        "schema": "nexus.core.frozen/1",
                        "channel": self.channel,
                        "month": month,
                        "source_id": source_id,
                        "file": first.file_name,
                        "sha256": first.sha256,
                        "instant": first.instant,
                        "source_system": first.source_system,
                        "records": len(first.records),
                        "rules_version": self.rules.version,
                        "registered_by": actor,
                        "registered_at": now,
                        **({} if self.calendar.is_utc else {"business_tz": self.calendar.name}),
                    },
                )
                self.state.append_event(
                    self.channel,
                    now,
                    "FROZEN_REGISTERED",
                    {
                        "month": month,
                        "source_id": source_id,
                        "sha256": first.sha256,
                        "instant": first.instant,
                    },
                )
            except FrozenExistsError:
                record = self.state.get_frozen(self.channel, month, source_id)
        if record is None:
            sel.failures.append(f"BLOCKED_SOURCE: sin Frozen {source_id} válido para {month}")
            return
        if record.get("business_tz", UTC_NAME) != self.calendar.name:
            sel.failures.append(
                f"ZONA HORARIA: el Frozen {source_id} de {month} se registró con "
                f"{record.get('business_tz', UTC_NAME)}; la configuración actual es "
                f"{self.calendar.name}. No se reinterpreta."
            )
            return
        for p in valid:
            if p.instant and p.instant < record["instant"] and p.sha256 != record["sha256"]:
                alert = {
                    "kind": "FROZEN_PRECEDENCE_CONFLICT",
                    "month": month,
                    "source_id": source_id,
                    "frozen_instant": record["instant"],
                    "earlier_file": p.file_name,
                    "earlier_instant": p.instant,
                    "earlier_sha256": p.sha256,
                    "action": "ninguna automática: requiere procedimiento con doble "
                    "aprobación; el Frozen registrado no se reemplaza",
                }
                self.state.put_alert(self.channel, month, alert)
                sel.alerts.append(alert)
        content = self.state.get_source(source_id, record["sha256"])  # verifica hash
        with tempfile.TemporaryDirectory(prefix="nexus-frozen-") as tmpdir:
            tmp = Path(tmpdir) / record["file"]
            tmp.write_bytes(content)
            parsed = read_source(source_id, tmp)
        if not parsed.valid or parsed.sha256 != record["sha256"]:
            sel.failures.append(f"INTEGRIDAD: el Frozen {source_id} registrado no se puede releer")
            return
        sel.frozen[source_id] = {"record": record, "parsed": parsed}

    # ---------------------------------------------------------------- ejecución
    def __call__(self, store: LocalObjectStore, payload: dict[str, Any]) -> dict[str, Any]:
        month, actor = str(payload["month"]), str(payload["actor"])
        ensure_tz_binding(self.state, self.channel, self.calendar, month, iso(self.clock()), actor)
        sel = self.select(month, actor)
        self._stage("after_capture")
        used = [sel.frozen[s]["parsed"] for s in FROZEN_SOURCES if s in sel.frozen]
        used += [p for p in (sel.fg, sel.shipments) if p is not None]
        systems = sorted({str(p.source_system) for p in used})
        failures = list(sel.failures)
        if self.channel == "operativo" and SYNTHETIC_SYSTEMS & set(systems):
            failures.append("el canal operativo no admite fuentes sintéticas")
        result: dict[str, Any] | None = None
        violations: list[str] = []
        if not failures:
            assert sel.fg is not None and sel.shipments is not None  # noqa: S101
            certified = all(
                p.source_id in self.rules.certified_sources
                and p.source_system not in SYNTHETIC_SYSTEMS
                for p in used
            )
            inp = CoverageInput(
                month=month,
                cut_ts=str(sel.fg.instant),
                oeras=sel.frozen["oeras_29"]["parsed"].records,
                portal=sel.frozen["portal_cat"]["parsed"].records,
                fg=sel.fg.records,
                shipments=sel.shipments.records,
                rules=self.rules,
                certified=certified,
                window_start_utc=None
                if self.calendar.is_utc
                else self.calendar.window_utc(month)[0],
            )
            try:
                result = compute(inp)
                violations = check_invariants(result, sel.shipments.records)
            except ValueError as exc:
                failures.append(f"cálculo rechazado: {exc}")
        self._stage("after_compute")
        inputs = {
            "engine": ENGINE_VERSION,
            "channel": self.channel,
            "month": month,
            "rules_sha256": self.rules.raw_sha256,
            "frozen": {s: sel.frozen[s]["record"]["sha256"] for s in sel.frozen},
            "fg": sel.fg.sha256 if sel.fg else None,
            "shipments": sel.shipments.sha256 if sel.shipments else None,
            "current": {s: p.sha256 for s, p in sorted(sel.current.items())},
            "rejected": sorted(str(r["sha256"]) for r in sel.rejected),
        }
        if not self.calendar.is_utc:  # B0-7: zona y límites UTC efectivos entran en la identidad
            inputs["calendar"] = self.calendar.fingerprint(month)
        gen_id = digest_json(inputs)[:32]
        validation = {
            "status": "PASS" if not failures and not violations else "FAIL",
            "failures": failures,
            "invariant_violations": violations,
        }
        artifacts = self._write_generation(gen_id, month, inputs, sel, result, validation, systems)
        self._stage("after_generation")
        return {
            "run_id": gen_id,
            "input_digest": digest_json(inputs),
            "result_digest": digest_json(serialize(result)) if result else "0" * 64,
            "artifacts": artifacts,
        }

    def _current_evolution(self, sel: Selection) -> dict[str, Any]:
        out: dict[str, Any] = {}
        frozen = sel.frozen.get("oeras_29")
        current = sel.current.get("oeras_29")
        if frozen is None or current is None:
            return out
        base = {r.identity: r for r in frozen["parsed"].records}
        cur = {r.identity: r for r in current.records}
        for item in sorted({k[0] for k in base} | {k[0] for k in cur}):
            b = {k: v for k, v in base.items() if k[0] == item}
            c = {k: v for k, v in cur.items() if k[0] == item}
            out[item] = {
                "current_file": current.file_name,
                "current_instant": current.instant,
                "added": len(c.keys() - b.keys()),
                "removed": len(b.keys() - c.keys()),
                "changed": sum(
                    1
                    for k in b.keys() & c.keys()
                    if (b[k].qty_open, b[k].due_date) != (c[k].qty_open, c[k].due_date)
                ),
            }
        return out

    def _write_generation(
        self,
        gen_id: str,
        month: str,
        inputs: dict[str, Any],
        sel: Selection,
        result: dict[str, Any] | None,
        validation: dict[str, Any],
        systems: list[str],
    ) -> list[dict[str, Any]]:
        prefix = self.state.gen_prefix(self.channel, gen_id)
        artifacts: list[dict[str, Any]] = []

        def put(contract: str, key: str, doc: dict[str, Any]) -> None:
            data = seal(doc)
            ref = self.state.store.put(key, data)
            artifacts.append(
                {
                    "contract": contract,
                    "key": key,
                    "content_sha256": ref.sha256,
                    "logical_hash": sha256_bytes(data),
                    "size": ref.size,
                }
            )

        evolution = self._current_evolution(sel)
        sources = {
            "frozen": {
                s: {
                    k: v
                    for k, v in sel.frozen[s]["record"].items()
                    if k not in ("registered_at", "registered_by")
                }
                for s in sel.frozen
            },
            "fg": sel.fg.describe() if sel.fg else None,
            "shipments": sel.shipments.describe() if sel.shipments else None,
            "current": {s: p.describe() for s, p in sel.current.items()},
            "rejected": sel.rejected,
            "alerts": sel.alerts,
            "source_systems": systems,
        }
        item_index: dict[str, Any] = {}
        if result is not None:
            for item, data in sorted(result["items"].items()):
                doc = {
                    "schema": SCHEMA,
                    "item": item,
                    "month": month,
                    "coverage": serialize(data),
                    "current_vs_frozen": evolution.get(item),
                }
                key = f"{prefix}/items/{item_key(item)}.json"
                put(f"core.item.{item_key(item)}", key, doc)
                item_index[item] = {"key": key, "sha256": artifacts[-1]["content_sha256"]}
            put(
                "core.summary",
                f"{prefix}/summary.json",
                {
                    "schema": SCHEMA,
                    "month": month,
                    "cut_ts": result["cut_ts"],
                    "rules": result["rules"],
                    "totals": serialize(result["totals"]),
                    "rows": {
                        item: {"facts": serialize(d["facts"]), "kpi": d["kpi"]}
                        for item, d in sorted(result["items"].items())
                    },
                    "attribution": serialize(result["attribution"]),
                    "future_lines": serialize(result["future_lines"]),
                },
            )
        manifest = {
            "schema": SCHEMA,
            "gen_id": gen_id,
            "channel": self.channel,
            "month": month,
            "inputs": inputs,
            "validation": validation,
            "sources": serialize(sources),
            "artifacts": {
                a["contract"]: {"key": a["key"], "sha256": a["content_sha256"]} for a in artifacts
            },
            "items": item_index,
        }
        key = f"{prefix}/manifest.json"
        if not self.state.store.exists(key):
            put("core.manifest", key, manifest)
        else:
            data = self.state.store.get_self_verifying(key, lambda _b: None)
            artifacts.append(
                {
                    "contract": "core.manifest",
                    "key": key,
                    "content_sha256": sha256_bytes(data),
                    "logical_hash": sha256_bytes(data),
                    "size": len(data),
                }
            )
        return artifacts


OPERATION_SCHEMA = "nexus.core.operation/1"


def load_operation(data_dir: Path) -> BusinessCalendar:
    """Configuración operativa (``DATA/operation.json``). Sin archivo: UTC (comportamiento previo
    a B0-7, idéntico byte a byte)."""
    path = data_dir / "operation.json"
    if not path.exists():
        return BusinessCalendar(UTC_NAME)
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"operation.json ilegible: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != OPERATION_SCHEMA:
        raise ConfigurationError(f"operation.json: esquema distinto de {OPERATION_SCHEMA}")
    unknown = set(data) - {"schema", "business_tz"}
    if unknown:
        raise ConfigurationError(f"operation.json: claves no admitidas {sorted(unknown)}")
    return BusinessCalendar(str(data.get("business_tz", UTC_NAME)))


def check_tz_binding(state: CoreState, channel: str, calendar: BusinessCalendar) -> None:
    """Rechaza una zona distinta de la que ya liga el historial del canal (fallo cerrado)."""
    bound = state.effective_tz(channel)
    if bound is not None and bound != calendar.name:
        raise TimezoneBindingError(
            f"el canal {channel} está ligado a la zona {bound} (Frozen o publicaciones "
            f"existentes); la configuración actual es {calendar.name}. {SAFE_PROCEDURE}"
        )


def ensure_tz_binding(
    state: CoreState, channel: str, calendar: BusinessCalendar, month: str, now: str, actor: str
) -> None:
    """Antes de crear estado: verifica el vínculo y, si el canal aún no tiene estado, registra la
    zona (una sola vez, enlace sin reemplazo). Un almacén previo a B0-7 con historial no recibe
    ningún archivo: queda ligado implícitamente a UTC. La verificación final es SIEMPRE: si otro
    proceso registró otra zona en paralelo, este se detiene antes de crear estado."""
    check_tz_binding(state, channel, calendar)
    if state.get_tz_binding(channel) is None and not state.has_history(channel):
        state.bind_tz(
            channel,
            {
                "schema": "nexus.core.tz-binding/1",
                "channel": channel,
                "business_tz": calendar.name,
                "first_month": month,
                "bound_at": now,
                "bound_by": actor,
            },
        )
    check_tz_binding(state, channel, calendar)  # carrera: otro proceso pudo ganar


class RefreshService:
    """Refresh de un clic. Una instancia por canal y almacén; segura para hilos."""

    def __init__(
        self,
        data_dir: Path,
        channel: str,
        rules_path: Path,
        clock: Clock = utcnow,
        hook: StageHook | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.channel = check_channel(channel)
        self.rules = load_core_rules(rules_path)
        self.clock = clock
        self.hook = hook
        self.state = CoreState(LocalObjectStore(data_dir / "store"))
        self.inbox = data_dir / "inbox"
        # B0-7: calendario operativo; una zona distinta de la que liga el historial se rechaza
        # al iniciar (la web y la CLI no arrancan).
        self.calendar = load_operation(data_dir)
        check_tz_binding(self.state, self.channel, self.calendar)
        self.repo = InMemoryMetadata()
        self.job = CoreJob(
            self.state, self.inbox, self.channel, self.rules, clock, hook, self.calendar
        )
        self._run_lock = threading.Lock()

    def current_month(self) -> str:
        """Mes operativo vigente según el calendario de la planta (UTC sin operation.json)."""
        return self.calendar.month_of(self.clock())

    def inbox_digest(self) -> str:
        listing = []
        for source_id in SOURCE_IDS:
            folder = self.inbox / source_id
            for path in sorted(folder.glob("*.csv")) if folder.is_dir() else []:
                listing.append([source_id, path.name, sha256_bytes(path.read_bytes())])
        return digest_json({"listing": listing, "rules": self.rules.raw_sha256})

    def request(self, actor: str, month: str | None = None, nonce: str = "") -> dict[str, Any]:
        month = month or self.current_month()
        if self.channel == "operativo" and month != self.current_month():
            raise PublicationRefusedError("el canal operativo solo admite el mes vigente")
        tz = "" if self.calendar.is_utc else f":tz={self.calendar.name}"
        key = f"core:{self.channel}:{month}:{self.inbox_digest()}{tz}{':' + nonce if nonce else ''}"
        rid, created = self.repo.enqueue(
            key,
            f"core:{self.channel}:{month}",
            "manual",
            {"month": month, "actor": actor},
            PROFILE.max_attempts,
        )
        if created:
            self.state.append_event(
                self.channel,
                iso(self.clock()),
                "REFRESH_REQUESTED",
                {"request_id": rid, "month": month, "actor": actor, "idempotency_key": key},
            )
        return {
            "request_id": rid,
            "key": key,
            "created": created,
            "state": (self.repo.get_request(key) or {}).get("state"),
        }

    def run_pending(self, worker: str = "core-worker") -> list[dict[str, Any]]:
        """Procesa la cola hasta vaciarla. Un único proceso de cálculo a la vez por servicio;
        entre procesos, el lease por ámbito y la publicación CAS excluyen efectos duplicados."""
        results = []
        with self._run_lock:
            for _ in range(50):
                outcome = work_once(
                    self.state.store,
                    self.repo,
                    self.repo,
                    PROFILE,
                    worker,
                    hook=self.hook,
                    executor=self.job,
                )
                if outcome.status == "IDLE":
                    break
                results.append(self._after_work(outcome))
        return results

    def refresh(self, actor: str, month: str | None = None) -> dict[str, Any]:
        """Atajo síncrono: solicitar + procesar."""
        req = self.request(actor, month)
        done = self.run_pending()
        return {"request": req, "outcomes": done, "head": self.state.publication_head(self.channel)}

    def _gen_for_request(self, request_id: int) -> str | None:
        for g in self.repo.list_generations():
            if g["request_id"] == request_id and g["sealed_at"]:
                return str(g["store_run_id"])
        return None

    def _after_work(self, outcome: Any) -> dict[str, Any]:
        now = iso(self.clock())
        info: dict[str, Any] = {
            "status": outcome.status,
            "request_id": outcome.request_id,
            "detail": outcome.detail,
        }
        if outcome.status != "COMPLETED":
            self.state.append_event(self.channel, now, "REFRESH_ATTEMPT_FAILED", info)
            return info
        gen_id = self._gen_for_request(int(outcome.request_id))
        info["gen_id"] = gen_id
        if gen_id is None:
            return info
        manifest = self.state.get_manifest(self.channel, gen_id)
        if manifest["validation"]["status"] != "PASS":
            info["published"] = False
            info["validation"] = manifest["validation"]
            self.state.append_event(
                self.channel,
                now,
                "REFRESH_NOT_PUBLISHED",
                {"gen_id": gen_id, "validation": manifest["validation"]},
            )
            return info
        if self.hook is not None:
            self.hook("before_publish")
        try:
            pub = self.publish(gen_id, actor=f"refresh:{outcome.request_id}", action="PUBLISH")
            info["published"] = True
            info["seq"] = pub["seq"]
        except (PublicationConflictError, PublicationRefusedError) as exc:
            info["published"] = False
            info["detail"] = str(exc)
            self.state.append_event(
                self.channel, now, "PUBLISH_REFUSED", {"gen_id": gen_id, "reason": str(exc)}
            )
        return info

    def publish(self, gen_id: str, actor: str, action: str = "PUBLISH") -> dict[str, Any]:
        """Publicación atómica (CAS). Rechaza generaciones no válidas o de otro canal o mes."""
        if not self.state.has_generation(self.channel, gen_id):
            raise PublicationRefusedError(f"generación inexistente: {gen_id}")
        manifest = self.state.get_manifest(self.channel, gen_id)
        if manifest["validation"]["status"] != "PASS":
            raise PublicationRefusedError("la generación no pasó la validación")
        if manifest["channel"] != self.channel:
            raise PublicationRefusedError("la generación pertenece a otro canal")
        check_tz_binding(self.state, self.channel, self.calendar)
        gen_tz = manifest["inputs"].get("calendar", {}).get("business_tz", UTC_NAME)
        if gen_tz != self.calendar.name:
            raise PublicationRefusedError(
                f"la generación se calculó con la zona {gen_tz}; la configuración es "
                f"{self.calendar.name}"
            )
        if self.channel == "operativo":
            if manifest["month"] != self.current_month():
                raise PublicationRefusedError("canal operativo: el Frozen no es del mes vigente")
            if SYNTHETIC_SYSTEMS & set(manifest["sources"]["source_systems"]):
                raise PublicationRefusedError("canal operativo: fuentes sintéticas")
        for contract, art in sorted(manifest["artifacts"].items()):  # reverificación previa
            self.state.store.verify(art["key"], art["sha256"])
            del contract
        head = self.state.publication_head(self.channel)
        if head is not None and head["gen_id"] == gen_id and action == "PUBLISH":
            return head  # idempotente: ya es la vigente
        expected = int(head["seq"]) if head else 0
        manifest_sha = sha256_bytes(
            self.state.store.get_self_verifying(
                f"{self.state.gen_prefix(self.channel, gen_id)}/manifest.json", lambda _b: None
            )
        )
        doc = self.state.append_publication(
            self.channel,
            expected,
            {
                "action": action,
                "gen_id": gen_id,
                "month": manifest["month"],
                "manifest_sha256": manifest_sha,
                "actor": actor,
                "server_ts": iso(self.clock()),
                **({} if self.calendar.is_utc else {"business_tz": self.calendar.name}),
            },
        )
        self.state.append_event(
            self.channel,
            doc["server_ts"],
            action,
            {"gen_id": gen_id, "seq": doc["seq"], "actor": actor},
        )
        return doc

    def rollback(self, to_gen_id: str, actor: str) -> dict[str, Any]:
        history = self.state.publication_history(self.channel)
        if not any(h["gen_id"] == to_gen_id for h in history):
            raise PublicationRefusedError("solo se puede volver a una generación ya publicada")
        return self.publish(to_gen_id, actor=actor, action="ROLLBACK")

    def status(self, key: str) -> dict[str, Any] | None:
        return self.repo.get_request(key)
