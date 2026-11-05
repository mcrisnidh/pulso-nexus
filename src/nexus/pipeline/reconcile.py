"""Reconciliación SQL ↔ ObjectStore (NX-E1B-02). SOLO INFORME: nunca borra ni mueve archivos.

R-A  SQL restaurado a un punto anterior, archivos actuales  -> archivos SIN fila:
     se informan como UNREGISTERED_FILE (no se borran, no se mueven, no se registran solos).
R-B  Archivos restaurados/eliminados, SQL actual             -> filas SIN archivo (o hash distinto):
     transición auditada a MISSING/CORRUPT (la generación deja de ser elegible).
R-C  Caída entre "archivo durable" y "fila"                 -> archivo sin fila de una corrida
     cuyo registro de almacén está incompleto: UNREGISTERED_FILE con etiqueta INCOMPLETE_RUN.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from nexus.errors import IntegrityError
from nexus.metadata.repository import MetadataRepository
from nexus.storage.object_store import LocalObjectStore

_ARTIFACT = re.compile(r"^runs/(run-[0-9a-f]{32})/generation/[^/]+\.parquet$")


@dataclass
class ReconcileReport:
    checked_rows: int = 0
    marked_missing: list[str] = field(default_factory=list)
    marked_corrupt: list[str] = field(default_factory=list)
    unregistered_files: list[dict[str, str]] = field(default_factory=list)


def reconcile(store: LocalObjectStore, repo: MetadataRepository) -> ReconcileReport:
    report = ReconcileReport()
    registered: set[str] = set()
    for row in repo.list_artifacts():
        key = str(row["object_key"])
        registered.add(key)
        report.checked_rows += 1
        if row["artifact_state"] != "PRESENT":
            continue
        try:
            store.verify(key, str(row["content_sha256"]))
        except FileNotFoundError:
            if repo.mark_artifact_state(
                str(row["attempt_id"]), str(row["contract"]), "MISSING", key
            ):
                report.marked_missing.append(key)
        except IntegrityError as exc:
            if repo.mark_artifact_state(
                str(row["attempt_id"]), str(row["contract"]), "CORRUPT", str(exc)
            ):
                report.marked_corrupt.append(key)
    for key in store.iter_keys("runs/"):
        match = _ARTIFACT.match(key)
        if not match or key in registered:
            continue
        run_id = match.group(1)
        complete = store.exists(f"runs/{run_id}/run_record.json")
        report.unregistered_files.append(
            {
                "key": key,
                "store_run_id": run_id,
                "kind": "UNREGISTERED_FILE",
                "label": "COMPLETE_STORE_RUN" if complete else "INCOMPLETE_RUN",
            }
        )
    return report
