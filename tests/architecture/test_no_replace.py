"""§10.2 — ningún código de NEXUS usa operaciones que puedan sustituir un destino.

- ``os.replace`` y ``shutil.move``: prohibidos en todo ``src/``.
- ``os.rename``: solo en ``storage/_fs.py``, dentro de la rama de Windows (``sys.platform``).
- ``Path.replace(target)`` y ``Path.rename(target)`` (un argumento): prohibidos
  (heurística AST: ``str.replace`` siempre recibe dos argumentos).
- Solo ``storage/_fs.py`` usa ``os.link``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from conftest import ROOT

SRC = ROOT / "src" / "nexus"
FS_MODULE = SRC / "storage" / "_fs.py"


def _calls(path: Path) -> list[tuple[str, str, int]]:
    out = []
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value.id if isinstance(node.func.value, ast.Name) else "?"
            out.append((owner, node.func.attr, len(node.args)))
    return out


def test_no_replacing_operations_anywhere() -> None:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        for owner, attr, nargs in _calls(path):
            rel = path.relative_to(SRC).as_posix()
            if (owner, attr) in {("os", "replace"), ("shutil", "move"), ("shutil", "copyfile")}:
                offenders.append((rel, f"{owner}.{attr}"))
            if attr in {"replace", "rename"} and nargs == 1 and owner != "os":
                offenders.append((rel, f"{owner}.{attr}(1 arg)"))
            if (owner, attr) in {("os", "rename"), ("os", "link")} and path != FS_MODULE:
                offenders.append((rel, f"{owner}.{attr}"))
    assert offenders == []


def test_os_rename_only_in_windows_branch() -> None:
    source = FS_MODULE.read_text("utf-8")
    tree = ast.parse(source)
    func = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "publish_no_replace"
    )
    windows_branch = next(
        n
        for n in func.body
        if isinstance(n, ast.If) and ast.unparse(n.test) == "sys.platform == 'win32'"
    )
    renames_in_branch = [
        n
        for n in ast.walk(windows_branch)
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "os.rename"
    ]
    renames_total = [
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == "os.rename"
    ]
    assert len(renames_in_branch) == 1
    assert len(renames_total) == 1
    assert "os.replace(" not in source


def test_object_store_never_resolves_full_paths_for_io() -> None:
    """NX-E1A-02: el ObjectStore no abre, enlaza ni renombra rutas completas por nombre;
    toda la E/S de objetos pasa por las primitivas relativas a directorio de ``_fs``."""
    store_module = SRC / "storage" / "object_store.py"
    tree = ast.parse(store_module.read_text("utf-8"))
    direct = sorted(
        {
            ast.unparse(n.func)
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and ast.unparse(n.func)
            in {"os.open", "os.link", "os.rename", "os.unlink", "open", "os.lstat", "os.stat"}
        }
    )
    assert direct == []
