"""Δ2 — ``nexus core probe`` y ``nexus core stage``: inspección y preparación LOCAL de fuentes
reales, separadas de cualquier publicación.

Garantías:

- **Origen intacto:** el original se abre solo para lectura (``rb``); nunca se escribe, mueve,
  renombra ni cambia de fecha. Las pruebas comparan hash y mtime antes y después.
- **probe:** devuelve SOLO estructura saneada (formas con letras → ``A`` y dígitos → ``9``,
  longitudes, clases de línea, conteos y códigos de excepción con su línea física). Nunca filas,
  ITEM, clientes, pedidos ni cantidades. Las claves de cliente distintas (bloqueo R3) solo se
  escriben, si se pide, en un archivo local privado fuera de cualquier repositorio git.
- **stage:** escribe en un directorio de datos PRIVADO (fuera de todo repositorio git y fuera
  del origen): copia de solo lectura del original por SHA-256 (``originals/``), CSV del contrato
  CORE (``inbox/<fuente>/``) y sidecar de procedencia (``provenance/<fuente>/``) con los tiempos
  separados: documento, extracción (solo si la fuente la declara) y recepción por NEXUS.
  Nunca reemplaza un archivo existente (creación exclusiva; contenido distinto → error).
- **No publica:** ``stage`` no calcula ni publica; la publicación es ``nexus core refresh`` y,
  para datos reales de noviembre, solo en el canal ``replay``.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexus.core.physical import KINDS, PhysicalResult, default_layout, detect_kind
from nexus.core.physical.in092u import read_in092u
from nexus.core.physical.oe965 import distinct_customers, read_oe965
from nexus.core.physical.portal import read_portal, unmapped_lines
from nexus.core.sources import layout as contract_layout
from nexus.errors import ConfigurationError
from nexus.hashing import digest_json, sha256_bytes

Clock = Callable[[], dt.datetime]
PROVENANCE_SCHEMA = "nexus.core.provenance/1"
PROBE_SCHEMA = "nexus.core.probe/1"
STAGE_SCHEMA = "nexus.core.stage-report/1"
HOMOLOGATION_STATUSES = ("APPROVED", "PROVISIONAL")
HOMOLOGATION_COLUMNS = ("item_portal", "item_erp", "status", "evidence")
OE965_EQUIVALENCE_NOTE = (
    "OE965 se entrega con el contrato lógico oeras_29 solo por la forma de sus campos; NO es el "
    "29 OERAS certificado y no prueba equivalencia entre ambos (equivalencia: NOT_ESTABLISHED)"
)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------ rutas y salvaguardas
def inside_git_worktree(path: Path) -> Path | None:
    """Raíz del repositorio git que contiene ``path`` (o ``None``)."""
    current = path.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def check_private_output(path: Path, source_dir: Path | None = None) -> None:
    """Rechaza escribir datos reales dentro de un repositorio git o dentro del origen."""
    repo = inside_git_worktree(path)
    if repo is not None:
        raise ConfigurationError(
            f"salida dentro del repositorio git {repo}: los datos reales deben quedar FUERA "
            "(p. ej. %LOCALAPPDATA%\\PULSO_NEXUS_LAB\\...)"
        )
    if source_dir is not None:
        out, src = path.resolve(), source_dir.resolve()
        if out == src or src in out.parents or out in src.parents:
            raise ConfigurationError("el directorio de datos no puede estar dentro del origen")


def read_only(path: Path) -> bytes:
    """Lectura binaria del origen (nunca se abre en escritura)."""
    with path.open("rb") as fh:
        return fh.read()


def write_exclusive(path: Path, data: bytes, read_only_after: bool = False) -> str:
    """Creación exclusiva: si existe con el mismo contenido → ``unchanged``; distinto → error.
    Nunca reemplaza."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
    except FileExistsError:
        if read_only(path) == data:
            return "unchanged"
        raise ConfigurationError(
            f"{path.name} ya existe con otro contenido: no se reemplaza"
        ) from None
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    if read_only_after:
        path.chmod(stat.S_IREAD)
    return "created"


# ------------------------------------------------------------------ homologación (bloqueo R1)
def parse_homologation(text: str) -> tuple[dict[str, str], dict[str, Any]]:
    """Tabla de homologación ITEM Portal → ITEM ERP. Igualdad EXACTA de texto (sin normalizar).

    Solo las filas ``APPROVED`` se aplican; las ``PROVISIONAL`` se cuentan y se ignoran (una
    asociación provisional nunca se convierte en regla). Un ITEM Portal con dos destinos invalida
    la tabla completa (no se aplica nada). Varios ITEM Portal hacia un mismo ERP se informan.
    """
    report: dict[str, Any] = {
        "approved": 0,
        "provisional_ignored": 0,
        "errors": [],
        "warnings": [],
    }
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != HOMOLOGATION_COLUMNS:
        report["errors"].append(f"columnas esperadas: {','.join(HOMOLOGATION_COLUMNS)}")
        return {}, report
    targets: dict[str, set[str]] = {}
    approved: dict[str, str] = {}
    for n, row in enumerate(reader, start=2):
        portal, erp, status = row["item_portal"], row["item_erp"], row["status"]
        if status not in HOMOLOGATION_STATUSES or not portal or not erp:
            report["errors"].append(f"fila {n}: estado inválido o ITEM vacío")
            continue
        targets.setdefault(portal, set()).add(erp)
        if status == "APPROVED":
            approved[portal] = erp
        else:
            report["provisional_ignored"] += 1
    ambiguous = sorted(p for p, t in targets.items() if len(t) > 1)
    if ambiguous:
        report["errors"].append(f"{len(ambiguous)} ITEM Portal con más de un ITEM ERP")
    if report["errors"]:
        return {}, report
    by_erp: dict[str, int] = {}
    for erp in approved.values():
        by_erp[erp] = by_erp.get(erp, 0) + 1
    many = sum(1 for c in by_erp.values() if c > 1)
    if many:
        report["warnings"].append(f"{many} ITEM ERP reciben más de un ITEM Portal (no inyectiva)")
    report["approved"] = len(approved)
    return approved, report


# ------------------------------------------------------------------ lectura física
def read_physical(
    kind: str,
    data: bytes,
    file_name: str,
    layout: dict[str, Any],
    business_tz: str | None = None,
    homologation: dict[str, str] | None = None,
) -> PhysicalResult:
    if kind == "oe965":
        return read_oe965(data, layout)
    if kind == "portal":
        return read_portal(data, layout, file_name, homologation)
    if kind == "in092u":
        return read_in092u(data, layout, business_tz)
    raise ValueError(f"tipo de fuente no soportado: {kind!r} (use {', '.join(KINDS)})")


def sanitized_structure(result: PhysicalResult, show_headers: bool = False) -> dict[str, Any]:
    """Estructura SANEADA (sin filas). Es lo único que se comparte para ajustar el layout."""
    lengths = sorted(result.lengths.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
    flags = {k: v for k, v in result.flags.items() if k != "header_names"}
    out: dict[str, Any] = {
        "kind": result.kind,
        "contract_source": result.source_id,
        "encoding": result.encoding,
        "bom": result.bom,
        "line_endings": result.line_endings,
        "total_lines": result.total_lines,
        "line_classes": dict(sorted(result.classes.items())),
        "line_lengths_top": [{"length": k, "lines": v} for k, v in lengths],
        "shapes_masked": result.shapes,
        "document_time": {
            "precision": result.document_time.get("precision"),
            "source": result.document_time.get("source"),
            "month": str(result.document_time.get("value", ""))[:7] or None,
        },
        "flags": flags,
        "records": len(result.records),
        "distinct_counts": {
            col: len({r[col] for r in result.records})
            for col in ("item", "item_portal", "customer_key_exact", "order", "stockroom")
            if result.records and col in result.records[0]
        },
        "line_balance": {
            "total_lines": result.total_lines,
            "classified_lines": sum(result.classes.values()),
            "balanced": sum(result.classes.values()) == result.total_lines,
        },
        "exceptions": result.exception_summary(),
        "exception_lines_first": [
            e.as_dict() for e in result.exceptions if e.severity == "BLOCKING"
        ][:20],
        "exception_first_line_by_code": {
            key: line for key, line in sorted(_first_lines(result).items())
        },
        "valid": result.valid,
    }
    if show_headers and "header_names" in result.flags:
        out["header_names"] = result.flags["header_names"]
    if result.kind == "oe965":
        out["contract_note"] = OE965_EQUIVALENCE_NOTE
    return out


def _first_lines(result: PhysicalResult) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for e in result.exceptions:
        key = f"{e.severity}:{e.code}" + (f":{e.field}" if e.field else "")
        out.setdefault(key, e.line)
    return out


def probe(
    path: Path,
    kind: str | None = None,
    layout: dict[str, Any] | None = None,
    business_tz: str | None = None,
    show_headers: bool = False,
    keys_out: Path | None = None,
) -> dict[str, Any]:
    """Inspección de un archivo en SOLO LECTURA. Devuelve un informe saneado."""
    layout = layout or default_layout()
    kind = kind or detect_kind(path.name, layout)
    if kind is None:
        raise ConfigurationError(f"no se reconoce el tipo de {path.name}; indique --kind")
    data = read_only(path)
    result = read_physical(kind, data, path.name, layout, business_tz)
    report = {
        "schema": PROBE_SCHEMA,
        "file": path.name,
        "sha256": sha256_bytes(data),
        "size": len(data),
        "layout_status": layout.get("status"),
        "layout_sha256": digest_json(layout),
        "structure": sanitized_structure(result, show_headers),
        "notice": "informe saneado: sin filas, ITEM, clientes, pedidos ni cantidades",
    }
    if keys_out is not None:
        if kind != "oe965":
            raise ConfigurationError("--keys-out solo aplica a OE965")
        check_private_output(keys_out, path.parent)
        lines = ["customer_key_exact,lines"] + [
            f'"{k.replace(chr(34), chr(34) * 2)}",{n}'
            for k, n in distinct_customers(result).items()
        ]
        write_exclusive(keys_out, ("\n".join(lines) + "\n").encode("utf-8"))
        report["keys_out"] = "escrito (archivo local privado; no compartir)"
    return report


# ------------------------------------------------------------------ stage
def contract_csv(source_id: str, records: list[dict[str, str]]) -> bytes:
    cols = contract_layout()[source_id]["required_columns"]
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    writer.writerows(records)
    return buf.getvalue().encode("utf-8")


def _excluded_statuses(data_dir: Path) -> frozenset[str]:
    rules = data_dir / "rules.json"
    if not rules.is_file():
        return frozenset()
    data = json.loads(read_only(rules).decode("utf-8"))
    return frozenset(str(s) for s in data.get("portal_excluded_statuses", []))


def _business_tz(data_dir: Path) -> str | None:
    path = data_dir / "operation.json"
    if not path.is_file():
        return None
    data = json.loads(read_only(path).decode("utf-8"))
    return str(data["business_tz"]) if data.get("business_tz") else None


def stage(  # noqa: PLR0915 (pasos lineales del procedimiento, cada uno auditado)
    src_dir: Path,
    data_dir: Path,
    homologation_path: Path | None = None,
    layout: dict[str, Any] | None = None,
    clock: Clock = utcnow,
) -> dict[str, Any]:
    """Prepara las fuentes del origen en el directorio de datos privado. No publica."""
    if not src_dir.is_dir():
        raise ConfigurationError(f"origen inexistente: {src_dir}")
    check_private_output(data_dir, src_dir)
    layout = layout or default_layout()
    received = _iso(clock())
    business_tz = _business_tz(data_dir)
    homologation: dict[str, str] = {}
    homologation_report: dict[str, Any] = {"provided": False}
    if homologation_path is not None:
        check_private_output(homologation_path)
        raw = read_only(homologation_path)
        homologation, homologation_report = parse_homologation(raw.decode("utf-8-sig"))
        homologation_report.update({"provided": True, "sha256": sha256_bytes(raw)})
    excluded = _excluded_statuses(data_dir)
    report: dict[str, Any] = {
        "schema": STAGE_SCHEMA,
        "received_at": received,
        "business_tz": business_tz,
        "homologation": homologation_report,
        "files": [],
        "ignored": {},
    }
    seen_sha: dict[str, str] = {}
    for path in sorted(p for p in src_dir.iterdir() if p.is_file()):
        kind = detect_kind(path.name, layout)
        if kind is None:
            ext = path.suffix.lower() or "(sin extensión)"
            report["ignored"][ext] = report["ignored"].get(ext, 0) + 1
            continue
        data = read_only(path)
        sha = sha256_bytes(data)
        entry: dict[str, Any] = {"file": path.name, "kind": kind, "sha256": sha}
        report["files"].append(entry)
        if sha in seen_sha:
            entry["status"] = "DUPLICATE_CONTENT"
            entry["same_as"] = seen_sha[sha]
            continue
        seen_sha[sha] = path.name
        write_exclusive(data_dir / "originals" / f"{sha}{path.suffix.lower()}", data, True)
        result = read_physical(kind, data, path.name, layout, business_tz, homologation)
        entry.update(
            {
                "records": len(result.records),
                "exceptions": result.exception_summary(),
                "document_time": result.document_time,
            }
        )
        records = result.records
        if kind == "portal" and result.valid:
            unmapped = unmapped_lines(result)
            drop = [r for r in unmapped if r["status"] in excluded]
            keep_unmapped = [r for r in unmapped if r["status"] not in excluded]
            entry["unmapped_excluded_status_dropped"] = len(drop)
            entry["unmapped_active"] = len(keep_unmapped)
            if keep_unmapped:
                entry["status"] = "QUARANTINED"
                entry["reason"] = (
                    "R1: líneas Portal activas sin homologación exacta aprobada; el Portal no "
                    "se usa y la demanda de la clase sustituida queda desconocida"
                )
                continue
            records = [r for r in records if r["item"]]
        if not result.valid:
            entry["status"] = "QUARANTINED"
            entry["reason"] = "lectura física inválida (ver códigos de excepción)"
            continue
        csv_bytes = contract_csv(result.source_id, records)
        csv_name = f"{path.stem}__{sha[:12]}.csv"
        csv_status = write_exclusive(data_dir / "inbox" / result.source_id / csv_name, csv_bytes)
        sidecar = {
            "schema": PROVENANCE_SCHEMA,
            "original": {
                "file_name": path.name,
                "sha256": sha,
                "size": len(data),
                "kind": kind,
                "encoding": result.encoding,
                "bom": result.bom,
                "line_endings": result.line_endings,
            },
            "contract_csv": {
                "source_id": result.source_id,
                "file": csv_name,
                "sha256": sha256_bytes(csv_bytes),
                "records": len(records),
            },
            "times": {
                "document": result.document_time,
                "extraction": {
                    "value": None,
                    "reason": "la fuente no declara hora de extracción del sistema",
                },
                "reception": {"value": received, "source": "nexus core stage (reloj local)"},
            },
            "layout": {"status": layout.get("status"), "sha256": digest_json(layout)},
            "homologation": homologation_report if kind == "portal" else None,
            "contract_equivalence": (
                {"status": "NOT_ESTABLISHED", "note": OE965_EQUIVALENCE_NOTE}
                if kind == "oe965"
                else None
            ),
            "reconciliation": result.flags.get("reconciliation"),
            "exceptions": result.exception_summary(),
        }
        sidecar_path = data_dir / "provenance" / result.source_id / f"{csv_name}.json"
        if sidecar_path.exists():  # la PRIMERA recepción se conserva (C1)
            sidecar_status = "kept_first_reception"
        else:
            write_exclusive(
                sidecar_path,
                json.dumps(sidecar, indent=1, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            )
            sidecar_status = "created"
        entry.update(
            {
                "status": "STAGED",
                "contract_file": csv_name,
                "contract_sha256": sha256_bytes(csv_bytes),
                "csv": csv_status,
                "provenance": sidecar_status,
            }
        )
    return report
