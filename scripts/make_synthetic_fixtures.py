"""Genera los fixtures SINTÉTICOS de E1-A con semilla fija (salida byte a byte determinista).

Uso:
    python scripts/make_synthetic_fixtures.py --out tests/fixtures/synthetic

Los datos son aleatorios y no tienen significado: no derivan de ninguna fuente,
entidad ni regla corporativa.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import unicodedata
from datetime import date, timedelta
from pathlib import Path

SEED = 20261105
HEADER = "record_id,group_code,quantity,weight,event_date"
VALID_ROWS = 5000
INVALID_FILE_VALID_ROWS = 200

# Un grupo con acentos aparece en forma NFC y NFD para ejercitar la normalización Unicode.
ACCENTED_GROUP = "ÁREA-Ñ"


def _quantity(rng: random.Random) -> str:
    q = rng.randrange(-100_000, 10_000_000)
    sign = "-" if q < 0 else ""
    return f"{sign}{abs(q) // 1000}.{abs(q) % 1000:03d}"


def _weight(rng: random.Random) -> str:
    magnitude = 10.0 ** rng.randrange(-3, 7)
    sign = -1.0 if rng.random() < 0.2 else 1.0
    return repr(sign * rng.random() * magnitude)


def _date(rng: random.Random) -> str:
    return (date(2026, 1, 1) + timedelta(days=rng.randrange(0, 365))).isoformat()


def _group(rng: random.Random) -> str:
    n = rng.randrange(0, 26)
    if n == 25:
        form = "NFD" if rng.random() < 0.5 else "NFC"
        return unicodedata.normalize(form, ACCENTED_GROUP)
    return f"G{n:02d}"


def _valid_lines(rng: random.Random, count: int, prefix: str) -> list[str]:
    ids = list(range(count))
    rng.shuffle(ids)
    return [
        f"{prefix}{i:06d},{_group(rng)},{_quantity(rng)},{_weight(rng)},{_date(rng)}" for i in ids
    ]


INVALID_LINES: tuple[tuple[str, str], ...] = (
    ("X000001,G01,1.2345,1.0,2026-01-01", "invalid_quantity"),
    ("X000002,G01,abc,1.0,2026-01-01", "invalid_quantity"),
    ("X000003,G01,1.000,nan,2026-01-01", "non_finite_weight"),
    ("X000004,G01,1.000,inf,2026-01-01", "non_finite_weight"),
    ("X000005,G01,1.000,peso,2026-01-01", "invalid_weight"),
    ("X000006,G01,1.000,1.0,2026-02-30", "invalid_date"),
    ("X000007,G01,1.000,1.0,2026/01/01", "invalid_date"),
    (",G01,1.000,1.0,2026-01-01", "invalid_record_id"),
    ("X 000009,G01,1.000,1.0,2026-01-01", "invalid_record_id"),
    ("V000000,G01,1.000,1.0,2026-01-01", "duplicate_key"),
    ("X000011,G01,1.000,1.0", "column_count:4"),
    ("X000012,G01,1.000,1.0,2026-01-01,extra", "column_count:6"),
    ("", "empty_line"),
    ("X000014,G\x0701,1.000,1.0,2026-01-01", "invalid_group_code"),
    ('X000015,"G01,1.000,1.0,2026-01-01', "malformed_csv"),
    ("X000016,,1.000,1.0,2026-01-01", "invalid_group_code"),
    ("X000017,G01,1.000,1.0,2026-13-01", "invalid_date"),
    ("X000018,G01,9999999999999999.000,1.0,2026-01-01", "invalid_quantity"),
    ("X000019,G01,'; DROP TABLE x; --,1.0,2026-01-01", "invalid_quantity"),
    ("X000020,<script>alert(1)</script>,1.000,1.0,2026-01-01", "accepted"),
)


def build(out: Path) -> dict[str, str]:
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    valid = [HEADER, *_valid_lines(rng, VALID_ROWS, "R")]
    invalid_base = _valid_lines(rng, INVALID_FILE_VALID_ROWS, "V")
    invalid = [HEADER, *invalid_base, *(line for line, _ in INVALID_LINES)]
    files = {
        "input_v1.csv": "\n".join(valid) + "\n",
        "input_v1_invalid.csv": "\n".join(invalid) + "\n",
    }
    sums: dict[str, str] = {}
    for name, text in files.items():
        data = text.encode("utf-8")
        (out / name).write_bytes(data)
        sums[name] = hashlib.sha256(data).hexdigest()
    (out / "SHA256SUMS").write_text(
        "".join(f"{sums[n]}  {n}\n" for n in sorted(sums)), encoding="utf-8", newline="\n"
    )
    return sums


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    for name, digest in sorted(build(args.out).items()):
        print(f"{digest}  {name}")


if __name__ == "__main__":
    main()
