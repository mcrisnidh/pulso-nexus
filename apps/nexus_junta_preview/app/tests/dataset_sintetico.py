"""Dataset SINTÉTICO de noviembre con el formato físico real (OE965, Portal CAT, IN092U).

Todos los valores son ficticios (prefijo QZX). Reutiliza los generadores del repositorio
(``tests/core/physical_fixtures.py``). Cada ITEM ejercita una regla del mandato:

  QZXITEM1  TUPY + OTHER conservados, CAT retirado y sustituido por Portal (PL-1), futura fuera
  QZXITEM2  APTIV ≠ DELPHI; clave CAT con espacios distintos NO es CAT; FG negativo
  QZXITEM3  solo CAT; su Portal llega por un grupo de revisiones muchos-a-uno (REV-01/REV-02)
  QZXITEM4  solo clave «en revisión» (CAT_EN_REVISION): A la conserva, B la sustituye
  QZXITEM5  clave sin propuesta R3: UNKNOWN (pertenencia pendiente, nunca OTHER)
  QZXITEM6  solo en IN092U: no entra al universo
  QZXITEM7  solo en el corte posterior: no entra al universo
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests" / "core"))
import physical_fixtures as pf  # noqa: E402

REVIEW = pf.REVIEW
NEW_KEY = "QZXNEW    001"
LATER_KEY = "QZXPRK    001"

ITEMS = [
    (
        "QZXITEM1",
        "150",
        "150",
        [
            ("7310101", "1", "11/10/26", "300", "300", pf.TUPY),
            ("7310102", "1", "11/15/26", "999", "999", pf.CAT),
            ("7310103", "1", "10/20/26", "50", "50", pf.OTHER),
            ("7310104", "1", "12/05/26", "700", "700", pf.OTHER),
        ],
    ),
    (
        "QZXITEM2",
        "25",
        "25",
        [
            ("7310201", "1", "11/20/26", "120", "120", pf.APTIV),
            ("7310202", "1", "11/21/26", "80", "80", pf.DELPHI),
            ("7310203", "2", "11/25/26", "40", "40", pf.CAT_SPACED),
        ],
    ),
    ("QZXITEM3", "0", "0", [("7310301", "1", "11/28/26", "80", "80", pf.CAT)]),
    ("QZXITEM4", "0", "0", [("7310401", "1", "11/12/26", "60", "60", REVIEW)]),
    ("QZXITEM5", "10", "10", [("7310501", "1", "11/14/26", "25", "25", NEW_KEY)]),
]
LATER_ITEMS = [
    (
        "QZXITEM1",
        "150",
        "150",
        [
            ("7310101", "1", "11/10/26", "300", "250", pf.TUPY),  # modificada
            ("7310102", "1", "11/15/26", "999", "999", pf.CAT),
            ("7310104", "1", "12/05/26", "700", "700", pf.OTHER),  # 7310103 ya no está
        ],
    ),
    ("QZXITEM7", "0", "0", [("7310701", "1", "11/26/26", "15", "15", LATER_KEY)]),
]
PORTAL_ROWS = [
    ("PL-1", "QZX PORTAL 1", "Open", "400", "100", "11/18/2026", "11/20/2026"),
    ("PL-2", "QZX PORTAL 1", "Cancelled", "50", "0", "11/19/2026", "11/21/2026"),
    ("PL-3", "QZX PORTAL 1", "Open", "200", "", "12/13/2026", "12/15/2026"),
    ("PL-4", "QZX REV-01", "Open", "50", "0", "11/18/2026", "11/20/2026"),
    ("PL-5", "QZX REV-02", "Open", "30", "", "11/20/2026", "11/22/2026"),
    ("PL-6", "QZX PORTAL 7", "Completed", "10", "10", "11/05/2026", "11/06/2026"),
    ("PL-7", "QZX PORTAL 8", "Open", "10", "20", "11/05/2026", "11/06/2026"),
]
IN_ROWS = [
    ("QZXITEM1", "FG", "150.000"),
    ("QZXITEM2", "FG", "5.000-"),
    ("QZXITEM6", "FG", "99.000"),
    ("QZXITEM1", "QF", "40.000"),
    ("QZXITEM2", "RM", "300.000"),
]
R3_PROPOSALS = [
    (pf.CAT, "CAT_NOMBRE_CANDIDATO"),
    (pf.TUPY, "TUPY_CANDIDATO"),
    (pf.APTIV, "APTIV_CANDIDATO"),
    (pf.DELPHI, "DELPHI_CANDIDATO"),
    (pf.OTHER, "NO_CAT_EVIDENTE_SIN_APROBAR"),
    (pf.CAT_SPACED, "NO_CAT_EVIDENTE_SIN_APROBAR"),
    (REVIEW, "CAT_EN_REVISION"),
]
R1_PROPOSALS = [
    ("QZX PORTAL 1", "QZXITEM1"),
    ("QZX REV-01", "QZXITEM3"),
    ("QZX REV-02", "QZXITEM3"),
    ("QZX PORTAL 7", "QZXITEM9"),
]


def build(root: Path) -> dict[str, Path]:
    src, priv = root / "inputs" / "2026-11", root / "private"
    src.mkdir(parents=True)
    priv.mkdir(parents=True)
    (src / "R_20261101.TXT").write_bytes(pf.oe965_bytes(items=ITEMS))
    (src / "R_20261106.TXT").write_bytes(pf.oe965_bytes(date="11/06/26", items=LATER_ITEMS))
    (src / "Portal_CAT_2026-11-03_0955.csv").write_bytes(pf.portal_bytes(rows=PORTAL_ROWS))
    (src / "INV_STA289.TXT").write_bytes(pf.in092u_bytes(rows=IN_ROWS))
    r3 = "clave_exacta,propuesta\n" + "".join(f"{k},{t}\n" for k, t in R3_PROPOSALS)
    (priv / "r3_provisional.csv").write_bytes(r3.encode("utf-8"))
    r1 = "item_portal,item_erp\n" + "".join(f"{p},{e}\n" for p, e in R1_PROPOSALS)
    (priv / "r1_provisional.csv").write_bytes(r1.encode("utf-8"))
    (priv / "homologacion.csv").write_bytes(pf.HOMOLOGATION.encode("utf-8"))
    (priv / "registro_aprobaciones.json").write_bytes(json.dumps(pf.LEDGER).encode("utf-8"))
    (priv / "aprobadores.json").write_bytes(json.dumps(pf.APPROVERS).encode("utf-8"))
    return {
        "src": src,
        "priv": priv,
        "r3": priv / "r3_provisional.csv",
        "r1": priv / "r1_provisional.csv",
    }


if __name__ == "__main__":
    out = build(Path(sys.argv[1]))
    print(json.dumps({k: str(v) for k, v in out.items()}))
