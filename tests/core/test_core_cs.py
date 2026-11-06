"""WP7-CS — notas y seguimiento de Customer Service (append-only, independiente de Refresh)."""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import threading
from pathlib import Path
from typing import Any

import pytest
from core_helpers import make, values

from nexus.core import demo
from nexus.core.cs import (
    Association,
    CsConflictError,
    CsStore,
    CsValidationError,
    board_state,
)
from nexus.core.refresh import RefreshService
from nexus.storage.object_store import LocalObjectStore

VOCAB = tuple(demo.RULES["cs_status_vocabulary"])


def _cs(path: Path) -> CsStore:
    return CsStore(LocalObjectStore(path), VOCAB)


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _add(
    cs: CsStore,
    assoc: Association,
    kind: str,
    value: Any,
    author: str = "editorcs",
    month: str = "2031-01",
    ts: str = "2031-01-10T21:00:00Z",
) -> dict[str, Any]:
    return cs.append(
        assoc,
        kind,
        value,
        author=author,
        expected_version=cs.head_version(assoc.thread_id),
        server_ts=ts,
        month=month,
    )


def test_cs01_create_and_query_with_exact_association(tmp_path: Path) -> None:
    cs = _cs(tmp_path / "cs")
    a = Association("SYN-A")
    b = Association("SYN-A", demo.TUPY, "000101", "1")
    _add(cs, a, "NOTE", "Cliente pide adelantar")
    _add(cs, b, "CS_STATUS", "EN_RIESGO")
    threads = {(t["customer_key"], t["order"], t["line"]): t for t in cs.threads_for_item("SYN-A")}
    assert set(threads) == {("", "", ""), (demo.TUPY, "000101", "1")}
    assert threads[(demo.TUPY, "000101", "1")]["versions"][0]["customer_key"] == "SYNTUP    000"
    assert cs.threads_for_item("SYN-Z") == []


def test_cs02_cs03_versions_history_and_retract(tmp_path: Path) -> None:
    cs = _cs(tmp_path / "cs")
    a = Association("SYN-A")
    _add(cs, a, "CS_STATUS", "EN_TIEMPO", author="u1", ts="2031-01-10T10:00:00Z")
    _add(cs, a, "CS_STATUS", "EN_RIESGO", author="u2", ts="2031-01-11T10:00:00Z")
    _add(cs, a, "DELIVERY_DATE", "2031-01-29")
    _add(cs, a, "ADVANCE", {"qty": "120", "date": "2031-01-15"})
    _add(cs, a, "RETRACT", 3)
    hist = cs.history(a.thread_id)
    assert [v["version"] for v in hist] == [1, 2, 3, 4, 5]
    assert [v["author"] for v in hist[:2]] == ["u1", "u2"] and hist[0]["value"] == "EN_TIEMPO"
    assert all(v["origin"] == "MANUAL_CS" for v in hist)
    state = board_state(hist, "2031-01")
    assert state["current"]["CS_STATUS"]["value"] == "EN_RIESGO"
    assert "DELIVERY_DATE" not in state["current"]  # retirada, pero sigue en el historial
    assert state["current"]["ADVANCE"]["value"] == {"qty": "120", "date": "2031-01-15"}
    with pytest.raises(CsValidationError):
        _add(cs, a, "RETRACT", 5)  # no se retira un retiro


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("CS_STATUS", "INVENTADO"),
        ("DELIVERY_DATE", "31/01"),
        ("ADVANCE", "-3"),
        ("NOTE", ""),
        ("NOTE", "x" * 2001),
        ("NOTE", "a\x00b"),
        ("OTRO", "x"),
    ],
)
def test_cs_validation_rejects(tmp_path: Path, kind: str, value: Any) -> None:
    with pytest.raises(CsValidationError):
        _add(_cs(tmp_path / "cs"), Association("SYN-A"), kind, value)


def test_cs_validation_author_month_association(tmp_path: Path) -> None:
    cs = _cs(tmp_path / "cs")
    with pytest.raises(CsValidationError):
        cs.append(
            Association("SYN-A"),
            "NOTE",
            "x",
            author="",
            expected_version=0,
            server_ts="t",
            month="2031-01",
        )
    with pytest.raises(CsValidationError):
        Association("")
    with pytest.raises(CsValidationError):
        Association("SYN-A", order="", line="1")


def test_cs07_monthly_reset_keeps_history_and_reference(tmp_path: Path) -> None:
    cs = _cs(tmp_path / "cs")
    a = Association("SYN-A")
    _add(cs, a, "CS_STATUS", "ATRASADO", month="2031-01")
    _add(cs, a, "DELIVERY_DATE", "2031-02-03", month="2031-01")
    _add(cs, a, "NOTE", "acuerdo de enero", month="2031-01")
    feb = board_state(cs.history(a.thread_id), "2031-02")
    assert feb["current"] == {}
    assert feb["reference"]["CS_STATUS"]["value"] == "ATRASADO"
    assert feb["reference"]["CS_STATUS"]["note"] == "requiere reconfirmar"
    assert feb["last_note"]["value"] == "acuerdo de enero"
    _add(cs, a, "CS_STATUS", "EN_TIEMPO", month="2031-02")
    feb = board_state(cs.history(a.thread_id), "2031-02")
    assert feb["current"]["CS_STATUS"]["value"] == "EN_TIEMPO"
    assert len(cs.history(a.thread_id)) == 4
    jan = board_state(cs.history(a.thread_id), "2031-01")
    assert jan["current"]["CS_STATUS"]["value"] == "ATRASADO"


def test_cs09_concurrent_editors_threads_one_wins_nothing_lost(tmp_path: Path) -> None:
    cs = _cs(tmp_path / "cs")
    a = Association("SYN-A")
    _add(cs, a, "NOTE", "base")
    barrier = threading.Barrier(8)
    results: list[str] = []

    def go(i: int) -> None:
        barrier.wait()
        try:
            cs.append(
                a,
                "NOTE",
                f"edición {i}",
                author=f"u{i}",
                expected_version=1,
                server_ts="2031-01-10T21:00:00Z",
                month="2031-01",
            )
            results.append("OK")
        except CsConflictError:
            results.append("CONFLICT")

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("OK") == 1 and results.count("CONFLICT") == 7
    hist = cs.history(a.thread_id)
    assert [v["version"] for v in hist] == [1, 2] and hist[0]["value"] == "base"


def _proc_writer(root: str, i: int, start: Any, out: Any) -> None:
    cs = CsStore(LocalObjectStore(root), VOCAB)
    start.wait()
    try:
        cs.append(
            Association("SYN-A"),
            "NOTE",
            f"proceso {i}",
            author=f"p{i}",
            expected_version=0,
            server_ts="2031-01-10T21:00:00Z",
            month="2031-01",
        )
        out.put("OK")
    except CsConflictError:
        out.put("CONFLICT")


def test_cs09_concurrent_editors_processes(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    start, out = ctx.Event(), ctx.Queue()
    procs = [
        ctx.Process(target=_proc_writer, args=(str(tmp_path / "cs"), i, start, out))
        for i in range(6)
    ]
    for p in procs:
        p.start()
    start.set()
    for p in procs:
        p.join(60)
    results = sorted(out.get(timeout=5) for _ in procs)
    assert results.count("OK") == 1 and results.count("CONFLICT") == 5
    assert len(_cs(tmp_path / "cs").history(Association("SYN-A").thread_id)) == 1


def test_cs04_05_06_refresh_failure_rollback_and_restart_never_touch_cs(tmp_path: Path) -> None:
    root, clock, svc = make(tmp_path)
    cs_root = root / "cs"
    cs = _cs(cs_root)
    g1 = svc.refresh("t")["head"]["gen_id"]
    a = Association("SYN-A", demo.TUPY, "000101", "1")
    _add(cs, a, "NOTE", "seguimiento")
    _add(cs, a, "CS_STATUS", "EN_RIESGO")
    before = _tree_hash(cs_root)
    for _ in range(3):  # CS-04: Refresh × 3 con fuentes cambiantes
        demo.new_cut_m1(root)
        clock.set("2031-01-20T20:00:00Z")
        svc.refresh("t")
    assert _tree_hash(cs_root) == before
    demo.broken_cut_m1(root)  # CS-06: Refresh fallido
    clock.set("2031-01-25T20:00:00Z")
    svc.refresh("t")
    assert _tree_hash(cs_root) == before
    svc.rollback(g1, "admin")  # CS-05: rollback de publicación
    assert _tree_hash(cs_root) == before
    restarted = _cs(cs_root)  # reinicio de la aplicación
    assert [v["value"] for v in restarted.history(a.thread_id)] == ["seguimiento", "EN_RIESGO"]


def test_cs08_note_on_line_absent_from_new_frozen_is_kept(tmp_path: Path) -> None:
    root, clock, svc = make(tmp_path)
    svc.refresh("t")
    cs = _cs(root / "cs")
    a = Association("SYN-B", demo.REVIEW, "000202", "1")  # no existe en el Frozen de febrero
    _add(cs, a, "NOTE", "pendiente de revisión comercial")
    demo.month2(root)
    clock.set("2031-02-10T20:00:00Z")
    svc.refresh("t")
    assert [t["order"] for t in cs.threads_for_item("SYN-B")] == ["000202"]


def test_cs10_refresh_concurrent_with_cs_edits(tmp_path: Path) -> None:
    root, _clock, svc = make(tmp_path)
    cs = _cs(root / "cs")
    a = Association("SYN-C")
    demo.new_cut_m1(root)
    errors: list[BaseException] = []

    def edits() -> None:
        try:
            for i in range(20):
                _add(cs, a, "NOTE", f"nota {i}")
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=edits)
    t.start()
    svc.refresh("t")
    t.join()
    assert not errors and len(cs.history(a.thread_id)) == 20
    assert svc.state.publication_head("sintetico") is not None


def test_cs14_cs_fields_do_not_change_coverage(tmp_path: Path) -> None:
    root, _clock, svc = make(tmp_path / "x")
    _root2, _clock2, svc2 = make(tmp_path / "y")
    cs = _cs(root / "cs")
    _add(cs, Association("SYN-A"), "ADVANCE", {"qty": "999"})
    _add(cs, Association("SYN-A"), "DELIVERY_DATE", "2031-01-02")
    g1 = svc.refresh("t")["head"]["gen_id"]
    g2 = svc2.refresh("t")["head"]["gen_id"]
    assert g1 == g2 and values(svc) == values(svc2)


def test_cs_store_is_separate_from_generation_store(tmp_path: Path) -> None:
    root, _clock, svc = make(tmp_path)
    svc.refresh("t")
    _add(_cs(root / "cs"), Association("SYN-A"), "NOTE", "x")
    assert not any(k.startswith("cs/") for k in svc.state.store.iter_keys())
    assert os.path.realpath(svc.state.store.root) != os.path.realpath(root / "cs")
    RefreshService(root, "sintetico", root / "rules.json")  # no recibe ni crea el almacén CS
