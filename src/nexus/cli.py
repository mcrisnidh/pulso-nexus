"""CLI de PULSO NEXUS (E1-A).

Comandos:
  nexus snapshot --store DIR --path FILE [--source-id ID]
  nexus run      --store DIR --snapshot ID --as-of YYYY-MM-DDTHH:MM:SSZ [--param k=v]...
                 [--threads N] [--trigger manual|scheduled]
  nexus replay   --store DIR --run-id ID [--level L1|L2|L3] [--threads N]
  nexus verify   --store DIR --all

La ejecución manual y la programada usan exactamente el mismo comando ``run``.
El planificador solo debe invocarlo con ``--trigger scheduled``.

Códigos de salida: 0 OK · 1 error de uso o configuración · 2 nivel de replay
insuficiente · 3 integridad · 4 no reproducible · 5 verificación con fallos.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Any

from nexus.connectors import synthetic_file
from nexus.errors import ConfigurationError, IntegrityError, NexusError, NotReproducibleError
from nexus.pipeline.compare import LEVELS, level_rank
from nexus.pipeline.replay import replay
from nexus.pipeline.run import execute
from nexus.pipeline.verify import verify_store
from nexus.snapshots.capture import create_snapshot
from nexus.storage.object_store import LocalObjectStore

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_LEVEL = 2
EXIT_INTEGRITY = 3
EXIT_NOT_REPRODUCIBLE = 4
EXIT_VERIFY = 5


def _emit(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _threads(value: int | None) -> int:
    if value is not None:
        return value
    raw = os.environ.get("NEXUS_THREADS", "1")
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"NEXUS_THREADS inválido: {raw!r}") from exc


def _params(pairs: Sequence[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ConfigurationError(f"parámetro inválido (se espera k=v): {pair!r}")
        if key in params:
            raise ConfigurationError(f"parámetro repetido: {key}")
        params[key] = value
    return params


def _cmd_snapshot(args: argparse.Namespace) -> int:
    store = LocalObjectStore(args.store)
    capture = synthetic_file.capture(args.path, source_id=args.source_id)
    manifest = create_snapshot(store, capture)
    _emit(
        {
            "snapshot_id": manifest["snapshot_id"],
            "content_sha256": manifest["content_sha256"],
            "size": manifest["size"],
        }
    )
    return EXIT_OK


def _cmd_run(args: argparse.Namespace) -> int:
    store = LocalObjectStore(args.store)
    record = execute(
        store,
        snapshot_id=args.snapshot,
        as_of=args.as_of,
        params=_params(args.param),
        threads=_threads(args.threads),
        trigger=args.trigger,
    )
    _emit(
        {
            "run_id": record["run_id"],
            "input_digest": record["input_digest"],
            "result_digest": record["result_digest"],
            "counts": record["counts"],
            "artifacts": [
                {k: a[k] for k in ("contract", "content_sha256", "logical_hash", "rows")}
                for a in record["artifacts"]
            ],
        }
    )
    return EXIT_OK


def _cmd_replay(args: argparse.Namespace) -> int:
    store = LocalObjectStore(args.store)
    report = replay(store, args.run_id, threads=_threads(args.threads))
    payload = report.as_dict()
    payload["required_level"] = args.level
    payload["meets_required_level"] = level_rank(report.level) >= level_rank(args.level)
    _emit(payload)
    return EXIT_OK if payload["meets_required_level"] else EXIT_LEVEL


def _cmd_verify(args: argparse.Namespace) -> int:
    store = LocalObjectStore(args.store)
    report = verify_store(store)
    _emit(
        {
            "ok": report.ok,
            "snapshots_ok": report.snapshots_ok,
            "runs_ok": report.runs_ok,
            "objects_verified": report.objects_verified,
            "failures": report.failures,
            "incomplete_runs": report.incomplete_runs,
            "unreferenced_objects": report.unreferenced_objects,
            "staging_leftovers": report.staging_leftovers,
        }
    )
    return EXIT_OK if report.ok else EXIT_VERIFY


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nexus", description="PULSO NEXUS — núcleo E1-A")
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snapshot", help="captura un archivo sintético como snapshot")
    p_snap.add_argument("--store", required=True)
    p_snap.add_argument("--path", required=True)
    p_snap.add_argument("--source-id", default="synthetic")
    p_snap.set_defaults(func=_cmd_snapshot)

    p_run = sub.add_parser("run", help="ejecuta el pipeline sobre un snapshot")
    p_run.add_argument("--store", required=True)
    p_run.add_argument("--snapshot", required=True)
    p_run.add_argument("--as-of", required=True)
    p_run.add_argument("--param", action="append", default=[])
    p_run.add_argument("--threads", type=int, default=None)
    p_run.add_argument("--trigger", choices=["manual", "scheduled"], default="manual")
    p_run.set_defaults(func=_cmd_run)

    p_rep = sub.add_parser("replay", help="recalcula una corrida y compara por niveles")
    p_rep.add_argument("--store", required=True)
    p_rep.add_argument("--run-id", required=True)
    p_rep.add_argument("--level", choices=list(LEVELS[1:]), default="L3")
    p_rep.add_argument("--threads", type=int, default=None)
    p_rep.set_defaults(func=_cmd_replay)

    p_ver = sub.add_parser("verify", help="verifica la integridad del almacén")
    p_ver.add_argument("--store", required=True)
    p_ver.add_argument("--all", action="store_true", required=True)
    p_ver.set_defaults(func=_cmd_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except IntegrityError as exc:
        _emit({"error": "integrity", "detail": str(exc)})
        return EXIT_INTEGRITY
    except NotReproducibleError as exc:
        _emit({"error": "not_reproducible", "detail": str(exc)})
        return EXIT_NOT_REPRODUCIBLE
    except (NexusError, OSError, ValueError) as exc:
        _emit({"error": type(exc).__name__, "detail": str(exc)})
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
