"""A14 — fixtures sintéticos reproducibles y red bloqueada durante las pruebas."""

from __future__ import annotations

import hashlib
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIXTURES, ROOT, NetworkBlockedError


def test_fixtures_regenerate_byte_identical(tmp_path: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "make_synthetic_fixtures.py"),
            "--out",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
    )
    for name in ("input_v1.csv", "input_v1_invalid.csv", "SHA256SUMS"):
        assert (tmp_path / name).read_bytes() == (FIXTURES / name).read_bytes(), name


def test_committed_fixtures_match_sha256sums() -> None:
    for line in (FIXTURES / "SHA256SUMS").read_text("utf-8").splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == digest


def test_network_is_blocked() -> None:
    with pytest.raises(NetworkBlockedError):
        socket.create_connection(("example.com", 443), timeout=1)
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("example.com", 443)
    s = socket.socket()
    try:
        with pytest.raises(NetworkBlockedError):
            s.connect(("127.0.0.1", 9))
    finally:
        s.close()
