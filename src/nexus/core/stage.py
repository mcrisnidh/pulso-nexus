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
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexus.core.physical import KINDS, PhysicalResult, default_layout, detect_kind
from nexus.core.physical.in092u import read_in092u
from nexus.core.physical.oe965 import distinct_customers, read_oe965
from nexus.core.physical.portal import read_portal
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


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _reject_links(path: Path) -> None:
    """P-STG-01: ningún componente existente de la ruta puede ser un enlace o junction."""
    for part in (path, *path.parents):
        if part.exists() and _is_link(part):
            raise ConfigurationError(f"ruta con enlace simbólico o junction: {part.name!r}")


def check_isolation(data_dir: Path, src_dir: Path) -> None:
    """P-STG-01/02: el directorio privado es real (sin enlaces en ningún componente ni en sus
    subdirectorios), no contiene un almacén operativo/sintético y no escapa por enlaces."""
    _reject_links(data_dir)
    _reject_links(src_dir)
    if (data_dir / "store").exists():
        raise ConfigurationError("el directorio de datos ya contiene store/: use uno nuevo")
    for sub in ("originals", "inbox", "provenance"):
        base = data_dir / sub
        if base.exists():
            for item in (base, *base.rglob("*")):
                if _is_link(item):
                    raise ConfigurationError(f"enlace dentro de {sub}/: {item.name!r}")
    real = data_dir.resolve()
    for sub in ("originals", "inbox", "provenance"):
        target = (data_dir / sub).resolve()
        if real not in (target, *target.parents):
            raise ConfigurationError(f"{sub}/ escapa del directorio de datos")


def read_only(path: Path) -> bytes:
    """Lectura binaria del origen (nunca se abre en escritura)."""
    with path.open("rb") as fh:
        return fh.read()


def write_exclusive(path: Path, data: bytes, read_only_after: bool = False) -> str:
    """Creación exclusiva: si existe con el mismo contenido → ``unchanged``; distinto → error.
    Nunca reemplaza."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_links(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))
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
R1_TABLE_COLUMNS = ("item_portal", "item_erp", "status", "approval_id")
R1_MAP, R1_PORTAL_ONLY, R1_PROVISIONAL = "APPROVED_MAP", "APPROVED_PORTAL_ONLY", "PROVISIONAL"
LEDGER_SCHEMA = "nexus.core.approvals-ledger/1"
LEDGER_KINDS = ("R1_MAP", "R1_PORTAL_ONLY", "R1_REVISION_EQUIVALENCE")
PORTAL_NS = "PORTAL::"  # espacio de nombres de identidades solo-Portal (nunca un ITEM ERP)
_SHA = re.compile(r"^[0-9a-f]{64}$")


def valid_identity(value: str) -> bool:
    """D-R1-01: identidad EXACTA utilizable: no vacía, sin espacios exteriores, imprimible.
    Nunca se normaliza: una identidad inválida se rechaza."""
    return bool(value) and value == value.strip() and value.isprintable()


@dataclass(frozen=True)
class R1Resolution:
    """Resolución R1 de la tabla v2 verificada contra el registro autorizado."""

    mapping: dict[str, str]  # item_portal -> item_erp (APPROVED_MAP verificado)
    portal_only: frozenset[str]  # item_portal declarados sin equivalente ERP (verificado)
    report: dict[str, Any]


def parse_homologation(
    table_text: str,
    ledger: dict[str, Any] | None,
    approvers: frozenset[str],
    received_on: str,
    erp_universe: frozenset[str],
) -> R1Resolution:
    """Tabla R1 v2 (editable) + registro autorizado independiente (D-D).

    - Una fila solo es operativa si su ``approval_id`` existe en el registro, con el MISMO tipo
      e identidades, evidencia (referencia + SHA-256), aprobador autorizado y fecha ≤ recepción.
    - PROVISIONAL nunca se aplica. Una identidad inválida o un ITEM Portal con dos destinos
      invalida la TABLA COMPLETA (fallo cerrado, sin aplicar ninguna fila).
    - D-E: varios ITEM Portal → un ITEM ERP solo con ``R1_REVISION_EQUIVALENCE`` que los cubra a
      todos; si no, el grupo completo queda PENDIENTE.
    - D-F: el destino debe existir EXACTO en OE965/IN092U del periodo; si no, la fila queda
      PENDIENTE (``HOMOLOGATION_TARGET_NOT_IN_ERP``). Los ITEM solo-Portal se declaran con
      ``APPROVED_PORTAL_ONLY``, nunca se excluyen.
    """
    rep: dict[str, Any] = {"errors": [], "pending": {}, "warnings": []}
    empty = R1Resolution({}, frozenset(), rep)

    def pend(code: str, n: int = 1) -> None:
        rep["pending"][code] = rep["pending"].get(code, 0) + n

    reader = csv.DictReader(io.StringIO(table_text))
    if tuple(reader.fieldnames or ()) != R1_TABLE_COLUMNS:
        rep["errors"].append(f"columnas esperadas: {','.join(R1_TABLE_COLUMNS)}")
        return empty
    entries = _ledger_entries(ledger, approvers, received_on, rep)
    if rep["errors"]:
        return empty
    rows = list(reader)
    targets: dict[str, set[str]] = {}
    for n, row in enumerate(rows, start=2):
        portal, erp, status = row["item_portal"], row["item_erp"], row["status"]
        if status not in (R1_MAP, R1_PORTAL_ONLY, R1_PROVISIONAL) or not valid_identity(portal):
            rep["errors"].append(f"fila {n}: estado o identidad Portal inválidos")
            continue
        if status == R1_MAP and not valid_identity(erp):
            rep["errors"].append(f"fila {n}: identidad ERP inválida")
            continue
        targets.setdefault(portal, set()).add(f"{status}:{erp}")
    if any(len(t) > 1 for t in targets.values()):
        rep["errors"].append("ITEM Portal con más de una resolución (ambigua)")
    if rep["errors"]:
        return empty
    mapping: dict[str, str] = {}
    portal_only: set[str] = set()
    for row in rows:
        portal, erp, status, aid = (row[k] for k in R1_TABLE_COLUMNS)
        if status == R1_PROVISIONAL:
            pend("PROVISIONAL_NOT_APPLIED")
            continue
        entry = entries.get(aid)
        kind = "R1_MAP" if status == R1_MAP else "R1_PORTAL_ONLY"
        if (
            entry is None
            or entry["kind"] != kind
            or entry["item_portal"] != [portal]
            or (kind == "R1_MAP" and entry["item_erp"] != erp)
        ):
            pend("APPROVAL_NOT_IN_LEDGER")
            continue
        if kind == "R1_PORTAL_ONLY":
            portal_only.add(portal)
        elif erp not in erp_universe:
            pend("HOMOLOGATION_TARGET_NOT_IN_ERP")
        else:
            mapping[portal] = erp
    by_erp: dict[str, set[str]] = {}
    for portal, erp in mapping.items():
        by_erp.setdefault(erp, set()).add(portal)
    for erp, group in by_erp.items():
        if len(group) > 1 and not any(
            e["kind"] == "R1_REVISION_EQUIVALENCE"
            and e["item_erp"] == erp
            and set(e["item_portal"]) >= group
            for e in entries.values()
        ):
            for portal in group:
                del mapping[portal]
            pend("MANY_TO_ONE_WITHOUT_REVISION_EQUIVALENCE", len(group))
    rep.update({"mapped": len(mapping), "portal_only": len(portal_only)})
    return R1Resolution(mapping, frozenset(portal_only), rep)


def _ledger_entries(
    ledger: dict[str, Any] | None, approvers: frozenset[str], received_on: str, rep: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Entradas verificadas del registro autorizado. Una entrada inválida invalida el registro."""
    if ledger is None or ledger.get("schema") != LEDGER_SCHEMA:
        rep["errors"].append(
            f"registro autorizado ausente o con esquema distinto de {LEDGER_SCHEMA}"
        )
        return {}
    out: dict[str, dict[str, Any]] = {}
    for n, e in enumerate(ledger.get("entries", []), start=1):
        ok = (
            isinstance(e, dict)
            and valid_identity(str(e.get("approval_id", "")))
            and e.get("kind") in LEDGER_KINDS
            and isinstance(e.get("item_portal"), list)
            and e["item_portal"]
            and all(valid_identity(str(x)) for x in e["item_portal"])
            and (e["kind"] == "R1_PORTAL_ONLY" or valid_identity(str(e.get("item_erp", ""))))
            and valid_identity(str(e.get("evidence_ref", "")))
            and bool(_SHA.match(str(e.get("evidence_sha256", ""))))
            and str(e.get("approved_by", "")) in approvers
            and _iso_date(str(e.get("approved_at", ""))) is not None
            and str(e.get("approved_at")) <= received_on
            and e["approval_id"] not in out
        )
        if not ok:
            rep["errors"].append(f"entrada {n} del registro inválida o no autorizada")
            continue
        out[e["approval_id"]] = e
    return out


def _iso_date(text: str) -> str | None:
    try:
        return dt.date.fromisoformat(text).isoformat() if len(text) == 10 else None
    except ValueError:
        return None


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
        # Contabilidad de líneas: cada línea física quedó en ALGUNA clase (incluida
        # «unclassified»). NO es aceptación: ver ``acceptance``.
        "line_accounting": {
            "total_lines": result.total_lines,
            "lines_in_some_class": sum(result.classes.values()),
            "unclassified_lines": result.classes.get("unclassified", 0),
            "all_lines_counted": sum(result.classes.values()) == result.total_lines,
            "meaning": "conteo contable de líneas; NO indica que el archivo sea válido",
        },
        "acceptance": {
            "verdict": "PASS" if result.valid else "FAIL",
            "records_accepted": len(result.records) if result.valid else 0,
            "records_read": len(result.records),
            "unclassified_lines": result.classes.get("unclassified", 0),
            "blocking_exceptions": sum(1 for e in result.exceptions if e.severity == "BLOCKING"),
            "rule": "PASS solo sin excepciones bloqueantes, con registros y fecha de documento",
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


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _resolve_r1(
    table: Path | None,
    ledger: Path | None,
    approvers: Path | None,
    received_on: str,
    erp_universe: set[str],
) -> R1Resolution:
    """Sin tabla, registro o aprobadores: nada se homologa (todo PENDIENTE, nada se descarta)."""
    if table is None or ledger is None or approvers is None:
        return R1Resolution({}, frozenset(), {"errors": [], "pending": {"NO_R1_EVIDENCE": 1}})
    allowed = json.loads(read_only(approvers).decode("utf-8"))
    res = parse_homologation(
        read_only(table).decode("utf-8-sig"),
        json.loads(read_only(ledger).decode("utf-8")),
        frozenset(str(x) for x in allowed.get("approvers", [])),
        received_on,
        frozenset(erp_universe),
    )
    res.report.update(
        {
            "table_sha256": sha256_bytes(read_only(table)),
            "ledger_sha256": sha256_bytes(read_only(ledger)),
            "approvers_sha256": sha256_bytes(read_only(approvers)),
        }
    )
    return res


def _with_r1(rec: dict[str, str], r1: R1Resolution) -> dict[str, str]:
    """Identidad ERP solo por resolución R1 verificada; la identidad Portal se conserva."""
    portal = rec["item_portal"]
    if portal in r1.mapping:
        return {**rec, "item": r1.mapping[portal], "r1_status": "MAPPED"}
    if portal in r1.portal_only:
        return {**rec, "item": PORTAL_NS + portal, "r1_status": "PORTAL_ONLY"}
    return {**rec, "item": "", "r1_status": "PENDING"}


# ------------------------------------------------------------------ stage
def contract_csv(source_id: str, records: list[dict[str, str]]) -> bytes:
    spec = contract_layout()[source_id]
    cols = [*spec["required_columns"], *spec.get("optional_columns", [])]
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
    ledger_path: Path | None = None,
    approvers_path: Path | None = None,
) -> dict[str, Any]:
    """Prepara las fuentes del origen en el directorio de datos privado. No publica."""
    if not src_dir.is_dir():
        raise ConfigurationError(f"origen inexistente: {src_dir}")
    check_private_output(data_dir, src_dir)
    check_isolation(data_dir, src_dir)
    layout = layout or default_layout()
    received = _iso(clock())
    business_tz = _business_tz(data_dir)
    homologation_report: dict[str, Any] = {"provided": homologation_path is not None}
    for priv in (homologation_path, ledger_path, approvers_path):
        if priv is not None:
            check_private_output(priv)
            _reject_links(priv)
    report: dict[str, Any] = {
        "schema": STAGE_SCHEMA,
        "received_at": received,
        "business_tz": business_tz,
        "homologation": homologation_report,
        "files": [],
        "ignored": {},
    }
    seen_sha: dict[str, str] = {}
    erp_universe: set[str] = set()
    order = {"oe965": 0, "in092u": 1, "portal": 2}
    files = [p for p in src_dir.iterdir() if p.is_file() or _is_link(p)]
    for link in [p for p in files if _is_link(p)]:
        raise ConfigurationError(f"archivo de origen enlazado (no se sigue): {link.name!r}")
    files.sort(key=lambda p: (order.get(detect_kind(p.name, layout) or "", 3), p.name))
    for path in files:
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
        result = read_physical(kind, data, path.name, layout, business_tz, {})
        entry.update(
            {
                "records": len(result.records),
                "exceptions": result.exception_summary(),
                "document_time": result.document_time,
            }
        )
        records = result.records
        if kind in ("oe965", "in092u") and result.valid:
            erp_universe.update(r["item"] for r in records)  # D-F: universo ERP del periodo
        if kind == "portal" and result.valid:
            r1 = _resolve_r1(
                homologation_path, ledger_path, approvers_path, received[:10], erp_universe
            )
            homologation_report.update(r1.report)
            records = [_with_r1(r, r1) for r in records]  # se conservan TODAS las líneas
            # El lector avisa UNMAPPED_ITEM antes de aplicar R1; el estado real es r1_status.
            result.exceptions[:] = [e for e in result.exceptions if e.code != "UNMAPPED_ITEM"]
            entry["exceptions"] = result.exception_summary()
            entry["r1_status"] = dict(sorted(_count(r["r1_status"] for r in records).items()))
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
