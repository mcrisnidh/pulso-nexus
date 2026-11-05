"""A6 — los manifiestos no contienen rutas absolutas ni secretos; allowlist de conectores."""

from __future__ import annotations

import json
import pkgutil
import re
from pathlib import Path
from typing import Any

import nexus.connectors as connectors_pkg
from conftest import AS_OF
from nexus.connectors import load_allowlist
from nexus.pipeline.run import execute
from nexus.storage.object_store import LocalObjectStore

SECRET_KEY = re.compile(
    r"(passw|secret|token|credential|api[_-]?key|private[_-]?key|conn(ection)?[_-]?str)", re.I
)
SECRET_VALUE = re.compile(r"(password=|pwd=|BEGIN [A-Z ]*PRIVATE KEY|AccountKey=|Bearer\s)", re.I)
ABSOLUTE = re.compile(r"^(/|[A-Za-z]:[\\/]|\\\\)")


def _walk(obj: Any, path: str = "$") -> list[tuple[str, str, Any]]:
    items: list[tuple[str, str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            items.append((path, str(k), v))
            items.extend(_walk(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            items.extend(_walk(v, f"{path}[{i}]"))
    return items


def test_manifests_have_no_absolute_paths_or_secrets(
    store: LocalObjectStore, snapshot_id: str, tmp_path: Path
) -> None:
    execute(store, snapshot_id=snapshot_id, as_of=AS_OF)
    manifests = list((store.root / "objects").rglob("*.json"))
    assert len(manifests) >= 3
    home = str(Path.home())
    for path in manifests:
        text = path.read_text("utf-8")
        assert str(tmp_path) not in text
        assert home not in text
        doc = json.loads(text)
        for where, key, value in _walk(doc):
            assert not SECRET_KEY.search(key), f"{path}:{where}.{key}"
            if isinstance(value, str):
                assert not ABSOLUTE.match(value), f"{path}:{where}.{key}={value!r}"
                assert not SECRET_VALUE.search(value), f"{path}:{where}.{key}"


def test_connectors_package_matches_allowlist() -> None:
    allow = load_allowlist()
    modules = {
        m.name for m in pkgutil.iter_modules(connectors_pkg.__path__) if m.name not in {"base"}
    }
    assert modules == set(allow)
    root = Path(__file__).resolve().parents[2]
    for entry in allow.values():
        assert (root / entry["adr"]).is_file(), entry["adr"]
