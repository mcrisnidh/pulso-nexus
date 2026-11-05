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
    "pyodbc*",
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


def test_runtime_dependencies_are_only_pyarrow() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    deps = [
        _norm(d.split(">")[0].split("=")[0].split("<")[0].strip()) for d in project["dependencies"]
    ]
    assert deps == ["pyarrow"]


def test_source_does_not_import_forbidden_modules() -> None:
    forbidden_roots = {
        "msal",
        "msgraph",
        "office365",
        "sharepoint",
        "psycopg",
        "psycopg2",
        "pyodbc",
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
