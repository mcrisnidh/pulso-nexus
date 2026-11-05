"""A3 — dependencias prohibidas ausentes (lockfile, pyproject y código)."""

from __future__ import annotations

import ast
import fnmatch
import tomllib

from conftest import ROOT

FORBIDDEN = (
    "msal*",
    "msgraph*",
    "office365*",
    "sharepoint*",
    "psycopg*",
    "sqlalchemy*",
    "pymssql*",
    "azure-identity*",
    "o365*",
)


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def test_lockfile_has_no_forbidden_packages() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text("utf-8"))
    names = {_norm(p["name"]) for p in lock["package"]}
    hits = sorted(n for n in names for pat in FORBIDDEN if fnmatch.fnmatch(n, pat))
    assert hits == []
    assert "pyarrow" in names


def test_runtime_dependencies_are_pyarrow_and_pyodbc() -> None:
    """E1-A: solo pyarrow. E1-B (B2) añade únicamente pyodbc, restringido a nexus.metadata."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    deps = [
        _norm(d.split(">")[0].split("=")[0].split("<")[0].strip()) for d in project["dependencies"]
    ]
    assert deps == ["pyarrow", "pyodbc"]


def test_source_does_not_import_forbidden_modules() -> None:
    forbidden_roots = {
        "msal",
        "msgraph",
        "office365",
        "sharepoint",
        "psycopg",
        "psycopg2",
        "sqlalchemy",
        "pymssql",
        "sqlite3",
    }
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert not roots & forbidden_roots, f"{path}: {roots & forbidden_roots}"


def test_only_metadata_imports_pyodbc() -> None:
    """B2: el único paquete que importa pyodbc (directamente) es nexus.metadata."""
    offenders = []
    for path in (ROOT / "src" / "nexus").rglob("*.py"):
        rel = path.relative_to(ROOT / "src" / "nexus").parts
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            names: set[str] = set()
            if isinstance(node, ast.Import):
                names = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = {node.module.split(".")[0]}
            if "pyodbc" in names and rel[0] != "metadata":
                offenders.append("/".join(rel))
    assert offenders == []
