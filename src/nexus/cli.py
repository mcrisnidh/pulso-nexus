"""CLI de PULSO NEXUS (E1-A).

Comandos:
  nexus snapshot --store DIR --path FILE [--source-id ID]
  nexus run      --store DIR --snapshot ID --as-of YYYY-MM-DDTHH:MM:SSZ [--param k=v]...
                 [--threads N] [--trigger manual|scheduled]
  nexus replay   --store DIR --run-id ID [--level L1|L2|L3] [--threads N]
  nexus verify   --store DIR --all
  nexus planning --dataset DIR --rules FILE --out DIR [--layout FILE] [--item ITEM]...
  nexus core init-demo|refresh|status|rollback --data DIR --channel CANAL ...   (CORE v1.0)
  nexus web --data DIR --channel CANAL [--port N] [--simulate-start ISO]       (solo loopback)

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
from nexus.snapshots.capture import create_snapshot, read_manifest
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


# --------------------------------------------------------------------------- E1-B (SQL Server)
def _sql_repo(actor: str) -> Any:
    """Repositorio SQL con credenciales efímeras del entorno; el guard de runtime se aplica al
    abrir la conexión, antes de cualquier escritura."""
    from nexus.metadata.connection import settings_from_env  # noqa: PLC0415
    from nexus.metadata.profile import load_profile  # noqa: PLC0415
    from nexus.metadata.sqlserver import SqlServerMetadata  # noqa: PLC0415

    profile = load_profile(os.environ.get("NEXUS_PROFILE", "e1b-ephemeral"))
    settings = settings_from_env("NEXUS_SQL_PRODUCER_USER", "NEXUS_SQL_PRODUCER_PASSWORD")
    nonce = os.environ.get("NEXUS_SQL_NONCE", "")
    return profile, lambda: SqlServerMetadata(settings, profile, nonce, actor=actor)


def _cmd_enqueue(args: argparse.Namespace) -> int:
    store = LocalObjectStore(args.store)
    snap = read_manifest(store, args.snapshot)
    profile, factory = _sql_repo("cli-enqueue")
    repo = factory()
    try:
        payload = {
            "snapshot_id": args.snapshot,
            "snapshot_sha256": snap["content_sha256"],
            "as_of": args.as_of,
            "params": _params(args.param),
            "trigger": args.trigger,
        }
        request_id, created = repo.enqueue(
            args.key, args.scope, args.trigger, payload, profile.max_attempts
        )
    finally:
        repo.close()
    _emit({"request_id": request_id, "created": created})
    return EXIT_OK


def _cmd_work(args: argparse.Namespace) -> int:
    from nexus.pipeline.worker import work_once  # noqa: PLC0415

    store = LocalObjectStore(args.store)
    profile, factory = _sql_repo(args.worker)
    repo, beat = factory(), factory()
    outcomes = []
    try:
        for _ in range(args.max_iterations):
            outcome = work_once(store, repo, beat, profile, args.worker)
            outcomes.append(outcome.__dict__)
            if outcome.status == "IDLE":
                break
    finally:
        repo.close()
        beat.close()
    _emit({"outcomes": outcomes})
    return EXIT_OK


def _cmd_reconcile(args: argparse.Namespace) -> int:
    from nexus.pipeline.reconcile import reconcile  # noqa: PLC0415

    store = LocalObjectStore(args.store)
    _, factory = _sql_repo("cli-reconcile")
    repo = factory()
    try:
        report = reconcile(store, repo)
    finally:
        repo.close()
    _emit(report.__dict__)
    return EXIT_OK


def _cmd_planning(args: argparse.Namespace) -> int:
    from pathlib import Path  # noqa: PLC0415

    from nexus.planning.runner import run as planning_run  # noqa: PLC0415

    out = Path(args.out).resolve()
    repo_root = Path(__file__).resolve().parents[2]
    if not args.allow_out_in_repo and out.is_relative_to(repo_root):
        raise ConfigurationError(
            f"--out {out} está dentro del repositorio: las salidas derivadas de datos reales "
            "deben quedar fuera (use --allow-out-in-repo solo con datos sintéticos)"
        )
    summary = planning_run(
        Path(args.dataset),
        Path(args.rules),
        out,
        Path(args.layout) if args.layout else None,
        args.item or None,
    )
    _emit(summary)
    return EXIT_OK


LOOPBACK = ("127.0.0.1", "::1", "localhost")


def _core_clock(args: argparse.Namespace) -> Any:
    import datetime as dt  # noqa: PLC0415

    start = getattr(args, "now", None) or getattr(args, "simulate_start", None)
    if not start:
        return None
    fixed = dt.datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    origin = dt.datetime.now(dt.UTC)
    return lambda: fixed + (dt.datetime.now(dt.UTC) - origin)


def _cmd_core(args: argparse.Namespace) -> int:
    from pathlib import Path  # noqa: PLC0415

    from nexus.core.refresh import RefreshService  # noqa: PLC0415

    data = Path(args.data)
    if args.core_command == "init-demo":
        from nexus.core.demo import scenario  # noqa: PLC0415

        paths = scenario(data)
        _emit({"data": str(data), "rules": str(paths["rules"]), "users": str(paths["users"])})
        return EXIT_OK
    clock = _core_clock(args)
    service = RefreshService(
        data, args.channel, data / "rules.json", **({"clock": clock} if clock else {})
    )
    if args.core_command == "refresh":
        result = service.refresh(args.actor, args.month)
        _emit(
            {"request": result["request"], "outcomes": result["outcomes"], "head": result["head"]}
        )
    elif args.core_command == "status":
        _emit(
            {
                "head": service.state.publication_head(args.channel),
                "frozen": service.state.list_frozen(args.channel),
                "chain_problems": service.state.verify_publication_chain(args.channel),
            }
        )
    else:
        _emit(service.rollback(args.to, args.actor))
    return EXIT_OK


def _cmd_core_probe(args: argparse.Namespace) -> int:
    from pathlib import Path  # noqa: PLC0415

    from nexus.core.stage import probe  # noqa: PLC0415

    report = probe(
        Path(args.file),
        kind=args.kind,
        layout=_physical_layout(args.layout),
        business_tz=args.tz,
        show_headers=args.show_headers,
        keys_out=Path(args.keys_out) if args.keys_out else None,
    )
    if args.out:
        from nexus.core.stage import write_exclusive  # noqa: PLC0415

        write_exclusive(Path(args.out), _json_bytes(report))
        _emit({"probe": args.out, "valid": report["structure"]["valid"]})
    else:
        _emit(report)
    return EXIT_OK


def _cmd_core_stage(args: argparse.Namespace) -> int:
    from pathlib import Path  # noqa: PLC0415

    from nexus.core.stage import stage  # noqa: PLC0415

    report = stage(
        Path(args.src),
        Path(args.data),
        homologation_path=Path(args.homologation) if args.homologation else None,
        layout=_physical_layout(args.layout),
        ledger_path=Path(args.ledger) if args.ledger else None,
        approvers_path=Path(args.approvers) if args.approvers else None,
    )
    _emit(report)
    return EXIT_OK


def _physical_layout(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    from pathlib import Path  # noqa: PLC0415

    data: dict[str, Any] = json.loads(Path(path).read_bytes().decode("utf-8"))
    if data.get("schema") != "nexus.core.physical-layout/1":
        raise ConfigurationError("layout físico: esquema distinto de nexus.core.physical-layout/1")
    return data


def _json_bytes(doc: Any) -> bytes:
    return (json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _cmd_web(args: argparse.Namespace) -> int:
    from pathlib import Path  # noqa: PLC0415

    if args.host not in LOOPBACK:
        raise ConfigurationError(
            f"--host {args.host!r} rechazado: el prototipo solo escucha en loopback (127.0.0.1/::1)"
        )
    from nexus.web import create_app  # noqa: PLC0415

    secret = os.environ.get("NEXUS_WEB_SECRET", "")
    app = create_app(Path(args.data), args.channel, secret, clock=_core_clock(args))
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915 (declaración lineal de la CLI)
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
    p_enq = sub.add_parser("enqueue", help="E1-B: encola una solicitud (idempotente por --key)")
    p_enq.add_argument("--store", required=True)
    p_enq.add_argument("--snapshot", required=True)
    p_enq.add_argument("--as-of", required=True)
    p_enq.add_argument("--scope", required=True)
    p_enq.add_argument("--key", required=True)
    p_enq.add_argument("--param", action="append", default=[])
    p_enq.add_argument("--trigger", choices=["manual", "scheduled"], default="manual")
    p_enq.set_defaults(func=_cmd_enqueue)

    p_work = sub.add_parser("work", help="E1-B: procesa la cola (mismo camino manual/programado)")
    p_work.add_argument("--store", required=True)
    p_work.add_argument("--worker", default="cli-worker")
    p_work.add_argument("--max-iterations", type=int, default=1)
    p_work.set_defaults(func=_cmd_work)

    p_rec = sub.add_parser("reconcile", help="E1-B: reconcilia SQL y almacén (solo informe)")
    p_rec.add_argument("--store", required=True)
    p_rec.set_defaults(func=_cmd_reconcile)

    p_plan = sub.add_parser("planning", help="PILOT-20: vista integrada por ITEM (offline)")
    p_plan.add_argument("--dataset", required=True, help="directorio del dataset extraído")
    p_plan.add_argument("--rules", required=True, help="archivo de reglas (nexus.planning.rules/1)")
    p_plan.add_argument("--out", required=True, help="directorio de salida (fuera del repo)")
    p_plan.add_argument("--layout", default=None, help="layout de fuentes (por defecto pilot20_v1)")
    p_plan.add_argument("--item", action="append", default=[], help="limitar a uno o más ITEM")
    p_plan.add_argument("--allow-out-in-repo", action="store_true")
    p_plan.set_defaults(func=_cmd_planning)

    p_core = sub.add_parser("core", help="CORE v1.0: Frozen, Refresh y publicación (offline)")
    core_sub = p_core.add_subparsers(dest="core_command", required=True)
    for name in ("init-demo", "refresh", "status", "rollback"):
        cp = core_sub.add_parser(name)
        cp.add_argument("--data", required=True)
        cp.add_argument(
            "--channel", default="sintetico", choices=["sintetico", "replay", "operativo"]
        )
        cp.add_argument("--now", default=None, help="reloj simulado (ISO UTC) para la demo")
        cp.add_argument("--actor", default="cli")
        if name == "refresh":
            cp.add_argument("--month", default=None)
        if name == "rollback":
            cp.add_argument("--to", required=True)
        cp.set_defaults(func=_cmd_core)

    cp = core_sub.add_parser("probe", help="inspección SANEADA de un archivo real (solo lectura)")
    cp.add_argument("--file", required=True)
    cp.add_argument("--kind", choices=["oe965", "portal", "in092u"], default=None)
    cp.add_argument("--layout", default=None, help="layout físico ajustado (JSON)")
    cp.add_argument("--tz", default=None, help="zona de la planta para el corte IN092U")
    cp.add_argument("--show-headers", action="store_true", help="nombres de columnas del Portal")
    cp.add_argument("--keys-out", default=None, help="claves de cliente → archivo local privado")
    cp.add_argument("--out", default=None, help="guardar el informe (no reemplaza)")
    cp.set_defaults(func=_cmd_core_probe)
    cp = core_sub.add_parser("stage", help="prepara fuentes reales en un directorio privado")
    cp.add_argument("--src", required=True, help="carpeta con las copias autorizadas")
    cp.add_argument("--data", required=True, help="directorio de datos privado (fuera de git)")
    cp.add_argument("--homologation", default=None, help="tabla ITEM Portal → ERP aprobada")
    cp.add_argument("--ledger", default=None, help="registro autorizado de aprobaciones (privado)")
    cp.add_argument("--approvers", default=None, help="lista privada de aprobadores")
    cp.add_argument("--layout", default=None, help="layout físico ajustado (JSON)")
    cp.set_defaults(func=_cmd_core_stage)

    p_web = sub.add_parser("web", help="CORE v1.0: web mínima (solo loopback, sin debug)")
    p_web.add_argument("--data", required=True)
    p_web.add_argument(
        "--channel", default="sintetico", choices=["sintetico", "replay", "operativo"]
    )
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=8765)
    p_web.add_argument("--simulate-start", default=None, help="reloj simulado (ISO UTC)")
    p_web.set_defaults(func=_cmd_web)
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
