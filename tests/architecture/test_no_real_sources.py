"""Higiene (noviembre 2026): el repositorio nunca contiene fuentes corporativas reales ni salidas
de ``stage``. Escanea los archivos versionados y los pendientes (no ignorados)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FORBIDDEN = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(^|/)R_\d{8}[^/]*\.txt$",
        r"(^|/)Portal_CAT_[^/]*$",
        r"(^|/)INV_STA[^/]*$",
        r"\.xlsx?$",
        r"(^|/)MF_[^/]*$",
        r"(^|/)originals/[0-9a-f]{64}",
        r"(^|/)provenance/(oeras_29|portal_cat|fg_inventory)/",
    )
]


def _files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git no disponible")
    return out.splitlines()


def test_hy01_no_real_source_files_tracked_or_pending() -> None:
    offenders = [f for f in _files() if any(p.search(f) for p in FORBIDDEN)]
    assert offenders == []


def test_hy02_gitignore_blocks_real_source_names() -> None:
    names = ["R_20261101.TXT", "Portal_CAT_2026-11-03_0955.csv", "INV_STA289.TXT", "x.xlsx"]
    out = subprocess.run(
        ["git", "check-ignore", "--no-index", *names],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    assert sorted(out) == sorted(names)
