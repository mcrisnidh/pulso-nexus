"""Δ6 — Portabilidad Linux/Windows de la identidad (TZ-18, CRLF).

En Windows, ``Path.write_text`` traduce ``\\n`` a ``\\r\\n``. Si un archivo que entra en la
identidad de la generación (reglas, extractos) se escribiera así, el ``gen_id`` cambiaría según la
plataforma.
Estas pruebas emulan esa traducción en Linux y exigen la MISMA huella dorada (constante sin cambio).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from tz_scenarios import UTC_BASELINE_DIGEST, state_digest, utc_baseline_scenario

CORE_SRC = Path(__file__).resolve().parents[2] / "src" / "nexus" / "core"


def _windows_write_text(
    self: Path,
    data: str,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> int:
    """Emulación del modo texto de Windows (``newline=None`` → ``os.linesep == '\\r\\n'``)."""
    translated = data if newline == "" else data.replace("\n", newline or "\r\n")
    return self.write_bytes(translated.encode(encoding or "utf-8", errors or "strict"))


def test_p01_golden_digest_with_windows_style_text_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "write_text", _windows_write_text)
    svc = utc_baseline_scenario(tmp_path / "win")
    assert state_digest(svc.state.store.root) == UTC_BASELINE_DIGEST


def test_p02_emulation_is_effective(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: la emulación sí produce CRLF (si no, P01 no probaría nada)."""
    monkeypatch.setattr(Path, "write_text", _windows_write_text)
    target = tmp_path / "x.txt"
    target.write_text("a\nb\n", "utf-8")
    assert target.read_bytes() == b"a\r\nb\r\n"


def test_p03_demo_files_are_lf_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus.core import demo

    monkeypatch.setattr(Path, "write_text", _windows_write_text)
    paths = demo.scenario(tmp_path / "d")
    demo.invalid_extract(tmp_path / "d")
    for path in (
        paths["rules"],
        paths["users"],
        tmp_path / "d" / "inbox" / "oeras_29" / "R_20310103_0600.csv",
    ):
        assert b"\r" not in path.read_bytes(), path.name


def test_p04_no_text_mode_writes_in_core() -> None:
    """Prueba estática: ningún módulo de ``nexus.core`` usa ``write_text`` ni ``open(..., 'w')``
    en modo texto (todo lo que puede entrar en una identidad se escribe en bytes)."""
    offenders = []
    for path in sorted(CORE_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "write_text":
                offenders.append(f"{path.name}:{node.lineno} write_text")
            if isinstance(func, ast.Name | ast.Attribute) and (
                getattr(func, "id", None) == "open" or getattr(func, "attr", None) == "open"
            ):
                modes = [a for a in node.args[1:2] if isinstance(a, ast.Constant)]
                modes += [
                    k.value
                    for k in node.keywords
                    if k.arg == "mode" and isinstance(k.value, ast.Constant)
                ]
                for m in modes:
                    text = m.value if isinstance(m.value, str) else ""
                    if ("w" in text or "a" in text) and "b" not in text:
                        offenders.append(f"{path.name}:{node.lineno} open({m.value!r})")
    assert offenders == []
