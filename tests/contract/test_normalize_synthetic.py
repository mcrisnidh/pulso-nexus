"""A7 — normalización: cumple el contrato, rechazos con motivo, conservación de filas."""

from __future__ import annotations

import importlib.util
import unicodedata
from types import ModuleType

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import INVALID_CSV, ROOT, VALID_CSV
from nexus.contracts.synthetic import SYNTHETIC_INPUT, SYNTHETIC_REJECTION
from nexus.errors import NormalizationError
from nexus.normalize.synthetic_v1 import HEADER, normalize


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "make_synthetic_fixtures", ROOT / "scripts" / "make_synthetic_fixtures.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_valid_fixture_fully_accepted_and_conforms() -> None:
    result = normalize(VALID_CSV.read_bytes())
    assert result.data_rows == 5000
    assert result.accepted.num_rows == 5000
    assert result.rejections.num_rows == 0
    SYNTHETIC_INPUT.validate(result.accepted)
    SYNTHETIC_REJECTION.validate(result.rejections)


def test_invalid_fixture_reasons_and_conservation() -> None:
    gen = _generator()
    result = normalize(INVALID_CSV.read_bytes())
    assert result.accepted.num_rows + result.rejections.num_rows == result.data_rows
    expected_rejections = [(ln, r) for ln, r in gen.INVALID_LINES if r != "accepted"]
    assert result.rejections.num_rows == len(expected_rejections)
    by_raw = dict(
        zip(
            result.rejections.column("raw").to_pylist(),
            result.rejections.column("reason").to_pylist(),
            strict=True,
        )
    )
    for line, reason in expected_rejections:
        assert by_raw[line] == reason, line
    accepted_ids = set(result.accepted.column("record_id").to_pylist())
    assert "X000020" in accepted_ids  # texto tipo HTML es dato, no se ejecuta
    assert "V000000" in accepted_ids  # la primera ocurrencia se acepta; el duplicado no


def test_unicode_normalized_to_nfc() -> None:
    result = normalize(VALID_CSV.read_bytes())
    groups = set(result.accepted.column("group_code").to_pylist())
    assert all(unicodedata.is_normalized("NFC", g) for g in groups)
    assert "ÁREA-Ñ" in groups
    raw = VALID_CSV.read_text("utf-8")
    assert unicodedata.normalize("NFD", "ÁREA-Ñ") in raw  # el fixture contiene NFD


def test_whole_capture_errors() -> None:
    with pytest.raises(NormalizationError, match="UTF-8"):
        normalize(b"\xff\xfe" + b"x")
    with pytest.raises(NormalizationError, match="vacía"):
        normalize(b"")
    with pytest.raises(NormalizationError, match="cabecera"):
        normalize(b"a,b,c\n1,2,3\n")


def test_bom_tolerated() -> None:
    data = "﻿" + ",".join(HEADER) + "\nR1,G,1.000,1.0,2026-01-01\n"
    assert normalize(data.encode()).accepted.num_rows == 1


_LINE_ALPHABET = st.characters(
    blacklist_categories=("Cs",),
    blacklist_characters="\n\r\x0b\x0c\x1c\x1d\x1e\x85  ",
)
_valid_line = st.builds(
    lambda i, g, q, w: f"{i},{g},{q},{w},2026-01-01",
    st.from_regex(r"[A-Za-z0-9_-]{1,10}", fullmatch=True),
    st.sampled_from(["G1", "G2", "ÁREA"]),
    st.decimals(min_value=-1000, max_value=1000, places=3, allow_nan=False),
    st.floats(allow_nan=False, allow_infinity=False),
)


@settings(max_examples=200, deadline=None)
@given(st.lists(st.one_of(_valid_line, st.text(_LINE_ALPHABET, max_size=40)), max_size=40))
def test_row_conservation_property(lines: list[str]) -> None:
    data = ("\n".join([",".join(HEADER), *lines]) + "\n").encode("utf-8")
    result = normalize(data)
    assert result.data_rows == len(lines)
    assert result.accepted.num_rows + result.rejections.num_rows == len(lines)
    SYNTHETIC_INPUT.validate(result.accepted)
    SYNTHETIC_REJECTION.validate(result.rejections)
