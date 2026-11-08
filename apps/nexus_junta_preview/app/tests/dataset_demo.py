"""Dataset SINTÉTICO de demostración (≈40 artículos) con el formato físico real.

Todo es ficticio (prefijo QZX; responsables CS-1…CS-3; grupos GRP-*). Semilla fija.
``build(root)`` crea el corte inicial; ``corte_posterior(src)`` y ``fg_posterior(src)`` añaden
un backlog R y un IN092U posteriores para probar el Refresh.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests" / "core"))
import physical_fixtures as pf  # noqa: E402

KEYS = {
    "CAT": ["QZXCAT    001", "QZXCATP   000"],
    "TUPY": ["QZXTUP    000"],
    "APTIV": ["QZXAPT    001"],
    "DELPHI": ["QZXDEL    001"],
    "OTHER": ["QZXDRV    001", "QZXFRD    001", "QZXZF     000", "QZXJD     001", "QZXBRK    002"],
}
TREATMENT = {
    "QZXCAT    001": "CAT_NOMBRE_CANDIDATO",
    "QZXCATP   000": "CAT_EN_REVISION",
    "QZXTUP    000": "TUPY_CANDIDATO",
    "QZXAPT    001": "APTIV_CANDIDATO",
    "QZXDEL    001": "DELPHI_CANDIDATO",
}
UNKNOWN_KEY = "QZXNEW    001"  # sin propuesta R3: UNKNOWN
GROUP = {"QZXDRV    001": "GRP-DRIV", "QZXFRD    001": "GRP-FORD", "QZXZF     000": "GRP-ZF",
         "QZXJD     001": "GRP-JD", "QZXBRK    002": "GRP-BRK", "QZXTUP    000": "GRP-TUPY",
         "QZXAPT    001": "GRP-APTIV", "QZXDEL    001": "GRP-DELPHI", "QZXCAT    001": "GRP-CAT",
         "QZXCATP   000": "GRP-CAT", UNKNOWN_KEY: "GRP-NUEVO"}
CS = ["CS-1", "CS-2", "CS-3"]


def _plan(seed: int = 7) -> list[dict]:
    rnd = random.Random(seed)
    items = []
    for n in range(1, 41):
        item = f"QZXA{n:04d}"
        if n <= 6:
            keys = ["QZXCAT    001"]  # solo CAT: llegan por Portal
        elif n == 7:
            keys = ["QZXCATP   000"]  # en revisión
        elif n == 8:
            keys = [UNKNOWN_KEY]
        else:
            keys = rnd.sample(KEYS["OTHER"] + KEYS["TUPY"] + KEYS["APTIV"] + KEYS["DELPHI"], rnd.randint(1, 2))
            if n % 9 == 0:
                keys.append("QZXCAT    001")
        lines = []
        for k_i, key in enumerate(keys):
            for j in range(rnd.randint(1, 3)):
                day = rnd.choice([0, 3, 5, 9, 12, 16, 19, 23, 26, 28, 33])
                due = f"10/2{rnd.randint(0, 9)}/26" if day == 0 else (f"11/{day:02d}/26" if day <= 30 else "12/03/26")
                qty = rnd.choice([100, 250, 400, 600, 1000, 1200, 2500, 4000])
                lines.append((f"73{n:03d}{k_i}{j}", "1", due, str(qty), str(qty), key))
        items.append({"item": item, "lines": lines, "fg": rnd.choice([None, 0, 150, 500, 1500, 3000, 8000])})
    return items


def _oe(items: list[dict], date: str) -> bytes:
    return pf.oe965_bytes(date=date, items=[(i["item"], "0", "0", i["lines"]) for i in items])


def build(root: Path) -> dict[str, Path]:
    src, priv = root / "inputs" / "2026-11", root / "private"
    src.mkdir(parents=True)
    priv.mkdir(parents=True)
    plan = _plan()
    (src / "R_20261101.TXT").write_bytes(_oe(plan, "11/01/26"))
    portal = []
    for n in range(1, 8):
        portal.append((f"PL-{n}", f"QZX-P-{n:04d}", "Open", str(500 * n), str(100 * (n % 3)), f"11/{10 + n:02d}/2026", f"11/{12 + n:02d}/2026"))
    portal.append(("PL-8", "QZX-P-0001", "Cancelled", "300", "0", "11/20/2026", "11/22/2026"))
    portal.append(("PL-9", "QZX-REV-01", "Open", "700", "", "11/24/2026", "11/26/2026"))
    portal.append(("PL-10", "QZX-REV-02", "Open", "300", "0", "11/24/2026", "11/26/2026"))
    (src / "Portal_CAT_2026-11-03_0955.csv").write_bytes(pf.portal_bytes(rows=portal))
    fg_rows = [(i["item"], "FG", f"{i['fg']}.000") for i in plan if i["fg"]]
    fg_rows += [("QZXA0010", "QF", "40.000"), ("QZXA0011", "RM", "90.000")]
    (src / "INV_STA289.TXT").write_bytes(pf.in092u_bytes(date="11/06/26", rows=fg_rows))
    keys = sorted({line[5] for i in plan for line in i["lines"]})
    r3 = "clave_exacta,propuesta\n" + "".join(
        f"{k},{TREATMENT.get(k, 'NO_CAT_EVIDENTE_SIN_APROBAR')}\n" for k in keys if k != UNKNOWN_KEY
    )
    (priv / "r3_provisional.csv").write_bytes(r3.encode("utf-8"))
    r1 = "item_portal,item_erp\n" + "".join(f"QZX-P-{n:04d},QZXA{n:04d}\n" for n in range(1, 7))
    r1 += "QZX-P-0007,QZXA0099\nQZX-REV-01,QZXA0006\nQZX-REV-02,QZXA0006\n"
    (priv / "r1_provisional.csv").write_bytes(r1.encode("utf-8"))
    asg = "clave_cliente,cs,grupo\n" + "".join(f"{k},{CS[n % 3]},{GROUP.get(k, '')}\n" for n, k in enumerate(keys))
    (priv / "asignaciones.csv").write_bytes(asg.encode("utf-8"))
    return {"src": src, "priv": priv, "r3": priv / "r3_provisional.csv", "r1": priv / "r1_provisional.csv",
            "asignaciones": priv / "asignaciones.csv"}


def corte_posterior(src: Path, date: str = "11/07/26", name: str = "R_20261107.TXT") -> Path:
    """Backlog posterior: embarques parciales (menos abierto), líneas cerradas y un ITEM nuevo
    que NO debe entrar al universo."""
    plan = _plan()
    later = []
    for i in plan:
        lines = []
        for k, line in enumerate(i["lines"]):
            if k % 3 == 2:
                continue  # línea cerrada
            open_qty = int(line[4]) // 2 if k % 2 else int(line[4])
            lines.append((*line[:4], str(open_qty), line[5]))
        if lines:
            later.append({"item": i["item"], "lines": lines})
    later.append({"item": "QZXA0999", "lines": [("7399900", "1", "11/20/26", "50", "50", "QZXDRV    001")]})
    path = src / name
    path.write_bytes(_oe(later, date))
    return path


def fg_posterior(src: Path, name: str = "INV_STA290.TXT") -> Path:
    plan = _plan()
    rows = [(i["item"], "FG", f"{(i['fg'] or 0) + 1000}.000") for i in plan[:30]]
    path = src / name
    path.write_bytes(pf.in092u_bytes(date="11/07/26", time=" 8:15:00", rows=rows))
    return path


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
