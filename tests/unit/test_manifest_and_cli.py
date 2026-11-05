"""Manifiestos, CLI en proceso y verificación integral del almacén."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import AS_OF, INVALID_CSV, VALID_CSV, object_path, tamper
from nexus import cli
from nexus.errors import ConfigurationError
from nexus.pipeline import manifest as manifest_mod
from nexus.pipeline.manifest import result_digest, validate_as_of
from nexus.pipeline.run import execute
from nexus.pipeline.verify import verify_store
from nexus.storage.object_store import LocalObjectStore


@pytest.mark.parametrize(
    "bad", ["2026-01-01", "2026-01-01T00:00:00", "2026-01-01T00:00:00+00:00", "x"]
)
def test_as_of_must_be_canonical_utc(bad: str) -> None:
    with pytest.raises(ConfigurationError):
        validate_as_of(bad)
    assert validate_as_of("2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"


def test_result_digest_is_order_invariant_and_sensitive() -> None:
    a = [("x@1", "h1"), ("y@1", "h2")]
    assert result_digest(a) == result_digest(list(reversed(a)))
    assert result_digest(a) != result_digest([("x@1", "h1"), ("y@1", "h3")])


def test_code_identity_outside_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_PROJECT_ROOT", str(tmp_path))
    identity = manifest_mod.code_identity()
    assert identity["git_commit"] == "unknown"
    assert identity["lock_sha256"] == "unknown"


def test_code_identity_in_repository() -> None:
    identity = manifest_mod.code_identity()
    assert len(identity["lock_sha256"]) == 64
    assert identity["nexus_version"]


def test_trigger_validation(store: LocalObjectStore, snapshot_id: str) -> None:
    with pytest.raises(ValueError):
        execute(store, snapshot_id=snapshot_id, as_of=AS_OF, trigger="cron")


def _main(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, object]]:
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_cli_in_process_happy_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    st = str(tmp_path / "st")
    code, snap = _main(capsys, "snapshot", "--store", st, "--path", str(VALID_CSV))
    assert code == 0
    code, run = _main(
        capsys,
        "run",
        "--store",
        st,
        "--snapshot",
        str(snap["snapshot_id"]),
        "--as-of",
        AS_OF,
        "--param",
        "min_quantity=0",
        "--threads",
        "2",
        "--trigger",
        "scheduled",
    )
    assert code == 0
    code, rep = _main(capsys, "replay", "--store", st, "--run-id", str(run["run_id"]))
    assert code == 0 and rep["level"] == "L3" and rep["meets_required_level"] is True
    code, ver = _main(capsys, "verify", "--store", st, "--all")
    assert code == 0 and ver["ok"] is True and ver["runs_ok"] == 1


def test_cli_usage_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    st = str(tmp_path / "st")
    _, snap = _main(capsys, "snapshot", "--store", st, "--path", str(INVALID_CSV))
    sid = str(snap["snapshot_id"])
    for extra in (["--param", "novalue"], ["--param", "a=1", "--param", "a=2"], ["--threads", "0"]):
        code, out = _main(capsys, "run", "--store", st, "--snapshot", sid, "--as-of", AS_OF, *extra)
        assert code == cli.EXIT_USAGE, out
    monkeypatch.setenv("NEXUS_THREADS", "muchos")
    code, out = _main(capsys, "run", "--store", st, "--snapshot", sid, "--as-of", AS_OF)
    assert code == cli.EXIT_USAGE and out["error"] == "ConfigurationError"
    monkeypatch.delenv("NEXUS_THREADS")
    code, out = _main(capsys, "run", "--store", st, "--snapshot", sid, "--as-of", "2026-01-01")
    assert code == cli.EXIT_USAGE
    code, out = _main(capsys, "snapshot", "--store", st, "--path", str(tmp_path / "missing.csv"))
    assert code == cli.EXIT_USAGE and out["error"] == "FileNotFoundError"


def test_verify_reports_failures_and_warnings(
    store: LocalObjectStore, snapshot_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    record = execute(store, snapshot_id=snapshot_id, as_of=AS_OF)
    store.put("stray/object.bin", b"no referenciado")
    (store.root / "staging" / "leftover.tmp").write_bytes(b"x")
    store.put("runs/run-" + "0" * 32 + "/input_manifest.json", b"{}")
    report = verify_store(store)
    assert report.ok
    assert report.unreferenced_objects == ["stray/object.bin"]
    assert report.staging_leftovers == ["leftover.tmp"]
    assert report.incomplete_runs == ["run-" + "0" * 32]

    tamper(object_path(store, record["artifacts"][2]["key"]))
    report = verify_store(store)
    assert not report.ok
    assert any(record["run_id"] in f for f in report.failures)

    code, out = _main(capsys, "verify", "--store", str(store.root), "--all")
    assert code == cli.EXIT_VERIFY and out["ok"] is False


def test_verify_detects_missing_artifact_and_bad_snapshot(
    store: LocalObjectStore, snapshot_id: str
) -> None:
    record = execute(store, snapshot_id=snapshot_id, as_of=AS_OF)
    path = object_path(store, record["artifacts"][0]["key"])
    path.chmod(0o600)
    path.unlink()
    snap_content = object_path(store, f"snapshots/{snapshot_id}/content.bin")
    tamper(snap_content)
    report = verify_store(store)
    assert any("artefacto ausente" in f for f in report.failures)
    assert any(snapshot_id in f for f in report.failures)


def test_failed_run_objects_not_reported_as_unreferenced(
    store: LocalObjectStore, snapshot_id: str
) -> None:
    record = execute(store, snapshot_id=snapshot_id, as_of=AS_OF)
    tamper(object_path(store, record["artifacts"][0]["key"]))
    report = verify_store(store)
    assert not report.ok
    assert report.unreferenced_objects == []
