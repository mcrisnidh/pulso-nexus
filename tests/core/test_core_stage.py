"""Δ2 — ``nexus core probe`` y ``nexus core stage`` (solo lectura del origen, salida saneada,
directorio privado, sin reemplazos ni publicación)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import stat
from pathlib import Path
from typing import Any

import physical_fixtures as fx
import pytest

from nexus.cli import main
from nexus.core.stage import probe, stage, write_exclusive
from nexus.errors import ConfigurationError

REPO = Path(__file__).resolve().parents[2]
# Valores sintéticos que NUNCA deben aparecer en un informe saneado
SECRETS = (
    "QZXITEM",
    "QZXCAT",
    "QZXTUP",
    "QZXAPT",
    "QZXDEL",
    "QZX PORTAL",
    "7310101",
    "7310203",
    "CLIENTE SINTETICO",
    "6516",
    "PL-1",
)


def clock() -> dt.datetime:
    return dt.datetime(2026, 11, 7, 18, 0, tzinfo=dt.UTC)


def fingerprint(folder: Path) -> dict[str, tuple[str, int, int]]:
    return {
        p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns, p.stat().st_mode)
        for p in sorted(folder.iterdir())
    }


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, Any]:
    code = main(list(argv))
    return code, json.loads(capsys.readouterr().out)


# ------------------------------------------------------------------ probe
@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("R_20261101.TXT", "oe965"),
        ("Portal_CAT_2026-11-03_0955.csv", "portal"),
        ("INV_STA289.TXT", "in092u"),
    ],
)
def test_st01_probe_is_read_only_and_sanitized(tmp_path: Path, name: str, kind: str) -> None:
    lab = fx.lab(tmp_path)
    before = fingerprint(lab["src"])
    report = probe(lab["src"] / name, business_tz="America/Mexico_City")
    assert fingerprint(lab["src"]) == before  # hash, mtime y permisos intactos
    text = json.dumps(report, ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in text, secret
    s = report["structure"]
    assert s["kind"] == kind and s["valid"] is True and s["records"] > 0
    assert all(
        set(shape) <= set("A9 -/:*.=\f\t(),#") for v in s["shapes_masked"].values() for shape in v
    )
    assert "header_names" not in s


def test_st02_probe_reports_structure_needed_for_layout(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    s = probe(lab["src"] / "R_20261101.TXT")["structure"]
    assert s["encoding"] == "cp850" and s["line_endings"] == {"CRLF": s["total_lines"]}
    assert s["line_classes"]["order_line"] == 8 and s["line_classes"]["item_header"] == 3
    assert {"length": 164, "lines": 8} in s["line_lengths_top"]
    assert s["document_time"] == {
        "precision": "date",
        "source": "document_header",
        "month": "2026-11",
    }
    assert s["distinct_counts"]["customer_key_exact"] == 6
    portal = probe(lab["src"] / "Portal_CAT_2026-11-03_0955.csv", show_headers=True)["structure"]
    assert portal["header_names"] == fx.PORTAL_HEADER  # solo con --show-headers (no son filas)


def test_st03_probe_reports_exception_codes_and_lines_only(tmp_path: Path) -> None:
    bad = tmp_path / "R_20261199.TXT"
    data = fx.oe965_bytes().decode("cp850").replace("11/10/26", "11/1/26 ")
    bad.write_bytes(data.encode("cp850"))
    s = probe(bad)["structure"]
    assert s["valid"] is False
    assert s["exceptions"] == {"BLOCKING:DUE_DATE_UNREADABLE_OR_AMBIGUOUS:due_date": 1}
    assert s["exception_lines_first"][0]["line"] > 0
    assert "11/1/26" not in json.dumps(s)


def test_st04_customer_keys_only_to_private_file_outside_git(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    with pytest.raises(ConfigurationError, match="repositorio git"):
        probe(lab["src"] / "R_20261101.TXT", keys_out=REPO / "claves.csv")
    assert not (REPO / "claves.csv").exists()
    out = tmp_path / "private" / "claves.csv"
    report = probe(lab["src"] / "R_20261101.TXT", keys_out=out)
    assert fx.CAT not in json.dumps(report)
    keys = out.read_text("utf-8")
    assert f'"{fx.CAT}",2' in keys and f'"{fx.APTIV}",1' in keys and f'"{fx.DELPHI}",1' in keys


def test_st05_probe_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lab = fx.lab(tmp_path)
    code, out = run_cli(
        capsys,
        "core",
        "probe",
        "--file",
        str(lab["src"] / "INV_STA289.TXT"),
        "--tz",
        "America/Mexico_City",
    )
    assert code == 0 and out["structure"]["valid"] is True
    target = tmp_path / "probe.json"
    code, out = run_cli(
        capsys, "core", "probe", "--file", str(lab["src"] / "R_20261101.TXT"), "--out", str(target)
    )
    assert code == 0 and json.loads(target.read_text("utf-8"))["schema"] == "nexus.core.probe/1"
    code, out = run_cli(capsys, "core", "probe", "--file", str(tmp_path / "x.xlsx"))
    assert code != 0  # tipo no reconocido (STKBAL/xlsx fuera de alcance)


# ------------------------------------------------------------------ stage
def test_st10_stage_writes_only_private_dir_and_keeps_source_intact(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    before = fingerprint(lab["src"])
    report = stage(lab["src"], lab["data"], lab["homologation"], clock=clock)
    assert fingerprint(lab["src"]) == before
    status = {f["file"]: f["status"] for f in report["files"]}
    assert set(status.values()) == {"STAGED"}
    for sid in ("oeras_29", "portal_cat", "fg_inventory"):
        assert len(list((lab["data"] / "inbox" / sid).glob("*.csv"))) == 1
    originals = sorted((lab["data"] / "originals").iterdir())
    assert len(originals) == 3
    assert all(not (p.stat().st_mode & stat.S_IWUSR) for p in originals)  # solo lectura
    for p in originals:  # copia por SHA-256: identidad independiente del original
        assert p.stem == hashlib.sha256(p.read_bytes()).hexdigest()
    text = json.dumps(report, ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in text, secret


def test_st11_sidecar_separates_document_extraction_and_reception(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    stage(lab["src"], lab["data"], lab["homologation"], clock=clock)
    [side] = (lab["data"] / "provenance" / "oeras_29").glob("*.json")
    doc = json.loads(side.read_text("utf-8"))
    raw = (lab["src"] / "R_20261101.TXT").read_bytes()
    assert doc["original"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert doc["original"]["file_name"] == "R_20261101.TXT"
    assert doc["times"]["document"] == {
        "value": "2026-11-01",
        "precision": "date",
        "source": "document_header",
    }
    assert doc["times"]["extraction"]["value"] is None  # no declarada: no se inventa
    assert doc["times"]["reception"]["value"] == "2026-11-07T18:00:00Z"
    [portal] = (lab["data"] / "provenance" / "portal_cat").glob("*.json")
    pdoc = json.loads(portal.read_text("utf-8"))
    assert pdoc["times"]["document"]["source"] == "file_name"
    assert "09:55" not in json.dumps(pdoc["times"])
    [fg] = (lab["data"] / "provenance" / "fg_inventory").glob("*.json")
    assert (
        json.loads(fg.read_text("utf-8"))["times"]["document"]["business_tz"]
        == "America/Mexico_City"
    )


def test_st12_restage_is_idempotent_and_keeps_first_reception(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    stage(lab["src"], lab["data"], lab["homologation"], clock=clock)
    later = lambda: dt.datetime(2026, 11, 9, 8, 0, tzinfo=dt.UTC)  # noqa: E731
    report = stage(lab["src"], lab["data"], lab["homologation"], clock=later)
    assert {f["csv"] for f in report["files"]} == {"unchanged"}
    assert {f["provenance"] for f in report["files"]} == {"kept_first_reception"}
    [side] = (lab["data"] / "provenance" / "oeras_29").glob("*.json")
    assert (
        json.loads(side.read_text("utf-8"))["times"]["reception"]["value"] == "2026-11-07T18:00:00Z"
    )


def test_st13_never_replaces_existing_files(tmp_path: Path) -> None:
    target = tmp_path / "x.csv"
    assert write_exclusive(target, b"a\n") == "created"
    assert write_exclusive(target, b"a\n") == "unchanged"
    with pytest.raises(ConfigurationError, match="no se reemplaza"):
        write_exclusive(target, b"b\n")
    assert target.read_bytes() == b"a\n"


def test_st14_refuses_output_inside_git_or_source(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    with pytest.raises(ConfigurationError, match="repositorio git"):
        stage(lab["src"], REPO / "tmp-stage-should-not-exist", clock=clock)
    assert not (REPO / "tmp-stage-should-not-exist").exists()
    with pytest.raises(ConfigurationError, match="dentro del origen"):
        stage(lab["src"], lab["src"] / "data", clock=clock)
    inside = tmp_path / "inputs"
    with pytest.raises(ConfigurationError, match="dentro del origen"):
        stage(lab["src"], inside, clock=clock)


def test_st15_duplicate_content_staged_once(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    raw = (lab["src"] / "R_20261101.TXT").read_bytes()
    (lab["src"] / "R_20261101(3).TXT").write_bytes(raw)
    (lab["src"] / "R_20261101(4).TXT").write_bytes(raw)
    report = stage(lab["src"], lab["data"], lab["homologation"], clock=clock)
    status = [f["status"] for f in report["files"] if f["kind"] == "oe965"]
    assert sorted(status) == ["DUPLICATE_CONTENT", "DUPLICATE_CONTENT", "STAGED"]
    assert len(list((lab["data"] / "inbox" / "oeras_29").glob("*.csv"))) == 1


def test_st16_portal_without_exact_homologation_is_quarantined(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    report = stage(lab["src"], lab["data"], None, clock=clock)  # sin tabla (bloqueo R1)
    portal = next(f for f in report["files"] if f["kind"] == "portal")
    assert portal["status"] == "QUARANTINED" and "R1" in portal["reason"]
    assert portal["unmapped_active"] == 2 and portal["unmapped_excluded_status_dropped"] == 1
    assert not (lab["data"] / "inbox" / "portal_cat").exists()


def test_st17_invalid_physical_file_is_quarantined_not_staged(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    (lab["src"] / "R_20261101.TXT").write_bytes(fx.oe965_bytes(pages=False))  # sin fecha
    report = stage(lab["src"], lab["data"], lab["homologation"], clock=clock)
    oe = next(f for f in report["files"] if f["kind"] == "oe965")
    assert oe["status"] == "QUARANTINED"
    assert "BLOCKING:DOCUMENT_DATE_MISSING" in oe["exceptions"]
    assert not (lab["data"] / "inbox" / "oeras_29").exists()


def test_st18_in092u_requires_declared_zone(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    (lab["data"] / "operation.json").unlink()
    report = stage(lab["src"], lab["data"], lab["homologation"], clock=clock)
    inv = next(f for f in report["files"] if f["kind"] == "in092u")
    assert (
        inv["status"] == "QUARANTINED" and "BLOCKING:BUSINESS_TZ_NOT_DECLARED" in inv["exceptions"]
    )


def test_st19_out_of_scope_files_are_ignored_and_not_read(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    for name in ("CO 36 inventory 32INAS.xlsx", "MF_000123.xml", "Embarques Octubre.xlsx"):
        (lab["src"] / name).write_bytes(b"no se lee")
    report = stage(lab["src"], lab["data"], lab["homologation"], clock=clock)
    assert report["ignored"] == {".xlsx": 2, ".xml": 1}
    assert "fg_inventory" in {p.name for p in (lab["data"] / "inbox").iterdir()}
    assert not any("xlsx" in p.name for p in (lab["data"] / "originals").iterdir())


def test_st20_stage_cli_does_not_publish(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lab = fx.lab(tmp_path)
    code, out = run_cli(
        capsys,
        "core",
        "stage",
        "--src",
        str(lab["src"]),
        "--data",
        str(lab["data"]),
        "--homologation",
        str(lab["homologation"]),
    )
    assert code == 0 and out["schema"] == "nexus.core.stage-report/1"
    assert not (lab["data"] / "store").exists() and not (lab["data"] / "replay").exists()
