"""R3 — consulta de cartera y solicitudes de cambio NO OPERATIVAS.

Separación de responsabilidades:
 - El catálogo es solo histórico + criterios comerciales informados; NO es rules.json.
 - Las solicitudes son append-only, nunca aprobaciones.
 - No existe función de publicación, activación ni escritura de clasificación.
 - La identidad exacta es customer_number (10 chars, padding derecho) + sequence (3 digitos).

La interfaz web es localhost SIN autenticación corporativa; por ello el autor
se etiqueta EDITOR_LOCAL_SIN_AUTENTICAR y ninguna acción puede aprobar cambios.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

SCHEMA = "nexus.r3.propuesta/1"
CATALOG_SCHEMA = "nexus.r3.catalog.preview/1"
ACTIONS = {"AGREGAR_PORTAL_CAT", "RETIRAR_PORTAL_CAT"}
PROTECTED_SSRS = frozenset({"TUPY      000", "CATPERK   000", "CATPERK   001", "PERK1     001"})
CONFIRMED_PORTAL = frozenset({"98753462  001", "PERK2     001", "PECL      001", "17057704  205"})
EXPECTED_HISTORICAL_SHA = "34c8a2097fed5d8a9d2c0ae5aeb003dd34d73ba1f3dbe12f968a8c24fc0550e7"


class R3AdminError(ValueError):
    pass


def canonical(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def exact_key(customer_number: str, sequence: str) -> str:
    """Valida por campos; no admite heurísticas para adivinar claves de ERP."""
    code = str(customer_number).strip().upper()
    seq = str(sequence).strip()
    if not re.fullmatch(r"[A-Z0-9]{1,10}", code, flags=re.ASCII):
        raise R3AdminError("Número de cliente: 1-10 caracteres alfanuméricos, sin espacios interiores.")
    if not re.fullmatch(r"[0-9]{3}", seq, flags=re.ASCII):
        raise R3AdminError("Secuencia: exactamente tres dígitos, incluidos ceros iniciales.")
    key = f"{code:<10}{seq}"
    assert len(key) == 13
    return key


def split_key(key: str) -> tuple[str, str]:
    if not isinstance(key, str) or len(key) != 13:
        raise R3AdminError("Identidad histórica sin 13 caracteres exactos.")
    code, seq = key[:10].rstrip(), key[10:]
    if exact_key(code, seq) != key:
        raise R3AdminError("Identidad ERP no canónica.")
    return code, seq


def next_month_utc() -> str:
    today = dt.datetime.now(dt.timezone.utc).date()
    year, month = today.year, today.month
    month += 1
    if month == 13:
        month, year = 1, year + 1
    return f"{year:04d}-{month:02d}"


class R3Admin:
    def __init__(self, seed: Path, data_dir: Path):
        self.seed_path = Path(seed)
        self.data_dir = Path(data_dir)
        self.proposals_dir = self.data_dir / "propuestas"
        self._catalog = self._read_seed()

    def _read_seed(self) -> dict[str, Any]:
        raw = json.loads(self.seed_path.read_text("utf-8"))
        if (raw.get("schema") != CATALOG_SCHEMA or raw.get("source_sha256") != EXPECTED_HISTORICAL_SHA
                or raw.get("operational") is not False or len(raw.get("customers", [])) != 84):
            raise R3AdminError("Catálogo visual R3 distinto del contrato histórico de 84 cuentas.")
        seen = set()
        for row in raw["customers"]:
            key = row["customer_key_exact"]
            split_key(key)
            if key in seen or row.get("formal_approval") is not False:
                raise R3AdminError("Duplicidad o aprobación implícita en catálogo visual.")
            seen.add(key)
        if sum(int(r["historical_lines"]) for r in raw["customers"]) != 3147:
            raise R3AdminError("Diferencia de inventario histórico.")
        for row in raw["customers"]:
            key = row["customer_key_exact"]
            expected = "SSRS" if key in PROTECTED_SSRS else "PORTAL_CAT" if key in CONFIRMED_PORTAL else "PENDIENTE"
            if row.get("commercial_criterion") != expected:
                raise R3AdminError("Criterio visual cambiado sin evidencia ni autorización.")
        return raw

    def catalog(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._catalog["customers"])

    def _load_event(self, path: Path) -> dict[str, Any]:
        if path.is_symlink():
            raise R3AdminError("Archivo de propuesta simbólico no permitido.")
        ev = json.loads(path.read_text("utf-8"))
        digest = ev.pop("record_sha256", None)
        if ev.get("schema") != SCHEMA or not isinstance(digest, str) or digest != hashlib.sha256(canonical(ev)).hexdigest():
            raise R3AdminError(f"Propuesta alterada o inválida: {path.name}")
        ev["record_sha256"] = digest
        return ev

    def proposals(self) -> list[dict[str, Any]]:
        if not self.proposals_dir.exists():
            return []
        if self.proposals_dir.is_symlink():
            raise R3AdminError("Ruta de propuestas simbólica no permitida.")
        files = sorted(self.proposals_dir.glob("*.json"))
        if len(files) > 2000:
            raise R3AdminError("Límite de solicitudes superado; requiere revisión administrativa.")
        events = [self._load_event(p) for p in files]
        return sorted(events, key=lambda x: x["created_utc"], reverse=True)

    def submit(self, *, number: str, sequence: str, action: str, effective_month: str,
               reason: str, evidence_ref: str, author: str = "EDITOR_LOCAL_SIN_AUTENTICAR") -> dict[str, Any]:
        key = exact_key(number, sequence)
        if action not in ACTIONS:
            raise R3AdminError("Acción no permitida.")
        if key in PROTECTED_SSRS and action == "AGREGAR_PORTAL_CAT":
            raise R3AdminError("Cuenta SSRS protegida por decisión comercial. Requiere expediente de excepción formal.")
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", effective_month):
            raise R3AdminError("Mes propuesto inválido (AAAA-MM).")
        # Solo propuestas a futuro; jamás hay cambios retroactivos al Frozen.
        if effective_month < next_month_utc():
            raise R3AdminError("Mes efectivo debe ser posterior al actual; el Frozen sellado no se reescribe.")
        reason = str(reason).strip()
        evidence_ref = str(evidence_ref).strip()
        if not (12 <= len(reason) <= 500):
            raise R3AdminError("Motivo obligatorio: 12 a 500 caracteres.")
        if not (5 <= len(evidence_ref) <= 250):
            raise R3AdminError("Referencia de evidencia obligatoria: 5 a 250 caracteres.")
        if self.proposals_dir.exists() and self.proposals_dir.is_symlink():
            raise R3AdminError("Ruta de propuestas no permitida.")
        for prev in self.proposals():
            if (prev["customer_key_exact"] == key and prev["action"] == action
                    and prev["effective_month"] == effective_month):
                raise R3AdminError("Ya existe una solicitud para esta cuenta, acción y mes; no duplicar.")
        record = {
            "schema": SCHEMA,
            "id": secrets.token_hex(16),
            "created_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "customer_key_exact": key,
            "action": action,
            "effective_month": effective_month,
            "reason": reason,
            "evidence_reference": evidence_ref,
            "author": author,
            "status": "PENDIENTE_APROBACION",
            "approved": False,
            "applies_to_refresh": False,
            "applies_to_frozen": False,
        }
        record["record_sha256"] = hashlib.sha256(canonical(record)).hexdigest()
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        dest = self.proposals_dir / (record["created_utc"].replace(":", "").replace("-", "") + "_" + record["id"] + ".json")
        with dest.open("xb") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
            f.flush()
            os.fsync(f.fileno())
        return record
