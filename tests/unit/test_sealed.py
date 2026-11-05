"""Documentos autosellados."""

from __future__ import annotations

import pytest

from nexus.errors import IntegrityError
from nexus.sealed import seal, unseal, verifier


def test_roundtrip() -> None:
    doc = {"a": 1, "b": "ñ"}
    assert unseal(seal(doc), "doc") == doc


def test_detects_tampering_and_malformed() -> None:
    sealed = seal({"a": 1})
    with pytest.raises(IntegrityError):
        unseal(sealed.replace(b'"a":1', b'"a":2'), "doc")
    with pytest.raises(IntegrityError):
        unseal(b"\xff\xfe", "doc")
    with pytest.raises(IntegrityError):
        unseal(b"[1, 2]", "doc")
    with pytest.raises(IntegrityError):
        unseal(b'{"a": 1}', "doc")
    with pytest.raises(IntegrityError):
        verifier("doc")(b"{}")
    verifier("doc")(sealed)


def test_cannot_seal_twice() -> None:
    with pytest.raises(ValueError):
        seal({"seal_sha256": "x"})
