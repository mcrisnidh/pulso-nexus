"""A2 — fronteras entre módulos y pureza de motores y normalizadores.

Complementa a import-linter (capas internas) con un análisis AST de la biblioteca
estándar: los motores no pueden importar E/S, red, procesos, reloj ni aleatoriedad.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

SRC = ROOT / "src" / "nexus"
FORBIDDEN_STDLIB = {
    "os",
    "io",
    "pathlib",
    "socket",
    "subprocess",
    "urllib",
    "http",
    "shutil",
    "tempfile",
    "time",
    "random",
    "secrets",
    "ssl",
    "ftplib",
    "smtplib",
    "multiprocessing",
    "threading",
    "asyncio",
    "sqlite3",
    "glob",
    "fileinput",
    "requests",
    "httpx",
}
FORBIDDEN_CALLS = {"now", "today", "utcnow", "open", "getenv", "environ"}
PURE_PACKAGES = ("engines", "normalize")


def _modules(package: str) -> list[Path]:
    return sorted((SRC / package).rglob("*.py"))


def _imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


@pytest.mark.parametrize(
    "path",
    [p for pkg in PURE_PACKAGES for p in _modules(pkg)],
    ids=lambda p: p.relative_to(SRC).as_posix(),
)
def test_pure_modules_have_no_io_clock_or_network(path: Path) -> None:
    tree = ast.parse(path.read_text("utf-8"))
    bad_imports = {n for n in _imports(tree) if n.split(".")[0] in FORBIDDEN_STDLIB}
    assert not bad_imports, f"{path}: imports prohibidos {bad_imports}"
    bad_calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_CALLS:
            bad_calls.add(node.attr)
        if isinstance(node, ast.Name) and node.id in {"open", "input", "exec", "eval"}:
            bad_calls.add(node.id)
    assert not bad_calls, f"{path}: llamadas prohibidas {bad_calls}"


def test_only_pipeline_integrates_engines_and_normalizers() -> None:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).parts
        if rel[0] in ("pipeline", "engines", "normalize"):
            continue
        for name in _imports(ast.parse(path.read_text("utf-8"))):
            if name.startswith(("nexus.engines", "nexus.normalize")):
                offenders.append((path.relative_to(SRC).as_posix(), name))
    assert offenders == []


def test_no_web_layer_in_e1a() -> None:
    assert not (SRC / "web").exists()
    for path in SRC.rglob("*.py"):
        names = _imports(ast.parse(path.read_text("utf-8")))
        assert not {n for n in names if n.split(".")[0] in {"fastapi", "flask", "django"}}


def test_import_linter_contracts_pass() -> None:
    exe = Path(sys.executable).parent / (
        "lint-imports.exe" if sys.platform == "win32" else "lint-imports"
    )
    proc = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BROKEN" not in proc.stdout.upper().replace("0 BROKEN", "")
