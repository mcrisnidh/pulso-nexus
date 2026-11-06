"""WP2 (Frozen) y WP6 (Refresh, publicación atómica, fallos, rollback, canales)."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from core_helpers import Clock, make, summary, values

from nexus.core import demo
from nexus.core.refresh import RefreshService
from nexus.core.state import (
    CoreState,
    FrozenExistsError,
    PublicationConflictError,
    PublicationRefusedError,
)
from nexus.errors import IntegrityError
from nexus.storage.object_store import LocalObjectStore

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ G2 Frozen
def test_g2_first_valid_extract_is_frozen_later_is_current(tmp_path: Path) -> None:
    root, clock, svc = make(tmp_path)
    svc.refresh("t")
    frozen = svc.state.get_frozen("sintetico", "2031-01", "oeras_29")
    assert frozen and frozen["file"] == "R_20310101_0600.csv"
    demo.current_m1(root)
    demo.invalid_extract(root)
    clock.set("2031-01-10T21:00:00Z")
    out = svc.refresh("t")
    assert svc.state.get_frozen("sintetico", "2031-01", "oeras_29") == frozen
    m = svc.state.get_manifest("sintetico", out["head"]["gen_id"])
    assert m["sources"]["current"]["oeras_29"]["file"] == "R_20310105_0600.csv"
    assert any(r["file"] == "R_20310103_0600.csv" for r in m["sources"]["rejected"])
    assert values(svc)["SYN-C"]["demand"] == "80"  # Current no altera la demanda congelada
    item = svc.state.get_artifact(m["items"]["SYN-C"]["key"], m["items"]["SYN-C"]["sha256"])
    assert item["current_vs_frozen"]["added"] == 1 and item["current_vs_frozen"]["changed"] == 1


def test_g2_invalid_first_never_frozen(tmp_path: Path) -> None:
    root, _clock, svc = make(tmp_path)
    good = root / "inbox/oeras_29/R_20310101_0600.csv"
    content = good.read_bytes()
    good.unlink()
    demo.invalid_extract(root)
    out = svc.refresh("t")
    assert svc.state.get_frozen("sintetico", "2031-01", "oeras_29") is None
    assert out["head"] is None
    demo.drop(
        root,
        "oeras_29",
        "R_20310104_0600.csv",
        content.replace(b"2031-01-01T06:00:00Z", b"2031-01-04T06:00:00Z"),
    )
    svc.refresh("t")
    assert svc.state.get_frozen("sintetico", "2031-01", "oeras_29")["file"] == "R_20310104_0600.csv"


def test_g2_late_earlier_extract_alerts_without_replacing(tmp_path: Path) -> None:
    root, clock, svc = make(tmp_path)
    svc.refresh("t")
    before = svc.state.get_frozen("sintetico", "2031-01", "oeras_29")
    demo.late_earlier_extract(root)
    clock.set("2031-01-10T22:00:00Z")
    svc.refresh("t")
    assert svc.state.get_frozen("sintetico", "2031-01", "oeras_29") == before
    alerts = svc.state.alerts("sintetico", "2031-01")
    assert [a["kind"] for a in alerts] == ["FROZEN_PRECEDENCE_CONFLICT"]
    assert alerts[0]["earlier_file"] == "R_20310101_0500.csv"


def test_g2_frozen_cannot_be_overwritten_even_concurrently(tmp_path: Path) -> None:
    state = CoreState(LocalObjectStore(tmp_path / "s"))
    results: list[Any] = []
    barrier = threading.Barrier(8)

    def go(i: int) -> None:
        barrier.wait()
        try:
            results.append(
                state.register_frozen(
                    "sintetico", "2031-01", "oeras_29", {"writer": i, "sha256": str(i)}
                )
            )
        except FrozenExistsError:
            results.append("EXISTS")

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in results if r != "EXISTS"]
    assert len(winners) == 1 and results.count("EXISTS") == 7
    assert state.get_frozen("sintetico", "2031-01", "oeras_29") == winners[0]
    with pytest.raises(FrozenExistsError):
        state.register_frozen("sintetico", "2031-01", "oeras_29", {"writer": 99})


def test_g2_tampered_frozen_fails_closed(tmp_path: Path) -> None:
    _root, clock, svc = make(tmp_path)
    svc.refresh("t")
    rec = svc.state.get_frozen("sintetico", "2031-01", "oeras_29")
    path = svc.state.store.root / "objects" / f"sources/oeras-29/{rec['sha256']}.csv"
    path.chmod(0o644)
    path.write_bytes(path.read_bytes().replace(b"300", b"301"))
    head = svc.state.publication_head("sintetico")
    demo.new_cut_m1(svc.data_dir)
    clock.set("2031-01-20T20:00:00Z")
    out = svc.refresh("t")
    assert out["outcomes"][0]["status"] == "FAILED"
    assert svc.state.publication_head("sintetico") == head  # sin publicar


# ------------------------------------------------------------------ G6 Refresh y publicación
def test_g6_idempotent_double_click_single_run(tmp_path: Path) -> None:
    _root, _clock, svc = make(tmp_path)
    a = svc.request("u1")
    b = svc.request("u2")
    assert a["key"] == b["key"] and a["created"] and not b["created"]
    done = svc.run_pending()
    assert len(done) == 1 and done[0]["published"]
    again = svc.refresh("u3")
    assert again["outcomes"] == [] and again["head"]["seq"] == 1


def test_g6_same_sources_same_generation_after_restart(tmp_path: Path) -> None:
    root, clock, svc = make(tmp_path)
    first = svc.refresh("t")["head"]
    svc2 = RefreshService(root, "sintetico", root / "rules.json", clock)
    second = svc2.refresh("t")
    assert second["outcomes"][0]["gen_id"] == first["gen_id"]
    assert svc2.state.publication_head("sintetico")["seq"] == 1  # sin publicación duplicada


def test_g6_new_cut_publishes_new_generation_with_expected_values(tmp_path: Path) -> None:
    root, clock, svc = make(tmp_path)
    svc.refresh("t")
    demo.new_cut_m1(root)
    clock.set("2031-01-20T20:00:00Z")
    out = svc.refresh("t")
    assert out["head"]["seq"] == 2
    v = values(svc)["SYN-A"]
    assert (
        v["demand"],
        v["attended"],
        v["pending"],
        v["fg_assignable"],
        v["covered"],
        v["uncovered"],
    ) == ("650", "490", "160", "90", "90", "70")


def test_g6_missing_or_misaligned_sources_do_not_publish(tmp_path: Path) -> None:
    root, clock, svc = make(tmp_path)
    svc.refresh("t")
    head = svc.state.publication_head("sintetico")
    demo.broken_cut_m1(root)  # FG nuevo sin embarques del mismo corte
    clock.set("2031-01-25T20:00:00Z")
    out = svc.refresh("t")
    assert out["outcomes"][0]["published"] is False
    assert "TEMPORAL_MISMATCH" in " ".join(out["outcomes"][0]["validation"]["failures"])
    assert svc.state.publication_head("sintetico") == head
    kinds = [e["kind"] for e in svc.state.events("sintetico")]
    assert "REFRESH_NOT_PUBLISHED" in kinds


@pytest.mark.parametrize(
    "stage",
    [
        "after_claim",
        "after_lease",
        "after_capture",
        "after_compute",
        "after_generation",
        "after_register",
        "after_seal",
        "before_publish",
    ],
)
def test_g6_failure_at_every_stage_never_partial_then_retry_publishes(
    tmp_path: Path, stage: str
) -> None:
    fired = {"n": 0, "armed": False}

    def hook(name: str) -> None:
        if fired["armed"] and name == stage and fired["n"] == 0:
            fired["n"] += 1
            raise RuntimeError(f"fallo inyectado en {stage}")

    root, clock, svc = make(tmp_path, hook=hook)
    svc.refresh("t")
    fired["armed"] = True
    demo.new_cut_m1(root)
    clock.set("2031-01-20T20:00:00Z")
    before = svc.state.publication_head("sintetico")
    with contextlib.suppress(RuntimeError):
        svc.refresh("t")
    head = svc.state.publication_head("sintetico")
    assert fired["n"] == 1
    assert head is not None and head["seq"] in (1, 2)
    if head and head["seq"] == 2:  # el reintento automático (max_attempts) ya publicó
        assert values(svc)["SYN-A"]["attended"] == "490"
    else:
        assert head == before
        svc2 = RefreshService(root, "sintetico", root / "rules.json", clock)
        svc2.refresh("t")
        assert values(svc2)["SYN-A"]["attended"] == "490"
    assert svc.state.verify_publication_chain("sintetico") == []


def test_g6_process_killed_mid_refresh_then_restart_recovers(tmp_path: Path) -> None:
    root, clock, svc = make(tmp_path)
    svc.refresh("t")
    demo.new_cut_m1(root)
    script = (
        "import os,signal,sys,datetime as dt\n"
        "from pathlib import Path\n"
        "from nexus.core.refresh import RefreshService\n"
        "def hook(n):\n"
        "    if n==os.environ['KILL_AT']: os.kill(os.getpid(), signal.SIGKILL)\n"
        "now=dt.datetime(2031,1,20,20,tzinfo=dt.UTC)\n"
        "r=Path(sys.argv[1]); RefreshService(r,'sintetico',r/'rules.json',lambda:now,hook)"
        ".refresh('k')\n"
    )
    for kill_at in ("after_compute", "after_generation"):
        proc = subprocess.run(
            [sys.executable, "-c", script, str(root)],
            check=False,
            env={**os.environ, "KILL_AT": kill_at, "PYTHONPATH": str(ROOT / "src")},
            timeout=120,
        )
        assert proc.returncode == -signal.SIGKILL
        assert svc.state.publication_head("sintetico")["seq"] == 1
    clock.set("2031-01-20T20:00:00Z")
    restarted = RefreshService(root, "sintetico", root / "rules.json", clock)
    restarted.refresh("t")
    assert restarted.state.publication_head("sintetico")["seq"] == 2
    assert values(restarted)["SYN-A"]["attended"] == "490"


def test_g6_publication_cas_concurrent_single_winner(tmp_path: Path) -> None:
    state = CoreState(LocalObjectStore(tmp_path / "s"))
    barrier = threading.Barrier(6)
    results: list[str] = []

    def go(i: int) -> None:
        barrier.wait()
        try:
            state.append_publication("sintetico", 0, {"gen_id": f"g{i}"})
            results.append("OK")
        except PublicationConflictError:
            results.append("CONFLICT")

    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("OK") == 1 and results.count("CONFLICT") == 5
    assert len(state.publication_history("sintetico")) == 1


def test_g6_rollback_and_chain(tmp_path: Path) -> None:
    root, clock, svc = make(tmp_path)
    g1 = svc.refresh("t")["head"]["gen_id"]
    demo.new_cut_m1(root)
    clock.set("2031-01-20T20:00:00Z")
    g2 = svc.refresh("t")["head"]["gen_id"]
    assert g1 != g2
    rb = svc.rollback(g1, "admin")
    assert rb["action"] == "ROLLBACK" and rb["seq"] == 3
    assert svc.state.publication_head("sintetico")["gen_id"] == g1
    assert values(svc)["SYN-A"]["attended"] == "240"
    with pytest.raises(PublicationRefusedError):
        svc.rollback("f" * 32, "admin")
    assert svc.state.verify_publication_chain("sintetico") == []


def test_g6_tampered_published_artifact_detected(tmp_path: Path) -> None:
    _root, _clock, svc = make(tmp_path)
    svc.refresh("t")
    m = svc.state.get_manifest("sintetico", svc.state.publication_head("sintetico")["gen_id"])
    art = m["items"]["SYN-A"]
    p = svc.state.store.root / "objects" / art["key"]
    p.chmod(0o644)
    p.write_bytes(p.read_bytes().replace(b"650", b"651"))
    with pytest.raises(IntegrityError):
        svc.state.get_artifact(art["key"], art["sha256"])
    with pytest.raises(IntegrityError):
        svc.publish(m["gen_id"], "x")


def test_g6_operativo_channel_rejects_synthetic_and_other_month(tmp_path: Path) -> None:
    root = tmp_path / "data"
    demo.scenario(root)
    clock = Clock("2031-01-10T20:00:00Z")
    op = RefreshService(root, "operativo", root / "rules.json", clock)
    out = op.refresh("t")
    assert out["head"] is None
    assert "no admite fuentes sintéticas" in " ".join(out["outcomes"][0]["validation"]["failures"])
    with pytest.raises(PublicationRefusedError):
        op.request("t", month="2030-12")
    syn = RefreshService(root, "sintetico", root / "rules.json", clock)
    gen = syn.refresh("t")["head"]["gen_id"]
    with pytest.raises(PublicationRefusedError):
        op.publish(gen, "t")  # una generación sintética nunca se publica en operativo


def test_g6_two_months_frozen_m1_intact(tmp_path: Path) -> None:
    root, clock, svc = make(tmp_path)
    svc.refresh("t")
    f1 = svc.state.get_frozen("sintetico", "2031-01", "oeras_29")
    demo.month2(root)
    clock.set("2031-02-10T20:00:00Z")
    out = svc.refresh("t")
    assert out["head"]["month"] == "2031-02"
    assert svc.state.get_frozen("sintetico", "2031-01", "oeras_29") == f1
    assert svc.state.get_frozen("sintetico", "2031-02", "oeras_29")["file"] == "R_20310201_0600.csv"
    v = values(svc)
    assert (v["SYN-A"]["demand"], v["SYN-A"]["attended"], v["SYN-A"]["uncovered"]) == (
        "400",
        "200",
        "100",
    )
    assert (v["SYN-B"]["covered"], v["SYN-B"]["uncovered"]) == ("50", "40")
    assert summary(svc)["month"] == "2031-02"


def test_g6_determinism_same_bytes(tmp_path: Path) -> None:
    _, _, a = make(tmp_path / "a")
    _, _, b = make(tmp_path / "b")
    ga = a.refresh("x")["head"]["gen_id"]
    gb = b.refresh("y")["head"]["gen_id"]
    assert ga == gb
    ka = sorted(a.state.store.iter_keys(f"gen/sintetico/{ga}/"))
    kb = sorted(b.state.store.iter_keys(f"gen/sintetico/{gb}/"))
    assert ka == kb
    for k in ka:
        assert (a.state.store.root / "objects" / k).read_bytes() == (
            b.state.store.root / "objects" / k
        ).read_bytes()
