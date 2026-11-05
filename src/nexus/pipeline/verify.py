"""Verificación integral del almacén (``nexus verify --all``).

- Fallos: manifiestos alterados, contenidos con hash distinto, artefactos ausentes.
- Avisos: corridas incompletas (sin ``run_record``), objetos no referenciados y
  restos de staging. Ninguno de ellos es referenciado por un manifiesto válido.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from nexus.errors import IntegrityError, NexusError
from nexus.pipeline.replay import load_run
from nexus.pipeline.run import run_keys
from nexus.snapshots.capture import load_snapshot, read_manifest
from nexus.storage.object_store import LocalObjectStore

_SNAP = re.compile(r"^snapshots/(snap-[0-9a-f]{32})/manifest\.json$")
_RUN_DIR = re.compile(r"^runs/(run-[0-9a-f]{32})/")


@dataclass
class VerifyReport:
    snapshots_ok: int = 0
    runs_ok: int = 0
    objects_verified: int = 0
    failures: list[str] = field(default_factory=list)
    incomplete_runs: list[str] = field(default_factory=list)
    unreferenced_objects: list[str] = field(default_factory=list)
    staging_leftovers: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def verify_store(store: LocalObjectStore) -> VerifyReport:
    report = VerifyReport()
    keys = list(store.iter_keys())
    referenced: set[str] = set()

    for key in keys:
        match = _SNAP.match(key)
        if not match:
            continue
        snapshot_id = match.group(1)
        try:
            manifest = read_manifest(store, snapshot_id)
            load_snapshot(store, snapshot_id, str(manifest["content_sha256"]))
        except (NexusError, KeyError) as exc:
            report.failures.append(f"snapshot {snapshot_id}: {exc}")
            continue
        referenced.update({key, str(manifest["content_key"])})
        report.snapshots_ok += 1
        report.objects_verified += 2

    run_ids = sorted({m.group(1) for k in keys if (m := _RUN_DIR.match(k))})
    failed_runs: list[str] = []
    for run_id in run_ids:
        rk = run_keys(run_id)
        if rk["run_record"] not in keys:
            report.incomplete_runs.append(run_id)
            continue
        try:
            record, _ = load_run(store, run_id)
            for art in record["artifacts"]:
                try:
                    store.verify(art["key"], art["content_sha256"])
                except FileNotFoundError as exc:
                    raise IntegrityError(f"artefacto ausente {art['key']}") from exc
                referenced.add(art["key"])
                report.objects_verified += 1
        except (NexusError, KeyError) as exc:
            report.failures.append(f"run {run_id}: {type(exc).__name__}: {exc}")
            failed_runs.append(run_id)
            continue
        referenced.update({rk["run_record"], rk["input_manifest"]})
        report.runs_ok += 1
        report.objects_verified += 2

    # Los objetos de corridas incompletas o fallidas ya se informan en su propia categoría.
    owned_prefixes = tuple(f"runs/{r}/" for r in (*report.incomplete_runs, *failed_runs))
    report.unreferenced_objects = [
        k for k in keys if k not in referenced and not k.startswith(owned_prefixes)
    ]
    report.staging_leftovers = store.staging_entries()
    return report
