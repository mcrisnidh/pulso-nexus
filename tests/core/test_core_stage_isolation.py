"""T4 — R1 por línea en ``stage()`` (D-D, D-E, D-F, R1-18) y T5 — aislamiento de ``stage()``.

Solo datos sintéticos en directorios temporales. Cada prueba de aislamiento comprueba que, ante
un enlace o un escape, ``stage()`` se niega ANTES de escribir y nada fuera del directorio de datos
privado cambia.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import physical_fixtures as fx
import pytest

from nexus.core.refresh import RefreshService
from nexus.core.stage import stage
from nexus.errors import ConfigurationError

REPO = Path(__file__).resolve().parents[2]
needs_symlinks = pytest.mark.skipif(os.name == "nt", reason="enlaces simbólicos POSIX")


def clock() -> dt.datetime:
    return dt.datetime(2026, 11, 7, 18, 0, tzinfo=dt.UTC)


def tree(root: Path, skip: Path | None = None) -> dict[str, tuple[str, int]]:
    """Huella de un árbol: ruta → (SHA-256 o destino del enlace, modo)."""
    out: dict[str, tuple[str, int]] = {}
    for p in sorted(root.rglob("*")):
        if skip is not None and (p == skip or skip in p.parents):
            continue
        st = p.lstat()
        if p.is_symlink():
            out[p.relative_to(root).as_posix()] = ("->" + os.readlink(p), st.st_mode)
        elif p.is_file():
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            out[p.relative_to(root).as_posix()] = (digest, st.st_mode)
        else:
            out[p.relative_to(root).as_posix()] = ("dir", st.st_mode)
    return out


def repo_state() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


# ------------------------------------------------------------------ T4 R1 por línea
def _ledger(lab: dict[str, Path], *entries: dict[str, Any]) -> None:
    led = {"schema": "nexus.core.approvals-ledger/1", "entries": [*fx.LEDGER["entries"], *entries]}
    lab["ledger"].chmod(0o600)
    lab["ledger"].write_bytes(json.dumps(led).encode("utf-8"))


def _table(lab: dict[str, Path], extra: str) -> None:
    lab["homologation"].write_bytes((fx.HOMOLOGATION + extra).encode("utf-8"))


def test_t4_r1_statuses_per_line_and_portal_only_enters_universe(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    rows = [
        *fx.PORTAL_ROWS,
        ("PL-10", "QZX SOLO", "Open", "70", "0", "11/18/2026", "11/22/2026"),  # solo-Portal
        ("PL-11", "QZX FUERA", "Open", "30", "0", "11/18/2026", "11/22/2026"),  # destino fuera
        ("PL-12", "QZX SIN", "Open", "20", "0", "11/18/2026", "11/22/2026"),  # sin decisión
    ]
    (lab["src"] / "Portal_CAT_2026-11-03_0955.csv").write_bytes(fx.portal_bytes(rows=rows))
    _table(
        lab,
        "QZX SOLO,,APPROVED_PORTAL_ONLY,R1-SYN-0002\nQZX FUERA,QZXITEM8,APPROVED_MAP,R1-SYN-0003\n",
    )
    _ledger(
        lab,
        fx.ledger_entry("R1-SYN-0002", "R1_PORTAL_ONLY", ["QZX SOLO"], ""),
        fx.ledger_entry("R1-SYN-0003", "R1_MAP", ["QZX FUERA"], "QZXITEM8"),
    )
    report = stage(lab["src"], lab["data"], clock=clock, **fx.r1(lab))
    portal = next(f for f in report["files"] if f["kind"] == "portal")
    assert portal["r1_status"] == {"MAPPED": 3, "PENDING": 2, "PORTAL_ONLY": 1}
    assert report["homologation"]["pending"] == {
        "HOMOLOGATION_TARGET_NOT_IN_ERP": 1,
        "PROVISIONAL_NOT_APPLIED": 1,  # la fila PROVISIONAL del fixture nunca se aplica
    }
    assert "UNMAPPED_ITEM" not in json.dumps(portal["exceptions"])
    side = next((lab["data"] / "provenance" / "portal_cat").glob("*.json"))
    hom = json.loads(side.read_text("utf-8"))["homologation"]
    for key in ("table_sha256", "ledger_sha256", "approvers_sha256"):
        assert len(hom[key]) == 64  # procedencia de la resolución R1
    svc = RefreshService(lab["data"], "replay", lab["data"] / "rules.json", clock=clock)
    svc.refresh("t")
    head = svc.state.publication_head("replay")
    m = svc.state.get_manifest("replay", head["gen_id"])
    art = m["artifacts"]["core.summary"]
    s = svc.state.get_artifact(art["key"], art["sha256"])
    assert "PORTAL::QZX SOLO" in s["rows"]  # solo-Portal acreditado: en el universo
    assert s["rows"]["PORTAL::QZX SOLO"]["facts"]["demand"]["value"] == "70"
    assert s["pending_r1"] == ["QZX FUERA", "QZX SIN"]  # conservadas íntegras, no sumadas
    assert any(x.startswith("PENDING_R1") for x in m["validation"]["no_go"])  # R1-18


def test_t4_many_to_one_without_equivalence_is_pending_in_stage(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    rows = [
        *fx.PORTAL_ROWS,
        ("PL-20", "QZX PORTAL 1B", "Open", "40", "0", "11/18/2026", "11/22/2026"),
    ]
    (lab["src"] / "Portal_CAT_2026-11-03_0955.csv").write_bytes(fx.portal_bytes(rows=rows))
    _table(lab, "QZX PORTAL 1B,QZXITEM1,APPROVED_MAP,R1-SYN-0004\n")
    _ledger(lab, fx.ledger_entry("R1-SYN-0004", "R1_MAP", ["QZX PORTAL 1B"], "QZXITEM1"))
    report = stage(lab["src"], lab["data"], clock=clock, **fx.r1(lab))
    portal = next(f for f in report["files"] if f["kind"] == "portal")
    assert portal["r1_status"] == {"PENDING": 4}  # el grupo completo, no solo la fila nueva
    assert report["homologation"]["pending"]["MANY_TO_ONE_WITHOUT_REVISION_EQUIVALENCE"] == 2


def test_t4_tampered_ledger_rejects_all_r1(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    _ledger(
        lab, fx.ledger_entry("R1-SYN-0009", "R1_MAP", ["QZX X"], "QZXITEM2", approved_by="intruso")
    )
    report = stage(lab["src"], lab["data"], clock=clock, **fx.r1(lab))
    portal = next(f for f in report["files"] if f["kind"] == "portal")
    assert portal["r1_status"] == {"PENDING": 3}  # registro inválido: nada se homologa
    assert report["homologation"]["errors"]


# ------------------------------------------------------------------ T5 aislamiento
def test_t5_stage_writes_only_inside_private_data_dir(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    before_tmp = tree(tmp_path, skip=lab["data"])
    before_data = set(tree(lab["data"]))
    before_repo = repo_state()
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "LOCALAPPDATA": str(tmp_path),
    }
    code = (
        "import sys, datetime as dt; from pathlib import Path\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import physical_fixtures as fx\n"
        "from nexus.core.stage import stage\n"
        "keys = ('src', 'data', 'homologation', 'ledger', 'approvers')\n"
        "lab = {k: Path(v) for k, v in zip(keys, sys.argv[2:])}\n"
        "at = dt.datetime(2026, 11, 7, 18, tzinfo=dt.UTC)\n"
        "stage(lab['src'], lab['data'], clock=lambda: at, **fx.r1(lab))\n"
    )
    args = [
        str(Path(__file__).parent),
        *(str(lab[k]) for k in ("src", "data", "homologation", "ledger", "approvers")),
    ]
    subprocess.run([sys.executable, "-c", code, *args], cwd=tmp_path, env=env, check=True)
    assert tree(tmp_path, skip=lab["data"]) == before_tmp  # origen, privados y padre intactos
    created = set(tree(lab["data"])) - before_data
    assert created and all(p.split("/")[0] in ("originals", "inbox", "provenance") for p in created)
    assert repo_state() == before_repo  # el repositorio no cambia


def _refused(lab: dict[str, Path], outside: Path, match: str) -> None:
    before = tree(outside) if outside.exists() else {}
    with pytest.raises(ConfigurationError, match=match):
        stage(lab["src"], lab["data"], clock=clock, **fx.r1(lab))
    assert (tree(outside) if outside.exists() else {}) == before  # nada escrito fuera


@needs_symlinks
@pytest.mark.parametrize("sub", ["inbox", "originals", "provenance"])
def test_t5_symlinked_subdirectory_is_refused(tmp_path: Path, sub: str) -> None:
    lab = fx.lab(tmp_path)
    outside = tmp_path / "otra_bandeja"
    outside.mkdir()
    (lab["data"] / sub).symlink_to(outside, target_is_directory=True)
    _refused(lab, outside, "enlace")


@needs_symlinks
def test_t5_nested_symlink_inside_subdirectory_is_refused(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    outside = tmp_path / "fuera"
    outside.mkdir()
    (lab["data"] / "inbox").mkdir()
    (lab["data"] / "inbox" / "oeras_29").symlink_to(outside, target_is_directory=True)
    _refused(lab, outside, "enlace")


@needs_symlinks
def test_t5_data_dir_or_parent_component_symlink_is_refused(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    real = tmp_path / "real_parent"
    real.mkdir()
    link_parent = tmp_path / "link_parent"
    link_parent.symlink_to(real, target_is_directory=True)
    data = link_parent / "datos"
    with pytest.raises(ConfigurationError, match="enlace"):
        stage(lab["src"], data, clock=clock, **fx.r1(lab))
    assert not (real / "datos").exists()
    direct = tmp_path / "datos_link"
    direct.symlink_to(real, target_is_directory=True)
    with pytest.raises(ConfigurationError, match="enlace"):
        stage(lab["src"], direct, clock=clock, **fx.r1(lab))
    assert list(real.iterdir()) == []


@needs_symlinks
def test_t5_symlinked_source_file_is_never_followed(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    secret = tmp_path / "fuera_del_origen.TXT"
    secret.write_bytes(fx.oe965_bytes())
    (lab["src"] / "R_20261102.TXT").symlink_to(secret)
    _refused(lab, lab["data"], "enlazado")
    assert not (lab["data"] / "originals").exists()


@needs_symlinks
def test_t5_symlinked_private_input_is_refused(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    real = lab["ledger"].with_name("registro_real.json")
    lab["ledger"].rename(real)
    lab["ledger"].symlink_to(real)
    _refused(lab, lab["data"], "enlace")


def test_t5_data_dir_with_existing_store_is_refused(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    (lab["data"] / "store").mkdir()
    _refused(lab, lab["data"] / "store", "store")
    assert not (lab["data"] / "inbox").exists()


def test_t5_output_inside_repository_is_refused(tmp_path: Path) -> None:
    lab = fx.lab(tmp_path)
    before = repo_state()
    with pytest.raises(ConfigurationError, match="repositorio git"):
        stage(lab["src"], REPO / "tmp-no-debe-existir", clock=clock, **fx.r1(lab))
    assert repo_state() == before
