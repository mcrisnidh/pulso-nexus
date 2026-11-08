"""Puertas de calidad: ÚNICA definición para la ejecución local y para la CI de GitHub.

  uv run python scripts/quality_gates.py estatico   # Ruff CORE, formato CORE, línea base Junta
  uv run python scripts/quality_gates.py gobernanza # pruebas adversariales de estas puertas
  uv run python scripts/quality_gates.py tipos      # mypy --strict src, import-linter
  uv run python scripts/quality_gates.py junta      # pytest de apps/nexus_junta_preview
  uv run python scripts/quality_gates.py core       # pytest CORE sin SQL (argumentos de la CI)
  uv run python scripts/quality_gates.py todo       # todas, en este orden

La CI (``.github/workflows/ci.yml``) invoca ESTE script para ``estatico``, ``gobernanza`` y
``junta``; sus pasos de mypy, import-linter y pytest CORE usan los comandos de ``TIPOS`` y ``CORE``.
En Windows, ``core`` añade tzdata y carga explícitamente pytest-cov e Hypothesis; la invocación
CORE directa de CI Linux conserva su carga normal de plugins y la base horaria del sistema.
``verificar_ci()`` analiza los PASOS de cada job (no el texto): la orden debe ser una línea
ejecutable completa de un ``run`` (los comentarios YAML y de shell no cuentan), en el job exigido,
sin ``if: false`` ni ``continue-on-error``. La línea base se valida contra la del commit base
(``NEXUS_BASELINE_BASE_REF``; por omisión, ``HEAD``).
Las pruebas omitidas se informan como SKIPPED (nunca como PASS). Salida 0 solo si todo pasa.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
JUNTA_TESTS = "apps/nexus_junta_preview/app/tests"

ESTATICO = [
    ["ruff", "check", "."],
    ["ruff", "format", "--check", "."],
    ["python", "scripts/junta_ruff_baseline.py", "check"],
]
TIPOS = [
    ["mypy", "--strict", "src"],
    ["lint-imports"],
]
CORE = [
    [
        "pytest",
        "-p",
        "pytest_cov",
        "-p",
        "hypothesis.extra.pytestplugin",
        "-m",
        "not sql",
        "-ra",
        "--tb=short",
        "--cov=nexus",
        "--cov-branch",
        "--cov-report=term",
    ]
]
JUNTA = [["pytest", JUNTA_TESTS, "-p", "no:cacheprovider", "-ra", "--tb=short"]]
GOBERNANZA = [["pytest", "scripts/tests", "-p", "no:cacheprovider", "-ra", "--tb=short"]]
UV_BASE = ["uv", "run", "--frozen"]
# Dependencias ADICIONALES por puerta, fuera de uv.lock (que no se modifica).
# Junta y CORE requieren zoneinfo("America/Mexico_City"): Windows no trae la base IANA
# y uv.lock no incluye tzdata. CORE fuerza además pytest-cov e Hypothesis cuando la carga
# automática de plugins está deshabilitada por PYTEST_DISABLE_PLUGIN_AUTOLOAD.
TZDATA = "tzdata==2025.2"
DEPENDENCIAS_EXTRA: dict[str, list[str]] = {
    "junta": ["--with", TZDATA],
    "core": ["--with", TZDATA],
}
GATES = "uv run python scripts/quality_gates.py"
CORE_CI = 'uv run pytest -m "not sql" -ra --tb=short --cov=nexus --cov-branch --cov-report=term'
# job → órdenes exigidas (línea completa) y si el paso debe fijar NEXUS_BASELINE_BASE_REF
REQUISITOS: dict[str, list[tuple[str, bool]]] = {
    "linux": [
        (f"{GATES} estatico", True),
        (f"{GATES} gobernanza", False),
        ("uv run mypy --strict src", False),
        ("uv run lint-imports", False),
        (CORE_CI, False),
        (f"{GATES} junta", False),
    ],
    "windows": [(f"{GATES} gobernanza", False), (f"{GATES} junta", False)],
}


def _sin_comentario(valor: str) -> str:
    """Escalar YAML plano de una línea: un « #» inicia comentario (fuera de comillas)."""
    comilla = ""
    for i, ch in enumerate(valor):
        if ch in "'\"" and not comilla:
            comilla = ch
        elif ch == comilla:
            comilla = ""
        elif ch == "#" and not comilla and (i == 0 or valor[i - 1] in " \t"):
            return valor[:i].rstrip()
    return valor.rstrip()


def _ind(s: str) -> int:
    return len(s) - len(s.lstrip(" "))


def _bloque(lineas: list[str], i: int) -> tuple[list[str], int]:
    """Líneas más indentadas que la clave de un paso (nivel 8) a partir de ``i``."""
    out = []
    while i < len(lineas) and (not lineas[i].strip() or _ind(lineas[i]) > 8):
        out.append(lineas[i].strip())
        i += 1
    return out, i


def _clave_paso(paso: dict[str, Any], clave: str, valor: str, lineas: list[str], i: int) -> int:
    if clave in ("env", "with") and not valor:
        sub, i = _bloque(lineas, i)
        for x in sub:
            if x and not x.startswith("#") and ":" in x:
                k, _, v = x.partition(":")
                paso[clave][k.strip()] = _sin_comentario(v.strip())
    elif clave == "run":
        cmds = [valor]
        if valor[:1] in ("|", ">"):
            cmds, i = _bloque(lineas, i)
            if valor.startswith(">"):  # plegado: YAML une las líneas en una sola orden
                cmds = [" ".join(x for x in cmds if x)]
        paso["run"] = [c for c in (x.strip() for x in cmds) if c and not c.startswith("#")]
    elif clave in ("if", "continue-on-error"):
        paso[clave] = valor
    return i


def pasos_ci(texto: str) -> dict[str, list[dict[str, Any]]]:
    """Pasos por job de un workflow con la forma de ci.yml (sin dependencias externas).

    Cada paso: {"run": [líneas ejecutables], "env": {...}, "if": str, "continue-on-error": str,
    "with": {...}}. Las líneas de comentario YAML y las de shell (``#``) se descartan."""
    jobs: dict[str, list[dict[str, Any]]] = {}
    lineas = texto.splitlines()
    en_jobs, job, paso, i = False, "", None, 0
    while i < len(lineas):
        ln, st = lineas[i], lineas[i].strip()
        i += 1
        if not st or st.startswith("#"):
            continue
        n = _ind(ln)
        if n == 0:
            en_jobs, job, paso = st == "jobs:", "", None
        elif en_jobs and n == 2 and st.endswith(":"):
            job, paso = st[:-1], None
            jobs[job] = []
        elif en_jobs and job and n >= 6:
            if n == 6 and st.startswith("- "):
                paso = {"run": [], "env": {}, "with": {}, "if": "", "continue-on-error": ""}
                jobs[job].append(paso)
                st, n = st[2:].strip(), 8
            if paso is not None and n == 8 and ":" in st:
                clave, _, valor = st.partition(":")
                i = _clave_paso(paso, clave.strip(), _sin_comentario(valor.strip()), lineas, i)
    return jobs


def verificar_ci(texto: str | None = None) -> list[str]:
    texto = texto if texto is not None else (ROOT / ".github/workflows/ci.yml").read_text("utf-8")
    jobs = pasos_ci(texto)
    faltan: list[str] = []
    for job, reqs in REQUISITOS.items():
        pasos = jobs.get(job)
        if not pasos:
            faltan.append(f"job {job!r} ausente")
            continue
        for cmd, env_base in reqs:
            validos = [
                p
                for p in pasos
                if cmd in p["run"]
                and str(p["continue-on-error"]).lower() not in ("true", "${{ true }}")
                and not re.fullmatch(r"(false|\$\{\{\s*false\s*\}\})", str(p["if"]).strip(), re.I)
            ]
            if not validos:
                faltan.append(f"[{job}] {cmd}")
            elif env_base and not any(
                str(p["env"].get("NEXUS_BASELINE_BASE_REF", "")).strip() for p in validos
            ):
                faltan.append(f"[{job}] {cmd} sin env NEXUS_BASELINE_BASE_REF")
    checkout = [p for p in jobs.get("linux", []) if str(p["with"].get("fetch-depth", "")) == "0"]
    if not checkout:
        faltan.append(
            "[linux] checkout sin fetch-depth: 0 (la línea base se valida contra el commit base)"
        )
    return faltan


def _conteo(junit: Path) -> str:
    # XML que pytest acaba de escribir en un directorio temporal propio: no es entrada externa
    root = ET.parse(junit).getroot()  # noqa: S314
    suites = [root] if root.tag == "testsuite" else list(root)
    tot = {
        k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")
    }
    passed = tot["tests"] - tot["failures"] - tot["errors"] - tot["skipped"]
    skipped = [
        f"{c.get('classname')}::{c.get('name')} — {(sk.get('message') or '').strip()}"
        for s in suites
        for c in s.iter("testcase")
        for sk in c.findall("skipped")
    ]
    lines = [
        f"passed={passed} failed={tot['failures']} errors={tot['errors']} skipped={tot['skipped']}"
    ]
    lines += [f"  SKIPPED {x}" for x in skipped]
    return "\n".join(lines)


def comando_uv(grupo: str, cmd: list[str]) -> list[str]:
    """Orden completa con la que se lanza ``cmd`` en la puerta ``grupo``."""
    return [*UV_BASE, *DEPENDENCIAS_EXTRA.get(grupo, []), *cmd]


def ejecutar(nombre: str, cmds: list[list[str]], junit: bool = False) -> bool:
    ok = True
    for cmd in cmds:
        with tempfile.TemporaryDirectory() as tmp:
            extra = [f"--junitxml={Path(tmp) / 'r.xml'}"] if junit else []
            orden = comando_uv(nombre, cmd)
            print(f"\n=== [{nombre}] {' '.join(orden)}", flush=True)
            env = dict(os.environ)
            env.setdefault("NEXUS_BASELINE_BASE_REF", "HEAD")
            rc = subprocess.run([*orden, *extra], cwd=ROOT, check=False, env=env).returncode
            if junit and (Path(tmp) / "r.xml").is_file():
                print(f"=== [{nombre}] {_conteo(Path(tmp) / 'r.xml')}", flush=True)
        print(f"=== [{nombre}] {'PASS' if rc == 0 else f'FAIL (código {rc})'}", flush=True)
        ok = ok and rc == 0
    return ok


def main(argv: list[str]) -> int:
    grupos = {
        "estatico": (ESTATICO, False),
        "gobernanza": (GOBERNANZA, True),
        "tipos": (TIPOS, False),
        "junta": (JUNTA, True),
        "core": (CORE, True),
    }
    pedido = argv[0] if argv else ""
    if pedido not in (*grupos, "todo"):
        print(__doc__)
        return 2
    faltan = verificar_ci()
    if faltan:
        print("La CI no aplica la misma política que este script; falta:", *faltan, sep="\n  ")
        return 1
    resultado = {g: ejecutar(g, *grupos[g]) for g in (grupos if pedido == "todo" else [pedido])}
    print(
        "\n=== RESUMEN "
        + " · ".join(f"{g}: {'PASS' if ok else 'FAIL'}" for g, ok in resultado.items())
    )
    return 0 if all(resultado.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
