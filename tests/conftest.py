"""Configuración común de pruebas.

- La red queda bloqueada en el proceso de pruebas (A14).
- Solo se usan fixtures sintéticos generados con semilla fija.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from nexus.connectors import synthetic_file
from nexus.snapshots.capture import create_snapshot
from nexus.storage.object_store import LocalObjectStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "synthetic"
VALID_CSV = FIXTURES / "input_v1.csv"
INVALID_CSV = FIXTURES / "input_v1_invalid.csv"
AS_OF = "2026-12-31T00:00:00Z"


class NetworkBlockedError(RuntimeError):
    pass


def _blocked(*_args: Any, **_kwargs: Any) -> Any:
    raise NetworkBlockedError("acceso de red bloqueado durante las pruebas")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    yield


@pytest.fixture
def store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "store")


@pytest.fixture
def snapshot_id(store: LocalObjectStore) -> str:
    manifest = create_snapshot(store, synthetic_file.capture(VALID_CSV))
    return str(manifest["snapshot_id"])


@pytest.fixture
def invalid_snapshot_id(store: LocalObjectStore) -> str:
    manifest = create_snapshot(store, synthetic_file.capture(INVALID_CSV))
    return str(manifest["snapshot_id"])


def child_env(**overrides: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(overrides)
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def run_cli(*args: str, env: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    """Ejecuta el CLI en un proceso separado (como lo haría un operador o un planificador)."""
    proc = subprocess.run(
        [sys.executable, "-m", "nexus.cli", *args],
        capture_output=True,
        text=True,
        env=env or child_env(),
        timeout=300,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:  # pragma: no cover - diagnóstico
        payload = {"raw_stdout": proc.stdout, "stderr": proc.stderr}
    if proc.stderr.strip():
        payload.setdefault("_stderr", proc.stderr)
    return proc.returncode, payload


def object_path(store: LocalObjectStore, key: str) -> Path:
    return store.root / "objects" / key


def tamper(path: Path, data: bytes | None = None) -> None:
    """Simula una alteración externa del archivo (fuera del protocolo del almacén)."""
    os.chmod(path, 0o600)
    content = path.read_bytes()
    path.write_bytes(data if data is not None else content[:-1] + bytes([content[-1] ^ 0x01]))
