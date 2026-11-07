"""Verificación nativa controlada de R1/R3, Frozen definitivo, stage() y RefreshService.

Pensada para ASMXC1200 (Windows), funciona también en Linux. SOLO datos sintéticos y
directorios temporales bajo ``--lab``. No conecta SQL, no importa ni publica datos reales y no
escribe en el repositorio.

Comprueba:
  A. Entorno: código bajo prueba (``src/``, ``tests/``) idéntico a la referencia, árbol limpio,
     ``uv.lock`` esperado, Python y tzdata.
  B. Suites: ``pytest -m "not sql"`` completa (incluye la batería R1/R3, el Frozen definitivo,
     T1–T5, la huella dorada y la portabilidad) y extracción explícita de los casos clave.
  C. Aislamiento NATIVO de ``stage()``: junctions de Windows (sin privilegios) y enlaces
     simbólicos (si el sistema los permite) en cada componente; escapes; ``store/`` previo;
     salida dentro del repositorio; escrituras fuera del directorio de datos.

Uso (PowerShell, desde el repositorio):
  uv run --frozen --with tzdata==2025.2 python scripts\\core_win_native_check.py --lab <LAB>

Salida: ``<LAB>\\win_native_<fecha>\\informe.json`` (saneado: códigos y conteos, sin rutas
absolutas ni datos) y los ``junit-*.xml`` de pytest. Código de salida 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import traceback
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

REFERENCE = "d98e4d3b26422a69435487a7a817917c9384ac53"
UV_LOCK_SHA256 = "7d6889a8f2ca70be7ad2566cfdfb4112ed8e0e08a9aa36cca2fe7b36a1e27534"
KEY_TESTS = {
    "unknown_bloquea_publicacion": "test_t1_unknown_customer_blocks_official_publication",
    "pendiente_r1_bloquea_publicacion": "test_t1_pending_r1_blocks_official_publication",
    "clasificado_publica": "test_t1_all_classified_publishes",
    "v1_rechazado_con_fuentes_reales": "test_t2_v1_rules_rejected_with_real_sources_in_any_channel",
    "v1_solo_sintetico_aislado": "test_t2_v1_rules_only_in_isolated_synthetic_channel",
    "externos_no_cambian_resultados": (
        "test_t3_external_items_in_current_sources_never_change_official_results"
    ),
    "huella_dorada_historica": "test_tz18",
    "huella_dorada_escritura_windows": "test_p01_golden_digest_with_windows_style_text_writes",
    "stage_escribe_solo_en_datos": "test_t5_stage_writes_only_inside_private_data_dir",
    "frozen_definitivo_solo_cat_sale": "test_pure_frozen_controls[F1_",
    "r1_estado_por_linea_y_solo_portal": (
        "test_t4_r1_statuses_per_line_and_portal_only_enters_universe"
    ),
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


# ------------------------------------------------------------------ A. entorno
def environment(repo: Path) -> dict[str, Any]:
    head = git(repo, "rev-parse", "HEAD").strip()
    code_diff = git(repo, "diff", "--stat", REFERENCE, "--", "src", "tests", "pyproject.toml")
    try:
        import tzdata  # type: ignore[import-untyped]  # noqa: PLC0415

        tz_version = str(getattr(tzdata, "IANA_VERSION", "?"))
    except ImportError:
        tz_version = None
    lock = hashlib.sha256((repo / "uv.lock").read_bytes()).hexdigest()
    env = {
        "head": head,
        "code_identical_to_reference": code_diff.strip() == "",
        "worktree_clean": git(repo, "status", "--porcelain", "--untracked-files=no").strip() == "",
        "uv_lock_expected": lock == UV_LOCK_SHA256,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        "tzdata": tz_version,
        "path_is_junction_api": hasattr(Path, "is_junction"),
    }
    env["ok"] = all(
        (
            env["code_identical_to_reference"],
            env["worktree_clean"],
            env["uv_lock_expected"],
            str(env["python"]).startswith("3.12"),
            env["path_is_junction_api"],
            os.name != "nt" or tz_version is not None,
        )
    )
    return env


# ------------------------------------------------------------------ B. suites
def run_pytest(repo: Path, out: Path) -> dict[str, Any]:
    junit = out / "junit-not-sql.xml"
    before = git(repo, "status", "--porcelain", "--untracked-files=all")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "not sql",
            "-p",
            "no:cacheprovider",
            "-q",
            f"--junitxml={junit}",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    (out / "pytest-not-sql.txt").write_text(proc.stdout[-20000:] + proc.stderr[-5000:], "utf-8")
    after = git(repo, "status", "--porcelain", "--untracked-files=all")
    cases: dict[str, str] = {}
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for case in ET.parse(junit).getroot().iter("testcase"):  # noqa: S314 (junit propio)
        name = f"{case.get('classname')}::{case.get('name')}"
        status = "passed"
        for tag in ("failure", "error", "skipped"):
            if case.find(tag) is not None:
                status = tag
        cases[name] = status
        totals["tests"] += 1
        if status != "passed":
            totals[{"failure": "failures", "error": "errors", "skipped": "skipped"}[status]] += 1
    key: dict[str, str] = {}
    for label, needle in KEY_TESTS.items():
        found = [s for n, s in cases.items() if needle in n]
        key[label] = (
            "MISSING" if not found else "PASS" if all(s == "passed" for s in found) else "FAIL"
        )
    skipped = sorted(n.split("::")[-1] for n, s in cases.items() if s == "skipped")
    return {
        "returncode": proc.returncode,
        **totals,
        "passed": totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"],
        "failed_cases": sorted(n for n, s in cases.items() if s in ("failure", "error")),
        "skipped_cases": skipped,
        "key_cases": key,
        "repo_unchanged_by_tests": before == after,
        "ok": proc.returncode == 0
        and totals["failures"] == 0
        and totals["errors"] == 0
        and before == after
        and all(v == "PASS" for v in key.values()),
    }


# ------------------------------------------------------------------ C. aislamiento nativo
def fingerprint(root: Path, skip: Path | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if skip is not None and (p == skip or skip in p.parents):
            continue
        rel = p.relative_to(root).as_posix()
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            out[rel] = "link"
        elif p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        else:
            out[rel] = "dir"
    return out


def make_junction(link: Path, target: Path) -> None:
    import _winapi  # noqa: PLC0415

    _winapi.CreateJunction(str(target), str(link))  # type: ignore[attr-defined]


def symlinks_available(base: Path) -> bool:
    probe = base / "_symlink_probe"
    try:
        os.symlink(base, probe, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


def isolation(repo: Path, out: Path) -> dict[str, Any]:  # noqa: PLR0915
    sys.path.insert(0, str(repo / "tests" / "core"))
    import physical_fixtures as fx  # type: ignore[import-not-found]  # noqa: PLC0415

    from nexus.core.stage import stage  # noqa: PLC0415
    from nexus.errors import ConfigurationError  # noqa: PLC0415

    def clock() -> dt.datetime:
        return dt.datetime(2026, 11, 7, 18, 0, tzinfo=dt.UTC)

    lab_root = out / "aislamiento"
    lab_root.mkdir()
    is_windows = os.name == "nt"
    can_symlink = symlinks_available(lab_root)
    results: dict[str, dict[str, Any]] = {}
    counter = [0]

    def fresh() -> tuple[Path, dict[str, Path], Path]:
        counter[0] += 1
        base = lab_root / f"caso{counter[0]:02d}"
        base.mkdir()
        outside = base / "FUERA_DEL_LABORATORIO"
        outside.mkdir()
        (outside / "centinela.txt").write_bytes(b"no debe cambiar\n")
        return base, fx.lab(base / "lab"), outside

    def refused(
        name: str,
        prepare: Callable[[dict[str, Path], Path, Path], Path | None],
        kind: str,
        match: str,
    ) -> None:
        if kind == "junction" and not is_windows:
            results[name] = {"status": "NOT_APPLICABLE", "reason": "junctions solo en Windows"}
            return
        if kind == "symlink" and not can_symlink:
            results[name] = {
                "status": "NOT_AVAILABLE",
                "reason": "el sistema no permite crear enlaces simbólicos sin privilegio",
            }
            return
        base, lab, outside = fresh()
        data = prepare(lab, outside, base) or lab["data"]
        before_out = fingerprint(outside)
        before_base = fingerprint(base, skip=data)
        before_data = fingerprint(data) if data.exists() and not data.is_symlink() else None
        try:
            stage(lab["src"], data, clock=clock, **fx.r1(lab))
            outcome = "ACCEPTED"
            message = ""
        except ConfigurationError as exc:
            outcome = "REFUSED"
            message = str(exc)
        ok = (
            outcome == "REFUSED"
            and match in message
            and fingerprint(outside) == before_out
            and fingerprint(base, skip=data) == before_base
            # nada nuevo en el directorio de datos (sin seguir enlaces)
            and (before_data is None or fingerprint(data) == before_data)
        )
        results[name] = {
            "status": "PASS" if ok else "FAIL",
            "outcome": outcome,
            "expected_message": match,
            "message_matches": match in message,
            "outside_unchanged": fingerprint(outside) == before_out,
        }

    def link(kind: str) -> Callable[[Path, Path], None]:
        if kind == "junction":
            return make_junction
        return lambda lk, tg: lk.symlink_to(tg, target_is_directory=tg.is_dir())

    for kind in ("junction", "symlink"):
        mk = link(kind)
        for sub in ("inbox", "originals", "provenance"):

            def prep(
                lab: dict[str, Path],
                outside: Path,
                _b: Path,
                sub: str = sub,
                mk: Callable[[Path, Path], None] = mk,
            ) -> None:
                mk(lab["data"] / sub, outside)

            refused(f"{kind}_{sub}", prep, kind, "enlace")

        def nested(
            lab: dict[str, Path], outside: Path, _b: Path, mk: Callable[[Path, Path], None] = mk
        ) -> None:
            (lab["data"] / "inbox").mkdir()
            mk(lab["data"] / "inbox" / "oeras_29", outside)

        refused(f"{kind}_anidado_inbox_fuente", nested, kind, "enlace")

        def data_link(
            lab: dict[str, Path], outside: Path, base: Path, mk: Callable[[Path, Path], None] = mk
        ) -> Path:
            target = outside / "datos_reales"
            target.mkdir()
            shutil.copy2(lab["data"] / "rules.json", target / "rules.json")
            shutil.copy2(lab["data"] / "operation.json", target / "operation.json")
            link_dir = base / "datos_enlazados"
            mk(link_dir, target)
            return link_dir

        refused(f"{kind}_directorio_de_datos", data_link, kind, "enlace")

        def parent_link(
            lab: dict[str, Path], outside: Path, base: Path, mk: Callable[[Path, Path], None] = mk
        ) -> Path:
            mk(base / "padre_enlazado", outside)
            return base / "padre_enlazado" / "datos"

        refused(f"{kind}_componente_padre", parent_link, kind, "enlace")

        def src_link(
            lab: dict[str, Path], outside: Path, _b: Path, mk: Callable[[Path, Path], None] = mk
        ) -> None:
            mk(lab["src"] / "R_20261102_enlace", outside)

        refused(f"{kind}_en_origen", src_link, kind, "enlazado")

    def store_prev(lab: dict[str, Path], _o: Path, _b: Path) -> None:
        (lab["data"] / "store").mkdir()

    refused("store_previo", store_prev, "plain", "store")

    def in_repo(lab: dict[str, Path], _o: Path, _b: Path) -> Path:
        return repo / "tmp-verificacion-no-debe-existir"

    repo_before = git(repo, "status", "--porcelain", "--untracked-files=all")
    refused("salida_dentro_del_repositorio", in_repo, "plain", "repositorio git")
    results["salida_dentro_del_repositorio"]["repo_unchanged"] = (
        git(repo, "status", "--porcelain", "--untracked-files=all") == repo_before
        and not (repo / "tmp-verificacion-no-debe-existir").exists()
    )

    # alcance de escritura en un proceso aislado (HOME/TEMP/APPDATA dirigidos al laboratorio)
    base, lab, _outside = fresh()
    env = dict(os.environ)
    for var in ("HOME", "USERPROFILE", "TMP", "TEMP", "TMPDIR", "LOCALAPPDATA", "APPDATA"):
        env[var] = str(base)
    before = fingerprint(base, skip=lab["data"])
    before_repo = git(repo, "status", "--porcelain", "--untracked-files=all")
    data_before = set(fingerprint(lab["data"]))
    code = (
        "import sys, datetime as dt; from pathlib import Path\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import physical_fixtures as fx\n"
        "from nexus.core.stage import stage\n"
        "keys = ('src', 'data', 'homologation', 'ledger', 'approvers')\n"
        "lab = {k: Path(v) for k, v in zip(keys, sys.argv[2:])}\n"
        "at = dt.datetime(2026, 11, 7, 18, tzinfo=dt.UTC)\n"
        "stage(lab['src'], lab['data'], clock=lambda: at, **fx.r1(lab))\n"
    )
    keys = ("src", "data", "homologation", "ledger", "approvers")
    proc = subprocess.run(
        [sys.executable, "-c", code, str(repo / "tests" / "core"), *(str(lab[k]) for k in keys)],
        cwd=base,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    created = set(fingerprint(lab["data"])) - data_before
    results["alcance_de_escritura"] = {
        "status": "PASS"
        if proc.returncode == 0
        and fingerprint(base, skip=lab["data"]) == before
        and created
        and all(p.split("/")[0] in ("originals", "inbox", "provenance") for p in created)
        and git(repo, "status", "--porcelain", "--untracked-files=all") == before_repo
        else "FAIL",
        "returncode": proc.returncode,
        "created_top_dirs": sorted({p.split("/")[0] for p in created}),
    }
    statuses = [r["status"] for r in results.values()]
    return {
        "windows": is_windows,
        "symlinks_available": can_symlink,
        "cases": results,
        "ok": "FAIL" not in statuses
        and (not is_windows or any(k.startswith("junction") for k in results)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", required=True, help="directorio de laboratorio FUERA del repositorio")
    ap.add_argument("--repo", default=".")
    a = ap.parse_args(argv)
    repo = Path(a.repo).resolve()
    lab = Path(a.lab).resolve()
    if repo == lab or repo in lab.parents:
        print("ERROR: --lab debe estar fuera del repositorio", file=sys.stderr)
        return 2
    lab.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix=f"win_native_{dt.date.today():%Y%m%d}_", dir=lab))
    report: dict[str, Any] = {
        "schema": "nexus.core.win-native-check/1",
        "reference": REFERENCE,
        "notice": "solo datos sintéticos; informe saneado (sin rutas absolutas ni datos)",
    }
    sections: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("A_entorno", lambda: environment(repo)),
        ("B_suites", lambda: run_pytest(repo, out)),
        ("C_aislamiento_nativo", lambda: isolation(repo, out)),
    ]
    for name, fn in sections:
        try:
            report[name] = fn()
        except Exception as exc:  # se informa como FAIL, nunca se oculta
            report[name] = {"ok": False, "exception": type(exc).__name__, "detail": str(exc)}
            (out / f"{name}-traceback.txt").write_text(traceback.format_exc(), "utf-8")
    report["dictamen"] = "PASS" if all(report[n]["ok"] for n, _ in sections) else "FAIL"
    text = json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True)
    (out / "informe.json").write_text(text + "\n", "utf-8")
    print(text)
    print(f"\nInforme: {out.name}{os.sep}informe.json  ->  DICTAMEN {report['dictamen']}")
    return 0 if report["dictamen"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
