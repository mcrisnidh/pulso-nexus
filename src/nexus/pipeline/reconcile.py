"""Reconciliación SQL ↔ ObjectStore (NX-E1B-02, completada en NX-E1B-R1-02). SOLO INFORME.

Nunca elimina, mueve, altera ni registra archivos o filas por su cuenta. La única escritura es
la transición AUDITADA de filas de artefacto ``PRESENT → MISSING/CORRUPT`` mediante el
procedimiento ``usp_mark_artifact_state`` (dirección R-B, contemplada en B15).

Cobertura: TODOS los objetos del almacén, no solo los artefactos.

- ``runs/<run_id>/generation/<contrato>-<sha256>.parquet``: artefactos (``ARTIFACT``).
- ``runs/<run_id>/input_manifest.json``: identidad de reproducibilidad (``INPUT_MANIFEST``).
- ``runs/<run_id>/run_record.json``: registro de la corrida (``RUN_RECORD``).
- ``snapshots/<snapshot_id>/manifest.json`` y ``content.bin``: snapshots
  (``SNAPSHOT_MANIFEST`` y ``SNAPSHOT_CONTENT``).
- Cualquier otra clave: ``UNKNOWN_OBJECT``. Restos de staging: se informan aparte.

Para cada objeto se calcula su **identidad** (SHA-256 de los bytes y tamaño) y su
**integridad**:

- sello del documento autosellado;
- hash declarado en el nombre del artefacto;
- entrada del ``run_record``;
- ``content_sha256`` del manifiesto del snapshot.

También se registra su **procedencia** cuando está disponible: corrida, contrato, digests,
snapshot y fechas.

Cada corrida recibe un estado:

- ``COMPLETE_STORE_RUN``: ``run_record`` válido y todas sus referencias presentes e íntegras.
- ``INCOMPLETE_RUN``: sin ``run_record`` (caída antes del paso 3 de E1-A).
- ``CORRUPT_RUN_RECORD``: el sello del ``run_record`` no coincide.
- ``INCONSISTENT_RUN``: referencias incompletas, por ejemplo un artefacto citado ausente o con
  otro hash, un ``input_manifest`` ausente, corrupto o con otro digest, o un ``.parquet`` no
  citado por el registro.

Direcciones:

- **R-A**: SQL restaurado a un punto anterior. Los archivos sin fila se informan como
  ``UNREGISTERED_FILE``.
- **R-B**: archivos ausentes o alterados con SQL actual. La fila de artefacto pasa a
  ``MISSING``/``CORRUPT`` con auditoría. Los snapshots y las generaciones selladas cuyas
  referencias no casan se informan en ``sql_reference_issues``.
- **R-C**: caída entre archivo y fila. Igual que R-A, con el estado de la corrida como etiqueta.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from nexus.errors import IntegrityError
from nexus.hashing import sha256_bytes
from nexus.metadata.repository import MetadataRepository
from nexus.pipeline.manifest import input_digest
from nexus.sealed import unseal
from nexus.storage.object_store import LocalObjectStore

_RUN_OBJECT = re.compile(r"^runs/(run-[0-9a-f]{32})/(.+)$")
_ARTIFACT_NAME = re.compile(r"^generation/(?P<contract>[^/]+)-(?P<sha>[0-9a-f]{64})\.parquet$")
_SNAPSHOT_OBJECT = re.compile(r"^snapshots/(snap-[0-9a-f]{32})/(manifest\.json|content\.bin)$")

COMPLETE = "COMPLETE_STORE_RUN"
INCOMPLETE = "INCOMPLETE_RUN"
CORRUPT_RECORD = "CORRUPT_RUN_RECORD"
INCONSISTENT = "INCONSISTENT_RUN"


@dataclass
class ReconcileReport:
    checked_rows: int = 0
    marked_missing: list[str] = field(default_factory=list)
    marked_corrupt: list[str] = field(default_factory=list)
    objects_scanned: int = 0
    unregistered_files: list[dict[str, Any]] = field(default_factory=list)
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    sql_reference_issues: list[dict[str, Any]] = field(default_factory=list)
    staging_leftovers: list[str] = field(default_factory=list)


def _accept_any(_: bytes) -> None:
    """Lectura sin verificación: solo se calculan la identidad y la integridad aquí."""


def _read(store: LocalObjectStore, key: str) -> bytes | None:
    try:
        return store.get_self_verifying(key, _accept_any)
    except (FileNotFoundError, IntegrityError, OSError):
        return None


def _sealed(data: bytes | None, key: str) -> dict[str, Any] | None:
    if data is None:
        return None
    try:
        return unseal(data, key)
    except IntegrityError:
        return None


def _mark_sql_artifacts(
    store: LocalObjectStore, repo: MetadataRepository, report: ReconcileReport
) -> set[str]:
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
    return registered


def _analyze_run(
    run_id: str, objects: dict[str, tuple[str, int]], docs: dict[str, dict[str, Any] | None]
) -> dict[str, Any]:
    """Estado e integridad de una corrida a partir de sus objetos (clave -> (sha256, tamaño))."""
    base = f"runs/{run_id}"
    record_key, manifest_key = f"{base}/run_record.json", f"{base}/input_manifest.json"
    problems: list[str] = []
    record = docs.get(record_key)
    status = COMPLETE
    cited: dict[str, dict[str, Any]] = {}
    provenance: dict[str, Any] = {"run_id": run_id}
    if record_key not in objects:
        status = INCOMPLETE
    elif record is None:
        status = CORRUPT_RECORD
        problems.append("run_record: sello no coincide")
    else:
        provenance.update(
            {
                "input_digest": record.get("input_digest"),
                "result_digest": record.get("result_digest"),
                "trigger": record.get("trigger"),
                "started_at": record.get("started_at"),
                "finished_at": record.get("finished_at"),
                "artifacts": len(record.get("artifacts", [])),
            }
        )
        if record.get("run_id") != run_id:
            problems.append(f"run_record declara run_id {record.get('run_id')!r}")
        for art in record.get("artifacts", []):
            key = str(art.get("key"))
            cited[key] = art
            if key not in objects:
                problems.append(f"artefacto citado ausente: {key}")
            elif objects[key][0] != art.get("content_sha256"):
                problems.append(f"artefacto citado con otro SHA-256: {key}")
        manifest = docs.get(manifest_key)
        if manifest_key not in objects:
            problems.append("input_manifest ausente")
        elif manifest is None:
            problems.append("input_manifest: sello no coincide")
        elif input_digest(manifest) != record.get("input_digest"):
            problems.append("input_manifest con otro input_digest")
        for key in objects:
            if key.startswith(f"{base}/generation/") and key not in cited:
                problems.append(f"artefacto no citado por el run_record: {key}")
        if problems:
            status = INCONSISTENT
    return {"status": status, "problems": problems, "provenance": provenance, "cited": cited}


def _analyze_snapshot(
    snapshot_id: str, objects: dict[str, tuple[str, int]], docs: dict[str, dict[str, Any] | None]
) -> dict[str, Any]:
    manifest_key = f"snapshots/{snapshot_id}/manifest.json"
    content_key = f"snapshots/{snapshot_id}/content.bin"
    manifest = docs.get(manifest_key)
    problems: list[str] = []
    provenance: dict[str, Any] = {"snapshot_id": snapshot_id}
    if manifest_key not in objects:
        problems.append("manifiesto ausente")
    elif manifest is None:
        problems.append("manifiesto: sello no coincide")
    else:
        provenance.update(
            {
                "content_sha256": manifest.get("content_sha256"),
                "captured_at": manifest.get("captured_at"),
                "source_id": manifest.get("source_id"),
                "connector": manifest.get("connector"),
            }
        )
        if manifest.get("snapshot_id") != snapshot_id:
            problems.append("el manifiesto declara otro snapshot_id")
    if content_key not in objects:
        problems.append("contenido ausente")
    elif manifest is not None and objects[content_key][0] != manifest.get("content_sha256"):
        problems.append("contenido con otro SHA-256")
    status = "SNAPSHOT_VALID" if not problems else "SNAPSHOT_INCONSISTENT"
    return {"status": status, "problems": problems, "provenance": provenance}


def _object_entry(
    key: str,
    identity: tuple[str, int],
    runs: dict[str, dict[str, Any]],
    snapshots: dict[str, dict[str, Any]],
    docs: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    sha, size = identity
    entry: dict[str, Any] = {"key": key, "kind": "UNREGISTERED_FILE", "sha256": sha, "size": size}
    run_match = _RUN_OBJECT.match(key)
    snap_match = _SNAPSHOT_OBJECT.match(key)
    if run_match:
        run_id, rest = run_match.group(1), run_match.group(2)
        run = runs[run_id]
        entry.update({"store_run_id": run_id, "label": run["status"]})
        artifact = _ARTIFACT_NAME.match(rest)
        if artifact:
            cited = run["cited"].get(key)
            ok = sha == artifact.group("sha") and (cited is None or cited["content_sha256"] == sha)
            entry.update(
                {
                    "object_type": "ARTIFACT",
                    "integrity": "VALID" if ok else "CORRUPT",
                    "provenance": {
                        **run["provenance"],
                        "contract": artifact.group("contract"),
                        "declared_sha256": artifact.group("sha"),
                        "run_record_entry": (
                            {
                                k: cited.get(k)
                                for k in ("contract", "content_sha256", "logical_hash")
                            }
                            if cited
                            else None
                        ),
                    },
                }
            )
        elif rest in ("run_record.json", "input_manifest.json"):
            entry.update(
                {
                    "object_type": "RUN_RECORD" if rest == "run_record.json" else "INPUT_MANIFEST",
                    "integrity": "VALID" if docs.get(key) is not None else "CORRUPT",
                    "provenance": run["provenance"],
                }
            )
        else:
            entry.update(
                {"object_type": "UNKNOWN_OBJECT", "integrity": "UNVERIFIABLE", "provenance": None}
            )
    elif snap_match:
        snapshot = snapshots[snap_match.group(1)]
        is_manifest = snap_match.group(2) == "manifest.json"
        if is_manifest:
            integrity = "VALID" if docs.get(key) is not None else "CORRUPT"
        else:
            declared = snapshot["provenance"].get("content_sha256")
            integrity = (
                "UNVERIFIABLE" if declared is None else ("VALID" if declared == sha else "CORRUPT")
            )
        entry.update(
            {
                "object_type": "SNAPSHOT_MANIFEST" if is_manifest else "SNAPSHOT_CONTENT",
                "snapshot_id": snap_match.group(1),
                "label": snapshot["status"],
                "integrity": integrity,
                "provenance": snapshot["provenance"],
            }
        )
    else:
        entry.update(
            {
                "object_type": "UNKNOWN_OBJECT",
                "label": "UNKNOWN_OBJECT",
                "integrity": "UNVERIFIABLE",
                "provenance": None,
            }
        )
    return entry


def reconcile(store: LocalObjectStore, repo: MetadataRepository) -> ReconcileReport:
    report = ReconcileReport()
    registered_keys = _mark_sql_artifacts(store, repo, report)
    generations = repo.list_generations()
    snapshot_refs = {
        str(r["snapshot_id"]): str(r["content_sha256"]) for r in repo.list_snapshot_refs()
    }

    objects: dict[str, tuple[str, int]] = {}
    docs: dict[str, dict[str, Any] | None] = {}
    for key in store.iter_keys():
        data = _read(store, key)
        if data is None:
            objects[key] = ("UNREADABLE", -1)
            continue
        objects[key] = (sha256_bytes(data), len(data))
        if key.endswith(".json"):
            docs[key] = _sealed(data, key)
    report.objects_scanned = len(objects)

    run_ids = {m.group(1) for k in objects if (m := _RUN_OBJECT.match(k))}
    snapshot_ids = {m.group(1) for k in objects if (m := _SNAPSHOT_OBJECT.match(k))}
    sealed = [g for g in generations if g.get("sealed_at") is not None and g.get("store_run_id")]
    registered_runs = {str(g["store_run_id"]) for g in sealed}
    registered_runs.update(m.group(1) for k in registered_keys if (m := _RUN_OBJECT.match(k)))

    runs = {
        r: _analyze_run(r, {k: v for k, v in objects.items() if k.startswith(f"runs/{r}/")}, docs)
        for r in sorted(run_ids | registered_runs)
    }
    snapshots = {
        s: _analyze_snapshot(
            s, {k: v for k, v in objects.items() if k.startswith(f"snapshots/{s}/")}, docs
        )
        for s in sorted(snapshot_ids | set(snapshot_refs))
    }
    report.runs = {
        r: {k: v for k, v in info.items() if k != "cited"} | {"registered": r in registered_runs}
        for r, info in runs.items()
    }
    report.snapshots = {
        s: info | {"registered": s in snapshot_refs} for s, info in snapshots.items()
    }

    # Referencias de SQL que no casan con el almacén (procedencia; solo informe).
    for gen in sealed:
        run = runs[str(gen["store_run_id"])]
        prov = run["provenance"]
        if run["status"] in (INCOMPLETE, CORRUPT_RECORD):
            report.sql_reference_issues.append(
                {
                    "kind": "GENERATION_RUN_RECORD_UNAVAILABLE",
                    "attempt_id": gen["attempt_id"],
                    "store_run_id": gen["store_run_id"],
                    "run_status": run["status"],
                }
            )
        elif (prov.get("input_digest"), prov.get("result_digest")) != (
            gen.get("input_digest"),
            gen.get("result_digest"),
        ):
            report.sql_reference_issues.append(
                {
                    "kind": "GENERATION_DIGEST_MISMATCH",
                    "attempt_id": gen["attempt_id"],
                    "store_run_id": gen["store_run_id"],
                }
            )
        elif run["status"] == INCONSISTENT:
            report.sql_reference_issues.append(
                {
                    "kind": "GENERATION_RUN_INCONSISTENT",
                    "attempt_id": gen["attempt_id"],
                    "store_run_id": gen["store_run_id"],
                    "problems": run["problems"],
                }
            )
    for snapshot_id, sha in sorted(snapshot_refs.items()):
        snap = snapshots[snapshot_id]
        declared = snap["provenance"].get("content_sha256")
        if snap["status"] != "SNAPSHOT_VALID" or declared != sha:
            report.sql_reference_issues.append(
                {
                    "kind": "SNAPSHOT_REF_UNMATCHED",
                    "snapshot_id": snapshot_id,
                    "registered_sha256": sha,
                    "store_sha256": declared,
                    "problems": snap["problems"],
                }
            )

    for key in sorted(objects):
        run_match = _RUN_OBJECT.match(key)
        snap_match = _SNAPSHOT_OBJECT.match(key)
        if key in registered_keys:
            continue
        if (
            run_match
            and run_match.group(1) in registered_runs
            and not _ARTIFACT_NAME.match(run_match.group(2))
        ):
            continue  # run_record / input_manifest de una corrida registrada
        if snap_match and snap_match.group(1) in snapshot_refs:
            continue
        report.unregistered_files.append(_object_entry(key, objects[key], runs, snapshots, docs))
    report.staging_leftovers = store.staging_entries()
    return report
